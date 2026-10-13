"""People from a search engine: `site:instagram.com "шеф-повар" <city>` ("chef") on Brave Search.

Measured 2026-09-30: of 11 handles in the results, 10 were new to the database — both chefs and
venues outside the 2GIS registry. The Instagram rate limit isn't spent until a handle goes into
profile verification. Brave already returns 429 on the third request in a row from one IP, so we
throttle: one request every INTERVAL seconds, VPN exits rotated round-robin, an exit that got a
429 cools down for 30 minutes.

    deploy/local/run.sh leadgen.sources.websearch        # daemon (launchd io.leadgen.sources.websearch)
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

from leadgen.core import contacts, db, queue as wq, settings
from leadgen.sources import gis_cities
from leadgen.qualify import handlers
from leadgen.access import vpn

ROLES = ('шеф-повар', 'су-шеф', 'бренд-шеф', 'шеф-кондитер')
# second wave (2026-10-06, "widen coverage"): how chefs actually sign themselves — in English,
# and "шеф ресторана" ("restaurant chef"); runs after the first wave across all cities
EXTRA_ROLES = ('executive chef', 'head chef', 'шеф ресторана', 'chef de cuisine', 'pastry chef')
# third wave (2026-10-07): venues themselves, outside 2GIS, Afisha and Restoclub — the venue's
# account goes to header check and subscriber walk; no bars — bar direction paused since 2026-09-16
VENUE_TERMS = ('ресторан', 'бистро', 'гастробар', 'авторская кухня', 'ресторан высокой кухни')
# fourth wave (2026-10-07): venues by cuisine — reserve behind the third wave
# 2026-10-12, "speed up collection, widen sources": districts of large cities (per-district
# results go deeper than for the whole city), Latin script (chefs write "Chef, Moscow"), and new venue types
DISTRICT_ROLES = ('шеф-повар', 'бренд-шеф', 'су-шеф')
LATIN_ROLES = ('chef', 'head chef', 'brand chef')
LATIN_CITIES = {'Moscow': 'Москва', 'Saint Petersburg': 'Санкт-Петербург', 'Kazan': 'Казань', 'Sochi': 'Сочи',
                'Ekaterinburg': 'Екатеринбург', 'Novosibirsk': 'Новосибирск', 'Krasnodar': 'Краснодар',
                'Nizhny Novgorod': 'Нижний Новгород', 'Samara': 'Самара', 'Rostov-on-Don': 'Ростов-на-Дону',
                'Kaliningrad': 'Калининград', 'Vladivostok': 'Владивосток', 'Krasnoyarsk': 'Красноярск',
                'Irkutsk': 'Иркутск', 'Tyumen': 'Тюмень', 'Ufa': 'Уфа', 'Perm': 'Пермь', 'Chelyabinsk': 'Челябинск',
                'Omsk': 'Омск', 'Voronezh': 'Воронеж', 'Volgograd': 'Волгоград', 'Tomsk': 'Томск'}
VENUE_TERMS_2 = ('гастрономический ресторан', 'fine dining', 'ресторан при отеле', 'загородный ресторан',
                 'ресторан морепродуктов', 'винный ресторан')
PER_TICK = 4  # requests per engine per tick — from different exits (100+ exits, ~30 distinct IPs)
CUISINE_TERMS = ('итальянский ресторан', 'грузинский ресторан', 'паназиатский ресторан', 'рыбный ресторан',
                 'стейк-хаус', 'ресторан русской кухни')
# consulting — East only (NSK department), client decision 2026-09-30; runs first in the queue
CONSULT_ROLES = ('ресторанный консультант', 'запуск ресторанов')
# HoReCa tableware dealers — East only (NSK department), client request 2026-10-08; run right after consulting
DEALER_TERMS = ('посуда для ресторанов', 'посуда horeca', 'оснащение ресторанов', 'поставщик посуды',
                'сервировка для ресторанов')
# 2026-10-13: first wave of dealer queries exhausted (780 queries, 2 East dealers) — other phrasings
# and restaurant tableware brands combined with a city
DEALER_TERMS_2 = ('посуда для кафе', 'посуда для общепита', 'фарфор для ресторанов', 'оборудование для ресторанов',
                  'инвентарь для ресторанов', 'посуда оптом', 'Porland', 'Tognana', 'Wilmax', 'Kutahya', 'RAK Porcelain',
                  'Steelite')
PAGES = (0, 1)  # Brave's third results page is nearly empty
FIRST_CITIES = ('Москва', 'Санкт-Петербург')
INTERVAL = 45  # 2026-10-05: 22 exits — each one rests ~16 minutes per engine
COOLDOWN_MIN = 30
REPEAT_DAYS = 30
SOURCE = handlers.SOURCE_WEBSEARCH
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/129.0 Safari/537.36')
NICK = re.compile(r'instagram\.com/([A-Za-z0-9_.]{2,30})(/[A-Za-z0-9_./-]*)?', re.I)
NOT_PROFILES = {'p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'popular', 'web',
                'about', 'legal', 'direct', 'developer', 'directory'}


def parse_nicks(page):
    out = []
    # DuckDuckGo wraps links: //duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fnick
    for m in NICK.finditer(urllib.parse.unquote(html.unescape(page or ''))):
        nick = m.group(1).lower().rstrip('.')
        if nick in NOT_PROFILES or nick in out:
            continue
        out.append(nick)
    return out


def _cities():
    names = [c for _, c in gis_cities.CITIES]
    return list(FIRST_CITIES) + [c for c in dict.fromkeys(names) if c not in FIRST_CITIES]


def _east_cities():
    return list(dict.fromkeys(c for slug, c in gis_cities.CITIES if gis_cities.department(slug) == 'НСК'))


def queries():
    out = []
    for city in _east_cities():
        for role in CONSULT_ROLES + DEALER_TERMS + DEALER_TERMS_2:
            for page in PAGES:
                out.append(('site:instagram.com "%s" %s' % (role, city), page))
    for roles in (ROLES, EXTRA_ROLES, VENUE_TERMS, CUISINE_TERMS):
        for city in _cities():
            for role in roles:
                for page in PAGES:
                    out.append(('site:instagram.com "%s" %s' % (role, city), page))
    city_of = dict(gis_cities.CITIES)
    for slug, districts in gis_cities.DISTRICTS.items():
        for district in districts:
            for role in DISTRICT_ROLES:
                for page in PAGES:
                    out.append(('site:instagram.com "%s" %s %s' % (role, district, city_of.get(slug, '')), page))
    for latin in LATIN_CITIES:
        for role in LATIN_ROLES:
            for page in PAGES:
                out.append(('site:instagram.com "%s" %s' % (role, latin), page))
    for city in _cities():
        for role in VENUE_TERMS_2:
            for page in PAGES:
                out.append(('site:instagram.com "%s" %s' % (role, city), page))
    return out


def city_of_query(query):
    """City from the query's tail: "Казань" ("Kazan"), "Хамовники Москва" → Москва, "Moscow" → Москва; else ''."""
    tail = query.rsplit('" ', 1)[-1].strip()
    known = {c for _, c in gis_cities.CITIES}
    if tail in known:
        return tail
    if tail in LATIN_CITIES:
        return LATIN_CITIES[tail]
    for city in sorted(known, key=len, reverse=True):
        if tail.endswith(' ' + city):
            return city
    return ''


def brave(query, page, port):
    """(HTTP code, page) from Brave's results via a VPN exit (None — direct)."""
    url = 'https://search.brave.com/search?q=%s&offset=%d' % (urllib.parse.quote(query), page)
    cmd = ['curl', '-s', '-L', '--max-time', '25', '-A', UA, '-H', 'accept-language: ru-RU,ru;q=0.9',
           '-w', '\n%{http_code}', url]
    if port:
        cmd[1:1] = ['-x', 'socks5h://127.0.0.1:%d' % port]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    body, _, code = out.rpartition('\n')
    return int(code) if code.isdigit() else 0, body


def ddg(query, page, port):
    """(HTTP code, page) from DuckDuckGo's results (HTML version). 202 is its soft refusal, like 429."""
    url = 'https://html.duckduckgo.com/html/?q=%s&kl=ru-ru' % urllib.parse.quote(query)
    cmd = ['curl', '-s', '-L', '--compressed', '--max-time', '25', '-A', UA, '-H', 'accept-language: ru-RU,ru;q=0.9',
           '-w', '\n%{http_code}', url]
    if port:
        cmd[1:1] = ['-x', 'socks5h://127.0.0.1:%d' % port]
    out = subprocess.run(cmd, capture_output=True).stdout.decode('utf-8', 'replace')
    body, _, code = out.rpartition('\n')
    return int(code) if code.isdigit() else 0, body


DDG_PAGES = (0,)  # DuckDuckGo's second page needs a POST form; the first page is enough


def _next_query(archive, engine='brave', pages=PAGES):
    done = {(r['query'], r['page']) for r in archive.execute(
        "SELECT DISTINCT query, page FROM websearch_runs WHERE status = 'ok' AND engine = %s "
        "AND at > now() - %s * interval '1 day'", (engine, REPEAT_DAYS)).fetchall()}
    for q in queries():
        if q[1] in pages and q not in done:
            return q
    return None


def _free_port(archive, ports, engine='brave', ip_of=None):
    """Exit for a request: not cooling down, and least recently used. With ip_of, cooldown and
    rotation are keyed by IP: the subscription has 143 servers and ~30 IPs (2026-10-12), so a
    neighboring port of a cooling-down one shares the same address."""
    key = (lambda p: ip_of.get(p or 0) or p or 0) if ip_of else (lambda p: p or 0)
    cooling = {key(r['port']) for r in archive.execute(
        # a connection error means a dead exit: cools down the same as on a 429
        "SELECT DISTINCT port FROM websearch_runs WHERE status IN ('throttled', 'error') AND engine = %s "
        "AND at > now() - %s * interval '1 minute'", (engine, COOLDOWN_MIN)).fetchall()}
    last = {}
    for r in archive.execute('SELECT port, max(at) AS at FROM websearch_runs WHERE engine = %s GROUP BY port',
                             (engine,)).fetchall():
        k = key(r['port'])
        last[k] = max(last.get(k) or r['at'], r['at'])
    free = [p for p in ports if key(p) not in cooling]
    if not free:
        return False, None
    # round-robin: pick the address that was used longest ago
    return True, min(free, key=lambda p: (last.get(key(p)) is not None, last.get(key(p))))


def queued(archive, nick):
    return archive.execute("SELECT 1 FROM work_queue WHERE task_type = 'profile_info' "
                           "AND payload->>'nick' = %s LIMIT 1", (nick,)).fetchone() is not None


def run_once(stores, engine, ports, name='brave', pages=PAGES, ip_of=None):
    """One request via `engine` (logged under `name`). None — nothing to do
    (everything's done, or every exit is cooling down)."""
    archive = stores.archive
    q = _next_query(archive, name, pages)
    ok, port = _free_port(archive, ports, name, ip_of)
    if q is None or not ok:
        return None
    query, page = q
    code, body = engine(query, page, port)
    status = 'ok' if code == 200 else 'throttled' if code in (202, 429) else 'error'
    found = added = 0
    if status == 'ok':
        city = city_of_query(query)
        nicks = parse_nicks(body)
        found = len(nicks)
        for nick in nicks:
            if contacts.is_known(stores, nick) or queued(archive, nick):
                continue
            wq.enqueue(archive, 'profile_info', {'nick': nick, 'venue': '', 'city': city,
                                                 'source': SOURCE}, priority=80)
            added += 1
    archive.execute('INSERT INTO websearch_runs (query, page, port, status, found, added, engine) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s)', (query, page, port or 0, status, found, added, name))
    out = {'query': query, 'page': page, 'status': status, 'found': found, 'added': added}
    if name != 'brave':
        out['engine'] = name
    return out


BRIDGE_CANDIDATES = 3


def run_bridge_once(stores, engine, ports, name='brave', ip_of=None):
    """Bridge via a search engine (2026-10-06): for a venue account from a web_bridge task, search
    `site:instagram.com "name" city`, handles with a matching name go for a page check
    (bridge_check, which works even for logged-out viewers). No candidates — fall back to the
    usual Instagram-search bridge (bridge). None — nothing to do, or every exit is cooling down."""
    from leadgen.qualify import quality
    archive = stores.archive
    ok, port = _free_port(archive, ports, name, ip_of)
    if not ok:
        return None
    task = wq.claim_next(archive, ['web_bridge'], 'websearch-%s' % name)
    if not task:
        return None
    venue, city = task['payload']['venue_name'], task['payload']['city']
    second = task['payload'].get('variant') == 2
    # second request (refill.bridge_retry) — unquoted, with the word "ресторан" ("restaurant") added
    query = ('site:instagram.com ресторан %s %s' if second else 'site:instagram.com "%s" %s') % (venue, city)
    code, body = engine(query, 0, port)
    status = 'ok' if code == 200 else 'throttled' if code in (202, 429) else 'error'
    found = added = 0
    if status == 'ok':
        nicks = parse_nicks(body)
        found = len(nicks)
        cands = [n for n in nicks if quality.name_matches(n, '', venue)][:BRIDGE_CANDIDATES]
        if cands:
            check = {'venue_name': venue, 'city': city, 'nicks': cands}
            if second:
                check['no_fallback'] = True
            wq.enqueue(archive, 'bridge_check', check, priority=50)
            added = len(cands)
        elif not second:  # don't repeat the Instagram-search fallback for the second request — it already ran
            wq.enqueue(archive, 'bridge', {'venue_name': venue, 'city': city, 'web_tried': True}, priority=60)
        wq.complete(archive, task['id'])
    else:
        wq.release(archive, task['id'])  # exit cooling down or network issue — retry the same query later, from a different exit
    archive.execute('INSERT INTO websearch_runs (query, page, port, status, found, added, engine) '
                    'VALUES (%s, 0, %s, %s, %s, %s, %s)', (query, port or 0, status, found, added, name))
    return {'query': query, 'page': 0, 'status': status, 'found': found, 'added': added}


def _ports():
    try:
        with open(vpn.CONFIG) as f:
            return [None] + [i['port'] for i in json.load(f)['inbounds']]
    except (OSError, ValueError, KeyError):
        return [None]


ENGINES = (('brave', brave, PAGES), ('ddg', ddg, DDG_PAGES))


def tick(stores, engines, ports, ip_of=None):
    """One tick: up to PER_TICK requests per engine, each from the least-busy exit. Venue bridge
    requests go first (the whole walk is waiting on them), then people search. Returns the count done."""
    done = 0
    for name, engine, pages in engines:
        for _ in range(PER_TICK):
            out = run_bridge_once(stores, engine, ports, name=name, ip_of=ip_of) or \
                run_once(stores, engine, ports, name=name, pages=pages, ip_of=ip_of)
            if out is None:
                break
            done += 1
            print('поисковик %s: %s стр.%d — %s, ников %d, новых %d'
                  % (name, out['query'], out['page'], out['status'], out['found'], out['added']), flush=True)
    return done


def main():
    stores = db.open_stores()
    db.init_archive(stores.archive)
    ip_of, ip_at = {}, 0.0
    while True:
        try:
            if settings.get(stores.main, 'paused') == '1':
                time.sleep(300)
                continue
            if time.time() - ip_at > 1800:  # refresh the "port → IP" map every half hour: subscriptions change
                from leadgen.access import exits
                ip_of, ip_at = exits.live_exits(), time.time()
            ports = [None] + sorted(ip_of) if ip_of else _ports()
            worked = tick(stores, ENGINES, ports, ip_of)
            if not worked:
                time.sleep(600)
                continue
            time.sleep(int(settings.get(stores.main, 'websearch_interval_sec', INTERVAL)))
        except Exception as e:  # noqa: BLE001 — the daemon never crashes: DB or network issues just retry in a minute
            print('поисковик: ошибка: %s' % e, file=sys.stderr, flush=True)
            try:
                stores.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)
            stores = db.open_stores()


if __name__ == '__main__':
    main()

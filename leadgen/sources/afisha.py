"""Venues from the Afisha restaurant catalog (2026-10-05): 18 cities.

Each listing page has schema.org Restaurant cards embedded in it — name, address, check
(priceRange "3000-6000 RUB"): 30 venues per request, no need to open the card. Cards have no
Instagram, so new PREMIUM/UPPER-MIDDLE venues go into the registry and onto the bridge (account
lookup by name and city), same as 2GIS venues without a link. Already-seen cards are remembered
in the archive.

    deploy/local/run.sh leadgen.sources.afisha      # once a day, alongside awards (leadgen.sources.awards)
"""
import json
import re
import time

import httpx

from leadgen.sources import gis, gis_cities, websearch
from leadgen.core import queue as wq

BASE = 'https://www.afisha.ru'
SOURCE = 'Афиша'
LEVELS = ('PREMIUM', 'UPPER-MIDDLE')
MAX_PAGES = 300
CITIES = {'msk': 'Москва', 'spb': 'Санкт-Петербург', 'ekaterinburg': 'Екатеринбург', 'novosibirsk': 'Новосибирск',
          'kazan': 'Казань', 'nizhniy-novgorod': 'Нижний Новгород', 'krasnodar': 'Краснодар', 'sochi': 'Сочи',
          'samara': 'Самара', 'rostov-na-donu': 'Ростов-на-Дону', 'ufa': 'Уфа', 'perm': 'Пермь',
          'vladivostok': 'Владивосток', 'krasnoyarsk': 'Красноярск', 'chelyabinsk': 'Челябинск',
          'tyumen': 'Тюмень', 'voronezh': 'Воронеж', 'kaliningrad': 'Калининград'}
CARD = re.compile(r'"@type"\s*:\s*"Restaurant"')


def fetch(url):
    try:
        r = httpx.get(url, headers={'user-agent': websearch.UA}, follow_redirects=True, timeout=40)
    except httpx.HTTPError:
        return ''
    return r.text if r.status_code == 200 else ''


def check_of(price):
    """"3000-6000 RUB" → midpoint of the range; "from 6000 RUB", "up to 500 RUB" → that number itself."""
    nums = [int(re.sub(r'\s', '', n)) for n in re.findall(r'\d[\d\s]*\d|\d', price or '')]
    if not nums:
        return None
    return sum(nums[:2]) // len(nums[:2])


def parse_listing(page):
    """[{name, city, check, url}] from the cards embedded in the listing page."""
    # JSON-LD sits as a string inside a script tag: strip one level of escaping
    text = (page or '').replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\')
    decoder, out, end = json.JSONDecoder(), [], 0
    for m in CARD.finditer(text):
        if m.start() < end:
            continue  # inside a card already parsed
        start = text.rfind('{', 0, m.start())
        try:
            item, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        out.append({'name': (item.get('name') or '').strip(),
                    'city': ((item.get('address') or {}).get('addressLocality') or '').strip(),
                    'check': check_of(item.get('priceRange')), 'url': item.get('url') or ''})
    return out


def _ensure_table(archive):
    archive.execute('CREATE TABLE IF NOT EXISTS afisha_seen (url TEXT PRIMARY KEY, '
                    'at TIMESTAMPTZ NOT NULL DEFAULT now())')


def _take(stores, c, city, seen, out):
    """Card → registry (PREMIUM/UPPER-MIDDLE) and bridge. Already-seen cards don't count."""
    archive, main = stores.archive, stores.main
    if c['url'] in seen or not c['name']:
        return
    archive.execute('INSERT INTO afisha_seen (url) VALUES (%s) ON CONFLICT DO NOTHING', (c['url'],))
    seen.add(c['url'])
    out['places'] += 1
    level = gis.level_for(c['check'])
    if level not in LEVELS:
        return
    out['target'] += 1
    row = main.execute(
        'INSERT INTO venues (name, city, region, level, avg_check, status, source) '
        "VALUES (%s, %s, '', %s, %s, 'не отработано', %s) "
        'ON CONFLICT (name, city) DO UPDATE SET '
        "level = COALESCE(NULLIF(venues.level, ''), excluded.level) "
        'RETURNING (xmax = 0) AS inserted',
        (c['name'], city, level, c['check'], SOURCE)).fetchone()
    if row['inserted']:
        out['new'] += 1
        wq.enqueue(archive, 'web_bridge', {'venue_name': c['name'], 'city': city}, priority=50)


def _locs(xml):
    return re.findall(r'<loc>\s*(.*?)\s*</loc>', xml or '')


def run_choice(stores, fetch=fetch, pause=1.0):
    """"Afisha's Choice" — editorial picks for all cities, from the sitemap (117 in Moscow).
    We don't pull the whole catalog: 24,827 Moscow venues at 400 KB per card is 2GIS's job."""
    _ensure_table(stores.archive)
    seen = {r['url'] for r in stores.archive.execute('SELECT url FROM afisha_seen').fetchall()}
    ours = {c for _, c in gis_cities.CITIES}
    out = {'places': 0, 'target': 0, 'new': 0}
    for city_map in _locs(fetch(BASE + '/rests/sitemap.xml')):
        m = re.search(r'afisha\.ru/([a-z-]+)/restaurants/', city_map)
        if not m:
            continue
        slug = m.group(1)
        for url in _locs(fetch('%s/%s/restaurants/sitemap-afisha_choice.xml' % (BASE, slug))):
            cards = parse_listing(fetch(url))
            time.sleep(pause)
            for c in cards:
                city = CITIES.get(slug) or (c['city'] if c['city'] in ours else '')
                if city:
                    _take(stores, c, city, seen, out)
    return out


def run(stores, fetch=fetch, cities=CITIES, pause=1.0):
    archive, main = stores.archive, stores.main
    _ensure_table(archive)
    seen = {r['url'] for r in archive.execute('SELECT url FROM afisha_seen').fetchall()}
    out = {'places': 0, 'target': 0, 'new': 0}
    for slug, city in cities.items():
        this_run = set()
        for n in range(1, MAX_PAGES + 1):
            url = '%s/%s/restaurants/' % (BASE, slug) + ('?page=%d' % n if n > 1 else '')
            cards = parse_listing(fetch(url))
            time.sleep(pause)
            fresh = {c['url'] for c in cards} - this_run
            # end — a page with not a single new venue: embedded cards on any ?page=N are the
            # same as on page 1 (the rest of the list is loaded by a script) — measured 2026-10-05
            if not fresh:
                break
            this_run |= fresh
            for c in cards:
                _take(stores, c, city, seen, out)
        print('Афиша: %s — заведений %d, нужного уровня %d, новых %d' % (city, out['places'], out['target'], out['new']),
              flush=True)
    return out


if __name__ == '__main__':
    from leadgen.core import db
    stores = db.open_stores()
    print('Афиша: заведений %(places)d, нужного уровня %(target)d, новых в реестре %(new)d' % run(stores))
    print('Выбор Афиши: заведений %(places)d, нужного уровня %(target)d, новых в реестре %(new)d' % run_choice(stores))

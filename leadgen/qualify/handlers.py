"""Task handlers for the Instagram pipeline.

Ported from agent1_daemon.py: same logic and measurements. What changed is
storage (two databases) and Instagram access (the leadgen.access.transport
contract — our own accounts or HikerAPI). Everything found through a venue is
tagged "Агент №2" ("Agent #2") at candidate-creation time — once exported, the
source channel can no longer be recovered."""
import os
import re

from leadgen.qualify import bio_roles, quality
from leadgen.core import contacts, venues
from leadgen.access import health
from leadgen.core import queue as wq
from leadgen.access.transport import EMPTY, FAILURES, OK

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TASK_TYPES = ['bridge', 'bridge_check', 'venue_header', 'following', 'profile_info']
MAX_EMPTY_ATTEMPTS = 3

# Budget cap: max_pages=1 at count=100 -> up to 100 follows per venue, ~41%
# pass the pre-filter. Raise this only as the account pool grows.
MAX_FOLLOWING_PAGES = 3  # 2026-09-30: after the main DB moved to the Mac, speed allows 3 pages
# re-walking venues already walked before: the first page is known, new ground is deeper in
REWALK_PAGES = 3
# PREMIUM venues get a deeper walk — the most valuable sample, and a following request is cheap (2026-10-01)
PREMIUM_PAGES = 5
REWALK_PRIORITY = 85  # below fresh work (profile_info 80, vet 40)

AGENT_VENUE = 'Агент №2'

# Endpoint classes are Instagram ENDPOINTS, not task types: an endpoint block
# caught on one task type must be visible to the others.
ENDPOINT_SEARCH = 'search'
ENDPOINT_PROFILE = 'profile_info'
ENDPOINT_FOLLOWING = 'following'
ENDPOINT_PROFILE_HTML = 'profile_html'

# pk is resolved via two interchangeable routes: search goes first, the
# profile page is usually still alive while that happens.
RESOLVE_ENDPOINTS = (ENDPOINT_SEARCH, ENDPOINT_PROFILE_HTML)

# With a ready pk in the payload, these task types skip search entirely.
PK_SHORTCUT_TYPES = ('venue_header', 'profile_info')


def _requeue_or_close(stores, task):
    """Empty result: requeue until the attempt budget runs out."""
    if (task.get('attempts') or 0) >= MAX_EMPTY_ATTEMPTS:
        wq.complete(stores.archive, task['id'])
        return 'закрыта'
    wq.fail(stores.archive, task['id'], requeue=True)
    return 'возвращена'


def _empty_search(stores, transport, account_id, task):
    """For our own accounts an empty result is a silent block; for HikerAPI it's a genuine "no"."""
    if transport.silent_empty_is_block:
        health.record_failure(stores.archive, account_id, ENDPOINT_SEARCH, 'soft_block')
        return _requeue_or_close(stores, task)
    health.record_success(stores.archive, account_id, ENDPOINT_SEARCH)
    wq.complete(stores.archive, task['id'])
    return 'закрыта'


def resolve_pk(stores, transport, account_id, nick):
    """pk by nickname: via search if it's alive, otherwise via the profile page."""
    archive = stores.archive
    if health.pick_account(archive, ENDPOINT_SEARCH, [account_id]) is not None:
        candidates, status = transport.search(nick)
        if status in FAILURES:
            health.record_failure(archive, account_id, ENDPOINT_SEARCH, status)
        elif status == EMPTY:
            if transport.silent_empty_is_block:
                health.record_failure(archive, account_id, ENDPOINT_SEARCH, 'soft_block')
        else:
            health.record_success(archive, account_id, ENDPOINT_SEARCH)
            match = next((u for u in candidates or []
                          if (u.get('username') or '').lower() == nick.lower()), None)
            if match and match.get('pk'):
                return str(match['pk'])

    if health.pick_account(archive, ENDPOINT_PROFILE_HTML, [account_id]) is None:
        return None
    pk, status = transport.pk_by_nick(nick)
    if status in FAILURES:
        health.record_failure(archive, account_id, ENDPOINT_PROFILE_HTML, status)
        return None
    health.record_success(archive, account_id, ENDPOINT_PROFILE_HTML)
    return pk


BLOCKED = 'blocked'
ACCOUNT_DOWN = frozenset({'checkpoint', 'proxy_dead'})

# Profile fetched via two routes: the API users/{pk}/info/ and the profile
# page (the same one that returns pk). Measured 2026-09-28: API returns 429
# IP-wide, the page returns 200.
PROFILE_ROUTES = (ENDPOINT_PROFILE, ENDPOINT_PROFILE_HTML)


def fetch_profile(stores, transport, account_id, pk, nick):
    """(profile, OK) | (None, EMPTY) — no such profile | (None, BLOCKED) — both
    routes are currently unavailable: requeue the task, the pool will hand it
    to HikerAPI in hybrid mode."""
    archive = stores.archive
    if pk and health.pick_account(archive, ENDPOINT_PROFILE, [account_id]) is not None:
        profile, status = transport.profile_info(pk)
        if status == OK and profile:
            health.record_success(archive, account_id, ENDPOINT_PROFILE)
            return profile, OK
        if status not in FAILURES:
            health.record_success(archive, account_id, ENDPOINT_PROFILE)
            return None, EMPTY
        health.record_failure(archive, account_id, ENDPOINT_PROFILE, status)
        if status in ACCOUNT_DOWN:
            return None, BLOCKED  # session is suspect — don't push through with the second route
    if nick and health.pick_account(archive, ENDPOINT_PROFILE_HTML, [account_id]) is not None:
        # pk unknown — read the page far enough to get it; otherwise the bio alone is enough (saves VPN traffic)
        profile, status = (transport.profile_by_nick(nick, need_pk=True) if not pk
                           else transport.profile_by_nick(nick))
        if status == OK and profile:
            health.record_success(archive, account_id, ENDPOINT_PROFILE_HTML)
            return profile, OK
        if status not in FAILURES:
            health.record_success(archive, account_id, ENDPOINT_PROFILE_HTML)
            return None, EMPTY
        health.record_failure(archive, account_id, ENDPOINT_PROFILE_HTML, status)
    return None, BLOCKED


def _bridge_accept(stores, venue_name, city, username, pk, via='мост'):
    """Venue account confirmed: into the registry, plus a bio and following walk — if not already walked."""
    archive = stores.archive
    twin = stores.main.execute(
        'SELECT 1 FROM venues WHERE pk = %s AND NOT (name = %s AND city = %s) LIMIT 1',
        (str(pk), venue_name, city)).fetchone()
    venues.set_account(stores.main, venue_name, city, username, pk)
    print('%s: %s (%s) -> @%s' % (via, venue_name, city, username))
    if twin:
        # account is already linked to another registry row — its bio and
        # following have already been walked; walking again would waste ~450 requests
        return
    wq.enqueue(archive, 'venue_header',
               {'venue_name': venue_name, 'city': city, 'ig_account': username,
                'pk': str(pk)}, priority=55)
    wq.enqueue(archive, 'following',
               {'pk': str(pk), 'venue': venue_name, 'city': city, 'page': 1,
                'max_pages': MAX_FOLLOWING_PAGES}, priority=70)


def handle_bridge_check(stores, transport, account_id, task):
    """Handles for a venue found by the web search bridge (leadgen.sources.websearch.run_bridge_once):
    fetch the profile page and run the same bridge checks. If none confirm, fall back to the
    regular Instagram-search bridge."""
    archive = stores.archive
    payload = task['payload']
    venue_name, city = payload['venue_name'], payload['city']
    address = (stores.main.execute('SELECT address FROM venues WHERE name = %s AND city = %s',
                                   (venue_name, city)).fetchone() or {}).get('address')
    for nick in payload.get('nicks') or []:
        profile, outcome = fetch_profile(stores, transport, account_id, None, nick)
        if outcome == BLOCKED:
            wq.fail(archive, task['id'], requeue=True)
            return
        if not profile:
            continue
        pk = profile.get('pk') or profile.get('id')
        if profile.get('_source') == 'page' or not profile.get('category'):
            ok, _ = quality.bridge_verify_web(nick, profile.get('full_name'), profile.get('biography'),
                                              venue_name, city, address, stores.main)
        else:
            ok, _ = quality.bridge_verify(nick, profile.get('full_name'), profile, venue_name, city)
        if ok and pk:
            contacts.cache_profile(stores, pk, profile)
            _bridge_accept(stores, venue_name, city, nick, pk, via='мост-поисковик')
            wq.complete(archive, task['id'])
            return
    if not payload.get('no_fallback'):
        wq.enqueue(archive, 'bridge', {'venue_name': venue_name, 'city': city, 'web_tried': True}, priority=60)
    wq.complete(archive, task['id'])


def handle_bridge(stores, transport, account_id, task):
    archive = stores.archive
    payload = task['payload']
    venue_name, city = payload['venue_name'], payload['city']
    known = stores.main.execute("SELECT 1 FROM venues WHERE name = %s AND city = %s AND coalesce(ig_account, '') <> ''",
                                (venue_name, city)).fetchone()
    if known:
        # account was already found (e.g. via a website link) — don't spend a search on it
        wq.complete(archive, task['id'])
        return
    users, status = transport.search('%s %s' % (venue_name, city))
    if status in FAILURES:
        health.record_failure(archive, account_id, ENDPOINT_SEARCH, status)
        wq.fail(archive, task['id'], requeue=True)
        print('мост: %s (%s) — %s, задача возвращена' % (venue_name, city, status))
        return
    if status == EMPTY:
        outcome = _empty_search(stores, transport, account_id, task)
        print('мост: %s (%s) — пустая выдача, задача %s' % (venue_name, city, outcome))
        return
    health.record_success(archive, account_id, ENDPOINT_SEARCH)
    for u in users or []:
        pk = u.get('pk')
        if not pk:
            continue
        username, full_name = u.get('username', ''), u.get('full_name', '')
        # cheap name check BEFORE fetching the profile
        if not quality.name_matches(username, full_name, venue_name):
            continue
        profile, outcome = fetch_profile(stores, transport, account_id, pk, username)
        if outcome == BLOCKED:
            wq.fail(archive, task['id'], requeue=True)
            print('мост: %s (%s) — профиль недоступен ни API, ни страницей, задача возвращена'
                  % (venue_name, city))
            return
        if not profile:
            continue
        contacts.cache_profile(stores, pk, profile)
        if profile.get('_source') == 'page':
            # page without business fields: check the bio against the 2GIS address for the city
            address = (stores.main.execute('SELECT address FROM venues WHERE name = %s AND city = %s',
                                           (venue_name, city)).fetchone() or {}).get('address')
            ok, _reason = quality.bridge_verify_web(username, full_name, profile.get('biography'),
                                                    venue_name, city, address, stores.main)
        else:
            ok, _reason = quality.bridge_verify(username, full_name, profile, venue_name, city)
        if ok:
            _bridge_accept(stores, venue_name, city, username, pk)
            break
    else:
        print('мост: %s (%s) — подходящий аккаунт не найден' % (venue_name, city))
    wq.complete(archive, task['id'])


def _walked_before(ig_account):
    """Ledger of following-walks done by the old tool (db/tools/venue_walk.py)."""
    path = os.environ.get('LEADGEN_WALKED_LEDGER') or os.path.join(ROOT, 'db', 'cache', 'walked_venues.txt')
    if not os.path.exists(path):
        return False
    with open(path, encoding='utf-8') as f:
        return any(line.strip().lower() == ig_account.lower() for line in f)


def _queue_walk(stores, pk, ig_account, venue_name, city, rewalk=None, pages=None):
    """Walking a venue's following list — the main lead channel. Once per account:
    skip if it's already queued or in the ledger of past walks. rewalk is a tag for a
    repeat walk: the ledger and past walk don't block it, once per tag, deeper in pages."""
    premium = stores.main.execute("SELECT 1 FROM venues WHERE lower(ig_account) = %s AND level = 'PREMIUM' LIMIT 1",
                                  ((ig_account or '').lower(),)).fetchone() is not None
    if rewalk:
        if stores.archive.execute("SELECT 1 FROM work_queue WHERE task_type = 'following' "
                                  "AND payload->>'pk' = %s AND payload->>'rewalk' = %s LIMIT 1",
                                  (str(pk), rewalk)).fetchone():
            return False
        wq.enqueue(stores.archive, 'following',
                   {'pk': str(pk), 'venue': venue_name, 'city': city, 'page': 1,
                    'max_pages': pages or (PREMIUM_PAGES if premium else REWALK_PAGES), 'rewalk': rewalk},
                   priority=REWALK_PRIORITY)
        return True
    if _walked_before(ig_account):
        return False
    if stores.archive.execute("SELECT 1 FROM work_queue WHERE task_type = 'following' "
                              "AND payload->>'pk' = %s LIMIT 1", (str(pk),)).fetchone():
        return False
    wq.enqueue(stores.archive, 'following',
               {'pk': str(pk), 'venue': venue_name, 'city': city, 'page': 1,
                'max_pages': PREMIUM_PAGES if premium else MAX_FOLLOWING_PAGES}, priority=70)
    return True


def handle_venue_header(stores, transport, account_id, task):
    archive = stores.archive
    payload = task['payload']
    venue_name, city, ig_account = payload['venue_name'], payload['city'], payload['ig_account']
    pk = payload.get('pk') or venues.stored_pk(stores.main, ig_account)
    if not pk:
        pk = resolve_pk(stores, transport, account_id, ig_account)
        if not pk:
            _requeue_or_close(stores, task)
            return
        venues.store_pk(stores.main, ig_account, pk)
    profile, outcome = fetch_profile(stores, transport, account_id, pk, ig_account)
    if outcome == BLOCKED:
        wq.fail(archive, task['id'], requeue=True)
        return
    if not profile:
        wq.complete(archive, task['id'])
        return
    contacts.cache_profile(stores, pk, profile)
    bio = profile.get('biography', '') or ''
    # the BACK regex can truncate a handle when the role word sits inside the
    # handle itself ("Шеф @ivan_chef" -> 'ivan_'). Keep only handles fully mentioned in the bio.
    full_mentions = {m.strip('.').lower() for m in bio_roles.MENTION.findall(bio)}
    queued = 0
    for role, nick in bio_roles.extract_role_mentions(bio):
        nick = contacts.norm_nick(nick)
        if nick not in full_mentions or role == 'упомянут' or nick == ig_account.lower():
            continue
        if contacts.is_known(stores, nick):
            continue
        wq.enqueue(archive, 'profile_info',
                   {'nick': nick, 'role': role, 'venue': venue_name, 'city': city,
                    'source': 'venue_header'}, priority=60)
        queued += 1
    if _queue_walk(stores, pk, ig_account, venue_name, city, rewalk=payload.get('rewalk'), pages=payload.get('pages')):
        print('шапка: %s — поставлен обход подписок' % ig_account)
    print('шапка: %s (%s) @%s — людей из шапки: %d' % (venue_name, city or '—', ig_account, queued))
    wq.complete(archive, task['id'])


def handle_following(stores, transport, account_id, task):
    archive = stores.archive
    payload = task['payload']
    pk = payload['pk']
    users, next_max_id, status = transport.following(pk, max_id=payload.get('max_id'))
    if status in FAILURES:
        health.record_failure(archive, account_id, ENDPOINT_FOLLOWING, status)
        wq.fail(archive, task['id'], requeue=True)
        return
    health.record_success(archive, account_id, ENDPOINT_FOLLOWING)
    queued = 0
    for u in users or []:
        username, full_name, upk = u.get('username', ''), u.get('full_name', ''), u.get('pk')
        if not upk or not username:
            continue
        if not quality.looks_like_person(username, full_name):
            continue
        if contacts.is_known(stores, username, upk):
            continue
        wq.enqueue(archive, 'profile_info',
                   {'nick': contacts.norm_nick(username), 'pk': str(upk),
                    'venue': payload.get('venue', ''), 'city': payload.get('city', ''),
                    'source': 'following'}, priority=80)
        queued += 1
    print('подписки: pk=%s (%s) — кандидатов: %d из %d' % (
        pk, payload.get('venue') or '—', queued, len(users or [])))
    page = payload.get('page', 1)
    max_pages = payload.get('max_pages', MAX_FOLLOWING_PAGES)
    if next_max_id and page < max_pages:
        next_payload = dict(payload)
        next_payload.update({'max_id': next_max_id, 'page': page + 1})
        wq.enqueue(archive, 'following', next_payload,
                   priority=REWALK_PRIORITY if payload.get('rewalk') else 70)
    wq.complete(archive, task['id'])


SOURCE_WEBSEARCH = 'поисковик'  # leadgen/websearch.py
# sources where venue accounts turn up alongside people — walk these instead of discarding them
PLACE_SOURCES = (SOURCE_WEBSEARCH, 'премия', 'отметки', 'похожие', 'геометки')  # leadgen/expand.py
PLACE_WALK_REASON = 'аккаунт заведения — обход подписок'


def _russian(profile):
    """Signs of Russia in the bio: walking a foreign venue's following would mean collecting foreigners."""
    from leadgen.qualify import review
    text = '%s %s %s' % (profile.get('biography') or '', profile.get('full_name') or '', profile.get('city_name') or '')
    return bool(review.CYRILLIC.search(text) or review.RUSSIA_LATIN.search(text))


def _is_place(nick, profile):
    from leadgen.qualify import vetting  # vetting imports handlers
    return vetting._is_place(nick, profile)


# A bio reads as a place, not a person: opening hours, "since 2009", "family
# restaurant", a first line like "Турецкий ресторан …". Just a phone number
# and the word "ресторан" aren't enough — chefs have those too (2026-09-30:
# the first version of this rule dropped 28 chefs like "Chef @restaurant.tarragon1").
VENUE_SIGNS = re.compile(r'\d{1,2}[:.]\d{2}\s*[-–—]\s*\d{1,2}[:.]\d{2}|пн[-–—\s]*вс|ежедневно|daily'
                         r'|\bс\s+\d{4}\s+года|since\s+\d{4}|семейный ресторан|наш ресторан'
                         # speech in the venue's own voice (incident 2026-10-06, "Улов":
                         # "сами ловим, сами готовим, место, где пахнет морем… бренд-шеф @maple1")
                         # "мы" and "готовим" alone aren't matched: chefs write things like
                         # "жизнь — кухня, на которой мы готовим…"
                         r'|место,?\s+где|\bсами\s+(?:ловим|готовим|печ[её]м|варим)\b'
                         r'|\bнаш[аеиу]?\s+(?:ресторан|кухня|команда|шеф|бар)\b|\bбронь\b|\bбронирование\b'
                         r'|\bработаем\s+(?:круглосуточно|ежедневно|с\s+\d)|первый ресторан|доставка', re.I)
SELF_DESCRIPTION = re.compile(r'^\W*(?:[\w-]+\s+){0,2}(?:ресторан|бистро|кафе|пиццерия|остерия|траттория|гастробар)\b',
                              re.I)


def _venue_naming_staff(nick, profile):
    """Bio names a role for a DIFFERENT account, while itself reading as a venue."""
    bio = profile.get('biography') or ''
    others = [n for r, n in bio_roles.extract_role_mentions(bio)
              if r != 'упомянут' and contacts.norm_nick(n) != nick]
    if not others:
        return False
    category = profile.get('category') or ''
    from leadgen.qualify import vetting  # vetting imports handlers
    return (bool(category and vetting.VENUE_PLACE_CAT.search(category)) or bool(VENUE_SIGNS.search(bio))
            or bool(SELF_DESCRIPTION.search(bio)))


def _matches_enabled_role(stores, category, bio):
    from leadgen.qualify import roles
    role, stop = roles.match(roles.load_enabled(stores.main), category, bio)
    return role is not None and not stop


def handle_profile_info(stores, transport, account_id, task):
    archive = stores.archive
    payload = task['payload']
    nick = contacts.norm_nick(payload['nick'])
    if contacts.is_known(stores, nick, payload.get('pk')):
        wq.complete(archive, task['id'])
        return
    pk = payload.get('pk')
    if not pk:
        pk = resolve_pk(stores, transport, account_id, nick)
        if not pk:
            _requeue_or_close(stores, task)
            return
        if contacts.is_known(stores, nick, pk):
            wq.complete(archive, task['id'])
            return
    profile, outcome = fetch_profile(stores, transport, account_id, pk, nick)
    if outcome == BLOCKED:
        wq.fail(archive, task['id'], requeue=True)
        return
    if not profile:
        wq.complete(archive, task['id'])
        return
    contacts.cache_profile(stores, pk, profile)
    full_name = profile.get('full_name', '') or ''
    bio = profile.get('biography', '') or ''
    category = profile.get('category', '') or ''
    city_name = profile.get('city_name', '') or ''
    city = payload.get('city', '') or city_name
    # A role named by the venue itself ("Шеф @ник" in the bio) is the most
    # reliable signal. The bar filter still applies to it: bars on pause since 2026-09-16.
    payload_role = (payload.get('role') or '').strip()
    role_from_venue = (bool(payload_role) and payload_role.lower() != 'упомянут'
                       and quality.has_role(payload_role, '', '', ''))
    accepted = (role_from_venue or quality.has_role(bio, full_name, category, city_name)
                # enabled roles without the words "шеф/повар" — e.g. consulting ("turnkey restaurant launch")
                or _matches_enabled_role(stores, category, bio))
    if accepted and _venue_naming_staff(nick, profile):
        # "Шеф-повар Алексей Волков @ember1 📞… ⏰ 12:00-23:00" — this is the venue
        # naming its chef, not the person speaking: the chef gets picked up from the
        # venue's own bio, via the following walk
        accepted = False
        contacts.reject(stores, nick, PLACE_WALK_REASON, 'роль', pk=pk, full_name=full_name, city=city,
                        source=payload.get('source', ''))
        wq.enqueue(archive, 'venue_header', {'venue_name': full_name or nick, 'city': city,
                                             'ig_account': nick, 'pk': str(pk)}, priority=60)
    elif accepted:
        contacts.add_candidate(stores, nick, pk, profile, dict(payload, city=city), AGENT_VENUE)
        wq.enqueue(archive, 'vet', {'nick': nick, 'pk': str(pk)}, priority=40)
    elif payload.get('source') in PLACE_SOURCES and _is_place(nick, profile) and _russian(profile):
        # the web search also surfaces venues outside the 2GIS registry: bio + following walk is a lead channel
        contacts.reject(stores, nick, PLACE_WALK_REASON, 'роль', pk=pk,
                        full_name=full_name, city=city, source=payload.get('source', ''))
        wq.enqueue(archive, 'venue_header', {'venue_name': full_name or nick, 'city': city,
                                             'ig_account': nick, 'pk': str(pk)}, priority=60)
    else:
        contacts.reject(stores, nick, 'нет роли в шапке', 'роль', pk=pk, full_name=full_name,
                        venue=payload.get('venue', ''), city=city,
                        source=payload.get('source', ''))
    print('кандидат: @%-24s %-9s %s | %s' % (
        nick, 'ПРИНЯТ' if accepted else 'отклонён',
        (payload.get('venue') or city or '—')[:24], payload.get('source') or '—'))
    wq.complete(archive, task['id'])


HANDLERS = {
    'bridge': handle_bridge,
    'bridge_check': handle_bridge_check,
    'venue_header': handle_venue_header,
    'following': handle_following,
    'profile_info': handle_profile_info,
}

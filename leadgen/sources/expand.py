"""New candidate sources derived from already-known venues (2026-10-01).

"Tags" ("отметки") — people tagged in the venue's last 12 posts, plus post co-authors: chefs
featured in plating shots. "Similar" ("похожие") — similar accounts from the venue's page:
mostly restaurants in the same city that aren't in 2GIS (measured: ~40 per venue, more than half
new). Venues from both channels get walked (header + subscriber list), people with a job title
become candidates; one hop only, no "similar of similar" chains. One request per venue, run at
lower priority than the main job.

    deploy/local/run.sh leadgen.sources.expand            # label — current month
"""
import sys
from datetime import date

from leadgen.core import contacts, db, queue as wq
from leadgen.access import health
from leadgen.qualify import vetting
from leadgen.access.transport import FAILURES

ENDPOINT_SIMILAR = 'similar'
ENDPOINT_LOCATION = 'location'
SOURCE_TAGS = 'отметки'
SOURCE_SIMILAR = 'похожие'
SOURCE_GEO = 'геометки'
SOURCES = (SOURCE_TAGS, SOURCE_SIMILAR, SOURCE_GEO)
SEED_PRIORITY = 90       # lower priority than fresh work and the re-walk pass (85)
PEOPLE_PRIORITY = 82
# Measured 2026-10-05, profiles per accepted contact: headers 7, tags 190, subscriptions 1,080,
# similar 1,600, geo tags 3,200. Similar runs after venue headers (85) and seeds (90);
# geo tags aren't seeded at all (restaurant guests, not chefs).
SIMILAR_PEOPLE_PRIORITY = 95
SEED_GEO = False
TARGET_LEVELS = ('PREMIUM', 'UPPER-MIDDLE')
# "Similar" checked for chefs on 2026-10-01: 11 out of 11 personal chef accounts returned an
# empty list (Instagram only serves recommendations for business accounts) — channel not wired up.


def _queued(archive, nick):
    return archive.execute("SELECT 1 FROM work_queue WHERE task_type = 'profile_info' "
                           "AND payload->>'nick' = %s LIMIT 1", (nick,)).fetchone() is not None


def _known_venue(main, nick):
    return main.execute('SELECT 1 FROM venues WHERE lower(ig_account) = %s LIMIT 1',
                        (nick,)).fetchone() is not None


def _new(stores, nick):
    return not (contacts.is_known(stores, nick) or _known_venue(stores.main, nick)
                or _queued(stores.archive, nick))


def _blocked(stores, account_id, endpoint, status, task):
    health.record_failure(stores.archive, account_id, endpoint, status)
    wq.fail(stores.archive, task['id'], requeue=True)


def handle_venue_tags(stores, transport, account_id, task):
    payload = task['payload']
    venue_nick = contacts.norm_nick(payload['ig_account'])
    if not hasattr(transport, 'web_feed'):
        wq.complete(stores.archive, task['id'])
        return
    items, status = transport.web_feed(venue_nick)
    if status in FAILURES:
        _blocked(stores, account_id, vetting.ENDPOINT_WEB_FEED, status, task)
        return
    health.record_success(stores.archive, account_id, vetting.ENDPOINT_WEB_FEED)
    people = []
    for it in items or []:
        for nick in it.get('people') or []:
            nick = contacts.norm_nick(nick)
            if nick != venue_nick and nick not in people:
                people.append(nick)
    added = 0
    for nick in people:
        if _new(stores, nick):
            wq.enqueue(stores.archive, 'profile_info',
                       {'nick': nick, 'venue': payload.get('venue') or '', 'city': payload.get('city') or '',
                        'source': SOURCE_TAGS}, priority=PEOPLE_PRIORITY)
            added += 1
    print('отметки: @%s — людей %d, новых %d' % (venue_nick, len(people), added))
    wq.complete(stores.archive, task['id'])


def handle_similar(stores, transport, account_id, task):
    payload = task['payload']
    if not hasattr(transport, 'suggested'):
        wq.complete(stores.archive, task['id'])
        return
    users, status = transport.suggested(payload['pk'], payload['ig_account'])
    if status in FAILURES:
        _blocked(stores, account_id, ENDPOINT_SIMILAR, status, task)
        return
    health.record_success(stores.archive, account_id, ENDPOINT_SIMILAR)
    added = 0
    for u in users or []:
        nick = contacts.norm_nick(u['username'])
        if _new(stores, nick):
            # similar account's city is unknown — don't attribute it to the source venue's city
            wq.enqueue(stores.archive, 'profile_info',
                       {'nick': nick, 'pk': str(u['pk']), 'venue': '', 'city': '', 'source': SOURCE_SIMILAR},
                       priority=SIMILAR_PEOPLE_PRIORITY)
            added += 1
    print('похожие: @%s — аккаунтов %d, новых %d' % (payload['ig_account'], len(users or []), added))
    wq.complete(stores.archive, task['id'])


def handle_venue_geo(stores, transport, account_id, task):
    """Venue's geo tag — the most frequent one on its own posts; its popular posts are
    plating shots that chefs post (measured 10-01: "Жажда крови" location → chef @sage1)."""
    from collections import Counter
    payload = task['payload']
    venue_nick = contacts.norm_nick(payload['ig_account'])
    if not hasattr(transport, 'location_people'):
        wq.complete(stores.archive, task['id'])
        return
    items, status = transport.web_feed(venue_nick)
    if status in FAILURES:
        _blocked(stores, account_id, vetting.ENDPOINT_WEB_FEED, status, task)
        return
    health.record_success(stores.archive, account_id, vetting.ENDPOINT_WEB_FEED)
    pks = Counter((it.get('loc') or {}).get('pk') for it in items or [] if (it.get('loc') or {}).get('pk'))
    if not pks:
        wq.complete(stores.archive, task['id'])
        return
    location_pk = pks.most_common(1)[0][0]
    people, status = transport.location_people(location_pk)
    if status in FAILURES:
        _blocked(stores, account_id, ENDPOINT_LOCATION, status, task)
        return
    health.record_success(stores.archive, account_id, ENDPOINT_LOCATION)
    added = 0
    for nick in people or []:
        nick = contacts.norm_nick(nick)
        if nick != venue_nick and _new(stores, nick):
            wq.enqueue(stores.archive, 'profile_info',
                       {'nick': nick, 'venue': payload.get('venue') or '', 'city': payload.get('city') or '',
                        'source': SOURCE_GEO}, priority=PEOPLE_PRIORITY)
            added += 1
    print('геометки: @%s — людей %d, новых %d' % (venue_nick, len(people or []), added))
    wq.complete(stores.archive, task['id'])


HANDLERS = {'venue_tags': handle_venue_tags, 'similar': handle_similar, 'venue_geo': handle_venue_geo}


def enqueue(stores, label):
    """"Tags" and "similar" tasks for all venues of the target level — once per label."""
    done = {(r['t'], r['ig']) for r in stores.archive.execute(
        "SELECT task_type AS t, payload->>'ig_account' AS ig FROM work_queue "
        "WHERE task_type IN ('venue_tags', 'similar', 'venue_geo') AND payload->>'label' = %s", (label,)).fetchall()}
    rows = stores.main.execute(
        "SELECT name, city, ig_account, pk FROM venues WHERE level = ANY(%s) AND coalesce(ig_account, '') <> '' "
        "ORDER BY level = 'PREMIUM' DESC, id", (list(TARGET_LEVELS),)).fetchall()
    counts = {'venue_tags': 0, 'similar': 0, 'venue_geo': 0}
    for r in rows:
        ig = r['ig_account'].strip().lstrip('@').lower()
        if ('venue_tags', ig) not in done:
            wq.enqueue(stores.archive, 'venue_tags', {'ig_account': ig, 'venue': r['name'], 'city': r['city'] or '',
                                                      'label': label}, priority=SEED_PRIORITY)
            done.add(('venue_tags', ig))
            counts['venue_tags'] += 1
        if SEED_GEO and ('venue_geo', ig) not in done:
            wq.enqueue(stores.archive, 'venue_geo', {'ig_account': ig, 'venue': r['name'], 'city': r['city'] or '',
                                                     'label': label}, priority=SEED_PRIORITY)
            done.add(('venue_geo', ig))
            counts['venue_geo'] += 1
        if r['pk'] and ('similar', ig) not in done:
            wq.enqueue(stores.archive, 'similar', {'ig_account': ig, 'pk': str(r['pk']), 'label': label},
                       priority=SEED_PRIORITY)
            done.add(('similar', ig))
            counts['similar'] += 1
    return counts


def our_venue_test(stores):
    """Venue is ours: its city is in the registry, or, if there's no city (a workplace taken
    from a chef's header), it has a chef with a department attached to it."""
    cities = {r['city'] for r in stores.main.execute('SELECT city FROM geo WHERE department IS NOT NULL').fetchall()}
    linked = {r['v'] for r in stores.main.execute(
        "SELECT DISTINCT lower(venue) AS v FROM contacts WHERE department IS NOT NULL AND coalesce(venue, '') <> ''").fetchall()}

    def ours(row):
        city = (row.get('city') or '').strip()
        if city and city != '—':
            return city in cities
        return (row.get('name') or '').lower() in linked
    return ours


def enqueue_new(stores):
    """"Tags", "similar", geo tags — for venues that haven't had any of these under any label
    yet: target level, or unknown level but in one of our cities (workplaces from chef headers)."""
    seeded = {r['ig'] for r in stores.archive.execute(
        "SELECT DISTINCT payload->>'ig_account' AS ig FROM work_queue WHERE task_type = 'venue_tags'").fetchall()}
    ours = our_venue_test(stores)
    rows = stores.main.execute(
        "SELECT name, city, level, ig_account, pk FROM venues WHERE coalesce(ig_account, '') <> '' "
        "AND (level = ANY(%s) OR coalesce(level, '') = '') ORDER BY level = 'PREMIUM' DESC, id",
        (list(TARGET_LEVELS),)).fetchall()
    counts = {'venue_tags': 0, 'similar': 0, 'venue_geo': 0}
    for r in rows:
        ig = r['ig_account'].strip().lstrip('@').lower()
        if ig in seeded or not ours(r):
            continue
        seeded.add(ig)
        base = {'ig_account': ig, 'venue': r['name'], 'city': r['city'] or '', 'label': 'auto'}
        wq.enqueue(stores.archive, 'venue_tags', base, priority=SEED_PRIORITY)
        counts['venue_tags'] += 1
        if SEED_GEO:
            wq.enqueue(stores.archive, 'venue_geo', base, priority=SEED_PRIORITY)
            counts['venue_geo'] += 1
        if r['pk']:
            wq.enqueue(stores.archive, 'similar', {'ig_account': ig, 'pk': str(r['pk']), 'label': 'auto'},
                       priority=SEED_PRIORITY)
            counts['similar'] += 1
    return counts


if __name__ == '__main__':
    label = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime('%Y-%m')
    print('новые источники %s: %s' % (label, enqueue(db.open_stores(), label)))

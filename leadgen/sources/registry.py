"""Registry of venues from 2GIS — the main source of new leads.

    python -m leadgen.sources.registry

The East has been exhausted under the old registry: only expanding the registry
yields new contacts. A "city x category" pair is walked once every 30 days;
only PREMIUM and UPPER-MIDDLE get saved, no stop-format venues; every new
venue gets a bridge task queued — the rest of the pipeline is as usual."""
import os
import sys
import time

from leadgen.core import alerts, db, settings
from leadgen.qualify import geo, rules
from leadgen.sources import gis, gis_cities
from leadgen.core import queue as wq

LEVELS = ('PREMIUM', 'UPPER-MIDDLE')


def _record(stores, slug, query, status, found, added):
    stores.archive.execute(
        'INSERT INTO registry_runs (slug, query, harvested_at, status, found, added) '
        'VALUES (%s, %s, now(), %s, %s, %s) ON CONFLICT (slug, query) DO UPDATE SET '
        'harvested_at = now(), status = excluded.status, found = excluded.found, added = excluded.added',
        (slug, query, status, found, added))


END_OF_RESULTS = ('http_404', 'wrong_city')
BAD_SLUG = 'bad_slug'


def _alert_once_a_day(stores, text):
    seen = stores.archive.execute(
        "SELECT 1 FROM alerts WHERE kind = 'registry' AND text = %s "
        "AND created_at > now() - interval '1 day' LIMIT 1", (text,)).fetchone()
    if not seen:
        alerts.raise_alert(stores.archive, 'registry', text)


def _site_bridge(stores, client, slug, city, v, queue_search):
    """Venue's account from a link on its website — no Instagram search involved.
    Not found — fall back to the usual search-based bridge (for a new venue)."""
    finder = getattr(client, 'site_instagram', None)
    nick = None
    if finder and v.get('gis_id'):
        nick, _status = finder(slug, v['gis_id'], v['name'])
    stores.main.execute('UPDATE venues SET site_checked_at = now() WHERE name = %s AND city = %s',
                        (v['name'], city))
    if nick:
        stores.main.execute(
            "UPDATE venues SET ig_account = %s, status = 'аккаунт с сайта' WHERE name = %s AND city = %s",
            (nick, v['name'], city))
        wq.enqueue(stores.archive, 'venue_header',
                   {'venue_name': v['name'], 'city': city, 'ig_account': nick}, priority=55)
    elif queue_search:
        wq.enqueue(stores.archive, 'web_bridge', {'venue_name': v['name'], 'city': city}, priority=50)


def harvest(stores, client, slug, city, query, pages=6):
    found = added = 0
    status = 'ok'
    department = gis_cities.department(slug)
    if department and geo.department_for_city(stores.main, city) is None:
        # a new registry city must be added to geography — otherwise all its leads have no department
        region = {'НСК': 'Восток', 'МСК': 'Москва', 'СПБ': 'Питер'}[department]
        geo.upsert_city(stores.main, city, region, department)
    for page in range(1, pages + 1):
        venues, status = client.search(slug, query, page)
        if status in END_OF_RESULTS and page > 1:
            # small cities have fewer than 6 pages: beyond that it's 404 or a neighboring city
            status = 'ok'
            break
        if status != 'ok':
            if status in END_OF_RESULTS:
                # empty on page 1: for the main category, 2GIS doesn't know the slug;
                # for a narrow one ("steak house" in Rubtsovsk), the city just has no such venues
                status = BAD_SLUG if query == gis_cities.RUBRICS[0] else 'ok'
            break
        if not venues:
            break
        found += len(venues)
        for v in venues:
            if v['level'] not in LEVELS:
                continue
            if rules.STOP_FORMAT.search('%s %s %s' % (v['name'], v['kind'], ' '.join(v['rubrics']))):
                continue
            row = stores.main.execute(
                'INSERT INTO venues (name, city, region, level, avg_check, rating, status, source, address, gis_id) '
                "VALUES (%s, %s, '', %s, %s, %s, 'не отработано', '2ГИС', %s, %s) "
                # for a known venue — only fill in a missing address and card id
                "ON CONFLICT (name, city) DO UPDATE SET address = COALESCE(NULLIF(venues.address, ''), excluded.address), "
                'gis_id = COALESCE(venues.gis_id, excluded.gis_id) '
                "RETURNING (xmax = 0) AS inserted, coalesce(ig_account, '') AS ig, site_checked_at",
                (v['name'], city, v['level'], v['check'], str(v['rating'] or ''), v.get('address') or '',
                 v.get('gis_id'))).fetchone()
            if row['inserted']:
                added += 1
            if not row['ig'] and row['site_checked_at'] is None:
                _site_bridge(stores, client, slug, city, v, queue_search=row['inserted'])
    if status == BAD_SLUG:
        _alert_once_a_day(stores, '2ГИС не знает слаг «%s» (%s) — город пропускается; '
                                  'поправьте слаг в leadgen/gis_cities.py.' % (slug, city))
    elif status != 'ok':
        _alert_once_a_day(stores, '2ГИС: сбой обхода реестра (%s) — возможно, капча или новая '
                                  'вёрстка; обход продолжится позже.' % status)
    _record(stores, slug, query, status, found, added)
    return {'status': status, 'found': found, 'added': added}


def next_pairs(stores, limit=10, days=30, retry_days=1):
    # a successful pair — once every 30 days; a failure — retry next day, not "burned" for a month
    recent = {(r['slug'], r['query']) for r in stores.archive.execute(
        "SELECT slug, query FROM registry_runs WHERE "
        "(status IN ('ok', 'bad_slug') AND harvested_at > now() - %s * interval '1 day') OR "
        "(status NOT IN ('ok', 'bad_slug') AND harvested_at > now() - %s * interval '1 day')",
        (days, retry_days)).fetchall()}
    city_of = dict(gis_cities.CITIES)
    first, rest = gis_cities.RUBRICS[:1], gis_cities.RUBRICS[1:]
    pairs = [(slug, city, q) for q in first for slug, city in gis_cities.CITIES]
    # districts of large cities — after the base pass over all cities, before the other categories
    pairs += [(slug, city_of[slug], '%s %s' % (getattr(gis_cities, 'DISTRICT_RUBRIC', 'рестораны'), d))
              for slug, districts in getattr(gis_cities, 'DISTRICTS', {}).items() if slug in city_of
              for d in districts]
    pairs += [(slug, city, q) for q in rest for slug, city in gis_cities.CITIES]
    unlocked = int(settings.get(stores.main, 'gis_extra_rubrics') or 0)
    pairs += [(slug, city, q) for q in getattr(gis_cities, 'EXTRA_RUBRICS', [])[:unlocked]
              for slug, city in gis_cities.CITIES]
    out = []
    for slug, city, query in pairs:
        if (slug, query) not in recent:
            out.append((slug, city, query))
            if len(out) >= limit:
                return out
    return out


def run_once(stores, client, limit=10):
    if settings.get(stores.main, 'paused') == '1':
        return 0
    done = 0
    for slug, city, query in next_pairs(stores, limit):
        out = harvest(stores, client, slug, city, query)
        print('реестр: %s «%s» — %s, найдено %d, новых %d' % (city, query, out['status'], out['found'], out['added']))
        done += 1
        if out['status'] not in ('ok', BAD_SLUG):
            break  # 2GIS failure (captcha, network, rate limit) — the rest of the pairs wait for the next round
    return done


def main():
    sys.stdout.reconfigure(line_buffering=True)
    stores = db.open_stores(os.environ['MAIN_DB_URL'], os.environ['ARCHIVE_DB_URL'])
    db.init_main(stores.main)
    db.init_archive(stores.archive)
    client = gis.GisClient()
    while True:
        try:
            if run_once(stores, client) == 0:
                time.sleep(600)  # everything's fresh, or paused
            else:
                time.sleep(60)
        except Exception as e:
            print('реестр: ошибка: %s' % e, file=sys.stderr)
            stores.close()
            time.sleep(60)
            stores = db.open_stores(os.environ['MAIN_DB_URL'], os.environ['ARCHIVE_DB_URL'])


if __name__ == '__main__':
    main()

"""Automatic source switching (2026-10-05): client request — "if one source runs dry, other
ones should kick in automatically". On 10-03 the queue went empty and collection sat idle for
two days while a ready reserve just waited.

A supervisor checks the queue every CHECK_MINUTES. If there are fewer tasks than LOW_WATER,
sources are tried in order: the first one that yields tasks is all we need this round. A source
that yields nothing goes on cooldown (each has its own cooldown period), and the next one is
tried. Everything empty — one alert per day."""
from datetime import date

from leadgen.core import alerts, queue as wq, settings
from leadgen.sources import expand, gis_cities, rewalk
from leadgen.qualify import handlers

LOW_WATER = 3000
CHECK_MINUTES = 15
DEPTH_STEP, DEPTH_MAX = 3, 15


def _pending(archive):
    return archive.execute("SELECT count(*) AS c FROM work_queue WHERE status IN ('pending', 'in_progress')").fetchone()['c']


def walk_new(stores):
    """Header and subscriptions for Instagram venues (in our cities) that haven't been walked yet."""
    headed = {r['ig'] for r in stores.archive.execute(
        "SELECT DISTINCT lower(payload->>'ig_account') AS ig FROM work_queue WHERE task_type = 'venue_header'").fetchall()}
    ours = expand.our_venue_test(stores)
    added = 0
    for r in stores.main.execute(
            "SELECT name, city, ig_account, pk FROM venues WHERE coalesce(ig_account, '') <> '' "
            "AND (level = ANY(%s) OR coalesce(level, '') = '') ORDER BY level = 'PREMIUM' DESC, id",
            (list(expand.TARGET_LEVELS),)).fetchall():
        ig = r['ig_account'].strip().lstrip('@').lower()
        if ig in headed or not ours(r):
            continue
        payload = {'venue_name': r['name'], 'city': r['city'] or '', 'ig_account': ig}
        if r['pk']:
            payload['pk'] = str(r['pk'])
        wq.enqueue(stores.archive, 'venue_header', payload, priority=handlers.REWALK_PRIORITY)
        headed.add(ig)
        added += 1
    return added


def _deep_busy(archive):
    return archive.execute(
        "SELECT 1 FROM work_queue WHERE status IN ('pending', 'in_progress') "
        "AND task_type IN ('venue_header', 'following') AND payload->>'rewalk' LIKE 'deep-%%' LIMIT 1").fetchone()


def _deeper(stores, level, key, start, prefix):
    """Go deeper into subscriptions of venues at `level`: DEPTH_STEP more pages each time, up to DEPTH_MAX."""
    if _deep_busy(stores.archive):
        return None  # previous depth pass is still running — don't stack another one on top
    depth = int(settings.get(stores.main, key) or start)
    if depth >= DEPTH_MAX:
        return 0
    depth = min(DEPTH_MAX, depth + DEPTH_STEP)
    added = rewalk.enqueue(stores, '%s%d' % (prefix, depth), levels=(level,), pages=depth)
    settings.put(stores.main, key, str(depth))
    return added


def deeper_premium(stores):
    return _deeper(stores, 'PREMIUM', 'refill_depth', handlers.PREMIUM_PAGES, 'deep-')


def deeper_upper(stores):
    """2026-10-06: after PREMIUM comes UPPER-MIDDLE, whose subscriptions were walked to MAX_FOLLOWING_PAGES."""
    return _deeper(stores, 'UPPER-MIDDLE', 'refill_depth_um', handlers.MAX_FOLLOWING_PAGES, 'deep-um-')


def unlock_rubric(stores):
    """2GIS: open the next reserve category — the registry will walk it across all cities."""
    n = int(settings.get(stores.main, 'gis_extra_rubrics') or 0)
    if n >= len(gis_cities.EXTRA_RUBRICS):
        return 0
    settings.put(stores.main, 'gis_extra_rubrics', str(n + 1))
    return len(gis_cities.CITIES)


def retag_month(stores):
    """Tags and co-authors on venues' last 12 posts — redone monthly: posts are new, so are the
    chefs on them. ~190 profiles per contact vs. ~1,000 for subscriptions (measured 2026-10-05)."""
    label = date.today().strftime('tags-%Y-%m')
    done = {r['ig'] for r in stores.archive.execute(
        "SELECT payload->>'ig_account' AS ig FROM work_queue WHERE task_type = 'venue_tags' AND payload->>'label' = %s",
        (label,)).fetchall()}
    ours = expand.our_venue_test(stores)
    added = 0
    for r in stores.main.execute(
            "SELECT name, city, ig_account FROM venues WHERE level = ANY(%s) AND coalesce(ig_account, '') <> '' "
            "ORDER BY level = 'PREMIUM' DESC, id", (list(expand.TARGET_LEVELS),)).fetchall():
        ig = r['ig_account'].strip().lstrip('@').lower()
        if ig in done or not ours(r):
            continue
        done.add(ig)
        wq.enqueue(stores.archive, 'venue_tags', {'ig_account': ig, 'venue': r['name'], 'city': r['city'] or '',
                                                  'label': label}, priority=expand.SEED_PRIORITY)
        added += 1
    return added


def bridge_retry(stores):
    """Target-level venues with no account: a second search query — unquoted, with the word
    "ресторан" ("restaurant") added (Instagram often spells the name differently). Once per venue."""
    tried = {(r['n'], r['c']) for r in stores.archive.execute(
        "SELECT payload->>'venue_name' AS n, payload->>'city' AS c FROM work_queue "
        "WHERE task_type = 'web_bridge' AND payload->>'variant' = '2'").fetchall()}
    added = 0
    for r in stores.main.execute(
            "SELECT name, city FROM venues WHERE level = ANY(%s) AND coalesce(ig_account, '') = '' ORDER BY "
            "level = 'PREMIUM' DESC, id", (list(expand.TARGET_LEVELS),)).fetchall():
        if (r['name'], r['city'] or '') in tried:
            continue
        tried.add((r['name'], r['city'] or ''))
        wq.enqueue(stores.archive, 'web_bridge', {'venue_name': r['name'], 'city': r['city'] or '', 'variant': 2},
                   priority=75)
        added += 1
    return added


def monthly_rewalk(stores):
    return rewalk.enqueue(stores, date.today().strftime('%Y-%m'))


SOURCES = [
    ('новые заведения: отметки, похожие, геометки', lambda s: sum(expand.enqueue_new(s).values()), 1),
    ('новые заведения: шапка и подписки', walk_new, 1),
    ('глубже подписки PREMIUM', deeper_premium, 30),
    ('глубже подписки UPPER-MIDDLE', deeper_upper, 30),
    ('2ГИС: новые рубрики', unlock_rubric, 30),
    ('повторный обход месяца', monthly_rewalk, 30),
    # reserves (2026-10-07) — kick in once everything above is empty
    ('отметки заведений: повтор месяца', retag_month, 30),
    ('мост: второй запрос для заведений без аккаунта', bridge_retry, 30),
]


def _table(archive):
    archive.execute('CREATE TABLE IF NOT EXISTS refill_runs (id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, '
                    'added INTEGER NOT NULL, at TIMESTAMPTZ NOT NULL DEFAULT now())')


def _cooling(archive, name, days):
    return archive.execute(
        "SELECT 1 FROM refill_runs WHERE source = %s AND added = 0 AND at > now() - %s * interval '1 day' LIMIT 1",
        (name, days)).fetchone() is not None


def run_once(stores):
    """None — queue isn't running low; (source, count) — refilled; ('', 0) — everything exhausted."""
    archive = stores.archive
    _table(archive)
    if _pending(archive) >= LOW_WATER:
        return None
    for name, seed, cooldown_days in SOURCES:
        if _cooling(archive, name, cooldown_days):
            continue
        added = seed(stores)
        if added is None:
            continue  # source is busy with a previous batch — not exhausted, no cooldown
        archive.execute('INSERT INTO refill_runs (source, added) VALUES (%s, %s)', (name, added))
        if added:
            return name, added
    if not archive.execute("SELECT 1 FROM refill_runs WHERE source = '_исчерпано' "
                           "AND at > now() - interval '1 day' LIMIT 1").fetchone():
        archive.execute("INSERT INTO refill_runs (source, added) VALUES ('_исчерпано', 0)")
        alerts.raise_alert(archive, 'sources', 'Все источники лидов исчерпаны — очередь пуста. '
                                               'Нужны новые: города, рубрики, списки заведений.')
    return '', 0

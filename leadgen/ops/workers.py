"""Workers for the Instagram pipeline, and the supervisor.

    python -m leadgen.ops.workers     # needs MAIN_DB_URL and ARCHIVE_DB_URL

One thread per pool account. Each thread claims a task whose endpoint is
alive for at least one free account, and runs it on the least-loaded one —
own accounts first, then HikerAPI (in hybrid mode). Once a minute the
supervisor reclaims tasks with an expired lease, watches for idle periods,
and rebuilds the pool whenever the account set or mode changes."""
import os
import sys
import threading
import time

import psycopg

from leadgen.access import accounts, health
from leadgen.core import alerts, db, settings
from leadgen.ops import watchdog
from leadgen.core import queue as wq
from leadgen.qualify import handlers as _handlers
from leadgen.sources import expand, festivals, refill
from leadgen.qualify import vetting
from leadgen.qualify.handlers import (ENDPOINT_FOLLOWING, ENDPOINT_PROFILE, ENDPOINT_PROFILE_HTML, ENDPOINT_SEARCH,
                              PK_SHORTCUT_TYPES, RESOLVE_ENDPOINTS)

# vet is a separate module so handlers doesn't depend on rules and grids
HANDLERS = dict(_handlers.HANDLERS, vet=vetting.handle_vet, **expand.HANDLERS, **festivals.HANDLERS)
TASK_TYPES = _handlers.TASK_TYPES + ['vet'] + list(expand.HANDLERS) + list(festivals.HANDLERS)

# Guard against an endless spin: on 09-22 a task with no pk looped 137 times
# and fired 6,426 requests into blocked search.
MAX_TASK_ATTEMPTS = 10
RECONNECT_SECONDS = 30
IDLE_ALERT_SECONDS = 7200


def task_endpoints(task_type, has_pk=False):
    """Groups of endpoints: within a group, one alive endpoint is enough; every
    group is required. The bio comes from the API or the profile page, the feed from the API or /embed/."""
    profile = _handlers.PROFILE_ROUTES
    if task_type == 'vet':
        return ((vetting.ENDPOINT_FEED, vetting.ENDPOINT_EMBED), profile, RESOLVE_ENDPOINTS)
    if has_pk and task_type in PK_SHORTCUT_TYPES:
        return (profile,)
    if task_type == 'following':
        return ((ENDPOINT_FOLLOWING,),)
    if task_type == 'venue_tags':
        return ((vetting.ENDPOINT_WEB_FEED,),)
    if task_type == 'similar':
        return ((expand.ENDPOINT_SIMILAR,),)
    if task_type == 'venue_geo':
        return ((vetting.ENDPOINT_WEB_FEED,), (expand.ENDPOINT_LOCATION,))
    if task_type == 'person_search':
        return ((ENDPOINT_SEARCH,),)
    if task_type == 'bridge_check':
        return ((ENDPOINT_PROFILE_HTML,),)  # nicks with no pk — profile page only
    if task_type == 'bridge':
        # city from the API's business fields, or, when unavailable, from the
        # page's bio, checked against the 2GIS address (quality.bridge_verify_web)
        return ((ENDPOINT_SEARCH,), profile)
    return (RESOLVE_ENDPOINTS, profile)


def pick_account_for(conn, endpoint_groups, account_ids):
    """The least-loaded account, alive across every endpoint group the task needs."""
    alive = [aid for aid in account_ids
             if all(any(health.pick_account(conn, endpoint, [aid]) is not None
                        for endpoint in group)
                    for group in endpoint_groups)]
    if not alive:
        return None
    for group in endpoint_groups:
        for endpoint in group:
            chosen = health.pick_account(conn, endpoint, alive)
            if chosen is not None:
                return chosen
    return alive[0]


def claimable_task_types(conn, account_ids):
    """(types takeable regardless of payload; types takeable only with a pk)."""
    full, pk_only = [], []
    for task_type in TASK_TYPES:
        if pick_account_for(conn, task_endpoints(task_type), account_ids) is not None:
            full.append(task_type)
        elif (task_type in PK_SHORTCUT_TYPES
              and pick_account_for(conn, task_endpoints(task_type, has_pk=True),
                                   account_ids) is not None):
            pk_only.append(task_type)
    return full, pk_only


def run_once(stores, pool, agent_label='worker'):
    """One task. Returns False if there's nothing to take (empty queue, or everything cooling down)."""
    archive = stores.archive
    own_free = pool.free(pool.own_ids)
    hiker_free = pool.free(pool.hiker_ids)
    full, pk_only = set(), set()
    if own_free:
        f, p = claimable_task_types(archive, own_free)
        full.update(f)
        pk_only.update(p)
    if hiker_free:
        # HikerAPI costs money: it only takes types that own accounts can't do
        # at all right now (endpoint cooling down), not ones where they're merely busy
        own_f, own_p = claimable_task_types(archive, pool.own_ids) if pool.own_ids else ([], [])
        f, p = claimable_task_types(archive, hiker_free)
        full.update(set(f) - set(own_f))
        pk_only.update(set(p) - set(own_f) - set(own_p))
    pk_only -= full
    if len(getattr(pool, 'anon_ids', []) or []) >= READERS_TAKE_OVER:
        full -= set(ANON_TASK_TYPES)
        pk_only -= set(ANON_TASK_TYPES)
    if not full and not pk_only:
        return False
    task = wq.claim_next(archive, sorted(full), agent_label, pk_only_types=sorted(pk_only))
    if not task:
        return False
    if (task.get('attempts') or 0) >= MAX_TASK_ATTEMPTS:
        wq.fail(archive, task['id'], requeue=False)
        print('%s: задача %s (%s) остановлена после %d попыток'
              % (agent_label, task['id'], task['task_type'], task['attempts']))
        return True
    endpoints = task_endpoints(task['task_type'], bool(task['payload'].get('pk')))
    account_id = pick_account_for(archive, endpoints, own_free) if own_free else None
    if (account_id is None and hiker_free
            and pick_account_for(archive, endpoints, pool.own_ids) is None):
        account_id = pick_account_for(archive, endpoints, hiker_free)
    return _execute(stores, pool, task, account_id)


# Anonymous readers (2026-10-01) handle profile checks: bio and pk are reachable
# logged out; since 2026-10-13 also vetting (feed via /embed/, mentioned accounts'
# bios via the page, thumbnails via the CDN)
ANON_TASK_TYPES = ('profile_info', 'bridge_check', 'vet')
# 2026-10-13: own accounts were spending 2/3 of their 300/hour cap on profile pages
# while vetting stalled. Once there are this many readers or more, own accounts stop
# taking their tasks — the cap is reserved for what actually needs a login
READERS_TAKE_OVER = 5


def run_once_anon(stores, pool, agent_label='anon'):
    archive = stores.archive
    anon_free = pool.free(getattr(pool, 'anon_ids', []))
    if not anon_free:
        return False
    full = [t for t in ANON_TASK_TYPES if pick_account_for(archive, task_endpoints(t), anon_free) is not None]
    pk_only = [t for t in ANON_TASK_TYPES if t not in full and t in PK_SHORTCUT_TYPES
               and pick_account_for(archive, task_endpoints(t, has_pk=True), anon_free) is not None]
    if not full and not pk_only:
        return False
    task = wq.claim_next(archive, full, agent_label, pk_only_types=pk_only)
    if not task:
        return False
    if (task.get('attempts') or 0) >= MAX_TASK_ATTEMPTS:
        wq.fail(archive, task['id'], requeue=False)
        return True
    endpoints = task_endpoints(task['task_type'], bool(task['payload'].get('pk')))
    return _execute(stores, pool, task, pick_account_for(archive, endpoints, anon_free))


def _execute(stores, pool, task, account_id):
    archive = stores.archive
    if account_id is None or not pool.locks[account_id].acquire(blocking=False):
        # a race with a sibling thread: no request was made, so don't spend an attempt
        wq.release(archive, task['id'])
        return True
    try:
        HANDLERS[task['task_type']](stores, pool.transports[account_id], account_id, task)
    except Exception:
        wq.fail(archive, task['id'], requeue=True)
        raise
    finally:
        pool.locks[account_id].release()
    return True


def worker_loop(make_stores, pool, stop_event, label, step=None):
    """A worker thread. Owns its own database connections; a DB drop triggers a reconnect."""
    stores = None
    while not stop_event.is_set():
        try:
            if stores is None:
                stores = make_stores()
            did_work = (step or run_once)(stores, pool, label)
        except psycopg.OperationalError as e:
            print('%s: база недоступна (%s), переподключение через %d с'
                  % (label, e, RECONNECT_SECONDS), file=sys.stderr)
            if stores is not None:
                stores.close()
            stores = None
            stop_event.wait(RECONNECT_SECONDS)
            continue
        except Exception as e:
            print('%s: ошибка в цикле: %s' % (label, e), file=sys.stderr)
            did_work = False
        stop_event.wait(0.5 if did_work else 5)
    if stores is not None:
        stores.close()


def _pending_tasks(conn):
    return conn.execute("SELECT COUNT(*) AS c FROM work_queue WHERE status = 'pending'").fetchone()['c']


def _check_idle(control):
    since = health.seconds_since_last_success(control.archive)
    if since is not None and since > IDLE_ALERT_SECONDS and _pending_tasks(control.archive):
        alerts.raise_alert(control.archive, 'idle',
                           'Нет успешных запросов к Instagram больше 2 часов при непустой очереди.')


class _Running:
    """A running pool: worker threads plus their stop event."""

    def __init__(self, make_stores, pool, rows, mode):
        self.pool, self.mode, self.ids = pool, mode, [r['id'] for r in rows]
        self.stop = threading.Event()
        logged = [r for r in rows if r['kind'] != 'anon']
        anon = [r for r in rows if r['kind'] == 'anon']
        self.threads = [threading.Thread(target=worker_loop,
                                         args=(make_stores, pool, self.stop, 'w%d' % i), daemon=True)
                        for i in range(len(logged))]
        self.threads += [threading.Thread(target=worker_loop,
                                          args=(make_stores, pool, self.stop, 'a%d' % i, run_once_anon), daemon=True)
                         for i in range(len(anon))]
        for t in self.threads:
            t.start()
        print('супервизор: режим %s, аккаунтов %d, анонимных читателей %d' % (mode, len(logged), len(anon)))

    def shutdown(self):
        self.stop.set()
        for t in self.threads:
            t.join()
        self.pool.close()


def supervise(make_stores, stop_event, pool_factory=accounts.Pool, check_seconds=60):
    control = make_stores()
    running = None
    last_refill = 0.0
    try:
        db.init_main(control.main)
        db.init_archive(control.archive)
        if not settings.get(control.main, 'seeded_at'):
            print('СТОП: база не засеяна — все уже сданные контакты будут считаться новыми.\n'
                  'Сначала: python -m leadgen.core.seed', file=sys.stderr)
            raise SystemExit(1)
        added = vetting.enqueue_missing(control)
        if added:
            print('супервизор: поставлено задач отбора для ожидающих кандидатов: %d' % added)
        while not stop_event.is_set():
            try:
                wq.reclaim_expired(control.archive)
                back = accounts.revive_dead(control.archive)
                if back:
                    print('супервизор: выход VPN ожил — снова в работе: %s' % ', '.join(back))
                if time.time() - last_refill >= refill.CHECK_MINUTES * 60:
                    # the queue is running dry — move to the next source (client request, 2026-10-05)
                    last_refill = time.time()
                    filled = refill.run_once(control)
                    if filled:
                        print('супервизор: пополнение очереди — %s: %d задач' % filled if filled[0]
                              else 'супервизор: все источники исчерпаны')
                if running is not None:
                    _check_idle(control)
                    watchdog.check_flow(control)
                watchdog.morning_report(control)
                mode = accounts.transport_mode(control.main)
                rows = accounts.active_accounts(control.archive, mode)
                paused = settings.get(control.main, 'paused') == '1'
                if not rows:
                    alerts.raise_alert(control.archive, 'no_accounts',
                                       'Нет активных аккаунтов для режима %s — сбор стоит.' % mode)
            except psycopg.OperationalError as e:
                # A dropped supervisor connection doesn't touch the workers: they
                # have their own connections, so the pool keeps running while we reconnect.
                print('супервизор: база недоступна (%s), переподключение' % e, file=sys.stderr)
                control.close()
                stop_event.wait(RECONNECT_SECONDS)
                try:
                    control = make_stores()
                except psycopg.OperationalError:
                    pass  # a still-closed control will fail again next loop — retry then
                continue
            if paused:
                if running is not None:
                    print('супервизор: пауза — пул остановлен')
                    running.shutdown()
                    running = None
                stop_event.wait(check_seconds)
                continue
            ids = [r['id'] for r in rows]
            if running is not None and (mode != running.mode or ids != running.ids):
                print('супервизор: состав пула или режим изменился — пересборка')
                running.shutdown()
                running = None
            if running is None and rows:
                running = _Running(make_stores, pool_factory(rows), rows, mode)
            stop_event.wait(check_seconds)
    finally:
        if running is not None:
            running.shutdown()
        control.close()


def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    stop = threading.Event()
    make = lambda: db.open_stores(os.environ['MAIN_DB_URL'], os.environ['ARCHIVE_DB_URL'])
    try:
        supervise(make, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == '__main__':
    main()

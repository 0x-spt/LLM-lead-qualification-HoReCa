import threading

import psycopg
import pytest

from leadgen.access import accounts, health
from leadgen.core import db, queue as wq, settings
from leadgen.ops import workers
from leadgen.access.transport import EMPTY, OK
from tests.test_handlers import FakeHiker, FakeTransport, VENUE_PROFILE


@pytest.fixture
def venue(stores):
    stores.main.execute("INSERT INTO venues (name, city, region) "
                        "VALUES ('Fiorentina', 'Нижний Новгород', 'Поволжье')")
    return {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'}


def pool_of(stores, *transports, hiker=None):
    own = {}
    for i, t in enumerate(transports):
        own[accounts.register_own(stores.archive, 'acc%d' % i, {'sessionid': 's'})] = t
    hikers = {}
    if hiker is not None:
        hikers[accounts.register_hiker(stores.archive, 'KEY')] = hiker
    return accounts.Pool.from_transports(own, hiker=hikers), list(own), list(hikers)


def test_hybrid_uses_own_account_while_it_is_alive(stores, venue):
    own_t = FakeTransport(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}],
                          profile=VENUE_PROFILE)
    hiker_t = FakeHiker()
    pool, own, hk = pool_of(stores, own_t, hiker=hiker_t)
    wq.enqueue(stores.archive, 'bridge', venue)
    assert workers.run_once(stores, pool) is True
    assert own_t.search_calls and not hiker_t.search_calls


def test_hybrid_falls_back_to_hiker_when_own_search_cools_down(stores, venue):
    own_t = FakeTransport()
    hiker_t = FakeHiker(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}],
                        profile=VENUE_PROFILE)
    pool, own, hk = pool_of(stores, own_t, hiker=hiker_t)
    health.record_failure(stores.archive, own[0], 'search', 'throttled')
    wq.enqueue(stores.archive, 'bridge', venue)
    assert workers.run_once(stores, pool) is True
    assert hiker_t.search_calls and not own_t.search_calls


def test_busy_account_is_not_used_by_second_worker(stores, venue):
    t = FakeTransport()
    pool, own, _ = pool_of(stores, t)
    pool.locks[own[0]].acquire()
    wq.enqueue(stores.archive, 'bridge', venue)
    assert workers.run_once(stores, pool) is False
    assert t.search_calls == []
    pool.locks[own[0]].release()


def test_lock_is_released_even_when_handler_raises(stores, venue):
    class Boom(FakeTransport):
        def search(self, query):
            raise RuntimeError('boom')
    pool, own, _ = pool_of(stores, Boom())
    wq.enqueue(stores.archive, 'bridge', venue)
    with pytest.raises(RuntimeError):
        workers.run_once(stores, pool)
    assert not pool.locks[own[0]].locked()


def test_worker_loop_reconnects_after_database_error(stores, pg_urls, venue, monkeypatch):
    """Supabase моргнул — поток не умирает, открывает соединения заново."""
    calls = {'n': 0}
    stop = threading.Event()

    def make_stores():
        calls['n'] += 1
        if calls['n'] == 1:
            raise psycopg.OperationalError('connection refused')
        if calls['n'] >= 2:
            stop.set()
        return db.open_stores(pg_urls['main'], pg_urls['archive'])

    pool, _, _ = pool_of(stores, FakeTransport())
    monkeypatch.setattr(workers, 'RECONNECT_SECONDS', 0)
    workers.worker_loop(make_stores, pool, stop, 'w0')
    assert calls['n'] >= 2


def test_supervisor_rebuilds_pool_when_account_goes_down(stores, pg_urls):
    """Аккаунт ушёл в checkpoint — пул собирается заново без него."""
    settings.put(stores.main, 'seeded_at', '2026-09-28')
    one = accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})
    two = accounts.register_own(stores.archive, 'acc2', {'sessionid': 's'})
    built = []
    stop = threading.Event()

    class RecordingPool(accounts.Pool):
        def __init__(self, rows, factory=None):
            super().__init__(rows, factory=lambda r: FakeTransport())
            built.append(self.signature())
            if len(built) == 1:
                health.record_failure(stores.archive, one, 'search', 'checkpoint')
            else:
                stop.set()

    make = lambda: db.open_stores(pg_urls['main'], pg_urls['archive'])
    workers.supervise(make, stop, pool_factory=RecordingPool, check_seconds=0.05)
    assert built[0] == (one, two)
    assert built[1] == (two,)


def test_supervisor_refuses_unseeded_database(stores, pg_urls):
    stop = threading.Event()
    make = lambda: db.open_stores(pg_urls['main'], pg_urls['archive'])
    with pytest.raises(SystemExit):
        workers.supervise(make, stop, check_seconds=0.05)


def test_supervisor_alerts_when_no_active_accounts(stores, pg_urls):
    settings.put(stores.main, 'seeded_at', '2026-09-28')
    stop = threading.Event()
    make = lambda: db.open_stores(pg_urls['main'], pg_urls['archive'])
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    workers.supervise(make, stop, check_seconds=0.05)
    kinds = {r['kind'] for r in stores.archive.execute('SELECT kind FROM alerts').fetchall()}
    assert 'no_accounts' in kinds  # рядом может быть утренний отчёт сторожа


def test_supervisor_alerts_when_idle_with_pending_tasks(stores, pg_urls, venue):
    settings.put(stores.main, 'seeded_at', '2026-09-28')
    aid = accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})
    health.record_success(stores.archive, aid, 'search')
    stores.archive.execute("UPDATE request_log SET at = now() - interval '3 hours'")
    health.record_failure(stores.archive, aid, 'search', 'throttled')
    health.record_failure(stores.archive, aid, 'profile_info', 'throttled')
    health.record_failure(stores.archive, aid, 'profile_html', 'throttled')
    health.record_failure(stores.archive, aid, 'following', 'throttled')
    wq.enqueue(stores.archive, 'bridge', venue)
    stop = threading.Event()
    make = lambda: db.open_stores(pg_urls['main'], pg_urls['archive'])
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    workers.supervise(make, stop, pool_factory=lambda rows: accounts.Pool(rows, factory=lambda r: FakeTransport()),
                      check_seconds=0.05)
    kinds = [r['kind'] for r in stores.archive.execute('SELECT kind FROM alerts').fetchall()]
    assert 'idle' in kinds


def test_hybrid_does_not_pay_hiker_while_own_account_is_just_busy(stores, venue):
    """Свой аккаунт жив, но занят соседним потоком — HikerAPI не трогаем:
    платный транспорт только когда у своих нужная ручка остывает."""
    own_t = FakeTransport()
    hiker_t = FakeHiker(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}],
                        profile=VENUE_PROFILE)
    pool, own, hk = pool_of(stores, own_t, hiker=hiker_t)
    pool.locks[own[0]].acquire()
    wq.enqueue(stores.archive, 'bridge', venue)
    workers.run_once(stores, pool)
    pool.locks[own[0]].release()
    assert hiker_t.search_calls == []
    assert stores.archive.execute('SELECT status FROM work_queue').fetchone()['status'] == 'pending'


def test_hybrid_hiker_takes_task_when_own_search_cools_down_even_if_own_is_busy(stores, venue):
    own_t = FakeTransport()
    hiker_t = FakeHiker(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}],
                        profile=VENUE_PROFILE)
    pool, own, hk = pool_of(stores, own_t, hiker=hiker_t)
    health.record_failure(stores.archive, own[0], 'search', 'throttled')
    pool.locks[own[0]].acquire()
    wq.enqueue(stores.archive, 'bridge', venue)
    assert workers.run_once(stores, pool) is True
    pool.locks[own[0]].release()
    assert hiker_t.search_calls


def test_supervisor_survives_database_blip_on_its_own_connection(stores, pg_urls, monkeypatch):
    """Supabase моргнул на проверке режима — супервизор переподключается,
    а не роняет весь процесс вместе с воркерами."""
    settings.put(stores.main, 'seeded_at', '2026-09-28')
    accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})
    real = accounts.transport_mode
    calls = {'n': 0, 'opened': 0}

    def flaky(conn):
        calls['n'] += 1
        if calls['n'] == 2:
            raise psycopg.OperationalError('server closed the connection unexpectedly')
        return real(conn)

    def make():
        calls['opened'] += 1
        return db.open_stores(pg_urls['main'], pg_urls['archive'])

    monkeypatch.setattr(accounts, 'transport_mode', flaky)
    monkeypatch.setattr(workers, 'RECONNECT_SECONDS', 0)
    stop = threading.Event()
    threading.Timer(0.6, stop.set).start()
    workers.supervise(make, stop,
                      pool_factory=lambda rows: accounts.Pool(rows, factory=lambda r: FakeTransport()),
                      check_seconds=0.05)
    assert calls['n'] > 2


def test_profile_task_with_pk_claimable_when_only_page_route_is_alive(stores):
    pool, own, _ = pool_of(stores, FakeTransport())
    health.record_failure(stores.archive, own[0], 'profile_info', 'throttled')
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'x', 'pk': '1', 'source': 'following'})
    full, pk_only = workers.claimable_task_types(stores.archive, own)
    assert 'profile_info' in full + pk_only


def test_supervisor_stops_pool_while_paused(stores, pg_urls):
    settings.put(stores.main, 'seeded_at', '2026-09-28')
    settings.put(stores.main, 'paused', '1')
    accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})
    built = []
    stop = threading.Event()

    def factory(rows):
        built.append(1)
        return accounts.Pool(rows, factory=lambda r: FakeTransport())
    threading.Timer(0.4, stop.set).start()
    workers.supervise(lambda: db.open_stores(pg_urls['main'], pg_urls['archive']), stop,
                      pool_factory=factory, check_seconds=0.05)
    assert built == []

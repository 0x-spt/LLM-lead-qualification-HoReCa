"""Анонимные читатели (01.10.2026): шапка профиля и pk открыты без входа — массовую проверку
профилей ведут читатели без cookie, по одному на выход VPN; аккаунты — на том, что требует входа."""
import httpx

from leadgen.access import accounts, health
from leadgen.core import queue as wq
from leadgen.ops import workers
from leadgen.access.transport import OK
from leadgen.access.transport.own import OwnTransport


def test_register_anon_and_pool_split(stores):
    own = accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})
    anon = accounts.register_anon(stores.archive, 'anon-21811', 'socks5://127.0.0.1:21811')
    rows = accounts.active_accounts(stores.archive, 'own')
    assert {r['id'] for r in rows} == {own, anon}
    pool = accounts.Pool(rows)
    assert pool.own_ids == [own] and pool.anon_ids == [anon]
    t = pool.transports[anon]
    assert isinstance(t, OwnTransport) and t._proxy == 'socks5://127.0.0.1:21811'
    assert 'sessionid' not in dict(t._client.cookies)
    pool.close()


def test_anon_gets_only_logged_out_endpoints(stores):
    anon = accounts.register_anon(stores.archive, 'anon-1', None)
    for endpoint in ('profile_html', 'embed'):
        assert health.pick_account(stores.archive, endpoint, [anon]) == anon
    for endpoint in ('profile_info', 'following', 'search', 'feed_web', 'similar', 'highlights', 'feed'):
        assert health.pick_account(stores.archive, endpoint, [anon]) is None


def test_login_redirect_cools_anon_but_keeps_it_active(stores):
    anon = accounts.register_anon(stores.archive, 'anon-1', None)
    health.record_failure(stores.archive, anon, 'profile_html', 'checkpoint')
    assert stores.archive.execute('SELECT status FROM accounts WHERE id=%s', (anon,)).fetchone()['status'] == 'active'
    assert health.pick_account(stores.archive, 'profile_html', [anon]) is None
    assert stores.archive.execute('SELECT count(*) AS c FROM alerts').fetchone()['c'] == 0


def page_transport():
    page = ('<head><meta property="og:title" content="Ivan (@ivan_chef)"><meta property="og:description" '
            'content="1 Followers, 2 Following, 40 Posts - See Instagram photos and videos from Ivan (@ivan_chef)">'
            '<meta name="description" content="1 Followers, 2 Following, 40 Posts - Ivan (@ivan_chef) on Instagram: '
            '&quot;Шеф-повар @fiorentina_nn&quot;"></head>')
    return OwnTransport({}, http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page)))


def test_anon_worker_takes_profile_info_only(stores):
    own = accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})
    anon = accounts.register_anon(stores.archive, 'anon-1', None)
    pool = accounts.Pool.from_transports({own: page_transport()}, anon={anon: page_transport()})
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'page': 1}, priority=10)
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan_chef', 'pk': '77', 'source': 'following'}, priority=80)
    assert workers.run_once_anon(stores, pool, 'a') is True
    left = {r['task_type']: r['status'] for r in stores.archive.execute('SELECT task_type, status FROM work_queue').fetchall()}
    # шеф из шапки — кандидат, дальше его отбор ведут аккаунты (лента из 12 постов требует входа)
    assert left == {'following': 'pending', 'profile_info': 'done', 'vet': 'pending'}
    log = stores.archive.execute('SELECT account_id, endpoint_class FROM request_log').fetchall()
    assert log and all(r['account_id'] == anon for r in log)


def test_own_worker_never_uses_anon(stores):
    anon = accounts.register_anon(stores.archive, 'anon-1', None)
    pool = accounts.Pool.from_transports({}, anon={anon: page_transport()})
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan_chef', 'pk': '77', 'source': 'following'})
    assert workers.run_once(stores, pool, 'w') is False


def test_dead_anon_exit_is_revived(stores):
    anon = accounts.register_anon(stores.archive, 'anon-1', 'socks5://127.0.0.1:21811')
    health.record_failure(stores.archive, anon, 'profile_html', 'proxy_dead')
    assert accounts.revive_dead(stores.archive, probe=lambda p: True) == ['anon-1']


def test_readers_vet_and_accounts_keep_their_hourly_cap_for_login_only_work(stores, monkeypatch):
    """13.10.2026: 8 аккаунтов упёрлись в 300 запросов/час, 1 523 из 2 400 — страницы профилей, которые читают и
    19 читателей без входа; на отбор лимита не осталось — 2 кандидата за час при очереди 2 314.
    Читатели делают отбор; свои аккаунты при достаточном числе читателей не берут их задачи."""
    assert 'vet' in workers.ANON_TASK_TYPES
    monkeypatch.setattr(workers, 'READERS_TAKE_OVER', 2)
    own = accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})
    readers = [accounts.register_anon(stores.archive, 'anon-%d' % i, None) for i in range(2)]
    pool = accounts.Pool(accounts.active_accounts(stores.archive, 'own'))
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'x_chef'})
    assert workers.run_once(stores, pool, 'w') is False          # профиль — работа читателей
    wq.enqueue(stores.archive, 'following', {'pk': '1', 'venue': 'V', 'city': '', 'page': 1, 'max_pages': 1})
    claimed = []
    monkeypatch.setattr(workers, '_execute', lambda st, p, task, aid: claimed.append(task['task_type']) or True)
    assert workers.run_once(stores, pool, 'w') is True and claimed == ['following']
    pool.close()

"""30.09.2026: Happ переподключал туннель — четыре выхода VPN «умерли» за 25 секунд,
аккаунты выключились до ручного вмешательства, а в бот летело «Замените прокси»."""
from leadgen.access import accounts, health


def own(stores, label='ig3', proxy='socks5://127.0.0.1:21811'):
    return accounts.register_own(stores.archive, label, {'sessionid': 's'}, proxy=proxy)


def alerts(stores):
    return [r['text'] for r in stores.archive.execute('SELECT text FROM alerts ORDER BY id').fetchall()]


def test_dead_proxy_gives_no_alert_at_once(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'following', 'proxy_dead')
    assert stores.archive.execute('SELECT status FROM accounts WHERE id=%s', (aid,)).fetchone()['status'] == 'proxy_dead'
    assert alerts(stores) == []


def test_revive_returns_account_when_exit_answers(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'following', 'proxy_dead')
    assert accounts.revive_dead(stores.archive, probe=lambda proxy: True) == ['ig3']
    assert stores.archive.execute('SELECT status FROM accounts WHERE id=%s', (aid,)).fetchone()['status'] == 'active'


def test_long_dead_exit_alerts_once(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'following', 'proxy_dead')
    stores.archive.execute("UPDATE accounts SET down_at = now() - interval '20 minutes' WHERE id=%s", (aid,))
    accounts.revive_dead(stores.archive, probe=lambda proxy: False)
    accounts.revive_dead(stores.archive, probe=lambda proxy: False)
    assert alerts(stores) == ['Аккаунт ig3: выход VPN не отвечает больше 15 минут. Проверьте VPN или замените прокси.']


def test_short_outage_no_alert(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'following', 'proxy_dead')
    accounts.revive_dead(stores.archive, probe=lambda proxy: False)
    assert alerts(stores) == []


def test_checkpoint_is_not_revived_and_alerts_at_once(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'search', 'checkpoint')
    assert accounts.revive_dead(stores.archive, probe=lambda proxy: True) == []
    assert len(alerts(stores)) == 1


def test_net_error_cools_endpoint_for_a_minute_only(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'profile_html', 'net_error')
    left = stores.archive.execute(
        "SELECT extract(epoch FROM cooldown_until - now()) AS s FROM endpoint_health WHERE account_id=%s",
        (aid,)).fetchone()['s']
    assert 0 < left <= 60
    assert stores.archive.execute('SELECT status FROM accounts WHERE id=%s', (aid,)).fetchone()['status'] == 'active'


def test_revived_account_endpoints_are_unfrozen(stores):
    aid = own(stores)
    health.record_failure(stores.archive, aid, 'following', 'proxy_dead')
    accounts.revive_dead(stores.archive, probe=lambda proxy: True)
    assert health.pick_account(stores.archive, 'following', [aid]) == aid

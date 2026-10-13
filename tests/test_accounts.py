from leadgen.access import accounts, health
from leadgen.core import settings
from leadgen.access.transport.hiker import HikerTransport
from leadgen.access.transport.own import OwnTransport


def test_transport_mode_defaults_to_own(stores):
    assert accounts.transport_mode(stores.main) == 'own'


def test_transport_mode_unknown_value_falls_back_to_own(stores):
    settings.put(stores.main, 'ig_transport', 'что-то')
    assert accounts.transport_mode(stores.main) == 'own'


def test_transport_mode_reads_setting(stores):
    settings.put(stores.main, 'ig_transport', 'hybrid')
    assert accounts.transport_mode(stores.main) == 'hybrid'


def test_active_accounts_filtered_by_mode(stores):
    a = stores.archive
    own = accounts.register_own(a, 'ig3', {'sessionid': 's'}, proxy='http://p:1')
    hiker = accounts.register_hiker(a, 'KEY')
    ids = lambda mode: [r['id'] for r in accounts.active_accounts(a, mode)]
    assert ids('own') == [own]
    assert ids('hiker') == [hiker]
    assert ids('hybrid') == [own, hiker]


def test_accounts_down_are_not_active(stores):
    a = stores.archive
    own = accounts.register_own(a, 'ig3', {'sessionid': 's'})
    health.record_failure(a, own, 'search', 'checkpoint')
    assert accounts.active_accounts(a, 'own') == []


def test_re_register_brings_account_back_and_clears_cooldown(stores):
    a = stores.archive
    own = accounts.register_own(a, 'ig3', {'sessionid': 'old'})
    health.record_failure(a, own, 'search', 'checkpoint')
    again = accounts.register_own(a, 'ig3', {'sessionid': 'new'})
    assert again == own
    row = accounts.active_accounts(a, 'own')[0]
    assert row['cookies'] == {'sessionid': 'new'}
    assert health.pick_account(a, 'search', [own]) == own


def test_register_hiker_twice_updates_key(stores):
    a = stores.archive
    first = accounts.register_hiker(a, 'OLD')
    second = accounts.register_hiker(a, 'NEW')
    assert first == second
    assert accounts.active_accounts(a, 'hiker')[0]['api_key'] == 'NEW'


def test_build_transport_by_kind(stores):
    a = stores.archive
    accounts.register_own(a, 'ig3', {'sessionid': 's'})
    accounts.register_hiker(a, 'KEY')
    built = [accounts.build_transport(r) for r in accounts.active_accounts(a, 'hybrid')]
    assert isinstance(built[0], OwnTransport)
    assert isinstance(built[1], HikerTransport)
    for t in built:
        t.close()


def test_pool_puts_own_tier_before_hiker():
    pool = accounts.Pool.from_transports({1: object(), 2: object()}, hiker={9: object()})
    assert pool.tiers() == [[1, 2], [9]]


def test_pool_without_hiker_has_one_tier():
    pool = accounts.Pool.from_transports({1: object()})
    assert pool.tiers() == [[1]]


def test_pool_free_skips_locked_accounts():
    pool = accounts.Pool.from_transports({1: object(), 2: object()})
    pool.locks[1].acquire()
    assert pool.free([1, 2]) == [2]
    pool.locks[1].release()


def test_pool_signature_changes_with_membership():
    a = accounts.Pool.from_transports({1: object()})
    b = accounts.Pool.from_transports({1: object(), 2: object()})
    assert a.signature() != b.signature()

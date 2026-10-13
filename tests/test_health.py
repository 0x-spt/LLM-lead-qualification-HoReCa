from leadgen.access import health


def _account(conn, label, kind='own'):
    return conn.execute("INSERT INTO accounts (label, kind) VALUES (%s, %s) RETURNING id",
                        (label, kind)).fetchone()['id']


def test_pick_account_returns_least_used_among_healthy(stores):
    a = stores.archive
    one, two = _account(a, 'a1'), _account(a, 'a2')
    health.record_success(a, one, 'search')
    for _ in range(5):
        health.record_success(a, two, 'search')
    assert health.pick_account(a, 'search', [one, two]) == one


def test_pick_account_skips_account_in_cooldown(stores):
    a = stores.archive
    one, two = _account(a, 'a1'), _account(a, 'a2')
    health.record_failure(a, one, 'search', 'throttled')
    health.record_success(a, two, 'search')
    assert health.pick_account(a, 'search', [one, two]) == two


def test_pick_account_returns_none_when_all_in_cooldown(stores):
    a = stores.archive
    one, two = _account(a, 'a1'), _account(a, 'a2')
    health.record_failure(a, one, 'search', 'throttled')
    health.record_failure(a, two, 'search', 'soft_block')
    assert health.pick_account(a, 'search', [one, two]) is None


def test_endpoint_classes_are_independent(stores):
    a = stores.archive
    one = _account(a, 'a1')
    health.record_failure(a, one, 'search', 'throttled')
    health.record_success(a, one, 'following')
    assert health.pick_account(a, 'following', [one]) == one
    assert health.pick_account(a, 'search', [one]) is None


def test_pick_account_with_empty_list_is_none(stores):
    assert health.pick_account(stores.archive, 'search', []) is None


def test_every_call_is_logged(stores):
    a = stores.archive
    one = _account(a, 'a1')
    health.record_success(a, one, 'search')
    health.record_failure(a, one, 'search', 'throttled')
    rows = a.execute('SELECT status FROM request_log ORDER BY id').fetchall()
    assert [r['status'] for r in rows] == ['ok', 'throttled']


def test_checkpoint_takes_account_down_and_raises_alert(stores):
    a = stores.archive
    one = _account(a, 'ig3')
    health.record_failure(a, one, 'profile_info', 'checkpoint')
    assert a.execute('SELECT status FROM accounts WHERE id = %s', (one,)).fetchone()['status'] == 'нужен_вход'
    alert = a.execute('SELECT kind, text FROM alerts').fetchone()
    assert alert['kind'] == 'checkpoint'
    assert 'ig3' in alert['text']


def test_dead_proxy_takes_account_down(stores):
    a = stores.archive
    one = _account(a, 'ig4')
    health.record_failure(a, one, 'search', 'proxy_dead')
    assert a.execute('SELECT status FROM accounts WHERE id = %s', (one,)).fetchone()['status'] == 'proxy_dead'


def test_rejected_hiker_key_takes_hiker_down_with_clear_alert(stores):
    a = stores.archive
    hiker = _account(a, 'hiker', kind='hiker')
    health.record_failure(a, hiker, 'search', 'checkpoint')
    assert a.execute('SELECT status FROM accounts WHERE id = %s', (hiker,)).fetchone()['status'] == 'нужен_вход'
    assert 'HikerAPI' in a.execute('SELECT text FROM alerts').fetchone()['text']


def test_seconds_since_last_success(stores):
    a = stores.archive
    assert health.seconds_since_last_success(a) is None
    one = _account(a, 'a1')
    health.record_success(a, one, 'search')
    assert 0 <= health.seconds_since_last_success(a) < 60


def test_account_down_is_never_picked_on_any_endpoint(stores):
    """Checkpoint на одной ручке — остальные ручки аккаунта тоже закрыты сразу,
    а не через минуту до пересборки пула: сессию уже пометил Instagram."""
    a = stores.archive
    one = _account(a, 'ig3')
    health.record_failure(a, one, 'profile_info', 'checkpoint')
    for endpoint in ('search', 'profile_html', 'following'):
        assert health.pick_account(a, endpoint, [one]) is None


def test_fresh_account_only_reads_profiles_first_day(stores):
    """05.10.2026: ig13 через 4 минуты после входа получил checkpoint на обходе подписок.
    Первые сутки новый аккаунт только читает профили; тяжёлые ручки — у старых."""
    a = stores.archive
    from leadgen.access import accounts
    old = _account(a, 'old')
    fresh = accounts.register_own(a, 'fresh', {'sessionid': 'x'})
    accounts.start_warmup(a, fresh)
    for _ in range(5):
        health.record_success(a, old, 'following')
        health.record_success(a, old, 'profile_info')
    assert health.pick_account(a, 'following', [old, fresh]) == old
    assert health.pick_account(a, 'following', [fresh]) is None
    assert health.pick_account(a, 'profile_info', [old, fresh]) == fresh
    a.execute("UPDATE accounts SET warmup_until = now() - interval '1 minute' WHERE id = %s", (fresh,))
    assert health.pick_account(a, 'following', [old, fresh]) == fresh


def test_logged_in_account_has_an_hourly_ceiling(stores, monkeypatch):
    """08.10.2026: ig3, ig8, ig4 ушли в checkpoint на 1 100–2 200 запросах в час (страницы профилей
    под своей сессией). Свой аккаунт — не больше OWN_HOURLY_CAP запросов в час; читатель без входа — без потолка."""
    from leadgen.access import accounts
    a = stores.archive
    monkeypatch.setattr(health, 'OWN_HOURLY_CAP', 3)
    own = accounts.register_own(a, 'busy', {'sessionid': 'x'})
    anon = accounts.register_anon(a, 'anon-1', 'socks5://127.0.0.1:21801')
    for _ in range(3):
        health.record_success(a, own, 'profile_html')
        health.record_success(a, anon, 'profile_html')
    assert health.pick_account(a, 'profile_html', [own]) is None
    assert health.pick_account(a, 'profile_html', [anon]) == anon
    a.execute("UPDATE request_log SET at = now() - interval '61 minutes' WHERE account_id = %s", (own,))
    assert health.pick_account(a, 'profile_html', [own]) == own

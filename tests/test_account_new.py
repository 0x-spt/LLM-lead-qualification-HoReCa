from leadgen.access import account_new


def test_exit_is_live_free_and_has_unique_ip():
    exits = {21801: '1.1.1.1', 21802: '2.2.2.2', 21803: '2.2.2.2', 21804: None, 21805: '5.5.5.5'}
    # 21801 занят аккаунтом, 21803 делит IP с 21802, 21804 не отвечает
    assert account_new.pick_exits(exits, used_ports={21801}, used_ips={'1.1.1.1'}, n=3) == [21802, 21805]


def test_exit_sharing_ip_with_existing_account_is_skipped():
    exits = {21801: '9.9.9.9', 21802: '3.3.3.3'}
    assert account_new.pick_exits(exits, used_ports=set(), used_ips={'9.9.9.9'}, n=2) == [21802]


def test_cdp_ports_skip_taken_ones():
    assert account_new.free_cdp_ports({9223, 9224, 9225, 9226, 9228}, 3) == [9227, 9229, 9230]


def test_label_must_be_new_and_simple():
    assert account_new.check_label('ig6', {'ig2', 'ig5'}) is None
    assert account_new.check_label('ig5', {'ig2', 'ig5'})
    assert account_new.check_label('ig 6; rm', set())


def test_logged_in_needs_full_session():
    assert account_new.logged_in({'sessionid': 'a', 'csrftoken': 'b', 'ds_user_id': '1'})
    assert not account_new.logged_in({'csrftoken': 'b'})


def test_avoid_flag_excludes_bad_exits():
    labels, avoid, _ = account_new.parse_args(['ig6', '--avoid', '21801,21805', 'ig12'])
    assert labels == ['ig6', 'ig12'] and avoid == {21801, 21805}


def test_relogin_flag_and_existing_labels_allowed():
    labels, avoid, relogin = account_new.parse_args(['--relogin', 'ig2', 'ig7'])
    assert labels == ['ig2', 'ig7'] and relogin is True
    assert account_new.parse_args(['ig12'])[2] is False


def test_relogin_waits_for_a_new_session_not_the_stale_one():
    stale = {'sessionid': 'old', 'csrftoken': 'c', 'ds_user_id': '1'}
    assert not account_new.fresh_session(stale, 'old')
    assert account_new.fresh_session(dict(stale, sessionid='new'), 'old')
    assert not account_new.fresh_session({'csrftoken': 'c'}, None)


def test_share_puts_extra_accounts_on_least_used_live_exits():
    # 05.10.2026: «профили делай на подписке, которую я дал» — серверов меньше, чем профилей
    exits = {21847: 'a', 21849: 'b', 21851: 'c', 21854: 'd', 21899: None}
    counts = {21847: 1, 21849: 1, 21851: 1, 21854: 1}
    assert account_new.share_exits(exits, counts, 2) == [21847, 21849]  # при равной загрузке — по порядку
    assert account_new.parse_args(['--share', 'ig13'])[0] == ['ig13']


def test_relogin_after_checkpoint_also_warms_up(stores):
    """09.10.2026: ig3 вошёл заново после checkpoint в 07:15, на новом IP сразу пошёл обходить подписки —
    в 07:17 снова checkpoint. Повторный вход прогревается так же, как новый аккаунт."""
    from leadgen.access import account_new
    aid = account_new.activate(stores.archive, 'ig3', {'sessionid': 'new'}, 'socks5://127.0.0.1:21860', 'UA')
    row = stores.archive.execute('SELECT status, warmup_until > now() AS warm FROM accounts WHERE id = %s', (aid,)).fetchone()
    assert row['status'] == 'active' and row['warm'] is True


def test_exit_with_login_page_429_is_never_offered():
    """12.10.2026, заказчик: «чтобы в профилях не было 429». Выход годится для окна входа, только если
    у него есть IP и страница входа Instagram отдаёт 200."""
    from leadgen.access import account_new
    codes = {21801: 200, 21802: 429, 21803: 200}
    ips = {21801: '1.1.1.1', 21802: '2.2.2.2', 21803: None}
    usable = account_new.usable_exits([21801, 21802, 21803], ip=ips.get, login_code=codes.get)
    assert usable == {21801: '1.1.1.1', 21802: None, 21803: None}

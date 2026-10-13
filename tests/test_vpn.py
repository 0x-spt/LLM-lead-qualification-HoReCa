from leadgen.access import vpn


def server(remark, host='1.2.3.4', tag='proxy'):
    return {'remarks': remark, 'outbounds': [
        {'tag': tag, 'protocol': 'vless', 'settings': {'vnext': [{'address': host}]},
         'streamSettings': {'network': 'tcp', 'security': 'reality'}},
        {'tag': 'direct', 'protocol': 'freedom'}]}


def test_each_server_gets_own_socks_port_and_route():
    cfg, ports = vpn.build([server('🇪🇪Эстония'), server('🇦🇹 Австрия')], {})
    assert ports == {'🇪🇪Эстония': 21801, '🇦🇹 Австрия': 21802}
    inb = {i['port']: i for i in cfg['inbounds']}
    assert set(inb) == {21801, 21802}
    assert all(i['listen'] == '127.0.0.1' and i['protocol'] == 'socks' for i in inb.values())
    routes = {r['inboundTag'][0]: r['outboundTag'] for r in cfg['routing']['rules']}
    tags = {o['tag'] for o in cfg['outbounds']}
    assert set(routes.values()) <= tags and len(set(routes.values())) == 2


def test_ports_stay_with_server_name_when_subscription_reorders():
    _, ports = vpn.build([server('A'), server('B')], {})
    _, again = vpn.build([server('C'), server('B'), server('A')], ports)
    assert again['A'] == ports['A'] and again['B'] == ports['B']
    assert again['C'] == 21803


def test_auto_russian_and_lte_exits_are_skipped():
    # LTE — обход белых списков с лимитом трафика (заказчик 30.09.2026): сбор их выедает
    _, ports = vpn.build([server('🇪🇺 АВТО | Самый быстрый'), server('🇪🇺LTE Авто (Моб. Интернет)'),
                          server('🇷🇺Россия'), server('🇱🇻LTE Латвия'), server('🇸🇪Швеция')], {})
    assert list(ports) == ['🇸🇪Швеция']


def test_server_without_proxy_outbound_is_skipped():
    only_direct = {'remarks': 'X', 'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}]}
    _, ports = vpn.build([only_direct, server('Y')], {})
    assert list(ports) == ['Y']


def test_outbounds_named_by_server_each_become_an_exit():
    """провайдер B (05.10.2026): выход назван именем сервера, а не «proxy», и в одном пункте
    бывает два сервера на разных хостах (балансировщик) — каждый идёт отдельным выходом."""
    pool = server('🇳🇱 Нидерланды', host='cdn-nl', tag='🇳🇱 Нидерланды')
    pool['outbounds'].insert(1, dict(server('x', host='edge10')['outbounds'][0], tag='🇳🇱 Нидерланды — 4'))
    single = server('🇯🇵 Япония', host='node11', tag='🇯🇵 Япония')
    cfg, ports = vpn.build([pool, single], {})
    assert list(ports) == ['🇳🇱 Нидерланды', '🇳🇱 Нидерланды — 4', '🇯🇵 Япония']
    hosts = [o['settings']['vnext'][0]['address'] for o in cfg['outbounds'] if o['protocol'] == 'vless']
    assert hosts == ['cdn-nl', 'edge10', 'node11']


def test_happ_port_is_never_used():
    # 10808 держит сам Happ: занять его — завернуть весь VPN в петлю.
    assert not (vpn.BASE_PORT < 10808 <= vpn.BASE_PORT + 500)


def test_removed_server_keeps_its_port_reserved():
    _, ports = vpn.build([server('A'), server('B')], {})
    cfg, again = vpn.build([server('B'), server('D')], ports)
    assert again['A'] == 21801 and again['D'] == 21803
    assert {i['port'] for i in cfg['inbounds']} == {21802, 21803}


def test_transport_variants_of_one_server_are_skipped():
    # у провайдер A на каждый сервер 5 вариантов — это один IP; берём TLS (reality)
    names = ['🇳🇱 • NL #2 — TLS', '🇳🇱 • NL #2 — XHTTP iOS', '🇳🇱 • NL #2 — WS', '🇳🇱 • NL #2 — gRPC',
             '🇫🇮 ⟳ Финляндия — LTE (0.0/6 ГБ)', '🇩🇪 ⟳ Германия — Авто']
    _, ports = vpn.build([server(n) for n in names], {})
    assert list(ports) == ['🇳🇱 • NL #2 — TLS']


def test_several_subscriptions_one_failing_uses_its_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn, 'SUB_CACHE', str(tmp_path))
    monkeypatch.setattr(vpn, 'RETRY_PAUSE', 0)
    state = {'bad': False}

    def fake_fetch(url, hwid):
        if state['bad'] and 'x2' in url:
            raise OSError('обрыв')
        return [server(url.rsplit('/', 1)[1])]
    monkeypatch.setattr(vpn, 'fetch', fake_fetch)
    urls = 'https://a/x1,https://b/x2'
    assert [s['remarks'] for s in vpn.fetch_all(urls, 'hw')] == ['x1', 'x2']
    state['bad'] = True
    assert [s['remarks'] for s in vpn.fetch_all(urls, 'hw')] == ['x1', 'x2']  # x2 — из кэша


def test_flaky_subscription_is_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn, 'SUB_CACHE', str(tmp_path))
    monkeypatch.setattr(vpn, 'RETRY_PAUSE', 0)
    calls = []

    def fake_fetch(url, hwid):
        calls.append(url)
        if len(calls) < 3:
            raise OSError('Broken pipe')
        return [server('x1')]
    monkeypatch.setattr(vpn, 'fetch', fake_fetch)
    assert [s['remarks'] for s in vpn.fetch_all('https://a/x1', 'hw')] == ['x1'] and len(calls) == 3


def test_all_subscriptions_failing_without_cache_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn, 'SUB_CACHE', str(tmp_path))
    monkeypatch.setattr(vpn, 'RETRY_PAUSE', 0)

    def fake_fetch(url, hwid):
        raise OSError('обрыв')
    monkeypatch.setattr(vpn, 'fetch', fake_fetch)
    import pytest
    with pytest.raises(OSError):
        vpn.fetch_all('https://bad/1', 'hw')


def test_whitelist_bypass_exits_are_skipped():
    # «Обход I…X» — обход белых списков через российский узел: один IP на всех, лимит трафика
    _, ports = vpn.build([server('Обход I'), server('АВТО-ОБХОД ⚡️'), server('🇷🇺 Россия | для зарубежа'),
                          server('🇩🇪 Германия⚡️')], {})
    assert list(ports) == ['🇩🇪 Германия⚡️']


def test_direct_subscription_bypasses_happ_tunnel(monkeypatch, tmp_path):
    """провайдер B (05.10.2026): через туннель Happ сервер отвечает настоящим сертификатом
    («VPN в VPN» не пускает), напрямую с Wi-Fi — работает. Подписка с пометкой direct:
    подключается мимо Happ, привязкой к физическому интерфейсу; остальные — как были."""
    monkeypatch.setattr(vpn, 'SUB_CACHE', str(tmp_path))
    subs = {'https://a/s': [server('A')], 'https://b/s': [server('B')]}
    monkeypatch.setattr(vpn, 'fetch', lambda url, hwid: subs[url])
    servers = vpn.fetch_all('https://a/s,direct:https://b/s', 'hw')
    cfg, ports = vpn.build(servers, {}, direct_if='en0')
    sock = {o['tag']: (o.get('streamSettings') or {}).get('sockopt') for o in cfg['outbounds']}
    assert sock['out%d' % ports['A']] is None
    assert sock['out%d' % ports['B']] == {'interface': 'en0'}


def test_same_server_name_in_two_subscriptions_gets_its_own_port():
    """06.10.2026: в новой подписке нашёлся сервер с тем же именем, что в провайдер B, — два входа на
    одном порту, xray не запустился, все аккаунты легли. Второй получает своё имя и порт."""
    cfg, ports = vpn.build([server('🇵🇱 Польша', host='a'), server('🇵🇱 Польша', host='b')], {})
    inb = [i['port'] for i in cfg['inbounds']]
    assert len(inb) == 2 and len(set(inb)) == 2
    assert set(ports) == {'🇵🇱 Польша', '🇵🇱 Польша #2'}

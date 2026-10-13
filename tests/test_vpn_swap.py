"""Новая подписка VPN из бота (05.10.2026): «если ВПН отвалился — кидаю КФГ в бота, ты подтягиваешь»."""
from leadgen.access import vpn_swap

ENV = 'A=1\nVPN_SUB_URL=https://old/s/1,direct:https://giga/s/2\nVPN_SUB_HWID=x\n'


def test_env_urls_roundtrip():
    assert vpn_swap.env_urls(ENV) == ['https://old/s/1', 'direct:https://giga/s/2']
    out = vpn_swap.with_urls(ENV, ['direct:https://giga/s/2', 'https://new/s/3'])
    assert 'VPN_SUB_URL=direct:https://giga/s/2,https://new/s/3\n' in out
    assert out.startswith('A=1\n') and out.endswith('VPN_SUB_HWID=x\n')


def test_bare_url_is_compared_without_direct_prefix():
    assert vpn_swap.bare('direct:https://giga/s/2') == 'https://giga/s/2'


def server(name):
    return {'remarks': name, 'outbounds': [{'tag': 'proxy', 'protocol': 'vless',
                                            'settings': {'vnext': [{'address': 'h'}]}}]}


def test_subscriptions_without_a_single_live_exit_are_dead():
    subs = {'https://old/s/1': [server('NL'), server('DE')], 'https://giga/s/2': [server('JP')]}
    ports = {'NL': 21801, 'DE': 21802, 'JP': 21803}
    dead = vpn_swap.dead_urls(['https://old/s/1', 'direct:https://giga/s/2'], subs.get, ports, live={21803: 'ip'})
    assert dead == ['https://old/s/1']


def test_mode_is_picked_by_live_exits():
    counts = {'happ': 1, 'direct': 7}
    assert vpn_swap.pick_mode(lambda mode: counts[mode]) == ('direct', 7)
    counts = {'happ': 5, 'direct': 5}
    assert vpn_swap.pick_mode(lambda mode: counts[mode]) == ('happ', 5)  # при равенстве — как все остальные
    assert vpn_swap.pick_mode(lambda mode: 0) == (None, 0)


def test_message_is_a_subscription_link():
    assert vpn_swap.looks_like_subscription('https://vpn.example/s/Abc123Def456')
    assert not vpn_swap.looks_like_subscription('https://www.instagram.com/vale1.rest/')
    assert not vpn_swap.looks_like_subscription('https://docs.google.com/spreadsheets/d/1/edit')
    assert not vpn_swap.looks_like_subscription('https://a.ru/s/x и ещё текст')

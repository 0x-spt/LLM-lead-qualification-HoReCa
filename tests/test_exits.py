"""Перевод аккаунтов с мёртвых выходов VPN на живые (05.10.2026): для команды /vpn в боте
и самолечения сторожа. Один IP — один аккаунт; своим аккаунтам выход достаётся раньше читателей."""
from leadgen.access import accounts, exits


def acc(stores, label, port, kind='own', status='active'):
    proxy = 'socks5://127.0.0.1:%d' % port if port else None
    if kind == 'own':
        aid = accounts.register_own(stores.archive, label, {'sessionid': 's'}, proxy=proxy)
    else:
        aid = accounts.register_anon(stores.archive, label, proxy)
    stores.archive.execute('UPDATE accounts SET status = %s WHERE id = %s', (status, aid))
    return aid


def row(stores, label):
    return stores.archive.execute('SELECT proxy, status FROM accounts WHERE label = %s', (label,)).fetchone()


def test_dead_accounts_move_to_free_exits_with_unique_ip(stores):
    acc(stores, 'ig3', 21801)                          # жив, выход занят
    acc(stores, 'ig6', 21850, status='proxy_dead')     # выход умер
    acc(stores, 'ig8', 21852, status='proxy_dead')     # выход пропал из конфига
    live = {21801: '1.1.1.1', 21860: '2.2.2.2', 21861: '1.1.1.1', 21863: '3.3.3.3'}
    moved, stuck = exits.reassign_dead(stores.archive, live)
    assert sorted(moved) == [('ig6', 21860, '2.2.2.2'), ('ig8', 21863, '3.3.3.3')]
    assert stuck == []
    assert row(stores, 'ig6') == {'proxy': 'socks5://127.0.0.1:21860', 'status': 'active'}
    assert row(stores, 'ig3')['proxy'] == 'socks5://127.0.0.1:21801'


def test_own_accounts_first_then_readers_and_spare_readers_switch_off(stores):
    acc(stores, 'anon-21850', 21850, kind='anon', status='proxy_dead')
    acc(stores, 'ig6', 21851, status='proxy_dead')
    moved, stuck = exits.reassign_dead(stores.archive, {21860: '2.2.2.2'})
    assert moved == [('ig6', 21860, '2.2.2.2')]
    assert stuck == ['anon-21850']
    assert row(stores, 'anon-21850')['status'] == 'disabled'  # без тревог каждые 15 минут


def test_accounts_needing_login_or_deleted_are_left_alone(stores):
    acc(stores, 'ig7', 21850, status='нужен_вход')
    acc(stores, 'ig5', 21851, status='удалён')
    assert exits.reassign_dead(stores.archive, {21860: '2.2.2.2'}) == ([], [])


def test_working_account_is_never_moved_by_a_missed_probe(stores):
    """06.10.2026: проверка выхода ig3 разово не ответила — сторож пересадил рабочий аккаунт на
    другой IP. Пересаживаем только тех, кого сборщик сам пометил proxy_dead."""
    acc(stores, 'ig3', 21801)
    assert exits.reassign_dead(stores.archive, {21862: '9.9.9.9'}) == ([], [])
    assert row(stores, 'ig3')['proxy'] == 'socks5://127.0.0.1:21801'

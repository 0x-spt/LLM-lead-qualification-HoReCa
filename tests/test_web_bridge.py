"""Мост через поисковик (06.10.2026): аккаунт заведения ищем `site:instagram.com "название" город`
в DuckDuckGo/Brave, а не поиском Instagram — он самый дефицитный (все старые аккаунты в soft_block,
676 заведений Афиши и Restoclub в очереди). Найденные ники проверяются страницей профиля теми же
правилами моста; не нашлось — обычный мост через поиск Instagram."""
from leadgen.access import accounts
from leadgen.qualify import handlers
from leadgen.core import queue as wq
from leadgen.sources import websearch
from tests.test_handlers import FakeTransport
from tests.test_websearch import FakeEngine


def venue(stores, name='Fiorentina', city='Нижний Новгород', address='Большая Покровская, 5'):
    stores.main.execute("INSERT INTO venues (name, city, level, address) VALUES (%s, %s, 'PREMIUM', %s)",
                        (name, city, address))


def queued(stores, ttype):
    return [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = %s AND status = 'pending' ORDER BY id", (ttype,)).fetchall()]


def run_check(stores, transport, payload):
    aid = accounts.register_anon(stores.archive, 'anon-1', 'socks5://127.0.0.1:21801')
    wq.enqueue(stores.archive, 'bridge_check', payload)
    task = wq.claim_next(stores.archive, ['bridge_check'], 't')
    handlers.handle_bridge_check(stores, transport, aid, task)


def test_checked_nick_becomes_venue_account_and_is_walked(stores):
    venue(stores)
    t = FakeTransport()
    t.web_status = 'ok'
    t.web_profile = {'username': 'fiorentina_nn', 'full_name': 'Fiorentina', 'pk': '77', '_source': 'page',
                     'biography': 'Ресторан итальянской кухни. Нижний Новгород, Большая Покровская 5'}
    run_check(stores, t, {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород', 'nicks': ['fiorentina_nn']})
    assert stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()['ig_account'] == 'fiorentina_nn'
    assert queued(stores, 'venue_header')[0]['ig_account'] == 'fiorentina_nn'
    assert queued(stores, 'following')[0]['pk'] == '77'
    assert queued(stores, 'bridge') == []


def test_unconfirmed_nicks_fall_back_to_instagram_search(stores):
    venue(stores)
    t = FakeTransport()
    t.web_status = 'ok'
    t.web_profile = {'username': 'fiorentina_msk', 'full_name': 'Fiorentina', 'pk': '78', '_source': 'page',
                     'biography': 'Ресторан, Москва, Тверская 1'}       # чужой город
    run_check(stores, t, {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород', 'nicks': ['fiorentina_msk']})
    assert queued(stores, 'bridge') == [{'venue_name': 'Fiorentina', 'city': 'Нижний Новгород', 'web_tried': True}]
    assert queued(stores, 'venue_header') == []


def test_web_bridge_queues_only_name_matching_nicks(stores):
    wq.enqueue(stores.archive, 'web_bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    q = 'site:instagram.com "Fiorentina" Нижний Новгород'
    page = ('<a href="https://www.instagram.com/fiorentina_nn/">x</a>'
            '<a href="https://www.instagram.com/chef_ivan/">y</a>')
    out = websearch.run_bridge_once(stores, FakeEngine({(q, 0): page}), ports=[None])
    assert out['status'] == 'ok'
    assert queued(stores, 'bridge_check') == [{'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                               'nicks': ['fiorentina_nn']}]


def test_web_bridge_without_candidates_goes_to_instagram_search(stores):
    wq.enqueue(stores.archive, 'web_bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    websearch.run_bridge_once(stores, FakeEngine(), ports=[None])
    assert queued(stores, 'bridge') == [{'venue_name': 'Fiorentina', 'city': 'Нижний Новгород', 'web_tried': True}]


def test_web_bridge_rate_limited_returns_task(stores):
    wq.enqueue(stores.archive, 'web_bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    out = websearch.run_bridge_once(stores, FakeEngine(status=429), ports=[21801])
    assert out['status'] == 'throttled'
    assert len(queued(stores, 'web_bridge')) == 1 and queued(stores, 'bridge') == []


def test_workers_never_take_web_bridge():
    from leadgen.ops import workers
    assert 'web_bridge' not in workers.TASK_TYPES and 'bridge_check' in workers.TASK_TYPES
    assert 'bridge_check' in workers.ANON_TASK_TYPES


def test_second_variant_query_without_quotes_and_no_instagram_fallback(stores):
    wq.enqueue(stores.archive, 'web_bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород', 'variant': 2})
    engine = FakeEngine()
    websearch.run_bridge_once(stores, engine, ports=[None])
    assert engine.calls[0][0] == 'site:instagram.com ресторан Fiorentina Нижний Новгород'
    assert queued(stores, 'bridge') == []          # поиск Instagram по нему уже был — не повторяем

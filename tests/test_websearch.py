import time

from leadgen.core import contacts, queue as wq
from leadgen.sources import websearch

PAGE = '''<html><a href="https://www.instagram.com/iris1_chef/">Лебедев</a>
<a href="https://instagram.com/lumen1_chef">x</a> <a href="https://www.instagram.com/p/C1abc/">пост</a>
<a href="https://www.instagram.com/explore/tags/chef/">тег</a> <cite>instagram.com/dune.restaurant/</cite>
<a href="https://www.instagram.com/iris1_chef/reels/">дубль</a></html>'''


class FakeEngine:
    def __init__(self, pages=None, status=200):
        self.pages, self.status, self.calls = pages or {}, status, []

    def __call__(self, query, page, port):
        self.calls.append((query, page, port))
        return self.status, self.pages.get((query, page), '')


def test_nicks_are_profiles_only_lowercased_and_unique():
    assert websearch.parse_nicks(PAGE) == ['iris1_chef', 'lumen1_chef', 'dune.restaurant']


def test_queries_start_with_east_consulting_then_capitals():
    qs = websearch.queries()
    assert qs[0] == ('site:instagram.com "ресторанный консультант" Новосибирск', 0)
    consult = [q for q, _ in qs if q.split('"')[1] in websearch.CONSULT_ROLES]
    assert consult and not any('Москва' in q or 'Казань' in q for q in consult)
    chefs = [(q, p) for q, p in qs if q.split('"')[1] in websearch.ROLES]
    assert chefs[0] == ('site:instagram.com "шеф-повар" Москва', 0)
    assert any('Санкт-Петербург' in q for q, _ in chefs[:16])
    assert {q.split('"')[1] for q, _ in chefs} == set(websearch.ROLES)


def test_run_once_queues_new_people_and_skips_known(stores):
    contacts.reject(stores, 'lumen1_chef', 'нет роли в шапке', 'роль')
    q0 = websearch.queries()[0][0]
    engine = FakeEngine({(q0, 0): PAGE})
    out = websearch.run_once(stores, engine, ports=[None])
    assert out == {'query': q0, 'page': 0, 'status': 'ok', 'found': 3, 'added': 2}
    tasks = [t['payload'] for t in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'profile_info' ORDER BY id").fetchall()]
    assert [t['nick'] for t in tasks] == ['iris1_chef', 'dune.restaurant']
    assert tasks[0]['source'] == 'поисковик' and tasks[0]['city'] == 'Новосибирск'


def test_done_queries_are_not_repeated(stores):
    engine = FakeEngine()
    websearch.run_once(stores, engine, ports=[None])
    websearch.run_once(stores, engine, ports=[None])
    assert engine.calls[0][:2] != engine.calls[1][:2]


def test_rate_limited_exit_cools_and_query_is_retried_elsewhere(stores):
    engine = FakeEngine(status=429)
    out = websearch.run_once(stores, engine, ports=[21801, 21802])
    assert out['status'] == 'throttled'
    engine.status = 200
    websearch.run_once(stores, engine, ports=[21801, 21802])
    assert engine.calls[1][:2] == engine.calls[0][:2]  # тот же запрос
    assert engine.calls[1][2] == 21802                  # другой выход


def test_all_exits_cooling_waits(stores):
    engine = FakeEngine(status=429)
    websearch.run_once(stores, engine, ports=[21801])
    assert websearch.run_once(stores, engine, ports=[21801]) is None
    assert len(engine.calls) == 1


def test_person_already_queued_is_not_queued_twice(stores):
    q0 = websearch.queries()[0][0]
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'iris1_chef', 'source': 'following'})
    websearch.run_once(stores, FakeEngine({(q0, 0): PAGE}), ports=[None])
    n = stores.archive.execute("SELECT count(*) AS c FROM work_queue WHERE payload->>'nick' = 'iris1_chef'").fetchone()
    assert n['c'] == 1


def test_second_engine_keeps_its_own_progress_and_cooldowns(stores):
    """05.10.2026: DuckDuckGo вторым движком параллельно Brave. Тот же запрос в другом движке —
    другая выдача, поэтому пройденное учитывается по движку; 429 у Brave не остужает выход для DDG."""
    brave, ddg = FakeEngine(status=429), FakeEngine()
    websearch.run_once(stores, brave, ports=[21801])
    out = websearch.run_once(stores, ddg, ports=[21801], name='ddg', pages=(0,))
    assert out['status'] == 'ok' and ddg.calls[0] == (brave.calls[0][0], 0, 21801)
    brave.status = 200
    websearch.run_once(stores, brave, ports=[21802])
    assert brave.calls[1][:2] == brave.calls[0][:2]  # для Brave запрос ещё не пройден


def test_ddg_soft_limit_202_counts_as_throttled(stores):
    out = websearch.run_once(stores, FakeEngine(status=202), ports=[21801], name='ddg', pages=(0,))
    assert out['status'] == 'throttled'


def test_ddg_result_links_are_unwrapped():
    page = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fquince1_chef%2F&amp;rut=1">x</a>'
            '<a class="result__url" href="https://www.instagram.com/gastro.rose/">instagram.com/gastro.rose</a>')
    assert websearch.parse_nicks(page) == ['quince1_chef', 'gastro.rose']


def test_queries_cover_how_chefs_actually_sign():
    """06.10.2026: «шире охват» — шефы подписываются и по-английски («Executive chef @…», «Head chef»),
    и «шеф ресторана»; по «шеф-повар» Brave находит только часть."""
    roles = {q.split('"')[1] for q, _ in websearch.queries()}
    assert {'executive chef', 'head chef', 'шеф ресторана', 'chef de cuisine', 'pastry chef'} <= roles
    assert {'шеф-повар', 'су-шеф', 'бренд-шеф', 'шеф-кондитер'} <= roles


def test_dead_exit_cools_like_a_rate_limited_one(stores):
    """06.10.2026: 14 из 22 выходов мертвы (кончившаяся подписка) — поисковик тратил на них больше
    половины ходов: ошибка соединения не остужала выход, в отличие от 429."""
    engine = FakeEngine(status=0)
    websearch.run_once(stores, engine, ports=[21801, 21802])
    engine.status = 200
    websearch.run_once(stores, engine, ports=[21801, 21802])
    websearch.run_once(stores, engine, ports=[21801, 21802])
    assert [c[2] for c in engine.calls] == [21801, 21802, 21802]


def test_third_wave_finds_venues_beyond_registries():
    """07.10.2026: 2ГИС, Афиша, Restoclub выбраны, очередь пуста с вечера. Поисковик ищет сами
    заведения — их аккаунты идут на шапку и обход подписок (handlers.PLACE_SOURCES)."""
    qs = websearch.queries()
    terms = {q.split('"')[1] for q, _ in qs}
    assert set(websearch.VENUE_TERMS) <= terms
    first_venue = next(i for i, (q, _) in enumerate(qs) if q.split('"')[1] in websearch.VENUE_TERMS)
    last_person = max(i for i, (q, _) in enumerate(qs) if q.split('"')[1] in websearch.EXTRA_ROLES
                      and q.rsplit('" ', 1)[-1] not in websearch.LATIN_CITIES)  # латинская волна — позже, отдельно
    assert first_venue > last_person          # заведения — после людей
    assert not any(t in ('бар', 'винный бар', 'коктейльный бар') for t in websearch.VENUE_TERMS)  # бары на паузе


def test_fourth_wave_cuisine_venues():
    terms = {q.split('"')[1] for q, _ in websearch.queries()}
    assert set(websearch.CUISINE_TERMS) <= terms and 'итальянский ресторан' in websearch.CUISINE_TERMS


# --- 12.10.2026: «ускорь сбор, расширь источники» — новые волны и несколько запросов за такт

def test_new_waves_districts_latin_and_venue_types():
    qs = [q for q, _ in websearch.queries()]
    assert 'site:instagram.com "шеф-повар" Хамовники Москва' in qs
    assert 'site:instagram.com "chef" Moscow' in qs
    assert 'site:instagram.com "fine dining" Казань' in qs


def test_city_is_recognised_in_district_and_latin_queries():
    assert websearch.city_of_query('site:instagram.com "шеф-повар" Хамовники Москва') == 'Москва'
    assert websearch.city_of_query('site:instagram.com "chef" Saint Petersburg') == 'Санкт-Петербург'
    assert websearch.city_of_query('site:instagram.com "шеф-повар" Казань') == 'Казань'
    assert websearch.city_of_query('site:instagram.com "шеф-повар" Неведомск') == ''


def test_district_query_gives_the_real_city_to_found_people(stores):
    q = 'site:instagram.com "шеф-повар" Хамовники Москва'
    page = '<a href="https://www.instagram.com/new_chef_x/">x</a>'

    class Engine(FakeEngine):
        pass
    import itertools
    qs = websearch.queries()
    idx = next(i for i, (qq, p) in enumerate(qs) if qq == q and p == 0)
    for qq, p in qs[:idx]:
        stores.archive.execute("INSERT INTO websearch_runs (query, page, port, status, engine) VALUES (%s, %s, 0, 'ok', 'brave')", (qq, p))
    websearch.run_once(stores, FakeEngine({(q, 0): page}), ports=[None])
    task = stores.archive.execute("SELECT payload FROM work_queue WHERE task_type = 'profile_info'").fetchone()['payload']
    assert task['city'] == 'Москва'


def test_several_queries_per_tick_on_different_exits(stores):
    engine = FakeEngine()
    n = websearch.tick(stores, [('brave', engine, websearch.PAGES)], ports=[21801, 21802, 21803, 21804, 21805])
    assert n == websearch.PER_TICK
    assert len({c[2] for c in engine.calls}) == websearch.PER_TICK


def test_rate_limit_cools_the_ip_not_just_the_port(stores):
    """12.10.2026: у новой подписки 143 сервера и ~30 IP — поисковик чередовал порты и бил в один IP,
    почти все запросы получали 429. Остывание и очерёдность — по IP."""
    ip_of = {21801: '1.1.1.1', 21802: '1.1.1.1', 21803: '2.2.2.2'}
    engine = FakeEngine(status=429)
    websearch.run_once(stores, engine, ports=[21801, 21802, 21803], ip_of=ip_of)
    engine.status = 200
    websearch.run_once(stores, engine, ports=[21801, 21802, 21803], ip_of=ip_of)
    assert engine.calls[0][2] == 21801 and engine.calls[1][2] == 21803   # 21802 — тот же IP, что остыл


def test_more_dealer_phrasings_and_brands_for_the_east():
    qs = [q for q, _ in websearch.queries()]
    assert 'site:instagram.com "посуда для общепита" Новосибирск' in qs
    assert 'site:instagram.com "Porland" Новосибирск' in qs
    assert not any('"Porland" Москва' in q for q in qs)

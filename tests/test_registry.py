from leadgen.core import queue as wq, settings
from leadgen.sources import registry


def v(name, check, alias='tyumen', kind='ресторан', rubrics=('Рестораны',), branches=1):
    from leadgen.sources.gis import level_for
    return {'gis_id': name, 'name': name, 'kind': kind, 'city': 'Тюмень', 'alias': alias,
            'address': 'ул. Ленина, 1', 'rubrics': list(rubrics), 'rating': 4.8, 'check': check,
            'level': level_for(check), 'branches': branches}


class FakeGis:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def search(self, slug, query, page=1):
        self.calls.append((slug, query, page))
        return self.pages.get(page, ([], 'ok'))


def test_harvest_writes_only_premium_and_upper_middle_and_queues_bridge(stores):
    gis = FakeGis({1: ([v('Чум', 3000), v('Встекле', 1500), v('Люкс', 5000), v('Без чека', None)], 'ok')})
    out = registry.harvest(stores, gis, 'tyumen', 'Тюмень', 'рестораны')
    assert out == {'status': 'ok', 'found': 4, 'added': 2}
    rows = {r['name']: r for r in stores.main.execute('SELECT * FROM venues').fetchall()}
    assert set(rows) == {'Чум', 'Люкс'}
    assert rows['Чум']['level'] == 'UPPER-MIDDLE' and rows['Чум']['avg_check'] == 3000
    assert rows['Люкс']['source'] == '2ГИС' and rows['Люкс']['status'] == 'не отработано'
    bridges = []
    while True:
        t = wq.claim_next(stores.archive, ['web_bridge'], 't')
        if not t:
            break
        bridges.append(t['payload']['venue_name'])
    assert sorted(bridges) == ['Люкс', 'Чум']


def test_new_city_gets_department_in_geo(stores):
    """Сургут в старом реестре — ноль заведений, в geo его нет: без строки лиды ушли бы в спорные."""
    from leadgen.qualify import geo
    registry.harvest(stores, FakeGis({1: ([v('Сургут-Люкс', 4000, alias='surgut')], 'ok')}), 'surgut', 'Сургут', 'рестораны')
    assert geo.department_for_city(stores.main, 'Сургут') == 'НСК'


def test_stop_formats_skipped(stores):
    gis = FakeGis({1: ([v('Пицца Мия', 4000, kind='пиццерия'), v('Шашлычный двор', 4000, rubrics=('Шашлычные',))], 'ok')})
    assert registry.harvest(stores, gis, 'tyumen', 'Тюмень', 'рестораны')['added'] == 0


def test_existing_venue_not_overwritten_or_requeued(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('Чум', 'Тюмень', 'PREMIUM', 'chum')")
    registry.harvest(stores, FakeGis({1: ([v('Чум', 3000)], 'ok')}), 'tyumen', 'Тюмень', 'рестораны')
    row = stores.main.execute("SELECT level, ig_account FROM venues WHERE name = 'Чум'").fetchone()
    assert (row['level'], row['ig_account']) == ('PREMIUM', 'chum')
    assert wq.claim_next(stores.archive, ['web_bridge'], 't') is None


def test_pages_until_empty_or_limit(stores):
    gis = FakeGis({1: ([v('A', 4000)], 'ok'), 2: ([v('B', 4000)], 'ok'), 3: ([], 'ok')})
    registry.harvest(stores, gis, 'tyumen', 'Тюмень', 'рестораны', pages=6)
    assert [c[2] for c in gis.calls] == [1, 2, 3]


def test_wrong_city_writes_nothing_and_is_recorded(stores):
    out = registry.harvest(stores, FakeGis({1: (None, 'wrong_city')}), 'habarovsk', 'Хабаровск', 'рестораны')
    assert out['status'] == 'bad_slug' and out['added'] == 0
    run = stores.archive.execute("SELECT status FROM registry_runs WHERE slug = 'habarovsk'").fetchone()
    assert run['status'] == 'bad_slug'


def test_page_without_state_raises_alert(stores):
    registry.harvest(stores, FakeGis({1: (None, 'no_state')}), 'tyumen', 'Тюмень', 'кафе')
    alert = stores.archive.execute('SELECT kind, text FROM alerts').fetchone()
    assert alert['kind'] == 'registry' and '2ГИС' in alert['text']


def test_next_pairs_skips_recent_and_run_once_respects_pause(stores, monkeypatch):
    monkeypatch.setattr(registry.gis_cities, 'CITIES', [('tyumen', 'Тюмень'), ('omsk', 'Омск')])
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны'])
    stores.archive.execute("INSERT INTO registry_runs (slug, query, harvested_at, status, found, added) "
                           "VALUES ('tyumen', 'рестораны', now(), 'ok', 1, 1)")
    assert registry.next_pairs(stores) == [('omsk', 'Омск', 'рестораны')]
    settings.put(stores.main, 'paused', '1')
    assert registry.run_once(stores, FakeGis({})) == 0
    settings.put(stores.main, 'paused', '0')
    assert registry.run_once(stores, FakeGis({})) == 1


def test_failed_pair_is_retried_soon_not_burned_for_30_days(stores, monkeypatch):
    """№4: капча не должна «выжигать» пары реестра на месяц."""
    monkeypatch.setattr(registry.gis_cities, 'CITIES', [('tyumen', 'Тюмень')])
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны'])
    registry.harvest(stores, FakeGis({1: (None, 'no_state')}), 'tyumen', 'Тюмень', 'рестораны')
    assert registry.next_pairs(stores) == []  # сразу не долбим
    stores.archive.execute("UPDATE registry_runs SET harvested_at = now() - interval '2 days'")
    assert registry.next_pairs(stores) == [('tyumen', 'Тюмень', 'рестораны')]


def test_run_once_stops_at_first_failure_with_one_alert(stores, monkeypatch):
    monkeypatch.setattr(registry.gis_cities, 'CITIES', [('tyumen', 'Тюмень'), ('omsk', 'Омск'), ('tomsk', 'Томск')])
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны'])
    gis = FakeGis({1: (None, 'http_429')})
    assert registry.run_once(stores, gis) == 1
    assert len(gis.calls) == 1
    registry.run_once(stores, FakeGis({1: (None, 'http_429')}))
    assert stores.archive.execute("SELECT count(*) AS c FROM alerts WHERE kind = 'registry'").fetchone()['c'] == 1


def test_404_after_first_page_is_end_of_results_not_failure(stores):
    """Живой прогон 29.09: у маленьких городов меньше 6 страниц — следующая отвечает 404."""
    gis = FakeGis({1: ([v('A', 4000)], 'ok'), 2: (None, 'http_404')})
    out = registry.harvest(stores, gis, 'tyumen', 'Тюмень', 'рестораны')
    assert out['status'] == 'ok' and out['added'] == 1
    assert stores.archive.execute("SELECT count(*) AS c FROM alerts").fetchone()['c'] == 0


def test_foreign_page_after_first_is_end_of_results(stores):
    """Свои заведения кончились — 2ГИС добивает выдачу соседними городами."""
    gis = FakeGis({1: ([v('A', 4000)], 'ok'), 2: (None, 'wrong_city')})
    assert registry.harvest(stores, gis, 'tyumen', 'Тюмень', 'рестораны')['status'] == 'ok'


def test_bad_slug_does_not_stop_round_and_is_not_retried_daily(stores, monkeypatch):
    monkeypatch.setattr(registry.gis_cities, 'CITIES', [('badslug', 'Нигдебург'), ('omsk', 'Омск')])
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны'])

    class G(FakeGis):
        def search(self, slug, query, page=1):
            self.calls.append((slug, query, page))
            return (None, 'http_404') if slug == 'badslug' else ([], 'ok')
    g = G({})
    assert registry.run_once(stores, g) == 2
    assert [c[0] for c in g.calls] == ['badslug', 'omsk']
    stores.archive.execute("UPDATE registry_runs SET harvested_at = now() - interval '2 days'")
    assert registry.next_pairs(stores) == []  # неверный слаг — не долбим каждый день
    alert = stores.archive.execute("SELECT text FROM alerts").fetchone()['text']
    assert 'badslug' in alert and 'Нигдебург' in alert


def test_same_registry_alert_at_most_once_a_day(stores):
    for _ in range(3):
        registry.harvest(stores, FakeGis({1: (None, 'http_429')}), 'tyumen', 'Тюмень', 'кафе')
        stores.archive.execute('UPDATE alerts SET sent_at = now()')
    assert stores.archive.execute("SELECT count(*) AS c FROM alerts").fetchone()['c'] == 1


def test_fixed_slugs():
    from leadgen.sources import gis_cities
    slugs = dict(gis_cities.CITIES)
    assert slugs.get('ntagil') == 'Нижний Тагил'
    assert slugs.get('n_urengoy') == 'Новый Уренгой'
    assert slugs.get('k_uralskiy') == 'Каменск-Уральский'
    assert 'nizhniy_tagil' not in slugs and 'noviy_urengoy' not in slugs


def test_empty_narrow_rubric_is_not_a_bad_slug(stores, monkeypatch):
    """Живой прогон 29.09: «стейк-хаус» в Рубцовске — 404, потому что их там нет,
    а не потому, что слаг неверный. 127 ложных тревог «поправьте слаг»."""
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны', 'стейк-хаус'])
    out = registry.harvest(stores, FakeGis({1: (None, 'http_404')}), 'rubtsovsk', 'Рубцовск', 'стейк-хаус')
    assert out['status'] == 'ok' and out['found'] == 0
    assert stores.archive.execute('SELECT count(*) AS c FROM alerts').fetchone()['c'] == 0
    out = registry.harvest(stores, FakeGis({1: (None, 'http_404')}), 'nowhere', 'Нигдебург', 'рестораны')
    assert out['status'] == 'bad_slug'


def test_real_2gis_slugs_29_09():
    from leadgen.sources import gis_cities
    slugs = dict(gis_cities.CITIES)
    for slug, city in (('kh_mansiysk', 'Ханты-Мансийск'), ('komsomolsk-on-amur', 'Комсомольск-на-Амуре'),
                       ('leninsk-kuzneckij', 'Ленинск-Кузнецкий'), ('p_kamchatskiy', 'Петропавловск-Камчатский'),
                       ('rostov-on-don', 'Ростов-на-Дону'), ('rubtsovsk', 'Рубцовск'),
                       ('salekhard', 'Салехард'), ('togliatti', 'Тольятти')):
        assert slugs.get(slug) == city, slug


def test_north_west_goes_to_spb_department():
    from leadgen.sources import gis_cities
    for slug in ('spb', 'kaliningrad', 'murmansk', 'petrozavodsk', 'vologda', 'arkhangelsk',
                 'v_novgorod', 'pskov', 'syktyvkar', 'cherepovets'):
        assert gis_cities.department(slug) == 'СПБ', slug
    assert gis_cities.department('kazan') == 'МСК' and gis_cities.department('omsk') == 'НСК'


def test_district_queries_go_after_first_rubric_and_before_the_rest(stores, monkeypatch):
    """01.10.2026: каждая рубрика Москвы упиралась в потолок 2ГИС (72) — у нас 352 заведения
    на всю Москву. Районы дают свой потолок на каждый."""
    monkeypatch.setattr(registry.gis_cities, 'CITIES', [('moscow', 'Москва'), ('omsk', 'Омск')])
    monkeypatch.setattr(registry.gis_cities, 'RUBRICS', ['рестораны', 'кафе'])
    monkeypatch.setattr(registry.gis_cities, 'DISTRICTS', {'moscow': ['Хамовники', 'Арбат']})
    assert registry.next_pairs(stores, limit=10) == [
        ('moscow', 'Москва', 'рестораны'), ('omsk', 'Омск', 'рестораны'),
        ('moscow', 'Москва', 'рестораны Хамовники'), ('moscow', 'Москва', 'рестораны Арбат'),
        ('moscow', 'Москва', 'кафе'), ('omsk', 'Омск', 'кафе')]


def test_every_district_city_is_a_known_city():
    from leadgen.sources import gis_cities
    slugs = {s for s, _ in gis_cities.CITIES}
    assert set(gis_cities.DISTRICTS) <= slugs
    assert len(gis_cities.DISTRICTS['moscow']) >= 40 and len(gis_cities.DISTRICTS['spb']) >= 15


def test_resorts_and_moscow_suburbs_are_searched_as_districts():
    """12.10.2026, «расширяем ещё сильнее»: премиальные места вне списка городов — курорты и пригороды."""
    from leadgen.sources import gis_cities
    d = gis_cities.DISTRICTS
    assert {'Жуковка', 'Николина Гора', 'Истра'} <= set(d['moscow'])
    assert {'Роза Хутор', 'Сириус'} <= set(d['sochi'])
    assert 'Кисловодск' in d['pyatigorsk'] and 'Шерегеш' in d['novokuznetsk'] and 'Суздаль' in d['vladimir']

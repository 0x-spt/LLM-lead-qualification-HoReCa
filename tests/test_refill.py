"""Автосмена источников (05.10.2026): «если один источник исчерпал себя, автоматически
находились другие». 03.10 очередь опустела — сбор стоял двое суток при готовом резерве."""
from leadgen.core import alerts, queue as wq, settings
from leadgen.sources import expand, gis_cities, refill, registry, rewalk
from leadgen.qualify import geo, handlers


def queued(stores, ttype):
    return [r['payload'] for r in stores.archive.execute(
        'SELECT payload FROM work_queue WHERE task_type = %s ORDER BY id', (ttype,)).fetchall()]


# --- глубина обхода задаётся меткой повторного обхода

def test_rewalk_pages_reach_the_following_task(stores, monkeypatch, tmp_path):
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    assert handlers._queue_walk(stores, '7', 'deep_rest', 'Deep', 'Москва', rewalk='deep-8', pages=8)
    assert queued(stores, 'following')[0]['max_pages'] == 8


def test_rewalk_enqueue_passes_pages(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('A', 'Москва', 'PREMIUM', 'a_rest')")
    assert rewalk.enqueue(stores, 'deep-8', levels=('PREMIUM',), pages=8) == 1
    assert queued(stores, 'venue_header')[0]['pages'] == 8


# --- 2ГИС: дополнительные рубрики открываются по одной

def test_extra_rubrics_unlock_one_by_one(stores, monkeypatch):
    monkeypatch.setattr(gis_cities, 'CITIES', [('omsk', 'Омск')])
    monkeypatch.setattr(gis_cities, 'RUBRICS', ['рестораны'])
    monkeypatch.setattr(gis_cities, 'DISTRICTS', {})
    monkeypatch.setattr(gis_cities, 'EXTRA_RUBRICS', ['гастробар', 'винный ресторан'])
    assert [q for _, _, q in registry.next_pairs(stores)] == ['рестораны']
    settings.put(stores.main, 'gis_extra_rubrics', '1')
    assert [q for _, _, q in registry.next_pairs(stores)] == ['рестораны', 'гастробар']


# --- новые заведения: посев без меток, раз на заведение

def russian_city(stores):
    geo.upsert_city(stores.main, 'Москва', 'Москва', 'МСК')


def test_enqueue_new_seeds_only_unseeded_russian_venues(stores):
    russian_city(stores)
    stores.main.execute(
        "INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
        "('Шеф-место', 'Москва', NULL, 'chef_place', '5'), ('Prem', 'Москва', 'PREMIUM', 'prem', '6'), "
        "('Abroad', 'Dubai', NULL, 'abroad', '7'), ('Low', 'Москва', 'LOW', 'low_cafe', '8')")
    out = expand.enqueue_new(stores)
    assert out == {'venue_tags': 2, 'similar': 2, 'venue_geo': 0}
    assert {p['ig_account'] for p in queued(stores, 'venue_tags')} == {'chef_place', 'prem'}
    assert expand.enqueue_new(stores) == {'venue_tags': 0, 'similar': 0, 'venue_geo': 0}


def test_walk_new_heads_venues_never_headed(stores):
    russian_city(stores)
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
                        "('A', 'Москва', NULL, 'a_rest', '1'), ('B', 'Москва', 'PREMIUM', 'b_rest', '2')")
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'B', 'city': 'Москва', 'ig_account': 'b_rest'})
    assert refill.walk_new(stores) == 1
    assert refill.walk_new(stores) == 0
    assert [p['ig_account'] for p in queued(stores, 'venue_header')] == ['b_rest', 'a_rest']


# --- сам перебор источников

def fake_sources(results, calls):
    def make(name, value):
        def seed(stores):
            calls.append(name)
            return value
        return seed
    return [(name, make(name, value), 1) for name, value in results]


def test_no_refill_while_queue_is_full(stores, monkeypatch):
    calls = []
    monkeypatch.setattr(refill, 'SOURCES', fake_sources([('a', 5)], calls))
    monkeypatch.setattr(refill, 'LOW_WATER', 2)
    for i in range(3):
        wq.enqueue(stores.archive, 'profile_info', {'nick': 'n%d' % i})
    assert refill.run_once(stores) is None and calls == []


def test_first_source_with_work_wins_and_empty_ones_cool_down(stores, monkeypatch):
    calls = []
    monkeypatch.setattr(refill, 'SOURCES', fake_sources([('пустой', 0), ('рабочий', 7), ('третий', 3)], calls))
    assert refill.run_once(stores) == ('рабочий', 7)
    assert calls == ['пустой', 'рабочий']
    calls.clear()
    assert refill.run_once(stores) == ('рабочий', 7)
    assert calls == ['рабочий']  # пустой остывает


def test_all_sources_exhausted_alerts_once(stores, monkeypatch):
    calls = []
    monkeypatch.setattr(refill, 'SOURCES', fake_sources([('a', 0), ('b', 0)], calls))
    assert refill.run_once(stores) == ('', 0)
    refill.run_once(stores)
    texts = [r['text'] for r in stores.archive.execute('SELECT text FROM alerts').fetchall()]
    assert len(texts) == 1 and 'исчерпаны' in texts[0]


def test_real_source_list_is_ordered_and_complete():
    names = [name for name, _, _ in refill.SOURCES]
    assert names[0].startswith('новые заведения') and len(names) >= 5


def test_venue_without_city_is_taken_when_our_chef_works_there(stores):
    """Места работы из шапок шефов часто без города: 728 из 945 на 05.10.2026."""
    russian_city(stores)
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
                        "('Без города', '', NULL, 'nocity_rest', '1'), ('Ничьё', '', NULL, 'orphan_rest', '2')")
    stores.main.execute("INSERT INTO contacts (nick, status, venue, department) VALUES ('chef_x', 'выгружен', 'Без города', 'МСК')")
    assert {p['ig_account'] for p in queued(stores, 'venue_tags')} == set()
    expand.enqueue_new(stores)
    assert {p['ig_account'] for p in queued(stores, 'venue_tags')} == {'nocity_rest'}
    assert refill.walk_new(stores) == 1


def test_busy_source_is_skipped_without_cooldown(stores, monkeypatch):
    """05.10.2026: «глубже PREMIUM» сработал дважды за час — 8, потом 11 страниц поверх
    незаконченного прохода. Занятый источник (None) пропускается и не остывает."""
    calls = []

    def busy(s):
        calls.append('занят')
        return None
    monkeypatch.setattr(refill, 'SOURCES', [('занят', busy, 30)] + fake_sources([('запасной', 4)], calls))
    assert refill.run_once(stores) == ('запасной', 4)
    calls.clear()
    refill.run_once(stores)
    assert calls[0] == 'занят'  # не остыл — пробуется снова


def test_deeper_premium_waits_for_previous_depth(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES ('A', 'Москва', 'PREMIUM', 'a_rest', '1')")
    assert refill.deeper_premium(stores) == 1
    assert refill.deeper_premium(stores) is None
    stores.archive.execute("UPDATE work_queue SET status = 'done'")
    assert refill.deeper_premium(stores) == 1


def test_deeper_upper_middle_after_premium(stores):
    """06.10.2026: PREMIUM пройдены до 15 страниц, очередь опустела к 18:00 — резерв — подписки
    UPPER-MIDDLE (≈4 000 заведений), которые обходились всего на 3 страницы."""
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
                        "('U', 'Москва', 'UPPER-MIDDLE', 'u_rest', '1'), ('P', 'Москва', 'PREMIUM', 'p_rest', '2')")
    assert refill.deeper_upper(stores) == 1
    assert queued(stores, 'venue_header')[0]['ig_account'] == 'u_rest'
    assert queued(stores, 'venue_header')[0]['pages'] == handlers.MAX_FOLLOWING_PAGES + refill.DEPTH_STEP
    assert refill.deeper_upper(stores) is None           # прошлая глубина ещё в работе
    names = [n for n, _, _ in refill.SOURCES]
    assert names.index('глубже подписки UPPER-MIDDLE') == names.index('глубже подписки PREMIUM') + 1


# --- резервы (07.10.2026): «если этот кончился — уже подготовлены другие»

def test_monthly_tags_refresh_only_seeds_tags(stores):
    russian_city(stores)
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES ('A', 'Москва', 'PREMIUM', 'a_rest', '1')")
    assert refill.retag_month(stores) == 1
    assert queued(stores, 'venue_tags')[0]['ig_account'] == 'a_rest'
    assert queued(stores, 'similar') == []
    assert refill.retag_month(stores) == 0          # в этом месяце уже поставлено


def test_bridge_retry_for_venues_without_account(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES "
                        "('Без аккаунта', 'Москва', 'PREMIUM', NULL), ('Мелкое', 'Москва', 'MIDDLE', NULL), "
                        "('С аккаунтом', 'Москва', 'PREMIUM', 'acc')")
    assert refill.bridge_retry(stores) == 1
    assert queued(stores, 'web_bridge') == [{'venue_name': 'Без аккаунта', 'city': 'Москва', 'variant': 2}]
    assert refill.bridge_retry(stores) == 0


def test_reserve_sources_are_lined_up():
    names = [n for n, _, _ in refill.SOURCES]
    for reserve in ('отметки заведений: повтор месяца', 'мост: второй запрос для заведений без аккаунта'):
        assert reserve in names

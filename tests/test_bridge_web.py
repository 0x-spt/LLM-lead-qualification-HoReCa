"""Мост «заведение → аккаунт» по веб-странице профиля, когда API профилей закрыт.

Правило трёх проверок не ослабляется: название, общепит, и город или улица —
теперь из шапки против адреса 2ГИС (строже, чем city_name API)."""
from leadgen.qualify import geo, handlers, quality
from leadgen.access import health
from leadgen.core import queue as wq
from tests.test_handlers import FakeTransport, claim, queued, aid  # noqa: F401


def setup_geo(stores):
    for city, dep in (('Сургут', 'НСК'), ('Тюмень', 'НСК'), ('Москва', 'МСК')):
        geo.upsert_city(stores.main, city, 'x', dep)


def test_verify_accepts_city_in_bio(stores):
    setup_geo(stores)
    ok, why = quality.bridge_verify_web('lesnoy_surgut', 'Лесной', 'Ресторан в Сургуте. Бронь +7…',
                                        'Лесной', 'Сургут', '', stores.main)
    assert ok, why


def test_verify_accepts_street_from_2gis_address(stores):
    setup_geo(stores)
    ok, _ = quality.bridge_verify_web('vstekle', 'Встекле', 'Ресторан. Машарова 2/1, 1 этаж',
                                      'Встекле', 'Тюмень', 'улица Николая Машарова, 2/1', stores.main)
    assert ok


def test_verify_rejects_other_city(stores):
    setup_geo(stores)
    ok, why = quality.bridge_verify_web('lesnoy_msk', 'Лесной', 'Ресторан в Москве', 'Лесной', 'Сургут', '',
                                        stores.main)
    assert (ok, why) == (False, 'конфликт города')


def test_verify_rejects_unconfirmed_city(stores):
    setup_geo(stores)
    ok, why = quality.bridge_verify_web('lesnoy', 'Лесной', 'Лучший ресторан', 'Лесной', 'Сургут', '', stores.main)
    assert (ok, why) == (False, 'город не подтверждён шапкой')


def test_verify_rejects_non_food_and_name_mismatch(stores):
    setup_geo(stores)
    assert quality.bridge_verify_web('lesnoy_fitness', 'Лесной', 'Фитнес-клуб, Сургут', 'Лесной', 'Сургут', '',
                                     stores.main) == (False, 'нет признака общепита')
    assert quality.bridge_verify_web('other', 'Другое', 'Ресторан, Сургут', 'Лесной', 'Сургут', '',
                                     stores.main)[0] is False


def test_registry_stores_address(stores):
    from leadgen.sources import registry

    class G:
        def search(self, slug, query, page=1):
            if page > 1:
                return [], 'ok'
            return [{'gis_id': '1', 'name': 'Встекле', 'kind': 'ресторан', 'city': 'Тюмень', 'alias': 'tyumen',
                     'address': 'улица Николая Машарова, 2/1', 'rubrics': ['Рестораны'], 'rating': 4.9,
                     'check': 3000, 'level': 'UPPER-MIDDLE', 'branches': 1}], 'ok'
    registry.harvest(stores, G(), 'tyumen', 'Тюмень', 'рестораны')
    assert stores.main.execute("SELECT address FROM venues WHERE name = 'Встекле'").fetchone()['address'] == \
        'улица Николая Машарова, 2/1'


def test_bridge_uses_page_when_api_profile_blocked(stores, aid):
    """API профилей в 429 на IP VPN — мост идёт страницей и строгой сверкой."""
    setup_geo(stores)
    stores.main.execute("INSERT INTO venues (name, city, address) VALUES ('Лесной', 'Сургут', 'улица Ленина, 1')")
    health.record_failure(stores.archive, aid, handlers.ENDPOINT_PROFILE, 'throttled')
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Лесной', 'city': 'Сургут'})
    t = FakeTransport(users=[{'pk': 50, 'username': 'lesnoy_surgut', 'full_name': 'Лесной'}])
    t.web_status = 'ok'
    t.web_profile = {'pk': '50', 'username': 'lesnoy_surgut', 'full_name': 'Лесной',
                     'biography': 'Ресторан в Сургуте', 'category': '', 'city_name': '', '_source': 'page'}
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    row = stores.main.execute("SELECT ig_account, pk FROM venues WHERE name = 'Лесной'").fetchone()
    assert (row['ig_account'], row['pk']) == ('lesnoy_surgut', '50')
    assert queued(stores, 'following')


def test_bridge_page_route_keeps_strictness(stores, aid):
    setup_geo(stores)
    stores.main.execute("INSERT INTO venues (name, city) VALUES ('Лесной', 'Сургут')")
    health.record_failure(stores.archive, aid, handlers.ENDPOINT_PROFILE, 'throttled')
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Лесной', 'city': 'Сургут'})
    t = FakeTransport(users=[{'pk': 51, 'username': 'lesnoy_msk', 'full_name': 'Лесной'}])
    t.web_status = 'ok'
    t.web_profile = {'pk': '51', 'username': 'lesnoy_msk', 'full_name': 'Лесной',
                     'biography': 'Ресторан в Москве', 'category': '', 'city_name': '', '_source': 'page'}
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    assert stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Лесной'").fetchone()['ig_account'] is None


def test_bridge_claimable_with_only_page_route(stores, aid):
    from leadgen.ops import workers
    health.record_failure(stores.archive, aid, handlers.ENDPOINT_PROFILE, 'throttled')
    assert 'bridge' in workers.claimable_task_types(stores.archive, [aid])[0]


def test_own_transport_marks_page_profiles():
    import httpx
    from leadgen.access.transport.own import OwnTransport
    html = ('<meta content="10 Followers, 1 Following, 5 Posts - Лесной (&#064;x) on Instagram: '
            '&quot;Ресторан&quot;" name="description" />')
    t = OwnTransport({'sessionid': 's'}, http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    assert t.profile_by_nick('x')[0]['_source'] == 'page'


def test_registry_fills_missing_address_without_requeue(stores):
    from leadgen.sources import registry
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('Встекле', 'Тюмень', 'PREMIUM', 'vstekle')")

    class G:
        def search(self, slug, query, page=1):
            if page > 1:
                return [], 'ok'
            return [{'gis_id': '1', 'name': 'Встекле', 'kind': 'ресторан', 'city': 'Тюмень', 'alias': 'tyumen',
                     'address': 'улица Николая Машарова, 2/1', 'rubrics': ['Рестораны'], 'rating': 4.9,
                     'check': 3000, 'level': 'UPPER-MIDDLE', 'branches': 1}], 'ok'
    out = registry.harvest(stores, G(), 'tyumen', 'Тюмень', 'рестораны')
    row = stores.main.execute("SELECT address, level, ig_account FROM venues WHERE name = 'Встекле'").fetchone()
    assert row['address'] == 'улица Николая Машарова, 2/1'
    assert (row['level'], row['ig_account'], out['added']) == ('PREMIUM', 'vstekle', 0)
    assert wq.claim_next(stores.archive, ['bridge'], 't') is None

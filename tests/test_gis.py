import os

import httpx

from leadgen.sources import gis, gis_cities

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'gis_search.html')


def page():
    return open(FIX, encoding='utf-8').read()


def test_parse_real_state():
    venues = gis.venues_from_state(gis.parse_state(page()))
    by = {v['name']: v for v in venues}
    assert set(by) == {'clou', 'Встекле', 'Чум', 'Омар Хайям'}
    assert by['Встекле']['kind'] == 'ресторан' and by['Встекле']['check'] == 1500
    assert by['Чум']['level'] == 'UPPER-MIDDLE' and by['Чум']['city'] == 'Тюмень'
    assert by['Чум']['alias'] == 'tyumen' and 'Рестораны' in by['Чум']['rubrics']
    assert by['Омар Хайям']['branches'] == 3
    assert by['clou']['check'] is None and by['clou']['level'] == ''
    assert by['Встекле']['address'].startswith('улица Николая Машарова')


def test_level_scale():
    assert [gis.level_for(c) for c in (None, 1900, 2000, 2500, 3499, 3500)] == \
        ['', 'LOW', 'MIDDLE', 'UPPER-MIDDLE', 'UPPER-MIDDLE', 'PREMIUM']


def test_page_without_state():
    assert gis.parse_state('<html>captcha</html>') is None


def test_client_search_ok_and_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=page())
    c = gis.GisClient(http_transport=httpx.MockTransport(handler), sleep=lambda s: None)
    items, status = c.search('tyumen', 'рестораны', page=2)
    assert status == 'ok' and len(items) == 4
    assert seen[0].endswith('/tyumen/search/%D1%80%D0%B5%D1%81%D1%82%D0%BE%D1%80%D0%B0%D0%BD%D1%8B/page/2')


def test_client_detects_city_substitution():
    """Ловушка 17.09: неверный слаг молча уводит на Новосибирск."""
    c = gis.GisClient(http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page())),
                      sleep=lambda s: None)
    assert c.search('khabarovsk', 'рестораны') == (None, 'wrong_city')


def test_client_no_state_and_http_error():
    c = gis.GisClient(http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<html></html>')),
                      sleep=lambda s: None)
    assert c.search('tyumen', 'кафе') == (None, 'no_state')
    c = gis.GisClient(http_transport=httpx.MockTransport(lambda r: httpx.Response(503)), sleep=lambda s: None)
    assert c.search('tyumen', 'кафе') == (None, 'http_503')


def test_client_keeps_pace():
    slept = []
    c = gis.GisClient(http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page())),
                      delay=2.0, sleep=slept.append)
    c.search('tyumen', 'кафе')
    c.search('tyumen', 'кафе', page=2)
    assert slept and all(s <= 2.0 for s in slept)


def test_cities_and_rubrics():
    slugs = dict(gis_cities.CITIES)
    assert slugs['khabarovsk'] == 'Хабаровск' and slugs['yuzhnosakhalinsk'] == 'Южно-Сахалинск'
    assert 'рестораны' in gis_cities.RUBRICS
    assert gis_cities.department('surgut') == 'НСК' and gis_cities.department('kazan') == 'МСК'
    assert gis_cities.department('ufa') == 'МСК'  # ловушка: Уфа — «Москва»
    assert not any('бар' in r for r in gis_cities.RUBRICS if r != 'бистро')


def test_mixed_city_page_drops_foreign_cards():
    """№5: на смешанной странице чужие карточки не пишутся под запрошенный город."""
    state = gis.parse_state(page())
    profiles = state['data']['entity']['profile']
    first = next(iter(profiles))
    profiles[first]['data']['city_alias'] = 'novosibirsk'
    html = "var initialState = JSON.parse('%s')" % __import__('json').dumps(state, ensure_ascii=False).replace('\\', '\\\\').replace("'", "\\'")
    c = gis.GisClient(http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)), sleep=lambda s: None)
    items, status = c.search('tyumen', 'рестораны')
    assert status == 'ok' and len(items) == 3 and all(v['alias'] == 'tyumen' for v in items)


# --- Отдел по близости (01.10.2026): «какой отдел ближе к городу, туда и пойдёт»,
# Северо-Западный федеральный округ — всегда СПБ (ответ заказчика на расхождения)

def test_department_is_nearest_center_with_northwest_rule():
    d = gis_cities.department
    assert d('ekaterinburg') == 'НСК' and d('chelyabinsk') == 'НСК' and d('tyumen') == 'НСК'
    assert d('ntagil') == 'МСК' and d('pervouralsk') == 'МСК' and d('magnitogorsk') == 'МСК'
    assert d('ufa') == 'МСК' and d('perm') == 'МСК' and d('orenburg') == 'МСК'
    assert d('vologda') == 'СПБ' and d('cherepovets') == 'СПБ' and d('syktyvkar') == 'СПБ'
    assert d('kaliningrad') == 'СПБ' and d('murmansk') == 'СПБ'


def test_new_cities_have_coordinates_and_department():
    from leadgen.qualify import geo_points
    names = [c for _, c in gis_cities.CITIES]
    assert len(names) == len(set(names))
    for slug, city in gis_cities.CITIES:
        assert city in geo_points.CITY_COORDS, city
        assert gis_cities.department(slug) in ('МСК', 'НСК', 'СПБ'), city
    assert gis_cities.department('nabchelny') == 'МСК' and gis_cities.department('kyzyl') == 'НСК'
    assert gis_cities.department('vyborg') == 'СПБ' and gis_cities.department('sevastopol') == 'МСК'


def test_region_label_follows_department():
    assert gis_cities.region('ntagil') == 'Москва' and gis_cities.region('surgut') == 'Восток'
    assert gis_cities.region('vologda') == 'Питер'

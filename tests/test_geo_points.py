from leadgen.qualify import geo_points as gp


def loc(lat, lng, name=''):
    return {'lat': lat, 'lng': lng, 'name': name}


def test_every_collection_city_has_coordinates():
    from leadgen.sources import gis_cities
    assert {c for _, c in gis_cities.CITIES} <= set(gp.CITY_COORDS)


def test_point_near_city_names_the_city():
    assert gp.classify(loc(56.0099, 92.8661, 'Krasnoyarsk')) == ('ru', 'Красноярск')
    assert gp.classify(loc(56.18, 92.49, 'Аэропорт Емельяново')) == ('ru', 'Красноярск')


def test_foreign_points():
    assert gp.classify(loc(43, 12, 'Italy'))[0] == 'foreign'
    assert gp.classify(loc(25.2, 55.27, 'Dubai'))[0] == 'foreign'
    assert gp.classify(loc(53.9, 27.56, 'Минск'))[0] == 'foreign'      # 330 км от Смоленска
    assert gp.classify(loc(41.72, 44.79, 'Tbilisi'))[0] == 'foreign'   # 150 км от Владикавказа
    assert gp.classify(loc(60.17, 24.94, 'Helsinki'))[0] == 'foreign'


def test_russia_pin_without_city():
    assert gp.classify(loc(61.5, 105.3, 'Russia')) == ('ru', None)


def test_verdict_by_majority_of_located_posts():
    kras = [loc(56.01, 92.85)] * 3
    italy = [loc(43, 12, 'Italy')] * 4
    assert gp.verdict(kras + [None] * 9) == ('ru', 'Красноярск')
    assert gp.verdict(italy + [loc(56.01, 92.85)]) == ('foreign', None)
    assert gp.verdict([None] * 12) == ('none', None)
    assert gp.verdict([loc(56.01, 92.85)]) == ('ru', None)  # одна метка — город не назначаем
    assert gp.names(italy + kras) == ['Italy']


NW_30 = ['Санкт-Петербург', 'Сестрорецк', 'Зеленогорск', 'Пушкин', 'Петергоф', 'Кронштадт', 'Выборг', 'Гатчина',
         'Всеволожск', 'Приозерск', 'Калининград', 'Светлогорск', 'Зеленоградск', 'Петрозаводск', 'Сортавала',
         'Великий Новгород', 'Старая Русса', 'Псков', 'Великие Луки', 'Вологда', 'Череповец', 'Великий Устюг',
         'Мурманск', 'Кировск', 'Апатиты', 'Архангельск', 'Северодвинск', 'Нарьян-Мар', 'Сыктывкар', 'Ухта']


def test_northwest_30_cities_are_searched_for_spb():
    """Заказчик 06.10.2026: Северо-Запад — 30 городов, поиск по ним для отдела СПБ.
    Пригороды Петербурга (Сестрорецк, Пушкин…) — районы раздела spb в 2ГИС, остальные — свои разделы."""
    from leadgen.sources import gis_cities
    own = {c for _, c in gis_cities.CITIES}
    districts = set(gis_cities.DISTRICTS['spb'])
    for city in NW_30:
        assert city in own or city in districts, city
        assert gis_cities.department_of_city(city) == "СПБ", city
        assert city in gp.CITY_COORDS, city
    slugs = dict((c, s) for s, c in gis_cities.CITIES)
    assert slugs['Старая Русса'] == 'staraya-russa' and slugs['Великий Устюг'] == 'velikij-ustyug'

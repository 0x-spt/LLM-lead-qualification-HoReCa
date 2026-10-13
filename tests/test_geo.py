from leadgen.qualify import geo


def test_department_for_known_city(stores):
    geo.upsert_city(stores.main, 'Новосибирск', 'Восток', 'НСК')
    assert geo.department_for_city(stores.main, 'Новосибирск') == 'НСК'


def test_ufa_is_moscow_department_not_ural_trap(stores):
    geo.upsert_city(stores.main, 'Уфа', 'Москва', 'МСК')
    assert geo.department_for_city(stores.main, 'Уфа') == 'МСК'


def test_upsert_overwrites(stores):
    geo.upsert_city(stores.main, 'Пермь', 'Восток', 'НСК')
    geo.upsert_city(stores.main, 'Пермь', 'Москва', 'МСК')
    assert geo.department_for_city(stores.main, 'Пермь') == 'МСК'


def test_unknown_city_returns_none(stores):
    assert geo.department_for_city(stores.main, 'Атлантида') is None


def test_latin_city_names_from_instagram_match_russian_geo(stores):
    """I1: city_name у Instagram часто латиницей."""
    m = stores.main
    for city, dep in (('Москва', 'МСК'), ('Новосибирск', 'НСК'), ('Екатеринбург', 'НСК'),
                      ('Нижний Новгород', 'МСК'), ('Санкт-Петербург', 'СПБ'), ('Южно-Сахалинск', 'НСК')):
        geo.upsert_city(m, city, 'x', dep)
    assert geo.department_for_city(m, 'Moscow') == 'МСК'
    assert geo.department_for_city(m, 'Novosibirsk') == 'НСК'
    assert geo.department_for_city(m, 'Yekaterinburg') == 'НСК'
    assert geo.department_for_city(m, 'Nizhniy Novgorod') == 'МСК'
    assert geo.department_for_city(m, 'Saint Petersburg') == 'СПБ'
    assert geo.department_for_city(m, 'Yuzhno-Sakhalinsk') == 'НСК'
    assert geo.department_for_city(m, 'Dubai') is None


def test_find_city_in_bio_text(stores):
    m = stores.main
    for city, dep in (('Тюмень', 'НСК'), ('Пермь', 'МСК'), ('Новосибирск', 'НСК'), ('Орёл', 'МСК')):
        geo.upsert_city(m, city, 'x', dep)
    assert geo.find_city(m, 'Петренко Роман Шеф-повар Тюмень') == 'Тюмень'
    assert geo.find_city(m, '📍Ural | Perm city 🇷🇺 #МореBBQ') == 'Пермь'
    assert geo.find_city(m, 'Ресторан в Новосибирске, бронь') == 'Новосибирск'
    assert geo.find_city(m, 'Шеф-повар, люблю готовить') is None
    assert geo.find_city(m, 'орёл и решка') == 'Орёл'  # короткие названия — только целым словом
    assert geo.find_city(m, 'орёлик') is None


def test_moscow_and_spb_recognised_by_addresses(stores):
    """Живой прогон 29.09: шапки «📍Новый Арбат, 21 📍МО» и «@barvikha_hotel» не давали город."""
    m = stores.main
    geo.upsert_city(m, 'Москва', 'Москва', 'МСК')
    geo.upsert_city(m, 'Санкт-Петербург', 'Питер', 'СПБ')
    assert geo.find_city(m, '📍Комсомольский пр, 21/10 📍Новый Арбат, 21 📍МО, Красногорск') == 'Москва'
    assert geo.find_city(m, 'Brand chef @barvikha_hotel') == 'Москва'
    assert geo.find_city(m, 'шеф, мск') == 'Москва'
    assert geo.find_city(m, 'Шеф-повар, СПб, Невский 12') == 'Санкт-Петербург'
    assert geo.find_city(m, 'Ресторан на Рубинштейна') == 'Санкт-Петербург'
    assert geo.find_city(m, 'Chef LA, high standards') is None

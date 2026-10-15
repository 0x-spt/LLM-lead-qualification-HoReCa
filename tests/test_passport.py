"""Паспорт контакта (06.10.2026): разбор выгрузки заказчиком — Ташкент, Астана, Костанай, Актобе,
Караганда, Таллин в «Москве»; «Артём» (имя) в поле города; аккаунт ресторана «Улов» как человек;
Артём Белов дважды с двух аккаунтов. Шапки — настоящие, из таблицы."""
from leadgen.qualify import geo, handlers, names, passport

CHEF_TASHKENT = ('Brand-chef @sorrel1.tashkent @basil2.rest @basil2.rest.sea @indigo1.gastrobar '
                 '@moscow.cafe1.uz     Консалтинг| Обновление меню')
CHEF_KZ = '🔪Chef  @_cedar_restaurant_ 🍔Brand Chef @willow1.kz 🍖Ambassador  @meadow1.kz 🍔Concept Chef  @saffron2'
SERGEY = 'Шеф повар @harbor1_aktobe Шеф повар @ulydala_brewery @sable2'
NIKITA = ('Ex Head-Chef Synone ( Tallinn🇪🇪) Syntwo ( Lugano 🇨🇭) Synthree ( Moscow 🇷🇺) Private Chef 🏴‍☠️'
          '........Owner@ PELMEN🥟🇪🇪')
OKHOTKA = 'сами ловим.сами готовим место, где пахнет морем и готовят с душой  бренд-шеф @maple1  лучшие море'


def cities(stores):
    for c, d in (('Москва', 'МСК'), ('Тюмень', 'НСК'), ('Ростов-на-Дону', 'МСК'), ('Дербент', 'МСК'),
                 ('Артём', 'НСК'), ('Калининград', 'СПБ')):
        geo.upsert_city(stores.main, c, c, d)


def test_city_is_not_taken_from_handles_or_person_name(stores):
    cities(stores)
    assert passport.bio_city(stores.main, CHEF_TASHKENT) is None        # @moscow.cafe1.uz — не Москва
    assert passport.bio_city(stores.main, 'Chef @alder1') is None  # «Artem Petrov» не смотрим
    assert passport.bio_city(stores.main, 'Шеф-повар, Тюмень @bar_x') == 'Тюмень'
    assert passport.bio_city(stores.main, 'Brand Chef @oak2_pine2_quartz2  Дербент • Ростов-на-Дону📍') in ('Дербент', 'Ростов-на-Дону')


def test_abroad_by_handles_domains_flags(stores):
    cities(stores)
    assert passport.abroad(stores.main, CHEF_TASHKENT) == 'Ташкент'
    assert passport.abroad(stores.main, CHEF_KZ) == 'Казахстан'
    assert passport.abroad(stores.main, SERGEY) == 'Актобе'
    assert passport.abroad(stores.main, NIKITA) == 'Таллин'             # Tallinn + Lugano против Moscow
    assert passport.abroad(stores.main, 'Шеф @kestrel2_restoran 🇷🇺 Тула') is None
    assert passport.abroad(stores.main, 'Brand Chef @oak2_pine2_quartz2  Дербент • Ростов-на-Дону📍') is None
    assert passport.abroad(stores.main, 'Шеф @clove1.italianbistro  Победитель Baltic Best Set') is None
    # Москва в нике уравновешивает заграницу; флаги кухни — не место работы
    assert passport.abroad(stores.main, 'Brand & co-founder  @basil1.moscow Brand Chef: @chio_chio_san_baku') is None
    assert passport.abroad(stores.main, 'Шеф-повар Luigi Garden 🇮🇹🍕🍝') is None
    assert passport.abroad(stores.main, 'Pastry chef @wasabi1_rest') is None


def test_restaurant_account_naming_its_chef_is_a_place():
    assert handlers._venue_naming_staff('mint1', {'biography': OKHOTKA, 'full_name': '', 'category': ''})


def test_two_accounts_of_one_person_are_a_duplicate(stores):
    stores.main.execute("INSERT INTO contacts (nick, name, venue, status, bio) VALUES "
                        "('harbor2_iris2', 'Artyom Belov', '', 'к_выгрузке', "
                        "'▪️Шеф-повар  ▪️Внутренний мир моей работы @_alder.birch_')")
    bio = '▪️Шеф @breaddy.cafe @yarrow1_brest  ▪️Создаю концепты ▪️Моя жизнь @harbor2_iris2'
    assert passport.duplicate_of(stores, '_alder.birch_', bio, 'Артём Белов', 'Myst') == 'harbor2_iris2'
    assert passport.duplicate_of(stores, 'other_chef', 'Шеф @yarrow1_brest', 'Пётр Иванов', 'Myst') is None


def test_same_name_same_venue_is_a_duplicate(stores):
    stores.main.execute("INSERT INTO contacts (nick, name, venue, status) VALUES ('ivan_chef', 'Иван Петров', 'Gusto', 'выгружен')")
    assert passport.duplicate_of(stores, 'ivan.petrov.2', 'Шеф @gusto', 'Иван Петров', 'Gusto') == 'ivan_chef'


def test_city_or_role_is_not_a_surname():
    assert names.person_name('Владимир Москва') is None
    assert names.person_name('Сергей Крым') is None
    assert names.person_name('Ресторанный Консультант') is None
    assert names.person_name('Work Кулинарный') is None
    assert names.person_name('Владимир Соколов') == ['Владимир', 'Соколов']


def test_same_name_same_workplace_mention_is_a_duplicate(stores):
    """@olive и @clove — Денис Климов, обе шапки «Chef @dune2_ember2_hotels», заведение пустое."""
    bio = 'Жизнь - это маленькая кухня. Chef @dune2_ember2_hotels  @tulip_inn_rosa'
    stores.main.execute("INSERT INTO contacts (nick, name, venue, status, bio) VALUES ('clove', 'Денис Климов', '', 'выгружен', %s)", (bio,))
    assert passport.duplicate_of(stores, 'olive', bio, 'Денис Климов', '') == 'clove'
    assert passport.duplicate_of(stores, 'other', bio, 'Иван Петров', '') is None


def test_ukraine_by_language_domain_flag_and_cities(stores):
    """09.10.2026, заказчик: «много украинских профилей» — 13 спорных за неделю; шапки на украинском,
    @…_ua, 🇺🇦, Київ/Одеса/Дніпро. Флаги в целом не считаем, но 🇺🇦 — не кухня, а место."""
    cities(stores)
    assert passport.abroad(stores.main, 'Шеф-кухар @thyme1.gastro.bar 🍽️ Розробка меню для закладів 📩 Для співпраці в Direct') == 'Украина'
    assert passport.abroad(stores.main, '🔪Шеф-Кухар @juniper_cafe_ua .🔪Член Асоціації шеф-кухарів України.') == 'Украина'
    assert passport.abroad(stores.main, '🔻Посуд для ресторанів і готелів по Україні. Шоуруми у Києві, Одесі') == 'Украина'
    assert passport.abroad(stores.main, 'Ambassador of Georgian 🇬🇪 food, working in Ukraine🇺🇦') == 'Украина'
    assert passport.abroad(stores.main, 'Срібний призер Bocuse d’Or Ukraine 2021🇺🇦 Brand сhef of @tundra2_dp') == 'Украина'


def test_russian_bios_with_ukrainian_looking_words_stay(stores):
    cities(stores)
    assert passport.abroad(stores.main, 'Бренд-шеф ресторана Украинский дворик г.Геленджика @ukrdvorikgel') is None
    assert passport.abroad(stores.main, 'Бренд-Шеф ресторанов: «Пивная Библиотека и Лев Николаевич» 12+ лет в профессии') is None
    assert passport.abroad(stores.main, 'Шеф-повар. Украинская и русская кухня, Москва') is None

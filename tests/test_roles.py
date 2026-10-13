from leadgen.qualify import roles


def enabled(stores):
    roles.ensure_defaults(stores.main)
    return roles.load_enabled(stores.main)


def test_defaults_enable_chef_pastry_and_east_consulting(stores):
    # консалтинг включён 30.09.2026 решением заказчика, только отдел НСК
    codes = [p['code'] for p in enabled(stores)]
    assert codes == ['pastry_chef', 'chef', 'restorator', 'consulting', 'dealer']  # дилеры Востока — 08.10.2026
    all_codes = {r['code'] for r in stores.main.execute('SELECT code FROM role_profiles').fetchall()}
    assert {'restorator', 'director', 'purchaser', 'consulting', 'dealer', 'bar'} <= all_codes


def test_ensure_defaults_does_not_overwrite_user_changes(stores):
    roles.ensure_defaults(stores.main)
    roles.set_enabled(stores.main, 'restorator', True)
    roles.ensure_defaults(stores.main)
    assert 'restorator' in [p['code'] for p in roles.load_enabled(stores.main)]


def test_chef_matches_role_in_bio(stores):
    p, stop = roles.match(enabled(stores), '', 'Бренд-шеф ресторана @fiorentina_nn')
    assert p['code'] == 'chef' and stop is None
    assert p['crm_role'] == 'Шеф Повар' and p['crm_position'] == 'Шеф'


def test_role_in_category_counts(stores):
    p, _ = roles.match(enabled(stores), 'Шеф-повар', 'просто люблю готовить')
    assert p['code'] == 'chef'


def test_no_role_in_header(stores):
    assert roles.match(enabled(stores), 'Личный блог', 'путешествия и спорт') == \
        (None, 'должности нет в шапке профиля')


def test_pastry_chef_detected_even_with_word_chef(stores):
    p, _ = roles.match(enabled(stores), '', 'Бренд Шеф Кондитер @grove2.rest')
    assert p['code'] == 'pastry_chef'
    assert p['venue_levels'] == ['PREMIUM']


def test_chef_of_group_with_bakery_is_chef_not_pastry(stores):
    """syn_group_chef: шеф группы, где среди прочего пекарня, — шеф кухни."""
    p, _ = roles.match(enabled(stores), '', 'шеф @river_cafe @north2_bistro @river__bakery')
    assert p['code'] == 'chef'


def test_home_pastry_is_stopped(stores):
    p, stop = roles.match(enabled(stores), '', 'Шеф-кондитер. Торты на заказ')
    assert p['code'] == 'pastry_chef'
    assert stop == 'стоп-маркер должности «Шеф-кондитер»'


def test_travel_chef_is_stopped(stores):
    p, stop = roles.match(enabled(stores), '', 'Travel chef, выездной шеф на ваши ужины')
    assert p['code'] == 'chef' and stop is not None


def test_bar_roles_do_not_pass_as_chef_while_bar_is_paused(stores):
    assert roles.match(enabled(stores), '', 'Шеф-сомелье ресторана')[0] is None
    p, _ = roles.match(enabled(stores), '', 'Бренд-шеф и шеф-сомелье')
    assert p['code'] == 'chef'


def test_disabled_profile_starts_matching_after_enable(stores):
    # ресторатор включён 13.10.2026 (только НСК); пример выключенной должности — управляющий
    roles.ensure_defaults(stores.main)
    assert roles.match(roles.load_enabled(stores.main), '', 'Управляющий ресторана @x')[0] is None
    roles.set_enabled(stores.main, 'director', True)
    p, _ = roles.match(roles.load_enabled(stores.main), '', 'Управляющий ресторана @x')
    assert p['code'] == 'director'


def test_restorator_is_on_only_for_the_east():
    r = next(p for p in roles.DEFAULTS if p['code'] == 'restorator')
    assert r['enabled'] and r['departments'] == ['НСК'] and r['needs_external_proof'] is True


def test_consulting_markers_catch_turnkey_launches():
    from leadgen.qualify import roles
    consult = next(p for p in roles.DEFAULTS if p['code'] == 'consulting')
    for bio in ('Запуск ресторанов под ключ', 'Разработка меню и запуск проектов', 'Шеф-консультант',
                'Ресторанный консалтинг', 'Открытие ресторанов с нуля'):
        assert roles.match([consult], '', bio)[0] is consult, bio
    assert consult['venue_levels'] == [] and consult['departments'] == ['НСК'] and consult['enabled']


def test_consulting_markers_skip_non_restaurant_consultants():
    # холостой прогон 30.09.2026 по архиву отказов: эти попадали по голым «под ключ» / «консультант»
    from leadgen.qualify import roles
    consult = next(p for p in roles.DEFAULTS if p['code'] == 'consulting')
    for bio in ('Закупки "Под Ключ". 44-ФЗ; 223-ФЗ; Банкротные торги',
                '@korevia_auto Подбор новых авто из Южной Кореи. Консультант',
                'Бизнес-консультант | IT-партнер SYN',
                'Дизайн ресторанов и гастропространств. Авторские концепции интерьеров',
                'PR и маркетинг директор. Коммуникационный стратег и консультант',
                'Ресторанный критик. Консультант Ultima Guide'):
        role, stop = roles.match([consult], '', bio)
        assert role is None or stop, bio
    for bio in ('Бренд-шеф | 18 лет в F&B Запускаю рестораны: меню, экономика, команда',
                'Консалтинг 🤝 Разработка меню 📖 Мастер-классы',
                'BRAND PASTRY CHEF Dessert Menu Development Restaurant Consulting',
                'Шеф-повар | Консалтинг Разработка меню под концепцию Создание ТТК Ресторан с нуля'):
        assert roles.match([consult], '', bio)[0] is consult, bio

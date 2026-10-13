from leadgen.qualify import rules

CHEF = {'code': 'chef', 'title': 'Шеф-повар', 'venue_levels': ['PREMIUM', 'UPPER-MIDDLE']}
PASTRY = {'code': 'pastry_chef', 'title': 'Шеф-кондитер', 'venue_levels': ['PREMIUM']}


def person(**kw):
    p = {'full_name': 'Иван Петров', 'biography': 'Шеф-повар @fiorentina_nn', 'category': '',
         'media_count': 40, 'is_private': False}
    p.update(kw)
    return p


def venue(**kw):
    v = {'name': 'Fiorentina', 'city': 'Нижний Новгород', 'level': 'PREMIUM', 'category': 'Ресторан'}
    v.update(kw)
    return v


def ev(p=None, role=CHEF, stop=None, v='default', days=10, city='Нижний Новгород', dep='МСК'):
    return rules.evaluate(p or person(), role, stop, venue() if v == 'default' else v,
                          days, city, dep)


def test_good_chef_passes_without_strong_profile_requirement():
    assert ev() == (True, None, False)


def test_private_profile_rejected():
    assert ev(person(is_private=True))[:2] == (False, 'закрытый профиль')


def test_less_than_three_posts_rejected():
    assert ev(person(media_count=2))[:2] == (False, 'меньше трёх постов')


def test_venue_account_is_not_a_person():
    assert ev(person(category='Ресторан'))[1] == 'аккаунт заведения, а не человека'


def test_stop_format_in_header():
    assert ev(person(biography='Шеф шашлычной @mangal'))[1] == 'формат из стоп-листа'


def test_catering_and_home_orders_rejected():
    assert ev(person(biography='Шеф. Кейтеринг и банкеты'))[1].startswith('не заведение')


def test_not_working_now_rejected():
    assert ev(person(biography='ex-chef, ищу работу'))[1] == 'сейчас не работает в заведении'


def test_no_role_profile_uses_role_reason():
    assert ev(role=None, stop='должности нет в шапке профиля')[1] == 'должности нет в шапке профиля'


def test_role_stop_marker_rejects():
    assert ev(stop='стоп-маркер должности «Шеф-повар»')[1] == 'стоп-маркер должности «Шеф-повар»'


def test_freshness_unknown_rejected():
    assert ev(days=None)[1] == 'свежесть не определена: постов не видно'


def test_stale_feed_rejected_at_184_days_passes_at_183():
    assert ev(days=184)[1] == 'не активен больше 183 дней'
    assert ev(days=183)[0] is True


def test_city_outside_territories_rejected():
    assert ev(city='Dubai', dep=None)[1] == 'город «Dubai» вне территорий'


def test_venue_level_below_bar_rejected():
    assert ev(v=venue(level='MIDDLE'))[1] == 'заведение уровня MIDDLE — ниже планки для роли'


def test_pastry_chef_needs_premium():
    assert ev(role=PASTRY, v=venue(level='UPPER-MIDDLE'))[0] is False
    assert ev(role=PASTRY, v=venue(level='PREMIUM'))[0] is True


def test_cafe_only_premium():
    assert ev(v=venue(name='Кафе Пушкин', level='UPPER-MIDDLE'))[1] == 'кафе или кофейня не PREMIUM'
    assert ev(v=venue(name='Кафе Пушкин', level='PREMIUM'))[0] is True


def test_pizzeria_venue_rejected_even_if_group_is_premium():
    """16.09: PREMIUM встал шефу пиццерии в ТРЦ по уровню группы."""
    assert ev(v=venue(name='Pizza Loft', category='Пиццерия', level='PREMIUM'))[1] == \
        'заведение из стоп-листа форматов'


def test_unknown_venue_or_level_needs_strong_profile():
    assert ev(v=None) == (True, None, True)
    assert ev(v=venue(level='')) == (True, None, True)


def test_pastry_chef_without_venue_rejected():
    """I6: «нет заведения — нет контакта», кондитер только с PREMIUM."""
    assert ev(role=PASTRY, v=None)[1] == 'шеф-кондитер: заведение не PREMIUM'
    assert ev(role=PASTRY, v=venue(level=''))[1] == 'шеф-кондитер: заведение не PREMIUM'


def test_unknown_city_is_not_a_rejection():
    """I11a: города нет вовсе — решит человек, а не вечный отказ."""
    assert ev(city=None, dep=None) == (True, None, False)


def test_precheck_runs_free_rules_only():
    assert rules.precheck(person(is_private=True), CHEF, None) == (False, 'закрытый профиль')
    assert rules.precheck(person(), CHEF, None) == (True, None)


# --- Консалтинг по Востоку (30.09.2026): «запуски меню, запуски ресторанов под ключ»
CONSULT = {'code': 'consulting', 'title': 'Ресторанный консалтинг', 'venue_levels': [], 'departments': ['НСК']}
CONSULT_BIO = 'Ресторанный консультант. Запуск ресторанов под ключ, разработка меню, обучение персонала'


def test_consultant_needs_no_venue():
    ok, reason, strong = ev(person(biography=CONSULT_BIO), role=CONSULT, v=None, city='Новосибирск', dep='НСК')
    assert (ok, strong) == (True, False)


def test_consultant_venue_level_is_not_checked():
    ok, _, _ = ev(person(biography=CONSULT_BIO), role=CONSULT, v=venue(level='MIDDLE'), city='Новосибирск', dep='НСК')
    assert ok


def test_consultant_outside_east_rejected():
    ok, reason, _ = ev(person(biography=CONSULT_BIO), role=CONSULT, v=None, city='Москва', dep='МСК')
    assert not ok and reason == 'Ресторанный консалтинг ищем только в отделе НСК'


def test_consultant_with_unknown_city_goes_on():
    ok, _, _ = ev(person(biography=CONSULT_BIO), role=CONSULT, v=None, city=None, dep=None)
    assert ok


def test_consultant_training_staff_and_company_category_are_fine():
    assert rules.precheck(person(biography=CONSULT_BIO, category='Компания'), CONSULT, None) == (True, None)
    assert rules.precheck(person(biography=CONSULT_BIO), CHEF, None)[0] is False  # шефу «обучение персонала» — как было


def test_consultant_still_rejected_for_cakes_to_order():
    ok, reason = rules.precheck(person(biography='Консультант. Торты на заказ'), CONSULT, None)
    assert not ok

import time
from leadgen.qualify import quality


def test_bridge_verify_accepts_matching_venue():
    profile = {'city_name': 'Нижний Новгород', 'address_street': 'ул. Ленина', 'category': 'Ресторан'}
    ok, reason = quality.bridge_verify('fiorentina_nn', 'Fiorentina Restaurant', profile,
                                        'Fiorentina', 'Нижний Новгород')
    assert ok is True


def test_bridge_verify_accepts_via_transliterated_name():
    # «Чердак» -> tr() -> «cherdak», должно найтись в кириллической/латинской шапке без ручного транслита в тесте
    profile = {'city_name': 'Сургут', 'address_street': '', 'category': 'Кафе'}
    ok, reason = quality.bridge_verify('cherdak_surgut', 'Cherdak Cafe', profile, 'Чердак', 'Сургут')
    assert ok is True


def test_bridge_verify_rejects_wrong_city():
    profile = {'city_name': 'Москва', 'address_street': '', 'category': 'Ресторан'}
    ok, reason = quality.bridge_verify('frank_rest', 'Frank', profile, 'Frank', 'Красноярск')
    assert ok is False
    assert 'город' in reason


def test_bridge_verify_rejects_non_horeca_category():
    # Шапка салона красоты не содержит ни одного признака общепита -> отказ
    profile = {'city_name': 'Иркутск', 'address_street': '', 'category': 'Салон красоты'}
    ok, reason = quality.bridge_verify('avenue_irk', 'Avenue', profile, 'Avenue', 'Иркутск')
    assert ok is False
    assert 'общепит' in reason


def test_bridge_verify_rejects_non_food_nick_when_category_empty():
    profile = {'city_name': '', 'address_street': '', 'category': ''}
    ok, reason = quality.bridge_verify('cherdak_tattoo', 'Cherdak', profile, 'Чердак', 'Сургут')
    assert ok is False


def test_bridge_verify_rejects_when_no_food_evidence_anywhere():
    """«Волна» — фитнес: имя совпало, но в шапке нет ни одного признака общепита."""
    profile = {'city_name': '', 'address_street': '', 'category': ''}
    ok, reason = quality.bridge_verify('volna_fit', 'Volna', profile, 'Волна', 'Иркутск')
    assert ok is False
    assert 'общепит' in reason


def test_bridge_verify_rejects_too_short_venue_name():
    """Название короче 3 символов совпадёт с чем угодно — отказ до любых проверок шапки."""
    profile = {'city_name': '', 'address_street': '', 'category': 'Restaurant'}
    ok, reason = quality.bridge_verify('barbershop_r', 'Random Rest', profile, 'Р', 'Омск')
    assert ok is False
    assert 'название' in reason


def test_bridge_verify_uses_food_evidence_from_biography():
    """Категория пуста, но в био есть «меню» — это валидный признак общепита."""
    profile = {'city_name': 'Томск', 'address_street': '', 'category': '',
               'biography': 'Обновили меню, бронь по телефону'}
    ok, reason = quality.bridge_verify('parusa_tomsk', 'Паруса', profile, 'Паруса', 'Томск')
    assert ok is True


def test_bridge_verify_accepts_transliterated_city_in_profile():
    """city_name латиницей против кириллического города заведения (Novosibirsk / Новосибирск)."""
    profile = {'city_name': 'Novosibirsk', 'address_street': '', 'category': 'Restaurant'}
    ok, reason = quality.bridge_verify('somename_nsk', 'Some Restaurant Novosibirsk', profile,
                                        'Somename', 'Новосибирск')
    assert ok is True


def test_name_matches_accepts_plain_and_transliterated():
    assert quality.name_matches('fiorentina_nn', 'Fiorentina Restaurant', 'Fiorentina') is True
    assert quality.name_matches('cherdak_surgut', 'Cherdak Cafe', 'Чердак') is True


def test_name_matches_rejects_short_venue_name():
    assert quality.name_matches('barbershop_r', 'Random Rest', 'Р') is False


def test_name_matches_rejects_unrelated_candidate():
    assert quality.name_matches('barbershop_r', 'Barber Shop', 'Фиорентина') is False


def test_is_fresh_within_window():
    recent = time.time() - 10 * 86400
    assert quality.is_fresh(recent) is True


def test_is_fresh_outside_window():
    old = time.time() - 200 * 86400
    assert quality.is_fresh(old) is False


def test_is_fresh_none_when_no_date():
    assert quality.is_fresh(0) is None


def test_looks_like_person_true_for_role_marker_in_username():
    assert quality.looks_like_person('chef_ivanov', '') is True


def test_looks_like_person_true_for_two_word_full_name():
    assert quality.looks_like_person('random_nick', 'Иван Петров') is True


def test_looks_like_person_false_for_venue_looking_account():
    assert quality.looks_like_person('restaurant_official', 'официальный аккаунт ресторана') is False


def test_has_role_true_when_role_in_bio():
    assert quality.has_role('шеф-повар ресторана', '', '', '') is True


def test_has_role_true_when_role_only_in_category():
    assert quality.has_role('', 'Иван Петров', 'ресторатор', '') is True


def test_has_role_false_when_no_role_anywhere():
    assert quality.has_role('люблю путешествия', 'Иван Петров', '', '') is False


def test_has_role_true_for_bare_role_words_passed_by_prefilter():
    """ROLE была уже предфильтра ROLE_MARK: на этих формулировках мы тратили
    запрос и затем отклоняли человека."""
    assert quality.has_role('Chef | Kazan', '', '', '') is True
    assert quality.has_role('Повар', '', '', '') is True
    assert quality.has_role('Шеф Волна', '', '', '') is True
    assert quality.has_role('Sous chef', '', '', '') is True
    assert quality.has_role('Кондитер', '', '', '') is True
    assert quality.has_role('Chef de cuisine', '', '', '') is True
    # cook проходил предфильтр looks_like_person, но отклонялся по ROLE
    assert quality.looks_like_person('cook_vasya', '') is True
    assert quality.has_role('Cook', '', '', '') is True


def test_has_role_false_for_bar_roles_while_direction_is_paused():
    """Барное направление на паузе с 16.09.2026: бармены, сомелье и
    бар-менеджеры не должны получать «на_проверке» и уезжать в выгрузку."""
    assert quality.has_role('сомелье', '', '', '') is False
    assert quality.has_role('sommelier', '', '', '') is False
    assert quality.has_role('бар-менеджер', '', '', '') is False
    assert quality.has_role('бармен', '', '', '') is False


def test_has_role_true_for_brand_chef_despite_bar_pause():
    """«Шеф бара остаётся шефом»: конструкции со словом «шеф» паузой не затронуты."""
    assert quality.has_role('бренд-шеф', '', '', '') is True
    assert quality.has_role('шеф-повар бара', '', '', '') is True


def test_looks_like_person_false_for_bar_only_markers():
    """Предфильтр не должен тратить запросы на барные ники."""
    assert quality.looks_like_person('barman_vova', '') is False
    assert quality.looks_like_person('somm_anna', '') is False


def test_bar_compound_roles_do_not_slip_through_bare_shef():
    """Регресс 22.09: «сомелье» убрали из ROLE (барное направление на паузе с
    16.09.2026), но следом добавили голое «шеф» — и «шеф-сомелье» снова прошёл,
    потому что содержит «шеф». Барные составные роли обязаны отсекаться."""
    assert quality.has_role('шеф-сомелье', '', '', '') is False
    assert quality.has_role('шеф-бармен ресторана', '', '', '') is False
    assert quality.has_role('Шеф-сомелье @somebody', '', '', '') is False


def test_bar_filter_does_not_reject_real_chef_of_a_bar():
    """«Шеф бара остаётся шефом»: отсекаем барные роли, а не слово «бар»."""
    assert quality.has_role('бренд-шеф бара «Булгаков»', '', '', '') is True
    assert quality.has_role('шеф-повар гриль-бара', '', '', '') is True
    assert quality.has_role('бренд-шеф и шеф-сомелье', '', '', '') is True

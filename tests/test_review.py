from leadgen.core import contacts, queue as wq
from leadgen.qualify import review, roles


def disputed(stores, nick='a'):
    stores.main.execute("INSERT INTO contacts (nick, status, venue) VALUES (%s, 'спорный', 'X')", (nick,))


def test_take_moves_to_export_and_records_human_verdict(stores):
    disputed(stores)
    assert review.take(stores, 'a') is True
    assert stores.main.execute("SELECT status FROM contacts WHERE nick='a'").fetchone()['status'] == 'к_выгрузке'
    v = stores.main.execute('SELECT verdict, by_human, stage FROM verdicts').fetchone()
    assert (v['verdict'], v['by_human'], v['stage']) == ('accept', True, 'человек')


def test_drop_moves_to_archive_with_reason(stores):
    disputed(stores)
    assert review.drop(stores, 'a', 'профиль выглядит дёшево') is True
    assert stores.main.execute("SELECT count(*) AS c FROM contacts").fetchone()['c'] == 0
    rej = stores.archive.execute("SELECT reason, stage FROM rejected WHERE nick='a'").fetchone()
    assert (rej['reason'], rej['stage']) == ('профиль выглядит дёшево', 'человек')


def test_double_drop_is_harmless(stores):
    disputed(stores)
    review.drop(stores, 'a')
    assert review.drop(stores, 'a') is False
    assert stores.main.execute("SELECT count(*) AS c FROM verdicts").fetchone()['c'] == 1


def test_take_only_from_disputed(stores):
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('b', 'выгружен')")
    assert review.take(stores, 'b') is False


def test_role_on_resurrects_rejected_of_that_role(stores):
    roles.ensure_defaults(stores.main)
    contacts.reject(stores, 'owner1', 'должность выключена: Ресторатор / собственник', 'роль_выкл',
                    pk='5', source='following')
    contacts.reject(stores, 'other', 'нет роли в шапке', 'роль', pk='6')
    ok, back = review.role_on(stores, 'restorator')
    assert ok is True and back == 1
    assert 'restorator' in [p['code'] for p in roles.load_enabled(stores.main)]
    assert contacts.is_known(stores, 'owner1') is False
    task = wq.claim_next(stores.archive, ['profile_info'], 't')
    assert task['payload'] == {'nick': 'owner1', 'pk': '5', 'source': 'following'}


def test_role_unknown_code(stores):
    roles.ensure_defaults(stores.main)
    assert review.role_on(stores, 'nope') == (False, 0)


# --- Проверка отказов человеком: калибровка критериев (29.09.2026)

def test_reviewable_skips_guests_disabled_roles_and_human_decisions():
    assert review.reviewable('лента старше 183 дней', 'правила')
    assert review.reviewable('консультант без заведения', 'оценка')
    assert not review.reviewable('нет роли в шапке', 'правила')
    assert not review.reviewable('должность выключена: Ресторатор', 'роль_выкл')
    assert not review.reviewable('отклонён заказчиком', 'человек')
    assert not review.reviewable('упоминание', 'импорт')  # старый архив из contacts.csv
    assert not review.reviewable('аккаунт заведения — обход подписок', 'роль')


def test_rejects_review_is_off_until_switched_on(stores):
    assert review.rejects_on(stores) is False
    review.set_rejects(stores, True)
    assert review.rejects_on(stores) is True
    review.set_rejects(stores, False)
    assert review.rejects_on(stores) is False


def test_agree_keeps_reject_and_records_example(stores):
    contacts.reject(stores, 'r1', 'заведение ниже планки', 'правила', pk='5')
    assert review.agree(stores, 'r1') is True
    assert contacts.is_known(stores, 'r1') is True
    row = stores.archive.execute("SELECT review FROM rejected WHERE nick='r1'").fetchone()
    assert row['review'] == 'верно'
    v = stores.main.execute('SELECT verdict, reason, by_human FROM verdicts').fetchone()
    assert v['verdict'] == 'reject' and v['by_human'] and 'заведение ниже планки' in v['reason']
    assert review.agree(stores, 'r1') is False


def test_overturn_revets_as_human_approved(stores):
    contacts.cache_profile(stores, '5', {'username': 'r1', 'full_name': 'Анна', 'biography': 'Шеф @x',
                                         'category': '', 'city_name': '', 'media_count': 30})
    contacts.reject(stores, 'r1', 'лента старше 183 дней', 'правила', pk='5', venue='X', source='following')
    assert review.overturn(stores, 'r1', 'сезонный ресторан, летом постит') is True
    assert stores.archive.execute("SELECT count(*) AS c FROM rejected").fetchone()['c'] == 0
    cand = stores.archive.execute("SELECT * FROM candidates WHERE nick='r1'").fetchone()
    assert cand['bio'] == 'Шеф @x' and cand['venue'] == 'X' and cand['source'] == 'following'
    task = wq.claim_next(stores.archive, ['vet'], 't')
    assert task['payload'] == {'nick': 'r1', 'pk': '5', 'approved': True}
    v = stores.main.execute('SELECT verdict, reason FROM verdicts').fetchone()
    assert v['verdict'] == 'accept'
    assert 'лента старше 183 дней' in v['reason'] and 'сезонный ресторан' in v['reason']
    assert review.overturn(stores, 'r1') is False


def test_grader_reject_keeps_bio_and_grid_for_review_card(stores):
    from leadgen.llm import grader
    row = {'nick': 'g1', 'pk': '9', 'name': 'Анна', 'venue': 'X', 'city': 'Москва', 'found_via': 'following',
           'bio': 'Шеф-повар X', 'grid_path': '/grids/g1.jpg'}
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('g1', 'сетка_готова')")
    grader.move_to_rejected(stores, row, 'кейтеринг', 'оценка')
    rej = stores.archive.execute("SELECT bio, grid_path FROM rejected WHERE nick='g1'").fetchone()
    assert (rej['bio'], rej['grid_path']) == ('Шеф-повар X', '/grids/g1.jpg')


# 30.09.2026, заказчик: «отправляй людей, которые связаны с ресторанами, HoReCa, а то футболисты…»
def test_worth_review_only_horeca_people_from_russia():
    ok = review.worth_review
    assert ok('должности нет в шапке профиля', 'правила',
              'Флагманский ресторан «Сыроварня» Аркадия Новикова, завтраки от шефа', 'Сыроварня', '')
    assert ok('не активен больше 183 дней', 'правила', 'Шеф-повар @fiorentina_nn', 'Иван Петров', '')
    assert ok('заведение ниже планки', 'правила', 'Шеф ресторана Pinsa, Moscow', 'Ivan', '')
    # пустая шапка — судить не по чему
    assert not ok('должности нет в шапке профиля', 'правила', '', 'vanilla_wasabi', '')
    # блогеры, рецепты, курсы
    assert not ok('должности нет в шапке профиля', 'правила', 'lifestyle | cooking & family blog  mom x4', 'Рузи', '')
    assert not ok('должности нет в шапке профиля', 'правила',
                  'Online cooking school, authentic French recipes', 'French Cook Academy', '')
    # не HoReCa вовсе
    assert not ok('должности нет в шапке профиля', 'правила', 'Нападающий ФК «Урал» ⚽️', 'Иван', '')
    assert not ok('должности нет в шапке профиля', 'правила', 'CEO ISweesh — исследование идей с ИИ', 'Руслан', '')
    # зарубежный шеф
    assert not ok('должности нет в шапке профиля', 'правила',
                  'Cheffe exécutive @fuga_family Ex-Ducasse Paris & Londres', 'Fanny', '')
    # аккаунт заведения — не человек
    assert not ok('аккаунт заведения, а не человека', 'правила', 'Ресторан, бронь', 'Ресторан X', '')
    assert not ok('Аккаунт ресторана', 'оценка_текст', 'Ресторан, бронь', 'Ресторан X', '')

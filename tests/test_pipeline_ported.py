"""Перенос тестов agent1_daemon (SQLite) на две базы и workers.run_once.

FakeClient — прежний, с теми же семантиками ответов; аккаунты 1 и 2
регистрируются фикстурой (RESTART IDENTITY даёт им id 1 и 2), заведение
Fiorentina — в основной базе, как в прежнем make_conn()."""
import pytest

from leadgen.access import accounts, health
from leadgen.qualify import handlers
from leadgen.ops import workers
from leadgen.core import queue as wq


@pytest.fixture(autouse=True)
def accs(stores):
    ids = [accounts.register_own(stores.archive, 'acc%d' % n, {'sessionid': 's'}) for n in (1, 2)]
    assert ids == [1, 2]
    stores.main.execute("INSERT INTO venues (name, city, region) "
                        "VALUES ('Fiorentina', 'Нижний Новгород', 'Поволжье')")
    return ids


def run_once(stores, clients):
    return workers.run_once(stores, accounts.Pool.from_transports(clients))


def contact(stores, nick):
    """Прежняя строка contacts: кандидат в работе — «на_проверке», отклонённый — «отклонён»."""
    row = stores.archive.execute('SELECT * FROM candidates WHERE nick = %s', (nick,)).fetchone()
    if row:
        return {'status': 'на_проверке', 'name': row['full_name'], 'role': row['role_hint'],
                'department': None}
    row = stores.archive.execute('SELECT * FROM rejected WHERE nick = %s', (nick,)).fetchone()
    if row:
        return {'status': 'отклонён', 'reason': row['reason']}
    return None


class FakeClient:
    kind = 'own'
    silent_empty_is_block = True

    def close(self):
        pass

    def __init__(self, users, profile, search_status='ok',
                 following_result=None, following_status='ok', profile_status='ok',
                 html_pk=None, html_status='ok'):
        self._html_pk = html_pk
        self._html_status = html_status
        self._users = users
        self._profile = profile
        self._search_status = search_status
        self._profile_status = profile_status
        self._following_result = following_result if following_result is not None else ([], None)
        self._following_status = following_status
        self.profile_calls = []
        self.html_calls = []
        self.search_calls = []
        self.following_calls = []

    def search(self, query):
        self.search_calls.append(query)
        return self._users, self._search_status

    def profile_info(self, pk):
        self.profile_calls.append(pk)
        return self._profile, self._profile_status

    def pk_by_nick(self, nick):
        self.html_calls.append(nick)
        return self._html_pk, self._html_status

    def profile_by_nick(self, nick, need_pk=False):
        self.web_calls = getattr(self, 'web_calls', []) + [nick]
        return None, 'soft_block'

    def following(self, pk, max_id=None):
        self.following_calls.append((pk, max_id))
        users, next_max_id = self._following_result
        return users, next_max_id, self._following_status


def test_run_once_writes_verified_account_to_venues(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    did_work = run_once(stores, {1: client})
    assert did_work is True
    row = stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert row['ig_account'] == 'fiorentina_nn'


def test_run_once_does_not_write_when_bridge_check_fails(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Москва', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    row = stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert row['ig_account'] is None


def test_run_once_returns_false_when_queue_empty(stores):
    assert run_once(stores, {1: FakeClient([], {})}) is False


def test_run_once_skips_profile_fetch_for_non_matching_candidate(stores):
    """Дешёвая проверка имени до users/{pk}/info/: на чужой ник запрос не тратится."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '99', 'username': 'barbershop_nn', 'full_name': 'Barber Shop'},
               {'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    assert client.profile_calls == ['1']
    row = stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert row['ig_account'] == 'fiorentina_nn'


def test_run_once_skips_candidate_without_pk(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    assert client.profile_calls == []
    row = stores.archive.execute("SELECT status FROM work_queue").fetchone()
    assert row['status'] == 'done'


def test_empty_search_requeues_and_marks_account_unhealthy(stores):
    """status='empty' — тихий отказ, а не успех: аккаунт в кулдаун, задача обратно в очередь."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(users=[], profile={}, search_status='empty')
    run_once(stores, {1: client})
    row = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    venue = stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert venue['ig_account'] is None
    hrow = stores.archive.execute('SELECT status, cooldown_until FROM endpoint_health '
                        'WHERE account_id = 1').fetchone()
    assert hrow['status'] == 'soft_block'
    assert hrow['cooldown_until'] is not None


def test_empty_search_completes_task_after_attempts_cap(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    stores.archive.execute('UPDATE work_queue SET attempts = 3')
    client = FakeClient(users=[], profile={}, search_status='empty')
    run_once(stores, {1: client})
    row = stores.archive.execute('SELECT status FROM work_queue').fetchone()
    assert row['status'] == 'done'


def test_run_once_does_not_claim_task_whose_endpoint_is_cooling_down(stores):
    """C2: задачу с остывающей ручкой не клеймим вовсе. Иначе она возвращается в
    очередь, через 0.5с берётся снова (та же priority, тот же id) и весь час
    куладауна крутится вхолостую, накручивая attempts — бюджет MAX_EMPTY_ATTEMPTS."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'throttled')
    client = FakeClient([], {})
    did_work = run_once(stores, {1: client})
    assert did_work is False
    assert client.search_calls == []
    row = stores.archive.execute('SELECT status, attempts, assigned_agent FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 0
    assert row['assigned_agent'] is None


def test_handle_profile_info_accepts_candidate_with_role(stores):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'chef_ivan', 'role': 'Шеф',
                                       'venue': 'Fiorentina', 'city': 'Нижний Новгород',
                                       'source': 'venue_header'})
    client = FakeClient(
        users=[{'pk': '10', 'username': 'chef_ivan'}],
        profile={'full_name': 'Иван Петров', 'biography': 'шеф-повар Fiorentina',
                 'category': '', 'city_name': ''})
    run_once(stores, {1: client})
    row = contact(stores, 'chef_ivan')
    assert row['status'] == 'на_проверке'
    assert row['name'] == 'Иван Петров'
    assert row['role'] == 'Шеф'


def test_handle_profile_info_rejects_candidate_without_role(stores):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'random_nick', 'venue': '', 'city': '',
                                       'source': 'following'})
    client = FakeClient(
        users=[{'pk': '11', 'username': 'random_nick'}],
        profile={'full_name': 'Просто Человек', 'biography': 'люблю путешествия',
                 'category': '', 'city_name': ''})
    run_once(stores, {1: client})
    row = contact(stores, 'random_nick')
    assert row['status'] == 'отклонён'
    assert row['reason'] == 'нет роли в шапке'


def test_handle_profile_info_skips_already_known_nick(stores):
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('known_nick', 'сдан')")
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'known_nick', 'venue': '', 'city': '',
                                       'source': 'following'})
    client = FakeClient(users=[], profile={})
    run_once(stores, {1: client})
    count = stores.main.execute(
        "SELECT COUNT(*) c FROM contacts WHERE nick = 'known_nick'").fetchone()['c']
    assert count == 1
    assert client.profile_calls == []
    assert client.search_calls == []


def test_handle_profile_info_uses_pk_from_payload_without_search(stores):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'has_pk_nick', 'pk': '77',
                                       'venue': '', 'city': '', 'source': 'following'})
    client = FakeClient(
        users=[], profile={'full_name': 'X', 'biography': 'шеф', 'category': '', 'city_name': ''})
    run_once(stores, {1: client})
    assert client.search_calls == []
    assert client.profile_calls == ['77']


def test_handle_profile_info_empty_search_requeues_not_success(stores):
    """status='empty' при резолве pk по нику — тихий отказ, как и в мосте
    (Task 9 части 1): не считается успехом, аккаунт уходит в куладаун."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'unresolved_nick', 'venue': '', 'city': '',
                                       'source': 'following'})
    client = FakeClient(users=[], profile={}, search_status='empty')
    run_once(stores, {1: client})
    row = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    hrow = stores.archive.execute(
        "SELECT status FROM endpoint_health WHERE account_id = 1 AND endpoint_class = 'search'"
    ).fetchone()
    assert hrow['status'] == 'soft_block'


def test_handle_bridge_success_enqueues_venue_header_task(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    task = wq.claim_next(stores.archive, ['venue_header'], 'test')
    assert task is not None
    assert task['payload'] == {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                'ig_account': 'fiorentina_nn', 'pk': '1'}


def test_handle_venue_header_enqueues_profile_info_for_bio_mentions(stores):
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue'})
    client = FakeClient(
        users=[{'pk': '5', 'username': 'my_venue'}],
        profile={'biography': 'Шеф @chef_ivan, су-шеф @sous_maria'})
    run_once(stores, {1: client})
    t1 = wq.claim_next(stores.archive, ['profile_info'], 'test')
    t2 = wq.claim_next(stores.archive, ['profile_info'], 'test')
    nicks = {t1['payload']['nick'], t2['payload']['nick']}
    assert nicks == {'chef_ivan', 'sous_maria'}


def test_handle_venue_header_does_not_enqueue_self_mention(stores):
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue'})
    client = FakeClient(
        users=[{'pk': '5', 'username': 'my_venue'}],
        profile={'biography': 'Заказ доставки @my_venue по ссылке в шапке'})
    run_once(stores, {1: client})
    assert wq.claim_next(stores.archive, ['profile_info'], 'test') is None


def test_handle_venue_header_empty_search_requeues_not_success(stores):
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue'})
    client = FakeClient(users=[], profile={}, search_status='empty')
    run_once(stores, {1: client})
    row = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    hrow = stores.archive.execute(
        "SELECT status FROM endpoint_health WHERE account_id = 1 AND endpoint_class = 'search'"
    ).fetchone()
    assert hrow['status'] == 'soft_block'


def test_handle_venue_header_skips_truncated_nick_from_role_substring(stores):
    """«Шеф @ivan_chef» даёт у BACK-регулярки ложную пару ('chef', 'ivan_') —
    обрезанный ник не должен становиться задачей: это запрос к Instagram впустую."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue'})
    client = FakeClient(
        users=[{'pk': '5', 'username': 'my_venue'}],
        profile={'biography': 'Шеф @ivan_chef'})
    run_once(stores, {1: client})
    nicks = []
    while True:
        t = wq.claim_next(stores.archive, ['profile_info'], 'test')
        if not t:
            break
        nicks.append(t['payload']['nick'])
    assert nicks == ['ivan_chef']


def test_handle_bridge_success_enqueues_following_task(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    task = wq.claim_next(stores.archive, ['following'], 'test')
    assert task is not None
    assert task['payload']['pk'] == '1'
    assert task['payload']['venue'] == 'Fiorentina'


def test_handle_following_enqueues_profile_info_for_person_like_candidates(stores):
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': 'Fiorentina', 'city': 'Нижний Новгород',
                                    'page': 1, 'max_pages': 3})
    client = FakeClient(
        users=[], profile={},
        following_result=([
            {'pk': '20', 'username': 'chef_zephyr', 'full_name': 'Пётр Петров'},
            {'pk': '21', 'username': 'fiorentina_official', 'full_name': 'Fiorentina'},
        ], None))
    run_once(stores, {1: client})
    task = wq.claim_next(stores.archive, ['profile_info'], 'test')
    assert task['payload']['nick'] == 'chef_zephyr'
    assert task['payload']['pk'] == '20'
    assert wq.claim_next(stores.archive, ['profile_info'], 'test') is None


def test_handle_following_paginates_when_more_pages_allowed(stores):
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': '', 'city': '',
                                    'page': 1, 'max_pages': 3})
    client = FakeClient(users=[], profile={}, following_result=([], 'cursor123'))
    run_once(stores, {1: client})
    task = wq.claim_next(stores.archive, ['following'], 'test')
    assert task['payload']['max_id'] == 'cursor123'
    assert task['payload']['page'] == 2


def test_handle_following_stops_pagination_at_max_pages(stores):
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': '', 'city': '',
                                    'page': 3, 'max_pages': 3})
    client = FakeClient(users=[], profile={}, following_result=([], 'cursor123'))
    run_once(stores, {1: client})
    assert wq.claim_next(stores.archive, ['following'], 'test') is None


# --- C1: классы здоровья — это ручки Instagram, а не типы задач ---

def test_search_block_from_bridge_also_blocks_venue_header_but_not_following(stores):
    """bridge, venue_header и profile_info ходят в один и тот же поиск. Блок,
    пойманный на bridge, обязан быть виден им всем — и не должен трогать
    обход подписок, у которого своя ручка."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(users=[], profile={}, search_status='soft_block')
    run_once(stores, {1: client})

    # venue_header и profile_info ходят в поиск, following — в свою ручку
    assert health.pick_account(stores.archive, handlers.ENDPOINT_SEARCH, [1]) is None
    assert health.pick_account(stores.archive, handlers.ENDPOINT_FOLLOWING, [1]) == 1


def test_bridge_records_health_for_search_and_profile_separately(stores):
    """Два сетевых вызова — две записи здоровья, каждая под своей ручкой."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    classes = {r['endpoint_class'] for r in stores.archive.execute(
        'SELECT endpoint_class FROM endpoint_health WHERE account_id = 1')}
    assert classes == {handlers.ENDPOINT_SEARCH, handlers.ENDPOINT_PROFILE}


# --- C2: остывающая ручка не блокирует конвейер ---

def test_following_still_runs_while_search_endpoint_is_cooling_down(stores):
    """Нет head-of-line blocking: задача с живой ручкой берётся, даже если
    впереди неё в очереди стоит задача с более высоким приоритетом, чья
    ручка в куладауне."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'},
               priority=10)
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': '', 'city': '', 'page': 1,
                                    'max_pages': 1}, priority=70)
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'soft_block')

    client = FakeClient(users=[], profile={}, following_result=([], None))
    assert run_once(stores, {1: client}) is True
    assert client.following_calls == [('5', None)]
    assert client.search_calls == []
    rows = {r['task_type']: r['status']
            for r in stores.archive.execute('SELECT task_type, status FROM work_queue')}
    assert rows['following'] == 'done'
    assert rows['bridge'] == 'pending'


def test_run_once_returns_false_when_every_endpoint_is_cooling_down(stores):
    """Живых типов нет — ведём себя как при пустой очереди (поток поспит 5с),
    а не возвращаем True и не крутим пустой цикл каждые 0.5с."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': '', 'city': '', 'page': 1})
    for endpoint in (handlers.ENDPOINT_SEARCH, handlers.ENDPOINT_PROFILE,
                     handlers.ENDPOINT_FOLLOWING):
        health.record_failure(stores.archive, 1, endpoint, 'soft_block')
    assert run_once(stores, {1: FakeClient([], {})}) is False
    rows = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchall()
    assert [r['status'] for r in rows] == ['pending', 'pending']
    assert [r['attempts'] for r in rows] == [0, 0]


# --- N1: куладаун ЛЮБОЙ ручки задачи останавливает запросы в неё ---

def test_profile_block_stops_further_profile_requests_for_task_with_pk(stores):
    """Ручка профиля — вторая в группах ручек задачи, но именно в ней делается работа.
    Если проверять только первую (поиск), задача каждые 0.5с уходит реальным
    запросом в ручку, которую система сама пометила заблокированной на час."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'blocked_nick', 'pk': '7', 'venue': '',
                                       'city': '', 'source': 'following'})
    client = FakeClient(users=[], profile={}, profile_status='soft_block')
    for _ in range(6):
        run_once(stores, {1: client})
    assert client.profile_calls == ['7']
    row = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1


def test_profile_block_stops_further_requests_for_bridge_task(stores):
    """У bridge заперт и поиск: после блока профиля задача не должна больше
    тратить ни одного запроса — ни в профиль, ни в поиск перед ним."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'},
        profile_status='soft_block')
    for _ in range(6):
        run_once(stores, {1: client})
    assert client.search_calls == ['Fiorentina Нижний Новгород']
    assert client.profile_calls == ['1']


# --- N2: с готовым pk поиск задачу не запирает ---

def test_task_with_pk_runs_while_search_is_blocked(stores):
    """После I2 venue_header от моста и profile_info от обхода подписок в поиск
    не ходят вовсе — блок поиска не должен держать их час в очереди."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue', 'pk': '5'})
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'soft_block')
    client = FakeClient(users=[], profile={'biography': 'Шеф @chef_ivan'})
    assert run_once(stores, {1: client}) is True
    assert client.search_calls == []
    assert client.profile_calls == ['5']


def test_task_without_pk_is_not_claimed_when_both_resolve_routes_are_blocked(stores):
    """Задачу без pk брать нельзя, только когда мертвы ОБА маршрута резолва.

    Раньше для этого хватало блока поиска, но теперь pk добывается ещё и
    вёрсткой профиля, так что при живой вёрстке такая задача — рабочая.
    Здесь блокируем оба маршрута и проверяем, что задача не клеймится и не
    мешает соседке с готовым pk (защита от head-of-line blocking)."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'guide_venue', 'city': '',
                                       'ig_account': 'guide_venue'}, priority=10)
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue', 'pk': '5'}, priority=55)
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'soft_block')
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_PROFILE_HTML, 'soft_block')
    client = FakeClient(users=[], profile={'biography': ''})
    assert run_once(stores, {1: client}) is True
    assert client.search_calls == []
    assert client.html_calls == []
    assert client.profile_calls == ['5']
    rows = {r['id']: r['status'] for r in stores.archive.execute('SELECT id, status FROM work_queue')}
    assert rows[1] == 'pending'  # задача без pk ждёт, пока оживёт любой маршрут
    assert rows[2] == 'done'


def test_task_without_pk_is_claimed_when_only_search_is_blocked(stores):
    """Обратная сторона: при мёртвом поиске, но живой вёрстке задача работает."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'guide_venue', 'city': '',
                                       'ig_account': 'guide_venue'})
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'soft_block')
    client = FakeClient(users=[], profile={'biography': ''}, html_pk='909')
    assert run_once(stores, {1: client}) is True
    assert client.search_calls == []
    assert client.html_calls == ['guide_venue']
    assert client.profile_calls == ['909']


# --- N3: непокрытые ветки run_once ---

def test_run_once_releases_task_without_spending_attempt_on_race(stores, monkeypatch):
    """Гонка: типы отобраны как живые, но между отбором и клеймом другой поток
    увёл ручку в блок. Сетевого запроса не было — попытку тратить нельзя."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    monkeypatch.setattr(workers, 'claimable_task_types',
                        lambda conn_, ids: (list(workers.TASK_TYPES), []))
    monkeypatch.setattr(workers, 'pick_account_for', lambda conn_, eps, ids: None)
    client = FakeClient([], {})
    assert run_once(stores, {1: client}) is True
    assert client.search_calls == []
    row = stores.archive.execute('SELECT status, attempts, assigned_agent FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 0
    assert row['assigned_agent'] is None


def test_run_once_picks_account_by_endpoint_not_by_task_type(stores):
    """Вторая половина C1: аккаунт выбирается по РУЧКЕ. Аккаунт 1 заблокирован на
    поиске, аккаунт 2 жив — задачу типа bridge должен взять аккаунт 2, хотя под
    именем 'bridge' в endpoint_health не записано ничего и он выглядел бы свободным."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'soft_block')
    blocked = FakeClient([], {})
    healthy = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    assert run_once(stores, {1: blocked, 2: healthy}) is True
    assert blocked.search_calls == []
    assert healthy.search_calls == ['Fiorentina Нижний Новгород']
    venue = stores.main.execute("SELECT ig_account FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert venue['ig_account'] == 'fiorentina_nn'


# --- C3: роль, названная заведением ---

def test_profile_info_accepts_role_from_payload_with_empty_own_bio(stores):
    """Заведение прямо написало «Шеф @nick» — самый надёжный сигнал в системе,
    даже если у самого человека в шапке роли нет."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'silent_nick', 'pk': '42', 'role': 'Шеф',
                                       'venue': 'Fiorentina', 'city': 'Нижний Новгород',
                                       'source': 'venue_header'})
    client = FakeClient(
        users=[], profile={'full_name': '', 'biography': '', 'category': '', 'city_name': ''})
    run_once(stores, {1: client})
    row = contact(stores, 'silent_nick')
    assert row['status'] == 'на_проверке'
    assert row['role'] == 'Шеф'


def test_profile_info_rejects_mention_without_role_and_empty_bio(stores):
    """'упомянут' ролью не является: это упоминание, рядом с которым роли не нашлось."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'just_mentioned', 'pk': '43', 'role': 'упомянут',
                                       'venue': '', 'city': '', 'source': 'venue_header'})
    client = FakeClient(
        users=[], profile={'full_name': '', 'biography': '', 'category': '', 'city_name': ''})
    run_once(stores, {1: client})
    row = contact(stores, 'just_mentioned')
    assert row['status'] == 'отклонён'
    assert row['reason'] == 'нет роли в шапке'


# --- I2: venue_header не повторяет запросы моста ---

def test_handle_venue_header_uses_pk_from_payload_without_search(stores):
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue', 'pk': '5'})
    client = FakeClient(users=[], profile={'biography': 'Шеф @chef_ivan'})
    run_once(stores, {1: client})
    assert client.search_calls == []
    assert client.profile_calls == ['5']
    task = wq.claim_next(stores.archive, ['profile_info'], 'test')
    assert task['payload']['nick'] == 'chef_ivan'


def test_handle_venue_header_falls_back_to_search_without_pk(stores):
    """Постановка из guide-источника: известен только ник — поиск обязателен."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'my_venue', 'city': '',
                                       'ig_account': 'my_venue'})
    client = FakeClient(users=[{'pk': '5', 'username': 'my_venue'}],
                        profile={'biography': 'Шеф @chef_ivan'})
    run_once(stores, {1: client})
    assert client.search_calls == ['my_venue']
    assert client.profile_calls == ['5']


def test_handle_venue_header_falls_back_to_html_when_search_result_has_no_pk(stores):
    """Выдача поиска без pk больше не тупик: pk добирается вёрсткой профиля.

    Раньше тут задача закрывалась ни с чем (проверялось только, что обработчик
    не падает KeyError). Теперь у резолва есть второй маршрут, и терять
    заведение из-за кривой выдачи поиска незачем."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'my_venue', 'city': '',
                                       'ig_account': 'my_venue'})
    client = FakeClient(users=[{'username': 'my_venue'}], profile={'biography': ''},
                        html_pk='555')
    run_once(stores, {1: client})
    assert client.html_calls == ['my_venue']
    assert client.profile_calls == ['555']
    assert stores.archive.execute("SELECT status FROM work_queue WHERE task_type = 'venue_header'").fetchone()['status'] == 'done'


def test_handle_profile_info_falls_back_to_html_when_search_result_has_no_pk(stores):
    """Парная к venue_header: выдача поиска без pk больше не тупик, pk добирается
    вёрсткой. Здесь вёрстка тоже ничего не даёт — задача возвращается в очередь,
    а не закрывается, и общий потолок попыток не даст ей крутиться вечно."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'no_pk_nick', 'venue': '', 'city': '',
                                       'source': 'following'})
    client = FakeClient(users=[{'username': 'no_pk_nick'}], profile={})
    run_once(stores, {1: client})
    assert client.html_calls == ['no_pk_nick']
    assert client.profile_calls == []
    assert stores.archive.execute('SELECT status FROM work_queue').fetchone()['status'] == 'pending'


def test_handle_profile_info_gives_up_when_neither_route_resolves_pk(stores):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'no_pk_nick', 'source': 'following'})
    stores.archive.execute('UPDATE work_queue SET attempts = %s', (handlers.MAX_EMPTY_ATTEMPTS,))
    client = FakeClient(users=[{'username': 'no_pk_nick'}], profile={})
    run_once(stores, {1: client})
    assert stores.archive.execute('SELECT status FROM work_queue').fetchone()['status'] == 'done'


# --- I3: упоминания без роли не превращаются в задачи ---

def test_handle_venue_header_skips_mentions_without_role(stores):
    """Доставка, фотограф, соседний проект: у пары роль 'упомянут', pk нет,
    а стоит каждая такая задача search + profile_info."""
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue', 'pk': '5'})
    client = FakeClient(
        users=[],
        profile={'biography': 'Шеф @chef_ivan. Доставка @yandex_eda, фото @foto_master'})
    run_once(stores, {1: client})
    nicks = []
    while True:
        t = wq.claim_next(stores.archive, ['profile_info'], 'test')
        if not t:
            break
        nicks.append(t['payload']['nick'])
    assert nicks == ['chef_ivan']


# --- I6: бюджет обхода подписок ---

def test_bridge_enqueues_following_with_budget_limited_max_pages(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '1', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    task = wq.claim_next(stores.archive, ['following'], 'test')
    assert task['payload']['max_pages'] == handlers.MAX_FOLLOWING_PAGES == 3


def test_handle_following_defaults_to_budget_when_payload_has_no_max_pages(stores):
    wq.enqueue(stores.archive, 'following', {'pk': '5', 'venue': '', 'city': '', 'page': 1})
    client = FakeClient(users=[], profile={}, following_result=([], 'cursor123'))
    run_once(stores, {1: client})
    # бюджет по умолчанию — MAX_FOLLOWING_PAGES (3): следующая страница по курсору
    nxt = wq.claim_next(stores.archive, ['following'], 'test')
    assert nxt['payload']['page'] == 2 and nxt['payload']['max_id'] == 'cursor123'


# --- I4: задача не остаётся в in_progress ---

def test_run_once_requeues_task_when_handler_raises(stores):
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})

    class BoomClient(FakeClient):
        def search(self, query):
            raise RuntimeError('обрыв соединения')

    try:
        run_once(stores, {1: BoomClient([], {})})
    except RuntimeError:
        pass
    else:
        raise AssertionError('исключение должно дойти до worker_loop')
    row = stores.archive.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1


# --- C4-гвард: демон не стартует на незасеянной базе ---
def test_venue_header_resolves_pk_via_html_when_search_is_blocked(stores):
    """Главный смысл обхода: поиск в блоке, а задача всё равно выполняется."""
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'throttled')
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Мой Ресторан', 'city': 'Томск',
                                       'ig_account': 'my_venue'})
    client = FakeClient(
        users=[], profile={'biography': 'Шеф @chef_ivan'},
        search_status='throttled', html_pk='777')
    assert run_once(stores, {1: client}) is True
    assert client.html_calls == ['my_venue']
    assert client.search_calls == []
    assert client.profile_calls == ['777']
    task = wq.claim_next(stores.archive, ['profile_info'], 'test')
    assert task['payload']['nick'] == 'chef_ivan'


def test_bridge_stores_pk_of_found_venue_account(stores):
    """pk не протухает: сохранённый однажды, он избавляет от поиска навсегда."""
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'})
    client = FakeClient(
        users=[{'pk': '42', 'username': 'fiorentina_nn', 'full_name': 'Fiorentina Restaurant'}],
        profile={'city_name': 'Нижний Новгород', 'address_street': '', 'category': 'Ресторан'})
    run_once(stores, {1: client})
    row = stores.main.execute("SELECT pk FROM venues WHERE name = 'Fiorentina'").fetchone()
    assert row['pk'] == '42'


def test_venue_header_uses_stored_pk_and_skips_resolution_entirely(stores):
    stores.main.execute("UPDATE venues SET ig_account='fiorentina_nn', pk='42' WHERE name='Fiorentina'")
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                       'ig_account': 'fiorentina_nn'})
    client = FakeClient(users=[], profile={'biography': 'Шеф @chef_ivan'}, search_status='throttled')
    run_once(stores, {1: client})
    assert client.search_calls == []
    assert client.html_calls == []
    assert client.profile_calls == ['42']


def test_profile_info_resolves_pk_via_html_and_never_touches_blocked_search(stores):
    """Регресс 22.09: допуск задачи считали по «поиск ИЛИ вёрстка», а обработчик
    ходил только в поиск. Задача бралась, била в 429, возвращалась и бралась
    снова — 6426 запросов в заблокированную ручку за считанные минуты."""
    health.record_failure(stores.archive, 1, handlers.ENDPOINT_SEARCH, 'throttled')
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'some_chef', 'role': 'Шеф',
                                       'venue': 'AQUATORIA', 'city': 'Владивосток',
                                       'source': 'venue_header'})
    client = FakeClient(users=[], profile={'full_name': 'Иван', 'biography': 'шеф-повар',
                                            'category': '', 'city_name': ''},
                        search_status='throttled', html_pk='321')
    run_once(stores, {1: client})
    assert client.search_calls == []      # в заблокированную ручку не ходим вовсе
    assert client.html_calls == ['some_chef']
    assert client.profile_calls == ['321']
    row = contact(stores, 'some_chef')
    assert row['status'] == 'на_проверке'


def test_task_is_failed_permanently_after_attempts_cap(stores):
    """Общая страховка от вечного спина: задача, которую не удаётся выполнить,
    обязана останавливаться навсегда, а не крутиться бесконечно."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'nobody', 'source': 'following'})
    stores.archive.execute('UPDATE work_queue SET attempts = %s', (workers.MAX_TASK_ATTEMPTS,))
    client = FakeClient(users=[], profile={})
    assert run_once(stores, {1: client}) is True
    row = stores.archive.execute('SELECT status FROM work_queue').fetchone()
    assert row['status'] == 'failed'
    assert client.search_calls == []
    assert client.html_calls == []
    assert client.profile_calls == []


def test_venue_stated_bar_role_is_not_accepted_blindly(stores):
    """Роль из шапки заведения принимается в обход has_role — значит барный
    фильтр надо применять и к ней. «Шеф-сомелье @ник» в шапке ресторана не
    должен давать принятого кандидата, пока барное направление на паузе."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'somm_guy', 'role': 'Шеф-сомелье',
                                       'venue': 'Ресторан', 'city': 'Омск',
                                       'source': 'venue_header'})
    client = FakeClient(users=[], profile={'full_name': 'Пётр', 'biography': 'вино и дегустации',
                                            'category': '', 'city_name': ''}, html_pk='11')
    run_once(stores, {1: client})
    row = contact(stores, 'somm_guy')
    assert row['status'] == 'отклонён'


def test_venue_stated_chef_role_is_still_accepted(stores):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'chef_guy', 'role': 'Бренд-шеф',
                                       'venue': 'Ресторан', 'city': 'Омск',
                                       'source': 'venue_header'})
    client = FakeClient(users=[], profile={'full_name': 'Иван', 'biography': 'люблю готовить',
                                            'category': '', 'city_name': ''}, html_pk='12')
    run_once(stores, {1: client})
    row = contact(stores, 'chef_guy')
    assert row['status'] == 'на_проверке'

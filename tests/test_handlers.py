import pytest

from leadgen.access import accounts, health
from leadgen.core import contacts, queue as wq
from leadgen.qualify import handlers
from leadgen.access.transport import CHECKPOINT, EMPTY, OK


class FakeTransport:
    """Подставной транспорт по контракту leadgen.access.transport."""
    kind = 'own'
    silent_empty_is_block = True

    def __init__(self, users=None, profile=None, search_status=OK, following_result=None,
                 following_status=OK, profile_status=OK, html_pk=None, html_status=OK):
        self._users = users or []
        self._profile = profile
        self._search_status = search_status
        self._profile_status = profile_status
        self._following_result = following_result if following_result is not None else ([], None)
        self._following_status = following_status
        self._html_pk = html_pk
        self._html_status = html_status
        self.search_calls, self.profile_calls, self.html_calls, self.following_calls = [], [], [], []

    def search(self, query):
        self.search_calls.append(query)
        if self._search_status != OK:
            return (None if self._search_status != EMPTY else []), self._search_status
        return self._users, OK

    def pk_by_nick(self, nick):
        self.html_calls.append(nick)
        return self._html_pk, self._html_status

    def profile_info(self, pk):
        self.profile_calls.append(pk)
        if self._profile_status != OK:
            return None, self._profile_status
        return self._profile, OK

    def following(self, pk, max_id=None):
        self.following_calls.append((pk, max_id))
        if self._following_status != OK:
            return None, None, self._following_status
        users, next_id = self._following_result
        return users, next_id, OK

    def media_info(self, media_id):
        return None, EMPTY

    web_profile = None
    web_status = 'soft_block'

    def profile_by_nick(self, nick, need_pk=False):
        self.web_calls = getattr(self, 'web_calls', []) + [nick]
        if self.web_status != OK:
            return None, self.web_status
        return self.web_profile, OK

    def embed_feed(self, nick):
        return None, 'soft_block'

    def close(self):
        pass


class FakeHiker(FakeTransport):
    kind = 'hiker'
    silent_empty_is_block = False


@pytest.fixture
def aid(stores):
    return accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})


@pytest.fixture
def venue(stores):
    stores.main.execute("INSERT INTO venues (name, city, region) "
                        "VALUES ('Fiorentina', 'Нижний Новгород', 'Поволжье')")
    return {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород'}


def claim(stores, task_type):
    return wq.claim_next(stores.archive, [task_type], 't', pk_only_types=[task_type])


def queued(stores, task_type=None):
    sql = 'SELECT task_type, payload, status, attempts FROM work_queue'
    rows = stores.archive.execute(sql + ' ORDER BY id').fetchall()
    return [r for r in rows if task_type is None or r['task_type'] == task_type]


VENUE_PROFILE = {'username': 'fiorentina_nn', 'full_name': 'Fiorentina',
                 'city_name': 'Нижний Новгород', 'category': 'Ресторан',
                 'biography': 'Итальянский ресторан. Шеф @ivan_chef'}


def test_bridge_writes_account_and_pk_to_main_and_enqueues_header_and_following(stores, aid, venue):
    wq.enqueue(stores.archive, 'bridge', venue)
    t = FakeTransport(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}],
                      profile=VENUE_PROFILE)
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    row = stores.main.execute('SELECT ig_account, pk FROM venues').fetchone()
    assert (row['ig_account'], row['pk']) == ('fiorentina_nn', '11')
    header = queued(stores, 'venue_header')[0]['payload']
    assert header['pk'] == '11'
    follow = queued(stores, 'following')[0]['payload']
    assert follow['max_pages'] == handlers.MAX_FOLLOWING_PAGES


def test_venue_header_candidate_becomes_candidate_with_agent_2(stores, aid, venue):
    wq.enqueue(stores.archive, 'profile_info',
               {'nick': 'ivan_chef', 'pk': '77', 'role': 'шеф', 'venue': 'Fiorentina',
                'city': 'Нижний Новгород', 'source': 'venue_header'})
    t = FakeTransport(profile={'username': 'ivan_chef', 'full_name': 'Иван', 'biography': ''})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    row = stores.archive.execute('SELECT * FROM candidates').fetchone()
    assert row['nick'] == 'ivan_chef'
    assert row['agent'] == 'Агент №2'
    assert row['status'] == 'обогащён'


def test_profile_without_role_goes_to_rejected_not_main(stores, aid, venue):
    wq.enqueue(stores.archive, 'profile_info',
               {'nick': 'random_guest', 'pk': '88', 'venue': 'Fiorentina',
                'city': 'Нижний Новгород', 'source': 'following'})
    t = FakeTransport(profile={'username': 'random_guest', 'full_name': 'Гость', 'biography': 'люблю кофе'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    row = stores.archive.execute('SELECT reason, stage FROM rejected').fetchone()
    assert (row['reason'], row['stage']) == ('нет роли в шапке', 'роль')
    assert stores.main.execute('SELECT COUNT(*) AS c FROM contacts').fetchone()['c'] == 0


def test_profile_info_caches_profile(stores, aid, venue):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan_chef', 'pk': '77', 'role': 'шеф',
                                                 'source': 'venue_header'})
    t = FakeTransport(profile={'username': 'ivan_chef', 'biography': 'Шеф-повар'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert stores.archive.execute("SELECT nick FROM profile_cache WHERE pk = '77'").fetchone()['nick'] == 'ivan_chef'


def test_known_by_pk_is_skipped_without_request(stores, aid, venue):
    stores.main.execute("INSERT INTO contacts (nick, pk, status) VALUES ('old_nick', '77', 'выгружен')")
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'new_nick', 'pk': '77', 'source': 'following'})
    t = FakeTransport(profile={'username': 'new_nick'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert t.profile_calls == []


def test_following_skips_known_by_pk(stores, aid, venue):
    stores.main.execute("INSERT INTO contacts (nick, pk, status) VALUES ('old_nick', '5', 'выгружен')")
    wq.enqueue(stores.archive, 'following', {'pk': '11', 'venue': 'Fiorentina',
                                              'city': 'Нижний Новгород', 'page': 1})
    t = FakeTransport(following_result=([{'pk': 5, 'username': 'renamed', 'full_name': 'Иван Петров'}], None))
    handlers.HANDLERS['following'](stores, t, aid, claim(stores, 'following'))
    assert queued(stores, 'profile_info') == []


def test_hiker_empty_search_is_not_a_block(stores, aid, venue):
    hiker = accounts.register_hiker(stores.archive, 'KEY')
    wq.enqueue(stores.archive, 'bridge', venue)
    t = FakeHiker(search_status=EMPTY)
    handlers.HANDLERS['bridge'](stores, t, hiker, claim(stores, 'bridge'))
    assert health.pick_account(stores.archive, handlers.ENDPOINT_SEARCH, [hiker]) == hiker
    assert queued(stores, 'bridge')[0]['status'] == 'done'


def test_own_empty_search_is_a_block(stores, aid, venue):
    wq.enqueue(stores.archive, 'bridge', venue)
    t = FakeTransport(search_status=EMPTY)
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    assert health.pick_account(stores.archive, handlers.ENDPOINT_SEARCH, [aid]) is None
    assert queued(stores, 'bridge')[0]['status'] == 'pending'


def test_checkpoint_on_profile_requeues_and_takes_account_down(stores, aid, venue):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan', 'pk': '1', 'source': 'following'})
    t = FakeTransport(profile_status=CHECKPOINT)
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert queued(stores, 'profile_info')[0]['status'] == 'pending'
    status = stores.archive.execute('SELECT status FROM accounts WHERE id = %s', (aid,)).fetchone()
    assert status['status'] == 'нужен_вход'


def test_profile_api_blocked_falls_back_to_profile_page(stores, aid, venue):
    """Замер 28.09: users/info в 429 на всём IP, страница профиля — 200."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan_chef', 'pk': '77', 'source': 'following'})
    t = FakeTransport(profile_status='throttled')
    t.web_status = OK
    t.web_profile = {'pk': '77', 'username': 'ivan_chef', 'full_name': 'Иван Петров',
                     'biography': 'Шеф-повар @fiorentina_nn', 'media_count': 40, 'category': '',
                     'city_name': '', 'is_private': None}
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert stores.archive.execute("SELECT nick FROM candidates").fetchone()['nick'] == 'ivan_chef'
    assert health.pick_account(stores.archive, handlers.ENDPOINT_PROFILE, [aid]) is None
    assert health.pick_account(stores.archive, handlers.ENDPOINT_PROFILE_HTML, [aid]) == aid


def test_checkpoint_on_api_does_not_try_web_route(stores, aid, venue):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ivan_chef', 'pk': '77', 'source': 'following'})
    t = FakeTransport(profile_status=CHECKPOINT)
    t.web_status = OK
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert getattr(t, 'web_calls', []) == []


def test_venue_header_reads_bio_from_page_when_api_blocked(stores, aid, venue):
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                                 'ig_account': 'fiorentina_nn', 'pk': '11'})
    t = FakeTransport(profile_status='throttled')
    t.web_status = OK
    t.web_profile = {'pk': '11', 'username': 'fiorentina_nn', 'biography': 'Шеф @ivan_chef'}
    handlers.HANDLERS['venue_header'](stores, t, aid, claim(stores, 'venue_header'))
    assert queued(stores, 'profile_info')[0]['payload']['nick'] == 'ivan_chef'


def test_bridge_does_not_repeat_following_walk_for_known_account(stores, aid, venue):
    """№6: дубль заведения в реестре не должен стоить второго обхода подписок (~450 запросов)."""
    stores.main.execute("INSERT INTO venues (name, city, ig_account, pk) VALUES ('Fiorentina NN', 'Нижний Новгород', 'fiorentina_nn', '11')")
    wq.enqueue(stores.archive, 'bridge', venue)
    t = FakeTransport(users=[{'pk': 11, 'username': 'fiorentina_nn', 'full_name': 'Fiorentina'}], profile=VENUE_PROFILE)
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    assert queued(stores, 'following') == [] and queued(stores, 'venue_header') == []


def test_venue_header_queues_following_walk_once(stores, aid, venue, tmp_path, monkeypatch):
    """Заведение с известным аккаунтом раньше никогда не обходилось по подпискам —
    а это главный канал; повторно и по журналу прежних обходов — не обходим."""
    ledger = tmp_path / 'walked.txt'
    ledger.write_text('already_walked\n')
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(ledger))
    for nick, pk in (('fiorentina_nn', '11'), ('already_walked', '12')):
        wq.enqueue(stores.archive, 'venue_header', {'venue_name': nick, 'city': 'Нижний Новгород',
                                                     'ig_account': nick, 'pk': pk})
        t = FakeTransport(profile={'username': nick, 'biography': 'Ресторан'})
        handlers.HANDLERS['venue_header'](stores, t, aid, claim(stores, 'venue_header'))
    walks = [r['payload'] for r in queued(stores, 'following')]
    assert [w['pk'] for w in walks] == ['11']
    assert walks[0]['max_pages'] == handlers.MAX_FOLLOWING_PAGES
    wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                                 'ig_account': 'fiorentina_nn', 'pk': '11'})
    handlers.HANDLERS['venue_header'](stores, FakeTransport(profile={'username': 'fiorentina_nn', 'biography': ''}),
                                      aid, claim(stores, 'venue_header'))
    assert len(queued(stores, 'following')) == 1


def test_rewalk_header_walks_three_pages_despite_ledger_and_old_walk(stores, aid, venue, tmp_path, monkeypatch):
    """Повторный обход (30.09.2026): сентябрьский журнал и прежний обход не мешают,
    глубина — REWALK_PAGES; одна метка повтора — один обход на заведение."""
    ledger = tmp_path / 'walked.txt'
    ledger.write_text('fiorentina_nn\n')
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(ledger))
    wq.enqueue(stores.archive, 'following', {'pk': '11', 'page': 1, 'max_pages': 1})
    for _ in range(2):
        wq.enqueue(stores.archive, 'venue_header', {'venue_name': 'Fiorentina', 'city': 'Нижний Новгород',
                                                     'ig_account': 'fiorentina_nn', 'pk': '11',
                                                     'rewalk': '2026-09'})
        handlers.HANDLERS['venue_header'](stores, FakeTransport(profile={'username': 'fiorentina_nn',
                                                                         'biography': 'Ресторан'}),
                                          aid, claim(stores, 'venue_header'))
    walks = [r['payload'] for r in queued(stores, 'following') if r['payload'].get('rewalk')]
    assert len(walks) == 1
    assert walks[0]['max_pages'] == handlers.REWALK_PAGES == 3 and walks[0]['rewalk'] == '2026-09'


def test_venue_found_by_search_engine_is_walked_not_just_rejected(stores, aid, tmp_path, monkeypatch):
    """Поисковик приносит и заведения вне реестра 2ГИС — их подписки тоже канал лидов."""
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'dune.restaurant', 'pk': '31', 'city': 'Казань',
                                                'source': 'поисковик'})
    t = FakeTransport(profile={'username': 'dune.restaurant', 'full_name': 'AGA', 'category': 'Ресторан',
                               'biography': 'Ресторан авторской кухни'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    header = queued(stores, 'venue_header')[0]['payload']
    assert header == {'venue_name': 'AGA', 'city': 'Казань', 'ig_account': 'dune.restaurant', 'pk': '31'}
    assert contacts.is_known(stores, 'dune.restaurant') is True


def test_person_without_role_from_following_is_not_walked(stores, aid):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'guest1', 'pk': '32', 'source': 'following'})
    t = FakeTransport(profile={'username': 'guest1', 'full_name': 'Гость', 'category': 'Ресторан',
                               'biography': ''})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert queued(stores, 'venue_header') == []


def test_award_winner_venue_is_walked(stores, aid, tmp_path, monkeypatch):
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ember.rest', 'pk': '41', 'source': 'премия'})
    t = FakeTransport(profile={'username': 'ember.rest', 'full_name': 'Agutin', 'category': 'Ресторан',
                               'biography': 'Ресторан'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert queued(stores, 'venue_header')[0]['payload']['ig_account'] == 'ember.rest'


def test_venue_naming_its_chef_is_walked_not_taken_as_person(stores, aid, tmp_path, monkeypatch):
    """30.09.2026: @vale1.rest — «Шеф-повар Алексей Волков @ember1 📞… ⏰ Пн-вс 12:00-23:00»
    прошёл как человек по слову «Шеф-повар»."""
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'vale1.rest', 'pk': '46', 'city': 'Чита',
                                                'source': 'поисковик'})
    t = FakeTransport(profile={'username': 'vale1.rest', 'full_name': 'Муссон', 'category': '',
                               'biography': 'Шеф-повар Алексей Волков @ember1\n📞+7 (495) 646-16-05\n⏰ Пн-вс 12:00-23:00'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert stores.archive.execute("SELECT count(*) AS c FROM candidates").fetchone()['c'] == 0
    header = queued(stores, 'venue_header')[0]['payload']
    assert header['ig_account'] == 'vale1.rest'


def test_chef_naming_his_restaurant_stays_a_person(stores, aid):
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'ember1', 'pk': '47', 'source': 'following'})
    t = FakeTransport(profile={'username': 'ember1', 'full_name': 'Алексей Волков', 'category': '',
                               'biography': 'Шеф-повар @vale1.rest'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert stores.archive.execute("SELECT nick FROM candidates").fetchone()['nick'] == 'ember1'


# 30.09.2026: первая версия правила сняла 28 настоящих шефов («Chef @restaurant.tarragon1»).
# Заведение — только если шапка говорит о месте (часы, «с 2009 года», «семейный ресторан»).
VENUES_NAMING_STAFF = {
    'vale1.rest': ('Муссон', 'Шеф-повар Алексей Волков @ember1\n📞+7 (495) 646-16-05\n⏰ Пн-вс 12:00-23:00'),
    'mint_nutmeg': ('Don Bazilio', 'Говорят, тут лучшая пицца во всей южной провинции 🍕\nBuon appetito!\n'
                                   'ПН-ВС 10:00-22:00\nБренд-шеф @alder2_birch2\nДоставка на дом ⬇️'),
    'nutmeg1': ('Osteria Gini', 'С 2009 года\nСемейный ресторан\nChef & Co-owner @fjord\n'
                                    'Настоящая итальянская Osteria в Москве\n+7 900 000-00-01'),
}
CHEFS_NAMING_VENUE = {
    'iris': ('Alex', 'Chef @restaurant.tarragon1'),
    'juniper1_kestrel1': ('Леонид Фролов', 'Шеф @gastrobar_sage'),
    'lotus': ('Dmitriy Sidorov', 'Food Travel Family\nFounder @zephyr1\nPR и сотрудничество Катя +79000000002'),
    'dill1_fennel1': ('Михаил Нестеров', '• Шеф-повар ресторана @thyme.ekb • Более 14 лет руководил кухней @maple_butchery'),
    'quince': ('Илья Сафин', 'Вице-президент Гильдии поваров региона\nРазработка меню и консалтинг\n'
                                  'Преподаю в @hazel_school\nШеф-повар @vanilla1'),
    'rose1': ('Максим Гусев', 'Ресторатор, Шеф-повар и иногда актер. Создатель: @fjord2 @umber1.gastrobistro'),
    'sage1': ('Павел Жуков', 'Совладелец и бренд-шеф ресторана @vale2\nПопулярно о мясе: цех, обучение, dry aged\n'
                                         'Контакт: Александра @syn_sasha_pr'),
    'chef_yarrow_': ('Local', '🔪 Head chef @river2.rest 🌱 Local and Modern Russian cuisine 🦄Консалтинг и обучение'),
    'sable1.tundra1': ('Иван Мостовой', 'Профессионально люблю еду! chef @cedar2 бистро Проспект Мира, 19 ст1 Красноярск'),
}
VENUES_NAMING_STAFF['ginger'] = (
    'Cihan', '🇹🇷Турецкий ресторан Аркадия Новикова @novikovgroup 🔪Шеф @fennel.chef 📍Комсомольский пр,21')


def test_venue_naming_staff_only_for_real_venues():
    for nick, (name, bio) in VENUES_NAMING_STAFF.items():
        assert handlers._venue_naming_staff(nick, {'full_name': name, 'biography': bio, 'category': ''}), nick
    for nick, (name, bio) in CHEFS_NAMING_VENUE.items():
        assert not handlers._venue_naming_staff(nick, {'full_name': name, 'biography': bio, 'category': ''}), nick


def test_consultant_without_chef_words_becomes_candidate(stores, aid):
    from leadgen.qualify import roles
    roles.ensure_defaults(stores.main)
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'menu_launch', 'pk': '51', 'source': 'following'})
    t = FakeTransport(profile={'username': 'menu_launch', 'full_name': 'Ольга Смирнова', 'category': '',
                               'biography': 'Запуск ресторанов под ключ. Разработка меню. Новосибирск'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert stores.archive.execute("SELECT nick FROM candidates").fetchone()['nick'] == 'menu_launch'


def test_foreign_place_from_tags_is_not_walked(stores, aid):
    """01.10.2026: отметки в постах приносят шампанские дома и зарубежные курорты —
    их подписки полны иностранцев."""
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'champagne_laculle', 'pk': '61', 'source': 'отметки'})
    t = FakeTransport(profile={'username': 'champagne_laculle', 'full_name': 'Champagne Laculle', 'category': 'Ресторан',
                               'biography': 'Maison de Champagne since 1890. Restaurant & tasting room'})
    handlers.HANDLERS['profile_info'](stores, t, aid, claim(stores, 'profile_info'))
    assert queued(stores, 'venue_header') == []


def test_premium_venue_is_walked_deeper(stores, aid, tmp_path, monkeypatch):
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES "
                        "('Prem', 'Москва', 'PREMIUM', 'prem_rest'), ('Mid', 'Москва', 'UPPER-MIDDLE', 'mid_rest')")
    assert handlers._queue_walk(stores, '1', 'prem_rest', 'Prem', 'Москва')
    assert handlers._queue_walk(stores, '2', 'mid_rest', 'Mid', 'Москва')
    assert handlers._queue_walk(stores, '3', 'other_rest', 'Other', 'Москва', rewalk='2026-10')
    pages = {r['payload']['pk']: r['payload']['max_pages'] for r in queued(stores, 'following')}
    assert pages == {'1': handlers.PREMIUM_PAGES, '2': handlers.MAX_FOLLOWING_PAGES, '3': handlers.REWALK_PAGES}
    assert handlers.PREMIUM_PAGES == 5

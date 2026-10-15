import io
import os
import time

import pytest
from PIL import Image

from leadgen.access import accounts, health
from leadgen.core import contacts, queue as wq
from leadgen.qualify import geo, grid, handlers, review, roles, vetting
from leadgen.ops import workers
from leadgen.access.transport import EMPTY, OK, THROTTLED
from tests.test_handlers import FakeTransport

NOW = int(time.time())


def jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (320, 320), (180, 60, 60)).save(buf, 'JPEG')
    return buf.getvalue()


class FeedTransport(FakeTransport):
    def __init__(self, feed=None, feed_status=OK, story=None, profiles=None, download_status=OK, **kw):
        super().__init__(**kw)
        self._download_status = download_status
        self._feed, self._feed_status, self._story = feed, feed_status, story
        self._profiles = profiles or {}
        self.feed_calls, self.story_calls, self.downloads = [], [], []

    def user_feed(self, pk):
        self.feed_calls.append(pk)
        return (self._feed if self._feed_status == OK else None), self._feed_status

    def highlights_latest(self, pk):
        self.story_calls.append(pk)
        return (self._story, OK) if self._story else (None, EMPTY)

    embed = None
    embed_status = 'soft_block'

    def embed_feed(self, nick):
        self.embed_calls = getattr(self, 'embed_calls', []) + [nick]
        if self.embed_status != OK:
            return None, self.embed_status
        return self.embed, OK

    def profile_by_nick(self, nick, need_pk=False):
        return None, 'soft_block'

    def download(self, url):
        self.downloads.append(url)
        if self._download_status != OK:
            return None, self._download_status
        return jpeg(), OK

    def profile_info(self, pk):
        self.profile_calls.append(pk)
        return self._profiles.get(str(pk), self._profile), OK


def feed(days_ago=5, n=12, loc=None):
    return [{'taken_at': NOW - days_ago * 86400 - i, 'code': 'C%d' % i, 'caption': 'Подача %d' % i,
             'thumb': 'https://cdn/%d.jpg' % i, 'loc': loc} for i in range(n)]


KRAS = {'lat': 56.01, 'lng': 92.85, 'name': 'Красноярск'}
ITALY = {'lat': 43, 'lng': 12, 'name': 'Italy'}


@pytest.fixture
def env(stores, tmp_path, monkeypatch):
    monkeypatch.setattr(grid, 'GRIDS_DIR', str(tmp_path))
    roles.ensure_defaults(stores.main)
    geo.upsert_city(stores.main, 'Нижний Новгород', 'Москва', 'МСК')
    geo.upsert_city(stores.main, 'Москва', 'Москва', 'МСК')
    geo.upsert_city(stores.main, 'Екатеринбург', 'Восток', 'НСК')
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES "
                        "('Fiorentina', 'Нижний Новгород', 'PREMIUM', 'fiorentina_nn'), "
                        "('Кафе Чайковский', 'Москва', 'PREMIUM', 'cafe_tchaikovsky'), "
                        "('Dorian Gray', 'Екатеринбург', 'PREMIUM', 'doriangray_ekb')")
    aid = accounts.register_own(stores.archive, 'acc1', {'sessionid': 's'})
    return stores, aid


def candidate(stores, nick='ivan_chef', pk='77', bio='Шеф-повар @fiorentina_nn', venue='',
              city='', source='following', posts=40, role=''):
    profile = {'username': nick, 'full_name': 'Иван Петров', 'biography': bio,
               'category': '', 'city_name': '', 'media_count': posts, 'is_private': False}
    contacts.cache_profile(stores, pk, profile)
    contacts.add_candidate(stores, nick, pk, profile,
                           {'venue': venue, 'city': city, 'source': source, 'role': role}, 'Агент №2')
    wq.enqueue(stores.archive, 'vet', {'nick': nick, 'pk': pk}, priority=40)
    return wq.claim_next(stores.archive, ['vet'], 't')


def main_row(stores, nick):
    return stores.main.execute('SELECT * FROM contacts WHERE nick = %s', (nick,)).fetchone()


def test_good_chef_is_promoted_with_grid_and_fields(env):
    stores, aid = env
    task = candidate(stores)
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'сетка_готова'
    assert (row['venue'], row['level'], row['city'], row['department']) == \
        ('Fiorentina', 'PREMIUM', 'Нижний Новгород', 'МСК')
    assert (row['role_code'], row['role'], row['crm_position']) == ('chef', 'Шеф Повар', 'Шеф')
    assert row['name'] == 'Иван Петров'
    assert row['agent'] == 'Агент №2'
    assert row['needs_strong_profile'] is False
    assert len(row['captions']) == 12
    assert Image.open(row['grid_path']).size == (960, 1280)
    assert stores.archive.execute('SELECT count(*) AS c FROM candidates').fetchone()['c'] == 0
    assert wq.claim_next(stores.archive, ['vet'], 't') is None


def test_department_comes_from_venue_in_bio_not_from_source_venue(env):
    """Кафе Чайковский: найден через подписки екатеринбургского Dorian Gray,
    а работает в московском заведении из своей шапки."""
    stores, aid = env
    task = candidate(stores, bio='Шеф @cafe_tchaikovsky', venue='Dorian Gray', city='Екатеринбург')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    row = main_row(stores, 'ivan_chef')
    assert (row['venue'], row['city'], row['department']) == ('Кафе Чайковский', 'Москва', 'МСК')


def test_source_venue_counts_only_when_it_named_the_person(env):
    stores, aid = env
    task = candidate(stores, bio='Шеф-повар', venue='Dorian Gray', city='Екатеринбург',
                     source='venue_header')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, 'ivan_chef')['venue'] == 'Dorian Gray'


def test_following_source_venue_is_not_evidence(env):
    """Заведение-источник обхода подписок не доказательство. Заведения и города
    нет — не вечный отказ, а к человеку (правила: «на проверку»)."""
    stores, aid = env
    task = candidate(stores, bio='Шеф-повар', venue='Dorian Gray', city='Екатеринбург',
                     source='following')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['venue'] == '' and row['department'] is None
    assert row['needs_strong_profile'] is True
    assert row['cap_reason'] == 'город не определён'


def test_unknown_mention_becomes_registry_venue_without_level(env):
    stores, aid = env
    task = candidate(stores, bio='Шеф @new_place_nn')
    t = FeedTransport(feed=feed(), html_pk='900', profiles={
        '900': {'username': 'new_place_nn', 'full_name': 'New Place', 'category': 'Ресторан',
                'city_name': 'Нижний Новгород', 'biography': 'ресторан, бронь'}})
    vetting.handle_vet(stores, t, aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['venue'] == 'New Place' and row['needs_strong_profile'] is True
    assert row['department'] == 'МСК'
    reg = stores.main.execute("SELECT city, level, pk FROM venues WHERE ig_account = 'new_place_nn'").fetchone()
    assert (reg['city'], reg['level'], reg['pk']) == ('Нижний Новгород', None, '900')


def test_person_mention_is_not_a_venue(env):
    stores, aid = env
    task = candidate(stores, bio='Шеф @fiorentina_nn, жена @maria_p')
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, task)
    assert main_row(stores, 'ivan_chef')['venue'] == 'Fiorentina'


def test_stale_feed_rescued_by_fresh_story(env):
    stores, aid = env
    task = candidate(stores)
    t = FeedTransport(feed=feed(days_ago=300), story=NOW - 10 * 86400)
    vetting.handle_vet(stores, t, aid, task)
    assert main_row(stores, 'ivan_chef')['status'] == 'сетка_готова'
    assert t.story_calls == ['77']


def test_fresh_feed_does_not_spend_story_request(env):
    stores, aid = env
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, candidate(stores))
    assert t.story_calls == []


def test_stale_feed_and_no_story_rejected(env):
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed=feed(days_ago=300)), aid, candidate(stores))
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'не активен больше 183 дней'
    assert main_row(stores, 'ivan_chef') is None


def test_feed_throttled_requeues_and_cools_down(env):
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed_status=THROTTLED), aid, candidate(stores))
    row = stores.archive.execute("SELECT status, attempts FROM work_queue WHERE task_type = 'vet'").fetchone()
    assert (row['status'], row['attempts']) == ('pending', 1)
    assert health.pick_account(stores.archive, vetting.ENDPOINT_FEED, [aid]) is None
    assert stores.archive.execute('SELECT count(*) AS c FROM candidates').fetchone()['c'] == 1


def test_candidate_gone_closes_task(env):
    stores, aid = env
    task = candidate(stores)
    contacts.drop_candidate(stores, 'ivan_chef')
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, task)
    assert t.feed_calls == []


def test_profile_info_enqueues_vet_for_accepted_candidate(env):
    stores, aid = env
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'petr_chef', 'pk': '55', 'source': 'following'})
    task = wq.claim_next(stores.archive, ['profile_info'], 't', pk_only_types=['profile_info'])
    handlers.HANDLERS['profile_info'](stores, FakeTransport(profile={'username': 'petr_chef',
                                                                     'biography': 'Шеф-повар'}),
                                      aid, task)
    vet = wq.claim_next(stores.archive, ['vet'], 't')
    assert vet['payload'] == {'nick': 'petr_chef', 'pk': '55'}


def test_workers_route_vet(env):
    assert 'vet' in workers.TASK_TYPES
    assert workers.task_endpoints('vet', has_pk=True) == \
        (('feed', 'embed'), ('profile_info', 'profile_html'), handlers.RESOLVE_ENDPOINTS)
    assert workers.HANDLERS['vet'] is vetting.handle_vet


def pending_vet(stores):
    return stores.archive.execute(
        "SELECT status, attempts FROM work_queue WHERE task_type = 'vet'").fetchone()


def cand_count(stores):
    return stores.archive.execute('SELECT count(*) AS c FROM candidates').fetchone()['c']


def test_download_failure_requeues_instead_of_grey_grid(env):
    """I2: серая сетка из-за сбоя CDN даёт уверенный отказ Sonnet — не допускаем."""
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed=feed(), download_status='soft_block'), aid,
                       candidate(stores))
    assert main_row(stores, 'ivan_chef') is None
    assert (pending_vet(stores)['status'], pending_vet(stores)['attempts']) == ('pending', 1)
    assert cand_count(stores) == 1


def test_sparse_feed_is_promoted_with_tile_count(env):
    stores, aid = env
    items = feed()
    for it in items[2:]:
        it['thumb'] = None
    vetting.handle_vet(stores, FeedTransport(feed=items), aid, candidate(stores))
    assert main_row(stores, 'ivan_chef')['grid_tiles'] == 2


def test_stale_posts_with_story_check_unavailable_requeue(env):
    """I3: истории считаются наравне с постами — без их проверки не отклоняем."""
    stores, aid = env
    health.record_failure(stores.archive, aid, vetting.ENDPOINT_HIGHLIGHTS, 'throttled')
    vetting.handle_vet(stores, FeedTransport(feed=feed(days_ago=300)), aid, candidate(stores))
    assert pending_vet(stores)['status'] == 'pending'
    assert stores.archive.execute('SELECT count(*) AS c FROM rejected').fetchone()['c'] == 0


def test_empty_feed_with_posts_is_a_silent_block(env):
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed=[], feed_status=EMPTY), aid, candidate(stores, posts=40))
    assert pending_vet(stores)['status'] == 'pending'
    assert health.pick_account(stores.archive, vetting.ENDPOINT_FEED, [aid]) is None
    assert cand_count(stores) == 1


def test_blocked_mention_lookup_requeues(env):
    """I4: блок поиска — не повод потерять заведение и отклонить по планке."""
    stores, aid = env
    for ep in (handlers.ENDPOINT_SEARCH, handlers.ENDPOINT_PROFILE_HTML):
        health.record_failure(stores.archive, aid, ep, 'throttled')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, candidate(stores, bio='Шеф @unknown_place'))
    assert main_row(stores, 'ivan_chef') is None
    assert pending_vet(stores)['status'] == 'pending'


def test_free_rules_reject_before_any_request(env):
    """I5: бесплатные правила раньше ленты."""
    stores, aid = env
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф шашлычной @mangal_nn'))
    assert t.feed_calls == []
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'формат из стоп-листа'


def test_cached_person_mention_costs_no_requests(env):
    stores, aid = env
    contacts.cache_profile(stores, '555', {'username': 'maria_p', 'full_name': 'Мария Петрова',
                                           'biography': 'люблю котиков', 'category': ''})
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф-повар, жена @maria_p'))
    assert t.search_calls == [] and t.html_calls == [] and t.profile_calls == []


def test_brand_mention_is_not_a_venue(env):
    """I10: бренд техники с «ресторан» в шапке — не место работы."""
    stores, aid = env
    t = FeedTransport(feed=feed(), html_pk='900', profiles={
        '900': {'username': 'robot_brand', 'full_name': 'Robot Brand', 'category': 'Товар/услуга',
                'city_name': 'Москва', 'biography': 'техника для ресторанов, food service'}})
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф-повар, амбассадор @robot_brand'))
    assert main_row(stores, 'ivan_chef')['venue'] == ''
    assert stores.main.execute("SELECT count(*) AS c FROM venues WHERE ig_account = 'robot_brand'").fetchone()['c'] == 0


def test_latin_city_of_new_venue_gets_department(env):
    stores, aid = env
    t = FeedTransport(feed=feed(), html_pk='901', profiles={
        '901': {'username': 'new_msk', 'full_name': 'New Msk', 'category': 'Ресторан',
                'city_name': 'Moscow', 'biography': 'ресторан'}})
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф @new_msk'))
    assert main_row(stores, 'ivan_chef')['department'] == 'МСК'


def test_role_named_only_by_venue_goes_to_human(env):
    """I11c: «Шеф @ник» в шапке заведения, а в своей шапке должности нет."""
    stores, aid = env
    task = candidate(stores, bio='Готовлю с любовью', venue='Fiorentina', city='Нижний Новгород',
                     source='venue_header', role='Шеф')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['role_code'] == 'chef'
    assert row['cap_reason'] == 'должность названа заведением, в шапке её нет'


def test_disabled_role_rejected_with_retryable_stage(env):
    """I11d: выключенная должность — отдельная стадия, её можно вернуть при включении."""
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid,
                       candidate(stores, bio='Управляющий ресторана @fiorentina_nn'))
    rej = stores.archive.execute("SELECT reason, stage FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert (rej['stage'], rej['reason']) == ('роль_выкл', 'должность выключена: Управляющий / директор')


def test_enqueue_missing_vets_for_stuck_candidates(env):
    """I7: кандидаты без задачи vet (из плана 1 или после сбоя) не застревают."""
    stores, aid = env
    contacts.add_candidate(stores, 'old_one', '9', {'full_name': 'X'}, {}, 'Агент №2')
    assert vetting.enqueue_missing(stores) == 1
    assert vetting.enqueue_missing(stores) == 0


def test_feed_api_blocked_falls_back_to_embed(env):
    stores, aid = env
    t = FeedTransport(feed_status=THROTTLED)
    t.embed_status = OK
    t.embed = {'items': feed(n=6), 'media_count': 40, 'has_public_story': False,
               'full_name': 'Иван Петров', 'pk': '77'}
    vetting.handle_vet(stores, t, aid, candidate(stores))
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'сетка_готова' and row['grid_tiles'] == 6
    assert health.pick_account(stores.archive, vetting.ENDPOINT_FEED, [aid]) is None


def test_public_story_in_embed_counts_as_fresh(env):
    """Истории наравне с постами: флаг has_public_story из embed."""
    stores, aid = env
    t = FeedTransport(feed_status=THROTTLED)
    t.embed_status = OK
    t.embed = {'items': feed(days_ago=300, n=6), 'media_count': 40, 'has_public_story': True,
               'full_name': '', 'pk': '77'}
    vetting.handle_vet(stores, t, aid, candidate(stores))
    assert main_row(stores, 'ivan_chef')['status'] == 'сетка_готова'
    assert t.story_calls == []


def test_both_feed_routes_blocked_requeues(env):
    stores, aid = env
    vetting.handle_vet(stores, FeedTransport(feed_status=THROTTLED), aid, candidate(stores))
    assert pending_vet(stores)['status'] == 'pending'
    assert cand_count(stores) == 1


def test_city_taken_from_bio_when_page_gives_none(env):
    """Страница профиля не отдаёт city_name: город ищем в шапке заведения, потом кандидата."""
    stores, aid = env
    geo.upsert_city(stores.main, 'Тюмень', 'Восток', 'НСК')
    t = FeedTransport(feed=feed(), html_pk='902', profiles={
        '902': {'username': 'new_tmn', 'full_name': 'New Place', 'category': '',
                'city_name': '', 'biography': 'Ресторан в Тюмени, бронь по телефону'}})
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф @new_tmn'))
    row = main_row(stores, 'ivan_chef')
    assert (row['city'], row['department']) == ('Тюмень', 'НСК')
    assert row['cap_reason'] is None


def test_candidate_own_bio_city_when_no_venue(env):
    stores, aid = env
    geo.upsert_city(stores.main, 'Тюмень', 'Восток', 'НСК')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid,
                       candidate(stores, bio='Шеф-повар Тюмень'))
    row = main_row(stores, 'ivan_chef')
    assert row['department'] == 'НСК' and row['needs_strong_profile'] is True


def test_stories_unavailable_for_long_goes_to_human_not_loops(env):
    """Истории на IP закрыты наглухо: после трёх повторов — человеку, а не вечный цикл."""
    stores, aid = env
    health.record_failure(stores.archive, aid, vetting.ENDPOINT_HIGHLIGHTS, 'throttled')
    task = candidate(stores)
    task['attempts'] = 3
    vetting.handle_vet(stores, FeedTransport(feed=feed(days_ago=300)), aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'сетка_готова'
    assert row['cap_reason'] == 'истории не проверены: посты старше 183 дней'


def test_hidden_feed_on_embed_rejected_as_private(env):
    stores, aid = env
    t = FeedTransport(feed_status=THROTTLED)
    t.embed_status = EMPTY
    vetting.handle_vet(stores, t, aid, candidate(stores, posts=40))
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'закрытый профиль или лента скрыта'


class WebFeedTransport(FeedTransport):
    """API ленты закрыт, /embed/ даёт 6 постов, запрос страницы — 12."""
    def __init__(self, web=None, web_status=OK, **kw):
        super().__init__(feed_status=THROTTLED, **kw)
        self.embed_status = OK
        self.embed = {'items': feed(n=6), 'media_count': 40, 'has_public_story': False,
                      'full_name': 'Иван Петров', 'pk': '77'}
        self._web, self._web_status, self.web_calls = web, web_status, []

    def web_feed(self, nick):
        self.web_calls.append(nick)
        return (self._web if self._web_status == OK else None), self._web_status


def test_card_grid_is_topped_up_to_twelve_posts(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores))
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'сетка_готова' and row['grid_tiles'] == 12
    assert len(row['captions']) == 12 and t.web_calls == ['ivan_chef']


def test_top_up_failure_keeps_six_and_cools_endpoint(env):
    stores, aid = env
    t = WebFeedTransport(web_status=THROTTLED)
    vetting.handle_vet(stores, t, aid, candidate(stores))
    assert main_row(stores, 'ivan_chef')['grid_tiles'] == 6
    assert health.pick_account(stores.archive, vetting.ENDPOINT_WEB_FEED, [aid]) is None


def test_no_top_up_when_profile_has_few_posts(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    t.embed = dict(t.embed, items=feed(n=5), media_count=5)
    vetting.handle_vet(stores, t, aid, candidate(stores, posts=5))
    assert t.web_calls == []


def test_rejected_profile_spends_no_top_up(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Люблю путешествия'))
    assert t.web_calls == []


# --- Проверка отказов человеком: карточка отказа с сеткой, повторный отбор «берём»

def approved(stores, bio='Шеф-повар @fiorentina_nn', posts=40):
    candidate(stores, bio=bio, posts=posts)
    stores.archive.execute("DELETE FROM work_queue WHERE task_type = 'vet'")
    wq.enqueue(stores.archive, 'vet', {'nick': 'ivan_chef', 'pk': '77', 'approved': True}, priority=10)
    return wq.claim_next(stores.archive, ['vet'], 't')


def test_reject_in_review_mode_keeps_bio_and_twelve_post_grid(env):
    stores, aid = env
    review.set_rejects(stores, True)
    t = WebFeedTransport(web=feed(days_ago=300, n=12))
    t.embed = dict(t.embed, items=feed(days_ago=300, n=6))
    vetting.handle_vet(stores, t, aid, candidate(stores))
    rej = stores.archive.execute("SELECT * FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'не активен больше 183 дней'
    assert rej['bio'] == 'Шеф-повар @fiorentina_nn'
    assert rej['grid_path'] and os.path.exists(rej['grid_path'])
    assert Image.open(rej['grid_path']).size == (grid.TILE * 3, grid.TILE * 4)
    assert t.web_calls == ['ivan_chef']


def test_free_rule_reject_in_review_mode_fetches_feed_for_grid(env):
    stores, aid = env
    review.set_rejects(stores, True)
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф шашлычной @mangal_nn'))
    rej = stores.archive.execute("SELECT grid_path FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['grid_path'] and os.path.exists(rej['grid_path'])


def test_reject_without_review_mode_spends_nothing_extra(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф шашлычной @mangal_nn'))
    rej = stores.archive.execute("SELECT grid_path FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['grid_path'] is None and t.web_calls == [] and t.feed_calls == []


def test_approved_profile_passes_rules_with_doubt_and_goes_to_export(env):
    stores, aid = env
    t = FeedTransport(feed=feed(days_ago=300))
    vetting.handle_vet(stores, t, aid, approved(stores))
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'к_выгрузке' and row['department'] == 'МСК'
    assert 'не активен больше 183 дней' in row['cap_reason']
    assert stores.archive.execute('SELECT count(*) AS c FROM rejected').fetchone()['c'] == 0


def test_approved_profile_without_role_in_bio_becomes_chef(env):
    stores, aid = env
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, approved(stores, bio='Готовлю в @fiorentina_nn'))
    row = main_row(stores, 'ivan_chef')
    assert row['role_code'] == 'chef' and row['status'] == 'к_выгрузке'
    assert 'должности нет в шапке' in row['cap_reason']


def test_approved_profile_without_department_waits_for_owner(env):
    stores, aid = env
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, approved(stores, bio='Шеф-повар'))
    assert main_row(stores, 'ivan_chef')['status'] == 'спорный'


def test_approval_survives_task_recreated_without_flag(env):
    stores, aid = env
    contacts.cache_profile(stores, '77', {'username': 'ivan_chef', 'full_name': 'Иван Петров',
                                          'biography': 'Шеф-повар @fiorentina_nn', 'media_count': 40})
    contacts.reject(stores, 'ivan_chef', 'не активен больше 183 дней', 'правила', pk='77')
    review.overturn(stores, 'ivan_chef')
    stores.archive.execute("DELETE FROM work_queue WHERE task_type = 'vet'")
    assert vetting.enqueue_missing(stores) == 1
    task = wq.claim_next(stores.archive, ['vet'], 't')
    vetting.handle_vet(stores, FeedTransport(feed=feed(days_ago=300)), aid, task)
    assert main_row(stores, 'ivan_chef')['status'] == 'к_выгрузке'


def test_review_grid_not_built_for_non_horeca_reject(env):
    stores, aid = env
    review.set_rejects(stores, True)
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф шашлычной @mangal_nn, lifestyle blog'))
    assert t.web_calls == [] and t.feed_calls == []


def test_approved_legacy_profile_without_bio_is_refetched_first(env):
    """30.09.2026: отменённые отказы из старого архива пришли без шапки и pk —
    контакты вышли пустыми. Одобренный без шапки сначала читается заново."""
    stores, aid = env
    contacts.add_candidate(stores, 'ivan_chef', None, {'full_name': '', 'biography': ''},
                           {'source': 'импорт'}, 'Агент №2')
    stores.archive.execute("UPDATE candidates SET status = 'одобрен' WHERE nick = 'ivan_chef'")
    wq.enqueue(stores.archive, 'vet', {'nick': 'ivan_chef', 'pk': None, 'approved': True}, priority=10)
    task = wq.claim_next(stores.archive, ['vet'], 't')
    t = FeedTransport(feed=feed(), html_pk='77',
                      profile={'username': 'ivan_chef', 'full_name': 'Иван Петров', 'category': '',
                               'biography': 'Шеф-повар @fiorentina_nn', 'city_name': '', 'media_count': 40,
                               'is_private': False})
    vetting.handle_vet(stores, t, aid, task)
    row = main_row(stores, 'ivan_chef')
    assert row['venue'] == 'Fiorentina' and row['department'] == 'МСК'
    assert row['status'] == 'к_выгрузке' and row['bio'] == 'Шеф-повар @fiorentina_nn'
    assert row['cap_reason'] is None


def test_approved_doubts_are_not_repeated(env):
    stores, aid = env
    t = FeedTransport(feed=feed())
    vetting.handle_vet(stores, t, aid, approved(stores, bio='Готовлю в @fiorentina_nn'))
    caps = main_row(stores, 'ivan_chef')['cap_reason'].split('; ')
    assert len(caps) == len(set(caps))



# --- Гео РФ (30.09.2026): «много иностранных поваров… РФ гео нужно»

def test_city_from_post_geotags_when_bio_has_none(env):
    stores, aid = env
    geo.upsert_city(stores.main, 'Красноярск', 'Восток', 'НСК')
    t = WebFeedTransport(web=feed(n=12, loc=KRAS))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф-повар'))
    row = main_row(stores, 'ivan_chef')
    assert row['city'] == 'Красноярск' and row['department'] == 'НСК'


def test_foreign_post_geotags_reject(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12, loc=ITALY))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Chef, 33, from Italy'))
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'вне РФ по геометкам постов: Italy'


def test_no_russian_evidence_rejects_latin_bio(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    task = candidate(stores, bio='Chef. Passion for food')
    stores.archive.execute("UPDATE candidates SET full_name = 'John Smith' WHERE nick = 'ivan_chef'")
    vetting.handle_vet(stores, t, aid, task)
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'нет признаков РФ: шапка не на русском, геометок в РФ нет'


def test_russian_bio_without_geo_stays_disputed(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф-повар'))
    row = main_row(stores, 'ivan_chef')
    assert row['status'] == 'сетка_готова' and row['department'] is None
    assert 'город не определён' in row['cap_reason']


def test_venue_city_wins_over_travel_geotags(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12, loc=ITALY))
    vetting.handle_vet(stores, t, aid, candidate(stores))  # «Шеф-повар @fiorentina_nn»
    row = main_row(stores, 'ivan_chef')
    assert row['department'] == 'МСК' and row['venue'] == 'Fiorentina'


def test_venue_naming_its_chef_is_walked_at_vetting(env, tmp_path, monkeypatch):
    stores, aid = env
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'none.txt'))
    task = candidate(stores, nick='nutmeg1', pk='88',
                     bio='С 2009 года\nСемейный ресторан\nChef & Co-owner @fjord\nOsteria в Москве')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, 'nutmeg1') is None
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'nutmeg1'").fetchone()
    assert rej['reason'] == handlers.PLACE_WALK_REASON
    header = stores.archive.execute("SELECT payload FROM work_queue WHERE task_type = 'venue_header'").fetchone()
    assert header['payload']['ig_account'] == 'nutmeg1'


def test_east_chef_consultant_without_venue_is_taken_as_consulting(env):
    stores, aid = env
    geo.upsert_city(stores.main, 'Новосибирск', 'Восток', 'НСК')
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Шеф-повар. Запуск ресторанов под ключ, Новосибирск'))
    row = main_row(stores, 'ivan_chef')
    assert row['role_code'] == 'consulting' and row['department'] == 'НСК'
    assert row['needs_strong_profile'] is False


def test_moscow_consultant_rejected(env):
    stores, aid = env
    t = WebFeedTransport(web=feed(n=12))
    vetting.handle_vet(stores, t, aid, candidate(stores, bio='Ресторанный консультант, запуск ресторанов. Москва'))
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'ivan_chef'").fetchone()
    assert rej['reason'] == 'Ресторанный консалтинг ищем только в отделе НСК'


def test_thumbnails_download_in_parallel_and_keep_order():
    class Slow:
        def download(self, url):
            time.sleep(0.2)
            return (url.encode(), OK) if 'bad' not in url else (None, 'soft_block')
    items = [{'thumb': 'u%d' % i} for i in range(11)] + [{'thumb': 'bad'}, {'thumb': None}]
    t0 = time.time()
    got = vetting.download_thumbs(Slow(), items[:12] + [items[12]])
    assert time.time() - t0 < 1.0
    assert [d for d, _ in got[:3]] == [b'u0', b'u1', b'u2']
    assert got[11] == (None, 'soft_block') and got[12] == (None, None)


# --- паспорт контакта (06.10.2026, разбор выгрузки заказчиком)

def rejected(stores, nick):
    return stores.archive.execute('SELECT reason FROM rejected WHERE nick = %s', (nick,)).fetchone()


def test_foreign_workplace_in_handles_is_rejected(env):
    """Шеф из Ташкента: «Brand-chef @sorrel1.tashkent … @moscow.cafe1.uz» ушёл в Москву из-за «moscow» в нике."""
    stores, aid = env
    task = candidate(stores, nick='north_oak_', bio='Brand-chef @sorrel1.tashkent @basil2.rest @moscow.cafe1.uz')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, 'north_oak_') is None
    assert 'вне РФ' in rejected(stores, 'north_oak_')['reason'] and 'Ташкент' in rejected(stores, 'north_oak_')['reason']


class VenueGeoTransport(FeedTransport):
    """Страница упомянутого заведения без города; его 12 постов — с геометками."""
    def __init__(self, venue_profile, venue_locs, **kw):
        super().__init__(**kw)
        self._vp, self._vl = venue_profile, venue_locs
        self.web_feed_calls = []

    def profile_by_nick(self, nick, need_pk=False):
        return (dict(self._vp, username=nick), OK) if nick == self._vp['username'] else (None, 'soft_block')

    def web_feed(self, nick):
        self.web_feed_calls.append(nick)
        return [{'loc': l} for l in self._vl], OK


ASTANA = {'lat': 51.17, 'lng': 71.45, 'name': 'Astana'}


def test_venue_without_city_is_placed_by_its_geotags(env):
    """oak1_pine1: «Chef de cuisine @amber1.rest» — в шапке заведения города нет, а его посты — в Астане."""
    stores, aid = env
    task = candidate(stores, nick='oak1_pine1', bio='Chef de cuisine @amber1.rest  Вкус без границ')
    vp = {'username': 'amber1.rest', 'pk': '901', 'full_name': 'На Крыше', 'biography': 'Ресторан. Бронь по телефону',
          'category': 'Ресторан', '_source': 'page'}
    t = VenueGeoTransport(vp, [ASTANA] * 6, feed=feed())
    vetting.handle_vet(stores, t, aid, task)
    assert main_row(stores, 'oak1_pine1') is None
    assert 'заведение вне РФ' in rejected(stores, 'oak1_pine1')['reason']
    assert t.web_feed_calls == ['amber1.rest']


def test_mutual_accounts_of_one_person_are_a_duplicate(env):
    stores, aid = env
    stores.main.execute("INSERT INTO contacts (nick, name, venue, status, bio) VALUES ('harbor2_iris2', 'Artyom Belov', '', "
                        "'к_выгрузке', 'Шеф-повар. Внутренний мир моей работы @_alder.birch_')")
    task = candidate(stores, nick='_alder.birch_', bio='Шеф @fiorentina_nn  Моя жизнь @harbor2_iris2')
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, '_alder.birch_') is None
    assert 'дубль @harbor2_iris2' in rejected(stores, '_alder.birch_')['reason']


def test_person_name_is_never_read_as_a_city(env):
    """«Artem Petrov» давал город Артём (Приморье) и отдел НСК."""
    stores, aid = env
    geo.upsert_city(stores.main, 'Артём', 'Восток', 'НСК')
    task = candidate(stores, nick='kestrel.lumen.chef', bio='Chef @fiorentina_nn')
    stores.archive.execute("UPDATE candidates SET full_name = 'Artem Petrov' WHERE nick = 'kestrel.lumen.chef'")
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, 'kestrel.lumen.chef')['city'] == 'Нижний Новгород'
    task = candidate(stores, nick='artem.two', pk='78', bio='Шеф-повар, Москва')
    stores.archive.execute("UPDATE candidates SET full_name = 'Артём Петров' WHERE nick = 'artem.two'")
    vetting.handle_vet(stores, FeedTransport(feed=feed()), aid, task)
    assert main_row(stores, 'artem.two')['city'] == 'Москва'

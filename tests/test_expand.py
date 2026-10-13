"""Новые источники (01.10.2026): отметки/соавторы в постах заведения и «похожие аккаунты»."""
from leadgen.access import accounts, health
from leadgen.core import contacts, queue as wq
from leadgen.sources import expand
from leadgen.qualify import handlers
from leadgen.ops import workers
from leadgen.access.transport import OK, THROTTLED


class T:
    def __init__(self, items=None, users=None, status=OK):
        self.items, self.users, self.status = items, users, status

    def web_feed(self, nick):
        return (self.items if self.status == OK else None), self.status

    def suggested(self, pk, nick):
        return (self.users if self.status == OK else None), self.status


def aid(stores):
    return accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})


def queued(stores):
    return [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'profile_info' ORDER BY id").fetchall()]


def task(stores, ttype, payload):
    wq.enqueue(stores.archive, ttype, payload)
    return wq.claim_next(stores.archive, [ttype], 't')


def test_tags_queue_new_people_except_the_venue_and_known(stores):
    a = aid(stores)
    contacts.reject(stores, 'old_guest', 'нет роли в шапке', 'роль')
    items = [{'people': ['maple_butchery', 'sorrel_tarragon', 'old_guest']}, {'people': ['sorrel_tarragon', 'sous_x']}]
    expand.handle_venue_tags(stores, T(items=items), a,
                             task(stores, 'venue_tags', {'ig_account': 'maple_butchery', 'venue': 'Asado', 'city': 'Москва'}))
    got = queued(stores)
    assert [p['nick'] for p in got] == ['sorrel_tarragon', 'sous_x']
    assert got[0]['source'] == 'отметки' and got[0]['venue'] == 'Asado' and got[0]['city'] == 'Москва'


def test_similar_queues_unknown_accounts_without_city_claim(stores):
    a = aid(stores)
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('Greko', 'Новосибирск', 'PREMIUM', 'dune1_nsk')")
    users = [{'username': 'dune1_nsk', 'pk': '11'}, {'username': 'pine.resto', 'pk': '12'}]
    expand.handle_similar(stores, T(users=users), a, task(stores, 'similar', {'pk': '55', 'ig_account': 'juniper2_nsk'}))
    got = queued(stores)
    assert got == [{'nick': 'pine.resto', 'pk': '12', 'venue': '', 'city': '', 'source': 'похожие'}]


def test_similar_people_wait_behind_cheaper_sources(stores):
    """«Похожие» — 1 600 профилей на контакт против 190 у отметок и 7 у шапок (05.10.2026)."""
    a = aid(stores)
    expand.handle_similar(stores, T(users=[{'username': 'x_rest', 'pk': '1'}]), a,
                          task(stores, 'similar', {'pk': '55', 'ig_account': 'v'}))
    prio = stores.archive.execute("SELECT priority FROM work_queue WHERE task_type = 'profile_info'").fetchone()['priority']
    assert prio > expand.PEOPLE_PRIORITY and prio > 85


def test_blocked_endpoint_requeues_and_cools(stores):
    a = aid(stores)
    t = task(stores, 'similar', {'pk': '55', 'ig_account': 'x'})
    expand.handle_similar(stores, T(status=THROTTLED), a, t)
    assert stores.archive.execute("SELECT status FROM work_queue WHERE id = %s", (t['id'],)).fetchone()['status'] == 'pending'
    assert health.pick_account(stores.archive, expand.ENDPOINT_SIMILAR, [a]) is None


def test_enqueue_seeds_every_target_venue_once_per_label(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
                        "('A', 'Москва', 'PREMIUM', 'a_rest', '1'), ('B', 'Казань', 'UPPER-MIDDLE', 'b_rest', NULL), "
                        "('C', 'Москва', 'MIDDLE', 'c_rest', '3')")
    assert expand.enqueue(stores, '2026-10') == {'venue_tags': 2, 'similar': 1, 'venue_geo': 0}
    assert expand.enqueue(stores, '2026-10') == {'venue_tags': 0, 'similar': 0, 'venue_geo': 0}


def test_workers_know_new_task_types():
    assert 'venue_tags' in workers.TASK_TYPES and 'similar' in workers.TASK_TYPES
    assert workers.HANDLERS['similar'] is expand.handle_similar
    assert workers.task_endpoints('similar') == ((expand.ENDPOINT_SIMILAR,),)
    assert set(expand.SOURCES) <= set(handlers.PLACE_SOURCES)



class G(T):
    def __init__(self, items=None, people=None, status=OK):
        super().__init__(items=items, status=status)
        self.people, self.locs = people, []

    def location_people(self, pk):
        self.locs.append(pk)
        return (self.people if self.status == OK else None), self.status


def test_venue_geo_queues_people_from_most_common_venue_location(stores):
    a = aid(stores)
    loc = {'pk': '219857600', 'lat': 1, 'lng': 1, 'name': 'Жажда крови'}
    other = {'pk': '1', 'lat': 1, 'lng': 1, 'name': 'Москва'}
    items = [{'loc': loc}, {'loc': loc}, {'loc': other}, {'loc': None}]
    t = G(items=items, people=['vale2', 'sage1', 'guest_1'])
    expand.handle_venue_geo(stores, t, a, task(stores, 'venue_geo', {'ig_account': 'vale2', 'venue': 'Жажда крови',
                                                                    'city': 'Москва'}))
    assert t.locs == ['219857600']
    got = queued(stores)
    assert [p['nick'] for p in got] == ['sage1', 'guest_1'] and got[0]['source'] == 'геометки'


def test_venue_without_geotags_spends_no_location_request(stores):
    a = aid(stores)
    t = G(items=[{'loc': None}], people=['x'])
    expand.handle_venue_geo(stores, t, a, task(stores, 'venue_geo', {'ig_account': 'v', 'venue': 'V', 'city': ''}))
    assert t.locs == [] and queued(stores) == []


def test_venue_geo_source_is_not_seeded(stores):
    """05.10.2026: «геометки» — 22 542 профиля за 5 дней, принято 7 (гости ресторана, не повара).
    Источник выключен; обработчик остаётся для задач, уже стоящих в очереди. Проверка гео
    кандидата по его постам (распределение по отделам) — отдельно и не меняется."""
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('A', 'Москва', 'PREMIUM', 'a_rest')")
    assert expand.enqueue(stores, '2026-10')['venue_geo'] == 0
    assert workers.task_endpoints('venue_geo') == ((vetting_endpoint(),), (expand.ENDPOINT_LOCATION,))


def vetting_endpoint():
    from leadgen.qualify import vetting
    return vetting.ENDPOINT_WEB_FEED

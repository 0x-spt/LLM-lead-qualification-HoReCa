"""Участники фестивалей и конкурсов (01.10.2026): имя + должность + город, ника на странице нет."""
from leadgen.access import accounts, health
from leadgen.core import contacts, queue as wq
from leadgen.sources import festivals
from leadgen.ops import workers
from leadgen.access.transport import OK, THROTTLED

PAGE = '''<div><p>#шефская</p><h3>Екатерина Медведева</h3><p>шеф-повар и владелица гастробистро Biologie</p>
<p>Москва</p><p>Екатерина – одна из самых талантливых женщин-шеф-поваров России.</p>
<h3>Борис Мельник</h3><p>Директор по развитию сети ресторанов «Мидийное место»</p><p>Москва</p>
<h3>Андрей Крылов</h3><p>шеф-повар Mamai-Calé</p><p>Сочи</p></div>'''


def test_parse_speakers_keeps_chefs_with_city():
    people = festivals.chefs(festivals.parse_speakers(PAGE))
    assert people == [
        {'name': 'Екатерина Медведева', 'role': 'шеф-повар и владелица гастробистро Biologie', 'city': 'Москва'},
        {'name': 'Андрей Крылов', 'role': 'шеф-повар Mamai-Calé', 'city': 'Сочи'}]


def test_run_queues_person_search_once(stores):
    out = festivals.run(stores, fetch=lambda url: PAGE if 'gastreet' in url else '')
    assert out['chefs'] == 2 and out['queued'] == 2
    assert festivals.run(stores, fetch=lambda url: PAGE if 'gastreet' in url else '')['queued'] == 0
    p = stores.archive.execute("SELECT payload FROM work_queue WHERE task_type = 'person_search' ORDER BY id").fetchone()['payload']
    assert p == {'name': 'Екатерина Медведева', 'city': 'Москва', 'role': 'шеф-повар и владелица гастробистро Biologie',
                 'source': 'фестиваль'}


class T:
    def __init__(self, users=None, status=OK):
        self.users, self.status, self.queries = users, status, []

    def search(self, q):
        self.queries.append(q)
        return (self.users if self.status == OK else None), self.status


def test_person_search_takes_only_exact_name_match(stores):
    aid = accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})
    wq.enqueue(stores.archive, 'person_search', {'name': 'Екатерина Медведева', 'city': 'Москва', 'role': 'шеф', 'source': 'фестиваль'})
    task = wq.claim_next(stores.archive, ['person_search'], 't')
    users = [{'username': 'alekhina.kitchen', 'pk': 1, 'full_name': 'Екатерина Медведева'},
             {'username': 'katya_a', 'pk': 2, 'full_name': 'Екатерина'},
             {'username': 'alekhina_flowers', 'pk': 3, 'full_name': 'Медведева Екатерина | цветы'}]
    t = T(users)
    festivals.handle_person_search(stores, t, aid, task)
    got = [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'profile_info' ORDER BY id").fetchall()]
    assert [g['nick'] for g in got] == ['alekhina.kitchen', 'alekhina_flowers']
    assert got[0]['source'] == 'фестиваль' and got[0]['city'] == 'Москва'
    assert t.queries == ['Екатерина Медведева']


def test_person_search_blocked_requeues(stores):
    aid = accounts.register_own(stores.archive, 'ig1', {'sessionid': 's'})
    wq.enqueue(stores.archive, 'person_search', {'name': 'А Б', 'city': '', 'role': 'шеф', 'source': 'фестиваль'})
    task = wq.claim_next(stores.archive, ['person_search'], 't')
    festivals.handle_person_search(stores, T(status=THROTTLED), aid, task)
    assert stores.archive.execute("SELECT status FROM work_queue WHERE id=%s", (task['id'],)).fetchone()['status'] == 'pending'
    assert health.pick_account(stores.archive, 'search', [aid]) is None


def test_workers_route_person_search():
    assert 'person_search' in workers.TASK_TYPES
    assert workers.task_endpoints('person_search') == (('search',),)

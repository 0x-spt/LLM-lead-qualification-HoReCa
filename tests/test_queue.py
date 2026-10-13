import threading

from leadgen.core import db, queue as wq


def test_claim_next_returns_pending_task_by_priority(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'}, priority=100)
    wq.enqueue(a, 'bridge', {'venue_name': 'B'}, priority=10)
    task = wq.claim_next(a, ['bridge'], 'w1')
    assert task['payload']['venue_name'] == 'B'
    assert task['task_type'] == 'bridge'


def test_claimed_task_not_returned_again(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    wq.claim_next(a, ['bridge'], 'w1')
    assert wq.claim_next(a, ['bridge'], 'w1') is None


def test_fail_with_requeue_makes_task_claimable_again_and_spends_attempt(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    wq.fail(a, task['id'], requeue=True)
    again = wq.claim_next(a, ['bridge'], 'w1')
    assert again['id'] == task['id']
    assert again['attempts'] == 1


def test_fail_without_requeue_stops_task(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    wq.fail(a, task['id'], requeue=False)
    assert wq.claim_next(a, ['bridge'], 'w1') is None
    status = a.execute('SELECT status FROM work_queue WHERE id = %s', (task['id'],)).fetchone()
    assert status['status'] == 'failed'


def test_release_returns_task_without_spending_attempt(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    wq.release(a, task['id'])
    again = wq.claim_next(a, ['bridge'], 'w1')
    assert again['attempts'] == 0


def test_complete_marks_done(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    wq.complete(a, task['id'])
    assert wq.claim_next(a, ['bridge'], 'w1') is None


def test_claim_filters_by_task_type(stores):
    a = stores.archive
    wq.enqueue(a, 'following', {'pk': '1'})
    assert wq.claim_next(a, ['bridge'], 'w1') is None
    assert wq.claim_next(a, ['following'], 'w1')['task_type'] == 'following'


def test_pk_only_types_take_only_tasks_with_pk(stores):
    a = stores.archive
    wq.enqueue(a, 'profile_info', {'nick': 'no_pk'}, priority=1)
    wq.enqueue(a, 'profile_info', {'nick': 'with_pk', 'pk': '42'}, priority=2)
    task = wq.claim_next(a, [], 'w1', pk_only_types=['profile_info'])
    assert task['payload']['nick'] == 'with_pk'


def test_no_types_means_nothing_claimed(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    assert wq.claim_next(a, [], 'w1') is None


def test_expired_lease_is_reclaimed(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    a.execute("UPDATE work_queue SET lease_until = now() - interval '1 minute' WHERE id = %s",
              (task['id'],))
    assert wq.reclaim_expired(a) == 1
    again = wq.claim_next(a, ['bridge'], 'w2')
    assert again['id'] == task['id']
    assert again['attempts'] == 0


def test_live_lease_is_not_reclaimed(stores):
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    wq.claim_next(a, ['bridge'], 'w1')
    assert wq.reclaim_expired(a) == 0


def test_two_workers_never_claim_the_same_task(stores, pg_urls):
    """SKIP LOCKED: параллельные воркеры разбирают очередь без двойного клейма."""
    for i in range(40):
        wq.enqueue(stores.archive, 'bridge', {'venue_name': 'V%d' % i})
    claimed, lock = [], threading.Lock()

    def worker(label):
        conn = db.connect(pg_urls['archive'])
        while True:
            task = wq.claim_next(conn, ['bridge'], label)
            if not task:
                break
            with lock:
                claimed.append(task['id'])
        conn.close()

    threads = [threading.Thread(target=worker, args=('w%d' % i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_stale_worker_cannot_complete_task_that_went_back_to_queue(stores):
    """Воркер завис, аренда истекла, задача вернулась в очередь — его запоздалое
    complete/fail не должно её закрыть или накрутить попытку."""
    a = stores.archive
    wq.enqueue(a, 'bridge', {'venue_name': 'A'})
    task = wq.claim_next(a, ['bridge'], 'w1')
    a.execute("UPDATE work_queue SET lease_until = now() - interval '1 minute'")
    wq.reclaim_expired(a)
    wq.complete(a, task['id'])
    wq.fail(a, task['id'], requeue=True)
    row = a.execute('SELECT status, attempts FROM work_queue').fetchone()
    assert (row['status'], row['attempts']) == ('pending', 0)

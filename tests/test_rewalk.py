from leadgen.core import queue as wq
from leadgen.sources import rewalk


def venues(stores):
    stores.main.execute(
        "INSERT INTO venues (name, city, level, ig_account, pk) VALUES "
        "('A', 'Москва', 'PREMIUM', 'a_rest', '1'), ('B', 'Казань', 'UPPER-MIDDLE', 'b_rest', NULL), "
        "('C', 'Москва', 'MIDDLE', 'c_rest', '3'), ('D', 'Москва', 'PREMIUM', '', NULL)")


def headers(stores):
    return [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'venue_header' ORDER BY id").fetchall()]


def test_rewalk_queues_header_for_every_target_venue_with_instagram(stores):
    venues(stores)
    assert rewalk.enqueue(stores, '2026-09') == 2
    got = headers(stores)
    assert [h['ig_account'] for h in got] == ['a_rest', 'b_rest']
    assert got[0] == {'venue_name': 'A', 'city': 'Москва', 'ig_account': 'a_rest', 'pk': '1', 'rewalk': '2026-09'}
    assert 'pk' not in got[1]


def test_rewalk_is_idempotent_per_label(stores):
    venues(stores)
    rewalk.enqueue(stores, '2026-09')
    assert rewalk.enqueue(stores, '2026-09') == 0
    assert rewalk.enqueue(stores, '2026-10') == 2


def test_rewalk_runs_after_fresh_work(stores):
    venues(stores)
    rewalk.enqueue(stores, '2026-09')
    wq.enqueue(stores.archive, 'profile_info', {'nick': 'x'}, priority=80)
    assert wq.claim_next(stores.archive, ['venue_header', 'profile_info'], 't')['task_type'] == 'profile_info'


def test_rewalk_can_be_limited_to_premium(stores):
    venues(stores)
    assert rewalk.enqueue(stores, '2026-10-prem', levels=('PREMIUM',)) == 1
    assert [h['ig_account'] for h in headers(stores)] == ['a_rest']

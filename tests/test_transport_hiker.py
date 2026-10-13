import httpx

from leadgen.access.transport import CHECKPOINT, EMPTY, OK, SOFT_BLOCK, THROTTLED
from leadgen.access.transport.hiker import HikerTransport


def make(handler):
    return HikerTransport('KEY', http_transport=httpx.MockTransport(handler))


def test_contract_attributes():
    t = make(lambda r: httpx.Response(200, json={}))
    assert t.kind == 'hiker'
    assert t.silent_empty_is_block is False


def test_search_sends_key_and_returns_users():
    def handler(request):
        assert request.headers['x-access-key'] == 'KEY'
        assert request.url.path == '/v2/search/accounts'
        assert request.url.params['query'] == 'Fiorentina Нижний Новгород'
        return httpx.Response(200, json={'users': [{'pk': '5', 'username': 'fiorentina_nn'}]})
    users, status = make(handler).search('Fiorentina Нижний Новгород')
    assert status == OK and users[0]['username'] == 'fiorentina_nn'


def test_search_without_users_is_empty():
    assert make(lambda r: httpx.Response(200, json={'users': []})).search('x') == ([], EMPTY)


def test_pk_by_nick():
    def handler(request):
        assert request.url.path == '/v1/user/by/username'
        return httpx.Response(200, json={'pk': 777, 'username': 'ivan'})
    assert make(handler).pk_by_nick('ivan') == ('777', OK)


def test_profile_info_passes_user_through():
    def handler(request):
        assert request.url.path == '/v1/user/by/id'
        assert request.url.params['id'] == '777'
        return httpx.Response(200, json={'pk': '777', 'biography': 'Шеф', 'city_name': 'Омск'})
    profile, status = make(handler).profile_info('777')
    assert status == OK and profile['city_name'] == 'Омск'


def test_following_chunk_is_unpacked():
    def handler(request):
        assert request.url.path == '/v1/user/following/chunk'
        assert request.url.params['user_id'] == '1'
        assert request.url.params['max_id'] == 'cur'
        return httpx.Response(200, json=[[{'pk': '2', 'username': 'b'}], 'next'])
    assert make(handler).following('1', max_id='cur') == ([{'pk': '2', 'username': 'b'}], 'next', OK)


def test_following_first_page_sends_no_max_id():
    def handler(request):
        assert 'max_id' not in request.url.params
        return httpx.Response(200, json=[[], None])
    assert make(handler).following('1') == ([], None, OK)


def test_media_info_exposes_taken_at_unix():
    handler = lambda r: httpx.Response(200, json={'pk': '9', 'taken_at_ts': 1758000000})
    item, status = make(handler).media_info('9')
    assert status == OK and item['taken_at'] == 1758000000


def test_404_is_empty():
    assert make(lambda r: httpx.Response(404, json={'detail': 'Not found'})).pk_by_nick('x') == (None, EMPTY)


def test_429_is_throttled():
    assert make(lambda r: httpx.Response(429)).search('x') == (None, THROTTLED)


def test_bad_key_or_no_balance_is_checkpoint():
    for code in (401, 402, 403):
        assert make(lambda r, c=code: httpx.Response(c)).search('x') == (None, CHECKPOINT)


def test_server_error_and_network_error_are_soft_block():
    assert make(lambda r: httpx.Response(500)).profile_info('1') == (None, SOFT_BLOCK)

    def boom(request):
        raise httpx.ConnectError('down')
    assert make(boom).profile_info('1') == (None, SOFT_BLOCK)

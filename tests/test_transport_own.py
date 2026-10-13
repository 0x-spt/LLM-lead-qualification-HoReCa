import httpx

from leadgen.access.transport import CHECKPOINT, EMPTY, OK, PROXY_DEAD, SOFT_BLOCK, THROTTLED
from leadgen.access.transport.own import APP_ID, OwnTransport


def make(handler, proxy=None):
    return OwnTransport({'sessionid': 's', 'csrftoken': 'c'}, proxy=proxy,
                        http_transport=httpx.MockTransport(handler))


def test_contract_attributes():
    t = make(lambda r: httpx.Response(200, json={}))
    assert t.kind == 'own'
    assert t.silent_empty_is_block is True


def test_search_returns_users_and_sends_ig_headers():
    def handler(request):
        assert request.headers['x-ig-app-id'] == APP_ID
        assert request.headers['x-csrftoken'] == 'c'
        assert 'account_serp' in str(request.url)
        return httpx.Response(200, json={'users': [{'pk': 1, 'username': 'a'}]})
    users, status = make(handler).search('шеф повар')
    assert status == OK
    assert users[0]['username'] == 'a'


def test_search_empty_list_is_empty_status():
    users, status = make(lambda r: httpx.Response(200, json={'users': []})).search('x')
    assert (users, status) == ([], EMPTY)


def test_429_is_throttled():
    users, status = make(lambda r: httpx.Response(429, text='rate')).search('x')
    assert (users, status) == (None, THROTTLED)


def test_html_instead_of_json_is_soft_block():
    profile, status = make(lambda r: httpx.Response(200, text='<html>вёрстка</html>')).profile_info('1')
    assert (profile, status) == (None, SOFT_BLOCK)


def test_redirect_to_login_is_checkpoint():
    handler = lambda r: httpx.Response(302, headers={'location': 'https://www.instagram.com/accounts/login/'})
    assert make(handler).profile_info('1') == (None, CHECKPOINT)


def test_redirect_to_challenge_is_checkpoint():
    handler = lambda r: httpx.Response(302, headers={'location': '/challenge/action/'})
    assert make(handler).search('x') == (None, CHECKPOINT)


def test_json_checkpoint_message_is_checkpoint():
    handler = lambda r: httpx.Response(400, json={'message': 'checkpoint_required', 'status': 'fail'})
    assert make(handler).following('1') == (None, None, CHECKPOINT)


def test_plain_400_is_soft_block_not_checkpoint():
    handler = lambda r: httpx.Response(400, json={'message': 'bad request'})
    assert make(handler).profile_info('1') == (None, SOFT_BLOCK)


def test_404_is_empty():
    assert make(lambda r: httpx.Response(404)).pk_by_nick('nobody') == (None, EMPTY)


def test_proxy_error_is_proxy_dead():
    def handler(request):
        raise httpx.ProxyError('proxy down')
    assert make(handler, proxy='http://p:1').search('x') == (None, PROXY_DEAD)


def test_connect_error_without_proxy_is_net_error():
    from leadgen.access.transport import NET_ERROR

    def handler(request):
        raise httpx.ConnectError('no network')
    assert make(handler).search('x') == (None, NET_ERROR)


def test_connect_error_with_proxy_is_proxy_dead():
    def handler(request):
        raise httpx.ConnectError('proxy refused')
    assert make(handler, proxy='http://p:1').search('x') == (None, PROXY_DEAD)


def test_timeout_is_net_error():
    from leadgen.access.transport import NET_ERROR

    def handler(request):
        raise httpx.ReadTimeout('slow')
    assert make(handler).profile_info('1') == (None, NET_ERROR)


def test_pk_by_nick_reads_pk_from_html():
    handler = lambda r: httpx.Response(200, text='<script>"profilePage_12345"</script>')
    assert make(handler).pk_by_nick('ivan') == ('12345', OK)


def test_pk_by_nick_without_pk_in_html_is_empty():
    assert make(lambda r: httpx.Response(200, text='<html></html>')).pk_by_nick('ivan') == (None, EMPTY)


def test_profile_info_returns_user():
    handler = lambda r: httpx.Response(200, json={'user': {'pk': 1, 'biography': 'Шеф'}})
    profile, status = make(handler).profile_info('1')
    assert status == OK and profile['biography'] == 'Шеф'


def test_following_returns_users_and_cursor():
    def handler(request):
        assert request.url.params['count'] == '100'
        assert request.url.params['max_id'] == 'cur'
        return httpx.Response(200, json={'users': [{'pk': 2}], 'next_max_id': 'next'})
    users, next_id, status = make(handler).following('1', max_id='cur')
    assert (users, next_id, status) == ([{'pk': 2}], 'next', OK)


def test_media_info_returns_first_item_with_taken_at():
    handler = lambda r: httpx.Response(200, json={'items': [{'taken_at': 1758000000}]})
    item, status = make(handler).media_info('99')
    assert status == OK and item['taken_at'] == 1758000000


def test_socks_proxy_is_supported():
    # Выход на аккаунт идёт через локальный xray (SOCKS5); без socksio httpx падает при создании клиента.
    t = OwnTransport({'csrftoken': 'x'}, proxy='socks5://127.0.0.1:21801')
    t.close()


def test_dead_socks_proxy_is_proxy_dead():
    # Порт, где никто не слушает, — это упавший xray, а не блок Instagram.
    t = OwnTransport({'csrftoken': 'x'}, proxy='socks5://127.0.0.1:1')
    _, status = t._fetch('https://www.instagram.com/')
    t.close()
    assert status == PROXY_DEAD


def test_pk_by_nick_requests_page_as_browser_navigation():
    """30.09.2026: с x-ig-app-id Instagram отдаёт пустую оболочку без pk — pk по вёрстке
    не находился ни у кого, повторный отбор одобренных закрывался без контакта."""
    def handler(request):
        assert 'x-ig-app-id' not in request.headers
        assert request.headers['sec-fetch-mode'] == 'navigate'
        return httpx.Response(200, text='<script>"page_id":"profilePage_4474171292"</script>')
    assert make(handler).pk_by_nick('dill') == ('4474171292', OK)


def test_network_glitch_is_net_error_not_soft_block():
    """30.09.2026: таймауты и оборванные ответы через VPN считались «тихим блоком» —
    ручка аккаунта остывала час, работали три аккаунта из десяти."""
    from leadgen.access.transport import NET_ERROR

    def handler(request):
        raise httpx.ReadTimeout('timed out', request=request)
    assert make(handler)._fetch('https://www.instagram.com/x/') == (None, NET_ERROR)

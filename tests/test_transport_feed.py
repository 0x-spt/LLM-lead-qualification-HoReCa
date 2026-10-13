import httpx

from leadgen.access.transport import EMPTY, OK, THROTTLED
from leadgen.access.transport.hiker import HikerTransport
from leadgen.access.transport.own import OwnTransport

OWN_FEED = {'items': [
    {'taken_at': 1758000000, 'code': 'AAA', 'caption': {'text': 'Тартар из говядины'},
     'image_versions2': {'candidates': [{'url': 'https://cdn/big.jpg', 'width': 1080},
                                        {'url': 'https://cdn/small.jpg', 'width': 320}]}},
    {'taken_at': 1757000000, 'code': 'BBB', 'caption': None,
     'carousel_media': [{'image_versions2': {'candidates': [{'url': 'https://cdn/c1.jpg',
                                                             'width': 640}]}}]},
    {'taken_at': 1756000000, 'code': 'CCC'},
], 'more_available': True}


def own(handler):
    return OwnTransport({'sessionid': 's'}, http_transport=httpx.MockTransport(handler))


def hiker(handler):
    return HikerTransport('KEY', http_transport=httpx.MockTransport(handler))


def test_own_feed_normalizes_items():
    def handler(request):
        assert request.url.path == '/api/v1/feed/user/42/'
        assert request.url.params['count'] == '12'
        return httpx.Response(200, json=OWN_FEED)
    items, status = own(handler).user_feed('42')
    assert status == OK
    assert items[0] == {'taken_at': 1758000000, 'code': 'AAA', 'caption': 'Тартар из говядины',
                        'thumb': 'https://cdn/small.jpg', 'loc': None, 'people': []}
    assert items[1]['thumb'] == 'https://cdn/c1.jpg' and items[1]['caption'] == ''
    assert items[2]['thumb'] is None


def test_own_feed_429():
    assert own(lambda r: httpx.Response(429)).user_feed('42') == (None, THROTTLED)


def test_own_highlights_latest():
    def handler(request):
        assert request.url.path == '/api/v1/highlights/42/highlights_tray/'
        return httpx.Response(200, json={'tray': [{'latest_reel_media': 1757500000},
                                                  {'latest_reel_media': 1758500000}]})
    assert own(handler).highlights_latest('42') == (1758500000, OK)


def test_own_highlights_empty_tray():
    assert own(lambda r: httpx.Response(200, json={'tray': []})).highlights_latest('42') == (None, EMPTY)


def test_own_download():
    assert own(lambda r: httpx.Response(200, content=b'JPEG')).download('https://cdn/x.jpg') == (b'JPEG', OK)


def test_hiker_feed_normalizes_items():
    def handler(request):
        assert request.url.path == '/v1/user/medias/chunk'
        assert request.url.params['user_id'] == '42'
        return httpx.Response(200, json=[[
            {'taken_at_ts': 1758000000, 'code': 'AAA', 'caption_text': 'Подача',
             'thumbnail_url': 'https://cdn/a.jpg'},
            {'taken_at_ts': 1757000000, 'code': 'BBB', 'caption_text': None, 'thumbnail_url': None,
             'resources': [{'thumbnail_url': 'https://cdn/r.jpg'}]},
        ], 'cursor'])
    items, status = hiker(handler).user_feed('42')
    assert status == OK
    assert items == [
        {'taken_at': 1758000000, 'code': 'AAA', 'caption': 'Подача', 'thumb': 'https://cdn/a.jpg'},
        {'taken_at': 1757000000, 'code': 'BBB', 'caption': '', 'thumb': 'https://cdn/r.jpg'}]


def test_hiker_highlights_latest():
    def handler(request):
        assert request.url.path == '/v1/user/highlights'
        return httpx.Response(200, json=[{'latest_reel_media': 1758100000}, {'latest_reel_media': None}])
    assert hiker(handler).highlights_latest('42') == (1758100000, OK)


def test_hiker_download_does_not_send_api_key_to_cdn():
    def handler(request):
        assert 'x-access-key' not in request.headers
        return httpx.Response(200, content=b'IMG')
    t = HikerTransport('KEY', http_transport=httpx.MockTransport(handler))
    assert t.download('https://scontent.cdninstagram.com/x.jpg') == (b'IMG', OK)

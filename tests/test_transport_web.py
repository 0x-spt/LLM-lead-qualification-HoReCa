"""Обход заблокированных профильных ручек: HTML-страница профиля и /embed/.

Фикстуры — настоящие ответы Instagram 28.09.2026 (урезаны, телефон заменён)."""
import os

import httpx

from leadgen.access.transport import CHECKPOINT, EMPTY, OK, SOFT_BLOCK, THROTTLED
from leadgen.access.transport.hiker import HikerTransport
from leadgen.access.transport.own import OwnTransport

FIX = os.path.join(os.path.dirname(__file__), 'fixtures')


def fixture(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return f.read()


def own(handler):
    return OwnTransport({'sessionid': 's'}, http_transport=httpx.MockTransport(handler))


def test_profile_by_nick_reads_bio_name_and_counts_from_html_meta():
    def handler(request):
        assert request.url.path == '/chef_example_1/'
        return httpx.Response(200, text=fixture('profile_page.html'))
    prof, status = own(handler).profile_by_nick('chef_example_1')
    assert status == OK
    assert prof['username'] == 'chef_example_1'
    assert prof['full_name'] == 'Пётр Иванов Шеф-повар Тюмень'
    assert prof['media_count'] == 241
    assert prof['follower_count'] == 10000
    assert 'Победитель кулинарного конкурса' in prof['biography']
    assert 'tv_channel_example' in prof['biography']
    assert prof['pk'] == '1000000001'
    assert prof['category'] == '' and prof['city_name'] == ''


def test_profile_by_nick_english_interface():
    html = ('<meta content="1,234 Followers, 10 Following, 57 Posts - Ivan Petrov (&#064;ivan) on '
            'Instagram: &quot;Head chef @fiorentina_nn&quot;" name="description" />'
            '<meta property="og:title" content="Ivan Petrov (&#064;ivan) &#x2022; Instagram photos" />')
    prof, status = own(lambda r: httpx.Response(200, text=html)).profile_by_nick('ivan')
    assert status == OK
    assert (prof['full_name'], prof['media_count'], prof['follower_count']) == ('Ivan Petrov', 57, 1234)
    assert prof['biography'] == 'Head chef @fiorentina_nn'


def test_profile_by_nick_without_bio():
    html = ('<meta content="120 Followers, 10 Following, 5 Posts - See Instagram photos and videos '
            'from Ivan (&#064;ivan)" name="description" />')
    prof, status = own(lambda r: httpx.Response(200, text=html)).profile_by_nick('ivan')
    assert status == OK and prof['biography'] == '' and prof['media_count'] == 5


def test_profile_page_without_meta_is_empty_not_a_block():
    """Живой прогон: страница без мета-описания охлаждала маршрут всему аккаунту на час."""
    assert own(lambda r: httpx.Response(200, text='<html>login</html>')).profile_by_nick('x') == (None, EMPTY)


def test_profile_by_nick_404_and_429():
    assert own(lambda r: httpx.Response(404)).profile_by_nick('x') == (None, EMPTY)
    assert own(lambda r: httpx.Response(429)).profile_by_nick('x') == (None, THROTTLED)


def test_embed_feed_reads_posts_dates_captions_thumbs():
    def handler(request):
        assert request.url.path == '/chef_example_1/embed/'
        return httpx.Response(200, text=fixture('embed_profile.html'))
    feed, status = own(handler).embed_feed('chef_example_1')
    assert status == OK
    assert feed['media_count'] == 241 and feed['has_public_story'] is False
    assert feed['pk'] == '1000000001'
    items = feed['items']
    assert len(items) == 6
    assert items[0]['code'] == 'SYNTH000001' and items[0]['taken_at'] == 1790360632
    assert items[0]['thumb'].startswith('https://scontent')
    assert items[1]['caption'].startswith('#chefslife')


def test_embed_without_context_is_empty_not_a_block():
    assert own(lambda r: httpx.Response(200, text='<html></html>')).embed_feed('x') == (None, EMPTY)


def test_embed_feed_redirect_to_login_is_checkpoint():
    handler = lambda r: httpx.Response(302, headers={'location': '/accounts/login/'})
    assert own(handler).embed_feed('x') == (None, CHECKPOINT)


def test_hiker_profile_by_nick_and_embed_feed():
    def handler(request):
        if request.url.path == '/v1/user/by/username':
            return httpx.Response(200, json={'pk': '42', 'username': 'ivan', 'full_name': 'Иван',
                                             'biography': 'Шеф', 'media_count': 30, 'city_name': 'Омск'})
        assert request.url.path == '/v1/user/medias/chunk'
        return httpx.Response(200, json=[[{'taken_at_ts': 1758000000, 'code': 'A', 'caption_text': 'x',
                                           'thumbnail_url': 'https://cdn/a.jpg'}], None])
    t = HikerTransport('KEY', http_transport=httpx.MockTransport(handler))
    prof, status = t.profile_by_nick('ivan')
    assert status == OK and prof['biography'] == 'Шеф' and prof['pk'] == '42'
    feed, status = t.embed_feed('ivan')
    assert status == OK and feed['items'][0]['code'] == 'A' and feed['media_count'] == 30


def test_web_pages_are_requested_like_browser_navigation_not_api():
    """С x-ig-app-id Instagram отдаёт пустую оболочку без мета-описания и
    contextJSON (живой замер 28.09.2026) — веб-маршруты ходят как навигация."""
    seen = []

    def handler(request):
        seen.append(request.headers)
        page = 'profile_page.html' if not request.url.path.endswith('/embed/') else 'embed_profile.html'
        return httpx.Response(200, text=fixture(page))
    t = own(handler)
    t.profile_by_nick('chef_example_1')
    t.embed_feed('chef_example_1')
    for h in seen:
        assert 'x-ig-app-id' not in h and 'x-requested-with' not in h
        assert h['sec-fetch-mode'] == 'navigate'
        assert h['cookie'].startswith('sessionid=')


# --- 12 постов для сетки: PolarisProfilePostsQuery с токенами страницы (замер 29.09.2026:
# feed/user отдаёт 302 на главную, /embed/ — только 6 постов)

TOKENS_PAGE = ('<script>["DTSGInitialData",[],{"token":"DT"}]["LSD",[],{"token":"LS"}]'
               '"actorID":"17841","server_revision":1027,"hsi":"75","jazoest=2654"</script>')


def gql_node(i):
    node = {'taken_at': 1700000000 - i, 'code': 'P%d' % i, 'caption': {'text': 'Подача %d' % i},
            'image_versions2': {'candidates': [{'url': 'https://cdn/%d.jpg' % i, 'width': 320}]}}
    if i == 0:
        node['location'] = {'pk': 1, 'lat': 56.01, 'lng': 92.85, 'name': 'Красноярск'}
    return {'node': node}


def gql_json(n=12):
    return {'data': {'xdt_api__v1__feed__user_timeline_graphql_connection':
                     {'edges': [gql_node(i) for i in range(n)]}}}


def test_web_feed_returns_twelve_posts_from_graphql():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        body = request.content.decode()
        assert 'doc_id=' in body and 'fb_dtsg=DT' in body and 'lsd=LS' in body and '__a=1' in body
        assert 'chef_example_1' in body and request.headers['x-fb-lsd'] == 'LS'
        return httpx.Response(200, json=gql_json())
    items, status = own(handler).web_feed('chef_example_1')
    assert status == OK and len(items) == 12
    assert items[0] == {'taken_at': 1700000000, 'code': 'P0', 'caption': 'Подача 0', 'thumb': 'https://cdn/0.jpg',
                        'loc': {'pk': '1', 'lat': 56.01, 'lng': 92.85, 'name': 'Красноярск'}, 'people': []}
    assert items[1]['loc'] is None
    # токены сессии — с постоянной страницы, а не с целевой (та бывает переименована)
    assert seen == [('GET', '/instagram/'), ('POST', '/graphql/query')]


def test_web_feed_reuses_page_tokens():
    gets = []

    def handler(request):
        if request.method == 'GET':
            gets.append(request.url.path)
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, json=gql_json(3))
    t = own(handler)
    t.web_feed('a')
    t.web_feed('b')
    assert gets == ['/instagram/']


def test_web_feed_without_tokens_is_soft_block():
    assert own(lambda r: httpx.Response(200, text='<html></html>')).web_feed('x') == (None, SOFT_BLOCK)


def test_web_feed_html_answer_is_soft_block_and_tokens_refreshed():
    gets = []

    def handler(request):
        if request.method == 'GET':
            gets.append(1)
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, text='<!DOCTYPE html><html></html>')
    t = own(handler)
    assert t.web_feed('x') == (None, SOFT_BLOCK)
    t.web_feed('y')
    assert len(gets) == 2


def test_web_feed_429_is_throttled():
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(429)
    assert own(handler).web_feed('x') == (None, THROTTLED)


# --- Трафик (30.09.2026): страница профиля 880 КБ, шапка — в первых 10 КБ, pk — на 460-м.
# 3 850 профилей в час съели тестовую подписку VPN (5 ГБ) за полдня.

def chunked(head, filler_chunks=200, tail=b''):
    consumed = []

    def gen():
        consumed.append(1)
        yield head
        for _ in range(filler_chunks):
            consumed.append(1)
            yield b'x' * 4096
        consumed.append(1)
        yield tail
    return gen, consumed


def test_profile_page_stops_reading_after_meta():
    page = fixture('profile_page.html').encode()
    cut = page.index(b'</head>') + 7 if b'</head>' in page else len(page)
    gen, consumed = chunked(page[:cut])
    t = own(lambda r: httpx.Response(200, content=gen()))
    prof, status = t.profile_by_nick('chef_example_1')
    assert status == OK and prof['media_count'] == 241 and 'tv_channel_example' in prof['biography']
    assert len(consumed) < 5


def test_profile_page_reads_on_to_pk_when_asked():
    gen, consumed = chunked(b'<head><meta property="og:description" content="1 Followers, 2 Following, 3 Posts - '
                            b'See Instagram photos and videos from Ivan (@ivan)"></head>',
                            filler_chunks=50, tail=b'"page_id":"profilePage_555"')
    prof, status = own(lambda r: httpx.Response(200, content=gen())).profile_by_nick('ivan', need_pk=True)
    assert status == OK and prof['pk'] == '555'


def test_pk_by_nick_stops_at_pk():
    gen, consumed = chunked(b'<html>"page_id":"profilePage_777"', filler_chunks=200)
    assert own(lambda r: httpx.Response(200, content=gen())).pk_by_nick('x') == ('777', OK)
    assert len(consumed) < 5


# --- Новые источники (01.10.2026): отметки и соавторы в постах заведения, «похожие аккаунты»

def test_web_feed_items_carry_tagged_people_and_coauthors():
    node = gql_node(0)
    node['node']['usertags'] = {'in': [{'user': {'username': 'sorrel_tarragon', 'pk': '7'}}]}
    node['node']['coauthor_producers'] = [{'username': 'maple_butchery'}, {'username': 'chef_two'}]
    node['node']['carousel_media'] = [{'usertags': {'in': [{'user': {'username': 'sous_chef_x'}}]}}]

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, json={'data': {'xdt_api__v1__feed__user_timeline_graphql_connection':
                                                  {'edges': [node]}}})
    items, status = own(handler).web_feed('maple_butchery')
    assert status == OK
    assert items[0]['people'] == ['sorrel_tarragon', 'sous_chef_x', 'maple_butchery', 'chef_two']


def test_suggested_accounts_parsed_from_graphql():
    seen = []

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        body = request.content.decode()
        seen.append(body)
        return httpx.Response(200, json={'data': {'xdt_api__v1__discover__chaining': {'users': [
            {'username': 'dune1_nsk', 'pk': '11', 'full_name': 'Greko'},
            {'username': 'pine.resto', 'id': '12', 'full_name': 'Bacco'},
            {'username': 'dune1_nsk', 'pk': '11'}]}}})
    users, status = own(handler).suggested('55', 'juniper2_nsk')
    assert status == OK
    assert users == [{'username': 'dune1_nsk', 'pk': '11', 'full_name': 'Greko'},
                     {'username': 'pine.resto', 'pk': '12', 'full_name': 'Bacco'}]
    assert 'target_id' in seen[0] and '55' in seen[0]


def test_suggested_html_answer_is_soft_block():
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, text='<html></html>')
    assert own(handler).suggested('55', 'x') == (None, SOFT_BLOCK)


def test_web_feed_of_missing_account_is_empty_not_block():
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, json={'data': {'xdt_api__v1__feed__user_timeline_graphql_connection': None}})
    assert own(handler).web_feed('renamed_venue') == (None, EMPTY)


def test_web_feed_field_exception_is_empty():
    # 01.10.2026: несуществующий ник — {"data": null, "errors": [{"code": 1357005, …}]}
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, json={'data': None, 'errors': [{'message': 'field_exception', 'code': 1357005}]})
    assert own(handler).web_feed('no_such_venue') == (None, EMPTY)


def test_suggested_request_error_for_one_target_is_empty_not_block():
    # 01.10.2026: noncoercible_argument_value (код 1675012) — у этого заведения pk не принят;
    # блок аккаунта на час из-за одного заведения выключал «похожие» целиком
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=TOKENS_PAGE)
        return httpx.Response(200, json={'errors': [{'message': 'noncoercible_argument_value', 'code': 1675012}]})
    assert own(handler).suggested('55', 'x') == (None, EMPTY)


# --- Геометки заведения (01.10.2026): популярные посты места — авторы и отмеченные

def test_feed_item_location_carries_pk():
    from leadgen.access.transport.own import _own_item
    it = _own_item({'location': {'pk': 219857600, 'lat': 55.7, 'lng': 37.5, 'name': 'Жажда крови'}})
    assert it['loc'] == {'pk': '219857600', 'lat': 55.7, 'lng': 37.5, 'name': 'Жажда крови'}


def test_location_people_from_ranked_sections():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content.decode()))
        return httpx.Response(200, json={'sections': [{'layout_content': {'medias': [
            {'media': {'user': {'username': 'sage1'}, 'usertags': {'in': [{'user': {'username': 'sous_x'}}]}}},
            {'media': {'user': {'username': 'guest_1'}}},
            {'media': {'user': {'username': 'sage1'}}}]}}]})
    people, status = own(handler).location_people('219857600')
    assert status == OK and people == ['sage1', 'sous_x', 'guest_1']
    assert seen[0][:2] == ('POST', '/api/v1/locations/219857600/sections/') and 'tab=ranked' in seen[0][2]

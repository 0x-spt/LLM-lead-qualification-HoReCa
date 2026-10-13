"""Мост через сайт заведения: карточка 2ГИС → сайт → ссылка на Instagram.

Замер 29.09.2026: у 20 из 24 ресторанов Новосибирска в 2ГИС есть сайт, на 6
из них — ссылка на Instagram заведения. Поиск Instagram не тратится вовсе."""
import httpx

from leadgen.sources import gis, registry
from leadgen.qualify import handlers
from leadgen.core import queue as wq
from tests.test_handlers import FakeTransport, aid, claim  # noqa: F401

FIRM = ("var initialState = JSON.parse('%s');" % __import__('json').dumps({'data': {'entity': {'profile': {
    '42': {'data': {'id': '42', 'name': 'Магадан, ресторан', 'contact_groups': [{'contacts': [
        {'type': 'phone', 'text': '+7 383 000'},
        {'type': 'website', 'text': 'magadan-nsk.ru', 'value': 'http://link.2gis.ru/x'},
        {'type': 'vkontakte', 'text': 'https://vk.com/magadan'}]}]}}}}}}, ensure_ascii=False)
    .replace('\\', '\\\\').replace("'", "\\'"))


def client(site_html, firm_html=FIRM):
    def handler(request):
        if request.url.host == '2gis.ru':
            return httpx.Response(200, text=firm_html)
        return httpx.Response(200, text=site_html)
    return gis.GisClient(http_transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_firm_contacts_parsed():
    contacts = gis.firm_contacts(gis.parse_state(FIRM), '42')
    assert [c['type'] for c in contacts] == ['phone', 'website', 'vkontakte']


def test_single_instagram_link_on_site():
    html = '<a href="https://www.instagram.com/magadan_syn.nsk/">Мы в Instagram</a>'
    assert client(html).site_instagram('novosibirsk', '42', 'Магадан') == ('magadan_syn.nsk', 'ok')


def test_group_site_with_many_links_picks_the_one_matching_name():
    html = ('<a href="https://instagram.com/novikovgroup">группа</a>'
            '<a href="https://instagram.com/magadan_syn.nsk">Магадан</a><a href="https://instagram.com/ptichka_syn_nsk">Птичка</a>')
    assert client(html).site_instagram('novosibirsk', '42', 'Магадан') == ('magadan_syn.nsk', 'ok')


def test_ambiguous_links_give_nothing():
    html = '<a href="https://instagram.com/novikovgroup">x</a><a href="https://instagram.com/ptichka_syn_nsk">y</a>'
    assert client(html).site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_match')


def test_service_links_ignored():
    html = '<a href="https://instagram.com/p/ABC">пост</a><a href="https://instagram.com/explore/tags/x">тег</a>'
    assert client(html).site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_instagram')


def test_no_website_in_card():
    firm = FIRM.replace('website', 'fax')
    assert client('', firm_html=firm).site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_site')


def test_site_down_is_just_no_instagram():
    def handler(request):
        if request.url.host == '2gis.ru':
            return httpx.Response(200, text=FIRM)
        raise httpx.ConnectTimeout('down')
    c = gis.GisClient(http_transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_instagram')


class SiteGis:
    def __init__(self, nick):
        self.nick, self.site_calls = nick, []

    def search(self, slug, query, page=1):
        if page > 1:
            return [], 'ok'
        return [{'gis_id': '42', 'name': 'Магадан', 'kind': 'ресторан', 'city': 'Новосибирск', 'alias': slug,
                 'address': 'Красный проспект, 1', 'rubrics': ['Рестораны'], 'rating': 4.9, 'check': 4000,
                 'level': 'PREMIUM', 'branches': 1}], 'ok'

    def site_instagram(self, slug, gis_id, name):
        self.site_calls.append((slug, gis_id))
        return (self.nick, 'ok') if self.nick else (None, 'no_instagram')


def test_registry_takes_account_from_site_and_skips_search_bridge(stores):
    registry.harvest(stores, SiteGis('magadan_syn.nsk'), 'novosibirsk', 'Новосибирск', 'рестораны')
    row = stores.main.execute("SELECT ig_account, gis_id, site_checked_at FROM venues WHERE name = 'Магадан'").fetchone()
    assert row['ig_account'] == 'magadan_syn.nsk' and row['gis_id'] == '42' and row['site_checked_at'] is not None
    assert wq.claim_next(stores.archive, ['web_bridge'], 't') is None
    assert wq.claim_next(stores.archive, ['venue_header'], 't')['payload']['ig_account'] == 'magadan_syn.nsk'


def test_registry_falls_back_to_search_bridge(stores):
    registry.harvest(stores, SiteGis(None), 'novosibirsk', 'Новосибирск', 'рестораны')
    assert wq.claim_next(stores.archive, ['web_bridge'], 't')['payload']['venue_name'] == 'Магадан'


def test_existing_venue_waiting_for_bridge_gets_site_check_once(stores):
    """Заведения, собранные до моста через сайт, проверяются при повторном проходе реестра."""
    stores.main.execute("INSERT INTO venues (name, city, source) VALUES ('Магадан', 'Новосибирск', '2ГИС')")
    g = SiteGis('magadan_syn.nsk')
    registry.harvest(stores, g, 'novosibirsk', 'Новосибирск', 'рестораны')
    registry.harvest(stores, g, 'novosibirsk', 'Новосибирск', 'рестораны')
    assert len(g.site_calls) == 1
    assert stores.main.execute("SELECT ig_account FROM venues").fetchone()['ig_account'] == 'magadan_syn.nsk'


def test_bridge_task_skips_search_when_account_already_known(stores, aid):
    stores.main.execute("INSERT INTO venues (name, city, ig_account) VALUES ('Магадан', 'Новосибирск', 'magadan_syn.nsk')")
    wq.enqueue(stores.archive, 'bridge', {'venue_name': 'Магадан', 'city': 'Новосибирск'})
    t = FakeTransport()
    handlers.HANDLERS['bridge'](stores, t, aid, claim(stores, 'bridge'))
    assert t.search_calls == []


def test_website_field_with_text_instead_of_domain_is_skipped():
    """Живой прогон 29.09: в поле сайта 2ГИС — «Перейти в Telegram-канал»; реестр падал на каждом круге."""
    firm = FIRM.replace('magadan-nsk.ru', 'Перейти в Telegram-канал')
    assert client('<a href="https://instagram.com/magadan_syn.nsk">x</a>', firm_html=firm) \
        .site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_site')


def test_single_link_of_group_is_not_the_venue_account():
    """Живой прогон 29.09: «Хозяин тайги» получил @bellini_group — аккаунт группы, а не ресторана."""
    html = '<a href="https://instagram.com/bellini_group">Мы в Instagram</a>'
    assert client(html).site_instagram('novosibirsk', '42', 'Магадан') == (None, 'no_match')

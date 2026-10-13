"""Restoclub (01.10.2026): рейтинги Москвы, Петербурга, Екатеринбурга и Казани — заведения со средним
чеком и часто с Instagram; новых PREMIUM/UPPER-MIDDLE — в реестр и на обход."""
from leadgen.sources import restoclub

INDEX = '<a href="/msk/ratings/rejting-italjanskih-restoranov">Итальянские</a><a href="/msk/ratings/premium">Премиум</a>'
RATING = '<a href="/msk/place/zio-vini">Zio Vini</a> <a href="/msk/place/cheap-cafe">Cheap</a> <a href="/msk/place/known-one">K</a>'
PLACES = {
    '/msk/place/zio-vini': ('<h1><span class="d-none mr-8">Ресторан<!-- --> </span><div class="d-flex gap-x-8">Zio Vini</div>'
                            '<span>/Зио вини</span></h1><p>Средний чек: 6000 Кухня: итальянская</p>'
                            '<a href="https://www.instagram.com/maple2.moscow/">ig</a><a href="https://instagram.com/restoclub_ru">rc</a>'),
    '/msk/place/cheap-cafe': '<h1><div class="d-flex gap-x-8">Cheap</div></h1><p>Средний чек: 900 Кухня</p>',
    '/msk/place/known-one': '<h1><div class="d-flex gap-x-8">Без инсты</div></h1><p>Средний чек: 3000</p>',
}


def fetch(url):
    path = url.replace('https://www.restoclub.ru', '')
    if path == '/msk/ratings':
        return INDEX
    if path.startswith('/msk/ratings/'):
        return RATING
    return PLACES.get(path, '')


def test_parse_place():
    assert restoclub.parse_place(PLACES['/msk/place/zio-vini']) == {'name': 'Zio Vini', 'check': 6000, 'instagram': 'maple2.moscow'}
    assert restoclub.parse_place(PLACES['/msk/place/cheap-cafe'])['check'] == 900


def test_run_adds_target_venues_and_queues_work(stores):
    out = restoclub.run(stores, fetch=fetch, cities={'msk': 'Москва'}, pause=0)
    assert out == {'places': 3, 'target': 2, 'with_instagram': 1, 'new': 2}
    v = {r['name']: r for r in stores.main.execute("SELECT name, city, level, avg_check, ig_account, source FROM venues").fetchall()}
    assert v['Zio Vini']['level'] == 'PREMIUM' and v['Zio Vini']['ig_account'] == 'maple2.moscow' and v['Zio Vini']['source'] == 'Restoclub'
    assert v['Без инсты']['level'] == 'UPPER-MIDDLE' and not v['Без инсты']['ig_account']
    assert 'Cheap' not in v
    tasks = {r['task_type']: r['payload'] for r in stores.archive.execute("SELECT task_type, payload FROM work_queue").fetchall()}
    assert tasks['venue_header']['ig_account'] == 'maple2.moscow'
    assert tasks['web_bridge'] == {'venue_name': 'Без инсты', 'city': 'Москва'}


def test_seen_places_are_not_fetched_again(stores):
    calls = []

    def counting(url):
        calls.append(url)
        return fetch(url)
    restoclub.run(stores, fetch=counting, cities={'msk': 'Москва'}, pause=0)
    n = len(calls)
    out = restoclub.run(stores, fetch=counting, cities={'msk': 'Москва'}, pause=0)
    assert out['places'] == 0 and len(calls) - n == 3  # индекс и две подборки, страницы заведений — нет


def test_parse_real_place_page():
    # 01.10.2026: в настоящей вёрстке число чека — в отдельном теге; тест на упрощённой разметке это пропустил
    import os
    page = open(os.path.join(os.path.dirname(__file__), 'fixtures', 'restoclub_place.html'), encoding='utf-8').read()
    info = restoclub.parse_place(page)
    assert info['name'] == 'Maurizio' and info['check'] == 3000


def test_name_html_entities_decoded():
    page = '<h1><div class="d-flex gap-x-8">Meat_Coin Steak &amp; Terrace</div></h1>'
    assert restoclub.parse_place(page)['name'] == 'Meat_Coin Steak & Terrace'


def test_city_without_ratings_uses_search_collections(stores):
    """05.10.2026: у Новосибирска, Нижнего, Краснодара и др. рейтингов нет, но есть подборки
    /<город>/search/… — по 60 заведений на странице."""
    pages = {'/nsk/ratings': '<a href="/nsk/search/restorany-v-centre">Центр</a>',
             '/nsk/search/restorany-v-centre': '<a href="/nsk/place/greko">Greko</a>',
             '/nsk/place/greko': '<h1><div class="d-flex gap-x-8">Greko</div></h1><p>Средний чек: 4000</p>'}
    out = restoclub.run(stores, fetch=lambda u: pages.get(u.replace('https://www.restoclub.ru', ''), ''),
                        cities={'nsk': 'Новосибирск'}, pause=0)
    assert out['target'] == 1
    assert stores.main.execute("SELECT city FROM venues WHERE name = 'Greko'").fetchone()['city'] == 'Новосибирск'


def test_restoclub_covers_ten_cities():
    assert {'nsk', 'nn', 'krd', 'rnd', 'sochi', 'samara'} <= set(restoclub.CITIES)

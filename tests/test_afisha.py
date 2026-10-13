"""Афиша (05.10.2026): каталог ресторанов по городам. В каждую страницу списка встроены карточки
schema.org Restaurant (название, город, чек) — 30 заведений за запрос, Instagram не тратится."""
import json

from leadgen.sources import afisha


def card(name, price, city='Новосибирск', url='https://www.afisha.ru/novosibirsk/restaurant/x-1/'):
    item = {'@context': 'https://schema.org', '@type': 'Restaurant', 'name': name,
            'address': {'@type': 'PostalAddress', 'addressLocality': city, 'streetAddress': 'Кирова, 50'},
            'url': url, 'description': 'Шеф готовит "по-сибирски"\r\nи не только', 'priceRange': price}
    return {'@type': 'ListItem', 'item': item}


def page(*cards):
    # как на сайте: JSON-LD лежит строкой внутри скрипта, кавычки экранированы
    raw = json.dumps({'@type': 'ItemList', 'itemListElement': list(cards)}, ensure_ascii=False)
    return '<script>self.__next_f.push([1,"%s"])</script>' % raw.replace('\\', '\\\\').replace('"', '\\"')


def test_parse_cards_from_listing():
    got = afisha.parse_listing(page(card('Териберка', '3000–6000 ₽', url='https://www.afisha.ru/novosibirsk/restaurant/teriberka-1/'),
                                    card('Шаурма', 'до 500 ₽')))
    assert got == [{'name': 'Териберка', 'city': 'Новосибирск', 'check': 4500,
                    'url': 'https://www.afisha.ru/novosibirsk/restaurant/teriberka-1/'},
                   {'name': 'Шаурма', 'city': 'Новосибирск', 'check': 500, 'url': 'https://www.afisha.ru/novosibirsk/restaurant/x-1/'}]


def test_check_from_price_range():
    assert afisha.check_of('3000–6000 ₽') == 4500
    assert afisha.check_of('от 6000 ₽') == 6000
    assert afisha.check_of('1 500–2 500 ₽') == 2000
    assert afisha.check_of('') is None


def test_run_adds_target_venues_queues_bridge_and_pages_until_empty(stores):
    pages = {1: page(card('Териберка', '3000–6000 ₽', url='u1'), card('Шаурма', 'до 500 ₽', url='u2')),
             2: page(card('Горыныч', '2000–3000 ₽', url='u3')), 3: ''}
    calls = []

    def fetch(url):
        calls.append(url)
        n = int(url.split('page=')[1]) if 'page=' in url else 1
        return pages.get(n, '')
    out = afisha.run(stores, fetch=fetch, cities={'novosibirsk': 'Новосибирск'}, pause=0)
    assert out == {'places': 3, 'target': 2, 'new': 2}
    v = {r['name']: r for r in stores.main.execute('SELECT name, city, level, source FROM venues').fetchall()}
    assert v['Териберка']['level'] == 'PREMIUM' and v['Териберка']['source'] == 'Афиша'
    assert v['Горыныч']['level'] == 'UPPER-MIDDLE' and 'Шаурма' not in v
    bridges = [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'web_bridge' ORDER BY id").fetchall()]
    assert bridges == [{'venue_name': 'Териберка', 'city': 'Новосибирск'}, {'venue_name': 'Горыныч', 'city': 'Новосибирск'}]
    # второй проход: карточки уже видены — новых нет, мост повторно не ставится
    assert afisha.run(stores, fetch=fetch, cities={'novosibirsk': 'Новосибирск'}, pause=0)['new'] == 0


def test_venue_city_is_the_afisha_section_not_the_card(stores):
    """В карточке бывает пригород («пос. Кольцово») — город для отдела берём по разделу Афиши."""
    pages = {1: page(card('Кольцо', '4000 ₽', city='пос. Кольцово', url='k1'))}
    afisha.run(stores, fetch=lambda url: '' if 'page=' in url else pages[1],
               cities={'novosibirsk': 'Новосибирск'}, pause=0)
    assert stores.main.execute("SELECT city FROM venues WHERE name = 'Кольцо'").fetchone()['city'] == 'Новосибирск'


def test_stops_when_site_repeats_pages_beyond_the_end(stores):
    """Афиша на ?page=400 отдаёт непустой список (замер 05.10.2026) — останавливаемся, когда
    страница не добавила ни одного заведения к этому проходу."""
    calls = []

    def fetch(url):
        calls.append(url)
        n = int(url.split('page=')[1]) if 'page=' in url else 1
        # карусель «популярное» меняется от страницы к странице — список целиком не повторяется
        return page(card('A%d' % min(n, 2), '4000 ₽', url='u%d' % min(n, 2)),
                    card('Карусель%d' % (n % 2), '4000 ₽', url='c%d' % (n % 2)))
    afisha.run(stores, fetch=fetch, cities={'novosibirsk': 'Новосибирск'}, pause=0)
    assert len(calls) == 3


def test_afisha_choice_collections_from_sitemap(stores):
    """«Выбор Афиши» (05.10.2026): редакционные подборки по городам из карты сайта; в Москве 117.
    Город — по разделу из нашего списка, иначе по адресу карточки, если такой город у нас есть."""
    site = {
        'https://www.afisha.ru/rests/sitemap.xml':
            '<loc>https://www.afisha.ru/tomsk/restaurants/sitemap.xml</loc>'
            '<loc>https://www.afisha.ru/almaty/restaurants/sitemap.xml</loc>',
        'https://www.afisha.ru/tomsk/restaurants/sitemap-afisha_choice.xml':
            '<loc>https://www.afisha.ru/tomsk/restaurants/luchshie/</loc>',
        'https://www.afisha.ru/tomsk/restaurants/luchshie/': page(card('Сибирь', '3000–6000 ₽', city='Томск', url='t1')),
        'https://www.afisha.ru/almaty/restaurants/sitemap-afisha_choice.xml':
            '<loc>https://www.afisha.ru/almaty/restaurants/best/</loc>',
        'https://www.afisha.ru/almaty/restaurants/best/': page(card('Алма', '3000–6000 ₽', city='Алматы', url='a1')),
    }
    out = afisha.run_choice(stores, fetch=lambda u: site.get(u, ''), pause=0)
    assert out['new'] == 1
    assert stores.main.execute("SELECT city FROM venues WHERE name = 'Сибирь'").fetchone()['city'] == 'Томск'
    assert stores.main.execute("SELECT 1 FROM venues WHERE name = 'Алма'").fetchone() is None

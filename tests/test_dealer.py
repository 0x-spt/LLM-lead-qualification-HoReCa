"""Дилеры по Востоку (08.10.2026, заказчик): «люди, кто занимается снабжением ресторанов посудой HoReCa»,
только отдел НСК. Своего заведения и ленты с подачами у дилера нет."""
from leadgen.qualify import roles, rules
from leadgen.sources import websearch

DEALER = [r for r in roles.DEFAULTS if r['code'] == 'dealer'][0]
ALL = sorted([dict(r, enabled=True) if r['code'] == 'dealer' else r for r in roles.DEFAULTS],
             key=lambda r: r['priority'])
ENABLED = [r for r in ALL if r['enabled']]


def code(bio, cat=''):
    role, stop = roles.match(ENABLED, cat, bio)
    return role and not stop and role['code']


def test_dealer_bios_match():
    assert code('Посуда для ресторанов и кафе | Новосибирск | Porland, Tognana, Kutahya') == 'dealer'
    assert code('Официальный дилер Wilmax в Красноярске. Оснащение HoReCa под ключ') == 'dealer'
    assert code('Менеджер по работе с HoReCa · фарфор и стекло для ресторанов · Иркутск') == 'dealer'
    assert code('Снабжение ресторанов посудой и инвентарём. Омск') == 'dealer'


def test_not_dealers():
    assert code('Посуда для дома и кухни, доставка по городу') != 'dealer'
    assert code('Официальный дилер Toyota в Тюмени') != 'dealer'
    assert code('Шеф-повар @fiorentina_nn') == 'chef'          # шеф остаётся шефом


def test_dealer_only_for_nsk_and_without_venue_level():
    assert DEALER['departments'] == ['НСК'] and DEALER['venue_levels'] == [] and not DEALER['needs_feed']


def test_supplier_name_and_wording_are_not_a_venue_for_dealer():
    """«Посуда HoReCa» в имени — для шефа признак вывески, для дилера это и есть профиль."""
    p = {'full_name': 'Посуда HoReCa Новосибирск', 'biography': 'Посуда для кафе, баров и фастфуда, кейтеринга',
         'category': 'Оптовая компания', 'media_count': 40}
    assert rules.precheck(p, DEALER, None) == (True, None)
    assert rules.precheck(dict(p, category='Ресторан'), DEALER, None)[0] is False


def test_dealer_search_runs_first_and_only_in_the_east():
    qs = websearch.queries()
    dealer = [i for i, (q, _) in enumerate(qs) if q.split('"')[1] in websearch.DEALER_TERMS]
    chefs = [i for i, (q, _) in enumerate(qs) if q.split('"')[1] in websearch.ROLES]
    assert dealer and max(dealer) < min(chefs)
    assert not any('Москва' in qs[i][0] or 'Санкт-Петербург' in qs[i][0] for i in dealer)
    assert any('Новосибирск' in qs[i][0] for i in dealer)


def test_grader_accepts_dealer_without_dishes():
    from leadgen.llm import grader
    assert 'Дилер' in grader.TEXT_SYSTEM and 'Дилер' in grader.VISION_SYSTEM

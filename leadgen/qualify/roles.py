"""Roles as data — the role_profiles table in the main database.

A profile: role markers in the bio (category + bio text), exclude markers,
stop markers, allowed venue levels, the grader prompt, reference accounts and
CRM tagging. A new role is a new row; enabled via a bot command (plan 3) or in
Supabase Studio. The matching code is shared across all roles.

Order is by priority: the first enabled profile whose markers matched and
whose excludes didn't. Pastry chef comes before chef: "Бренд Шеф Кондитер" ->
pastry chef."""
import re

from leadgen.qualify import quality

CHEF_PROMPT = (
    'Шеф-повар, бренд-шеф, су-шеф действующего заведения. Лента должна показывать '
    'готовые блюда в подаче на посуде. Консультанты без своего заведения, '
    'преподаватели, выездные и travel-шефы, кейтеринг — не подходят.')
PASTRY_PROMPT = (
    'Шеф-кондитер заведения уровня PREMIUM. Лента — десерты в подаче в заведении. '
    'Домашние кондитеры и торты на заказ — не подходят.')

# Role markers in the bio — ported from ROLE_STRONG in db/tools/accept.py
CHEF_MARKERS = (r'шеф.?повар|бренд.?шеф|брэнд.?шеф|су.?шеф|концепт.?шеф|executive chef'
                r'|brand ?chef|head chef|sous ?chef|corporate chef|\bchef\b|\bшеф\b')
PASTRY_WORDS = (r'кондитер|pastry|конфет|десерт|торт|выпечк|bakery|пекарн|patisserie'
                r'|шоколат|chocolat')

# HoReCa tableware dealer: goods near the target audience (restaurant/HoReCa), supply, dealership, restaurant-ware brands
_GOODS = r'(посуд|фарфор|стекл|сервировк|столов\w* прибор|инвентар|оснащени|оборудовани)'  # textiles aren't tableware
_FOR = r'(ресторан|horeca|хорека|кафе|отел|гостиниц|общепит|бар(ов|ы|ам)?\b)'  # excludes "барбершоп"
_BRANDS = (r'porland|tognana|kutahya|k[uü]tahya|wilmax|ariane|steelite|rak porcelain|bonna|lubiana|fine ?dine'
           r'|corone|narumi|sch[oö]nwald|arcoroc|pasabah[cç]e|chef ?& ?sommelier|p\.?l\.? ?proff|libbey|churchill')
DEALER_MARKERS = (r'%s[^\n]{0,40}%s|(horeca|хорека)[^\n]{0,40}(%s|поставк|снабжени|оснащени)'
                  r'|снабжени\w*[^\n]{0,30}(ресторан|horeca|хорека|общепит|кафе)'
                  r'|поставщик\w*[^\n]{0,30}(%s|horeca|хорека|ресторан)'
                  r'|(дилер|дистрибьют|представител)\w*[^\n]{0,40}(%s|%s|horeca|хорека)'
                  r'|(%s)[^\n]{0,60}(horeca|хорека|ресторан|дилер|представител|поставк)'
                  % (_GOODS, _FOR, _GOODS[1:-1], _GOODS[1:-1], _GOODS[1:-1], _BRANDS, _BRANDS))

DEFAULTS = [
    {'code': 'pastry_chef', 'title': 'Шеф-кондитер', 'enabled': True, 'priority': 10,
     'role_markers': r'^(?=.*(%s|кондитер|pastry))(?=.*(%s))' % (CHEF_MARKERS, PASTRY_WORDS),
     'exclude_markers': r'бистро|bistro|ресторан|restaurant|гастробар|кафе|cafe',
     'stop_markers': r'торт.{0,12}на заказ|десерт.{0,12}на заказ|домашн\w* кондитер'
                     r'|семейн\w* кондитерск|на дому',
     'venue_levels': ['PREMIUM'], 'needs_feed': True, 'needs_external_proof': False,
     'grader_prompt': PASTRY_PROMPT, 'reference_top': ['chef_amber_', 'jasper.kale'],
     'reference_bottom': ['jasper1_kale1_lotus1'], 'crm_role': 'Шеф Повар', 'crm_position': 'Шеф'},
    {'code': 'chef', 'title': 'Шеф-повар', 'enabled': True, 'priority': 20,
     'role_markers': CHEF_MARKERS, 'exclude_markers': '',
     'stop_markers': r'travel ?chef|выездн\w* (шеф|повар)|частн\w* (повар|шеф)|private ?chef'
                     r'|преподава',
     'venue_levels': ['PREMIUM', 'UPPER-MIDDLE'], 'needs_feed': True,
     'needs_external_proof': False, 'grader_prompt': CHEF_PROMPT,
     'reference_top': ['chef_amber_', 'jasper.kale'], 'reference_bottom': ['jasper1_kale1_lotus1'],
     'crm_role': 'Шеф Повар', 'crm_position': 'Шеф'},
    # 2026-10-13, client: "enable restaurateurs only for the НСК department, East region" —
    # 603 of them in one day out of 1,118 passing the initial filter; ownership is
    # confirmed in the grader prompt (press, legal entity, feed)
    {'code': 'restorator', 'title': 'Ресторатор / собственник', 'enabled': True, 'priority': 30, 'departments': ['НСК'],
     'role_markers': r'ресторатор|restaurateur|владел|owner|собственник|основател|founder|сооснов',
     'exclude_markers': '', 'stop_markers': r'будущ\w* ресторатор|future owner',
     'venue_levels': ['PREMIUM', 'UPPER-MIDDLE'], 'needs_feed': False,
     'needs_external_proof': True,
     'grader_prompt': 'Ресторатор или собственник конкретных заведений. Кухонная лента не '
                      'нужна; личная лента не повод для отказа, если роль и заведения '
                      'подтверждены.',
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Ресторатор',
     'crm_position': 'Ресторатор'},
    {'code': 'director', 'title': 'Управляющий / директор', 'enabled': False, 'priority': 40,
     'role_markers': r'управляющ|директор ресторан|операционн\w* директор|f&b|general manager',
     'exclude_markers': '', 'stop_markers': '',
     'venue_levels': ['PREMIUM', 'UPPER-MIDDLE'], 'needs_feed': False,
     'needs_external_proof': False, 'grader_prompt': 'Управляющий или директор заведения.',
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Директор',
     'crm_position': 'Директор'},
    {'code': 'purchaser', 'title': 'Закупщик / шеф-стюард', 'enabled': False, 'priority': 5,
     'role_markers': r'закупщ|закупк|снабжен|шеф.?стюард|purchas|procurement',
     'exclude_markers': '', 'stop_markers': '',
     'venue_levels': ['PREMIUM', 'UPPER-MIDDLE'], 'needs_feed': False,
     'needs_external_proof': False,
     'grader_prompt': 'Закупщик или шеф-стюард: решает по посуде и сервировке.',
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Закупщик',
     'crm_position': 'Закупщик'},
    # 2026-09-30, client: "turn on consulting search for the East region (menu launches,
    # turnkey restaurant launches)". A consultant has no venue of their own — level isn't
    # checked; НСК department only.
    {'code': 'consulting', 'title': 'Ресторанный консалтинг', 'enabled': True, 'priority': 50,
     'role_markers': r'ресторанн\w* консалт|консалт\w*[^\n]{0,40}(меню|ресторан|кухн|horeca|хорека|общепит)'
                     r'|(ресторанн\w*|шеф)[- ]консультант|консультант по (ресторан|кухн|меню|открыт)'
                     r'|разработк\w* меню|menu development|restaurant consult|аудит\w* (ресторан|кухн)'
                     r'|запуск\w* (ресторан|заведен|кухн|меню|гастро)|запускаю рестораны'
                     r'|(ресторан|заведен|кухн)\w*[^\n]{0,25}под ключ|под ключ[^\n]{0,25}(ресторан|заведен|кухн)'
                     r'|открыти\w* ресторан|открыва\w* ресторан|ресторан\w* с нуля',
     'exclude_markers': r'коуч|маркетолог|маркетинг|smm|таргет|франшиз|психолог|нутрициолог|бьюти|beauty'
                        r'|\bавто|44-фз|223-фз|закупк|бизнес-консультант|\bit-|дизайн|интерьер|критик',
     'stop_markers': '',
     'venue_levels': [], 'departments': ['НСК'], 'needs_feed': False,
     'needs_external_proof': False,
     'grader_prompt': ('Ресторанный консультант: запускает рестораны под ключ, разрабатывает меню, ставит кухню, '
                       'делает аудит; своего заведения может не быть — это не повод для отказа. Нужны реальные '
                       'проекты-заведения (подачи, открытия, кухни) в ленте или шапке. Отклоняй: онлайн-курсы '
                       'для всех желающих, коучей, маркетологов и SMM без кухни, продавцов франшиз, фуд-блогеров.'),
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Консалтинг',
     'crm_position': 'Консультант'},
    # 2026-10-08, client: "collect dealers for the East region, НСК — people who supply
    # restaurants with HoReCa tableware". A dealer has no venue or food-plating shots of
    # their own: level isn't checked, no feed needed; НСК department only.
    {'code': 'dealer', 'title': 'Дилер посуды HoReCa', 'enabled': True, 'priority': 60,
     'role_markers': DEALER_MARKERS,
     # specifically tableware and equipment supply: food products, beauty, geotextiles are out (dry run 2026-10-08)
     'exclude_markers': r'для дома|домашн\w* посуд|toyota|lada|kia|hyundai|автосалон|\bавто|недвижим|косметик'
                        r'|продукт\w* питани|продуктов|пищев|салон\w* красот|барбершоп|геотекстил',
     'stop_markers': '',
     'venue_levels': [], 'departments': ['НСК'], 'needs_feed': False, 'needs_external_proof': False,
     'grader_prompt': ('Дилер, поставщик или менеджер по снабжению ресторанов посудой, фарфором, стеклом, '
                       'инвентарём для HoReCa. Своего заведения и подач в ленте у него нет — это не повод для '
                       'отказа; в ленте — посуда, сервировка, проекты оснащения заведений. Отклоняй: магазины '
                       'посуды для дома, маркетплейсы, сувениры, дилеров не из HoReCa.'),
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Дилер', 'crm_position': 'Дилер'},
    {'code': 'bar', 'title': 'Бармен / бар-менеджер (пауза с 16.09.2026)', 'enabled': False,
     'priority': 70,
     'role_markers': r'бармен|бар.?менеджер|сомелье|bartender|sommelier|barman',
     'exclude_markers': '', 'stop_markers': '',
     'venue_levels': ['PREMIUM', 'UPPER-MIDDLE'], 'needs_feed': True,
     'needs_external_proof': False, 'grader_prompt': 'Бармен или бар-менеджер бара.',
     'reference_top': [], 'reference_bottom': [], 'crm_role': 'Бар Менеджер',
     'crm_position': 'Бармен'},
]

COLUMNS = ('code', 'title', 'enabled', 'priority', 'role_markers', 'exclude_markers',
           'stop_markers', 'venue_levels', 'needs_feed', 'needs_external_proof',
           'grader_prompt', 'reference_top', 'reference_bottom', 'crm_role', 'crm_position',
           'departments')


def ensure_defaults(main):
    added = 0
    for p in DEFAULTS:
        cur = main.execute(
            'INSERT INTO role_profiles (%s) VALUES (%s) ON CONFLICT (code) DO NOTHING'
            % (', '.join(COLUMNS), ', '.join(['%s'] * len(COLUMNS))),
            [p.get(c, []) if c == 'departments' else p[c] for c in COLUMNS])
        added += cur.rowcount
    return added


def load_enabled(main):
    return main.execute(
        'SELECT * FROM role_profiles WHERE enabled ORDER BY priority, code').fetchall()


def set_enabled(main, code, enabled):
    cur = main.execute('UPDATE role_profiles SET enabled = %s, updated_at = now() WHERE code = %s',
                       (bool(enabled), code))
    return cur.rowcount == 1


def _search(pattern, text):
    return bool(pattern) and re.search(pattern, text, re.I | re.S) is not None


def match(profiles, category, bio):
    blob = '%s %s' % (category or '', bio or '')
    # bartenders and sommeliers are on pause: strip bar roles while the bar profile is
    # disabled, otherwise "шеф-сомелье" passes on the bare "шеф"
    bar_on = any(p['code'] == 'bar' for p in profiles)
    for p in profiles:
        text = blob if (p['code'] == 'bar' or bar_on) else quality.BAR_ROLES.sub(' ', blob)
        if not _search(p['role_markers'], text):
            continue
        if _search(p['exclude_markers'], text):
            continue
        if _search(p['stop_markers'], text):
            return p, 'стоп-маркер должности «%s»' % p['title']
        return p, None
    return None, 'должности нет в шапке профиля'

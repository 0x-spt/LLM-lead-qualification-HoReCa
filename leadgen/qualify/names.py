# -*- coding: utf-8 -*-
"""First and last name of a person, extracted from the profile's full_name.

Chefs write anything into their Instagram full_name: "БРЕНД-ШЕФ • РЕЦЕПТЫ •
МОСКВА", "Алексей Ершов — шеф-повар из Казани", "ALEXSANDR KIM". The output
table needs exactly two columns, so the string is split into tokens, role
words, city words and promo words are dropped, and what's left is checked for
"two words that look like a name". The city is dropped only if two words still
remain afterward — otherwise "Владимир Соколов" would lose its first name.
"""
import re
import unicodedata

ROLE = {'шеф','повар','шефповар','бренд','брендшеф','брэндшеф','кондитер','шефкондитер',
        'сушеф','су','chef','chefs','sous','pastry','brand','head','executive','cook',
        'ресторатор','основатель','founder','owner','coowner','блогер','блог','food',
        'foodblogger','blogger','stylist','повара'}
CITY = {'москва','мск','moscow','питер','спб','казань','казани','сочи','краснодар','ростов',
        'самара','тула','уфа','пермь','воронеж','саратов','ярославль','владимир','нижний',
        'новгород','нн','геленджик','анапа','ставрополь','пятигорск','махачкала','нальчик',
        'липецк','белгород','рязань','тверь','ульяновск','астрахань','одесса','минск','киев',
        'астана','ташкент','баку','dubai','russia','italy','крым','россия','сибирь','урал','кавказ'}
JUNK = {'рецепты','рецепт','семья','обучение','курсы','мастеркласс','меню','кухня','кухни',
        'авторская','доставка','заказ','mini','official','личный','гранд','the','of','and',
        'из','в','по','на','с','man','faith','life','love','my','me','it','team',
        # signage words: a "name surname" can't be built from these
        'кейтеринг','catering','банкет','банкеты','фуршет','свадебный','свадебные','свадьба',
        'подарки','подарок','бенто','торты','торт','десерты','десерт','пирожные','сладости',
        'студия','studio','агентство','сервис','service','обзоры','обзор','блог','влог',
        'веган','пп','питание','выездной','выездная','площадка','персонал','консьерж',
        'клубника','кофе','десертов','кондитерская','пекарня','эксперты','от','для',
        'фуршеты','фуршет','бар','bar','шоу','проект','компания','группа','дом',
        # 2026-10-06: handles "Ресторанный Консультант", "Work Кулинарный" instead of a name
        'ресторанный','консультант','консалтинг','кулинарный','кулинар','work','работа'}
WORD = re.compile(r"^[А-ЯЁA-Z][а-яёa-z'\-]{1,}$")

_TR = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'j','з':'z','и':'i',
       'й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t',
       'у':'u','ф':'f','х':'h','ц':'c','ч':'c','ш':'s','щ':'s','ъ':'','ы':'i','ь':'',
       'э':'e','ю':'u','я':'a'}


def _tr(w):
    w = w.lower()
    w = ''.join(_TR.get(c, c) for c in w)
    return re.sub(r'[^a-z]', '', w).replace('kh', 'h').replace('ck', 'k')


# Instagram returns some names in NFD: "й" arrives as "и" + U+0306, and
# without normalization "Сойда" turns into "Соида".
NAMECITY = {'владимир'}          # cities that are also used as first names


def person_name(full):
    # NFKC: handles NFD names ("Сойда" = "Сои" + U+0306) and the bold math
    # unicode style chefs sign with ("𝐓𝐢𝐦𝐮𝐫 𝐀𝐳𝐢𝐦𝐨𝐯")
    s = unicodedata.normalize('NFKC', full or '')
    s = re.sub(r'[^\w\s\-\']', ' ', s, flags=re.UNICODE)
    s = re.sub(r'[_\d]+', ' ', s)
    toks = [t for t in s.split() if len(t) > 1]
    keep = [t for t in toks if t.lower().replace('-', '') not in ROLE | JUNK]
    nocity = [t for t in keep if t.lower() not in CITY]
    if len(nocity) >= 2:
        words = nocity
    elif any(t.lower() in NAMECITY for t in keep) and \
            all(t.lower() in NAMECITY for t in keep if t.lower() in CITY):
        words = keep          # "Владимир Соколов" — here "Владимир" is a first name; "Владимир Москва" — it isn't
    else:
        return None           # only one word left: no surname
    out = []
    for w in words:
        c = w.capitalize() if (w.isupper() or w.islower()) else w
        if WORD.match(c):
            out.append(c)
        if len(out) == 2:
            break
    if len(out) != 2:
        return None
    # "Davronbek Давронбек" — the same word in two languages, no surname
    if _tr(out[0]) == _tr(out[1]):
        return None
    return out


if __name__ == '__main__':
    tests = ['Андрей Тихонов   Шеф-повар   Сочи', 'Алексей Ершов — шеф-повар из Казани',
             'ALEXSANDR KIM', 'БРЕНД-ШЕФ • РЕЦЕПТЫ • МОСКВА', 'ГРАНД ВЛАДИМИР БРЕНД-ШЕФ',
             'Андрей', 'Chef.Zhanna', 'Elen-Maria KishMaria', 'Alina Lee Seong Kuin',
             'Никита Носков   Шеф   Одесса', 'Krainova_Chef_', 'Said_man of faith',
             'Милана  Шеф-повар', 'Davide_', 'Parvin Aliyeva Pastry Chef',
             'Владимир Соколов ШЕФ ПОВАР', 'Daria Kovalenko   Mini Chef',
             'Аристов Александр', 'Николай Курнев']
    for t in tests:
        print('%-40s -> %s' % (t, person_name(t)))


# --- Venue name from its Instagram account name (2026-10-01): owners pack it with
# SEO tails — "Chowara | Ресторан Азиатской Кухни | Уфа", "Рестораны il FORNO в Москве:".

# •Родное гнездо• — markers hugging the words act like quotes; "X • Y • Z" is a list
_QUOTED = re.compile(r'[«"“„]([^»"”“]{2,60})[»"”]|•(\S[^•]{0,58}\S)•')
_SEPARATORS = re.compile(r'\s*(?:\||•|·|∙|/|:|\.\s(?=[А-ЯЁ])|\s[—–-]\s)\s*')
_BRACKETS = re.compile(r'\s*[\[(][^\])]*[\])]')
# "Ресторан X" -> X; plural "Рестораны Раппопорта" is a chain name — leave it alone
_GENERIC_HEAD = re.compile(r'^(?:ресторан|restaurant|кафе|cafe|бар|гастробар|ресторан-бар)\s+(?=\S)', re.I)
_GENERIC_PLURAL = re.compile(r'^рестораны\s+(?=[A-Za-z0-9])', re.I)  # "Рестораны il FORNO" — a chain with a Latin name
_GENERIC_ONLY = re.compile(r'^(?:рестораны?|restaurant|кафе|cafe|бар|бистро|bistro|гостиница|отель)$', re.I)
_IN_CITY = re.compile(r'\s+(?:в|in)\s+[А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)?\s*$')


def _tidy(text):
    text = unicodedata.normalize('NFC', text)
    text = ''.join(ch for ch in text if ch.isalnum() or ch in " .,&'’-+_" or unicodedata.category(ch) == 'Mn')
    return re.sub(r'\s+', ' ', text).strip(" .,-'’")


def _city_names():
    from leadgen.sources import gis_cities
    return sorted({c for _, c in gis_cities.CITIES}, key=len, reverse=True)


def _strip_city(name):
    for city in _city_names():
        if name.lower().endswith(' ' + city.lower()) and len(name) > len(city) + 1:
            return name[:-len(city) - 1].strip()
    return name


def clean_venue_name(raw):
    """Short venue name with promo text, city and the word "Ресторан" stripped out."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    quoted = _QUOTED.search(raw)
    if quoted:
        return _tidy(quoted.group(1) or quoted.group(2))
    raw = _BRACKETS.sub('', raw)
    parts = [_tidy(p) for p in _SEPARATORS.split(raw)]
    parts = [p for p in parts if p]
    if not parts:
        return _tidy(raw)
    candidates = []
    for p in parts:
        if _GENERIC_ONLY.match(p):
            continue
        rest = _GENERIC_PLURAL.sub('', _GENERIC_HEAD.sub('', p))
        if rest != p and rest == rest.lower() and len(parts) > 1:
            continue  # "Ресторан сенсорной кухни" — a description, the name is further along
        candidates.append(rest)
    name = candidates[0] if candidates else parts[0]
    name = _IN_CITY.sub('', name).strip()
    name = _strip_city(name)
    return name or parts[0]

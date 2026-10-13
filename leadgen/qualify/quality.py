import re
import time

# foodOk, ported from db/tools/bridge.py: a required POSITIVE sign of food service.
# Searched across the whole bio (city, address, bio text, category, name), not just
# the category — half of venues have an empty category, and without this "Волна"
# would also match a fitness club.
FOOD_EVIDENCE = re.compile(
    r'ресторан|бар|кафе|бистро|кухн|меню|бронь|пицц|винотек|гастро|отель|завтрак'
    r'|пекарн|кофейн|ужин|обед|доставка|restaurant|cafe|bar|food|bistro|kitchen', re.I)

# 183 days — client decision from 2026-09-22, the window was widened from four
# months to six. The same number is hardcoded in db/tools/last_post3.py (WINDOW);
# if one changes, the other must too, or the standalone tool and the pipeline
# will disagree in their scoring.
FRESHNESS_WINDOW_DAYS = 183

PERSON_NAME = re.compile(r'^[А-ЯЁA-Z][а-яёa-z]+[\s.]+[А-ЯЁA-Z][а-яёa-z]+')

# Bar direction on pause since 2026-09-16 (client decision): bartenders, bar
# managers, sommeliers and bar owners are not being collected. So bar-related
# words are removed from BOTH regexes — from ROLE_MARK, to avoid spending
# requests on them, and from ROLE, so they don't get "на_проверке" (pending
# review) status and end up in the export.
# To bring the direction back, add here:
#   to ROLE_MARK — somm|barman|bartender|бармен|сомелье
#   to ROLE      — сомелье|sommelier|бар[-\s]?менеджер
# "A bar's chef is still a chef": constructions with the word "шеф" (бренд-шеф,
# шеф-повар, шеф-бармен, шеф-кондитер) are unaffected by the pause and pass as before.
ROLE_MARK = re.compile(
    r'chef|shef|chief|povar|cook|sous'
    r'|шеф|повар|кондитер', re.I)

ROLE = re.compile(
    r'шеф[-\s]?повар|су[-\s]?шеф|sous\s?chef|бренд[-\s]?шеф|брэнд[-\s]?шеф'
    r'|концепт[-\s]?шеф|executive\s?chef|brand\s?chef|head\s?chef'
    r'|chef\s?de\s?cuisine|шеф-бармен|шеф-кондитер|ресторатор'
    r'|управляющ|директор\s?рестора|владел|owner|co-?owner|основател|сооснова'
    r'|founder|закупщ'
    # Single-word roles. The ROLE_MARK pre-filter lets these through and spends
    # a request on them (a handle with "chef" or "cook", a bio of just "Повар"
    # or "Кондитер"), while ROLE without them used to reject: "Chef | Kazan",
    # "Шеф Волна", "Sous chef", "Повар" — all False.
    r'|шеф|chef|cook|повар|кондитер', re.I)

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'iu',
    'я': 'ia',
}


def _norm(s):
    """Letters and digits only, lowercased. Ported from nz() in db/tools/bridge.py —
    there too, punctuation and spaces don't take part in comparing handles/names."""
    s = (s or '').lower().replace('ё', 'е')
    return re.sub(r'[^a-zа-я0-9]', '', s)


def _translit(s):
    """Russian -> Latin, character by character. Ported from tr() in db/tools/bridge.py —
    needed so "Чердак" is found in the Latin handle cherdak_..., not only in a Cyrillic bio."""
    s = (s or '').lower()
    return ''.join(TRANSLIT.get(ch, ch) for ch in s)


def name_matches(username, full_name, venue_name):
    """Cheap name check BEFORE fetching the profile — ported from db/tools/bridge.py.
    A venue name shorter than 3 characters after normalization isn't searched for at
    all: "Р" would match almost anything."""
    nv = _norm(venue_name)
    tv = _norm(_translit(venue_name))
    if len(nv) < 3:
        return False
    un, fn = _norm(username), _norm(full_name)
    if any(needle and needle in hay for needle in (nv, tv) for hay in (un, fn)):
        return True
    # a long venue name can exceed the handle's length: "Фиорентина" -> @fiorent
    return len(nv) > 5 and un in nv and len(un) > 4


def bridge_verify(username, full_name, profile, venue_name, venue_city):
    """3-check bridge, ported from db/tools/bridge.py: the name matches (directly or via
    transliteration), the bio has a sign of food service, the city matches (or the name
    matches exactly) and doesn't conflict with the city set on the account."""
    if not name_matches(username, full_name, venue_name):
        return False, 'название не совпало'

    city_name = profile.get('city_name') or ''
    blob = ' '.join([
        city_name,
        profile.get('address_street') or '',
        profile.get('biography') or '',
        profile.get('category') or '',
        full_name or '',
    ]).lower()

    food_ok = bool(FOOD_EVIDENCE.search(blob))
    city = venue_city or ''
    # as in bridge.py: check both the Cyrillic and Latin city prefix — an
    # account's city_name is often written in Latin script ("Novosibirsk")
    city_ok = city.lower()[:5] in blob or _translit(city).lower()[:5] in blob

    nv, tv = _norm(venue_name), _norm(_translit(venue_name))
    un, fn = _norm(username), _norm(full_name)
    exact = un in (nv, tv) or fn in (nv, tv)

    has_city = len(city_name) > 2
    city_conflict = has_city and not city_ok

    if not food_ok:
        return False, 'нет признака общепита'
    if not (city_ok or exact):
        return False, 'город не совпал'
    if city_conflict:
        return False, 'конфликт города'
    return True, 'ок'


def is_fresh(taken_at_unix):
    """Freshness from taken_at in media/{id}/info/. Ported from db/tools/last_post.py.
    None means the date couldn't be read — a non-blocking rejection."""
    if not taken_at_unix:
        return None
    cutoff = time.time() - FRESHNESS_WINDOW_DAYS * 86400
    return taken_at_unix >= cutoff


def looks_like_person(username, full_name):
    """Cheap pre-filter for the following walk: looks like a personal account rather
    than a venue/public page. Ported from MARK/PERSON in db/tools/venue_walk.py."""
    fn = (full_name or '').strip()
    return bool(ROLE_MARK.search(username or '') or ROLE_MARK.search(fn) or PERSON_NAME.match(fn))


# Bar roles: direction on pause since 2026-09-16 (bartenders, bar managers,
# sommeliers, bar owners). Stripped from the bio BEFORE the role check,
# otherwise "шеф-сомелье" would pass on the bare "шеф" — which is exactly
# what happened on 09-22. Important: we strip the bar role words themselves,
# not the word "бар" — a bar's chef stays a chef, and "бренд-шеф гриль-бара"
# must still pass.
BAR_ROLES = re.compile(
    r'шеф[-\s]?сомелье|шеф[-\s]?бармен|бар[-\s]?менеджер|бар[-\s]?тендер'
    r'|сомелье|sommelier|бармен|barman|bartender', re.I)


def has_role(bio, full_name, category, city_name):
    """Whether the profile states a role. Ported from ROLE in
    db/tools/venue_walk.py — checked against the whole known bio."""
    blob = ' '.join([bio or '', full_name or '', category or '', city_name or ''])
    # We strip bar roles rather than check for them separately: this way
    # "шеф-сомелье" no longer passes on the bare "шеф", but "бренд-шеф и
    # шеф-сомелье" still passes, because a real chef role remains after stripping.
    return bool(ROLE.search(BAR_ROLES.sub(' ', blob)))


STREET_NOISE = re.compile(r'\b(улица|ул|проспект|пр-т|пр|переулок|пер|бульвар|б-р|шоссе|площадь|пл|набережная|наб|'
                          r'микрорайон|мкр|проезд|тракт|строение|стр|корпус|к|этаж|дом|д)\b\.?', re.I)


def _street_words(address):
    """Meaningful street words from a 2GIS address: "улица Николая Машарова, 2/1" -> {машарова, николая}."""
    street = (address or '').split(',')[0]
    street = STREET_NOISE.sub(' ', street)
    return {w.lower().replace('ё', 'е') for w in re.findall(r'[А-Яа-яЁёA-Za-z-]{5,}', street)}


def bridge_verify_web(username, full_name, bio, venue_name, venue_city, venue_address, main):
    """Bridge via the profile's web page (the profile API is blocked): there are no
    business fields, so the city is confirmed via the bio — either the city name or a
    street from the 2GIS address. Any other city in the bio is a rejection (the
    three-check rule)."""
    from leadgen.qualify import geo
    if not name_matches(username, full_name, venue_name):
        return False, 'название не совпало'
    blob = '%s %s' % (bio or '', full_name or '')
    if not FOOD_EVIDENCE.search(blob):
        return False, 'нет признака общепита'
    found = geo.find_city(main, blob)
    if found and found != venue_city:
        return False, 'конфликт города'
    low = blob.lower().replace('ё', 'е')
    street_ok = any(w in low for w in _street_words(venue_address))
    if found != venue_city and not street_ok:
        return False, 'город не подтверждён шапкой'
    return True, 'ок'

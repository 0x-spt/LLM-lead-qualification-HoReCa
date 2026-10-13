"""Code-based selection rules — the free stage of the funnel.

Ported from db/tools/accept.py (version 2026-09-23) with two differences:
territory is decided by the venue city's department (geo), not a blocklist of
cities in one region; role is decided by a profile from role_profiles.

A venue not found or with an unknown level is NOT a rejection: such a contact
moves on with needs_strong_profile, and only a reference-grade feed can let it
through (rule from 2026-09-14). A level found but below the bar is a rejection."""
import re

from leadgen.qualify import quality

VENUE_CAT = re.compile(r'Ресторан|^Бар$|Кафе|Cafe|Гостиниц|Отель|Пиццери|Суши|Кофейн'
                       r'|Пекарн|Food|Restaurant|Бистро|Паб|Винн|Сообщество|Компания'
                       r'|Магазин|Продукт|Коктейль|Служба доставки|Сеть|Товар/услуга'
                       r'|Местная компания|Блюда', re.I)
VENUE_NAME = re.compile(r'ресторан|гастробар|отель|кафе|бистро|кондитерская|пекарн'
                        r'|trattoria|pizzeria|grill|seafood|kitchen|винотек|студия'
                        r'|доставка|школа|обучение|посуд|сервис бар|horeca|хорека|оборудован',
                        re.I)
STOP_FORMAT = re.compile(r'шашлы|мангал|чайхан|пельмен|блинн|пончик|бургерн|пиццери'
                         r'|донер|гирос|хот.?дог|фуд.?трак|фастфуд|фаст.?фуд|фуд.?корт'
                         r'|корнер|гастро.?корт|столов|шаурм|дарк.?китчен|суши.?маркет'
                         r'|кальян|супермаркет', re.I)
NOT_HORECA = re.compile(r'торт.{0,12}на заказ|десерт.{0,12}на заказ|на дому'
                        r'|кулинарн.{0,6}школ|школа поваров|курсы|foodbox|фуд.?бокс'
                        r'|доставка еды|рацион|кейтеринг|catering|банкет|фуршет|свадеб'
                        r'|бенто|кондитерск(ая|ую|ой) студи|подарк|персонал'
                        r'|правильн\w* питани|\bпп\b|домашн\w* кондитер'
                        r'|обзор\w* рестора|food ?blog|фуд.?блог', re.I)
# for a consultant, "staff training", "courses", "school" are part of the job; only filter out non-HoReCa
NOT_HORECA_CONSULT = re.compile(r'торт.{0,12}на заказ|десерт.{0,12}на заказ|на дому|foodbox|фуд.?бокс'
                                r'|доставка еды|рацион|правильн\w* питани|\bпп\b|домашн\w* кондитер'
                                r'|food ?blog|фуд.?блог', re.I)
PLACE_CAT = re.compile(r'Ресторан|^Бар$|Кафе|Cafe|Гостиниц|Отель|Пиццери|Суши|Кофейн|Пекарн|Restaurant'
                       r'|Бистро|Паб|Винн', re.I)
NOT_NOW = re.compile(r'ex-chef|бывш|учусь|студент|future owner|ищу работу|в поиске работы', re.I)
CAFE = re.compile(r'кафе|cafe|кофейн|coffee', re.I)

WINDOW_DAYS = quality.FRESHNESS_WINDOW_DAYS


def precheck(profile, role, role_stop):
    """Free rules — applied before any request to Instagram. Returns (ok, reason)."""
    full = profile.get('full_name') or ''
    bio = profile.get('biography') or ''
    cat = profile.get('category') or ''
    blob = cat + ' ' + bio
    if profile.get('is_private'):
        return False, 'закрытый профиль'
    if (profile.get('media_count') or 0) < 3:
        return False, 'меньше трёх постов'
    consulting = bool(role) and role.get('code') in ('consulting', 'dealer')
    dealer = bool(role) and role.get('code') == 'dealer'
    # consultants and dealers have no venue of their own: only an actual restaurant
    # counts as a "venue signage" match for them; "посуда HoReCa" in a dealer's name
    # IS their profile (2026-10-08)
    if (PLACE_CAT.search(cat) if consulting else (VENUE_CAT.search(cat) or VENUE_NAME.search(full))):
        return False, 'аккаунт заведения, а не человека'
    if not dealer and STOP_FORMAT.search(blob):
        return False, 'формат из стоп-листа'
    if (NOT_HORECA_CONSULT if consulting else NOT_HORECA).search(full + ' ' + bio):
        return False, 'не заведение: заказы на дом, курсы, кейтеринг или доставка'
    if NOT_NOW.search(blob):
        return False, 'сейчас не работает в заведении'
    if role is None:
        return False, role_stop or 'должности нет в шапке профиля'
    if role_stop:
        return False, role_stop
    return True, None


def evaluate(profile, role, role_stop, venue, fresh_days, city, department):
    ok, reason = precheck(profile, role, role_stop)
    if not ok:
        return False, reason, False
    if fresh_days is None:
        return False, 'свежесть не определена: постов не видно', False
    if fresh_days > WINDOW_DAYS:
        return False, 'не активен больше %d дней' % WINDOW_DAYS, False
    # city is known but not ours -> rejection; no city at all -> a human decides (cap_reason)
    if city and not department:
        return False, 'город «%s» вне территорий' % city, False
    only = role.get('departments') or []
    if only and department and department not in only:
        return False, '%s ищем только в отделе %s' % (role.get('title') or role.get('code'), ', '.join(only)), False
    if not (role.get('venue_levels') or []):
        # role without a venue (consulting): skip the level check, "no venue" isn't a doubt
        return True, None, False
    premium_only = (role.get('venue_levels') or []) == ['PREMIUM'] and role.get('code') == 'pastry_chef'
    level_known = bool(venue and (venue.get('level') or '').strip())
    if premium_only and (not level_known or venue['level'].strip().upper() != 'PREMIUM'):
        return False, 'шеф-кондитер: заведение не PREMIUM', False
    if venue:
        vtext = '%s %s' % (venue.get('name') or '', venue.get('category') or '')
        if STOP_FORMAT.search(vtext):
            return False, 'заведение из стоп-листа форматов', False
        level = (venue.get('level') or '').strip().upper()
        if level:
            if CAFE.search(vtext) and level != 'PREMIUM':
                return False, 'кафе или кофейня не PREMIUM', False
            if level not in (role.get('venue_levels') or []):
                return False, 'заведение уровня %s — ниже планки для роли' % level, False
            return True, None, False
    return True, None, True

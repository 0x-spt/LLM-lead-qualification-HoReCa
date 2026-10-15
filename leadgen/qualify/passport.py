"""Contact passport (2026-10-06): geo, abroad flag, duplicates — built after the client
reviewed an export.

Tashkent, Astana, Kostanay, Aktobe, Karaganda, Tallinn had been ending up as "Москва":
the city used to be searched across the whole bio text together with @handles (a chef
from Tashkent with @moscow.cafe1.uz produced "Москва") and across the full name
("Artem Petrov" -> city Артём). Here the city comes
only from the bio's prose; "abroad" comes from city/country names, domains and flags,
including inside @handles (@harbor1_aktobe, @willow1.kz); duplicates are accounts that
reference each other, or the same name at the same venue.
"""
import re

from leadgen.qualify import bio_roles, geo
from leadgen.core import contacts

HANDLE = re.compile(r'@[\w.]+|https?://\S+|\b[\w.-]+\.(?:ru|com|рф|kz|uz|by|io|me)\b', re.I)
# city/country -> spellings seen in bios and handles (Cyrillic, Latin)
FOREIGN = {
    'Ташкент': ('ташкент', 'tashkent', 'toshkent'), 'Самарканд': ('самарканд', 'samarkand'),
    'Узбекистан': ('узбекистан', 'uzbekistan'),
    'Алматы': ('алматы', 'алма-ата', 'almaty'), 'Астана': ('астана', 'astana', 'нур-султан'),
    'Караганда': ('караганд', 'karaganda'), 'Костанай': ('костанай', 'kostanay', 'kostanai'),
    'Актобе': ('актобе', 'aktobe', 'aqtobe'), 'Шымкент': ('шымкент', 'shymkent'), 'Атырау': ('атырау', 'atyrau'),
    'Павлодар': ('павлодар', 'pavlodar'), 'Казахстан': ('казахстан', 'kazakhstan'),
    'Бишкек': ('бишкек', 'bishkek'), 'Кыргызстан': ('кыргызстан', 'kyrgyzstan'),
    'Минск': ('минск', 'minsk'), 'Беларусь': ('беларус', 'belarus'), 'Брест': ('брест', 'brest'),
    'Таллин': ('таллин', 'tallinn'), 'Рига': ('рига', 'riga'), 'Вильнюс': ('вильнюс', 'vilnius'),
    'Тбилиси': ('тбилиси', 'tbilisi'), 'Батуми': ('батуми', 'batumi'), 'Грузия': ('грузия', 'georgia'),
    'Ереван': ('ереван', 'yerevan'), 'Баку': ('баку', 'baku'),
    # Ukraine (2026-10-09): the country matches only as a noun ("украинская кухня",
    # "Украинский дворик" in Gelendzhik is not a place name); cities exclude "Николаев"
    # and "Львов" (also a surname/patronymic)
    'Украина': ('украина', 'украине', 'украины', 'україн', 'ukraine'),
    'Киев': ('киев', 'київ', 'києв', 'kyiv', 'kiev'), 'Харьков': ('харьков', 'харків', 'kharkiv'),
    'Одесса': ('одесс', 'одеса', 'одесі', 'odesa', 'odessa'), 'Днепр': ('дніпр', 'dnipro'),
    'Львов': ('львів', 'львові', 'lviv'), 'Запорожье': ('запорож', 'запоріж', 'zaporizh'),
    'Винница': ('винниц', 'вінниц', 'vinnytsia'), 'Ивано-Франковск': ('франковск', 'франківськ', 'frankivsk'),
    'Ужгород': ('ужгород', 'uzhhorod'), 'Черкассы': ('черкас', 'cherkasy'), 'Житомир': ('житомир', 'zhytomyr'),
    'Тернополь': ('тернопол', 'тернопіл', 'ternopil'), 'Луцк': ('луцьк', 'lutsk'), 'Полтава': ('полтава', 'poltava'),
    'Дубай': ('дубай', 'dubai'), 'Стамбул': ('стамбул', 'istanbul'), 'Бали': ('бали', 'bali'),
    'Пхукет': ('пхукет', 'phuket'), 'Лугано': ('lugano',), 'Кипр': ('кипр', 'cyprus', 'limassol', 'лимассол'),
}
DOMAINS = {'ua': 'Украина', 'kz': 'Казахстан', 'uz': 'Узбекистан', 'by': 'Беларусь', 'ge': 'Грузия', 'am': 'Армения',
           'az': 'Азербайджан', 'ee': 'Эстония', 'kg': 'Кыргызстан', 'ae': 'ОАЭ'}
UA_LETTERS = re.compile('[іїєґІЇЄҐ]')
UA_FLAG = '\U0001F1FA\U0001F1E6'
RU_WORDS = re.compile(r'\b(?:россия|russia|рф)\b', re.I)


def prose(text):
    """Bio with @handles, links and domains stripped — words like "moscow", "aktobe"
    inside those aren't about the city."""
    return HANDLE.sub(' ', text or '')


def bio_city(conn, bio):
    """City from the bio's prose. The full name isn't checked here: "Artem Petrov" is
    a person, not the city Артём."""
    return geo.find_city(conn, prose(bio))


def _foreign(text):
    low = (text or '').lower().replace('ё', 'е')
    found = []
    for place, forms in FOREIGN.items():
        # Latin forms match as a whole word ("rigazzi" isn't Рига, "georgian cuisine"
        # isn't Грузия); Cyrillic forms match with case endings ("в Ташкенте", "караганды")
        hits = sum(len(re.findall(r'(?<![a-zа-я])%s%s' % (re.escape(f), '(?![a-z])' if f.isascii() else ''), low))
                   for f in forms)
        found += [place] * hits
    for tld in re.findall(r'@[\w.]+[._]([a-z]{2})\b', low):  # @juniper_cafe_ua, @willow1.kz
        if tld in DOMAINS:
            found.append(DOMAINS[tld])
    # Ukrainian-only letters і ї є ґ never appear in Russian; one letter is a typo, two or more is the language
    if len(UA_LETTERS.findall(text or '')) >= 2:
        found += ['Украина'] * 2
    # flags normally aren't counted (cuisine, internships), but 🇺🇦 next to work is a place
    found += ['Украина'] * (text or '').count(UA_FLAG)
    # flags aren't counted: 🇮🇹🇬🇪🇫🇷 for chefs usually mean cuisine, internships or origin
    # (measured 2026-10-06: 15 false positives out of 47)
    return found


RU_HANDLE = re.compile(r'(?<![a-z])(?:moscow|msk|spb|piter|kazan|kzn|ekb|nsk|sochi|krd|krasnodar|samara|ufa|'
                       r'perm|tmn|omsk|nnov|rostov|kaliningrad|vladivostok|irkutsk|tomsk|tula)(?![a-z])')


def _russian(conn, text):
    """Signs of Russia — mirroring the abroad check: a city in the prose, "Россия", a
    city inside an @handle (@basil1.moscow, @lumen2.msk). Flags aren't counted here either."""
    found = [c for c in [geo.find_city(conn, prose(text))] if c]
    found += RU_WORDS.findall(prose(text))
    for handle in re.findall(r'@[\w.]+', (text or '').lower()):
        found += RU_HANDLE.findall(handle)
    return found


def abroad(conn, bio):
    """A foreign place from the bio, if the abroad signal outweighs the Russia signal; else None."""
    foreign = _foreign(bio)
    if not foreign or len(foreign) <= len(_russian(conn, bio)):
        return None
    return foreign[0]


def _mentions(bio):
    return {contacts.norm_nick(m) for m in bio_roles.MENTION.findall(bio or '')}


def duplicate_of(stores, nick, bio, name, venue):
    """Handle of an already-collected contact who is the same person: they reference each
    other, or share the same name and venue. None means it's not a duplicate."""
    nick = contacts.norm_nick(nick)
    for other in _mentions(bio) - {nick}:
        row = stores.main.execute('SELECT nick, bio FROM contacts WHERE nick = %s', (other,)).fetchone()
        if row and nick in _mentions(row['bio']):
            return row['nick']
    if name and venue:
        row = stores.main.execute(
            'SELECT nick FROM contacts WHERE lower(name) = lower(%s) AND lower(venue) = lower(%s) AND nick <> %s LIMIT 1',
            (name, venue, nick)).fetchone()
        if row:
            return row['nick']
    if name and ' ' in name.strip():
        # same name and a shared workplace @handle in the bios (the venue field can be empty)
        mine = _mentions(bio) - {nick}
        for row in stores.main.execute('SELECT nick, bio FROM contacts WHERE lower(name) = lower(%s) AND nick <> %s',
                                       (name, nick)).fetchall():
            if mine & (_mentions(row['bio']) - {row['nick']}):
                return row['nick']
    return None

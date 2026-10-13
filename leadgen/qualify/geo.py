import re

"""City -> region -> department (main database). Traps like "Ufa -> MSK" are set
by an explicit row, not derived from the registry's region."""


def upsert_city(conn, city, region, department):
    conn.execute(
        'INSERT INTO geo (city, region, department) VALUES (%s, %s, %s) '
        'ON CONFLICT (city) DO UPDATE SET region = excluded.region, department = excluded.department',
        (city, region, department))


# Latin spellings that transliteration can't reduce back to the Russian name
ALIASES = {'moscow': 'Москва', 'saintpetersburg': 'Санкт-Петербург',
           'stpetersburg': 'Санкт-Петербург', 'petersburg': 'Санкт-Петербург',
           'spb': 'Санкт-Петербург'}


def _skeleton(city):
    """Reduces "Нижний Новгород" and "Nizhniy Novgorod" to the same key: transliterate,
    keep letters only, й/y/j -> i, ё/yo/ye -> e."""
    from leadgen.qualify import quality
    s = quality._translit(city or '').lower()
    s = ''.join(ch for ch in s if ch.isalpha())
    for a, b in (('yo', 'e'), ('ye', 'e'), ('iy', 'i'), ('ii', 'i'), ('y', 'i'), ('j', 'i'),
                 ('kh', 'h'), ('ts', 'c')):
        s = s.replace(a, b)
    return s


def department_for_city(conn, city):
    """Department for a city. Instagram often writes city_name in Latin script
    ("Novosibirsk"), so after an exact match we also compare name "skeletons"."""
    if not city:
        return None
    row = conn.execute('SELECT department FROM geo WHERE city = %s', (city,)).fetchone()
    if row:
        return row['department']
    key = _skeleton(city)
    alias = ALIASES.get(key)
    if alias:
        row = conn.execute('SELECT department FROM geo WHERE city = %s', (alias,)).fetchone()
        return row['department'] if row else None
    if len(key) < 4:
        return None
    for r in conn.execute('SELECT city, department FROM geo').fetchall():
        other = _skeleton(r['city'])
        if other and (other == key or (len(key) >= 7 and other[:7] == key[:7])):
            return r['department']
    return None


CASE_ENDINGS = ('', 'и', 'е', 'а', 'у', 'ом', 'ой', 'ы', 'ь', 'ю', 'я', 'ях', 'ах', 'ем')
WORD = re.compile(r'[a-zа-яё]+', re.I)


def _stem(city):
    c = city.lower().replace('ё', 'е')
    return c[:-1] if c and c[-1] in 'ьаяеоыий' else c


# Landmarks that unambiguously point a bio to Moscow or St. Petersburg even
# without naming the city ("📍Новый Арбат, 21 📍МО", "@barvikha_hotel", "СПб").
LANDMARKS = (
    ('Москва', re.compile(r'(?<![а-яёa-z])(мо|мск|msk)(?![а-яёa-z])|москв|подмосков|барвих|barvikha|рублёв|рублев'
                          r'|новый арбат|арбат|тверская|патриарш|кутузовск|остоженк|пятницк|никольская', re.I)),
    ('Санкт-Петербург', re.compile(r'(?<![а-яёa-z])(спб|spb)(?![а-яёa-z])|питер|петербург|невский|рубинштейн'
                                   r'|петроградск|васильевск', re.I)),
)


def find_city(conn, text):
    city = _find_city_by_name(conn, text)
    if city:
        return city
    known = {r['city'] for r in conn.execute('SELECT city FROM geo').fetchall()} if text else set()
    for name, pattern in LANDMARKS:
        if name in known and pattern.search(text or ''):
            return name
    return None


def _find_city_by_name(conn, text):
    """City from free-text bio: "Шеф-повар Тюмень", "ресторан в Тюмени", "Perm city".
    The profile page doesn't expose city_name, so this is the only source.
    Returns the name as stored in geo, or None."""
    if not text:
        return None
    low = text.lower().replace('ё', 'е')
    cities = [r['city'] for r in conn.execute('SELECT city FROM geo').fetchall()]
    # multi-word names — matched as a substring ("Нижний Новгород", "Южно-Сахалинск")
    for city in cities:
        c = city.lower().replace('ё', 'е')
        if (' ' in c or '-' in c) and c in low:
            return city
    words = WORD.findall(low)
    for w in words:
        for city in cities:
            c = city.lower().replace('ё', 'е')
            if ' ' in c or '-' in c:
                continue
            stem = _stem(city)
            if w == c or (len(stem) >= 4 and w.startswith(stem) and w[len(stem):] in CASE_ENDINGS):
                return city
        if w.isascii() and len(w) >= 4:
            key = _skeleton(w)
            alias = ALIASES.get(key)
            if alias and alias in cities:
                return alias
            for city in cities:
                other = _skeleton(city)
                if other == key or (len(key) >= 7 and other[:7] == key[:7]):
                    return city
    return None

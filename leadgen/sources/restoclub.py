"""Venues from Restoclub ratings and curated lists (2026-10-01, +6 cities on 2026-10-05).

A venue page has a name, an average check (level computed the same way as for 2GIS), and often
an Instagram link. New PREMIUM/UPPER-MIDDLE venues go into the registry: with Instagram, straight
to header and subscriber checks; without it, account lookup by name and city (as with 2GIS).
Already-seen pages are remembered in the archive and never re-fetched.

    deploy/local/run.sh leadgen.sources.restoclub
"""
import html
import re
import time

from leadgen.sources import gis, websearch
from leadgen.core import queue as wq
from leadgen.sources.festivals import fetch as _fetch

BASE = 'https://www.restoclub.ru'
CITIES = {'msk': 'Москва', 'spb': 'Санкт-Петербург', 'ekb': 'Екатеринбург', 'kzn': 'Казань',
          # 2026-10-05: no ratings, only curated lists at /<city>/search/…
          'nsk': 'Новосибирск', 'nn': 'Нижний Новгород', 'krd': 'Краснодар', 'rnd': 'Ростов-на-Дону',
          'sochi': 'Сочи', 'samara': 'Самара'}
LEVELS = ('PREMIUM', 'UPPER-MIDDLE')
SOURCE = 'Restoclub'
OWN_ACCOUNTS = {'restoclub', 'restoclub_ru', 'restoclub.ru'}
NAME = re.compile(r'<h1[^>]*>.*?gap-x-8">\s*([^<]+?)\s*<', re.S)
CHECK = re.compile(r'Средний чек:\s*(\d[\d\s]*)')


def parse_place(page):
    name = NAME.search(page or '')
    # the check amount sits in a separate tag after "Средний чек:" ("Average check:"), so search the text with tags stripped
    check = CHECK.search(re.sub(r'<[^>]+>', ' ', page or ''))
    ig = next((n for n in websearch.parse_nicks(page) if n not in OWN_ACCOUNTS), None)
    return {'name': html.unescape(name.group(1)).strip() if name else '',
            'check': int(re.sub(r'\s', '', check.group(1))) if check else None,
            'instagram': ig}


def _links(page, pattern):
    return list(dict.fromkeys(re.findall(pattern, page or '')))


def _ensure_table(archive):
    archive.execute('CREATE TABLE IF NOT EXISTS restoclub_seen (path TEXT PRIMARY KEY, '
                    'at TIMESTAMPTZ NOT NULL DEFAULT now())')


def run(stores, fetch=_fetch, cities=CITIES, pause=1.0):
    archive, main = stores.archive, stores.main
    _ensure_table(archive)
    seen = {r['path'] for r in archive.execute('SELECT path FROM restoclub_seen').fetchall()}
    out = {'places': 0, 'target': 0, 'with_instagram': 0, 'new': 0}
    for slug, city in cities.items():
        index = fetch('%s/%s/ratings' % (BASE, slug))
        # ratings aren't available everywhere; curated lists ("downtown restaurants", "Georgian cuisine") exist in every city
        lists = _links(index, r'href="(/%s/ratings/[a-z0-9-]+)"' % slug) + \
            _links(index, r'href="(/%s/search/[a-z0-9-]+)"' % slug)
        places = []
        for r in lists:
            places += _links(fetch(BASE + r), r'href="(/%s/place/[a-z0-9-]+)"' % slug)
            time.sleep(pause)
        for path in dict.fromkeys(places):
            if path in seen:
                continue
            info = parse_place(fetch(BASE + path))
            time.sleep(pause)
            archive.execute('INSERT INTO restoclub_seen (path) VALUES (%s) ON CONFLICT DO NOTHING', (path,))
            seen.add(path)
            out['places'] += 1
            level = gis.level_for(info['check'])
            if not info['name'] or level not in LEVELS:
                continue
            out['target'] += 1
            row = main.execute(
                'INSERT INTO venues (name, city, region, level, avg_check, status, source, ig_account) '
                "VALUES (%s, %s, '', %s, %s, 'не отработано', %s, %s) "
                'ON CONFLICT (name, city) DO UPDATE SET '
                "ig_account = COALESCE(NULLIF(venues.ig_account, ''), excluded.ig_account), "
                "level = COALESCE(NULLIF(venues.level, ''), excluded.level) "
                "RETURNING (xmax = 0) AS inserted, coalesce(ig_account, '') AS ig",
                (info['name'], city, level, info['check'], SOURCE, info['instagram'] or None)).fetchone()
            out['new'] += bool(row['inserted'])
            if info['instagram']:
                out['with_instagram'] += 1
                if row['inserted']:
                    wq.enqueue(archive, 'venue_header', {'venue_name': info['name'], 'city': city,
                                                         'ig_account': info['instagram']}, priority=60)
            elif row['inserted'] and not row['ig']:
                wq.enqueue(archive, 'web_bridge', {'venue_name': info['name'], 'city': city}, priority=50)
    return out


if __name__ == '__main__':
    from leadgen.core import db
    print('Restoclub: заведений %(places)d, нужного уровня %(target)d, с Instagram %(with_instagram)d, '
          'новых в реестре %(new)d' % run(db.open_stores()))

"""Tableware dealers in the East, from 2GIS (2026-10-13, client: "hook it up").

Dealer queries have exhausted the East (780 queries, 2 East dealers). 2GIS returns HoReCa
supplier companies for queries like "посуда для ресторанов" ("restaurant tableware"),
"оборудование для ресторанов" ("restaurant equipment"); many have Instagram on their site.
Chain: NSK department cities x queries → companies with tableware/HoReCa-equipment categories →
Instagram from the site (gis.site_instagram) → profile_info → the usual filter by "дилер"
("dealer") role (NSK only). Already-seen companies are remembered in the archive and never
re-checked.

    deploy/local/run.sh leadgen.sources.suppliers     # once a day, alongside awards (leadgen.sources.awards)
"""
import re
import time

from leadgen.core import contacts, queue as wq
from leadgen.sources import gis_cities

SOURCE = '2ГИС-поставщик'
QUERIES = ('посуда для ресторанов', 'оборудование для ресторанов', 'товары для HoReCa', 'посуда оптом',
           'оборудование для общепита')
SUPPLIER = re.compile(r'посуд|оборудование для общепита|для общественного питания|horeca|хорека|сервировк|фарфор', re.I)
PAGES = 3


def east_cities():
    return [(slug, city) for slug, city in gis_cities.CITIES if gis_cities.department(slug) == 'НСК']


def _ensure(archive):
    archive.execute('CREATE TABLE IF NOT EXISTS suppliers_seen (gis_id TEXT PRIMARY KEY, nick TEXT, status TEXT, '
                    'at TIMESTAMPTZ NOT NULL DEFAULT now())')


def run(stores, client, cities=None, queries=QUERIES, pages=PAGES, pause=0.0):
    archive = stores.archive
    _ensure(archive)
    seen = {r['gis_id'] for r in archive.execute('SELECT gis_id FROM suppliers_seen').fetchall()}
    out = {'companies': 0, 'instagram': 0, 'queued': 0}
    for slug, city in cities or east_cities():
        for q in queries:
            for page in range(1, pages + 1):
                found, status = client.search(slug, q, page)
                time.sleep(pause)
                if status != 'ok' or not found:
                    break
                for c in found:
                    if c['gis_id'] in seen or not SUPPLIER.search(' '.join([c.get('kind') or ''] + (c.get('rubrics') or []))):
                        continue
                    seen.add(c['gis_id'])
                    out['companies'] += 1
                    nick, st = client.site_instagram(slug, c['gis_id'], c['name'], strict=False)
                    time.sleep(pause)
                    archive.execute('INSERT INTO suppliers_seen (gis_id, nick, status) VALUES (%s, %s, %s) '
                                    'ON CONFLICT DO NOTHING', (c['gis_id'], nick, st))
                    if not nick:
                        continue
                    out['instagram'] += 1
                    nick = contacts.norm_nick(nick)
                    if contacts.is_known(stores, nick):
                        continue
                    wq.enqueue(archive, 'profile_info', {'nick': nick, 'venue': '', 'city': city, 'source': SOURCE},
                               priority=60)
                    out['queued'] += 1
    return out


if __name__ == '__main__':
    from leadgen.core import db
    from leadgen.sources import gis
    print('поставщики 2ГИС (Восток): компаний %(companies)d, с Instagram %(instagram)d, в отбор %(queued)d'
          % run(db.open_stores(), gis.GisClient(), pause=1.0))

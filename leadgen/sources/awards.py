"""WhereToEat award winners by region: venue Instagram links are right on the page.

Measured 2026-09-30: 18 pages for 2024-2025 — about 600 links, all winner venues
(high tier by definition). The handle goes into profile verification with source "премия"
(award): a person with a job title is a candidate, a venue goes through header check
and subscriber-list traversal.

    deploy/local/run.sh leadgen.sources.awards      # once a day (launchd io.leadgen.sources.awards)
"""
import subprocess

from leadgen.core import contacts, db, queue as wq
from leadgen.sources import websearch

REGIONS = ('moscow', 'spb', 'south', 'ural', 'siberia', 'fareast', 'center', 'tatarstan')
YEARS = (2026, 2025, 2024)  # 2026 fills in as results are published; empty until the page exists
SOURCE = 'премия'
OWN_ACCOUNTS = {'wheretoeat.ru', 'wheretoeat', 'where2drink', 'where2drink.ru', 'wheretotravel.ru'}


def pages():
    return ['https://%s.wheretoeat.ru/winners_%d/' % (r, y) for r in REGIONS for y in YEARS]


def fetch(url):
    out = subprocess.run(['curl', '-s', '-L', '--max-time', '25', '-A', websearch.UA, '-w', '\n%{http_code}', url],
                         capture_output=True, text=True).stdout
    body, _, code = out.rpartition('\n')
    return body if code == '200' else ''


def _known_venue(main, nick):
    return main.execute('SELECT 1 FROM venues WHERE lower(ig_account) = %s LIMIT 1', (nick,)).fetchone() is not None


def run(stores, fetch=fetch):
    found = added = 0
    seen = set()
    for url in pages():
        for nick in websearch.parse_nicks(fetch(url)):
            if nick in seen or nick in OWN_ACCOUNTS:
                continue
            seen.add(nick)
            found += 1
            if (_known_venue(stores.main, nick) or contacts.is_known(stores, nick)
                    or websearch.queued(stores.archive, nick)):
                continue
            wq.enqueue(stores.archive, 'profile_info', {'nick': nick, 'venue': '', 'city': '', 'source': SOURCE},
                       priority=60)
            added += 1
    return {'pages': len(pages()), 'found': found, 'added': added}


if __name__ == '__main__':
    stores = db.open_stores()
    print('премии: %(pages)d страниц, ников %(found)d, новых в работу %(added)d' % run(stores))
    from leadgen.sources import festivals  # festivals and contests — same daily pass
    print('фестивали: %(chefs)d шефов, в поиск %(queued)d' % festivals.run(stores))
    from leadgen import restoclub  # new venues from Restoclub ratings (already-seen ones aren't re-pulled)
    print('Restoclub: заведений %(places)d, нужного уровня %(target)d, новых %(new)d' % restoclub.run(stores))
    from leadgen import afisha  # Afisha restaurant catalog, 18 cities (already-seen cards don't count)
    print('Афиша: заведений %(places)d, нужного уровня %(target)d, новых %(new)d' % afisha.run(stores))
    print('Выбор Афиши: заведений %(places)d, нужного уровня %(target)d, новых %(new)d' % afisha.run_choice(stores))
    from leadgen.sources import gis, suppliers  # East dealers: tableware and equipment suppliers from 2GIS
    print('Поставщики 2ГИС (Восток): компаний %(companies)d, с Instagram %(instagram)d, в отбор %(queued)d'
          % suppliers.run(stores, gis.GisClient(), pause=1.0))

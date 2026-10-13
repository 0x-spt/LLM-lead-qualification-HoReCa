"""Chefs from festivals and contests (2026-10-01): Gastreet, "Лавры" ("Laurels"), etc.

Speaker pages give a name, a job title with workplace, and a city, but no handle. We look up the
handle via Instagram search by name and keep only accounts whose full name matches the speaker;
after that it's the usual profile check (job title in header, city). Search is the scarcest
endpoint, so only chefs go through here, not marketers or business development directors.

    deploy/local/run.sh leadgen.sources.festivals      # once a day, alongside awards (leadgen.sources.awards)
"""
import html
import re
import subprocess

from leadgen.core import contacts, queue as wq
from leadgen.sources import gis_cities, websearch
from leadgen.access import health
from leadgen.access.transport import FAILURES

SOURCE = 'фестиваль'
ENDPOINT_SEARCH = 'search'
PAGES = ('https://gastreet.com/speakers',)
NAME = re.compile(r'^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?$')
CHEF = re.compile(r'шеф|повар|кондитер|\bchef\b', re.I)
MAX_MATCHES = 3


def fetch(url):
    out = subprocess.run(['curl', '-s', '-L', '--max-time', '40', '-A', websearch.UA, '-w', '\n%{http_code}', url],
                         capture_output=True, text=True).stdout
    body, _, code = out.rpartition('\n')
    return body if code == '200' else ''


def _lines(page):
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', page or '', flags=re.S)
    text = html.unescape(re.sub(r'<[^>]+>', '\n', text))
    return [line.strip() for line in text.split('\n') if line.strip()]


def parse_speakers(page):
    """[{name, role, city}]: a "First Last" line, followed by the job title, then the city in
    one of the next few lines."""
    cities = {c for _, c in gis_cities.CITIES}
    lines = _lines(page)
    out = []
    for i, line in enumerate(lines[:-1]):
        if not NAME.match(line):
            continue
        city = next((ln for ln in lines[i + 2:i + 4] if ln in cities), '')
        out.append({'name': line, 'role': lines[i + 1][:200], 'city': city})
    return out


def chefs(people):
    return [p for p in people if CHEF.search(p['role'])]


def run(stores, fetch=fetch):
    found = queued = 0
    have = {r['n'] for r in stores.archive.execute(
        "SELECT payload->>'name' AS n FROM work_queue WHERE task_type = 'person_search'").fetchall()}
    for url in PAGES:
        for p in chefs(parse_speakers(fetch(url))):
            found += 1
            if p['name'] in have:
                continue
            wq.enqueue(stores.archive, 'person_search', dict(p, source=SOURCE), priority=55)
            have.add(p['name'])
            queued += 1
    return {'chefs': found, 'queued': queued}


def _tokens(name):
    name = (name or '').lower().replace('ё', 'е')
    return set(re.findall(r'[a-zа-я-]+', name))


def handle_person_search(stores, transport, account_id, task):
    payload = task['payload']
    users, status = transport.search(payload['name'])
    if status in FAILURES:
        health.record_failure(stores.archive, account_id, ENDPOINT_SEARCH, status)
        wq.fail(stores.archive, task['id'], requeue=True)
        return
    health.record_success(stores.archive, account_id, ENDPOINT_SEARCH)
    want = _tokens(payload['name'])
    taken = 0
    for u in users or []:
        if taken >= MAX_MATCHES:
            break
        if not want <= _tokens(u.get('full_name')):
            continue  # same-surname/same-first-name matches without the other word — skip (measured 09-14: name search is noisy)
        nick = contacts.norm_nick(u.get('username') or '')
        if not nick or contacts.is_known(stores, nick):
            continue
        wq.enqueue(stores.archive, 'profile_info',
                   {'nick': nick, 'pk': str(u.get('pk') or ''), 'venue': '', 'city': payload.get('city') or '',
                    'source': SOURCE}, priority=60)
        taken += 1
    print('фестиваль: %s — совпадений %d' % (payload['name'], taken))
    wq.complete(stores.archive, task['id'])


HANDLERS = {'person_search': handle_person_search}


if __name__ == '__main__':
    from leadgen.core import db
    print('фестивали: %(chefs)d шефов, в поиск %(queued)d' % run(db.open_stores()))

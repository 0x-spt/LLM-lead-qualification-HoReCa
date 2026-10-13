"""2GIS results via a plain HTTP request — no browser needed.

The page is server-rendered: cards live inside `var initialState =
JSON.parse('…')`. Verified 2026-09-29. 2GIS doesn't show Instagram —
the venue's account is found by the bridge, via name lookup."""
import json
import re
import time
import urllib.parse

import httpx

BASE = 'https://2gis.ru'
HEADERS = {'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
           'accept': 'text/html,application/xhtml+xml', 'accept-language': 'ru-RU,ru;q=0.9'}
STATE = re.compile(r"var initialState = JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
SITE = re.compile(r'^(https?://)?[\w.-]+\.[a-zа-яё]{2,}(/\S*)?$', re.I)
IG_LINK = re.compile(r'instagram\.com/([A-Za-z0-9._]{2,30})', re.I)
IG_SERVICE = {'p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'instagram', 'direct', 'about', 'legal', 'static', 'developer', 'web'}
CHECK = re.compile(r'Чек\s*(\d[\d\s\xa0]*)\s*₽')


def parse_state(html):
    m = STATE.search(html or '')
    if not m:
        return None
    try:
        raw = m.group(1).encode('utf-8').decode('unicode_escape').encode('latin-1').decode('utf-8')
        return json.loads(raw)
    except (ValueError, UnicodeError):
        return None


def level_for(check):
    if not check:
        return ''
    if check >= 3500:
        return 'PREMIUM'
    if check >= 2500:
        return 'UPPER-MIDDLE'
    if check >= 2000:
        return 'MIDDLE'
    return 'LOW'


def _check(d):
    for group in d.get('attribute_groups') or []:
        for a in group.get('attributes') or []:
            m = CHECK.search(a.get('name') or '')
            if m:
                return int(re.sub(r'\D', '', m.group(1)))
    return None


def venues_from_state(state):
    out = []
    profiles = (((state or {}).get('data') or {}).get('entity') or {}).get('profile') or {}
    for fid, v in profiles.items():
        d = (v or {}).get('data') or {}
        if d.get('type') not in (None, 'branch'):
            continue
        full = d.get('name') or ''
        name, _, kind = full.partition(', ')
        city = next((a.get('name') for a in d.get('adm_div') or [] if a.get('type') == 'city'), '')
        check = _check(d)
        out.append({'gis_id': str(d.get('id') or fid), 'name': name.strip(), 'kind': kind.strip(),
                    'city': city, 'alias': d.get('city_alias') or '',
                    'address': d.get('address_name') or '',
                    'rubrics': [r.get('name') for r in d.get('rubrics') or [] if r.get('name')],
                    'rating': (d.get('reviews') or {}).get('general_rating'),
                    'check': check, 'level': level_for(check),
                    'branches': (d.get('org') or {}).get('branch_count') or 1})
    return out


def firm_contacts(state, fid):
    profiles = (((state or {}).get('data') or {}).get('entity') or {}).get('profile') or {}
    d = (profiles.get(str(fid)) or {}).get('data') or {}
    return [c for g in d.get('contact_groups') or [] for c in g.get('contacts') or []]


FILE_EXT = re.compile(r'\.(?:ico|png|jpe?g|gif|svg|webp|css|js|xml|txt)$', re.I)


def instagram_links(html):
    # Instagram site files (favicon.ico, logo.png) — not accounts (2026-10-13)
    return sorted({m.lower().rstrip('.') for m in IG_LINK.findall(html or '')
                   if m.lower() not in IG_SERVICE and not FILE_EXT.search(m)})


def pick_instagram(links, name, strict=True):
    """Account from the website: (nick | None, status). Name match is required for restaurants
    (a group's site carries the group's own account: "Хозяина тайги" ("Taiga Host") links to
    @bellini_group). For a supplier (strict=False) a single link on the site is enough:
    "Торговые технологии" ("Trade Technologies") links to @tt_horeca (2026-10-13)."""
    from leadgen.qualify import quality
    matching = [l for l in links if quality.name_matches(l, '', name)]
    if len(matching) == 1:
        return matching[0], 'ok'
    if matching:
        return None, 'ambiguous'
    if not strict and len(links) == 1:
        return links[0], 'ok'
    return None, 'no_match'


class GisClient:
    def __init__(self, http_transport=None, delay=2.0, sleep=time.sleep):
        kwargs = {'headers': HEADERS, 'timeout': 30.0, 'follow_redirects': True}
        if http_transport is not None:
            kwargs['transport'] = http_transport
        self._client = httpx.Client(**kwargs)
        # venue sites: third-party certs are sometimes broken, we only read public pages anyway
        site_kwargs = dict(kwargs, timeout=10.0, verify=False)
        self._sites = httpx.Client(**site_kwargs)
        self._delay, self._sleep, self._last = delay, sleep, 0.0

    def close(self):
        self._client.close()
        self._sites.close()

    def _pace(self):
        wait = self._delay - (time.monotonic() - self._last)
        if self._last and wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def site_instagram(self, slug, gis_id, name, strict=True):
        """Venue's account from a link on its website: (nick | None, status).
        Statuses: ok, no_site, no_instagram, ambiguous, http_<code>."""
        self._pace()
        try:
            r = self._client.get('%s/%s/firm/%s' % (BASE, slug, gis_id))
        except httpx.TransportError:
            return None, 'http_network'
        if r.status_code != 200:
            return None, 'http_%d' % r.status_code
        contacts = firm_contacts(parse_state(r.text), gis_id)
        # the "website" field sometimes holds plain text ("Go to Telegram channel") — keep only URLs
        sites = [c.get('text').strip() for c in contacts
                 if c.get('type') == 'website' and SITE.match((c.get('text') or '').strip())][:2]
        if not sites:
            return None, 'no_site'
        links = []
        for site in sites:
            url = site if site.startswith('http') else 'https://' + site
            try:
                links += instagram_links(self._sites.get(url).text)
            except Exception:
                continue  # site unreachable or malformed URL — not a reason to halt the registry
        links = sorted(set(links))
        if not links:
            return None, 'no_instagram'
        return pick_instagram(links, name, strict)

    def search(self, slug, query, page=1):
        wait = self._delay - (time.monotonic() - self._last)
        if self._last and wait > 0:
            self._sleep(wait)
        url = '%s/%s/search/%s' % (BASE, slug, urllib.parse.quote(query))
        if page > 1:
            url += '/page/%d' % page
        try:
            r = self._client.get(url)
        except httpx.TransportError:
            return None, 'http_network'
        finally:
            self._last = time.monotonic()
        if r.status_code != 200:
            return None, 'http_%d' % r.status_code
        state = parse_state(r.text)
        if state is None:
            return None, 'no_state'
        venues = venues_from_state(state)
        # a wrong slug silently redirects to another city — check against the data, not the URL;
        # on a mixed page (satellite towns) drop the other city's cards one by one
        own = [v for v in venues if not v['alias'] or v['alias'] == slug]
        if venues and not own:
            return None, 'wrong_city'
        return own, 'ok'

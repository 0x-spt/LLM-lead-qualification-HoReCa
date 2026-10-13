"""An own account: httpx with the session cookies and the account's proxy.

Endpoints and workarounds come from the project's own measurements:
account_serp instead of topsearch, pk from the profile's HTML when search is
blocked (ig-chef-handle-discovery)."""
import html
import json
import re

import httpx

from leadgen.access.transport import CHECKPOINT, EMPTY, NET_ERROR, OK, PROXY_DEAD, SOFT_BLOCK, THROTTLED

APP_ID = '936619743392459'
BASE_URL = 'https://www.instagram.com'
DEFAULT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')

PK_IN_HTML = (re.compile(r'"profilePage_(\d+)"'),
              re.compile(r'"user_id":"(\d+)"'),
              re.compile(r'"profile_id":"(\d+)"'))

CHECKPOINT_MESSAGES = ('checkpoint_required', 'challenge_required', 'login_required')
CHECKPOINT_LOCATIONS = ('/accounts/login', '/challenge/')


def _smallest(candidates, min_width=300):
    """Thumbnail for the grid: the smallest one at least min_width wide, else the largest."""
    ok = sorted((c for c in candidates or [] if (c.get('width') or 0) >= min_width),
                key=lambda c: c.get('width') or 0)
    if ok:
        return ok[0].get('url')
    return (candidates or [{}])[0].get('url')


def _own_item(it):
    versions = it.get('image_versions2') or {}
    if not versions.get('candidates') and it.get('carousel_media'):
        versions = it['carousel_media'][0].get('image_versions2') or {}
    loc = it.get('location') or None
    people = []
    for src in [it] + list(it.get('carousel_media') or []):
        for tag in ((src.get('usertags') or {}).get('in') or []):
            name = (tag.get('user') or {}).get('username')
            if name and name not in people:
                people.append(name)
    for co in it.get('coauthor_producers') or []:
        if co.get('username') and co['username'] not in people:
            people.append(co['username'])
    return {'taken_at': it.get('taken_at') or 0, 'code': it.get('code') or '',
            'caption': ((it.get('caption') or {}).get('text') or ''),
            'thumb': _smallest(versions.get('candidates')) if versions.get('candidates') else None,
            # the post's geotag — used for the "RF geo" check (geo_points)
            'loc': ({'pk': str(loc['pk']) if loc.get('pk') else None, 'lat': loc.get('lat'), 'lng': loc.get('lng'),
                     'name': loc.get('name') or ''} if loc else None),
            # people tagged in the photo and the post's co-authors — the source for "tags" (chefs on plating shots)
            'people': people}


# --- Working around blocked profile endpoints via web pages (measured 2026-09-28:
# users/info and web_profile_info return 429 on all accounts on the IP, while the
# profile page and /embed/ return 200)

META = re.compile(r'<meta\b[^>]*>', re.I)
ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
NUM = r'([\d][\d.,\s\xa0]*)\s*(K|M|тыс\.?|млн)?'
COUNT_PATTERNS = {
    'follower_count': (re.compile(NUM + r'\s*Followers', re.I), re.compile(r'подписчик\w*:\s*' + NUM, re.I)),
    'media_count': (re.compile(NUM + r'\s*Posts', re.I), re.compile(r'публикаци\w*:\s*' + NUM, re.I)),
}
# The profile page's grid query; doc_id changes with Instagram releases — when it
# does, the response comes back as HTML, web_feed returns soft_block, and the feed falls back to /embed/.
POSTS_QUERY = 'PolarisProfilePostsQuery'
POSTS_DOC_ID = '28570182382647478'
POSTS_ROOT = 'xdt_api__v1__feed__user_timeline_graphql_connection'
SUGGESTED_QUERY = 'PolarisProfileSuggestedUsersWithPreloadableQuery'
TOKEN_PAGE = 'instagram'
SUGGESTED_DOC_ID = '27929823133325729'
PAGE_TOKENS = {'fb_dtsg': re.compile(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"'),
               'lsd': re.compile(r'"LSD",\[\],\{"token":"([^"]+)"'),
               'av': re.compile(r'"actorID":"(\d+)"'), 'jazoest': re.compile(r'jazoest=(\d+)'),
               'rev': re.compile(r'"server_revision":(\d+)'), 'hsi': re.compile(r'"hsi":"(\d+)"')}
META_TITLE = re.compile(rb'<meta[^>]*og:title[^>]*>')
META_DESC = re.compile(rb'<meta[^>]*og:description[^>]*>')
PK_IN_BYTES = tuple(re.compile(p.pattern.encode()) for p in PK_IN_HTML)


def _has_meta(buf):
    return b'</head>' in buf or bool(META_TITLE.search(buf) and META_DESC.search(buf))


def _has_pk(buf):
    return any(p.search(buf) for p in PK_IN_BYTES)


class _Page:
    """A page read by streaming: callers only need .text."""
    def __init__(self, text):
        self.text = text


CONTEXT_JSON = re.compile(r'"contextJSON":"((?:[^"\\]|\\.)*)"')


def _meta(page):
    out = {}
    for tag in META.findall(page):
        attrs = dict(ATTR.findall(tag))
        key = attrs.get('name') or attrs.get('property')
        if key and 'content' in attrs:
            out[key] = html.unescape(attrs['content'])
    return out


def _count(text, key):
    for pattern in COUNT_PATTERNS[key]:
        m = pattern.search(text)
        if not m:
            continue
        raw, suffix = m.group(1).strip().replace('\xa0', '').replace(' ', ''), (m.group(2) or '').lower()
        if suffix:
            value = float(raw.replace(',', '.')) * (1000000 if suffix.startswith(('m', 'млн')) else 1000)
        else:
            value = float(raw.replace(',', '').replace('.', ''))
        return int(value)
    return None


def parse_profile_page(page, nick):
    """Bio from the meta description of the profile's HTML page. No category or city there."""
    meta = _meta(page)
    desc = meta.get('description') or meta.get('og:description') or ''
    if not desc:
        return None
    bio = ''
    m = re.search(r'Instagram:\s*"(.*)"\s*$', desc, re.S)
    if m:
        bio = m.group(1).strip()
    title = meta.get('og:title') or ''
    name = title.split(' (@')[0].strip() if ' (@' in title else ''
    if not name:
        m = re.search(r'[-–]\s*(?:See Instagram photos and videos from\s*)?(.+?)\s*\(@', desc)
        name = m.group(1).strip() if m else ''
    pk = None
    for pattern in PK_IN_HTML:
        found = pattern.search(page)
        if found:
            pk = found.group(1)
            break
    return {'pk': pk, 'username': nick, 'full_name': name, 'biography': bio,
            'media_count': _count(desc, 'media_count'),
            'follower_count': _count(desc, 'follower_count'),
            'category': '', 'city_name': '', 'is_private': None,
            # the page has no business fields (city, address, category) — the bridge checks more strictly
            '_source': 'page'}


def _embed_thumb(media):
    resources = sorted(media.get('display_resources') or [], key=lambda r: r.get('config_width') or 0)
    for r in resources:
        if (r.get('config_width') or 0) >= 300:
            return r.get('src')
    return media.get('display_url')


def parse_embed(page):
    """Latest posts from /<nick>/embed/: dates, captions, images, a story flag."""
    m = CONTEXT_JSON.search(page)
    if not m:
        return None
    try:
        ctx = json.loads(json.loads('"%s"' % m.group(1))).get('context') or {}
    except ValueError:
        return None
    items = []
    for g in ctx.get('graphql_media') or []:
        media = g.get('shortcode_media') or {}
        edges = (media.get('edge_media_to_caption') or {}).get('edges') or []
        caption = (edges[0].get('node') or {}).get('text', '') if edges else ''
        items.append({'taken_at': media.get('taken_at_timestamp') or 0,
                      'code': media.get('shortcode') or '', 'caption': caption or '',
                      'thumb': _embed_thumb(media)})
    items.sort(key=lambda it: it['taken_at'], reverse=True)
    owner = ctx.get('owner_id')
    return {'items': items, 'media_count': ctx.get('posts_count'),
            'has_public_story': bool(ctx.get('has_public_story')),
            'full_name': ctx.get('full_name') or '', 'pk': str(owner) if owner else None}


class OwnTransport:
    kind = 'own'
    # For own accounts, a 200 with an empty list after ~150 searches means a silent block.
    silent_empty_is_block = True

    def __init__(self, cookies, proxy=None, user_agent=None, timeout=15.0, http_transport=None):
        headers = {'x-ig-app-id': APP_ID, 'user-agent': user_agent or DEFAULT_UA,
                   'x-requested-with': 'XMLHttpRequest'}
        if cookies.get('csrftoken'):
            headers['x-csrftoken'] = cookies['csrftoken']
        self._proxy = proxy
        kwargs = {'cookies': cookies, 'headers': headers, 'timeout': timeout,
                  'follow_redirects': False}
        if http_transport is not None:
            kwargs['transport'] = http_transport
        elif proxy:
            kwargs['proxy'] = proxy
        self._client = httpx.Client(**kwargs)
        # Web pages go through a separate client with browser navigation headers:
        # with x-ig-app-id set, Instagram serves an empty shell with no bio or feed.
        web = dict(kwargs)
        web['headers'] = {'user-agent': user_agent or DEFAULT_UA,
                          'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                          'accept-language': 'ru-RU,ru;q=0.9,en;q=0.8',
                          'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document',
                          'sec-fetch-site': 'none', 'upgrade-insecure-requests': '1'}
        self._web = httpx.Client(**web)
        self._page_tokens = None  # fb_dtsg/lsd from the page — live for hours, fetched once

    def close(self):
        self._client.close()
        self._web.close()

    def _fetch(self, url, params=None, headers=None, client=None, data=None, stop=None):
        """stop(buffer) -> True — the page is read as a stream and cut off as soon as
        what's needed has arrived: a profile weighs 880 KB, the bio is in the first 10 KB."""
        c = client or self._client
        try:
            if stop is None:
                r = (c.get(url, params=params, headers=headers) if data is None
                     else c.post(url, data=data, headers=headers))
                return self._classify(r)
            with c.stream('GET', url, params=params, headers=headers) as r:
                if r.status_code != 200:
                    r.read()
                    return self._classify(r)
                buf = b''
                for chunk in r.iter_bytes():
                    buf += chunk
                    if stop(buf):
                        break
                return _Page(buf.decode('utf-8', 'ignore')), OK
        except httpx.ProxyError:
            return None, PROXY_DEAD
        except httpx.ConnectError:
            return None, PROXY_DEAD if self._proxy else NET_ERROR
        except httpx.TransportError:
            # a timeout or a response cut short along the way — the network, not an Instagram refusal
            return None, NET_ERROR

    @staticmethod
    def _classify(r):
        if r.status_code == 429:
            return None, THROTTLED
        if 300 <= r.status_code < 400:
            location = r.headers.get('location', '')
            if any(m in location for m in CHECKPOINT_LOCATIONS):
                return None, CHECKPOINT
            return None, SOFT_BLOCK
        if r.status_code == 404:
            return None, EMPTY
        if r.status_code != 200:
            if any(m in r.text for m in CHECKPOINT_MESSAGES):
                return None, CHECKPOINT
            return None, SOFT_BLOCK
        return r, OK

    def _api(self, path, params=None):
        r, status = self._fetch(BASE_URL + path, params)
        if status != OK:
            return None, status
        try:
            data = r.json()
        except ValueError:
            return None, SOFT_BLOCK  # HTML instead of JSON — throttling
        if isinstance(data, dict) and data.get('message') in CHECKPOINT_MESSAGES:
            return None, CHECKPOINT
        return data, OK

    def search(self, query):
        data, status = self._api('/api/v1/fbsearch/account_serp/', {'query': query})
        if status != OK:
            return None, status
        users = data.get('users') or []
        return users, (OK if users else EMPTY)

    def pk_by_nick(self, nick):
        r, status = self._fetch('%s/%s/' % (BASE_URL, nick), client=self._web, stop=_has_pk)
        if status != OK:
            return None, status
        for pattern in PK_IN_HTML:
            found = pattern.search(r.text)
            if found:
                return found.group(1), OK
        return None, EMPTY

    def profile_info(self, pk):
        data, status = self._api('/api/v1/users/%s/info/' % pk)
        if status != OK:
            return None, status
        if 'user' not in data:
            return None, SOFT_BLOCK
        return data['user'], OK

    def following(self, pk, max_id=None):
        params = {'count': 100}
        if max_id:
            params['max_id'] = max_id
        data, status = self._api('/api/v1/friendships/%s/following/' % pk, params)
        if status != OK:
            return None, None, status
        return data.get('users') or [], data.get('next_max_id'), OK

    def media_info(self, media_id):
        data, status = self._api('/api/v1/media/%s/info/' % media_id)
        if status != OK:
            return None, status
        items = data.get('items') or []
        if not items:
            return None, SOFT_BLOCK
        return items[0], OK

    def user_feed(self, pk):
        data, status = self._api('/api/v1/feed/user/%s/' % pk, {'count': 12})
        if status != OK:
            return None, status
        items = [_own_item(it) for it in (data.get('items') or [])[:12]]
        return items, (OK if items else EMPTY)

    def highlights_latest(self, pk):
        data, status = self._api('/api/v1/highlights/%s/highlights_tray/' % pk)
        if status != OK:
            return None, status
        dates = [t.get('latest_reel_media') or 0 for t in data.get('tray') or []]
        latest = max(dates) if dates else 0
        return (latest, OK) if latest else (None, EMPTY)

    def download(self, url):
        r, status = self._fetch(url)
        if status != OK:
            return None, status
        return r.content, OK

    def profile_by_nick(self, nick, need_pk=False):
        """Bio without users/info — from the profile page (the same endpoint as pk_by_nick).
        need_pk — read on until pk shows up (≈460 KB), otherwise the meta tags suffice (≈10 KB)."""
        stop = (lambda b: _has_meta(b) and _has_pk(b)) if need_pk else _has_meta
        r, status = self._fetch('%s/%s/' % (BASE_URL, nick), client=self._web, stop=stop)
        if status != OK:
            return None, status
        prof = parse_profile_page(r.text, nick)
        # the page loaded but has no data — the profile doesn't exist or is hidden; not an endpoint block
        return (prof, OK) if prof else (None, EMPTY)

    def _tokens(self, nick):
        # tokens are session-scoped: fetched from a stable page — the target page can get
        # renamed or deleted, and its 302 used to be counted as a one-hour account block (2026-10-01)
        if self._page_tokens is None:
            r, status = self._fetch('%s/%s/' % (BASE_URL, TOKEN_PAGE), client=self._web)
            if status != OK:
                return None, status
            tokens = {k: (rx.search(r.text) or [None, None])[1] for k, rx in PAGE_TOKENS.items()}
            if not tokens['fb_dtsg'] or not tokens['lsd']:
                return None, SOFT_BLOCK
            self._page_tokens = tokens
        return self._page_tokens, OK

    def _gql(self, nick, friendly, doc_id, variables, root_field=None):
        """The profile page's internal query (sent the same way the browser sends it), using
        page tokens. Returns (json, OK) | (None, status). HTML instead of JSON means the
        tokens expired or doc_id changed."""
        tokens, status = self._tokens(nick)
        if status != OK:
            return None, status
        data = {'av': tokens['av'] or '0', '__d': 'www', '__user': '0', '__a': '1', '__req': '1',
                '__comet_req': '7', 'fb_dtsg': tokens['fb_dtsg'], 'jazoest': tokens['jazoest'] or '',
                'lsd': tokens['lsd'], '__rev': tokens['rev'] or '', '__hsi': tokens['hsi'] or '',
                'fb_api_caller_class': 'RelayModern', 'fb_api_req_friendly_name': friendly,
                'variables': json.dumps(variables, separators=(',', ':')),
                'server_timestamps': 'true', 'doc_id': doc_id}
        headers = {'content-type': 'application/x-www-form-urlencoded', 'x-fb-friendly-name': friendly,
                   'x-fb-lsd': tokens['lsd'], 'x-asbd-id': '359341',
                   'referer': '%s/%s/' % (BASE_URL, nick), 'origin': BASE_URL, 'accept': '*/*',
                   'sec-fetch-site': 'same-origin', 'sec-fetch-mode': 'cors', 'sec-fetch-dest': 'empty'}
        if root_field:
            headers['x-root-field-name'] = root_field
        r, status = self._fetch(BASE_URL + '/graphql/query', headers=headers, data=data)
        if status != OK:
            return None, status
        try:
            payload = r.json()
        except ValueError:
            self._page_tokens = None
            return None, SOFT_BLOCK
        if not isinstance(payload, dict):
            return None, SOFT_BLOCK
        if payload.get('errors') and not payload.get('data'):
            # an error about this specific target (field_exception, noncoercible_argument_value) —
            # this account simply has no data; Instagram's rate limits come back as 429, not this
            return None, EMPTY
        if 'data' not in payload:
            self._page_tokens = None
            return None, SOFT_BLOCK
        return payload, OK

    def web_feed(self, nick):
        """12 latest posts via the profile page's own query (PolarisProfilePostsQuery).
        feed/user returns a 302 to the homepage, and /embed/ only gives 6 posts."""
        variables = {'data': {'count': 12, 'include_reel_media_seen_timestamp': True,
                              'include_relationship_info': True, 'latest_besties_reel_media': True,
                              'latest_reel_media': True},
                     'username': nick,
                     '__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider': True,
                     '__relay_internal__pv__PolarisShortDramaEnabledrelayprovider': False,
                     '__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider': False}
        payload, status = self._gql(nick, POSTS_QUERY, POSTS_DOC_ID, variables, POSTS_ROOT)
        if status != OK:
            return None, status
        if payload.get('data') is None and payload.get('errors'):
            return None, EMPTY  # field_exception: no such nick exists — not an endpoint block
        conn = (payload.get('data') or {}).get(POSTS_ROOT, 'нет')
        if conn is None:
            return None, EMPTY  # the account doesn't exist or is hidden — not an endpoint block
        try:
            edges = conn['edges']
        except (KeyError, TypeError):
            self._page_tokens = None
            return None, SOFT_BLOCK
        items = [_own_item(e.get('node') or {}) for e in edges or []][:12]
        return items, (OK if items else EMPTY)

    def suggested(self, pk, nick):
        """"Similar accounts" from the profile page: [{username, pk, full_name}].
        For a venue, these are mostly other restaurants in the same city (measured 2026-10-01: ~40 per venue)."""
        payload, status = self._gql(nick, SUGGESTED_QUERY, SUGGESTED_DOC_ID,
                                    {'module': 'profile', 'target_id': str(pk)})
        if status != OK:
            return None, status
        users, seen = [], set()

        def walk(o):
            if isinstance(o, dict):
                name, upk = o.get('username'), o.get('pk') or o.get('id')
                if name and upk and name not in seen:
                    seen.add(name)
                    users.append({'username': name, 'pk': str(upk), 'full_name': o.get('full_name') or ''})
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(payload.get('data'))
        users = [u for u in users if u['username'] != nick]
        return users, (OK if users else EMPTY)

    def location_people(self, location_pk):
        """Authors and tagged people in a location's top posts (the "ranked" tab): for a
        restaurant these are plating shots posted by chefs. The location's web page redirects
        to the homepage, but the API responds."""
        r, status = self._fetch('%s/api/v1/locations/%s/sections/' % (BASE_URL, location_pk),
                                headers={'content-type': 'application/x-www-form-urlencoded'},
                                data={'tab': 'ranked'})
        if status != OK:
            return None, status
        try:
            sections = r.json().get('sections') or []
        except ValueError:
            return None, SOFT_BLOCK
        people = []
        for sec in sections:
            for m in (sec.get('layout_content') or {}).get('medias') or []:
                media = m.get('media') or {}
                names = [(media.get('user') or {}).get('username')]
                names += [(t.get('user') or {}).get('username') for t in (media.get('usertags') or {}).get('in') or []]
                for name in names:
                    if name and name not in people:
                        people.append(name)
        return people, (OK if people else EMPTY)

    def embed_feed(self, nick):
        """Feed without feed/user — from the /embed/ page: 6 latest posts."""
        r, status = self._fetch('%s/%s/embed/' % (BASE_URL, nick), client=self._web)
        if status != OK:
            return None, status
        feed = parse_embed(r.text)
        return (feed, OK) if feed else (None, EMPTY)

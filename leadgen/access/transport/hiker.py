"""HikerAPI — paid access to Instagram without own accounts or proxies.

Can be plugged in at any time: the key is written to accounts (kind='hiker'),
the mode is settings.ig_transport. An empty response here is a genuine
"not found", not a silent block — hence silent_empty_is_block = False."""
import httpx

from leadgen.access.transport import CHECKPOINT, EMPTY, OK, SOFT_BLOCK, THROTTLED

BASE_URL = 'https://api.hikerapi.com'


class HikerTransport:
    kind = 'hiker'
    silent_empty_is_block = False

    def __init__(self, api_key, timeout=30.0, http_transport=None):
        kwargs = {'base_url': BASE_URL, 'timeout': timeout,
                  'headers': {'x-access-key': api_key, 'accept': 'application/json'}}
        if http_transport is not None:
            kwargs['transport'] = http_transport
        self._client = httpx.Client(**kwargs)
        # thumbnails live on Instagram's CDN — don't send the HikerAPI key there
        cdn_kwargs = {'timeout': timeout}
        if http_transport is not None:
            cdn_kwargs['transport'] = http_transport
        self._cdn = httpx.Client(**cdn_kwargs)

    def close(self):
        self._client.close()
        self._cdn.close()

    def _get(self, path, params):
        try:
            r = self._client.get(path, params=params)
        except httpx.TransportError:
            return None, SOFT_BLOCK
        if r.status_code == 429:
            return None, THROTTLED
        if r.status_code in (401, 402, 403):
            return None, CHECKPOINT
        if r.status_code == 404:
            return None, EMPTY
        if r.status_code != 200:
            return None, SOFT_BLOCK
        try:
            return r.json(), OK
        except ValueError:
            return None, SOFT_BLOCK

    def search(self, query):
        data, status = self._get('/v2/search/accounts', {'query': query})
        if status != OK:
            return (None, status) if status != EMPTY else ([], EMPTY)
        users = (data or {}).get('users') or []
        return users, (OK if users else EMPTY)

    def pk_by_nick(self, nick):
        data, status = self._get('/v1/user/by/username', {'username': nick})
        if status != OK:
            return None, status
        pk = (data or {}).get('pk')
        return (str(pk), OK) if pk else (None, EMPTY)

    def profile_info(self, pk):
        data, status = self._get('/v1/user/by/id', {'id': str(pk)})
        if status != OK:
            return None, status
        return data, OK

    def following(self, pk, max_id=None):
        params = {'user_id': str(pk)}
        if max_id:
            params['max_id'] = max_id
        data, status = self._get('/v1/user/following/chunk', params)
        if status != OK:
            return None, None, status
        users, next_max_id = data[0] or [], data[1]
        return users, next_max_id, OK

    def media_info(self, media_id):
        data, status = self._get('/v1/media/by/id', {'id': str(media_id)})
        if status != OK:
            return None, status
        item = dict(data)
        item['taken_at'] = data.get('taken_at_ts')
        return item, OK

    def user_feed(self, pk):
        data, status = self._get('/v1/user/medias/chunk', {'user_id': str(pk)})
        if status != OK:
            return None, status
        items = []
        for m in (data[0] or [])[:12]:
            thumb = m.get('thumbnail_url') or next(
                (r.get('thumbnail_url') for r in m.get('resources') or [] if r.get('thumbnail_url')),
                None)
            items.append({'taken_at': m.get('taken_at_ts') or 0, 'code': m.get('code') or '',
                          'caption': m.get('caption_text') or '', 'thumb': thumb})
        return items, (OK if items else EMPTY)

    def highlights_latest(self, pk):
        data, status = self._get('/v1/user/highlights', {'user_id': str(pk), 'amount': 20})
        if status != OK:
            return None, status
        dates = [h.get('latest_reel_media') or 0 for h in data or []]
        latest = max(dates) if dates else 0
        return (latest, OK) if latest else (None, EMPTY)

    def download(self, url):
        try:
            r = self._cdn.get(url)
        except httpx.TransportError:
            return None, SOFT_BLOCK
        if r.status_code != 200:
            return None, SOFT_BLOCK
        return r.content, OK

    def profile_by_nick(self, nick, need_pk=False):
        data, status = self._get('/v1/user/by/username', {'username': nick})
        if status != OK:
            return None, status
        prof = dict(data)
        prof['pk'] = str(data.get('pk')) if data.get('pk') else None
        return prof, OK

    def embed_feed(self, nick):
        prof, status = self.profile_by_nick(nick)
        if status != OK:
            return None, status
        items, status = self.user_feed(prof['pk'])
        if status not in (OK, EMPTY):
            return None, status
        return {'items': items or [], 'media_count': prof.get('media_count'),
                'has_public_story': False, 'full_name': prof.get('full_name') or '',
                'pk': prof['pk']}, OK

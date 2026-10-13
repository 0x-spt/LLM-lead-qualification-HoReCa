"""The vet task: candidate "обогащён" (enriched) -> "сетка_готова" (grid ready) in the
main database, or a rejection.

Ordered by cost: the feed, in a single request, gives freshness, captions and
thumbnails all at once; recent stories are checked only if posts are older
than the window; profiles of venues mentioned in the bio are fetched only for
venues not already in the registry.

The venue is taken from the candidate's own bio (an @mention that turned out
to be a venue) or from whatever named the candidate in ITS bio (source
venue_header). The venue that originated the following-walk is NOT treated as
proof: on 09-16 this is how Muscovites and Gelendzhik residents got mislabeled
as "Восток" (East region)."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from leadgen.qualify import bio_roles, geo, geo_points, grid, handlers, passport, quality, review, roles, rules
from leadgen.core import contacts
from leadgen.access import health
from leadgen.core import queue as wq
from leadgen.qualify.names import clean_venue_name, person_name
from leadgen.access.transport import EMPTY, FAILURES, OK

ENDPOINT_FEED = 'feed'
ENDPOINT_HIGHLIGHTS = 'highlights'
ENDPOINT_CDN = 'cdn'
ENDPOINT_EMBED = 'embed'
ENDPOINT_WEB_FEED = 'feed_web'
MAX_MENTIONS = 3


def _requeue(stores, account_id, endpoint, status, task):
    health.record_failure(stores.archive, account_id, endpoint, status)
    wq.fail(stores.archive, task['id'], requeue=True)


# Categories under which a mentioned account is a workplace. Narrower than
# rules.VENUE_CAT: "Товар/услуга", "Компания", "Магазин" are there to filter
# out people, but here they would wrongly match equipment brands and suppliers.
VENUE_PLACE_CAT = re.compile(r'Ресторан|^Бар$|Кафе|Cafe|Гостиниц|Отель|Hotel|Бистро|Bistro'
                             r'|Паб|Pub|Винн|Wine|Гастро|Кофейн|Coffee|Пекарн|Bakery|Кондитерск'
                             r'|Restaurant|Bar\b|Steak|Стейк|Суши|Sushi', re.I)
MIN_TILES = 6


class Retry(Exception):
    """Temporarily unable to vet — requeue the task, leave the candidate alone."""


class StoryRetry(Retry):
    """Posts are older than the window and recent stories can't be checked."""


STORY_RETRIES = 3


def _registry_venue(main, ig):
    return main.execute(
        'SELECT name, city, level, ig_account FROM venues WHERE lower(ig_account) = %s LIMIT 1',
        (ig,)).fetchone()


def _is_place(ig, prof):
    category = prof.get('category') or ''
    if category:
        return bool(VENUE_PLACE_CAT.search(category))
    blob = ' '.join([prof.get('biography') or prof.get('bio') or '', prof.get('full_name') or ''])
    return (bool(quality.FOOD_EVIDENCE.search(blob))
            and not quality.ROLE_MARK.search(ig + ' ' + (prof.get('full_name') or '')))


def _venue_from_profile(stores, ig, pk, prof):
    # a venue account's name comes with promo tails: "Chowara | Ресторан Азиатской Кухни | Уфа"
    name = clean_venue_name(prof.get('full_name') or '') or ig
    # the profile page has no city_name — look for the city in the venue's bio
    city = (prof.get('city_name') or prof.get('city')
            or geo.find_city(stores.main, ' '.join([passport.prose(prof.get('biography') or prof.get('bio') or ''),
                                                     name])) or '')
    stores.main.execute(
        'INSERT INTO venues (name, city, ig_account, pk, source, status) '
        "VALUES (%s, %s, %s, %s, 'шапка кандидата', 'уровень не определён') "
        'ON CONFLICT (name, city) DO NOTHING', (name, city, ig, str(pk)))
    return {'name': name, 'city': city, 'level': None, 'ig_account': ig,
            'category': prof.get('category') or ''}


def _venue_geo(stores, transport, ig):
    """(verdict, city, names of foreign location tags) from a venue's 12 posts; on
    failure — ('none', None, '')."""
    if not hasattr(transport, 'web_feed'):
        return 'none', None, ''
    items, status = transport.web_feed(ig)
    if status != OK or not items:
        return 'none', None, ''
    locs = [it.get('loc') for it in items]
    kind, city = geo_points.verdict(locs)
    return kind, city, ', '.join(geo_points.names(locs)[:3])


def resolve_venue(stores, transport, account_id, bio, payload):
    """The candidate's venue. Raises Retry if a block prevented checking."""
    mentions = []
    for m in bio_roles.MENTION.findall(bio or ''):
        n = contacts.norm_nick(m)
        if n and n not in mentions:
            mentions.append(n)
    mentions = mentions[:MAX_MENTIONS]
    for ig in mentions:
        row = _registry_venue(stores.main, ig)
        if row:
            return dict(row, category='')
    for ig in mentions:
        cached = stores.archive.execute('SELECT * FROM profile_cache WHERE nick = %s LIMIT 1',
                                        (ig,)).fetchone()
        if cached:
            # already looked at this account — don't spend repeat requests on it
            if _is_place(ig, cached):
                return _venue_from_profile(stores, ig, cached['pk'], cached)
            continue
        pk = None
        if health.pick_account(stores.archive, handlers.ENDPOINT_PROFILE, [account_id]) is not None:
            pk = handlers.resolve_pk(stores, transport, account_id, ig)
        # without pk, the profile page by nickname will return the bio — search isn't needed at all then
        prof, outcome = handlers.fetch_profile(stores, transport, account_id, pk, ig)
        if outcome == handlers.BLOCKED:
            raise Retry('шапка упомянутого аккаунта недоступна ни API, ни страницей')
        if not prof:
            continue
        pk = pk or prof.get('pk')
        if not pk:
            continue
        contacts.cache_profile(stores, pk, dict(prof, username=prof.get('username') or ig))
        if _is_place(ig, prof):
            return _venue_from_profile(stores, ig, pk, prof)
    if payload.get('source') == 'venue_header' and payload.get('venue'):
        row = stores.main.execute(
            'SELECT name, city, level, ig_account FROM venues WHERE name = %s AND city = %s',
            (payload['venue'], payload.get('city') or '')).fetchone()
        if row:
            return dict(row, category='')
    return None


def fetch_feed(stores, transport, account_id, pk, nick):
    """Feed: the feed/user API, falling back to the /embed/ page on a block (last 6
    posts plus a recent-story flag). None means both routes are unavailable —
    requeue the task."""
    archive = stores.archive
    if health.pick_account(archive, ENDPOINT_FEED, [account_id]) is not None:
        items, status = transport.user_feed(pk)
        if status not in FAILURES:
            health.record_success(archive, account_id, ENDPOINT_FEED)
            return {'items': items or [], 'endpoint': ENDPOINT_FEED}
        health.record_failure(archive, account_id, ENDPOINT_FEED, status)
        if status in handlers.ACCOUNT_DOWN:
            return None
    if health.pick_account(archive, ENDPOINT_EMBED, [account_id]) is not None:
        data, status = transport.embed_feed(nick)
        if status == EMPTY:
            # the /embed/ page came back with no feed — private or hidden profile
            health.record_success(archive, account_id, ENDPOINT_EMBED)
            return {'items': [], 'endpoint': ENDPOINT_EMBED, 'hidden': True}
        if status not in FAILURES:
            health.record_success(archive, account_id, ENDPOINT_EMBED)
            data = data or {}
            return {'items': data.get('items') or [], 'endpoint': ENDPOINT_EMBED,
                    'media_count': data.get('media_count'),
                    'has_public_story': data.get('has_public_story')}
        health.record_failure(archive, account_id, ENDPOINT_EMBED, status)
    return None


THUMB_THREADS = 6


def download_thumbs(transport, items):
    """Grid thumbnails fetched in parallel, order preserved: [(bytes | None, status | None)].
    12 images one by one through a VPN take ~6s per profile (measured 2026-09-30)."""
    def one(it):
        return transport.download(it['thumb']) if it.get('thumb') else (None, None)
    with ThreadPoolExecutor(THUMB_THREADS) as ex:
        return list(ex.map(one, items))


def _top_up(stores, transport, account_id, nick, items, media_count, force=False):
    """The card needs 12 posts, but /embed/ only gives 6: top up via a profile-page
    request. Only done for candidates that passed the rules; if it doesn't work out,
    we keep what we have — not worth a retry. force means we need post location tags
    (/embed/ has none), even if there are already enough posts."""
    if not hasattr(transport, 'web_feed'):
        return items
    if not force and (len(items) >= 12 or (media_count or 0) <= len(items)):
        return items
    if health.pick_account(stores.archive, ENDPOINT_WEB_FEED, [account_id]) is None:
        return items
    more, status = transport.web_feed(nick)
    if status in FAILURES:
        health.record_failure(stores.archive, account_id, ENDPOINT_WEB_FEED, status)
        return items
    health.record_success(stores.archive, account_id, ENDPOINT_WEB_FEED)
    return more if more and (len(more) > len(items) or force) else items


def _freshness(stores, transport, account_id, pk, items):
    """(days, unix timestamp) from a post or a recent story. Raises Retry if posts are
    older than the window and stories can't be checked: stories count the same as posts."""
    latest = max([it['taken_at'] for it in items if it.get('taken_at')] or [0])
    days = int((time.time() - latest) / 86400) if latest else None
    if days is not None and days <= rules.WINDOW_DAYS:
        return days, latest
    if health.pick_account(stores.archive, ENDPOINT_HIGHLIGHTS, [account_id]) is None:
        raise StoryRetry('актуальные истории остывают')
    story, status = transport.highlights_latest(pk)
    if status in FAILURES:
        health.record_failure(stores.archive, account_id, ENDPOINT_HIGHLIGHTS, status)
        raise StoryRetry('актуальные истории: %s' % status)
    health.record_success(stores.archive, account_id, ENDPOINT_HIGHLIGHTS)
    if story:
        story_days = int((time.time() - story) / 86400)
        if days is None or story_days < days:
            return story_days, story
    return days, latest


def _review_grid(stores, transport, account_id, cand, items, media_count):
    """12-post grid for a rejection card (rejection-review mode). Any failure here just
    means the card ships without an image: the rejection is already decided, so there's
    no point retrying the task."""
    try:
        if items is None:
            feed = fetch_feed(stores, transport, account_id, cand['pk'], cand['nick'])
            if not feed or not feed['items']:
                return None
            items, media_count = feed['items'], media_count or feed.get('media_count')
        items = _top_up(stores, transport, account_id, cand['nick'], items, max(media_count or 0, 12))
        thumbs = [data for data, _ in download_thumbs(transport, items[:12])]
        path = grid.path_for(cand['nick'])
        return path if grid.compose(thumbs, path) else None
    except Exception as e:  # noqa: BLE001 — a card without a grid beats a stuck rejection
        print('отбор: @%-24s сетка для карточки отказа не собрана: %s' % (cand['nick'], e))
        return None


def _reject(stores, cand, reason, task, stage='правила', ctx=None):
    """ctx = (transport, account_id, feed or None, post count) — for the rejection card."""
    path = None
    if ctx is not None and review.rejects_on(stores) and review.worth_review(
            reason, stage, cand['bio'], cand['full_name'], cand['category']):
        path = _review_grid(stores, ctx[0], ctx[1], cand, ctx[2], ctx[3])
    contacts.reject(stores, cand['nick'], reason, stage, pk=cand['pk'],
                    full_name=cand['full_name'], venue=cand['venue'], city=cand['city'],
                    source=cand['source'], bio=cand['bio'], grid_path=path)
    contacts.drop_candidate(stores, cand['nick'])
    wq.complete(stores.archive, task['id'])
    print('отбор: @%-24s отказ — %s' % (cand['nick'], reason))


def _role(stores, cand):
    """(role, stop, cap_reason, disabled_role)."""
    enabled = roles.load_enabled(stores.main)
    role, stop = roles.match(enabled, cand['category'], cand['bio'])
    if role is not None:
        return role, stop, None, None
    hint = (cand['role_hint'] or '').strip()
    if cand['source'] == 'venue_header' and hint and hint.lower() != 'упомянут':
        by_venue, venue_stop = roles.match(enabled, '', hint)
        if by_venue is not None and not venue_stop:
            return by_venue, None, 'должность названа заведением, в шапке её нет', None
    everything = stores.main.execute(
        'SELECT * FROM role_profiles WHERE NOT enabled ORDER BY priority, code').fetchall()
    off, _ = roles.match(everything, cand['category'], cand['bio'])
    return None, stop, None, off


def _consulting_instead(stores, role, venue, department, cand):
    """A chef with no venue of their own who launches restaurants, in the consulting
    department, is reclassified as a consultant — otherwise the grader would reject
    them as "a chef with no venue"."""
    if venue or not role or role.get('code') == 'consulting':
        return role
    consult = next((p for p in roles.load_enabled(stores.main) if p['code'] == 'consulting'), None)
    if not consult or (consult.get('departments') and department not in consult['departments']):
        return role
    found, stop = roles.match([consult], cand['category'], cand['bio'])
    return consult if found and not stop else role


def _default_role(stores):
    """An approved contact with no role in the bio defaults to chef: the main collection role."""
    rows = stores.main.execute("SELECT * FROM role_profiles WHERE code = 'chef'").fetchall()
    return rows[0] if rows else roles.load_enabled(stores.main)[0]


def enqueue_missing(stores):
    """Candidates with no live vet task (from plan 1, or after attempts ran out)."""
    rows = stores.archive.execute(
        "SELECT c.nick, c.pk FROM candidates c WHERE NOT EXISTS ("
        " SELECT 1 FROM work_queue q WHERE q.task_type = 'vet' AND q.payload->>'nick' = c.nick"
        " AND q.status IN ('pending', 'in_progress'))").fetchall()
    for r in rows:
        wq.enqueue(stores.archive, 'vet', {'nick': r['nick'], 'pk': r['pk']}, priority=40)
    return len(rows)


def _refetch(stores, transport, account_id, cand, pk, task):
    """Re-fetch the bio and pk from Instagram. (None, None) means the task was already
    requeued or closed."""
    archive = stores.archive
    nick = cand['nick']
    if not pk:
        pk = handlers.resolve_pk(stores, transport, account_id, nick)
        if not pk:
            handlers._requeue_or_close(stores, task)
            return None, None
    profile, outcome = handlers.fetch_profile(stores, transport, account_id, pk, nick)
    if outcome == handlers.BLOCKED:
        wq.fail(archive, task['id'], requeue=True)
        return None, None
    if profile:
        contacts.cache_profile(stores, pk, profile)
        archive.execute(
            'UPDATE candidates SET pk = %s, full_name = %s, bio = %s, category = %s, city_name = %s '
            'WHERE nick = %s', (str(pk), profile.get('full_name') or '', profile.get('biography') or '',
                                profile.get('category') or '', profile.get('city_name') or '', nick))
    cand = archive.execute('SELECT * FROM candidates WHERE nick = %s', (nick,)).fetchone()
    return cand, pk


def handle_vet(stores, transport, account_id, task):
    archive = stores.archive
    nick = contacts.norm_nick(task['payload']['nick'])
    cand = archive.execute('SELECT * FROM candidates WHERE nick = %s', (nick,)).fetchone()
    if not cand:
        wq.complete(archive, task['id'])
        return
    pk = cand['pk'] or task['payload'].get('pk')
    if (task['payload'].get('approved') or cand.get('status') == 'одобрен') and not (cand['bio'] or '').strip():
        # an approved contact from the old archive has neither bio nor pk: without them the contact would come out empty
        cand, pk = _refetch(stores, transport, account_id, cand, pk, task)
        if cand is None:
            return
    cached = archive.execute('SELECT posts, is_private FROM profile_cache WHERE pk = %s',
                             (str(pk),)).fetchone() or {}
    profile = {'full_name': cand['full_name'], 'biography': cand['bio'],
               'category': cand['category'], 'media_count': cached.get('posts'),
               'is_private': cached.get('is_private')}
    if not task['payload'].get('approved') and cand.get('status') != 'одобрен' and handlers._venue_naming_staff(
            nick, {'biography': cand['bio'], 'full_name': cand['full_name'], 'category': cand['category']}):
        # the venue account names its own chef — the chef will be picked up from the venue's bio, via the following walk
        contacts.reject(stores, nick, handlers.PLACE_WALK_REASON, 'роль', pk=pk, full_name=cand['full_name'],
                        city=cand['city'], source=cand['source'])
        contacts.drop_candidate(stores, nick)
        wq.enqueue(archive, 'venue_header', {'venue_name': cand['full_name'] or nick, 'city': cand['city'] or '',
                                             'ig_account': nick, 'pk': str(pk)}, priority=60)
        wq.complete(archive, task['id'])
        return
    role, role_stop, cap_reason, off = _role(stores, cand)
    # client overturned the rejection: rules no longer reject the contact, their objections become a doubt instead
    approved = bool(task['payload'].get('approved')) or cand.get('status') == 'одобрен'
    doubts = []

    def refuse(reason, stage='правила', ctx=None):
        if approved:
            if not any(reason.startswith(d) or d.startswith(reason) for d in doubts):
                doubts.append(reason)
            return False
        _reject(stores, cand, reason, task, stage=stage, ctx=ctx)
        return True
    if approved and role is None:
        role = off or _default_role(stores)
        doubts.append('должности нет в шапке')
    elif off is not None:
        _reject(stores, cand, 'должность выключена: %s' % off['title'], task, stage='роль_выкл')
        return
    # free rules — before any request; post count is known from the profile cache
    free = dict(profile, media_count=profile['media_count'] if profile['media_count'] is not None else 3)
    ok, reason = rules.precheck(free, role, role_stop)
    if not ok and refuse(reason, ctx=(transport, account_id, None, profile['media_count'])):
        return
    # "abroad" check from the bio, including inside @handles (@sorrel1.tashkent, @willow1.kz) — before any requests
    where = passport.abroad(stores.main, cand['bio'])
    if where and refuse('вне РФ по шапке: %s' % where):
        return
    feed = fetch_feed(stores, transport, account_id, pk, nick)
    if feed is None:
        wq.fail(archive, task['id'], requeue=True)
        print('отбор: @%-24s повтор — лента недоступна ни API, ни /embed/' % nick)
        return
    if feed.get('hidden') and refuse('закрытый профиль или лента скрыта'):
        return
    items, endpoint = feed['items'], feed['endpoint']
    if profile['media_count'] is None and feed.get('media_count') is not None:
        profile['media_count'] = feed['media_count']
    if not items and (profile['media_count'] or 0) >= 3:
        # the profile has posts but the feed came back empty — a silent block, not a "dead profile"
        _requeue(stores, account_id, endpoint, 'soft_block', task)
        return
    if profile['media_count'] is None:
        profile['media_count'] = len(items)
    story_cap = None
    try:
        if feed.get('has_public_story'):
            fresh_days, fresh_ts = 0, int(time.time())  # has a story right now -> active
        else:
            try:
                fresh_days, fresh_ts = _freshness(stores, transport, account_id, pk, items)
            except StoryRetry:
                if (task.get('attempts') or 0) < STORY_RETRIES:
                    raise
                # stories are blocked on this IP for a long time — let a human decide instead of looping forever
                fresh_days = 0
                fresh_ts = max([it['taken_at'] for it in items if it.get('taken_at')] or [0])
                story_cap = 'истории не проверены: посты старше %d дней' % rules.WINDOW_DAYS
        venue = resolve_venue(stores, transport, account_id, cand['bio'], dict(cand))
    except Retry as e:
        wq.fail(archive, task['id'], requeue=True)
        print('отбор: @%-24s повтор — %s' % (nick, e))
        return
    if venue and not venue.get('city') and venue.get('ig_account'):
        # the venue from the bio has no city — check the location tags on its posts (e.g. "На Крыше" -> Astana)
        kind, venue_city, where = _venue_geo(stores, transport, venue['ig_account'])
        if kind == 'foreign' and refuse('заведение вне РФ: %s — %s' % (venue.get('name') or venue['ig_account'], where)):
            return
        if kind == 'ru' and venue_city:
            venue = dict(venue, city=venue_city)
    # city comes from the venue, the profile's business fields, or the bio's prose; not
    # from @handles or the full name ("moscow" in @moscow.syncafe.uz, "Artem Orlov" -> city
    # Артём — 2026-10-06)
    city = ((venue or {}).get('city') or cand['city_name'] or passport.bio_city(stores.main, cand['bio']) or '')
    if not city:
        # no city named by the venue or the bio — check post location tags (client,
        # 2026-09-30: "a lot of foreign chefs... we need RF (Russia) geo")
        if not any(it.get('loc') for it in items):
            items = _top_up(stores, transport, account_id, nick, items, profile['media_count'], force=True)
        locs = [it.get('loc') for it in items]
        kind, post_city = geo_points.verdict(locs)
        if kind == 'ru' and post_city:
            city = post_city
        elif kind == 'foreign':
            if refuse('вне РФ по геометкам постов: %s' % ', '.join(geo_points.names(locs)[:3])):
                return
        elif kind == 'none' and not review.CYRILLIC.search('%s %s' % (cand['bio'] or '', cand['full_name'] or '')):
            if refuse('нет признаков РФ: шапка не на русском, геометок в РФ нет'):
                return
    department = geo.department_for_city(stores.main, city) if city else None
    role = _consulting_instead(stores, role, venue, department, cand)
    ok, reason, needs_strong = rules.evaluate(profile, role, role_stop, venue, fresh_days,
                                              city or None, department)
    if not ok and refuse(reason, ctx=(transport, account_id, items, profile['media_count'])):
        return
    if not department:
        cap_reason = cap_reason or 'город не определён'
    cap_reason = '; '.join(x for x in [cap_reason or story_cap] + doubts if x) or None
    items = _top_up(stores, transport, account_id, nick, items, profile['media_count'])
    thumbs, wanted, failed = [], 0, 0
    for it, (data, dstatus) in zip(items[:12], download_thumbs(transport, items[:12])):
        if it.get('thumb'):
            wanted += 1
            if dstatus != OK:
                data, failed = None, failed + 1
        thumbs.append(data)
    if failed and wanted - failed < MIN_TILES:
        # a grid with gray tiles from a CDN failure gets a confident rejection from the grader — retry later
        health.record_failure(archive, account_id, ENDPOINT_CDN, 'soft_block')
        wq.fail(archive, task['id'], requeue=True)
        print('отбор: @%-24s повтор — миниатюры не скачались (%d из %d)' % (nick, failed, wanted))
        return
    name = person_name(cand['full_name'] or '')
    twin = passport.duplicate_of(stores, nick, cand['bio'], ' '.join(name) if name else '', (venue or {}).get('name') or '')
    if twin and refuse('дубль @%s — тот же человек' % twin):
        return
    path = grid.path_for(nick)
    tiles = grid.compose(thumbs, path)
    contacts.promote(stores, {
        'nick': nick, 'pk': str(pk), 'type': 'контакт', 'name': ' '.join(name) if name else '',
        'role': role['crm_role'], 'role_code': role['code'], 'crm_position': role['crm_position'],
        'venue': (venue or {}).get('name') or '', 'city': city, 'department': department,
        'level': (venue or {}).get('level') or '',
        # a client-approved contact skips the grader: with a department -> straight to export, without -> ask about the department
        'status': ('к_выгрузке' if department else 'спорный') if approved else 'сетка_готова',
        'last_activity': date.fromtimestamp(fresh_ts).isoformat() if fresh_ts else '',
        'checked_date': date.today().isoformat(), 'found_via': cand['source'],
        'agent': cand['agent'], 'bio': cand['bio'],
        'captions': [it.get('caption') or '' for it in items[:12]], 'grid_path': path,
        'needs_strong_profile': needs_strong, 'grid_tiles': tiles, 'cap_reason': cap_reason})
    contacts.drop_candidate(stores, nick)
    wq.complete(archive, task['id'])
    print('отбор: @%-24s сетка готова — %s, %s' % (nick, (venue or {}).get('name') or '—', department))

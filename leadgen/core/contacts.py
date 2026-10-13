"""Dedup and person records.

The key is the lowercased nick without @ and a trailing dot; plus pk, which
catches a nick change. The check runs against BOTH databases: accepted
contacts live in main, candidates in progress and rejects live in archive.
A rejected lead the dedup can't see keeps coming back."""
from psycopg.types.json import Jsonb

BIO_LIMIT = 500

DEDUP_TABLES = (('main', 'contacts'), ('archive', 'candidates'), ('archive', 'rejected'))


def norm_nick(nick):
    return (nick or '').strip().lstrip('@').rstrip('.').lower()


def is_known(stores, nick, pk=None):
    n = norm_nick(nick)
    for base, table in DEDUP_TABLES:
        conn = getattr(stores, base)
        if pk:
            row = conn.execute('SELECT 1 FROM %s WHERE nick = %%s OR pk = %%s LIMIT 1' % table,
                               (n, str(pk))).fetchone()
        else:
            row = conn.execute('SELECT 1 FROM %s WHERE nick = %%s LIMIT 1' % table,
                               (n,)).fetchone()
        if row:
            return True
    return False


def add_candidate(stores, nick, pk, profile, payload, agent):
    stores.archive.execute('''
        INSERT INTO candidates (nick, pk, full_name, bio, category, city_name, role_hint,
                                venue, city, source, agent, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'обогащён')
        ON CONFLICT (nick) DO NOTHING''', (
        norm_nick(nick), str(pk) if pk else None, profile.get('full_name', ''),
        (profile.get('biography') or '')[:BIO_LIMIT], profile.get('category', ''),
        profile.get('city_name', ''), payload.get('role', ''), payload.get('venue', ''),
        payload.get('city', ''), payload.get('source', ''), agent))


def reject(stores, nick, reason, stage, pk=None, full_name='', venue='', city='', source='',
           legacy=None, bio=None, grid_path=None):
    stores.archive.execute('''
        INSERT INTO rejected (nick, pk, full_name, venue, city, reason, stage, source, legacy,
                              bio, grid_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (nick) DO NOTHING''', (
        norm_nick(nick), str(pk) if pk else None, full_name, venue, city, reason, stage,
        source, Jsonb(legacy) if legacy is not None else None,
        (bio or '')[:BIO_LIMIT] or None, grid_path))


def cache_profile(stores, pk, profile):
    stores.archive.execute('''
        INSERT INTO profile_cache (pk, nick, full_name, city, bio, category, followers, posts,
                                   is_private, checked_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (pk) DO UPDATE SET
            nick = excluded.nick, full_name = excluded.full_name, city = excluded.city,
            bio = excluded.bio, category = excluded.category, followers = excluded.followers,
            posts = excluded.posts, is_private = excluded.is_private, checked_at = now()''', (
        str(pk), norm_nick(profile.get('username')), profile.get('full_name', ''),
        profile.get('city_name', ''), (profile.get('biography') or '')[:BIO_LIMIT],
        profile.get('category', ''), profile.get('follower_count'), profile.get('media_count'),
        profile.get('is_private')))
def drop_candidate(stores, nick):
    stores.archive.execute('DELETE FROM candidates WHERE nick = %s', (norm_nick(nick),))


PROMOTE_COLUMNS = ('nick', 'pk', 'type', 'name', 'role', 'role_code', 'crm_position', 'venue',
                   'city', 'department', 'level', 'status', 'last_activity', 'checked_date',
                   'found_via', 'agent', 'bio', 'captions', 'grid_path', 'needs_strong_profile',
                   'grid_tiles', 'cap_reason')


def promote(stores, fields):
    """The candidate passed the rules — into the main database. The caller deletes the candidate."""
    values = dict(fields)
    values['nick'] = norm_nick(values['nick'])
    values['captions'] = Jsonb(values.get('captions') or [])
    cur = stores.main.execute(
        'INSERT INTO contacts (%s) VALUES (%s) ON CONFLICT (nick) DO NOTHING'
        % (', '.join(PROMOTE_COLUMNS), ', '.join(['%s'] * len(PROMOTE_COLUMNS))),
        [values.get(c) for c in PROMOTE_COLUMNS])
    return cur.rowcount == 1

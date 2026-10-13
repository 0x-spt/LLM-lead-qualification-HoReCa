"""Account pool: own accounts (cookies + proxy) and HikerAPI.

Mode — settings.ig_transport in the main database:
    own    — own accounts only
    hiker  — HikerAPI only
    hybrid — own accounts first; HikerAPI takes a task only when the needed
             endpoint is cooling down for all own accounts

Cookies, proxies and the HikerAPI key live only in the archive database on
the server."""
import threading

import httpx
from psycopg.types.json import Jsonb

from leadgen.core import alerts, settings
from leadgen.access.transport.hiker import HikerTransport
from leadgen.access.transport.own import OwnTransport

MODES = ('own', 'hiker', 'hybrid')
# anonymous readers work in any mode: they need neither a login nor a paid key
KINDS_BY_MODE = {'own': ['own', 'anon'], 'hiker': ['hiker', 'anon'], 'hybrid': ['own', 'hiker', 'anon']}


def register_anon(conn, label, proxy):
    """A logged-out reader: the profile page and /embed/ are reachable without cookies (measured 2026-10-01)."""
    return conn.execute('''
        INSERT INTO accounts (label, kind, cookies, proxy, status)
        VALUES (%s, 'anon', '{}'::jsonb, %s, 'active')
        ON CONFLICT (label) DO UPDATE SET proxy = excluded.proxy, status = 'active'
        RETURNING id''', (label, proxy)).fetchone()['id']


def register_own(conn, label, cookies, proxy=None, user_agent=None):
    row = conn.execute('''
        INSERT INTO accounts (label, kind, cookies, proxy, user_agent, status, last_login_at)
        VALUES (%s, 'own', %s, %s, %s, 'active', now())
        ON CONFLICT (label) DO UPDATE SET
            cookies = excluded.cookies, proxy = excluded.proxy,
            user_agent = excluded.user_agent, status = 'active', last_login_at = now()
        RETURNING id''', (label, Jsonb(cookies), proxy, user_agent)).fetchone()
    # a new session — this account's previous cooldowns no longer mean anything
    conn.execute('DELETE FROM endpoint_health WHERE account_id = %s', (row['id'],))
    return row['id']


WARMUP_HOURS = 24


def start_warmup(conn, account_id, hours=WARMUP_HOURS):
    """A new account only reads profiles for the first day (health.WARMUP_ENDPOINTS)."""
    conn.execute('UPDATE accounts SET warmup_until = now() + make_interval(hours => %s) WHERE id = %s',
                 (hours, account_id))


def register_hiker(conn, api_key):
    row = conn.execute('''
        INSERT INTO accounts (label, kind, api_key, status) VALUES ('hiker', 'hiker', %s, 'active')
        ON CONFLICT (label) DO UPDATE SET api_key = excluded.api_key, status = 'active'
        RETURNING id''', (api_key,)).fetchone()
    conn.execute('DELETE FROM endpoint_health WHERE account_id = %s', (row['id'],))
    return row['id']


def transport_mode(main_conn):
    mode = settings.get(main_conn, 'ig_transport', 'own')
    return mode if mode in MODES else 'own'


def active_accounts(conn, mode):
    return conn.execute(
        "SELECT * FROM accounts WHERE status = 'active' AND kind = ANY(%s) ORDER BY id",
        (KINDS_BY_MODE[mode],)).fetchall()


DOWN_ALERT_MINUTES = 15


def probe_proxy(proxy, tries=3):
    """An exit is alive if Instagram answered through it tries times in a row."""
    try:
        with httpx.Client(proxy=proxy, timeout=15) as c:
            return all(c.get('https://www.instagram.com/', headers={'user-agent': 'Mozilla/5.0'}).status_code
                       in (200, 301, 302) for _ in range(tries))
    except Exception:  # noqa: BLE001 — any connection failure means the exit is dead for now
        return False


def revive_dead(conn, probe=probe_proxy):
    """Accounts whose exit died: revived — back to work; down longer than
    DOWN_ALERT_MINUTES — a single alert. Returns the labels of those revived."""
    back = []
    for row in conn.execute(
            "SELECT id, label, proxy, down_alerted, now() - coalesce(down_at, now()) > %s * interval '1 minute' AS long "
            "FROM accounts WHERE status = 'proxy_dead' AND kind IN ('own', 'anon') AND coalesce(proxy, '') <> ''",
            (DOWN_ALERT_MINUTES,)).fetchall():
        if probe(row['proxy']):
            conn.execute("UPDATE accounts SET status = 'active', down_at = NULL, down_alerted = false WHERE id = %s",
                         (row['id'],))
            # endpoints that were cooling down because of the dead exit — back to work immediately
            conn.execute("UPDATE endpoint_health SET cooldown_until = NULL WHERE account_id = %s AND status = 'proxy_dead'",
                         (row['id'],))
            back.append(row['label'])
        elif row['long'] and not row['down_alerted']:
            alerts.raise_alert(conn, 'proxy_dead', 'Аккаунт %s: выход VPN не отвечает больше %d минут. '
                               'Проверьте VPN или замените прокси.' % (row['label'], DOWN_ALERT_MINUTES))
            conn.execute('UPDATE accounts SET down_alerted = true WHERE id = %s', (row['id'],))
    return back


def build_transport(row):
    if row['kind'] == 'hiker':
        return HikerTransport(row['api_key'])
    if row['kind'] == 'anon':
        return OwnTransport({}, proxy=row.get('proxy'))
    return OwnTransport(row['cookies'] or {}, proxy=row['proxy'], user_agent=row['user_agent'])


class Pool:
    """Transports of active accounts, with one lock per account: at any
    moment each account makes at most one request — concurrency is capped per account."""

    def __init__(self, rows, factory=build_transport):
        self.transports = {r['id']: factory(r) for r in rows}
        self.own_ids = [r['id'] for r in rows if r['kind'] == 'own']
        self.hiker_ids = [r['id'] for r in rows if r['kind'] == 'hiker']
        self.anon_ids = [r['id'] for r in rows if r['kind'] == 'anon']
        self.locks = {aid: threading.Lock() for aid in self.transports}

    @classmethod
    def from_transports(cls, own, hiker=None, anon=None):
        rows = [{'id': aid, 'kind': 'own'} for aid in own]
        rows += [{'id': aid, 'kind': 'hiker'} for aid in (hiker or {})]
        rows += [{'id': aid, 'kind': 'anon'} for aid in (anon or {})]
        everything = dict(own)
        everything.update(hiker or {})
        everything.update(anon or {})
        return cls(rows, factory=lambda r: everything[r['id']])

    def tiers(self):
        return [ids for ids in (self.own_ids, self.hiker_ids) if ids]

    def free(self, ids):
        return [aid for aid in ids if not self.locks[aid].locked()]

    def signature(self):
        return tuple(sorted(self.transports))

    def close(self):
        for t in self.transports.values():
            close = getattr(t, 'close', None)
            if close:
                close()

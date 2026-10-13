"""Two databases instead of a single SQLite file.

Main (Supabase) — accepted contacts, the venue registry, geography, settings.
Archive (local Postgres) — rejects, the profile cache, the queue, accounts
with cookies and proxies. URLs come from the environment: MAIN_DB_URL,
ARCHIVE_DB_URL.

Connect to Supabase through the Transaction pooler (port 6543): the free
tier's direct connection is IPv6-only, and the Session pooler caps the
client count at the pool size (~15) — with 15+ accounts, workers would fail
to connect. The code holds no session state, and prepared statements are
disabled."""
import os
import re
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_MAIN = os.path.join(HERE, 'schema_main.sql')
SCHEMA_ARCHIVE = os.path.join(HERE, 'schema_archive.sql')


# A silently dropped connection (NAT, pooler) with no timeouts set hangs
# execute() for the duration of the TCP retries — roughly 15 minutes. By
# then the task's lease has expired and a second worker claims it. Timeouts
# and keepalive turn the drop into a fast error instead.
CONNECT_OPTIONS = {
    'connect_timeout': 10,
    'keepalives': 1, 'keepalives_idle': 30, 'keepalives_interval': 10, 'keepalives_count': 3,
    'options': '-c statement_timeout=30000',
}


def connect(url):
    # autocommit: every write is committed immediately, so a crashed worker
    # never leaves open transactions holding locks on queue rows.
    # prepare_threshold=None: the Supabase pooler doesn't get along with prepared statements.
    return psycopg.connect(url, autocommit=True, row_factory=dict_row,
                           prepare_threshold=None, **CONNECT_OPTIONS)


def _apply(conn, path):
    with open(path, encoding='utf-8') as f:
        # drop "--" comments first: a ';' inside a comment would otherwise split a statement
        sql = re.sub(r'--[^\n]*', '', f.read())
    for statement in sql.split(';'):
        if statement.strip():
            conn.execute(statement)


def init_main(conn):
    _apply(conn, SCHEMA_MAIN)
    # without role profiles, every candidate gets rejected as "role not in the bio"
    from leadgen.qualify import roles
    roles.ensure_defaults(conn)


def init_archive(conn):
    _apply(conn, SCHEMA_ARCHIVE)


@dataclass
class Stores:
    main: psycopg.Connection
    archive: psycopg.Connection

    def close(self):
        for conn in (self.main, self.archive):
            try:
                conn.close()
            except Exception:
                pass


def open_stores(main_url=None, archive_url=None):
    return Stores(connect(main_url or os.environ['MAIN_DB_URL']),
                  connect(archive_url or os.environ['ARCHIVE_DB_URL']))

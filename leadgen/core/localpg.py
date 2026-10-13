"""Local Postgres for running workers on a Mac without Docker.

    python -m leadgen.core.localpg     # prints export lines for .env

Data lives in db/pgdata (in .gitignore). On the server this is replaced by
a Postgres container (plan 4) and Supabase."""
import os

import psycopg
from psycopg.conninfo import make_conninfo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PGDATA = os.path.join(ROOT, 'db', 'pgdata')
DBS = {'MAIN_DB_URL': 'leadgen_main', 'ARCHIVE_DB_URL': 'leadgen_archive'}


def main():
    import pgserver
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    base = server.get_uri()
    with psycopg.connect(base, autocommit=True) as admin:
        existing = {r[0] for r in admin.execute('SELECT datname FROM pg_database').fetchall()}
        for name in DBS.values():
            if name not in existing:
                admin.execute('CREATE DATABASE %s' % name)
    for env, name in DBS.items():
        print("export %s='%s'" % (env, make_conninfo(base, dbname=name)))


if __name__ == '__main__':
    main()

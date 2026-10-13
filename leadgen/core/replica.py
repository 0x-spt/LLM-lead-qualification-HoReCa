"""Replica of the main database: the working main database is a local
Postgres on the Mac, and Supabase is its copy, refreshed every INTERVAL
seconds.

Measured 2026-09-30: a query to the Supabase pooler over VPN took 135 ms, and
connection drops (124 in 3,000 lines of worker logs) cost a worker 40-100 s
each — workers were idle ~80% of the time. Supabase stays a full copy of the
accepted contacts — honoring the client's decision "accepted contacts live
in Supabase" — but collection no longer depends on it.

    deploy/local/run.sh leadgen.core.replica pull   # one-off: Supabase → local (before switching over)
    deploy/local/run.sh leadgen.core.replica        # service: local → Supabase on a loop

Environment: MAIN_DB_URL (local), REPLICA_DB_URL (Supabase, Session pooler 5432).
"""
import os
import sys
import time

from leadgen.core import db

TABLES = ('geo', 'settings', 'role_profiles', 'venues', 'contacts', 'verdicts', 'exports')
SEQUENCES = {'venues': 'id', 'verdicts': 'id', 'exports': 'id'}
INTERVAL = 300
RETRY_SECONDS = 60  # Supabase dropped the connection — retry in a minute, not a full cycle


def _columns(conn, table):
    return [r['column_name'] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
        'AND table_name = %s ORDER BY ordinal_position', (table,)).fetchall()]


def copy_all(src, dst):
    """All tables src → dst in a single transaction on dst: readers of the
    replica never see half-empty tables, and a failure partway through never
    leaves it truncated. Returns {table: (rows in src, rows in dst)}."""
    counts = {}
    with dst.transaction():
        dst.execute('TRUNCATE %s' % ', '.join(TABLES))
        for table in TABLES:
            dst_cols = set(_columns(dst, table))
            cols = [c for c in _columns(src, table) if c in dst_cols]
            collist = ', '.join('"%s"' % c for c in cols)
            with src.cursor(binary=True).copy('COPY %s (%s) TO STDOUT (FORMAT BINARY)' % (table, collist)) as out, \
                    dst.cursor().copy('COPY %s (%s) FROM STDIN (FORMAT BINARY)' % (table, collist)) as inp:
                for data in out:
                    inp.write(data)
            if table in SEQUENCES:
                dst.execute('SELECT setval(pg_get_serial_sequence(%%s, %%s), coalesce((SELECT max(%s) FROM %s), 0) + 1, false)'
                            % (SEQUENCES[table], table), (table, SEQUENCES[table]))
            counts[table] = (src.execute('SELECT count(*) AS c FROM %s' % table).fetchone()['c'],
                             dst.execute('SELECT count(*) AS c FROM %s' % table).fetchone()['c'])
            if counts[table][0] != counts[table][1]:
                raise RuntimeError('реплика: расхождение в %s: %s' % (table, counts[table]))
    return counts


def pull():
    """One-off switchover: Supabase → local main database."""
    src = db.connect(os.environ['REPLICA_DB_URL'])
    dst = db.connect(os.environ['MAIN_DB_URL'])
    db.init_main(dst)
    for table, (a, b) in copy_all(src, dst).items():
        print('%-14s Supabase %6d → локально %6d' % (table, a, b))


def serve():
    while True:
        try:
            src = db.connect(os.environ['MAIN_DB_URL'])
            dst = db.connect(os.environ['REPLICA_DB_URL'])
            try:
                db.init_main(dst)  # new columns in the local schema also go to the replica
                counts = copy_all(src, dst)
                print('реплика: Supabase обновлена (контактов %d)' % counts['contacts'][1], flush=True)
            finally:
                src.close()
                dst.close()
        except Exception as e:  # noqa: BLE001 — Supabase unreachable: retry in a minute
            print('реплика: не обновлена (%s)' % str(e).split('\n')[0][:160], file=sys.stderr, flush=True)
            time.sleep(RETRY_SECONDS)
            continue
        time.sleep(INTERVAL)


if __name__ == '__main__':
    pull() if sys.argv[1:] == ['pull'] else serve()

"""Nightly backup of both databases (pg_dump, 14 days) and the contacts.csv mirror.

    python -m leadgen.core.backup        # into $LEADGEN_BACKUP_DIR (default: db/backups)"""
import os
import re
import shutil
import subprocess
import time
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


RETRIES = 3
RETRY_PAUSE = 30


class BackupError(Exception):
    pass


def _redact(text, url):
    text = re.sub(r'://([^:/@\s]+):[^@\s]+@', r'://\1:***@', text or '')
    return text.replace(url, '<url>') if url else text


def dump(url, path, pg_dump='pg_dump', schemas=None):
    cmd = [pg_dump, '--format=custom', '--no-owner', '--file', path]
    for schema in schemas or []:
        cmd += ['--schema', schema]
    proc = subprocess.run(cmd + [url], capture_output=True, text=True)
    if proc.returncode != 0:
        # the password from the URL must not end up in container logs
        raise BackupError('pg_dump: код %d: %s' % (proc.returncode, _redact(proc.stderr, url)[-500:]))


def prune(root, keep_days=14, today=None):
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    removed = []
    for name in sorted(os.listdir(root)):
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if day <= cutoff:  # keep exactly the last 14 days
            path = os.path.join(root, name)
            shutil.rmtree(path)
            removed.append(path)
    return removed


def export_csv(url, folder):
    """All tables of the public schema — to CSV with a header, independent of
    pg_dump and server versions. Restore: COPY <table> FROM '<file>' CSV HEADER."""
    from leadgen.core import db
    os.makedirs(folder, exist_ok=True)
    conn = db.connect(url)
    try:
        tables = [r['tablename'] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename").fetchall()]
        for table in tables:
            with open(os.path.join(folder, '%s.csv' % table), 'wb') as f, \
                    conn.cursor().copy('COPY %s TO STDOUT (FORMAT CSV, HEADER)' % table) as copy:
                for data in copy:
                    f.write(data)
    finally:
        conn.close()
    return tables


def run(root, pg_dump='pg_dump'):
    """Each database separately: a failure of one doesn't cancel the other. Returns (folder, errors)."""
    folder = os.path.join(root, date.today().isoformat())
    os.makedirs(folder, exist_ok=True)
    errors = []
    # Supabase: dump through the Session pooler or a direct connection (the
    # Transaction pooler drops session-level SET), and only the public schema —
    # the auth/storage internals aren't ours
    main_url = os.environ.get('MAIN_DB_DUMP_URL') or os.environ['MAIN_DB_URL']
    for name, url, schemas in (('main', main_url, ['public']),
                               ('archive', os.environ['ARCHIVE_DB_URL'], None)):
        try:
            dump(url, os.path.join(folder, '%s.dump' % name), pg_dump, schemas)
        except BackupError as e:
            if 'version mismatch' in str(e):
                # pg_dump is older than the server (local 16 vs. Supabase 17) — fall back to CSV tables
                for attempt in range(RETRIES):
                    try:
                        export_csv(url, os.path.join(folder, '%s_csv' % name))
                        e = None
                        break
                    except Exception as csv_error:
                        # over VPN the Supabase pooler sometimes doesn't respond — retry
                        e = BackupError('CSV-выгрузка: %s' % _redact(str(csv_error), url))
                        time.sleep(RETRY_PAUSE)
                if e is None:
                    continue
            errors.append('%s: %s' % (name, e))
    prune(root)
    return folder, errors


def main():
    from leadgen.core import db, mirror
    root = os.environ.get('LEADGEN_BACKUP_DIR') or os.path.join(ROOT, 'db', 'backups')
    os.makedirs(root, exist_ok=True)
    folder, errors = run(root, os.environ.get('PG_DUMP', 'pg_dump'))
    print('бэкап: %s%s' % (folder, '' if not errors else ', ошибки: %s' % '; '.join(errors)))
    stores = db.open_stores()
    try:
        from leadgen.core import alerts
        for e in errors:
            alerts.raise_alert(stores.archive, 'backup', 'Ночной бэкап не удался — %s' % e[:300])
        print('зеркало contacts.csv: дописано %d' % mirror.sync(stores))
    finally:
        stores.close()


if __name__ == '__main__':
    sys.exit(main())

"""System settings — the settings table in the main database.

Changed via bot commands (plan 3) or by hand in Supabase Studio; workers
re-read them on the fly, so no restart is needed."""


def get(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key = %s', (key,)).fetchone()
    return row['value'] if row else default


def put(conn, key, value):
    conn.execute(
        'INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, now()) '
        'ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()',
        (key, str(value)))

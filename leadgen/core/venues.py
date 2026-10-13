"""A venue's account and pk in the registry (main database).

pk never goes stale: once obtained, it removes the need to search for it
ever again — this is what keeps the pipeline resilient to search being
blocked."""


def stored_pk(conn, ig_account):
    row = conn.execute(
        "SELECT pk FROM venues WHERE lower(ig_account) = lower(%s) AND pk IS NOT NULL AND pk <> '' "
        'LIMIT 1', (ig_account,)).fetchone()
    return row['pk'] if row else None


def store_pk(conn, ig_account, pk):
    conn.execute(
        "UPDATE venues SET pk = %s WHERE lower(ig_account) = lower(%s) AND (pk IS NULL OR pk = '')",
        (str(pk), ig_account))


def set_account(conn, name, city, ig_account, pk):
    conn.execute('UPDATE venues SET ig_account = %s, pk = %s WHERE name = %s AND city = %s',
                 (ig_account, str(pk), name, city))

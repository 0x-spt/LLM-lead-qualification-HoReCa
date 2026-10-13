"""Health of Instagram endpoints, keyed by "account × endpoint".

Instagram's limits apply to each endpoint independently (measured 2026-09-17:
search returns 429 while subscriptions return 200), so cooldown is set per
pair, not per account. The exceptions are checkpoint and a dead proxy: there
the whole account is disabled until a human intervenes."""
from leadgen.core import alerts

COOLDOWN_SECONDS = {
    'throttled': 900,     # 429 — a regular rate limit, clears quickly
    'soft_block': 3600,   # a silent refusal (empty 200 / HTML instead of JSON)
    'net_error': 60,      # a network hiccup over the VPN path — not a reason to hold the endpoint for an hour
}

ACCOUNT_DOWN = {'checkpoint': 'нужен_вход', 'proxy_dead': 'proxy_dead'}
# an anonymous reader (no cookie) can only reach pages viewable while logged out
ANON_ENDPOINTS = {'profile_html', 'embed', 'cdn'}
# Warmup (2026-10-05): for the first day after being added, an account only reads
# profiles — ig13 got checkpointed 4 minutes after login while crawling subscriptions.
WARMUP_ENDPOINTS = ANON_ENDPOINTS | {'profile_info'}
# 2026-10-08: ig3, ig8, ig4 got checkpointed at 1,100-2,200 requests/hour under their
# own session — no real person browses that fast. Cap applies to own accounts;
# logged-out readers have no cap.
OWN_HOURLY_CAP = 300

ALERT_TEXT = {
    ('checkpoint', 'own'): 'Аккаунт %s: Instagram требует вход (checkpoint). Войдите руками через его прокси и добавьте сессию заново.',
    ('checkpoint', 'hiker'): 'HikerAPI (%s): ключ отклонён или кончился баланс. Пополните баланс или задайте новый ключ.',
    ('proxy_dead', 'own'): 'Аккаунт %s: прокси не отвечает. Замените прокси.',
    ('proxy_dead', 'hiker'): 'HikerAPI (%s): сервис недоступен.',
}


def _log(conn, account_id, endpoint_class, status):
    conn.execute('INSERT INTO request_log (account_id, endpoint_class, status) VALUES (%s, %s, %s)',
                 (account_id, endpoint_class, status))


def record_success(conn, account_id, endpoint_class):
    conn.execute('''
        INSERT INTO endpoint_health (account_id, endpoint_class, status, requests_count, last_success_at)
        VALUES (%s, %s, 'ok', 1, now())
        ON CONFLICT (account_id, endpoint_class) DO UPDATE SET
            status = 'ok',
            requests_count = endpoint_health.requests_count + 1,
            last_success_at = excluded.last_success_at,
            cooldown_until = NULL
    ''', (account_id, endpoint_class))
    _log(conn, account_id, endpoint_class, 'ok')


def record_failure(conn, account_id, endpoint_class, reason):
    kind = (conn.execute('SELECT kind FROM accounts WHERE id = %s', (account_id,)).fetchone() or {}).get('kind')
    if kind == 'anon' and reason == 'checkpoint':
        # a cookie-less reader getting redirected to login is an IP-level limit, not an account checkpoint
        reason = 'throttled'
    cooldown = COOLDOWN_SECONDS.get(reason, 900)
    conn.execute('''
        INSERT INTO endpoint_health
            (account_id, endpoint_class, status, requests_count, last_failure_at, cooldown_until)
        VALUES (%s, %s, %s, 1, now(), now() + %s * interval '1 second')
        ON CONFLICT (account_id, endpoint_class) DO UPDATE SET
            status = excluded.status,
            requests_count = endpoint_health.requests_count + 1,
            last_failure_at = excluded.last_failure_at,
            cooldown_until = excluded.cooldown_until
    ''', (account_id, endpoint_class, reason, cooldown))
    _log(conn, account_id, endpoint_class, reason)
    if reason in ACCOUNT_DOWN:
        row = conn.execute(
            "UPDATE accounts SET status = %s, down_at = now(), down_alerted = false "
            "WHERE id = %s AND status = 'active' RETURNING label, kind",
            (ACCOUNT_DOWN[reason], account_id)).fetchone()
        # a dead exit on an own account is usually just the VPN tunnel reconnecting for a
        # few seconds: the supervisor brings the account back on its own (accounts.revive_dead);
        # alert only if it stays down for a while
        if row and not (reason == 'proxy_dead' and row['kind'] in ('own', 'anon')):
            alerts.raise_alert(conn, reason, ALERT_TEXT[(reason, row['kind'])] % row['label'])


def pick_account(conn, endpoint_class, account_ids):
    """The least-loaded account for which this endpoint isn't cooling down."""
    if not account_ids:
        return None
    # an account that hit a checkpoint or has a dead proxy is closed on every endpoint at
    # once — we don't wait for the supervisor to rebuild the pool
    active = {r['id'] for r in conn.execute(
        "SELECT id FROM accounts WHERE id = ANY(%s) AND status = 'active' "
        "AND (kind <> 'anon' OR %s) "
        "AND (%s OR warmup_until IS NULL OR warmup_until < now())",
        (list(account_ids), endpoint_class in ANON_ENDPOINTS, endpoint_class in WARMUP_ENDPOINTS)).fetchall()}
    account_ids = [aid for aid in account_ids if aid in active]
    if not account_ids:
        return None
    busy = {r['account_id'] for r in conn.execute(
        "SELECT l.account_id FROM request_log l JOIN accounts a ON a.id = l.account_id "
        "WHERE l.account_id = ANY(%s) AND a.kind = 'own' AND l.at > now() - interval '1 hour' "
        'GROUP BY l.account_id HAVING count(*) >= %s', (account_ids, OWN_HOURLY_CAP)).fetchall()}
    account_ids = [aid for aid in account_ids if aid not in busy]
    if not account_ids:
        return None
    rows = conn.execute(
        'SELECT account_id, requests_count, cooldown_until > now() AS cooling '
        'FROM endpoint_health WHERE endpoint_class = %s AND account_id = ANY(%s)',
        (endpoint_class, list(account_ids))).fetchall()
    by_id = {r['account_id']: r for r in rows}
    candidates = []
    for aid in account_ids:
        row = by_id.get(aid)
        if row and row['cooling']:
            continue
        candidates.append((row['requests_count'] if row else 0, aid))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def seconds_since_last_success(conn):
    row = conn.execute(
        "SELECT EXTRACT(EPOCH FROM now() - max(at)) AS s FROM request_log WHERE status = 'ok'"
    ).fetchone()
    return float(row['s']) if row['s'] is not None else None

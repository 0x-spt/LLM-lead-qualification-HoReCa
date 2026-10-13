"""Alerts for the Telegram bot (plan 3 sends them).

The same alert is never duplicated: while a prior one is still unsent, a new
identical alert is not written — otherwise an account stuck at a checkpoint
would flood the chat with messages on every task."""


def raise_alert(conn, kind, text):
    exists = conn.execute(
        'SELECT 1 FROM alerts WHERE kind = %s AND text = %s AND sent_at IS NULL',
        (kind, text)).fetchone()
    if exists:
        return False
    conn.execute('INSERT INTO alerts (kind, text) VALUES (%s, %s)', (kind, text))
    return True

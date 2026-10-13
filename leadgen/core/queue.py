"""Task queue in the archive database.

Claiming is a single UPDATE with FOR UPDATE SKIP LOCKED: no matter how many
workers run, exactly one will take any given task. A claimed task gets a
lease: if the worker dies, the task returns to the queue once the lease
expires."""
from psycopg.types.json import Jsonb


def enqueue(conn, task_type, payload, priority=100):
    conn.execute(
        'INSERT INTO work_queue (task_type, payload, priority) VALUES (%s, %s, %s)',
        (task_type, Jsonb(payload), priority))


def claim_next(conn, task_types, agent_label, pk_only_types=None, lease_minutes=10):
    """The highest-priority task.

    task_types — types that can be taken regardless of payload.
    pk_only_types — types that right now can ONLY be taken with a pk in the
    payload: search is cooling down for them, but the profile endpoint is
    still alive. Filtering happens in SQL so an unfit task is never claimed
    and never blocks the queue behind it."""
    clauses, params = [], []
    if task_types:
        clauses.append('task_type = ANY(%s)')
        params.append(list(task_types))
    if pk_only_types:
        clauses.append("(task_type = ANY(%s) AND (payload->>'pk') IS NOT NULL)")
        params.append(list(pk_only_types))
    if not clauses:
        return None
    row = conn.execute(
        "UPDATE work_queue SET status = 'in_progress', assigned_agent = %s, "
        "lease_until = now() + %s * interval '1 minute', updated_at = now() "
        'WHERE id = (SELECT id FROM work_queue '
        "            WHERE status = 'pending' AND (" + ' OR '.join(clauses) + ') '
        '            ORDER BY priority, id FOR UPDATE SKIP LOCKED LIMIT 1) '
        'RETURNING id, task_type, payload, attempts',
        [agent_label, lease_minutes] + params).fetchone()
    if not row:
        return None
    return {'id': row['id'], 'task_type': row['task_type'],
            'payload': row['payload'], 'attempts': row['attempts'] or 0}


def complete(conn, task_id):
    # only its own, still-claimed task: a straggling worker whose lease already
    # expired won't close a task that has been put back in the queue
    conn.execute("UPDATE work_queue SET status = 'done', lease_until = NULL, updated_at = now() "
                 "WHERE id = %s AND status = 'in_progress'", (task_id,))


def fail(conn, task_id, requeue=True):
    """An attempt was spent: either a network request happened or the handler
    crashed. attempts is the budget for MAX_EMPTY_ATTEMPTS and
    MAX_TASK_ATTEMPTS, and only grows on a real attempt."""
    if requeue:
        conn.execute(
            "UPDATE work_queue SET status = 'pending', assigned_agent = NULL, lease_until = NULL, "
            "attempts = attempts + 1, updated_at = now() WHERE id = %s AND status = 'in_progress'",
            (task_id,))
    else:
        conn.execute("UPDATE work_queue SET status = 'failed', lease_until = NULL, "
                     "updated_at = now() WHERE id = %s AND status = 'in_progress'", (task_id,))


def release(conn, task_id):
    """The task was never tried (no live account was available) — put it back, leave attempts alone."""
    conn.execute(
        "UPDATE work_queue SET status = 'pending', assigned_agent = NULL, lease_until = NULL, "
        "updated_at = now() WHERE id = %s AND status = 'in_progress'", (task_id,))


def reclaim_expired(conn):
    """Tasks of crashed workers — back to the queue. attempts is left alone."""
    cur = conn.execute(
        "UPDATE work_queue SET status = 'pending', assigned_agent = NULL, lease_until = NULL, "
        "updated_at = now() WHERE status = 'in_progress' AND lease_until < now()")
    return cur.rowcount

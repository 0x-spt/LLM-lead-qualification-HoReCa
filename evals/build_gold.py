"""Gold set from the client's decisions in the bot: each nick's latest manual verdict,
plus its bio, plus the LLM grader's verdicts issued BEFORE the human decision (what
the grader knew at the moment of vetting).

The gold set is personal data (real profiles): written only to evals/data/private/
(in .gitignore). Only metrics and an error summary with no bio text are public
(evals/out/report_public.md).

    .venv/bin/python -m evals.build_gold
"""
import json
import os

from leadgen.core import db

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, 'data', 'private', 'gold.jsonl')


def _profile(stores, nick):
    for conn, sql in ((stores.main, 'SELECT bio, name AS full_name, NULL AS category FROM contacts WHERE nick = %s'),
                      (stores.archive, 'SELECT bio, full_name, NULL AS category FROM rejected WHERE nick = %s'),
                      (stores.archive, 'SELECT bio, full_name, category FROM profile_cache WHERE nick = %s LIMIT 1')):
        row = conn.execute(sql, (nick,)).fetchone()
        if row and (row['bio'] or '').strip():
            cat = row['category']
            if cat is None:
                c = stores.archive.execute('SELECT category FROM profile_cache WHERE nick = %s LIMIT 1', (nick,)).fetchone()
                cat = (c or {}).get('category')
            return {'bio': row['bio'], 'full_name': row['full_name'] or '', 'category': cat or ''}
    return None


def _machine(stores, nick, stage, before):
    row = stores.main.execute(
        'SELECT verdict FROM verdicts WHERE nick = %s AND stage = %s AND NOT by_human AND created_at < %s '
        'ORDER BY created_at DESC LIMIT 1', (nick, stage, before)).fetchone()
    return row['verdict'] if row else None


def collect(stores):
    human = stores.main.execute(
        "SELECT DISTINCT ON (nick) nick, verdict, reason, created_at FROM verdicts WHERE by_human "
        "ORDER BY nick, created_at DESC").fetchall()
    out = []
    for h in human:
        prof = _profile(stores, h['nick'])
        if not prof:
            continue
        reason = h['reason'] or ''
        out.append(dict(prof, nick=h['nick'], label=h['verdict'], reason=reason,
                        # the system rejected it, the client reversed it — the most valuable cases to evaluate
                        overturned=reason.startswith('отказ отменён'),
                        grader_text=_machine(stores, h['nick'], 'текст', h['created_at']),
                        grader_grid=_machine(stores, h['nick'], 'сетка', h['created_at'])))
    return out


def write(rows):
    """Только личный файл. Публичной выборки шапок нет: обезличивание по @-никам оставляет имена людей,
    названия заведений и улицы, написанные текстом (проверено 12.10.2026) — в портфолио идут только цифры."""
    os.makedirs(os.path.dirname(PRIVATE), exist_ok=True)
    with open(PRIVATE, 'w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    rows = collect(db.open_stores())
    write(rows)
    print('эталон: %d профилей (берём %d, нет %d, отменённых отказов %d) → %s'
          % (len(rows), sum(r['label'] == 'accept' for r in rows), sum(r['label'] == 'reject' for r in rows),
             sum(r['overturned'] for r in rows), PRIVATE))

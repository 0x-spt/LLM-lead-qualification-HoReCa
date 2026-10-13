"""Public eval on synthetic profiles: free rules vs. LLM text stage vs. the cascade.

Runs anywhere — no production data, no Instagram. A throw-away Postgres (pgserver) holds the
role catalogue and a small city table, exactly as the rules see them in production.

    python -m evals.offline              # rules only; uses cached LLM verdicts if present
    python -m evals.offline --llm        # also calls the LLM text stage (Claude CLI) and caches it

Outputs evals/results/synthetic_report.md and evals/results/synthetic_metrics.json.
"""
import argparse
import json
import os
import tempfile
from collections import defaultdict

from evals import metrics, predict, synthetic
from leadgen.core import db
from leadgen.llm import grader
from leadgen.qualify import geo, roles

HERE = os.path.dirname(os.path.abspath(__file__))
LLM_CACHE = os.path.join(HERE, 'data', 'synthetic_llm.jsonl')
OUT = os.path.join(HERE, 'results')
BATCH = 25


def _database():
    """A scratch main DB: LEADGEN_EVAL_PG if set, otherwise a temporary pgserver."""
    url = os.environ.get('LEADGEN_EVAL_PG')
    server = None
    if not url:
        import pgserver
        server = pgserver.get_server(tempfile.mkdtemp(prefix='leadgen-eval-'), cleanup_mode='stop')
        url = server.get_uri()
    conn = db.connect(url)
    db.init_main(conn)
    for city in synthetic.CITIES:
        geo.upsert_city(conn, city, 'synthetic', 'synthetic')
    return conn, server


def run_rules(conn, cases):
    enabled = roles.load_enabled(conn)
    out = {}
    for c in cases:
        decision, why = predict.rules(conn, enabled, c)
        out[c['nick']] = {'decision': decision, 'why': why}
    return out


def run_llm(cases):
    """Text stage over bio + captions in batches of 25 — the same prompt, schema and model as production.
    Verdicts are appended to the cache after every batch, so an interrupted run loses nothing."""
    os.makedirs(os.path.dirname(LLM_CACHE), exist_ok=True)
    out = {}
    with open(LLM_CACHE, 'w', encoding='utf-8') as f:
        for i in range(0, len(cases), BATCH):
            rows = [dict(c, role=c['role'] or '') for c in cases[i:i + BATCH]]
            result = grader.run_claude(grader._text_prompt(rows), grader.TEXT_SCHEMA, grader.TEXT_MODEL,
                                       grader.TEXT_SYSTEM)
            for item in result.get('results') or []:
                nick = item['nick'].lstrip('@').lower()
                out[nick] = {'verdict': item['verdict'], 'reason': item.get('reason')}
                f.write(json.dumps(dict(out[nick], nick=nick), ensure_ascii=False) + '\n')
            f.flush()
            print('llm: %d/%d' % (min(i + BATCH, len(cases)), len(cases)))
    return out


def load_llm_cache():
    if not os.path.exists(LLM_CACHE):
        return None
    with open(LLM_CACHE, encoding='utf-8') as f:
        return {r['nick']: r for r in map(json.loads, f)}


def score(cases, rules, llm):
    rows = []
    for c in cases:
        r = rules[c['nick']]['decision']
        # "unsure" goes on to the grid stage / a human, so only an explicit reject drops the profile
        t = None if llm is None or c['nick'] not in llm else ('reject' if llm[c['nick']]['verdict'] == 'reject'
                                                               else 'accept')
        cascade = 'reject' if r == 'reject' or t == 'reject' else 'accept'
        rows.append(dict(c, rules=r, rules_why=rules[c['nick']]['why'], llm=t, cascade=cascade))
    stages = {'rules': 'rules', 'llm_text': 'llm', 'cascade': 'cascade'}
    overall = {}
    for name, key in stages.items():
        sub = [x for x in rows if x[key] is not None]
        if sub:
            overall[name] = metrics.binary([x['label'] for x in sub], [x[key] for x in sub], positive='accept')
    by_slice = defaultdict(lambda: {'n': 0, 'label': None, 'rules': 0, 'llm': 0, 'cascade': 0, 'llm_n': 0})
    for x in rows:
        s = by_slice[x['slice']]
        s['n'] += 1
        s['label'] = x['label']
        s['rules'] += x['rules'] == x['label']
        s['cascade'] += x['cascade'] == x['label']
        if x['llm'] is not None:
            s['llm_n'] += 1
            s['llm'] += x['llm'] == x['label']
    return rows, overall, dict(by_slice)


def _pct(x):
    return '—' if x is None else '%.0f%%' % (100 * x)


def _ci(ci):
    return '' if not ci else ' (%.0f–%.0f)' % (100 * ci[0], 100 * ci[1])


def report(cases, overall, by_slice):
    pos = sum(c['label'] == 'accept' for c in cases)
    lines = ['# Synthetic eval — rules vs. LLM text stage vs. cascade', '',
             '%d generated profiles in %d slices (%d accept, %d reject). All names, venues and handles are '
             'invented (`evals/synthetic.py`, seed %d).' % (len(cases), len(by_slice), pos, len(cases) - pos,
                                                            synthetic.SEED), '',
             '## Overall (positive class = accept, 95% Wilson CI)', '',
             '| Stage | n | Precision | Recall | F1 | False accepts | Lost leads |', '|---|---|---|---|---|---|---|']
    for name, m in overall.items():
        lines.append('| %s | %d | %s%s | %s%s | %s | %d | %d |' % (
            name, m['n'], _pct(m['precision']), _ci(m['precision_ci']), _pct(m['recall']), _ci(m['recall_ci']),
            _pct(m['f1']), m['fp'], m['fn']))
    lines += ['', '## Accuracy by slice', '', '| Slice | Label | n | Rules | LLM text | Cascade |',
              '|---|---|---|---|---|---|']
    for name, s in by_slice.items():
        llm = _pct(s['llm'] / s['llm_n']) if s['llm_n'] else '—'
        lines.append('| %s | %s | %d | %s | %s | %s |' % (name, s['label'], s['n'], _pct(s['rules'] / s['n']),
                                                         llm, _pct(s['cascade'] / s['n'])))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--llm', action='store_true', help='call the LLM text stage (needs the claude CLI)')
    args = ap.parse_args(argv)
    cases = synthetic.generate()
    conn, server = _database()
    try:
        rules = run_rules(conn, cases)
    finally:
        conn.close()
        if server is not None:
            server.cleanup()
    llm = run_llm(cases) if args.llm else load_llm_cache()
    rows, overall, by_slice = score(cases, rules, llm)
    os.makedirs(OUT, exist_ok=True)
    md = report(cases, overall, by_slice)
    with open(os.path.join(OUT, 'synthetic_report.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    with open(os.path.join(OUT, 'synthetic_metrics.json'), 'w', encoding='utf-8') as f:
        json.dump({'overall': overall, 'by_slice': by_slice,
                   'errors': [{k: r[k] for k in ('nick', 'slice', 'label', 'rules', 'rules_why', 'llm', 'cascade')}
                              for r in rows if r['cascade'] != r['label']]}, f, ensure_ascii=False, indent=1)
    print(md)


if __name__ == '__main__':
    main()

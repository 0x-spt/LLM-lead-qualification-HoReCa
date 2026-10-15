"""Running the eval set: vetting decision quality against the client's decisions, plus economics and stability.

    .venv/bin/python -m evals.build_gold    # gold set from decisions made in the bot (run once / after new decisions)
    .venv/bin/python -m evals.run           # metrics → evals/out/metrics.json, report.md, report_public.md
"""
import json
import os
from collections import Counter
from datetime import datetime

from leadgen.core import db
from leadgen.qualify import roles
from evals import build_gold, metrics, predict

OUT = os.path.join(build_gold.HERE, 'out')
CAP_AT = '2026-10-08 14:46'   # from this moment on, an own account gets no more than 300 requests/hour

# Client's review of the export (2026-10-06 to 2026-10-09): every one of these cases the system must catch.
REGRESSION = [
    ('north_oak_', 'вне РФ', 'Ташкент: @sorrel1.tashkent, «moscow» внутри @moscow.cafe1.uz'),
    ('pepper.chef', 'вне РФ', 'Казахстан: @willow1.kz'),
    ('chef_basil', 'вне РФ', 'Актобе: @harbor1_aktobe'),
    ('ginger1_hazel1_indigo1', 'вне РФ', 'Таллин'),
    ('mint1', 'заведение', 'ресторан «Улов» называет бренд-шефа'),
    ('olive1_pepper1', 'вне РФ', 'Украина: шапка на украинском'),
    ('chef_umber__vale', 'вне РФ', 'Украина: @juniper_cafe_ua'),
    ('quartz1.river1', 'вне РФ', 'Украина: Київ, Одеса'),
    ('umber2_chef', 'вне РФ', 'Украина: 🇺🇦'),
]


def _load():
    with open(build_gold.PRIVATE) as f:
        return [json.loads(line) for line in f]


def decisions(stores, gold):
    enabled = roles.load_enabled(stores.main)
    rows = []
    for g in gold:
        rule, why = predict.rules(stores.main, enabled, g)
        text = None if g['grader_text'] is None else ('reject' if g['grader_text'] == 'reject' else 'accept')
        grid = None if g['grader_grid'] in (None, 'unsure') else g['grader_grid']
        system = 'reject' if 'reject' in (rule, text, grid) else 'accept'
        who = ('правила: ' + why if rule == 'reject' else 'оценщик по тексту' if text == 'reject'
               else 'оценщик по сетке' if grid == 'reject' else 'принят (%s)' % why)
        rows.append(dict(g, rules=rule, rules_why=why, text=text, grid=grid, system=system, system_why=who))
    return rows


def quality(rows):
    out = {}
    for name, key in (('правила', 'rules'), ('оценщик_текст', 'text'), ('оценщик_сетка', 'grid'),
                      ('система_целиком', 'system')):
        sub = [r for r in rows if r[key] is not None]
        out[name] = dict(metrics.binary([r['label'] for r in sub], [r[key] for r in sub], positive='accept'),
                         coverage=len(sub) / len(rows) if rows else None)
    over = [r for r in rows if r['overturned']]
    out['отменённые_отказы'] = {'n': len(over), 'правила_теперь_берут': sum(r['rules'] == 'accept' for r in over)}
    return out


def regression(stores):
    enabled = roles.load_enabled(stores.main)
    res = []
    for nick, want, note in REGRESSION:
        prof = build_gold._profile(stores, nick)
        if not prof:
            res.append({'case': note, 'ok': None})
            continue
        d, why = predict.rules(stores.main, enabled, dict(prof, nick=nick))
        hit = d == 'reject'  # what matters is that it's not accepted; which rule caught it goes in the report
        res.append({'case': note, 'ok': hit, 'why': why})
    return res


def economics(stores, days=14):
    a, m = stores.archive, stores.main
    reads = {r['src']: r['n'] for r in a.execute(
        "SELECT coalesce(payload->>'source', 'following') AS src, count(*) AS n FROM work_queue "
        "WHERE task_type = 'profile_info' AND status = 'done' AND updated_at > now() - make_interval(days => %s) "
        "GROUP BY 1", (days,)).fetchall()}
    good = {r['src']: r['n'] for r in m.execute(
        "SELECT split_part(coalesce(found_via, ''), ' ', 1) AS src, count(*) AS n FROM contacts "
        "WHERE created_at > now() - make_interval(days => %s) AND status IN ('к_выгрузке', 'выгружен', 'сетка_готова', 'текст_ок') "
        "GROUP BY 1", (days,)).fetchall()}
    return {src: {'профилей': reads[src], 'контактов': good.get(src, 0),
                  'профилей_на_контакт': round(reads[src] / good[src]) if good.get(src) else None}
            for src in sorted(reads, key=reads.get, reverse=True) if reads[src] >= 100}


def stability(stores):
    a = stores.archive
    out = {}
    for name, cond in (('до_лимита', 'at < %s'), ('после_лимита', 'at >= %s')):
        req = a.execute("SELECT count(*) AS c FROM request_log l JOIN accounts ac ON ac.id = l.account_id "
                        "WHERE ac.kind = 'own' AND l." + cond + " AND l.at > %s::timestamptz - interval '7 days'",
                        (CAP_AT, CAP_AT)).fetchone()['c']
        cp = a.execute("SELECT count(*) AS c FROM alerts WHERE kind = 'checkpoint' AND created_at " + cond.split(' ', 1)[1] +
                       " AND created_at > %s::timestamptz - interval '7 days'", (CAP_AT, CAP_AT)).fetchone()['c']
        out[name] = {'запросов_своих_аккаунтов': req, 'проверок_instagram': cp,
                     'проверок_на_1000_запросов': round(cp * 1000 / req, 3) if req else None}
    before = []
    for r in a.execute("SELECT text, created_at FROM alerts WHERE kind = 'checkpoint' AND created_at > %s::timestamptz - interval '7 days'",
                       (CAP_AT,)).fetchall():
        acc = a.execute('SELECT id FROM accounts WHERE label = %s', (r['text'].split(':')[0].replace('Аккаунт ', ''),)).fetchone()
        if acc:
            before.append(a.execute("SELECT count(*) AS c FROM request_log WHERE account_id = %s "
                                    "AND at BETWEEN %s - interval '1 hour' AND %s",
                                    (acc['id'], r['created_at'], r['created_at'])).fetchone()['c'])
    before.sort()
    out['запросов_за_час_до_проверки'] = {'проверок': len(before), 'медиана': before[len(before) // 2] if before else None,
                                         'больше_200': sum(1 for x in before if x > 200)}
    return out


def _pct(x):
    return '—' if x is None else '%.0f%%' % (100 * x)


def _ci(ci):
    return '' if not ci else ' (95%%: %s–%s)' % (_pct(ci[0]), _pct(ci[1]))


def report(q, reg, eco, stab, rows, public):
    lines = ['# LLM lead qualifier — eval', '',
             'Эталон: %d профилей с решением заказчика в боте (берём %d, нет %d). Набор — не случайная выборка: '
             'в бот попадают спорные, отказы на проверку и автопринятые; он смещён к трудным случаям.'
             % (len(rows), sum(r['label'] == 'accept' for r in rows), sum(r['label'] == 'reject' for r in rows)), '',
             '## Решение «берём / нет»', '',
             '| Компонент | Покрытие | Точность (берём) | Полнота (берём) | F1 | Ложно взятых | Потерянных |',
             '|---|---|---|---|---|---|---|']
    for name, m in q.items():
        if 'precision' not in m:
            continue
        lines.append('| %s | %s | %s%s | %s%s | %s | %d | %d |' % (
            name.replace('_', ' '), _pct(m['coverage']), _pct(m['precision']), _ci(m['precision_ci']),
            _pct(m['recall']), _ci(m['recall_ci']), _pct(m['f1']), m['fp'], m['fn']))
    o = q['отменённые_отказы']
    lines += ['', 'Отказы системы, отменённые заказчиком: %d; нынешние правила берут %d из них.'
              % (o['n'], o['правила_теперь_берут']), '',
              '## Регрессия: случаи из разбора выгрузки заказчиком', '']
    lines += ['- %s %s%s' % ('✅' if r['ok'] else ('⚪ нет данных' if r['ok'] is None else '❌'), r['case'],
                             ' — отсечён: %s' % r['why'] if r.get('why') else '') for r in reg]
    lines += ['', '## Экономика: профилей Instagram на один принятый контакт (14 дней)', '',
              '| Канал | Прочитано профилей | Контактов | Профилей на контакт |', '|---|---|---|---|']
    lines += ['| %s | %d | %d | %s |' % (k, v['профилей'], v['контактов'], v['профилей_на_контакт'] or '—')
              for k, v in eco.items()]
    lines += ['', '## Устойчивость аккаунтов: лимит 300 запросов в час с %s' % CAP_AT, '',
              '| Период (7 дней) | Запросов своих аккаунтов | Проверок Instagram | На 1 000 запросов |', '|---|---|---|---|']
    lines += ['| %s | %d | %d | %s |' % (k.replace('_', ' '), v['запросов_своих_аккаунтов'], v['проверок_instagram'],
                                         v['проверок_на_1000_запросов']) for k, v in stab.items() if 'проверок_instagram' in v]
    b = stab['запросов_за_час_до_проверки']
    lines += ['', 'Запросов за час до проверки: медиана %s, больше 200 — у %d из %d. Темп последнего часа обычно '
              'не причина: сессия помечается раньше (прошлая нагрузка, смена IP, повторный вход), а проверка всплывает '
              'на первом запросе. Лимит 300/час не вредит, но его польза этими данными не доказана.'
              % (b['медиана'], b['больше_200'], b['проверок'])]
    errs = [r for r in rows if r['system'] != r['label']]
    lines += ['', '## Где система ошибается', '', '| Заказчик | Система | Кто решил | Случаев |', '|---|---|---|---|']
    groups = Counter((r['label'], r['system'], r['system_why'].split(':')[0].split(' (')[0]) for r in errs)
    lines += ['| %s | %s | %s | %d |' % (g[0], g[1], g[2], n) for g, n in groups.most_common(12)]
    if not public:  # bio text is personal data — local report only
        lines += ['', '## Примеры ошибок (локально, не публиковать)', '']
        for r in errs[:30]:
            lines.append('- заказчик: **%s**, система: %s (%s) — @%s — %s'
                         % (r['label'], r['system'], r['system_why'], r['nick'], r['bio'][:160].replace('\n', ' ')))
    return '\n'.join(lines) + '\n'


def main():
    stores = db.open_stores()
    rows = decisions(stores, _load())
    q, reg, eco, stab = quality(rows), regression(stores), economics(stores), stability(stores)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'metrics.json'), 'w') as f:
        json.dump({'at': datetime.now().isoformat(timespec='minutes'), 'качество': q, 'регрессия': reg,
                   'экономика': eco, 'устойчивость': stab}, f, ensure_ascii=False, indent=1, default=str)
    for name, public in (('report.md', False), ('report_public.md', True)):
        with open(os.path.join(OUT, name), 'w') as f:
            f.write(report(q, reg, eco, stab, rows, public))
    print(report(q, reg, eco, stab, rows, public=True))


if __name__ == '__main__':
    main()

"""Export of ready contacts into "AI ЛИДЫ AMOCRM" — governed entirely by the client's settings.

Quotas `quota:<DEPARTMENT>` and the `export_time` are set by hand (bot commands
or in Supabase Studio); without them there's no export. Before writing, column A
of the live sheet is checked: that way an export that crashed halfway never duplicates rows."""
import os
from datetime import date

from leadgen.core import csvfile, settings
from leadgen.ops import sheet

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def csv_path_default():
    # read on every call: tests override the path and leave the production CSV alone
    return os.environ.get('LEADGEN_CONTACTS_CSV') or os.path.join(ROOT, 'db', 'contacts.csv')
DEPARTMENTS = ('МСК', 'НСК', 'СПБ')
SHEET_NAME = 'AI ЛИДЫ AMOCRM'


def quotas(main):
    out = {}
    for dep in DEPARTMENTS:
        raw = settings.get(main, 'quota:%s' % dep)
        if raw and raw.strip().isdigit() and int(raw) > 0:
            out[dep] = int(raw)
    return out


def select(stores):
    out = {}
    for dep, limit in quotas(stores.main).items():
        rows = stores.main.execute(
            "SELECT * FROM contacts WHERE status = 'к_выгрузке' AND department = %s "
            "ORDER BY (level = 'PREMIUM') DESC, created_at, nick LIMIT %s", (dep, limit)).fetchall()
        if rows:
            out[dep] = rows
    return out


def preview(stores):
    q = quotas(stores.main)
    if not q:
        return 'Квоты не заданы — выгрузки не будет. Пример: /quota МСК 30'
    lines = []
    for dep, limit in q.items():
        waiting = stores.main.execute(
            "SELECT count(*) AS c FROM contacts WHERE status = 'к_выгрузке' AND department = %s",
            (dep,)).fetchone()['c']
        rows = select(stores).get(dep, [])
        lines.append('%s: %d из %d (квота %d)' % (dep, len(rows), waiting, limit))
        lines += ['  @%s — %s, %s' % (r['nick'], r['venue'] or '—', r['level'] or '—') for r in rows]
    return '\n'.join(lines)


def _mirror_csv(rows, day, csv_path):
    if not os.path.exists(csv_path):
        return
    header, known = csvfile.read(csv_path)
    out = []
    for r in rows:
        if r['nick'] in known:
            continue
        out.append({'ник': r['nick'], 'тип': 'контакт', 'имя': r.get('name') or '',
                    'роль': r.get('role') or '', 'заведение': r.get('venue') or '',
                    'город': r.get('city') or '', 'уровень': r.get('level') or '',
                    'статус': 'сдан', 'причина': '',
                    'последняя_активность': r.get('last_activity') or '',
                    'дата_проверки': r.get('checked_date') or '',
                    'найден_через': r.get('found_via') or '', 'агент': r.get('agent') or '',
                    'куда_сдан': '%s %s' % (SHEET_NAME, day.strftime('%d.%m.%Y'))})
    csvfile.append(csv_path, header, out)


def run(stores, sheet_client, trigger, day=None, csv_path=None):
    day = day or date.today()
    chosen = select(stores)
    out = {'added': 0, 'already': 0, 'by_department': {}}
    if not chosen:
        return out
    existing = sheet_client.existing_nicks()
    fresh, already = [], []
    for dep, rows in chosen.items():
        for r in rows:
            (already if r['nick'] in existing else fresh).append(r)
            if r['nick'] not in existing:
                out['by_department'][dep] = out['by_department'].get(dep, 0) + 1
    if fresh:
        sheet_client.append([sheet.row_for(r, day) for r in fresh], day)  # on error, statuses are left untouched
    delivered = '%s %s' % (SHEET_NAME, day.strftime('%d.%m.%Y'))
    for r in fresh:
        stores.main.execute("UPDATE contacts SET status = 'выгружен', delivered_to = %s, "
                            'updated_at = now() WHERE nick = %s', (delivered, r['nick']))
    for r in already:
        stores.main.execute("UPDATE contacts SET status = 'выгружен', delivered_to = 'уже в таблице', "
                            'updated_at = now() WHERE nick = %s', (r['nick'],))
    from psycopg.types.json import Jsonb
    stores.main.execute('INSERT INTO exports (trigger, counts, nicks) VALUES (%s, %s, %s)',
                        (trigger, Jsonb(out['by_department']), [r['nick'] for r in fresh]))
    # "already in the sheet" cases are recovery after a crash: the manual-work canon must know about them too
    _mirror_csv(fresh + already, day, csv_path or csv_path_default())
    out['added'], out['already'] = len(fresh), len(already)
    return out


def due(stores, now_local):
    raw = (settings.get(stores.main, 'export_time') or '').strip()
    if not raw or raw == 'off' or ':' not in raw:
        return False
    hh, mm = raw.split(':', 1)
    if (now_local.hour, now_local.minute) < (int(hh), int(mm)):
        return False
    if settings.get(stores.main, 'export_attempted_on') == now_local.date().isoformat():
        return False  # an attempt already happened today — even if there was nothing to export, or it failed
    tz = settings.get(stores.main, 'tz', 'Europe/Moscow')
    done = stores.main.execute(
        "SELECT 1 FROM exports WHERE trigger = 'расписание' AND (at AT TIME ZONE %s)::date = %s LIMIT 1",
        (tz, now_local.date())).fetchone()
    return done is None

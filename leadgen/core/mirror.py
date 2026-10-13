"""Mirrors the databases into db/contacts.csv — the canonical dedup source
for manual work and the skill.

Only appends rows for nicks not already in the file; existing rows, the
header and the order are left alone — the file is also edited by hand. Only
final states are mirrored — "delivered" (выгружен) and "rejected"
(отклонён): a "in progress" row would stay frozen forever, since appending
never updates what's already written."""
import os

from leadgen.core import alerts, csvfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _path(csv_path):
    return csv_path or os.environ.get('LEADGEN_CONTACTS_CSV') or os.path.join(ROOT, 'db', 'contacts.csv')


def sync(stores, csv_path=None):
    path = _path(csv_path)
    if not os.path.exists(path):
        return 0
    try:
        header, known = csvfile.read(path)
    except csvfile.FormatError as e:
        alerts.raise_alert(stores.archive, 'mirror',
                           'Зеркало contacts.csv не записано: %s. Сохраните файл как CSV с запятыми.' % e)
        return 0
    rows = []
    for r in stores.main.execute("SELECT * FROM contacts WHERE status = 'выгружен' ORDER BY created_at, nick").fetchall():
        if r['nick'] in known:
            continue
        rows.append({'ник': r['nick'], 'тип': 'контакт', 'имя': r.get('name') or '', 'роль': r.get('role') or '',
                     'заведение': r.get('venue') or '', 'город': r.get('city') or '',
                     'уровень': r.get('level') or '', 'статус': 'сдан', 'причина': '',
                     'последняя_активность': r.get('last_activity') or '',
                     'дата_проверки': r.get('checked_date') or '', 'найден_через': r.get('found_via') or '',
                     'агент': r.get('agent') or '',
                     'куда_сдан': r.get('delivered_to') or r.get('legacy_status') or ''})
        known.add(r['nick'])
    for r in stores.archive.execute("SELECT * FROM rejected WHERE stage <> 'импорт' ORDER BY rejected_at").fetchall():
        if r['nick'] in known:
            continue
        rows.append({'ник': r['nick'], 'тип': 'контакт', 'имя': r.get('full_name') or '',
                     'заведение': r.get('venue') or '', 'город': r.get('city') or '', 'статус': 'отклонён',
                     'причина': r['reason'], 'найден_через': r.get('source') or ''})
        known.add(r['nick'])
    return csvfile.append(path, header, rows)


if __name__ == '__main__':
    from leadgen.core import db
    s = db.open_stores()
    try:
        print('дописано: %d' % sync(s))
    finally:
        s.close()

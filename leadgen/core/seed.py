"""One-time seeding of both databases with the project's accumulated data. Idempotent.

    python -m leadgen.core.seed                            # seed
    python -m leadgen.core.seed --backfill-headers 50      # plus header tasks
    python -m leadgen.core.seed --backfill-bridges 50      # plus bridge tasks

Without seeding, the dedup treats every already-delivered contact as new —
so workers refuse to start until settings has seeded_at."""
import argparse
import csv
import glob
import os
import re
from datetime import date

from leadgen.core import contacts, db, settings
from leadgen.qualify import geo
from leadgen.core import queue as wq

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENUES_CSV = os.path.join(ROOT, 'db', 'venues.csv')
CONTACTS_CSV = os.path.join(ROOT, 'db', 'contacts.csv')
GS_TXT = os.path.join(ROOT, 'db', 'cache', 'gs.txt')
DESKTOP = os.path.expanduser('~/Desktop')
EXTRA_XLSX_GLOBS = (os.path.join(DESKTOP, '*.xlsx'),
                    os.path.join(DESKTOP, 'ПРОШЛЫЕ ТАБЛИЦЫ', '**', '*.xlsx'))

REGION_DEPARTMENT = {
    'Сибирь': ('Восток', 'НСК'), 'Урал': ('Восток', 'НСК'), 'Дальний Восток': ('Восток', 'НСК'),
    'Поволжье': ('Москва', 'МСК'), 'Юг': ('Москва', 'МСК'), 'Центр': ('Москва', 'МСК'),
    'Кавказ': ('Москва', 'МСК'), 'Москва': ('Москва', 'МСК'),
    'Северо-Запад': ('Питер', 'СПБ'), 'Питер': ('Питер', 'СПБ'),
}
# Formally Ural, but "Moscow" by business convention (the 2026-09-16 export got three contacts wrong)
CITY_OVERRIDES = {
    'Уфа': ('Москва', 'МСК'), 'Пермь': ('Москва', 'МСК'),
    'Ижевск': ('Москва', 'МСК'), 'Оренбург': ('Москва', 'МСК'),
    'Санкт-Петербург': ('Питер', 'СПБ'), 'Калининград': ('Питер', 'СПБ'),
}

DELIVERED = {'сдан', 'сдан_ранее'}
KEPT_IN_MAIN = {'принят', 'прошёл_ленту', 'резерв', 'на проверку'}
PREMIUM_LEVELS = ('PREMIUM', 'UPPER-MIDDLE')

IG_LINK = re.compile(r'instagram\.com/([A-Za-z0-9._]{2,30})', re.I)
BARE_NICK = re.compile(r'^@?([A-Za-z0-9._]{2,30})$')
# Anchored at the start: "Источник" and "Техник" also contain "ник"
NICK_HEADER = re.compile(r'^\s*(ник|никнейм|instagram|инстаграм|nick|ig)\b', re.I)
NOT_NICKS = {'p', 'reel', 'reels', 'stories', 'explore', 'accounts'}


def _rows(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f))


def seed_geo(main, venues_csv=VENUES_CSV):
    seen = set()
    for row in _rows(venues_csv):
        city, region = (row.get('город') or '').strip(), (row.get('регион') or '').strip()
        if not city or city == '—' or city in seen:
            continue
        mapped = CITY_OVERRIDES.get(city) or REGION_DEPARTMENT.get(region)
        if not mapped:
            continue
        geo.upsert_city(main, city, mapped[0], mapped[1])
        seen.add(city)
    return len(seen)


def import_venues(main, venues_csv=VENUES_CSV):
    imported = 0
    for row in _rows(venues_csv):
        name, city = (row.get('заведение') or '').strip(), (row.get('город') or '').strip()
        if not name or not city:
            continue
        check = (row.get('средний_чек') or '').strip()
        cur = main.execute(
            'INSERT INTO venues (name, city, region, level, avg_check, rating, ig_account,'
            ' chef_chefru, status, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
            ' ON CONFLICT (name, city) DO NOTHING',
            (name, city, row.get('регион', ''), row.get('уровень', ''),
             int(check) if check.isdigit() else None, row.get('рейтинг', ''),
             (row.get('ig_аккаунт') or '').strip().lstrip('@'), row.get('шеф_по_chefru', ''),
             row.get('статус', ''), row.get('источник', '')))
        imported += cur.rowcount
    return imported


def import_contacts(stores, contacts_csv=CONTACTS_CSV):
    to_main = to_rejected = 0
    for row in _rows(contacts_csv):
        nick = contacts.norm_nick(row.get('ник'))
        if not nick:
            continue
        old = (row.get('статус') or '').strip()
        city = (row.get('город') or '').strip()
        if old in DELIVERED or old in KEPT_IN_MAIN:
            cur = stores.main.execute(
                'INSERT INTO contacts (nick, type, name, role, venue, city, department, level,'
                ' status, legacy_status, reason, last_activity, checked_date, found_via, agent,'
                ' delivered_to) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
                ' ON CONFLICT (nick) DO NOTHING',
                (nick, row.get('тип', ''), row.get('имя', ''), row.get('роль', ''),
                 row.get('заведение', ''), city, geo.department_for_city(stores.main, city),
                 row.get('уровень', ''), 'выгружен' if old in DELIVERED else 'импорт', old,
                 row.get('причина', ''), row.get('последняя_активность', ''),
                 row.get('дата_проверки', ''), row.get('найден_через', ''), row.get('агент', ''),
                 row.get('куда_сдан', '')))
            to_main += cur.rowcount
        else:
            before = stores.archive.execute('SELECT COUNT(*) AS c FROM rejected').fetchone()['c']
            contacts.reject(stores, nick, (row.get('причина') or '').strip() or old or 'без статуса',
                            'импорт', full_name=row.get('имя', ''), venue=row.get('заведение', ''),
                            city=city, source=row.get('найден_через', ''), legacy=dict(row))
            after = stores.archive.execute('SELECT COUNT(*) AS c FROM rejected').fetchone()['c']
            to_rejected += after - before
    return to_main, to_rejected


def nicks_from_xlsx(path):
    import openpyxl
    found = set()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        nick_cols = set()
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            for j, value in enumerate(row):
                if value is None:
                    continue
                text = str(value)
                if i == 0 and NICK_HEADER.search(text):
                    nick_cols.add(j)
                    continue
                for m in IG_LINK.findall(text):
                    if m.lower() not in NOT_NICKS:
                        found.add(contacts.norm_nick(m))
                if j in nick_cols:
                    bare = BARE_NICK.match(text.strip())
                    if bare:
                        found.add(contacts.norm_nick(bare.group(1)))
    wb.close()
    return found


def nicks_from_gs(path=GS_TXT):
    with open(path, encoding='utf-8') as f:
        return {contacts.norm_nick(line.split('|')[0]) for line in f if line.strip()}


def import_delivered_nicks(stores, nicks, source):
    added = 0
    for nick in sorted(nicks):
        if not nick or contacts.is_known(stores, nick):
            continue
        stores.main.execute(
            "INSERT INTO contacts (nick, status, legacy_status) VALUES (%s, 'выгружен', %s)"
            ' ON CONFLICT (nick) DO NOTHING', (nick, 'сдан ранее (%s)' % source))
        added += 1
    return added


def backfill(stores, headers=0, bridges=0):
    """Header tasks — for venues with a known account; bridge tasks — for
    PREMIUM and UPPER-MIDDLE venues without one. Already-queued tasks aren't duplicated."""
    queued = {(r['t'], r['k']) for r in stores.archive.execute(
        "SELECT task_type AS t, coalesce(payload->>'ig_account', "
        "(payload->>'venue_name') || '|' || (payload->>'city')) AS k FROM work_queue").fetchall()}
    h = b = 0
    for row in stores.main.execute(
            "SELECT name, city, ig_account FROM venues WHERE coalesce(ig_account, '') <> '' "
            # venues sourced from candidate bios without a level — subscription crawling them wouldn't pay off
            "AND coalesce(source, '') <> 'шапка кандидата' ORDER BY id").fetchall():
        if h >= headers:
            break
        if ('venue_header', row['ig_account']) in queued:
            continue
        wq.enqueue(stores.archive, 'venue_header',
                   {'venue_name': row['name'], 'city': row['city'], 'ig_account': row['ig_account']},
                   priority=55)
        h += 1
    for row in stores.main.execute(
            "SELECT name, city FROM venues WHERE coalesce(ig_account, '') = '' AND level = ANY(%s) "
            'ORDER BY id', (list(PREMIUM_LEVELS),)).fetchall():
        if b >= bridges:
            break
        if ('bridge', '%s|%s' % (row['name'], row['city'])) in queued:
            continue
        wq.enqueue(stores.archive, 'bridge', {'venue_name': row['name'], 'city': row['city']},
                   priority=50)
        b += 1
    return h, b


def main(argv=None):
    parser = argparse.ArgumentParser(description='Засев баз накопленными данными')
    parser.add_argument('--venues', default=VENUES_CSV)
    parser.add_argument('--contacts', default=CONTACTS_CSV)
    parser.add_argument('--no-extra', action='store_true',
                        help='не читать xlsx с Рабочего стола и gs.txt')
    parser.add_argument('--backfill-headers', type=int, default=0)
    parser.add_argument('--backfill-bridges', type=int, default=0)
    args = parser.parse_args(argv)

    stores = db.open_stores()
    try:
        db.init_main(stores.main)
        db.init_archive(stores.archive)
        print('городов в географии: %d' % seed_geo(stores.main, args.venues))
        print('заведений: %d' % import_venues(stores.main, args.venues))
        print('контактов в основную / в отклонённые: %d / %d'
              % import_contacts(stores, args.contacts))
        if not args.no_extra:
            for pattern in EXTRA_XLSX_GLOBS:
                for path in glob.glob(pattern, recursive=True):
                    added = import_delivered_nicks(stores, nicks_from_xlsx(path),
                                                   os.path.relpath(path, DESKTOP))
                    print('из %s: %d' % (os.path.basename(path), added))
            if os.path.exists(GS_TXT):
                print('из gs.txt: %d' % import_delivered_nicks(stores, nicks_from_gs(), 'gs.txt'))
        settings.put(stores.main, 'seeded_at', date.today().isoformat())
        if args.backfill_headers or args.backfill_bridges:
            print('задач шапок / моста: %d / %d'
                  % backfill(stores, args.backfill_headers, args.backfill_bridges))
    finally:
        stores.close()


if __name__ == '__main__':
    main()

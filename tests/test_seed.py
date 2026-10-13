import csv

import openpyxl

from leadgen.core import contacts, seed
from leadgen.qualify import geo

CONTACT_FIELDS = ['ник', 'тип', 'имя', 'роль', 'заведение', 'город', 'уровень', 'статус', 'причина',
                  'последняя_активность', 'дата_проверки', 'найден_через', 'агент', 'куда_сдан']
VENUE_FIELDS = ['заведение', 'город', 'регион', 'уровень', 'средний_чек', 'рейтинг', 'ig_аккаунт',
                'шеф_по_chefru', 'статус', 'источник']


def write_csv(path, fields, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})
    return str(path)


def test_seed_geo_maps_regions_and_ufa_trap(stores, tmp_path):
    path = write_csv(tmp_path / 'v.csv', VENUE_FIELDS, [
        {'заведение': 'A', 'город': 'Новосибирск', 'регион': 'Сибирь'},
        {'заведение': 'B', 'город': 'Уфа', 'регион': 'Урал'},
        {'заведение': 'C', 'город': 'Санкт-Петербург', 'регион': 'Северо-Запад'}])
    seed.seed_geo(stores.main, path)
    assert geo.department_for_city(stores.main, 'Новосибирск') == 'НСК'
    assert geo.department_for_city(stores.main, 'Уфа') == 'МСК'
    assert geo.department_for_city(stores.main, 'Санкт-Петербург') == 'СПБ'


def test_import_venues_is_idempotent(stores, tmp_path):
    path = write_csv(tmp_path / 'v.csv', VENUE_FIELDS, [
        {'заведение': 'Fiorentina', 'город': 'Нижний Новгород', 'уровень': 'PREMIUM',
         'средний_чек': '3600', 'ig_аккаунт': 'fiorentina_nn'}])
    assert seed.import_venues(stores.main, path) == 1
    assert seed.import_venues(stores.main, path) == 0


def test_import_contacts_splits_between_bases(stores, tmp_path):
    path = write_csv(tmp_path / 'c.csv', CONTACT_FIELDS, [
        {'ник': 'Delivered.Chef', 'статус': 'сдан'},
        {'ник': 'accepted_chef', 'статус': 'принят'},
        {'ник': 'sable', 'статус': 'отклонён', 'причина': 'профиль выглядит дёшево'},
        {'ник': 'mention_rest', 'статус': 'упоминание'}])
    to_main, to_rejected = seed.import_contacts(stores, path)
    assert (to_main, to_rejected) == (2, 2)
    main = {r['nick']: r for r in stores.main.execute('SELECT * FROM contacts').fetchall()}
    assert main['delivered.chef']['status'] == 'выгружен'
    assert main['accepted_chef']['status'] == 'импорт'
    assert main['accepted_chef']['legacy_status'] == 'принят'
    rej = {r['nick']: r for r in stores.archive.execute('SELECT * FROM rejected').fetchall()}
    assert rej['sable']['reason'] == 'профиль выглядит дёшево'
    assert rej['mention_rest']['reason'] == 'упоминание'
    assert contacts.is_known(stores, 'mention_rest')


def test_nicks_from_xlsx_reads_links_and_nick_columns(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Имя', 'Instagram', 'Комментарий'])
    ws.append(['Иван', '@Ivan.Chef', 'https://www.instagram.com/other_chef/ в тексте'])
    ws.append(['Пётр', 'https://instagram.com/petr_cook?igsh=1', ''])
    path = str(tmp_path / 'x.xlsx')
    wb.save(path)
    assert seed.nicks_from_xlsx(path) == {'ivan.chef', 'other_chef', 'petr_cook'}


def test_source_column_is_not_mistaken_for_nicks(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Источник', 'Ник'])
    ws.append(['chef.ru', 'real_chef'])
    path = str(tmp_path / 'y.xlsx')
    wb.save(path)
    assert seed.nicks_from_xlsx(path) == {'real_chef'}


def test_nicks_from_gs_takes_first_column(tmp_path):
    path = tmp_path / 'gs.txt'
    path.write_text('chef_willow|Александр Зайцев|Ресторан-1|Пермь\ngrove_harbor|Иван Гроув|Бистро-1|Новосибирск\n',
                    encoding='utf-8')
    assert seed.nicks_from_gs(str(path)) == {'chef_willow', 'grove_harbor'}


def test_import_delivered_nicks_skips_known(stores):
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('grove_harbor', 'выгружен')")
    added = seed.import_delivered_nicks(stores, {'grove_harbor', 'new_one'}, 'gs.txt')
    assert added == 1
    row = stores.main.execute("SELECT status, legacy_status FROM contacts WHERE nick = 'new_one'").fetchone()
    assert row['status'] == 'выгружен'
    assert 'gs.txt' in row['legacy_status']


def test_backfill_queues_headers_for_known_accounts_and_bridges_for_premium_without(stores):
    m = stores.main
    m.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('A', 'Омск', 'PREMIUM', 'a_omsk')")
    m.execute("INSERT INTO venues (name, city, level) VALUES ('B', 'Омск', 'UPPER-MIDDLE')")
    m.execute("INSERT INTO venues (name, city, level) VALUES ('C', 'Омск', 'MIDDLE')")
    headers, bridges = seed.backfill(stores, headers=10, bridges=10)
    assert (headers, bridges) == (1, 1)
    again = seed.backfill(stores, headers=10, bridges=10)
    assert again == (0, 0)


def test_seed_main_marks_database_as_seeded(stores, tmp_path, pg_urls, monkeypatch):
    v = write_csv(tmp_path / 'v.csv', VENUE_FIELDS, [{'заведение': 'A', 'город': 'Омск', 'регион': 'Сибирь'}])
    c = write_csv(tmp_path / 'c.csv', CONTACT_FIELDS, [{'ник': 'x', 'статус': 'сдан'}])
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    seed.main(['--venues', v, '--contacts', c, '--no-extra'])
    assert stores.main.execute("SELECT value FROM settings WHERE key = 'seeded_at'").fetchone()


def test_backfill_skips_venues_found_in_candidate_bios(stores):
    """I10: заведения из шапок кандидатов без уровня — не повод тратить обход."""
    stores.main.execute("INSERT INTO venues (name, city, ig_account, source) VALUES ('X', 'Омск', 'x_omsk', 'шапка кандидата')")
    assert seed.backfill(stores, headers=10) == (0, 0)

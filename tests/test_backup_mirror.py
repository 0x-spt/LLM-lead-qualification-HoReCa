import csv
import glob
import os
from datetime import date

import pytest

from leadgen.core import backup, contacts, mirror

HEADER = ['ник', 'тип', 'имя', 'роль', 'заведение', 'город', 'уровень', 'статус', 'причина',
          'последняя_активность', 'дата_проверки', 'найден_через', 'агент', 'куда_сдан']


def pg_dump_path():
    import pgserver
    found = glob.glob(os.path.join(os.path.dirname(pgserver.__file__), 'pginstall', 'bin', 'pg_dump'))
    return found[0] if found else 'pg_dump'


def test_dump_writes_nonempty_file(stores, pg_urls, tmp_path):
    out = tmp_path / 'main.dump'
    backup.dump(pg_urls['main'], str(out), pg_dump=pg_dump_path())
    assert out.stat().st_size > 1000


def test_prune_keeps_last_14_days(tmp_path):
    for d in ('2026-09-01', '2026-09-15', '2026-09-28'):
        (tmp_path / d).mkdir()
    removed = backup.prune(str(tmp_path), keep_days=14, today=date(2026, 9, 29))
    assert [os.path.basename(p) for p in removed] == ['2026-09-01', '2026-09-15']
    assert sorted(os.listdir(tmp_path)) == ['2026-09-28']


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / 'contacts.csv'
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\r\n')
        w.writerow(HEADER)
        w.writerow(['known', 'контакт', '', '', '', '', '', 'сдан', '', '', '', '', '', ''])
    return str(p)


def test_mirror_appends_only_unknown_nicks_with_status(stores, csv_file):
    stores.main.execute("INSERT INTO contacts (nick, status, venue) VALUES ('known', 'выгружен', 'X'), "
                        "('newly', 'выгружен', 'Fiorentina'), ('wait', 'спорный', '')")
    contacts.reject(stores, 'nope', 'нет роли в шапке', 'роль')
    contacts.add_candidate(stores, 'cand', '1', {}, {}, 'Агент №2')
    assert mirror.sync(stores, csv_file) == 2
    rows = {r['ник']: r for r in csv.DictReader(open(csv_file, encoding='utf-8'))}
    assert rows['newly']['статус'] == 'сдан' and rows['newly']['заведение'] == 'Fiorentina'
    assert rows['nope']['статус'] == 'отклонён' and rows['nope']['причина'] == 'нет роли в шапке'
    assert rows['known']['статус'] == 'сдан'
    assert mirror.sync(stores, csv_file) == 0


def test_mirror_never_rewrites_existing_lines(stores, csv_file):
    before = open(csv_file, 'rb').read()
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('x', 'выгружен')")
    mirror.sync(stores, csv_file)
    assert open(csv_file, 'rb').read().startswith(before)


def test_mirror_missing_file_is_noop(stores, tmp_path):
    assert mirror.sync(stores, str(tmp_path / 'none.csv')) == 0


def test_mirror_file_without_trailing_newline_is_not_corrupted(stores, tmp_path):
    """№1: файл сохранён без перевода строки в конце — новая строка не склеивается с последней."""
    p = tmp_path / 'c.csv'
    p.write_bytes(('\r\n'.join([','.join(HEADER), 'last,контакт,,,,,,сдан,,,,,,X'])).encode('utf-8'))
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('newbie', 'выгружен')")
    mirror.sync(stores, str(p))
    rows = list(csv.DictReader(open(p, encoding='utf-8')))
    assert [r['ник'] for r in rows] == ['last', 'newbie'] and rows[0]['куда_сдан'] == 'X'


def test_mirror_bom_file_is_read_correctly(stores, tmp_path):
    """№2: Excel «CSV UTF-8» ставит BOM — ключ «ник» не должен теряться."""
    p = tmp_path / 'c.csv'
    p.write_bytes(('﻿' + ','.join(HEADER) + '\r\nknown,контакт,,,,,,сдан,,,,,,\r\n').encode('utf-8'))
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('known', 'выгружен'), ('fresh', 'выгружен')")
    assert mirror.sync(stores, str(p)) == 1


def test_mirror_refuses_foreign_format_and_alerts(stores, tmp_path):
    p = tmp_path / 'c.csv'
    p.write_text(';'.join(HEADER) + '\r\n', encoding='utf-8')
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('x', 'выгружен')")
    before = p.read_bytes()
    assert mirror.sync(stores, str(p)) == 0
    assert p.read_bytes() == before
    assert stores.archive.execute("SELECT kind FROM alerts").fetchone()['kind'] == 'mirror'


def test_mirror_writes_only_final_states(stores, csv_file):
    """№3: «в работе» замораживало статусы — зеркалим только итог: сдан или отклонён."""
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('wait', 'спорный'), ('done', 'выгружен')")
    contacts.add_candidate(stores, 'cand', '1', {}, {}, 'Агент №2')
    mirror.sync(stores, csv_file)
    nicks = {r['ник'] for r in csv.DictReader(open(csv_file, encoding='utf-8'))}
    assert 'done' in nicks and 'wait' not in nicks and 'cand' not in nicks


def test_dump_error_does_not_leak_password(tmp_path):
    """№9: ошибка pg_dump с паролем в URL не должна попадать в логи."""
    with pytest.raises(backup.BackupError) as e:
        backup.dump('postgresql://u:SECRETPW@127.0.0.1:1/x', str(tmp_path / 'x.dump'), pg_dump=pg_dump_path())
    assert 'SECRETPW' not in str(e.value)


def test_run_continues_after_one_failed_dump_and_alerts(stores, pg_urls, tmp_path, monkeypatch):
    monkeypatch.setenv('MAIN_DB_URL', 'postgresql://u:pw@127.0.0.1:1/x')
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    folder, errors = backup.run(str(tmp_path), pg_dump=pg_dump_path())
    assert os.path.exists(os.path.join(folder, 'archive.dump')) and len(errors) == 1


def test_csv_export_of_public_tables(stores, pg_urls, tmp_path):
    """pg_dump старше сервера (16 против Supabase 17) — бэкап основной базы в CSV."""
    stores.main.execute("INSERT INTO venues (name, city) VALUES ('Лесной', 'Сургут')")
    folder = tmp_path / 'main_csv'
    backup.export_csv(pg_urls['main'], str(folder))
    rows = list(csv.DictReader(open(folder / 'venues.csv', encoding='utf-8')))
    assert rows[0]['name'] == 'Лесной'
    assert (folder / 'contacts.csv').exists()


def test_run_falls_back_to_csv_when_pg_dump_cannot_dump_server(stores, pg_urls, tmp_path, monkeypatch):
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])

    def mismatch(url, path, pg_dump='pg_dump', schemas=None):
        raise backup.BackupError('pg_dump: код 1: server version mismatch: server version: 17.6; pg_dump version: 16.2')
    monkeypatch.setattr(backup, 'dump', mismatch)
    folder, errors = backup.run(str(tmp_path))
    assert os.path.exists(os.path.join(folder, 'main_csv', 'venues.csv'))
    assert [e for e in errors if e.startswith('main')] == []


def test_csv_fallback_retries_flaky_connection(stores, pg_urls, tmp_path, monkeypatch):
    """Через VPN пулер Supabase иногда не отвечает — бэкап пробует ещё раз."""
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    monkeypatch.setattr(backup, 'RETRY_PAUSE', 0)
    monkeypatch.setattr(backup, 'dump', lambda *a, **k: (_ for _ in ()).throw(
        backup.BackupError('server version mismatch')) if 'main' in a[1] else None)
    real, calls = backup.export_csv, []

    def flaky(url, folder):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('connection timeout expired')
        return real(url, folder)
    monkeypatch.setattr(backup, 'export_csv', flaky)
    folder, errors = backup.run(str(tmp_path))
    assert errors == [] and len(calls) == 2

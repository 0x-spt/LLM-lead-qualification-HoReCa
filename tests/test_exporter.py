import csv
from datetime import date, datetime

import pytest

from leadgen.ops import exporter
from leadgen.core import settings

HEADER = ['ник', 'тип', 'имя', 'роль', 'заведение', 'город', 'уровень', 'статус', 'причина',
          'последняя_активность', 'дата_проверки', 'найден_через', 'агент', 'куда_сдан']


class FakeSheet:
    def __init__(self, existing=()):
        self.existing, self.appended, self.fail = set(existing), [], False

    def existing_nicks(self):
        return set(self.existing)

    def append(self, rows, day):
        if self.fail:
            raise RuntimeError('quota exceeded')
        self.appended.append((rows, day))


def add(stores, nick, dep='МСК', level='PREMIUM', status='к_выгрузке'):
    stores.main.execute(
        "INSERT INTO contacts (nick, status, name, department, level, venue, city, role, "
        "crm_position, agent) VALUES (%s, %s, 'Иван Петров', %s, %s, 'Fiorentina', 'Москва', "
        "'Шеф Повар', 'Шеф', 'Агент №2')", (nick, status, dep, level))


def status(stores, nick):
    return stores.main.execute('SELECT status, delivered_to FROM contacts WHERE nick = %s', (nick,)).fetchone()


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / 'contacts.csv'
    with open(p, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(HEADER)
    return str(p)


def test_no_quota_no_export(stores, csv_path):
    add(stores, 'a')
    sh = FakeSheet()
    out = exporter.run(stores, sh, 'бот', day=date(2026, 9, 28), csv_path=csv_path)
    assert out['added'] == 0 and sh.appended == []
    assert status(stores, 'a')['status'] == 'к_выгрузке'


def test_quota_premium_first_and_limits(stores, csv_path):
    settings.put(stores.main, 'quota:МСК', '2')
    add(stores, 'um1', level='UPPER-MIDDLE')
    add(stores, 'p1')
    add(stores, 'p2')
    add(stores, 'nsk1', dep='НСК')
    sh = FakeSheet()
    out = exporter.run(stores, sh, 'бот', day=date(2026, 9, 28), csv_path=csv_path)
    nicks = [r[0].split('/')[-2] for r in sh.appended[0][0]]
    assert nicks == ['p1', 'p2']
    assert out['by_department'] == {'МСК': 2}
    assert status(stores, 'p1')['status'] == 'выгружен'
    assert status(stores, 'um1')['status'] == 'к_выгрузке'
    assert status(stores, 'nsk1')['status'] == 'к_выгрузке'


def test_already_in_sheet_not_duplicated(stores, csv_path):
    """Выгрузка упала после записи — повтор не дублирует строки."""
    settings.put(stores.main, 'quota:МСК', '5')
    add(stores, 'dup')
    add(stores, 'fresh')
    sh = FakeSheet(existing={'dup'})
    out = exporter.run(stores, sh, 'бот', day=date(2026, 9, 28), csv_path=csv_path)
    assert [r[0] for r in sh.appended[0][0]] == ['https://www.instagram.com/fresh/']
    assert out['already'] == 1
    assert status(stores, 'dup')['delivered_to'] == 'уже в таблице'


def test_sheet_failure_keeps_statuses(stores, csv_path):
    settings.put(stores.main, 'quota:МСК', '5')
    add(stores, 'a')
    sh = FakeSheet()
    sh.fail = True
    with pytest.raises(RuntimeError):
        exporter.run(stores, sh, 'бот', day=date(2026, 9, 28), csv_path=csv_path)
    assert status(stores, 'a')['status'] == 'к_выгрузке'


def test_export_logs_and_mirrors_csv(stores, csv_path):
    settings.put(stores.main, 'quota:МСК', '5')
    add(stores, 'a')
    exporter.run(stores, FakeSheet(), 'расписание', day=date(2026, 9, 28), csv_path=csv_path)
    log = stores.main.execute('SELECT trigger, counts, nicks FROM exports').fetchone()
    assert log['trigger'] == 'расписание' and log['nicks'] == ['a'] and log['counts'] == {'МСК': 1}
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    assert rows[-1]['ник'] == 'a' and rows[-1]['статус'] == 'сдан'
    assert rows[-1]['куда_сдан'] == 'AI ЛИДЫ AMOCRM 28.09.2026'


def test_contacts_without_department_never_exported(stores, csv_path):
    settings.put(stores.main, 'quota:МСК', '5')
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('nodep', 'к_выгрузке')")
    assert exporter.select(stores) == {}


def test_due_only_with_time_and_once_a_day(stores):
    now = datetime(2026, 9, 28, 18, 5)
    assert exporter.due(stores, now) is False
    settings.put(stores.main, 'export_time', '18:00')
    assert exporter.due(stores, datetime(2026, 9, 28, 17, 59)) is False
    assert exporter.due(stores, now) is True
    stores.main.execute("INSERT INTO exports (trigger, counts, nicks, at) VALUES "
                        "('расписание', '{}', '{}', '2026-09-28 18:01+03')")
    assert exporter.due(stores, now) is False
    settings.put(stores.main, 'export_time', 'off')
    assert exporter.due(stores, datetime(2026, 9, 29, 18, 5)) is False


def test_preview_lists_what_would_go(stores):
    settings.put(stores.main, 'quota:НСК', '1')
    add(stores, 'n1', dep='НСК')
    add(stores, 'n2', dep='НСК')
    text = exporter.preview(stores)
    assert 'НСК: 1 из 2' in text and '@n1' in text and '@n2' not in text


def test_already_in_sheet_still_mirrored_to_csv(stores, csv_path):
    """I4: восстановление после падения — канон ручной работы тоже знает этих людей."""
    settings.put(stores.main, 'quota:МСК', '5')
    add(stores, 'dup')
    exporter.run(stores, FakeSheet(existing={'dup'}), 'бот', day=date(2026, 9, 28), csv_path=csv_path)
    assert [r['ник'] for r in csv.DictReader(open(csv_path, encoding='utf-8'))] == ['dup']

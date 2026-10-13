"""Сторож (05.10.2026, «чтобы всё работало без остановок»): тревога, если час нет ни одного нового
контакта, и утренний отчёт по источникам."""
from datetime import datetime
from zoneinfo import ZoneInfo

from leadgen.core import settings
from leadgen.ops import watchdog

MSK = ZoneInfo('Europe/Moscow')


def alerts(stores, kind):
    return [r['text'] for r in stores.archive.execute('SELECT text FROM alerts WHERE kind = %s', (kind,)).fetchall()]


def contact(stores, nick, minutes_ago, status='к_выгрузке', via='following'):
    stores.main.execute("INSERT INTO contacts (nick, status, found_via, created_at) "
                        "VALUES (%s, %s, %s, now() - make_interval(mins => %s))", (nick, status, via, minutes_ago))


def test_no_alert_while_contacts_keep_coming(stores):
    contact(stores, 'a', 20)
    assert watchdog.check_flow(stores) is False and alerts(stores, 'no_leads') == []


def test_alert_after_an_hour_without_contacts_and_not_more_than_hourly(stores):
    contact(stores, 'a', 75)
    assert watchdog.check_flow(stores) is True
    stores.archive.execute('UPDATE alerts SET sent_at = now()')
    assert watchdog.check_flow(stores) is False  # уже напомнили в этот час
    assert len(alerts(stores, 'no_leads')) == 1


def test_paused_collection_does_not_alarm(stores):
    contact(stores, 'a', 300)
    settings.put(stores.main, 'paused', '1')
    assert watchdog.check_flow(stores) is False


def test_morning_report_once_a_day_after_nine(stores):
    contact(stores, 'a', 30, via='following | Шапка')
    contact(stores, 'b', 40, via='отметки')
    contact(stores, 'c', 50, status='спорный', via='отметки')
    assert watchdog.morning_report(stores, now=datetime(2026, 10, 6, 8, 30, tzinfo=MSK)) is False
    assert watchdog.morning_report(stores, now=datetime(2026, 10, 6, 9, 5, tzinfo=MSK)) is True
    assert watchdog.morning_report(stores, now=datetime(2026, 10, 6, 15, 0, tzinfo=MSK)) is False
    text = alerts(stores, 'report')[0]
    assert 'принято 2' in text and 'спорных 1' in text and 'following' in text and 'отметки' in text

"""Collection watchdog (2026-10-05, "so everything keeps running, without stalls, and fast").

check_flow — alerts the bot if no new contact has appeared in over an hour (at most
once an hour). Catches what the "no successful requests" alert misses: requests keep
going out but no leads come in — an empty source, a stuck service, a dead grader. This
is how the 2GIS registry stalled on 10-01 and nobody noticed for four days.

morning_report — once a day after 9:00 Moscow time: accepted and borderline counts for
the last 24 hours by channel, profiles read, queue size, accounts. Both are called by the
worker supervisor.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from leadgen.core import alerts, settings

MSK = ZoneInfo('Europe/Moscow')
QUIET_MINUTES = 60
REPORT_HOUR = 9
GOOD = ('к_выгрузке', 'выгружен', 'сетка_готова', 'текст_ок')


def check_flow(stores, minutes=QUIET_MINUTES):
    if settings.get(stores.main, 'paused') == '1':
        return False
    row = stores.main.execute(
        'SELECT max(created_at) AS last, max(created_at) < now() - make_interval(mins => %s) AS quiet '
        'FROM contacts', (minutes,)).fetchone()
    if not row['last'] or not row['quiet']:
        return False
    recent = stores.archive.execute(
        "SELECT 1 FROM alerts WHERE kind = 'no_leads' AND created_at > now() - make_interval(mins => %s)",
        (minutes,)).fetchone()
    if recent:
        return False
    return alerts.raise_alert(stores.archive, 'no_leads',
                              'Больше %d минут нет новых контактов — последний в %s. Проверьте /status.'
                              % (minutes, row['last'].astimezone(MSK).strftime('%H:%M')))


def _channel(found_via):
    return (found_via or '').split('|')[0].strip() or 'без канала'


def morning_report(stores, now=None):
    now = now or datetime.now(MSK)
    today = now.astimezone(MSK).strftime('%Y-%m-%d')
    if now.astimezone(MSK).hour < REPORT_HOUR or settings.get(stores.main, 'report_day') == today:
        return False
    rows = stores.main.execute(
        "SELECT found_via, status FROM contacts WHERE created_at > now() - interval '24 hours'").fetchall()
    by = {}
    for r in rows:
        counts = by.setdefault(_channel(r['found_via']), [0, 0])
        if r['status'] in GOOD:
            counts[0] += 1
        elif r['status'] == 'спорный':
            counts[1] += 1
    a = stores.archive
    read = a.execute("SELECT count(*) AS c FROM work_queue WHERE task_type = 'profile_info' AND status = 'done' "
                     "AND updated_at > now() - interval '24 hours'").fetchone()['c']
    queue = a.execute("SELECT count(*) AS c FROM work_queue WHERE status = 'pending'").fetchone()['c']
    acc = a.execute("SELECT count(*) FILTER (WHERE kind = 'own') AS own, count(*) FILTER (WHERE kind = 'anon') AS anon "
                    "FROM accounts WHERE status = 'active'").fetchone()
    good = sum(g for g, _ in by.values())
    disputed = sum(d for _, d in by.values())
    lines = ['Сбор за сутки: принято %d, спорных %d, прочитано профилей %d.' % (good, disputed, read)]
    for ch, (g, d) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        if g or d:
            lines.append('  %s — %d (+%d спорных)' % (ch, g, d))
    lines.append('Очередь %d задач, аккаунтов %d, читателей без входа %d.' % (queue, acc['own'], acc['anon']))
    settings.put(stores.main, 'report_day', today)
    return alerts.raise_alert(a, 'report', '\n'.join(lines))

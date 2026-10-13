"""Client decisions on borderline cases, plus enabling/disabling roles.

Each decision is a verdicts row with by_human: the grader mixes the last
twenty into the prompt as examples, so borderline cases decrease over time."""
import re

from leadgen.core import contacts, queue as wq, settings
from leadgen.llm import grader
from leadgen.qualify import quality, roles


def _verdict(stores, nick, verdict, reason):
    stores.main.execute(
        "INSERT INTO verdicts (nick, stage, verdict, reason, by_human) VALUES (%s, 'человек', %s, %s, true)",
        (nick, verdict, reason))


def take(stores, nick):
    cur = stores.main.execute(
        "UPDATE contacts SET status = 'к_выгрузке', updated_at = now() "
        "WHERE nick = %s AND status = 'спорный'", (nick,))
    if cur.rowcount != 1:
        return False
    _verdict(stores, nick, 'accept', 'берём')
    return True


def take_with_department(stores, nick, department):
    """Accept a borderline contact with no determined city — the client specifies the department."""
    cur = stores.main.execute(
        "UPDATE contacts SET status = 'к_выгрузке', department = %s, updated_at = now() "
        "WHERE nick = %s AND status = 'спорный'", (department, nick))
    if cur.rowcount != 1:
        return False
    _verdict(stores, nick, 'accept', 'берём, отдел %s' % department)
    return True


def drop(stores, nick, reason='отклонён заказчиком'):
    row = stores.main.execute("SELECT * FROM contacts WHERE nick = %s AND status = 'спорный'",
                              (nick,)).fetchone()
    if not row:
        return False
    grader.move_to_rejected(stores, row, reason, 'человек')
    _verdict(stores, nick, 'reject', reason)
    return True


def role_on(stores, code):
    title = stores.main.execute('SELECT title FROM role_profiles WHERE code = %s', (code,)).fetchone()
    if not title or not roles.set_enabled(stores.main, code, True):
        return False, 0
    rows = stores.archive.execute(
        "DELETE FROM rejected WHERE stage = 'роль_выкл' AND reason = %s RETURNING nick, pk, source",
        ('должность выключена: %s' % title['title'],)).fetchall()
    for r in rows:
        wq.enqueue(stores.archive, 'profile_info',
                   {'nick': r['nick'], 'pk': r['pk'], 'source': r['source'] or ''}, priority=60)
    return True, len(rows)


def role_off(stores, code):
    return roles.set_enabled(stores.main, code, False)


# --- Human review of rejections. While criteria are being calibrated, rejections go
# to the bot: "верно" ("correct") -> a rejection example for the grader; "берём"
# ("take it") -> the profile re-enters the pipeline tagged approved, so rules don't
# reject it but instead raise the concern as a doubt.
# Guests with no role in the bio (hundreds per hour) and disabled roles aren't a dispute about criteria.

TRIVIAL_REASONS = ('нет роли в шапке', 'аккаунт заведения — обход подписок')
NOT_REVIEWED_STAGES = ('роль_выкл', 'человек', 'импорт')


def reviewable(reason, stage):
    return stage not in NOT_REVIEWED_STAGES and (reason or '') not in TRIVIAL_REASONS


# 2026-09-30, client: only send HoReCa restaurant people to review, "otherwise it's footballers".
NOT_PEOPLE = re.compile(r'аккаунт (заведения|ресторана|кафе|бара)', re.I)
BLOGGERS = re.compile(r'blog|блог|lifestyle|beauty|бьюти|recipe|рецепт|cooking school|онлайн[- ]?школ'
                      r'|курс|mom\b|мама|family|семь[яи]|tours?\b|magazine|журнал|podcast', re.I)
RUSSIA_LATIN = re.compile(r'moscow|msk|spb|petersburg|russia|kazan|sochi|ekb|novosibirsk|krasnodar'
                          r'|vladivostok|kaliningrad|nsk\b', re.I)
CYRILLIC = re.compile(r'[а-яё]', re.I)


def worth_review(reason, stage, bio, full_name, category):
    """Whether this rejection is worth a look: a HoReCa person in Russia, not a guest,
    blogger, foreigner or venue account."""
    if not reviewable(reason, stage) or NOT_PEOPLE.search(reason or ''):
        return False
    bio = (bio or '').strip()
    if not bio:
        return False
    blob = ' '.join([bio, full_name or '', category or ''])
    if not (quality.ROLE_MARK.search(blob) or quality.FOOD_EVIDENCE.search(blob)):
        return False
    if BLOGGERS.search(blob):
        return False
    return bool(CYRILLIC.search(bio) or RUSSIA_LATIN.search(blob))


def rejects_on(stores):
    return settings.get(stores.main, 'review_rejects') == '1'


def set_rejects(stores, on):
    if on and not rejects_on(stores):
        # only rejections from this point on go to the bot, not the whole archive
        settings.put(stores.main, 'review_rejects_since',
                     stores.main.execute('SELECT now()::text AS t').fetchone()['t'])
    settings.put(stores.main, 'review_rejects', '1' if on else '0')


def agree(stores, nick):
    row = stores.archive.execute(
        "UPDATE rejected SET review = 'верно' WHERE nick = %s AND review IS NULL RETURNING reason",
        (nick,)).fetchone()
    if not row:
        return False
    _verdict(stores, nick, 'reject', 'отказ верный: %s' % row['reason'])
    return True


def overturn(stores, nick, why=''):
    row = stores.archive.execute(
        "DELETE FROM rejected WHERE nick = %s AND review IS NULL RETURNING *", (nick,)).fetchone()
    if not row:
        return False
    cached = stores.archive.execute('SELECT * FROM profile_cache WHERE pk = %s',
                                    (row['pk'],)).fetchone() or {} if row['pk'] else {}
    profile = {'full_name': row['full_name'] or cached.get('full_name') or '',
               'biography': row['bio'] or cached.get('bio') or '',
               'category': cached.get('category') or '', 'city_name': cached.get('city') or ''}
    contacts.add_candidate(stores, nick, row['pk'], profile,
                           {'venue': row['venue'] or '', 'city': row['city'] or '',
                            'source': row['source'] or ''}, 'Агент №2')
    # also tag the candidate: enqueue_missing could otherwise recreate the vet task without the flag
    stores.archive.execute("UPDATE candidates SET status = 'одобрен' WHERE nick = %s", (contacts.norm_nick(nick),))
    wq.enqueue(stores.archive, 'vet', {'nick': nick, 'pk': row['pk'], 'approved': True}, priority=10)
    _verdict(stores, nick, 'accept', 'отказ отменён заказчиком (было: %s)%s'
             % (row['reason'], ': %s' % why if why else ''))
    return True


# --- System-accepted contacts (2026-10-01): the client sees each one and can remove it before export

def confirm(stores, nick):
    row = stores.main.execute("SELECT 1 FROM contacts WHERE nick = %s AND status = 'к_выгрузке'", (nick,)).fetchone()
    if not row:
        return False
    _verdict(stores, nick, 'accept', 'принят системой — заказчик подтвердил')
    return True


def unaccept(stores, nick, reason='снят заказчиком после автопринятия'):
    """Remove an auto-accepted contact before export. 'выгружен' (exported) means it's
    already in the output table and can no longer be removed."""
    row = stores.main.execute('SELECT * FROM contacts WHERE nick = %s', (nick,)).fetchone()
    if not row:
        return False
    if row['status'] == 'выгружен':
        return 'выгружен'
    if row['status'] != 'к_выгрузке':
        return False
    grader.move_to_rejected(stores, row, reason, 'человек')
    _verdict(stores, nick, 'reject', reason)
    return True

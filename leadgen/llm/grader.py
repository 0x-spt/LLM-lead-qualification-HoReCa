"""Daily grader: a funnel of "text (Haiku) -> grid (Sonnet) -> human".

    python -m leadgen.llm.grader run     # one run; scheduled on the server
    python -m leadgen.llm.grader refs    # build reference grids (needs a live transport)

The model never sees the databases: Python picks the batch, builds the
prompt, and receives strict JSON (--json-schema). The text stage has no
tools at all; the vision stage only gets Read, scoped to the grids folder.
A claude error (subscription limit, logged out) stops the run; statuses are
left untouched, and contacts wait for the next window."""
import json
import subprocess
import sys
from datetime import date

import os

from leadgen.access import accounts
from leadgen.core import contacts, db, settings
from leadgen.qualify import grid
from leadgen.access.transport import OK

LOCK_KEY = 7317001
DEFAULT_DAILY_CALLS = 150
TEXT_MODEL = 'haiku'


class GraderError(Exception):
    pass


def run_claude(prompt, schema, model, system_prompt, tools='', add_dir=None, timeout=900):
    cmd = ['claude', '-p', '--no-session-persistence', '--strict-mcp-config',
           '--model', model, '--tools', tools, '--output-format', 'json',
           '--json-schema', json.dumps(schema, ensure_ascii=False),
           '--system-prompt', system_prompt]
    if add_dir:
        cmd += ['--add-dir', add_dir]
        os.makedirs(add_dir, exist_ok=True)
    # the prompt goes via stdin: a batch of 25 profiles exceeds Linux's single-argument limit
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=add_dir or None, input=prompt)
    except subprocess.TimeoutExpired:
        raise GraderError('claude не ответил за %d с' % timeout)
    except OSError as e:
        raise GraderError('claude не запустился: %s' % e)
    try:
        out = json.loads(proc.stdout or '{}')
    except ValueError:
        raise GraderError('claude вернул не JSON: %s' % (proc.stdout or proc.stderr)[:200])
    if proc.returncode != 0 or out.get('is_error') or not isinstance(out.get('structured_output'), dict):
        raise GraderError(str(out.get('result') or proc.stderr or 'нет structured_output')[:300])
    return out['structured_output']


def calls_left(stores):
    cap = int(settings.get(stores.main, 'grader_daily_calls', DEFAULT_DAILY_CALLS))
    used = stores.archive.execute(
        "SELECT count(*) AS c FROM grader_log WHERE at >= date_trunc('day', now())").fetchone()['c']
    return max(cap - used, 0)


def _log(stores, stage, model, n, ok, error=None):
    stores.archive.execute(
        'INSERT INTO grader_log (stage, model, profiles, ok, error) VALUES (%s, %s, %s, %s, %s)',
        (stage, model, n, ok, error))


def _verdict(stores, nick, stage, verdict, reason, model, confidence=None, strong=None):
    stores.main.execute(
        'INSERT INTO verdicts (nick, stage, verdict, confidence, strong_profile, reason, model) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s)',
        (nick, stage, verdict, confidence, strong, reason, model))


def move_to_rejected(stores, row, reason, stage):
    """A rejected contact goes to the archive database only; its row is deleted from the main one."""
    contacts.reject(stores, row['nick'], reason, stage, pk=row.get('pk'),
                    full_name=row.get('name') or '', venue=row.get('venue') or '',
                    city=row.get('city') or '', source=row.get('found_via') or '',
                    bio=row.get('bio'), grid_path=row.get('grid_path'))
    stores.main.execute('DELETE FROM contacts WHERE nick = %s', (row['nick'],))


TEXT_SYSTEM = (
    'Ты отбираешь личные Instagram-профили для B2B-продаж премиальной посуды ресторанам. '
    'По шапке и подписям постов реши, работает ли человек сейчас в заведении в указанной '
    'роли. Отклоняй (reject): консультантов без своего заведения, преподавателей и курсы, '
    'выездных и travel-шефов, кейтеринг, домашних кондитеров и торты на заказ, фуд-блогеров '
    'без работы в заведении, тех, кто сейчас не работает, аккаунты заведений и магазинов. '
    'Смешанная лента с личным контентом — НЕ повод для отказа. Требования роли кандидата главнее '
    'этих правил, но только для своей роли: консультант без своего заведения подходит ТОЛЬКО '
    'если роль кандидата — «Ресторанный консалтинг»; при роли «Шеф-повар» или «Шеф-кондитер» '
    'консультант без своего заведения — reject; '
    'для роли «Дилер» подходит дилер, поставщик или менеджер, снабжающий рестораны посудой и '
    'инвентарём HoReCa (в том числе аккаунт такой компании), а магазин посуды для дома — нет. '
    'Если по тексту неясно — '
    'unsure. Причину пиши одной короткой фразой по-русски.')

TEXT_SCHEMA = {'type': 'object', 'required': ['results'], 'properties': {'results': {
    'type': 'array', 'items': {'type': 'object', 'required': ['nick', 'verdict', 'reason'],
                               'properties': {'nick': {'type': 'string'},
                                              'verdict': {'type': 'string',
                                                          'enum': ['pass', 'reject', 'unsure']},
                                              'reason': {'type': 'string'}}}}}}


def _text_prompt(rows):
    parts = ['Профили (ник, роль, заведение, шапка, подписи последних постов):']
    for r in rows:
        caps = ' | '.join((c or '')[:160] for c in (r['captions'] or [])[:12] if c)
        parts.append('— @%s | роль: %s | заведение: %s | шапка: %s | подписи: %s'
                     % (r['nick'], r['role'] or '', r['venue'] or '—', (r['bio'] or '')[:400],
                        caps or '—'))
    return '\n'.join(parts)


def text_stage(stores, batch_size=25):
    out = {'calls': 0, 'passed': 0, 'rejected': 0}
    while calls_left(stores) > 0:
        rows = stores.main.execute(
            "SELECT * FROM contacts WHERE status = 'сетка_готова' ORDER BY created_at, nick LIMIT %s",
            (batch_size,)).fetchall()
        if not rows:
            break
        try:
            result = run_claude(_text_prompt(rows), TEXT_SCHEMA, TEXT_MODEL, TEXT_SYSTEM)
        except GraderError as e:
            _log(stores, 'текст', TEXT_MODEL, len(rows), False, str(e))
            raise
        _log(stores, 'текст', TEXT_MODEL, len(rows), True)
        out['calls'] += 1
        by_nick = {r['nick']: r for r in rows}
        for item in result.get('results') or []:
            row = by_nick.pop(contacts.norm_nick(item.get('nick')), None)
            if row is None:
                continue  # a nick we didn't send — ignore
            _verdict(stores, row['nick'], 'текст', item['verdict'], item.get('reason'), TEXT_MODEL)
            if item['verdict'] == 'reject':
                move_to_rejected(stores, row, item.get('reason') or 'отклонён по тексту', 'оценка_текст')
                out['rejected'] += 1
            else:
                stores.main.execute("UPDATE contacts SET status = 'текст_ок', updated_at = now() "
                                    'WHERE nick = %s', (row['nick'],))
                out['passed'] += 1
        if len(by_nick) == len(rows):
            break  # the model didn't answer for a single one — don't spin uselessly
    return out



VISION_MODEL = 'sonnet'
MIN_TILES = 6
DEFAULT_CONFIDENCE = 0.75
REF_NICKS = ('chef_amber_', 'jasper.kale', 'jasper1_kale1_lotus1')

VISION_SYSTEM = (
    'Ты оцениваешь ленты Instagram шефов для продаж премиальной фарфоровой посуды. Каждая '
    'лента — одна картинка-сетка из 12 последних постов; открой её инструментом Read. Нужны '
    'готовые блюда в подаче на посуде, в заведении; у ресторанного консультанта — подачи, '
    'кухни и открытия заведений из его проектов; у роли «Дилер» — посуда, фарфор, сервировка, '
    'оснащение заведений (подач может не быть вовсе). Смешанная лента с личным контентом '
    'проходит, если подачи в ней есть. Нижняя допустимая граница — уровень jasper1_kale1_lotus1: подачи '
    'вперемешку с городом, поездками и текстовыми карточками. Ниже границы: инфографика во '
    'весь кадр, QR-коды, скриншоты прайсов, реклама, тюнинг машин, отсутствие подач, общее '
    'впечатление «профиль выглядит дёшево». strong_profile = true только для ленты уровня '
    'chef_amber_ / jasper.kale: изящные авторские подачи, ровная профессиональная съёмка, '
    'отраслевой контекст, ни одного бытового кадра. confidence — насколько ты уверен '
    '(0..1). Причину пиши одной короткой фразой по-русски.')

VISION_SCHEMA = {'type': 'object', 'required': ['results'], 'properties': {'results': {
    'type': 'array', 'items': {'type': 'object',
                               'required': ['nick', 'verdict', 'confidence', 'strong_profile',
                                            'reason'],
                               'properties': {'nick': {'type': 'string'},
                                              'verdict': {'type': 'string',
                                                          'enum': ['accept', 'reject', 'unsure']},
                                              'confidence': {'type': 'number'},
                                              'strong_profile': {'type': 'boolean'},
                                              'reason': {'type': 'string'}}}}}}


def human_examples(stores, limit=20):
    rows = stores.main.execute(
        'SELECT nick, verdict, reason FROM verdicts WHERE by_human ORDER BY created_at DESC LIMIT %s',
        (limit,)).fetchall()
    return ['@%s — %s: %s' % (r['nick'], 'берём' if r['verdict'] in ('accept', 'принят') else 'нет',
                              r['reason'] or '') for r in rows]


def _vision_system(stores):
    examples = human_examples(stores)
    if not examples:
        return VISION_SYSTEM
    return VISION_SYSTEM + '\nНедавние решения заказчика по спорным (ориентир):\n' + '\n'.join(examples)


def _role_prompts(stores):
    return {r['code']: r['grader_prompt'] for r in stores.main.execute(
        'SELECT code, grader_prompt FROM role_profiles').fetchall()}


def _vision_prompt(rows, role_prompts=None):
    role_prompts = role_prompts or {}
    parts = []
    refs = [(n, grid.path_for(n, ref=True)) for n in REF_NICKS]
    refs = [(n, p) for n, p in refs if os.path.exists(p)]
    if refs:
        parts.append('Эталоны (открой для сравнения): ' +
                     '; '.join('@%s — %s' % (n, p) for n, p in refs))
    parts.append('Оцени профили:')
    for r in rows:
        parts.append('— @%s | роль: %s | заведение: %s (%s) | заведение подтверждено: %s | '
                     'шапка: %s | сетка: %s'
                     % (r['nick'], r['role'] or '', r['venue'] or '—', r['level'] or 'уровень ?',
                        'нет' if r['needs_strong_profile'] else 'да', (r['bio'] or '')[:300],
                        r['grid_path']))
        if role_prompts.get(r.get('role_code')):
            parts.append('  требования роли: %s' % role_prompts[r['role_code']])
    return '\n'.join(parts)


def _confidence(item):
    try:
        return float(item.get('confidence') or 0)
    except (TypeError, ValueError):
        return 0.0


def _outcome(item, row, threshold):
    """(status | None, rejection reason | None). A final rejection happens only when the
    model is confident and had enough to look at; anything doubtful goes to a human."""
    if _confidence(item) < threshold or item['verdict'] == 'unsure':
        return 'спорный', None
    if item['verdict'] == 'reject':
        if (row.get('grid_tiles') or 12) < MIN_TILES:
            return 'спорный', None  # can't confidently reject on an almost-empty grid
        return None, item.get('reason') or 'лента не проходит'
    if row['needs_strong_profile'] and not item.get('strong_profile'):
        return 'спорный', None  # rule: no venue and feed below the bar -> "needs review"
    if row.get('cap_reason'):
        return 'спорный', None
    return 'к_выгрузке', None


def vision_stage(stores, batch_size=5):
    out = {'calls': 0, 'accepted': 0, 'rejected': 0, 'disputed': 0}
    threshold = float(settings.get(stores.main, 'grade_confidence_min', DEFAULT_CONFIDENCE))
    system = _vision_system(stores)
    while calls_left(stores) > 0:
        rows = stores.main.execute(
            "SELECT * FROM contacts WHERE status = 'текст_ок' ORDER BY created_at, nick LIMIT %s",
            (batch_size,)).fetchall()
        if not rows:
            break
        try:
            result = run_claude(_vision_prompt(rows, _role_prompts(stores)), VISION_SCHEMA, VISION_MODEL, system,
                                tools='Read', add_dir=grid.GRIDS_DIR)
        except GraderError as e:
            _log(stores, 'сетка', VISION_MODEL, len(rows), False, str(e))
            raise
        _log(stores, 'сетка', VISION_MODEL, len(rows), True)
        out['calls'] += 1
        by_nick = {r['nick']: r for r in rows}
        for item in result.get('results') or []:
            row = by_nick.pop(contacts.norm_nick(item.get('nick')), None)
            if row is None:
                continue
            _verdict(stores, row['nick'], 'сетка', item['verdict'], item.get('reason'),
                     VISION_MODEL, _confidence(item), item.get('strong_profile'))
            new_status, reject_reason = _outcome(item, row, threshold)
            if reject_reason:
                move_to_rejected(stores, row, reject_reason, 'оценка')
                out['rejected'] += 1
            else:
                stores.main.execute('UPDATE contacts SET status = %s, reason = %s, updated_at = now() '
                                    'WHERE nick = %s', (new_status, item.get('reason'), row['nick']))
                out['accepted' if new_status == 'к_выгрузке' else 'disputed'] += 1
        if len(by_nick) == len(rows):
            break
    return out


def build_refs(stores, transport, nicks=REF_NICKS):
    paths = []
    for nick in nicks:
        pk, status = transport.pk_by_nick(nick)
        if status != OK or not pk:
            print('эталон @%s: pk не получен (%s)' % (nick, status))
            continue
        items, status = transport.user_feed(pk)
        if status != OK:
            print('эталон @%s: лента не получена (%s)' % (nick, status))
            continue
        images = []
        for it in items[:12]:
            data = transport.download(it['thumb'])[0] if it.get('thumb') else None
            images.append(data)
        path = grid.path_for(nick, ref=True)
        grid.compose(images, path)
        paths.append(path)
    return paths


def run(stores):
    locked = stores.archive.execute('SELECT pg_try_advisory_lock(%s) AS ok', (LOCK_KEY,)).fetchone()['ok']
    if not locked:
        return {'skipped': 'уже идёт другой прогон'}
    try:
        return {'текст': text_stage(stores), 'сетка': vision_stage(stores)}
    finally:
        stores.archive.execute('SELECT pg_advisory_unlock(%s)', (LOCK_KEY,))


DEFAULT_WINDOW = '09:00-21:00'


def in_window(window, now_local):
    start, _, end = (window or DEFAULT_WINDOW).partition('-')
    to_min = lambda s: int(s.split(':')[0]) * 60 + int(s.split(':')[1])
    a, b, n = to_min(start), to_min(end), now_local.hour * 60 + now_local.minute
    return a <= n < b if a < b else (n >= a or n < b)


def loop_once(stores, now_local):
    if settings.get(stores.main, 'paused') == '1':
        return None
    if not in_window(settings.get(stores.main, 'grader_window', DEFAULT_WINDOW), now_local):
        return None
    return run(stores)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    stores = db.open_stores()
    try:
        db.init_main(stores.main)
        db.init_archive(stores.archive)
        if argv[:1] == ['run']:
            try:
                print(run(stores))
            except GraderError as e:
                print('оценщик остановлен: %s' % e, file=sys.stderr)
                raise SystemExit(2)
        elif argv[:1] == ['refs']:
            mode = accounts.transport_mode(stores.main)
            rows = accounts.active_accounts(stores.archive, mode)
            if not rows:
                raise SystemExit('нет активного аккаунта для режима %s' % mode)
            transport = accounts.build_transport(rows[0])
            try:
                print(build_refs(stores, transport))
            finally:
                transport.close()
        elif argv[:1] == ['loop']:
            import time
            from datetime import datetime
            from zoneinfo import ZoneInfo
            while True:
                try:
                    tz = ZoneInfo(settings.get(stores.main, 'tz', 'Europe/Moscow'))
                    out = loop_once(stores, datetime.now(tz).replace(tzinfo=None))
                    if out is not None:
                        print(out)
                except GraderError as e:
                    print('оценщик остановлен до следующего круга: %s' % e, file=sys.stderr)
                except Exception as e:
                    print('оценщик: ошибка: %s' % e, file=sys.stderr)
                    stores.close()
                    time.sleep(60)
                    stores = db.open_stores()
                time.sleep(60 * int(settings.get(stores.main, 'grader_interval_min', 60)))
        else:
            print('использование: python -m leadgen.llm.grader run | refs | loop')
    finally:
        stores.close()


if __name__ == '__main__':
    main()

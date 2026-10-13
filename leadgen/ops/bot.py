"""The client's Telegram bot: borderline cards, alerts, the daily summary, exports, commands.

    python -m leadgen.ops.bot

Listens only to TELEGRAM_OWNER_ID. All settings live in the settings table:
bot commands change it, workers and the grader read it on the fly."""
import os
import re
import sys
import time
from datetime import datetime

import psycopg

from leadgen.access import accounts, vpn_swap
from leadgen.core import db, settings
from leadgen.ops import exporter, telegram
from leadgen.qualify import review, roles
from leadgen.ops.telegram import TelegramError, keyboard

HELP = (
    'Команды:\n'
    '/status — состояние\n/pause, /resume — остановить или продолжить сбор\n'
    '/quota — квоты; /quota МСК 30 — задать\n/export_time 18:00 | off\n'
    '/export_preview — что уйдёт\n/export_now — выгрузить сейчас\n'
    '/mode own | hiker | hybrid\n/hiker_key КЛЮЧ\n/sheet адрес Apps Script\n/roles, /role on|off код\n'
    '/rejects on | off — присылать отказы на проверку\n'
    '/vpn ССЫЛКА — новая подписка VPN (или просто пришлите ссылку)')
TIME = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


class Bot:
    def __init__(self, stores, tg, owner_id, sheet_factory, now=None, spawn_vpn=None):
        self.stores, self.tg, self.owner = stores, tg, int(owner_id)
        self.sheet_factory = sheet_factory
        self.now = now or (lambda: datetime.now())
        self.spawn_vpn = spawn_vpn or _spawn_vpn

    @property
    def main(self):
        return self.stores.main

    def say(self, text, **kw):
        return self.tg.send_message(self.owner, text, **kw)

    # --- inbound ---

    def handle_update(self, update):
        if 'callback_query' in update:
            q = update['callback_query']
            if (q.get('from') or {}).get('id') != self.owner:
                return
            self._callback(q)
            return
        m = update.get('message') or {}
        if (m.get('from') or {}).get('id') != self.owner:
            return
        text = (m.get('text') or '').strip()
        if not text:
            return
        pending = settings.get(self.main, 'await_reason') or ''
        nick, _, prompt_id = pending.partition(':')
        reply_to = (m.get('reply_to_message') or {}).get('message_id')
        if nick and prompt_id and reply_to is not None and str(reply_to) == prompt_id:
            # a reason is accepted only as a reply to the prompt — random text a day later
            # must not reject a contact; the reason itself may start with "/"
            settings.put(self.main, 'await_reason', '')
            if nick.startswith('acc|'):
                nick = nick[len('acc|'):]
                done = review.unaccept(self.stores, nick, text[:300])
                self.say('@%s — снят из выгрузки: %s' % (nick, text[:300]) if done is True else
                         '@%s уже в таблице — удалите строку там вручную' % nick if done == 'выгружен' else
                         '@%s уже решён' % nick)
            elif nick.startswith('back|'):
                nick = nick[len('back|'):]
                if review.overturn(self.stores, nick, text[:300]):
                    self.say('@%s — берём, пойдёт в повторный отбор. Учту: %s' % (nick, text[:300]))
                else:
                    self.say('@%s уже решён' % nick)
            elif review.drop(self.stores, nick, text[:300]):
                self.say('@%s — нет: %s' % (nick, text[:300]))
            else:
                self.say('@%s уже решён' % nick)
            return
        if text.startswith('/'):
            settings.put(self.main, 'await_reason', '')
            self._command(text, m)
        elif vpn_swap.looks_like_subscription(text):
            self._vpn(text, m)

    def _vpn(self, url, m):
        """A subscription link is an access key: the message gets deleted, nothing is
        written to the database, and the connection runs in the background."""
        deleted = self.tg.delete_message(self.owner, m.get('message_id'))
        self.spawn_vpn(url)
        self.say('Подписка %s: проверяю серверы и подключаю — отчёт пришлю через 2–3 минуты.%s'
                 % (vpn_swap.urlparse(url).hostname,
                    '' if deleted else ' Удалить сообщение со ссылкой не удалось — удалите его вручную.'))

    def _callback(self, q):
        action, _, nick = (q.get('data') or '').partition(':')
        msg = q.get('message') or {}
        if action in ('rok', 'rback', 'rwhy'):
            self._reject_callback(q, action, nick, msg)
            return
        if action in ('aok', 'adrop', 'awhy'):
            self._accepted_callback(q, action, nick, msg)
            return
        if action == 'why':
            self.tg.answer_callback(q['id'], 'Напишите причину ответом на сообщение')
            prompt = self.say('Причина отказа для @%s? Ответьте на это сообщение.' % nick,
                              force_reply=True)
            settings.put(self.main, 'await_reason', '%s:%s' % (nick, (prompt or {}).get('message_id')))
            return
        if action == 'take' and self._department(nick) is None:
            # city is undetermined — without a department the contact won't reach the sheet, so ask
            self.tg.answer_callback(q['id'], 'Укажите отдел')
            self.say('@%s — какой отдел?' % nick, reply_markup=self._department_keyboard(nick))
            return
        if action == 'dep':
            department, _, nick = nick.partition(':')
            done = review.take_with_department(self.stores, nick, department)
            action = 'take'
        elif action == 'geo':
            done = review.drop(self.stores, nick, 'вне географии')
            action = 'drop'
        else:
            done = review.take(self.stores, nick) if action == 'take' else \
                review.drop(self.stores, nick) if action == 'drop' else False
        if not done:
            self.tg.answer_callback(q['id'], 'Уже решено')
            return
        label = '✅ Берём' if action == 'take' else '❌ Нет'
        self.tg.answer_callback(q['id'], label)
        self._mark(msg, label)

    def _accepted_callback(self, q, action, nick, msg):
        """Card for an auto-accepted contact: "ok" becomes an example for the grader; "remove" pulls it before export."""
        if action == 'awhy':
            self.tg.answer_callback(q['id'], 'Напишите причину ответом на сообщение')
            prompt = self.say('Почему убираем @%s? Ответьте на это сообщение — поправлю критерий.' % nick,
                              force_reply=True)
            settings.put(self.main, 'await_reason', 'acc|%s:%s' % (nick, (prompt or {}).get('message_id')))
            return
        done = review.confirm(self.stores, nick) if action == 'aok' else review.unaccept(self.stores, nick)
        if done == 'выгружен':
            self.tg.answer_callback(q['id'], 'Уже в таблице — удалите строку там вручную')
            return
        if not done:
            self.tg.answer_callback(q['id'], 'Уже решено')
            return
        label = '👍 Ок, остаётся в выгрузке' if action == 'aok' else '❌ Убран из выгрузки'
        self.tg.answer_callback(q['id'], label)
        self._mark(msg, label)

    def _reject_callback(self, q, action, nick, msg):
        """Card for a reject: "correct" becomes an example for the grader; "take" sends it back for re-vetting."""
        if action == 'rwhy':
            self.tg.answer_callback(q['id'], 'Напишите, почему берём, ответом на сообщение')
            prompt = self.say('Почему @%s подходит? Ответьте на это сообщение — поправлю критерий.' % nick,
                              force_reply=True)
            settings.put(self.main, 'await_reason', 'back|%s:%s' % (nick, (prompt or {}).get('message_id')))
            return
        done = review.agree(self.stores, nick) if action == 'rok' else review.overturn(self.stores, nick)
        if not done:
            self.tg.answer_callback(q['id'], 'Уже решено')
            return
        label = '👍 Отказ верный' if action == 'rok' else '↩️ Берём — повторный отбор'
        self.tg.answer_callback(q['id'], label)
        self._mark(msg, label)

    def _mark(self, msg, label):
        """The decision gets appended to the card and the buttons are removed: a photo's
        caption is edited, a text message's body is edited."""
        if 'caption' in msg or 'text' not in msg:
            self.tg.edit_caption(self.owner, msg.get('message_id'),
                                 '%s\n\n%s' % ((msg.get('caption') or '')[:900], label))
        else:
            self.tg.edit_text(self.owner, msg.get('message_id'), '%s\n\n%s' % (msg['text'][:3800], label))

    def _command(self, text, m):
        parts = text.split()
        cmd, args = parts[0].split('@')[0].lower(), parts[1:]
        if cmd in ('/start', '/help'):
            self.say(HELP)
        elif cmd == '/status':
            self.say(self.summary_text())
        elif cmd == '/pause':
            settings.put(self.main, 'paused', '1')
            self.say('Сбор на паузе. /resume — продолжить')
        elif cmd == '/resume':
            settings.put(self.main, 'paused', '0')
            self.say('Сбор продолжается')
        elif cmd == '/quota':
            if not args:
                q = exporter.quotas(self.main)
                self.say('Квоты: %s' % (', '.join('%s %d' % kv for kv in q.items()) or 'не заданы'))
            elif len(args) == 2 and args[0].upper() in exporter.DEPARTMENTS and args[1].isdigit():
                settings.put(self.main, 'quota:%s' % args[0].upper(), args[1])
                self.say('Квота %s: %s' % (args[0].upper(), args[1]))
            else:
                self.say('Формат: /quota МСК 30. Отделы: %s' % ', '.join(exporter.DEPARTMENTS))
        elif cmd == '/export_time':
            value = args[0].lower() if args else ''
            if value == 'off' or TIME.match(value):
                settings.put(self.main, 'export_time', value)
                self.say('Выгрузка по расписанию: %s' % ('выключена' if value == 'off' else value))
            else:
                self.say('Формат: /export_time ЧЧ:ММ или /export_time off')
        elif cmd == '/export_preview':
            self.say(exporter.preview(self.stores))
        elif cmd == '/export_now':
            self._export('бот')
        elif cmd == '/mode':
            if args and args[0] in accounts.MODES:
                settings.put(self.main, 'ig_transport', args[0])
                self.say('Режим: %s' % args[0])
            else:
                self.say('Формат: /mode own | hiker | hybrid')
        elif cmd == '/hiker_key':
            deleted = self.tg.delete_message(self.owner, m.get('message_id'))
            if not args:
                self.say('Формат: /hiker_key КЛЮЧ')
                return
            accounts.register_hiker(self.stores.archive, args[0])
            if settings.get(self.main, 'ig_transport') is None:
                settings.put(self.main, 'ig_transport', 'hybrid')
            self.say('Ключ HikerAPI сохранён, режим: %s. %s'
                     % (accounts.transport_mode(self.main),
                        'Сообщение с ключом удалено.' if deleted
                        else 'Удалить сообщение с ключом не удалось — удалите его вручную.'))
        elif cmd == '/vpn':
            if args and vpn_swap.looks_like_subscription(args[0]):
                self._vpn(args[0], m)
            else:
                self.say('Формат: /vpn https://…/s/… — или просто пришлите ссылку подписки')
        elif cmd == '/sheet':
            if args and args[0].startswith('https://script.google.com/'):
                settings.put(self.main, 'sheet_webapp_url', args[0])
                self.say('Таблица подключена. Проверка: /export_preview, затем /export_now')
            else:
                self.say('Формат: /sheet https://script.google.com/macros/s/…/exec')
        elif cmd == '/rejects':
            if args and args[0] in ('on', 'off'):
                review.set_rejects(self.stores, args[0] == 'on')
                self.say('Отказы на проверку: %s' % ('присылаю' if args[0] == 'on' else 'не присылаю'))
            else:
                self.say('Формат: /rejects on | off (сейчас: %s)' % ('on' if review.rejects_on(self.stores) else 'off'))
        elif cmd == '/roles':
            rows = self.main.execute('SELECT code, title, enabled FROM role_profiles ORDER BY priority').fetchall()
            self.say('\n'.join('%s %s — %s' % ('✅' if r['enabled'] else '▫️', r['code'], r['title']) for r in rows))
        elif cmd == '/role' and len(args) == 2 and args[0] in ('on', 'off'):
            if args[0] == 'on':
                ok, back = review.role_on(self.stores, args[1])
                self.say('Должность %s включена, возвращено в работу: %d' % (args[1], back) if ok
                         else 'Нет такой должности: %s' % args[1])
            else:
                self.say('Должность %s выключена' % args[1] if review.role_off(self.stores, args[1])
                         else 'Нет такой должности: %s' % args[1])
        else:
            self.say(HELP)

    # --- outbound ---

    def _department(self, nick):
        row = self.main.execute('SELECT department FROM contacts WHERE nick = %s', (nick,)).fetchone()
        return (row or {}).get('department')

    @staticmethod
    def _department_keyboard(nick):
        return keyboard([[('МСК', 'dep:МСК:%s' % nick), ('НСК', 'dep:НСК:%s' % nick), ('СПБ', 'dep:СПБ:%s' % nick)],
                         [('🌍 Не наша география', 'geo:%s' % nick)],
                         [('❌ Нет', 'drop:%s' % nick), ('✍️ Нет + причина', 'why:%s' % nick)]])

    def card_caption(self, r):
        doubt = '; '.join(x for x in (r.get('reason'), r.get('cap_reason')) if x)
        return ('@%s — %s\n%s (%s), %s %s\nШапка: %s\nСомнение: %s\nhttps://www.instagram.com/%s/'
                % (r['nick'], r.get('role') or '—', r.get('venue') or 'заведение не найдено',
                   r.get('level') or 'уровень ?', r.get('city') or '', r.get('department') or '',
                   (r.get('bio') or '')[:350], doubt or '—', r['nick']))

    def push_cards(self, limit=5):
        rows = self.main.execute(
            "SELECT * FROM contacts WHERE status = 'спорный' AND tg_card_at IS NULL "
            'ORDER BY updated_at LIMIT %s', (limit,)).fetchall()
        for r in rows:
            if r.get('department'):
                kb = keyboard([[('✅ Берём', 'take:%s' % r['nick']), ('❌ Нет', 'drop:%s' % r['nick'])],
                               [('✍️ Нет + причина', 'why:%s' % r['nick'])]])
                caption = self.card_caption(r)
            else:
                kb = self._department_keyboard(r['nick'])
                caption = self.card_caption(r) + '\nГород не определён — укажите отдел кнопкой, чтобы взять.'
            try:
                sent = self.tg.send_photo(self.owner, r.get('grid_path'), caption, reply_markup=kb)
            except TelegramError:
                # the photo was rejected (a broken grid, etc.) — the card goes out as text
                # instead, otherwise one bad card would lock up the entire borderline queue
                sent = self.tg.send_message(self.owner, caption, reply_markup=kb)
            self.main.execute('UPDATE contacts SET tg_card_at = now(), tg_message_id = %s WHERE nick = %s',
                              ((sent or {}).get('message_id'), r['nick']))
        return len(rows)

    def reject_caption(self, r):
        via = ' (%s)' % r['city'] if r.get('city') else ''
        return ('🚫 Отклонён: @%s\nПричина: %s (%s)\nНайден через: %s%s\nШапка: %s\nhttps://www.instagram.com/%s/'
                % (r['nick'], r['reason'], r['stage'], r.get('venue') or r.get('source') or '—', via,
                   (r.get('bio') or '')[:350], r['nick']))

    def push_reject_cards(self, limit=5):
        if not review.rejects_on(self.stores):
            return 0
        since = settings.get(self.main, 'review_rejects_since') or 'now'
        archive = self.stores.archive
        rows = archive.execute(
            'SELECT r.*, p.category FROM rejected r LEFT JOIN profile_cache p ON p.pk = r.pk '
            'WHERE r.review_sent_at IS NULL AND r.review IS NULL '
            'AND r.rejected_at >= %s::timestamptz AND r.stage <> ALL(%s) AND r.reason <> ALL(%s) '
            'ORDER BY r.rejected_at LIMIT %s',
            (since, list(review.NOT_REVIEWED_STAGES), list(review.TRIVIAL_REASONS), limit * 10)).fetchall()
        sent_count = 0
        for r in rows:
            if sent_count >= limit:
                break
            if not review.worth_review(r['reason'], r['stage'], r.get('bio'), r.get('full_name'), r.get('category')):
                # not HoReCa, a blogger, a foreigner, a venue account — stays in the archive, doesn't go to the bot
                archive.execute("UPDATE rejected SET review = 'не_показан' WHERE nick = %s", (r['nick'],))
                continue
            sent_count += 1
            kb = keyboard([[('👍 Верно', 'rok:%s' % r['nick']), ('↩️ Берём', 'rback:%s' % r['nick'])],
                           [('✍️ Берём + почему', 'rwhy:%s' % r['nick'])]])
            caption = self.reject_caption(r)
            sent = None
            if r.get('grid_path'):
                try:
                    sent = self.tg.send_photo(self.owner, r['grid_path'], caption, reply_markup=kb)
                except TelegramError:
                    sent = None  # the grid was rejected — the card goes out as text
            if sent is None:
                sent = self.tg.send_message(self.owner, caption, reply_markup=kb)
            self.stores.archive.execute(
                'UPDATE rejected SET review_sent_at = now(), review_message_id = %s WHERE nick = %s',
                ((sent or {}).get('message_id'), r['nick']))
        return sent_count

    def push_accepted_cards(self, limit=5):
        """Contacts the system accepted without a bot card yet (client request, 2026-10-01:
        "so I can see the ones accepted automatically"). They reach the export regardless of
        any tap; "remove" pulls them out before export."""
        rows = self.main.execute(
            "SELECT * FROM contacts WHERE status = 'к_выгрузке' AND tg_card_at IS NULL "
            'ORDER BY updated_at LIMIT %s', (limit,)).fetchall()
        for r in rows:
            kb = keyboard([[('👍 Ок', 'aok:%s' % r['nick']), ('❌ Убрать', 'adrop:%s' % r['nick'])],
                           [('✍️ Убрать + почему', 'awhy:%s' % r['nick'])]])
            caption = ('✅ Принят автоматически: @%s — %s\n%s (%s), %s %s\nШапка: %s\nОценщик: %s\n'
                       'https://www.instagram.com/%s/'
                       % (r['nick'], r.get('role') or '—', r.get('venue') or 'заведение не найдено',
                          r.get('level') or 'уровень ?', r.get('city') or '', r.get('department') or '',
                          (r.get('bio') or '')[:350], r.get('reason') or '—', r['nick']))
            sent = None
            if r.get('grid_path'):
                try:
                    sent = self.tg.send_photo(self.owner, r['grid_path'], caption, reply_markup=kb)
                except TelegramError:
                    sent = None
            if sent is None:
                sent = self.tg.send_message(self.owner, caption, reply_markup=kb)
            self.main.execute('UPDATE contacts SET tg_card_at = now(), tg_message_id = %s WHERE nick = %s',
                              ((sent or {}).get('message_id'), r['nick']))
        return len(rows)

    def push_alerts(self):
        rows = self.stores.archive.execute(
            'SELECT id, text FROM alerts WHERE sent_at IS NULL ORDER BY id LIMIT 10').fetchall()
        for r in rows:
            self.say('⚠️ %s' % r['text'])
            self.stores.archive.execute('UPDATE alerts SET sent_at = now() WHERE id = %s', (r['id'],))
        return len(rows)

    def summary_text(self):
        m, a = self.main, self.stores.archive
        count = lambda conn, sql, *p: conn.execute(sql, p).fetchone()['c']
        waiting = m.execute("SELECT department, count(*) AS c FROM contacts WHERE status = 'к_выгрузке' "
                            'GROUP BY 1 ORDER BY 1').fetchall()
        reasons = a.execute("SELECT reason, count(*) AS c FROM rejected WHERE rejected_at > now() - interval '1 day' "
                            "AND stage <> 'импорт' GROUP BY 1 ORDER BY 2 DESC LIMIT 5").fetchall()
        accs = a.execute('SELECT status, count(*) AS c FROM accounts GROUP BY 1').fetchall()
        return '\n'.join([
            'Сводка %s' % self.now().strftime('%d.%m %H:%M'),
            'Сбор: %s, режим %s' % ('на паузе' if settings.get(m, 'paused') == '1' else 'идёт',
                                    accounts.transport_mode(m)),
            'Кандидатов в работе: %d' % count(a, 'SELECT count(*) AS c FROM candidates'),
            'Сетки за сутки: %d' % count(m, "SELECT count(*) AS c FROM contacts WHERE created_at > now() - interval '1 day'"),
            'Ждут выгрузки: %s' % (', '.join('%s %d' % (r['department'] or '?', r['c']) for r in waiting) or '0'),
            'Спорных ждут решения: %d' % count(m, "SELECT count(*) AS c FROM contacts WHERE status = 'спорный'"),
            'Квоты: %s; время: %s' % (', '.join('%s %d' % kv for kv in exporter.quotas(m).items()) or 'не заданы',
                                      settings.get(m, 'export_time') or 'не задано'),
            'Аккаунты: %s' % (', '.join('%s %d' % (r['status'], r['c']) for r in accs) or 'нет'),
            'Отказы за сутки: %s' % ('; '.join('%s — %d' % (r['reason'][:40], r['c']) for r in reasons) or 'нет'),
        ])

    def maybe_summary(self, now_local):
        at = settings.get(self.main, 'summary_time', '09:00')
        hh, mm = (int(x) for x in at.split(':'))
        today = now_local.date().isoformat()
        if (now_local.hour, now_local.minute) < (hh, mm) or settings.get(self.main, 'summary_sent_on') == today:
            return False
        self.say(self.summary_text())
        settings.put(self.main, 'summary_sent_on', today)
        return True

    def _export(self, trigger):
        try:
            out = exporter.run(self.stores, self.sheet_factory(), trigger)
        except Exception as e:
            self.say('⚠️ Выгрузка не удалась, статусы не тронуты: %s' % str(e)[:300])
            return None
        if out['added']:
            self.say('Выгружено новых строк: %d (%s)%s'
                     % (out['added'], ', '.join('%s %d' % kv for kv in out['by_department'].items()),
                        '; ещё %d уже были в таблице — им только обновлён статус' % out['already'] if out['already'] else ''))
        elif out['already']:
            self.say('Новых строк в таблице нет: все %d выбранных уже были в таблице (прошлая выгрузка '
                     'записалась, но не успела отметиться) — им обновлён статус. Повторите /export_now, '
                     'чтобы выгрузить следующих.' % out['already'])
        else:
            self.say('Выгружать нечего: %s' % exporter.preview(self.stores).split('\n')[0])
        return out

    def maybe_export(self, now_local):
        if not exporter.due(self.stores, now_local):
            return False
        # mark the attempt before exporting: empty or failed, it's one message a day, not a flood
        settings.put(self.main, 'export_attempted_on', now_local.date().isoformat())
        self._export('расписание')
        return True

    def tick(self, now_local):
        for step in (self.push_cards, self.push_accepted_cards, self.push_reject_cards, self.push_alerts,
                     lambda: self.maybe_summary(now_local), lambda: self.maybe_export(now_local)):
            try:
                step()
            except psycopg.OperationalError:
                raise  # database connection lost — serve() will reconnect
            except Exception as e:
                print('бот: шаг не выполнен: %s' % e, file=sys.stderr)


def _spawn_vpn(url):
    """Connecting a subscription runs as a separate process: the bot doesn't block for the 2-3 minutes of exit checks."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    log = open(os.path.join(root, 'logs', 'vpn_swap.log'), 'a')
    subprocess.Popen([os.path.join(root, 'deploy', 'local', 'run.sh'), 'leadgen.access.vpn_swap'],
                     env=dict(os.environ, NEW_VPN_SUB=url), stdout=log, stderr=log, start_new_session=True)


def make_sheet(main):
    """Apps Script inside the sheet (no Google Cloud) if its URL is set,
    otherwise a gspread service account keyed by settings.sheet_key."""
    from leadgen.ops import sheet
    url = settings.get(main, 'sheet_webapp_url')
    if url and os.environ.get('SHEETS_WEBAPP_SECRET'):
        return sheet.AppsScriptSheet(url, os.environ['SHEETS_WEBAPP_SECRET'])
    key = settings.get(main, 'sheet_key')
    if key:
        return sheet.GspreadSheet(key, settings.get(main, 'sheet_worksheet'), os.environ.get('GOOGLE_SA_JSON'))
    raise RuntimeError('таблица не подключена: /sheet <адрес веб-приложения Apps Script> '
                       'и SHEETS_WEBAPP_SECRET в .env (docs/apps-script.md)')


def _local_now(stores):
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(settings.get(stores.main, 'tz', 'Europe/Moscow'))
    except Exception:
        tz = ZoneInfo('Europe/Moscow')  # a typo in settings.tz must not crash the bot
    return datetime.now(tz).replace(tzinfo=None)


def serve(make_stores, tg, owner_id, sheet_factory_for, stop_event, sleep=time.sleep, max_loops=None):
    """The main loop. A database drop triggers a reconnect, a Telegram drop a pause;
    the bot process crashes from neither."""
    import psycopg
    stores, b, offset, loops = None, None, None, 0
    while not stop_event.is_set() and (max_loops is None or loops < max_loops):
        loops += 1
        try:
            if stores is None:
                stores = make_stores()
                b = Bot(stores, tg, owner_id, sheet_factory_for(stores),
                        now=lambda: _local_now(stores))
            for u in tg.get_updates(offset=offset, timeout=25):
                offset = u['update_id'] + 1
                try:
                    b.handle_update(u)
                except psycopg.OperationalError:
                    raise
                except Exception as e:
                    print('бот: обновление не обработано: %s' % e, file=sys.stderr)
            b.tick(_local_now(stores))
        except TelegramError as e:
            print('бот: Telegram недоступен: %s' % e, file=sys.stderr)
            sleep(10)
        except psycopg.OperationalError as e:
            print('бот: база недоступна (%s), переподключение' % e, file=sys.stderr)
            if stores is not None:
                stores.close()
            stores, b = None, None
            sleep(30)
    if stores is not None:
        stores.close()


def main():
    import threading
    sys.stdout.reconfigure(line_buffering=True)
    missing = [k for k in ('MAIN_DB_URL', 'ARCHIVE_DB_URL', 'TELEGRAM_TOKEN', 'TELEGRAM_OWNER_ID')
               if not os.environ.get(k)]
    if missing:
        raise SystemExit('не заданы переменные окружения: %s' % ', '.join(missing))
    first = db.open_stores()
    db.init_main(first.main)
    db.init_archive(first.archive)
    first.close()

    def sheet_factory_for(stores):
        return lambda: make_sheet(stores.main)

    print('бот запущен')
    serve(db.open_stores, telegram.Telegram(os.environ['TELEGRAM_TOKEN']),
          os.environ['TELEGRAM_OWNER_ID'], sheet_factory_for, threading.Event())


if __name__ == '__main__':
    main()

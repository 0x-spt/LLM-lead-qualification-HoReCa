from datetime import datetime

import pytest

from leadgen.access import accounts
from leadgen.core import alerts, settings
from leadgen.ops import bot, telegram
from leadgen.qualify import roles

OWNER = 111


class FakeTg:
    def __init__(self):
        self.sent, self.photos, self.edits, self.answers, self.deleted = [], [], [], [], []
        self.text_edits = []
        self.fail = False

    def send_message(self, chat_id, text, reply_markup=None, force_reply=False):
        if self.fail:
            raise telegram.TelegramError('сеть')
        self.sent.append((chat_id, text, reply_markup, force_reply))
        return {'message_id': len(self.sent)}

    def send_photo(self, chat_id, path, caption, reply_markup=None):
        if self.fail:
            raise telegram.TelegramError('сеть')
        self.photos.append((chat_id, path, caption, reply_markup))
        return {'message_id': 100 + len(self.photos)}

    def edit_caption(self, chat_id, message_id, caption):
        self.edits.append((message_id, caption))

    def edit_text(self, chat_id, message_id, text):
        self.text_edits.append((message_id, text))

    def answer_callback(self, cid, text=''):
        self.answers.append(text)

    def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


class FakeSheet:
    def __init__(self):
        self.appended = []

    def existing_nicks(self):
        return set()

    def append(self, rows, day):
        self.appended.append(rows)


@pytest.fixture
def b(stores):
    roles.ensure_defaults(stores.main)
    sheet = FakeSheet()
    return bot.Bot(stores, FakeTg(), OWNER, lambda: sheet), sheet


def msg(text, uid=OWNER):
    return {'update_id': 1, 'message': {'message_id': 9, 'chat': {'id': uid}, 'from': {'id': uid}, 'text': text}}


def cb(data, uid=OWNER):
    return {'update_id': 2, 'callback_query': {'id': 'c1', 'from': {'id': uid}, 'data': data,
                                               'message': {'message_id': 101, 'chat': {'id': uid},
                                                           'caption': 'карточка'}}}


def disputed(stores, nick='a'):
    stores.main.execute(
        "INSERT INTO contacts (nick, status, venue, level, department, role, bio, reason, cap_reason, grid_path) "
        "VALUES (%s, 'спорный', 'Fiorentina', 'PREMIUM', 'МСК', 'Шеф Повар', 'Шеф-повар', 'подачи на грани', "
        "NULL, '/nonexistent.jpg')", (nick,))


def last_text(bb):
    return bb.tg.sent[-1][1]


def test_stranger_is_ignored(b, stores):
    bb, _ = b
    bb.handle_update(msg('/pause', uid=999))
    bb.handle_update(cb('drop:a', uid=999))
    assert bb.tg.sent == [] and settings.get(stores.main, 'paused') is None


def test_quota_and_export_time_commands(b, stores):
    bb, _ = b
    bb.handle_update(msg('/quota МСК 30'))
    bb.handle_update(msg('/export_time 18:00'))
    assert settings.get(stores.main, 'quota:МСК') == '30'
    assert settings.get(stores.main, 'export_time') == '18:00'
    bb.handle_update(msg('/quota ЕКБ 5'))
    assert 'Отделы' in last_text(bb)
    bb.handle_update(msg('/export_time 25:99'))
    assert 'ЧЧ:ММ' in last_text(bb)


def test_pause_resume(b, stores):
    bb, _ = b
    bb.handle_update(msg('/pause'))
    assert settings.get(stores.main, 'paused') == '1'
    bb.handle_update(msg('/resume'))
    assert settings.get(stores.main, 'paused') == '0'


def test_hiker_key_is_stored_and_message_deleted(b, stores):
    bb, _ = b
    bb.handle_update(msg('/hiker_key SECRET'))
    assert accounts.active_accounts(stores.archive, 'hiker')[0]['api_key'] == 'SECRET'
    assert bb.tg.deleted == [9]
    assert settings.get(stores.main, 'ig_transport') == 'hybrid'


def test_cards_are_pushed_once_with_buttons(b, stores):
    bb, _ = b
    disputed(stores)
    assert bb.push_cards() == 1
    assert bb.push_cards() == 0
    chat, path, caption, kb = bb.tg.photos[0]
    assert '@a' in caption and 'Fiorentina' in caption and 'подачи на грани' in caption
    assert [btn['callback_data'] for row in kb['inline_keyboard'] for btn in row] == ['take:a', 'drop:a', 'why:a']


def test_take_and_drop_buttons(b, stores):
    bb, _ = b
    disputed(stores, 'a')
    disputed(stores, 'c')
    bb.handle_update(cb('take:a'))
    bb.handle_update(cb('drop:c'))
    st = {r['nick']: r['status'] for r in stores.main.execute('SELECT nick, status FROM contacts').fetchall()}
    assert st == {'a': 'к_выгрузке'}
    assert len(bb.tg.edits) == 2


def test_drop_with_reason_flow(b, stores):
    bb, _ = b
    disputed(stores, 'a')
    bb.handle_update(cb('why:a'))
    assert bb.tg.sent[-1][3] is True  # force_reply
    reply = msg('лента дёшево выглядит')
    reply['message']['reply_to_message'] = {'message_id': len(bb.tg.sent)}
    bb.handle_update(reply)
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick = 'a'").fetchone()
    assert rej['reason'] == 'лента дёшево выглядит'
    assert settings.get(stores.main, 'await_reason') == ''


def test_double_tap_on_decided_card(b, stores):
    bb, _ = b
    disputed(stores)
    bb.handle_update(cb('drop:a'))
    bb.handle_update(cb('drop:a'))
    assert bb.tg.answers[-1] == 'Уже решено'


def test_alerts_sent_once(b, stores):
    bb, _ = b
    alerts.raise_alert(stores.archive, 'checkpoint', 'ig3: нужен вход')
    assert bb.push_alerts() == 1
    assert bb.push_alerts() == 0
    assert 'ig3' in last_text(bb)


def test_summary_once_a_day_at_time(b, stores):
    bb, _ = b
    assert bb.maybe_summary(datetime(2026, 9, 28, 8, 59)) is False
    assert bb.maybe_summary(datetime(2026, 9, 28, 9, 0)) is True
    assert bb.maybe_summary(datetime(2026, 9, 28, 12, 0)) is False
    assert 'Ждут выгрузки' in last_text(bb)


def test_scheduled_export(b, stores):
    bb, sheet = b
    settings.put(stores.main, 'quota:МСК', '5')
    settings.put(stores.main, 'export_time', '18:00')
    stores.main.execute("INSERT INTO contacts (nick, status, department, level) VALUES ('x', 'к_выгрузке', 'МСК', 'PREMIUM')")
    assert bb.maybe_export(datetime(2026, 9, 28, 18, 1)) is True
    assert len(sheet.appended) == 1
    assert 'Выгружено' in last_text(bb)


def test_export_now_command(b, stores):
    bb, sheet = b
    settings.put(stores.main, 'quota:МСК', '5')
    stores.main.execute("INSERT INTO contacts (nick, status, department, level) VALUES ('x', 'к_выгрузке', 'МСК', 'PREMIUM')")
    bb.handle_update(msg('/export_now'))
    assert len(sheet.appended) == 1


def test_role_command_turns_role_on(b, stores):
    bb, _ = b
    bb.handle_update(msg('/role on restorator'))
    assert 'restorator' in [p['code'] for p in roles.load_enabled(stores.main)]


def test_tick_survives_telegram_failure(b, stores):
    bb, _ = b
    disputed(stores)
    bb.tg.fail = True
    bb.tick(datetime(2026, 9, 28, 10, 0))  # не бросает
    bb.tg.fail = False
    assert bb.push_cards() == 1


def test_scheduled_export_with_nothing_to_send_messages_once(b, stores):
    """C1: без квот или без контактов — одно сообщение в день, а не каждые 25 секунд."""
    bb, sheet = b
    settings.put(stores.main, 'export_time', '18:00')
    assert bb.maybe_export(datetime(2026, 9, 28, 18, 1)) is True
    assert bb.maybe_export(datetime(2026, 9, 28, 18, 2)) is False
    assert bb.maybe_export(datetime(2026, 9, 28, 23, 0)) is False
    assert len(bb.tg.sent) == 1 and sheet.appended == []


def test_scheduled_export_failure_reported_once(stores):
    roles.ensure_defaults(stores.main)

    def broken():
        raise RuntimeError('не задан settings.sheet_key')
    bb = bot.Bot(stores, FakeTg(), OWNER, broken)
    settings.put(stores.main, 'export_time', '18:00')
    settings.put(stores.main, 'quota:МСК', '5')
    stores.main.execute("INSERT INTO contacts (nick, status, department, level) VALUES ('x', 'к_выгрузке', 'МСК', 'PREMIUM')")
    bb.maybe_export(datetime(2026, 9, 28, 18, 1))
    bb.maybe_export(datetime(2026, 9, 28, 18, 2))
    assert len(bb.tg.sent) == 1 and 'не удалась' in bb.tg.sent[0][1]


def test_stale_reason_prompt_does_not_reject_later(b, stores):
    """I1: передумал, отправил команду, через день написал текст — контакт жив."""
    bb, _ = b
    disputed(stores, 'a')
    bb.handle_update(cb('why:a'))
    bb.handle_update(msg('/status'))
    bb.handle_update(msg('привет, как дела'))
    assert stores.main.execute("SELECT status FROM contacts WHERE nick='a'").fetchone()['status'] == 'спорный'


def test_reason_accepted_only_as_reply_to_prompt(b, stores):
    bb, _ = b
    disputed(stores, 'a')
    bb.handle_update(cb('why:a'))
    prompt_id = len(bb.tg.sent)  # FakeTg нумерует сообщения по порядку
    loose = msg('просто текст')
    bb.handle_update(loose)
    assert stores.main.execute("SELECT count(*) AS c FROM contacts WHERE nick='a'").fetchone()['c'] == 1
    reply = msg('/лента дёшево')  # причина может начинаться со слэша
    reply['message']['reply_to_message'] = {'message_id': prompt_id}
    bb.handle_update(reply)
    assert stores.archive.execute("SELECT reason FROM rejected WHERE nick='a'").fetchone()['reason'] == '/лента дёшево'


def test_one_broken_card_does_not_block_queue(b, stores):
    """I3: Telegram отказал в фото одной карточки — остальные уходят, битая — текстом."""
    bb, _ = b
    disputed(stores, 'bad')
    disputed(stores, 'good')
    real = bb.tg.send_photo

    def flaky(chat_id, path, caption, reply_markup=None):
        if '@bad' in caption:
            raise telegram.TelegramError('IMAGE_PROCESS_FAILED')
        return real(chat_id, path, caption, reply_markup)
    bb.tg.send_photo = flaky
    assert bb.push_cards() == 2
    assert any('@bad' in s[1] for s in bb.tg.sent)
    assert any('@good' in p[2] for p in bb.tg.photos)
    assert bb.push_cards() == 0


def test_hiker_key_reply_is_honest_when_delete_fails(b, stores):
    """I5: не удалось удалить сообщение с ключом — так и говорим."""
    bb, _ = b
    bb.tg.delete_message = lambda chat, mid: False
    bb.handle_update(msg('/hiker_key SECRET'))
    assert 'удалите его вручную' in last_text(bb)


def test_serve_reconnects_after_database_outage(stores, pg_urls):
    """I2: обрыв базы не роняет процесс бота."""
    import psycopg
    from leadgen.core import db
    opened = []

    def make():
        opened.append(1)
        return db.open_stores(pg_urls['main'], pg_urls['archive'])

    class Tg(FakeTg):
        calls = 0

        def get_updates(self, offset=None, timeout=25):
            Tg.calls += 1
            if Tg.calls == 1:
                raise psycopg.OperationalError('server closed the connection')
            return []
    stop = __import__('threading').Event()
    bot.serve(make, Tg(), OWNER, lambda stores_: (lambda: FakeSheet()), stop,
              sleep=lambda s: None, max_loops=3)
    assert len(opened) >= 2


def test_make_sheet_prefers_apps_script(stores, monkeypatch):
    from leadgen.ops import sheet as sheet_mod
    monkeypatch.setenv('SHEETS_WEBAPP_SECRET', 'S')
    settings.put(stores.main, 'sheet_webapp_url', 'https://script.google.com/macros/s/X/exec')
    assert isinstance(bot.make_sheet(stores.main), sheet_mod.AppsScriptSheet)


def test_make_sheet_without_any_config_explains(stores, monkeypatch):
    monkeypatch.delenv('SHEETS_WEBAPP_SECRET', raising=False)
    with pytest.raises(RuntimeError) as e:
        bot.make_sheet(stores.main)
    assert '/sheet' in str(e.value)


def test_sheet_command_sets_webapp_url(b, stores):
    bb, _ = b
    bb.handle_update(msg('/sheet https://script.google.com/macros/s/ABC/exec'))
    assert settings.get(stores.main, 'sheet_webapp_url') == 'https://script.google.com/macros/s/ABC/exec'
    bb.handle_update(msg('/sheet http://evil.example/exec'))
    assert 'script.google.com' in last_text(bb)
    assert settings.get(stores.main, 'sheet_webapp_url') == 'https://script.google.com/macros/s/ABC/exec'


def disputed_no_dep(stores, nick='nodep'):
    stores.main.execute(
        "INSERT INTO contacts (nick, status, venue, role, bio, reason, cap_reason, grid_path) "
        "VALUES (%s, 'спорный', '', 'Шеф Повар', 'Chef', 'нет заведения', 'город не определён', '/none.jpg')", (nick,))


def test_card_without_department_offers_department_buttons(b, stores):
    bb, _ = b
    disputed_no_dep(stores)
    bb.push_cards()
    data = [btn['callback_data'] for row in bb.tg.photos[0][3]['inline_keyboard'] for btn in row]
    assert data == ['dep:МСК:nodep', 'dep:НСК:nodep', 'dep:СПБ:nodep', 'geo:nodep', 'drop:nodep', 'why:nodep']
    assert all(len(d.encode()) <= 64 for d in data)
    assert 'укажите отдел' in bb.tg.photos[0][2]


def test_department_button_takes_contact_with_department(b, stores):
    bb, _ = b
    disputed_no_dep(stores)
    bb.handle_update(cb('dep:НСК:nodep'))
    row = stores.main.execute("SELECT status, department FROM contacts WHERE nick='nodep'").fetchone()
    assert (row['status'], row['department']) == ('к_выгрузке', 'НСК')


def test_out_of_geography_button_rejects(b, stores):
    bb, _ = b
    disputed_no_dep(stores)
    bb.handle_update(cb('geo:nodep'))
    assert stores.archive.execute("SELECT reason FROM rejected WHERE nick='nodep'").fetchone()['reason'] == 'вне географии'


def test_old_take_button_on_contact_without_department_asks_department(b, stores):
    bb, _ = b
    disputed_no_dep(stores)
    bb.handle_update(cb('take:nodep'))
    assert stores.main.execute("SELECT status FROM contacts WHERE nick='nodep'").fetchone()['status'] == 'спорный'
    kb = bb.tg.sent[-1][2]
    assert kb['inline_keyboard'][0][0]['callback_data'] == 'dep:МСК:nodep'


# --- Проверка отказов: карточки отказов с «верно» / «берём»

def rejected(stores, nick='r1', reason='не активен больше 183 дней', stage='правила'):
    from leadgen.core import contacts
    contacts.reject(stores, nick, reason, stage, pk='5', venue='Fiorentina', city='Москва',
                    bio='Шеф-повар Fiorentina', grid_path='/nonexistent.jpg')


def test_reject_cards_only_when_review_is_on_and_only_new(b, stores):
    from leadgen.qualify import review
    bb, _ = b
    rejected(stores, 'old_one')
    assert bb.push_reject_cards() == 0
    review.set_rejects(stores, True)
    rejected(stores, 'r1')
    rejected(stores, 'guest', reason='нет роли в шапке', stage='роль')
    assert bb.push_reject_cards() == 1
    chat, path, caption, kb = bb.tg.photos[-1]
    assert path == '/nonexistent.jpg' and '@r1' in caption and 'не активен больше 183 дней' in caption
    assert 'Шеф-повар Fiorentina' in caption
    # заведение и город у отказа — откуда пришёл профиль, а не где человек работает
    assert 'Найден через: Fiorentina (Москва)' in caption
    datas = [btn['callback_data'] for row in kb['inline_keyboard'] for btn in row]
    assert datas == ['rok:r1', 'rback:r1', 'rwhy:r1']
    assert bb.push_reject_cards() == 0


def test_reject_card_falls_back_to_text_without_grid(b, stores):
    from leadgen.core import contacts
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    contacts.reject(stores, 'r2', 'заведение ниже планки', 'правила', pk='6', bio='Су-шеф ресторана «Волна»')
    assert bb.push_reject_cards() == 1
    assert '@r2' in bb.tg.sent[-1][1]


def test_agree_button_confirms_reject(b, stores):
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    rejected(stores)
    bb.handle_update(cb('rok:r1'))
    assert stores.archive.execute("SELECT review FROM rejected WHERE nick='r1'").fetchone()['review'] == 'верно'
    assert '👍' in bb.tg.edits[-1][1]


def test_take_back_button_revets_profile(b, stores):
    from leadgen.core import queue as wq
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    rejected(stores)
    bb.handle_update(cb('rback:r1'))
    assert wq.claim_next(stores.archive, ['vet'], 't')['payload']['approved'] is True
    assert '↩️' in bb.tg.edits[-1][1]
    bb.handle_update(cb('rback:r1'))
    assert bb.tg.answers[-1] == 'Уже решено'


def test_take_back_with_reason_records_why(b, stores):
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    rejected(stores)
    bb.handle_update(cb('rwhy:r1'))
    reply = msg('сезонный ресторан, летом постит')
    reply['message']['reply_to_message'] = {'message_id': len(bb.tg.sent)}
    bb.handle_update(reply)
    v = stores.main.execute('SELECT verdict, reason FROM verdicts').fetchone()
    assert v['verdict'] == 'accept' and 'сезонный ресторан' in v['reason']
    assert stores.archive.execute("SELECT count(*) AS c FROM rejected").fetchone()['c'] == 0


def test_rejects_command_switches_review(b, stores):
    from leadgen.qualify import review
    bb, _ = b
    bb.handle_update(msg('/rejects on'))
    assert review.rejects_on(stores) is True
    bb.handle_update(msg('/rejects off'))
    assert review.rejects_on(stores) is False


def test_text_card_is_marked_by_editing_text(b, stores):
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    rejected(stores)
    update = cb('rok:r1')
    update['callback_query']['message'] = {'message_id': 7, 'chat': {'id': OWNER}, 'text': 'карточка текстом'}
    bb.handle_update(update)
    assert bb.tg.edits == [] and '👍' in bb.tg.text_edits[-1][1]


def test_tick_lets_lost_database_through_for_reconnect(b, stores):
    """29.09.2026: соединение с Supabase закрылось, tick глушил ошибку в каждом шаге —
    бот 10 часов писал «the connection is closed» и ничего не присылал."""
    import psycopg
    bb, _ = b

    def lost():
        raise psycopg.OperationalError('the connection is closed')
    bb.push_cards = lost
    with pytest.raises(psycopg.OperationalError):
        bb.tick(datetime(2026, 9, 29, 21, 0))


def test_non_horeca_rejects_are_not_sent_and_not_retried(b, stores):
    from leadgen.core import contacts
    from leadgen.qualify import review
    bb, _ = b
    review.set_rejects(stores, True)
    contacts.reject(stores, 'footballer', 'должности нет в шапке профиля', 'правила', pk='8',
                    bio='Нападающий ФК «Урал» ⚽️')
    rejected(stores, 'r1')
    assert bb.push_reject_cards() == 1
    assert [p[2].split('\n')[0] for p in bb.tg.photos] == ['🚫 Отклонён: @r1']
    assert bb.push_reject_cards() == 0
    skip = stores.archive.execute("SELECT review FROM rejected WHERE nick='footballer'").fetchone()
    assert skip['review'] == 'не_показан'


# --- Принятые системой (01.10.2026): «сделай чтобы я видел автоматически принятых»

def accepted(stores, nick='auto1'):
    stores.main.execute(
        "INSERT INTO contacts (nick, status, venue, level, department, role, bio, city, grid_path, reason) "
        "VALUES (%s, 'к_выгрузке', 'Fiorentina', 'PREMIUM', 'МСК', 'Шеф Повар', 'Шеф-повар', 'Москва', "
        "'/nonexistent.jpg', 'подачи уровня эталона')", (nick,))


def test_auto_accepted_card_is_sent_once(b, stores):
    bb, _ = b
    accepted(stores)
    stores.main.execute("INSERT INTO contacts (nick, status, tg_card_at) VALUES ('by_owner', 'к_выгрузке', now())")
    assert bb.push_accepted_cards() == 1
    chat, path, caption, kb = bb.tg.photos[-1]
    assert caption.startswith('✅ Принят автоматически: @auto1') and 'Fiorentina' in caption
    datas = [btn['callback_data'] for row in kb['inline_keyboard'] for btn in row]
    assert datas == ['aok:auto1', 'adrop:auto1', 'awhy:auto1']
    assert bb.push_accepted_cards() == 0
    assert stores.main.execute("SELECT status FROM contacts WHERE nick='auto1'").fetchone()['status'] == 'к_выгрузке'


def test_ok_keeps_contact_for_export(b, stores):
    bb, _ = b
    accepted(stores)
    bb.push_accepted_cards()
    bb.handle_update(cb('aok:auto1'))
    assert stores.main.execute("SELECT status FROM contacts WHERE nick='auto1'").fetchone()['status'] == 'к_выгрузке'
    v = stores.main.execute('SELECT verdict, by_human FROM verdicts').fetchone()
    assert v['verdict'] == 'accept' and v['by_human']


def test_drop_removes_auto_accepted_from_export(b, stores):
    bb, _ = b
    accepted(stores)
    bb.push_accepted_cards()
    bb.handle_update(cb('adrop:auto1'))
    assert stores.main.execute("SELECT count(*) AS c FROM contacts WHERE nick='auto1'").fetchone()['c'] == 0
    rej = stores.archive.execute("SELECT stage FROM rejected WHERE nick='auto1'").fetchone()
    assert rej['stage'] == 'человек'


def test_drop_with_reason_for_auto_accepted(b, stores):
    bb, _ = b
    accepted(stores)
    bb.push_accepted_cards()
    bb.handle_update(cb('awhy:auto1'))
    reply = msg('шеф столовой, не наш уровень')
    reply['message']['reply_to_message'] = {'message_id': len(bb.tg.sent)}
    bb.handle_update(reply)
    rej = stores.archive.execute("SELECT reason FROM rejected WHERE nick='auto1'").fetchone()
    assert rej['reason'] == 'шеф столовой, не наш уровень'


def test_drop_after_export_says_already_in_sheet(b, stores):
    bb, _ = b
    accepted(stores)
    bb.push_accepted_cards()
    stores.main.execute("UPDATE contacts SET status = 'выгружен' WHERE nick='auto1'")
    bb.handle_update(cb('adrop:auto1'))
    assert 'Уже в таблице' in bb.tg.answers[-1]



def test_vpn_link_is_deleted_and_handed_to_background(stores):
    """05.10.2026: ссылку подписки можно прислать и командой, и просто сообщением."""
    tg, started = FakeTg(), []
    b = bot.Bot(stores, tg, OWNER, lambda: None, spawn_vpn=started.append)
    b.handle_update({'message': {'message_id': 7, 'from': {'id': OWNER}, 'text': 'https://vpn.example/s/AbC'}})
    b.handle_update({'message': {'message_id': 8, 'from': {'id': OWNER}, 'text': '/vpn https://sub.example/x'}})
    assert started == ['https://vpn.example/s/AbC', 'https://sub.example/x']
    assert tg.deleted == [7, 8]
    assert 'подключаю' in tg.sent[0][1] and 'AbC' not in tg.sent[0][1]
    assert settings.get(stores.main, 'vpn_sub') is None  # ссылка в базу не пишется


def test_vpn_link_from_stranger_is_ignored(stores):
    tg, started = FakeTg(), []
    b = bot.Bot(stores, tg, OWNER, lambda: None, spawn_vpn=started.append)
    b.handle_update({'message': {'message_id': 7, 'from': {'id': 999}, 'text': 'https://vpn.example/s/AbC'}})
    assert started == [] and tg.sent == []


def test_export_that_only_reconciles_says_no_new_rows(b, stores):
    """12.10.2026: выгрузка 07.10 записала строки, но таблица ответила дольше минуты — статусы не обновились.
    Сегодня /export_now нашёл их в таблице и ответил «Выгружено: 0…» — заказчик понял как «выгружено»."""
    bb, sheet = b
    sheet.existing_nicks = lambda: {'x'}
    settings.put(stores.main, 'quota:МСК', '5')
    stores.main.execute("INSERT INTO contacts (nick, status, department, level) VALUES ('x', 'к_выгрузке', 'МСК', 'PREMIUM')")
    bb.handle_update(msg('/export_now'))
    text = last_text(bb)
    assert 'Новых строк в таблице нет' in text and '1' in text
    assert sheet.appended == []


def test_sheet_waits_long_enough_for_apps_script():
    from leadgen.ops import sheet
    import inspect
    assert inspect.signature(sheet.AppsScriptSheet.__init__).parameters['timeout'].default >= 300

import json
import subprocess

import pytest

from leadgen.core import db, settings
from leadgen.llm import grader


def add(stores, nick, status='сетка_готова', bio='Шеф-повар @x', needs_strong=False,
        grid_path='/tmp/none.jpg'):
    stores.main.execute(
        'INSERT INTO contacts (nick, status, bio, captions, grid_path, needs_strong_profile, '
        "role_code, role, venue, level, department) VALUES (%s, %s, %s, '[\"Подача\"]', %s, %s, "
        "'chef', 'Шеф Повар', 'Fiorentina', 'PREMIUM', 'МСК')",
        (nick, status, bio, grid_path, needs_strong))


def status(stores, nick):
    row = stores.main.execute('SELECT status FROM contacts WHERE nick = %s', (nick,)).fetchone()
    return row['status'] if row else None


@pytest.fixture
def fake_claude(monkeypatch):
    calls = []

    def install(responder):
        def fake(prompt, schema, model, system_prompt, tools='', add_dir=None, timeout=900):
            calls.append({'prompt': prompt, 'model': model, 'tools': tools, 'system': system_prompt})
            return responder(prompt)
        monkeypatch.setattr(grader, 'run_claude', fake)
        return calls
    return install


def test_text_stage_passes_and_rejects(stores, fake_claude):
    add(stores, 'good_chef')
    add(stores, 'consultant')
    calls = fake_claude(lambda p: {'results': [
        {'nick': 'good_chef', 'verdict': 'pass', 'reason': 'шеф ресторана'},
        {'nick': 'consultant', 'verdict': 'reject', 'reason': 'консультант без своего заведения'}]})
    out = grader.text_stage(stores)
    assert out == {'calls': 1, 'passed': 1, 'rejected': 1}
    assert calls[0]['model'] == 'haiku' and calls[0]['tools'] == ''
    assert status(stores, 'good_chef') == 'текст_ок'
    assert status(stores, 'consultant') is None
    rej = stores.archive.execute("SELECT reason, stage FROM rejected WHERE nick = 'consultant'").fetchone()
    assert (rej['reason'], rej['stage']) == ('консультант без своего заведения', 'оценка_текст')
    v = stores.main.execute("SELECT stage, verdict, model FROM verdicts WHERE nick = 'consultant'").fetchone()
    assert (v['stage'], v['verdict'], v['model']) == ('текст', 'reject', 'haiku')


def test_unsure_goes_to_vision_not_to_rejected(stores, fake_claude):
    add(stores, 'maybe')
    fake_claude(lambda p: {'results': [{'nick': 'maybe', 'verdict': 'unsure', 'reason': '?'}]})
    grader.text_stage(stores)
    assert status(stores, 'maybe') == 'текст_ок'


def test_missing_and_foreign_nicks(stores, fake_claude):
    add(stores, 'a')
    add(stores, 'b')
    fake_claude(lambda p: {'results': [{'nick': 'a', 'verdict': 'pass', 'reason': ''},
                                       {'nick': 'intruder', 'verdict': 'reject', 'reason': ''}]})
    grader.text_stage(stores)
    assert status(stores, 'a') == 'текст_ок'
    assert status(stores, 'b') == 'сетка_готова'
    assert stores.archive.execute("SELECT count(*) AS c FROM rejected").fetchone()['c'] == 0


def test_claude_error_stops_and_loses_nothing(stores, fake_claude):
    add(stores, 'a')

    def boom(p):
        raise grader.GraderError('usage limit reached')
    fake_claude(boom)
    with pytest.raises(grader.GraderError):
        grader.text_stage(stores)
    assert status(stores, 'a') == 'сетка_готова'
    log = stores.archive.execute('SELECT ok, error FROM grader_log').fetchone()
    assert log['ok'] is False and 'usage limit' in log['error']


def test_daily_cap(stores, fake_claude):
    settings.put(stores.main, 'grader_daily_calls', '1')
    for i in range(30):
        add(stores, 'n%02d' % i)
    fake_claude(lambda p: {'results': []})
    out = grader.text_stage(stores, batch_size=25)
    assert out['calls'] == 1
    assert grader.calls_left(stores) == 0


def test_prompt_contains_bio_and_captions(stores, fake_claude):
    add(stores, 'a', bio='Бренд-шеф @fiorentina_nn')
    calls = fake_claude(lambda p: {'results': []})
    grader.text_stage(stores)
    assert 'Бренд-шеф @fiorentina_nn' in calls[0]['prompt'] and 'Подача' in calls[0]['prompt']


def test_second_concurrent_run_exits_immediately(stores, pg_urls, fake_claude):
    add(stores, 'a')
    fake_claude(lambda p: {'results': [{'nick': 'a', 'verdict': 'pass', 'reason': ''}]})
    other = db.open_stores(pg_urls['main'], pg_urls['archive'])
    other.archive.execute('SELECT pg_advisory_lock(%s)', (grader.LOCK_KEY,))
    try:
        assert grader.run(stores) == {'skipped': 'уже идёт другой прогон'}
        assert status(stores, 'a') == 'сетка_готова'
    finally:
        other.archive.execute('SELECT pg_advisory_unlock(%s)', (grader.LOCK_KEY,))
        other.close()


def test_run_claude_parses_structured_output(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout, cwd=None, input=None):
        assert cmd[:2] == ['claude', '-p'] and '--bare' not in cmd
        assert input == 'p' and 'p' != cmd[-1]
        assert cmd[cmd.index('--model') + 1] == 'haiku'
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {'is_error': False, 'structured_output': {'results': []}, 'total_cost_usd': 0.01}), '')
    monkeypatch.setattr(grader.subprocess, 'run', fake_run)
    assert grader.run_claude('p', {'type': 'object'}, 'haiku', 'sys') == {'results': []}


def test_run_claude_raises_on_error_result(monkeypatch):
    monkeypatch.setattr(grader.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, json.dumps({'is_error': True, 'result': 'Not logged in'}), ''))
    with pytest.raises(grader.GraderError):
        grader.run_claude('p', {}, 'haiku', 'sys')


def vis(nick, verdict, conf, strong=False, reason='r'):
    return {'nick': nick, 'verdict': verdict, 'confidence': conf, 'strong_profile': strong,
            'reason': reason}


def test_vision_stage_applies_threshold_and_strong_profile(stores, fake_claude, tmp_path, monkeypatch):
    from leadgen.qualify import grid
    monkeypatch.setattr(grid, 'GRIDS_DIR', str(tmp_path))
    for nick, strong in (('sure_ok', False), ('unsure_ok', False), ('sure_no', False),
                         ('weak_novenue', True), ('star_novenue', True), ('meh', False)):
        add(stores, nick, status='текст_ок', needs_strong=strong)
    calls = fake_claude(lambda p: {'results': [
        vis('sure_ok', 'accept', 0.9), vis('unsure_ok', 'accept', 0.5),
        vis('sure_no', 'reject', 0.95, reason='инфографика вместо подач'),
        vis('weak_novenue', 'accept', 0.9, strong=False),
        vis('star_novenue', 'accept', 0.9, strong=True), vis('meh', 'unsure', 0.9)]})
    out = grader.vision_stage(stores, batch_size=6)
    assert calls[0]['model'] == 'sonnet' and calls[0]['tools'] == 'Read'
    assert status(stores, 'sure_ok') == 'к_выгрузке'
    assert status(stores, 'star_novenue') == 'к_выгрузке'
    assert status(stores, 'unsure_ok') == 'спорный'
    assert status(stores, 'meh') == 'спорный'
    assert status(stores, 'sure_no') is None
    assert status(stores, 'weak_novenue') == 'спорный'
    reasons = {r['nick']: r['reason'] for r in stores.archive.execute('SELECT nick, reason FROM rejected').fetchall()}
    assert reasons['sure_no'] == 'инфографика вместо подач'
    assert out == {'calls': 1, 'accepted': 2, 'rejected': 1, 'disputed': 3}


def test_vision_threshold_is_a_setting(stores, fake_claude):
    settings.put(stores.main, 'grade_confidence_min', '0.95')
    add(stores, 'a', status='текст_ок')
    fake_claude(lambda p: {'results': [vis('a', 'accept', 0.9)]})
    grader.vision_stage(stores)
    assert status(stores, 'a') == 'спорный'


def test_vision_prompt_has_grid_paths_refs_and_human_examples(stores, fake_claude, tmp_path, monkeypatch):
    from leadgen.qualify import grid
    monkeypatch.setattr(grid, 'GRIDS_DIR', str(tmp_path))
    ref = tmp_path / '_ref'
    ref.mkdir()
    (ref / 'chef_amber_.jpg').write_bytes(b'x')
    stores.main.execute("INSERT INTO verdicts (nick, stage, verdict, reason, by_human) "
                        "VALUES ('sable', 'человек', 'reject', 'профиль выглядит дёшево', true)")
    add(stores, 'a', status='текст_ок', grid_path=str(tmp_path / 'a.jpg'))
    calls = fake_claude(lambda p: {'results': []})
    grader.vision_stage(stores)
    assert str(tmp_path / 'a.jpg') in calls[0]['prompt']
    assert str(ref / 'chef_amber_.jpg') in calls[0]['prompt']
    assert 'sable' in calls[0]['system'] and 'профиль выглядит дёшево' in calls[0]['system']


def test_run_does_text_then_vision(stores, fake_claude):
    add(stores, 'a')

    def respond(prompt):
        if 'подписи' in prompt:
            return {'results': [{'nick': 'a', 'verdict': 'pass', 'reason': ''}]}
        return {'results': [vis('a', 'accept', 0.9)]}
    fake_claude(respond)
    out = grader.run(stores)
    assert out['текст']['passed'] == 1 and out['сетка']['accepted'] == 1
    assert status(stores, 'a') == 'к_выгрузке'


def test_build_refs_composes_reference_grids(stores, tmp_path, monkeypatch):
    import io
    from PIL import Image
    from leadgen.qualify import grid
    from leadgen.access.transport import OK
    monkeypatch.setattr(grid, 'GRIDS_DIR', str(tmp_path))

    class T:
        def pk_by_nick(self, nick):
            return '1', OK

        def user_feed(self, pk):
            return [{'taken_at': 1, 'code': 'c', 'caption': '', 'thumb': 'u'}] * 12, OK

        def download(self, url):
            buf = io.BytesIO()
            Image.new('RGB', (100, 100), (0, 0, 0)).save(buf, 'JPEG')
            return buf.getvalue(), OK
    paths = grader.build_refs(stores, T(), nicks=('chef_amber_',))
    assert paths == [str(tmp_path / '_ref' / 'chef_amber_.jpg')]


def test_run_claude_sends_prompt_on_stdin_and_wraps_os_errors(monkeypatch):
    """I8: 25 профилей — до 120 КБ, это за пределом одного аргумента Linux."""
    big = 'ы' * 200000
    seen = {}

    def fake_run(cmd, capture_output, text, timeout, cwd=None, input=None):
        seen['input'], seen['argv'] = input, sum(len(c) for c in cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({'is_error': False, 'structured_output': {'results': []}}), '')
    monkeypatch.setattr(grader.subprocess, 'run', fake_run)
    grader.run_claude(big, {}, 'haiku', 'sys')
    assert seen['input'] == big and seen['argv'] < 10000

    def missing(*a, **k):
        raise FileNotFoundError('claude')
    monkeypatch.setattr(grader.subprocess, 'run', missing)
    with pytest.raises(grader.GraderError):
        grader.run_claude('p', {}, 'haiku', 'sys')


def test_run_claude_rejects_non_object_structured_output(monkeypatch):
    monkeypatch.setattr(grader.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, json.dumps({'is_error': False, 'structured_output': None}), ''))
    with pytest.raises(grader.GraderError):
        grader.run_claude('p', {}, 'haiku', 'sys')


def test_sparse_grid_reject_goes_to_human(stores, fake_claude):
    """I2: по сетке из двух клеток уверенно отклонять нельзя."""
    add(stores, 'sparse', status='текст_ок')
    stores.main.execute("UPDATE contacts SET grid_tiles = 2 WHERE nick = 'sparse'")
    fake_claude(lambda p: {'results': [vis('sparse', 'reject', 0.95)]})
    grader.vision_stage(stores)
    assert status(stores, 'sparse') == 'спорный'


def test_cap_reason_accept_goes_to_human(stores, fake_claude):
    add(stores, 'capped', status='текст_ок')
    stores.main.execute("UPDATE contacts SET cap_reason = 'город не определён' WHERE nick = 'capped'")
    fake_claude(lambda p: {'results': [vis('capped', 'accept', 0.99, strong=True)]})
    grader.vision_stage(stores)
    assert status(stores, 'capped') == 'спорный'


def test_string_confidence_does_not_crash(stores, fake_claude):
    add(stores, 'a', status='текст_ок')
    fake_claude(lambda p: {'results': [dict(vis('a', 'accept', 0.9), confidence='high')]})
    grader.vision_stage(stores)
    assert status(stores, 'a') == 'спорный'


def test_vision_prompt_carries_role_requirements(stores, fake_claude):
    """I6: требования должности (кондитер — только PREMIUM) видит и модель."""
    from leadgen.qualify import roles
    roles.ensure_defaults(stores.main)
    add(stores, 'a', status='текст_ок')
    stores.main.execute("UPDATE contacts SET role_code = 'pastry_chef' WHERE nick = 'a'")
    calls = fake_claude(lambda p: {'results': []})
    grader.vision_stage(stores)
    assert roles.PASTRY_PROMPT in calls[0]['prompt']


from datetime import datetime


def test_window():
    assert grader.in_window('09:00-21:00', datetime(2026, 9, 29, 9, 0)) is True
    assert grader.in_window('09:00-21:00', datetime(2026, 9, 29, 21, 0)) is False
    assert grader.in_window('22:00-06:00', datetime(2026, 9, 29, 23, 0)) is True
    assert grader.in_window('22:00-06:00', datetime(2026, 9, 29, 12, 0)) is False


def test_loop_once_outside_window_does_not_call_claude(stores, fake_claude):
    add(stores, 'a')
    calls = fake_claude(lambda p: {'results': []})
    assert grader.loop_once(stores, datetime(2026, 9, 29, 3, 0)) is None
    assert calls == []


def test_loop_once_paused(stores, fake_claude):
    settings.put(stores.main, 'paused', '1')
    calls = fake_claude(lambda p: {'results': []})
    assert grader.loop_once(stores, datetime(2026, 9, 29, 12, 0)) is None
    assert calls == []


def test_loop_once_in_window_runs(stores, fake_claude):
    add(stores, 'a')
    fake_claude(lambda p: {'results': [{'nick': 'a', 'verdict': 'pass', 'reason': ''}]} if 'подписи' in p else {'results': []})
    out = grader.loop_once(stores, datetime(2026, 9, 29, 12, 0))
    assert out['текст']['passed'] == 1

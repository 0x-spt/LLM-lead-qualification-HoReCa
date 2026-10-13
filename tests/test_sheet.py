from datetime import date

from leadgen.ops import sheet

CONTACT = {'nick': 'birch1_cedar1.chef', 'name': 'Григорий Ким', 'department': 'НСК',
           'agent': 'Агент №2', 'crm_position': 'Шеф', 'city': 'Красноярск', 'venue': 'Fresco',
           'level': 'PREMIUM', 'role': 'Шеф Повар'}


def test_row_matches_sheet_layout_a_to_p():
    row = sheet.row_for(CONTACT, date(2026, 9, 28))
    assert len(row) == 16
    assert row == ['https://www.instagram.com/birch1_cedar1.chef/', 'Григорий', 'Ким', '',
                   '28.09.2026', 'НСК', 'Агент №2', '', '', '', 'Шеф', 'Красноярск', 'Fresco',
                   'PREMIUM', 'Шеф Повар', '']


def test_row_with_empty_name_is_allowed():
    row = sheet.row_for(dict(CONTACT, name=''), date(2026, 9, 28))
    assert row[1] == '' and row[2] == ''


def test_row_with_single_word_name():
    row = sheet.row_for(dict(CONTACT, name='Григорий'), date(2026, 9, 28))
    assert (row[1], row[2]) == ('Григорий', '')


def test_done_row():
    assert sheet.done_row(date(2026, 9, 28)) == ['Завершена обработка за 28.09.2026'] + [''] * 15


def test_nicks_from_links():
    values = ['Ссылка', 'https://www.instagram.com/birch1_cedar1.chef/', 'instagram.com/abc?igsh=1',
              '@plain_nick', '', 'Завершена обработка за 28.09.2026']
    assert sheet.nicks_from_links(values) == {'birch1_cedar1.chef', 'abc'}


def test_instagram_text_is_never_a_formula():
    """Имя или заведение, начинающиеся с =, +, -, @, не становятся формулой в CRM."""
    row = sheet.row_for(dict(CONTACT, name='=HYPERLINK Иван', venue='+7 Кухня', role='-'), date(2026, 9, 28))
    assert row[1] == "'=HYPERLINK" and row[12] == "'+7 Кухня" and row[14] == "'-"
    assert row[0].startswith('https://') and row[4] == '28.09.2026'


import json as _json

import httpx


def test_apps_script_existing_nicks_and_append():
    """Выгрузка без Google Cloud: веб-приложение Apps Script внутри самой таблицы."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == 'GET':
            assert request.url.params['secret'] == 'S'
            return httpx.Response(200, json={'ok': True, 'links': ['Ссылка', 'https://www.instagram.com/Known_One/']})
        body = _json.loads(request.content)
        assert body['secret'] == 'S' and len(body['rows'][0]) == 16
        assert body['done'][0] == 'Завершена обработка за 29.09.2026'
        return httpx.Response(200, json={'ok': True, 'added': len(body['rows'])})
    sh = sheet.AppsScriptSheet('https://script.google.com/macros/s/X/exec', 'S',
                               http_transport=httpx.MockTransport(handler))
    assert sh.existing_nicks() == {'known_one'}
    sh.append([sheet.row_for(CONTACT, date(2026, 9, 29))], date(2026, 9, 29))
    assert [r.method for r in seen] == ['GET', 'POST']


def test_apps_script_refusal_raises():
    sh = sheet.AppsScriptSheet('https://x/exec', 'bad',
                               http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'ok': False, 'error': 'forbidden'})))
    import pytest
    with pytest.raises(sheet.SheetError):
        sh.existing_nicks()


def test_apps_script_html_login_page_raises():
    """Развёрнуто не «для всех» — Google отдаёт страницу входа вместо JSON."""
    sh = sheet.AppsScriptSheet('https://x/exec', 'S',
                               http_transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<html>Sign in</html>')))
    import pytest
    with pytest.raises(sheet.SheetError):
        sh.existing_nicks()

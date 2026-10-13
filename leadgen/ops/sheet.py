"""The "AI ЛИДЫ AMOCRM" Google Sheet: the A-P layout, and a gspread client.

The layout was captured from the 2026-09-11 export: 16 columns, D/H/I/J empty
(J colored blue), memo P filled only as facts arrive, with a yellow
"Завершена обработка за" row at the end."""
import re

from leadgen.qualify.names import clean_venue_name

COLUMNS = 16
LINK = re.compile(r'instagram\.com/([A-Za-z0-9._]+)', re.I)
YELLOW = {'backgroundColor': {'red': 1.0, 'green': 1.0, 'blue': 0.0}}


def _day(day):
    return day.strftime('%d.%m.%Y')


FORMULA_START = ('=', '+', '-', '@')


def _cell(value):
    # text from Instagram must not turn into a formula in the sheet that feeds amoCRM
    value = value or ''
    return "'" + value if value.startswith(FORMULA_START) else value


def row_for(contact, day):
    parts = (contact.get('name') or '').split()
    first = parts[0] if parts else ''
    last = parts[1] if len(parts) > 1 else ''
    return ['https://www.instagram.com/%s/' % contact['nick'], _cell(first), _cell(last), '',
            _day(day), _cell(contact.get('department')), _cell(contact.get('agent')), '', '', '',
            _cell(contact.get('crm_position')), _cell(contact.get('city')),
            _cell(clean_venue_name(contact.get('venue') or '')), _cell(contact.get('level')), _cell(contact.get('role')), '']


def done_row(day):
    return ['Завершена обработка за %s' % _day(day)] + [''] * (COLUMNS - 1)


def nicks_from_links(values):
    out = set()
    for v in values:
        m = LINK.search(v or '')
        if m:
            out.add(m.group(1).rstrip('.').lower())
    return out


class GspreadSheet:
    def __init__(self, key, worksheet=None, credentials_path=None):
        import gspread
        client = gspread.service_account(filename=credentials_path) if credentials_path \
            else gspread.service_account()
        book = client.open_by_key(key)
        self._ws = book.worksheet(worksheet) if worksheet else book.sheet1

    def existing_nicks(self):
        return nicks_from_links(self._ws.col_values(1))

    def append(self, rows, day):
        result = self._ws.append_rows(rows + [done_row(day)], value_input_option='USER_ENTERED')
        rng = ((result or {}).get('updates') or {}).get('updatedRange') or ''
        m = re.search(r'!A(\d+):[A-Z]+(\d+)$', rng)
        if m:
            last = int(m.group(2))
            self._ws.format('A%d:P%d' % (last, last), YELLOW)


class SheetError(Exception):
    pass


class AppsScriptSheet:
    """The sheet via an Apps Script web app living inside it — no Google Cloud
    project or service-account keys needed (creating those is often blocked by policy).
    The script lives at docs/apps-script.md; its URL is settings.sheet_webapp_url,
    the shared secret is SHEETS_WEBAPP_SECRET in .env."""

    # 2026-10-07: writing ~105 rows — Apps Script took over a minute to respond; the rows landed, but the statuses didn't
    def __init__(self, url, secret, http_transport=None, timeout=300.0):
        import httpx
        kwargs = {'timeout': timeout, 'follow_redirects': True}
        if http_transport is not None:
            kwargs['transport'] = http_transport
        self._client, self._url, self._secret = httpx.Client(**kwargs), url, secret

    def _json(self, response):
        try:
            data = response.json()
        except ValueError:
            raise SheetError('Apps Script ответил не JSON (HTTP %d) — проверьте, что веб-приложение '
                             'развёрнуто с доступом «Все»' % response.status_code)
        if not data.get('ok'):
            raise SheetError('Apps Script отказал: %s' % data.get('error'))
        return data

    def existing_nicks(self):
        data = self._json(self._client.get(self._url, params={'secret': self._secret}))
        return nicks_from_links(data.get('links') or [])

    def append(self, rows, day):
        self._json(self._client.post(self._url, json={'secret': self._secret, 'rows': rows,
                                                        'done': done_row(day)}))

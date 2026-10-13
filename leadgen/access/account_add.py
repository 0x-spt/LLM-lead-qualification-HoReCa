"""Add an account to the pool, or switch the transport mode.

    # own account: log in by hand in Chrome, launched through ITS proxy, then
    python -m leadgen.access.account_add own --label ig3 --port 9224 --proxy http://user:pass@host:port
    # or from a session file (captured with the export command on the Mac)
    python -m leadgen.access.account_add own --label ig3 --session db/sessions/ig3.json --proxy ...
    # HikerAPI
    python -m leadgen.access.account_add hiker --key KEY
    # mode
    python -m leadgen.access.account_add mode hybrid

No passwords are entered on the user's behalf: the session is taken only
from an already logged-in browser."""
import argparse
import json
import os
import sys

from leadgen.access import accounts
from leadgen.core import db, settings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REQUIRED_COOKIES = {'sessionid', 'csrftoken', 'ds_user_id'}


def cookies_from_cdp(port):
    sys.path.insert(0, os.path.join(ROOT, 'db', 'tools'))
    import cdp  # existing client, db/tools/cdp.py
    target = cdp.find_page('instagram.com', port)
    if not target:
        raise SystemExit('нет вкладки instagram.com на порту %d — откройте Instagram и войдите' % port)
    ws = cdp.WS(target['webSocketDebuggerUrl'])
    try:
        cookies = ws.call('Network.getCookies', {'urls': ['https://www.instagram.com']})
        ua = ws.call('Runtime.evaluate', {'expression': 'navigator.userAgent', 'returnByValue': True})
    finally:
        ws.close()
    jar = {c['name']: c['value'] for c in cookies.get('cookies', [])}
    return jar, ua.get('result', {}).get('value')


def cookies_from_file(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return data['cookies'], data.get('user_agent')


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    own = sub.add_parser('own')
    own.add_argument('--label', required=True)
    own.add_argument('--port', type=int)
    own.add_argument('--session')
    own.add_argument('--proxy')
    hiker = sub.add_parser('hiker')
    hiker.add_argument('--key', required=True)
    exp = sub.add_parser('export')
    exp.add_argument('--port', type=int, required=True)
    exp.add_argument('--out', required=True)
    mode = sub.add_parser('mode')
    mode.add_argument('value', choices=accounts.MODES)
    args = parser.parse_args(argv)

    if args.cmd == 'export':
        # the session travels to the server as a file: no database needed here
        jar, ua = cookies_from_cdp(args.port)
        missing = REQUIRED_COOKIES - set(jar)
        if missing:
            raise SystemExit('сессия неполная, нет cookie: %s' % ', '.join(sorted(missing)))
        if not ua:
            raise SystemExit('не удалось прочитать User-Agent браузера — без него куки на сервере не совпадут')
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'cookies': jar, 'user_agent': ua}, f, ensure_ascii=False)
        os.chmod(args.out, 0o600)  # O_CREAT doesn't change the permissions of an already-existing file
        print('сессия сохранена в %s — перенесите на сервер и удалите отсюда' % args.out)
        return

    stores = db.open_stores()
    try:
        db.init_main(stores.main)
        db.init_archive(stores.archive)
        if args.cmd == 'own':
            if args.port:
                jar, ua = cookies_from_cdp(args.port)
            elif args.session:
                jar, ua = cookies_from_file(args.session)
            else:
                raise SystemExit('нужен --port (Chrome с Instagram) или --session (json)')
            missing = REQUIRED_COOKIES - set(jar)
            if missing:
                raise SystemExit('сессия неполная, нет cookie: %s — войдите в Instagram руками'
                                 % ', '.join(sorted(missing)))
            if not args.proxy:
                print('ВНИМАНИЕ: аккаунт без прокси. На сервере это быстрый путь к checkpoint.')
            aid = accounts.register_own(stores.archive, args.label, jar, proxy=args.proxy,
                                        user_agent=ua)
            print('аккаунт %s в пуле, id=%d' % (args.label, aid))
        elif args.cmd == 'hiker':
            aid = accounts.register_hiker(stores.archive, args.key)
            if settings.get(stores.main, 'ig_transport') is None:
                # no mode was ever chosen — default to "API as a last resort": own
                # accounts (API, then web pages) first, HikerAPI only once everything else is blocked
                settings.put(stores.main, 'ig_transport', 'hybrid')
            print('HikerAPI в пуле, id=%d. Режим сейчас: %s'
                  % (aid, accounts.transport_mode(stores.main)))
        else:
            settings.put(stores.main, 'ig_transport', args.value)
            print('режим транспорта: %s' % args.value)
    finally:
        stores.close()


if __name__ == '__main__':
    main()

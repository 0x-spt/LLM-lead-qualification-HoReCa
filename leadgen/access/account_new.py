"""Новые аккаунты Instagram одной командой: свой выход VPN, свой профиль Chrome, вход — руками.

    deploy/local/run.sh leadgen.access.account_new ig6 ig7 ig8
    deploy/local/run.sh leadgen.access.account_new ig6 --avoid 21801   # выход дал 429 на входе

Для каждого аккаунта: берётся живой свободный выход xray с IP, которого нет у других
аккаунтов; Chrome с профилем ~/chrome-<метка> запускается через этот выход и открывает
страницу входа. Человек вводит логин, пароль и код 2FA — скрипт их не видит и не хранит.
Как только в окне появилась сессия, аккаунт уходит в пул с этим же выходом, а воркеры
перезапускаются. Ждём до 30 минут.
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from leadgen.access import accounts, vpn
from leadgen.core import db
from leadgen.access.account_add import REQUIRED_COOKIES, cookies_from_cdp

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
FIRST_CDP_PORT = 9227
# shared Happ VPN with no dedicated xray exit — one more own IP, usable if no account is already on it (2026-10-05)
HAPP = 0
WAIT_SECONDS = 30 * 60
LABEL = re.compile(r'^[a-z][a-z0-9_]{1,20}$')


def _login_code(port):
    out = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '20', '-A', UA_LOGIN,
                          '-x', 'socks5h://127.0.0.1:%d' % port, 'https://www.instagram.com/accounts/login/'],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else 0


UA_LOGIN = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/129.0 Safari/537.36')


def usable_exits(ports, ip=None, login_code=None):
    """{port: IP} — None if the exit is dead or Instagram's login page doesn't return 200
    (client requirement, 2026-10-12: "no 429s on profiles")."""
    ip, login_code = ip or _exit_ip, login_code or _login_code
    with ThreadPoolExecutor(12) as ex:
        ips = dict(zip(ports, ex.map(ip, ports)))
        alive = [p for p in ports if ips[p]]
        codes = dict(zip(alive, ex.map(login_code, alive)))
    return {p: (ips[p] if codes.get(p) == 200 else None) for p in ports}


def pick_exits(exits, used_ports, used_ips, n):
    """exits: {port: exit IP or None}. Picks live, free exits, each with a distinct IP."""
    taken = set(used_ips)
    picked = []
    for port in sorted(exits):
        ip = exits[port]
        if len(picked) == n:
            break
        if ip is None or port in used_ports or ip in taken:
            continue
        picked.append(port)
        taken.add(ip)
    return picked


def share_exits(exits, counts, n):
    """n exits from the live ones (with an IP), starting from the least used."""
    live = sorted((p for p, ip in exits.items() if ip and p != HAPP), key=lambda p: (counts.get(p, 0), p))
    out = []
    for i in range(n):
        if not live:
            break
        p = min(live, key=lambda q: (counts.get(q, 0), q))
        counts[p] = counts.get(p, 0) + 1
        out.append(p)
    return out


def proxy_for(port):
    return None if port == HAPP else 'socks5://127.0.0.1:%d' % port


def free_cdp_ports(used, n):
    ports, port = [], FIRST_CDP_PORT
    while len(ports) < n:
        if port not in used:
            ports.append(port)
        port += 1
    return ports


def check_label(label, existing):
    """None if the label is fine, otherwise an error message."""
    if not LABEL.match(label):
        return 'метка %r: только латиница, цифры и _, например ig6' % label
    if label in existing:
        return 'аккаунт %s уже есть в пуле' % label
    return None


def parse_args(argv):
    """Account labels, plus exit ports that must not be used (--avoid 21801,21805)."""
    labels, avoid, relogin, it = [], set(), False, iter(argv)
    for arg in it:
        if arg == '--avoid':
            avoid |= {int(p) for p in next(it, '').split(',') if p.strip()}
        elif arg == '--share':
            continue  # handled in main(): more profiles than free subscription servers
        elif arg == '--happ':
            continue
        elif arg == '--relogin':
            relogin = True  # repeat login for already-known accounts (Instagram demanded re-verification)
        else:
            labels.append(arg)
    return labels, avoid, relogin


def fresh_session(jar, old_sessionid):
    """A login happened and the session is new: the old one, rejected by Instagram, stays in the window until re-login."""
    return logged_in(jar) and jar.get('sessionid') != old_sessionid


def logged_in(jar):
    return REQUIRED_COOKIES <= set(jar)


def _exit_ip(port):
    out = subprocess.run(['curl', '-s', '--max-time', '15', '-x', 'socks5h://127.0.0.1:%d' % port,
                          'https://api.ipify.org'], capture_output=True, text=True).stdout.strip()
    return out if re.match(r'^[0-9a-f.:]+$', out or '') else None


def _happ_ip():
    out = subprocess.run(['curl', '-s', '--max-time', '15', 'https://api.ipify.org'],
                         capture_output=True, text=True).stdout.strip()
    return out if re.match(r'^[0-9a-f.:]+$', out or '') else None


def _port_of(proxy):
    m = re.match(r'^socks5h?://127\.0\.0\.1:(\d+)$', proxy or '')
    return int(m.group(1)) if m else None


def _cdp_ports_in_use():
    ps = subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True).stdout
    return {int(p) for p in re.findall(r'--remote-debugging-port=(\d+)', ps)}


def _launch(label, cdp_port, socks_port):
    proxy = ['--proxy-server=socks5://127.0.0.1:%d' % socks_port] if socks_port != HAPP else []
    subprocess.Popen([CHROME, '--remote-debugging-port=%d' % cdp_port,
                      '--user-data-dir=%s' % os.path.expanduser('~/chrome-%s' % label)] + proxy +
                     ['--no-first-run', '--no-default-browser-check',
                      'https://www.instagram.com/accounts/login/'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _close_window(label):
    ps = subprocess.run(['ps', '-axo', 'pid,command'], capture_output=True, text=True).stdout
    for line in ps.splitlines():
        if re.search(r'user-data-dir=%s( |$)' % re.escape(os.path.expanduser('~/chrome-%s' % label)), line) \
                and 'Helper' not in line:
            subprocess.run(['kill', '-KILL', line.split()[0]])
            time.sleep(2)


def _session(cdp_port):
    try:
        return cookies_from_cdp(cdp_port)
    except (SystemExit, OSError, ValueError, KeyError):  # the window hasn't come up yet, or the tab is closed
        return {}, None


def activate(archive, label, jar, proxy, ua):
    """Login succeeded: the account goes into the pool, read-only profile access for the first day.
    Applies to both a new and a repeat login: on 2026-10-09, ig3 went straight to crawling
    subscriptions on a new IP after a checkpoint and got checkpointed again within 2 minutes.
    An anonymous reader on this exit yields it to the account."""
    aid = accounts.register_own(archive, label, jar, proxy=proxy, user_agent=ua)
    accounts.start_warmup(archive, aid)
    archive.execute("UPDATE accounts SET status = 'active', down_at = NULL, down_alerted = false "
                    'WHERE id = %s', (aid,))
    archive.execute("UPDATE accounts SET status = 'disabled' WHERE kind = 'anon' AND proxy IS NOT DISTINCT FROM %s",
                    (proxy,))
    return aid


def main(labels, avoid=frozenset(), relogin=False, share=False, happ=False):
    archive = db.connect(os.environ['ARCHIVE_DB_URL'])
    rows = archive.execute("SELECT label, proxy, cookies->>'sessionid' AS sid FROM accounts "
                           "WHERE kind = 'own' AND status <> 'заменён'").fetchall()
    existing = {r['label'] for r in rows}
    old_sid = {r['label']: r['sid'] for r in rows}
    for label in labels:
        err = (None if label in existing else 'аккаунта %s нет в пуле' % label) if relogin \
            else check_label(label, existing)
        if err:
            raise SystemExit(err)
    # old exits of accounts being re-logged-in are dead exits from a past subscription — don't keep them
    rows = [r for r in rows if r['label'] not in labels]
    if len(set(labels)) != len(labels):
        raise SystemExit('метки повторяются')

    with open(vpn.CONFIG) as f:
        live = [i['port'] for i in json.load(f)['inbounds']]
    used_ports = {p for p in (_port_of(r['proxy']) for r in rows) if p}
    print('проверяю выходы VPN…', flush=True)
    exits = usable_exits(live)
    with ThreadPoolExecutor(8) as ex:
        used_ips = set(ex.map(_exit_ip, sorted(used_ports)))
    if happ and not any(not (r['proxy'] or '').strip() for r in rows):
        exits[HAPP] = _happ_ip()
    socks = pick_exits(exits, used_ports | set(avoid), used_ips, len(labels))
    if share and len(socks) < len(labels):
        # fewer subscription servers than profiles — put the extras on the least-used ones (client request, 2026-10-05)
        counts = {}
        for r in rows:
            p = _port_of(r['proxy'])
            if p:
                counts[p] = counts.get(p, 0) + 1
        for p in socks:
            counts[p] = counts.get(p, 0) + 1
        socks += share_exits(exits, counts, len(labels) - len(socks))
    if len(socks) < len(labels):
        raise SystemExit('свободных выходов с уникальным IP: %d, а аккаунтов %d'
                         % (len(socks), len(labels)))
    cdps = free_cdp_ports(_cdp_ports_in_use(), len(labels))

    pending = {}
    for label, sp, cp in zip(labels, socks, cdps):
        if relogin:
            _close_window(label)
        _launch(label, cp, sp)
        pending[label] = (cp, sp)
        print('%s: окно Chrome открыто, выход %s (%s) — войдите в Instagram'
              % (label, exits[sp], 'общий VPN Happ' if sp == HAPP else 'порт %d' % sp), flush=True)

    added, deadline = [], time.time() + WAIT_SECONDS
    while pending and time.time() < deadline:
        time.sleep(5)
        for label, (cp, sp) in list(pending.items()):
            jar, ua = _session(cp)
            if fresh_session(jar, old_sid.get(label)) and ua:
                aid = activate(archive, label, jar, proxy_for(sp), ua)
                print('%s: вход есть — аккаунт в пуле (id=%d)' % (label, aid), flush=True)
                added.append(label)
                del pending[label]
    for label in pending:
        print('%s: за 30 минут входа не было — запустите команду для него ещё раз' % label)
    if added:
        subprocess.run(['launchctl', 'kickstart', '-k', 'gui/%d/io.leadgen.ops.workers' % os.getuid()])
        print('воркеры перезапущены: в пуле +%d (%s)' % (len(added), ', '.join(added)))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    labels, avoid, relogin = parse_args(sys.argv[1:])
    main(labels, avoid, relogin, share='--share' in sys.argv, happ='--happ' in sys.argv)

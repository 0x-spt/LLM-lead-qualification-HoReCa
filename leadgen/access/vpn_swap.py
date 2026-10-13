"""New VPN subscription from the bot (2026-10-05): "if the VPN drops, I send the config
link to the bot and you pick it up".

The bot deletes the message with the link (it contains an access key) and launches this
module in the background; the link travels through the NEW_VPN_SUB environment variable
and is never written to the database (settings are mirrored to Supabase).
Order of operations:
  1. the subscription must return servers, otherwise abort;
  2. mode: through the Happ tunnel or bypassing it (direct:, like provider B) — whichever gives more live exits;
  3. .env gets the new subscription plus any previous ones with at least one live exit; a copy of .env is kept alongside;
  4. restart xray, check exits, move accounts off dead exits onto free ones (exits);
  5. report back to the bot as a kind='vpn' alert.

    NEW_VPN_SUB=… deploy/local/run.sh leadgen.access.vpn_swap
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse

from leadgen.core import alerts, db
from leadgen.access import exits, vpn

ENV_PATH = os.path.join(vpn.ROOT, '.env')
LINE = re.compile(r'^VPN_SUB_URL=(.*)$', re.M)
TEST_BASE = 29100  # temporary xray for testing — well clear of production 21801+ and Happ's 10808
NOT_SUBS = ('instagram.com', 'google.com', 'script.google', 'docs.google', 't.me', 'telegram')


def bare(url):
    return url[len(vpn.DIRECT):] if url.startswith(vpn.DIRECT) else url


def env_urls(text):
    m = LINE.search(text)
    return [u for u in (m.group(1).replace(',', ' ').split() if m else []) if u]


def with_urls(text, urls):
    line = 'VPN_SUB_URL=' + ','.join(urls)
    return LINE.sub(lambda _: line, text) if LINE.search(text) else text.rstrip('\n') + '\n' + line + '\n'


def looks_like_subscription(text):
    text = (text or '').strip()
    if not re.match(r'^https?://\S+$', text):
        return False
    host = (urlparse(text).hostname or '').lower()
    return bool(host) and not any(n in host for n in NOT_SUBS)


def _names(servers):
    out = []
    for s in servers or []:
        remark = (s.get('remarks') or '').strip().lower()
        for name, _ in vpn._exits(s):
            if name and not any(k in name.lower() or k in remark for k in vpn.SKIP):
                out.append(name)
    return out


def dead_urls(urls, fetch, ports, live):
    """Subscriptions with no exit in live. fetch(url) → servers, or None."""
    dead = []
    for url in urls:
        servers = fetch(bare(url))
        if servers is None:
            continue  # the subscription didn't respond — doesn't mean it's dead; decide next time
        mine = [ports[n] for n in _names(servers) if n in ports]
        if not any(p in live for p in mine):
            dead.append(url)
    return dead


def pick_mode(live_count):
    """live_count(mode) → how many exits are live. On a tie, go through Happ, like the rest."""
    happ, direct = live_count('happ'), live_count('direct')
    if not happ and not direct:
        return None, 0
    return ('direct', direct) if direct > happ else ('happ', happ)


def _trial(servers, direct):
    """How many of the subscription's exits are live: a temporary xray on test ports."""
    marked = [dict(s, **{vpn.DIRECT_KEY: True}) for s in servers] if direct else servers
    cfg, _ = vpn.build(marked, {'\x00база': TEST_BASE}, direct_if=os.environ.get('VPN_DIRECT_IF', 'en0'))
    if not cfg['inbounds']:
        return 0
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(cfg, f)
    proc = subprocess.Popen([vpn.XRAY, 'run', '-c', f.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        return len(exits.live_exits(config=f.name))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        os.unlink(f.name)


def _restart_xray():
    subprocess.run(['launchctl', 'kickstart', '-k', 'gui/%d/io.leadgen.xray' % os.getuid()], check=False)
    time.sleep(20)


def apply(url, hwid, report):
    host = urlparse(url).hostname
    try:
        servers = vpn._fetch_retry(url, hwid)
    except Exception as e:  # noqa: BLE001
        return report('Подписка %s не ответила (%s) — ничего не менял.' % (host, type(e).__name__))
    if not _names(servers):
        return report('В подписке %s нет подходящих серверов — ничего не менял.' % host)
    mode, alive = pick_mode(lambda m: _trial(servers, m == 'direct'))
    if mode is None:
        return report('Подписка %s: ни один из %d серверов не отвечает ни через Happ, ни напрямую — '
                      'ничего не менял.' % (host, len(_names(servers))))
    entry = (vpn.DIRECT if mode == 'direct' else '') + url
    with open(ENV_PATH) as f:
        text = f.read()
    old = [u for u in env_urls(text) if bare(u) != url]
    ports = vpn._read(vpn.PORTS, {})
    now_live = exits.live_exits()
    # no exit alive at all — more likely xray itself is down than the subscriptions: don't drop anything
    dead = dead_urls(old, lambda u: _quiet_fetch(u, hwid), ports, now_live) if now_live else []
    keep = [u for u in old if u not in dead] + [entry]
    shutil.copy(ENV_PATH, ENV_PATH + '.bak-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    with open(ENV_PATH, 'w') as f:
        f.write(with_urls(text, keep))
    _restart_xray()
    live = exits.live_exits()
    conn = db.connect(os.environ['ARCHIVE_DB_URL'])
    moved, stuck = exits.reassign_dead(conn, live)
    lines = ['Подписка %s подключена (%s): живых выходов %d из %d.'
             % (host, 'мимо Happ' if mode == 'direct' else 'через Happ', alive, len(_names(servers))),
             'Всего живых выходов: %d.' % len(live)]
    if dead:
        lines.append('Убраны подписки без живых выходов: %s.' % ', '.join(urlparse(bare(u)).hostname for u in dead))
    if moved:
        lines.append('Пересажены: %s.' % ', '.join('%s → %s' % (lb, ip) for lb, _, ip in moved))
    if stuck:
        lines.append('Не хватило выходов: %s.' % ', '.join(stuck))
    return report('\n'.join(lines), conn)


def _quiet_fetch(url, hwid):
    try:
        return vpn._fetch_retry(url, hwid, tries=2)
    except Exception:  # noqa: BLE001
        return None


def main():
    url = os.environ.pop('NEW_VPN_SUB', '').strip()

    def report(text, conn=None):
        conn = conn or db.connect(os.environ['ARCHIVE_DB_URL'])
        alerts.raise_alert(conn, 'vpn', text)
        print(text, flush=True)
    if not url:
        return report('Нет ссылки на подписку.')
    apply(url, os.environ['VPN_SUB_HWID'], report)


if __name__ == '__main__':
    main()

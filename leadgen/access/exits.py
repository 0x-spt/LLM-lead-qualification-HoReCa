"""VPN exits for accounts (2026-10-05): which are alive, and who to move off a dead one.

Used by the bot's /vpn command (new subscription — accounts move onto its exits right
away) and by the watchdog's self-healing (an exit dies — the account doesn't wait to
be revived, it moves to a free one). The rule stays the same: one IP per account; own
accounts get an exit before readers do.
"""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from leadgen.access import vpn

# only accounts the collector itself marked dead: a single missed exit check is not
# a reason to move a working account's IP (2026-10-06: ig3 got moved for no reason)
MOVABLE = ('proxy_dead',)
# the exit stays reserved for the account in these statuses too — it might log in again
HOLDING = ('active', 'proxy_dead', 'нужен_вход', 'throttled', 'checkpoint')


def proxy_for(port):
    return 'socks5://127.0.0.1:%d' % port


def port_of(proxy):
    m = re.match(r'^socks5h?://127\.0\.0\.1:(\d+)/?$', proxy or '')
    return int(m.group(1)) if m else None


def exit_ip(port):
    out = subprocess.run(['curl', '-s', '--max-time', '15', '-x', 'socks5h://127.0.0.1:%d' % port,
                          'https://api.ipify.org'], capture_output=True, text=True).stdout.strip()
    return out if re.match(r'^[0-9a-f.:]+$', out or '') else None


def live_exits(probe=exit_ip, config=None):
    """{port: external IP} for exits from the xray config that are currently responding."""
    try:
        with open(config or vpn.CONFIG) as f:
            ports = [i['port'] for i in json.load(f)['inbounds']]
    except (OSError, ValueError, KeyError):
        return {}
    with ThreadPoolExecutor(12) as ex:
        ips = dict(zip(ports, ex.map(probe, ports)))
    return {p: ip for p, ip in ips.items() if ip}


def reassign_dead(conn, live):
    """Accounts whose exit isn't in live go onto free live exits with an unused IP.
    Returns ([(label, port, IP)], [labels that got no exit]). A reader left without
    an exit is disabled: otherwise the "exit not responding" alert would fire for it
    every 15 minutes."""
    rows = conn.execute(
        "SELECT id, label, kind, proxy, status FROM accounts WHERE kind IN ('own', 'anon') "
        "AND coalesce(proxy, '') <> '' ORDER BY kind = 'anon', id").fetchall()
    dead = [r for r in rows if r['status'] in MOVABLE and port_of(r['proxy']) not in live]
    dead_ids = {r['id'] for r in dead}
    used = {port_of(r['proxy']) for r in rows if r['status'] in HOLDING and r['id'] not in dead_ids}
    taken_ips = {live[p] for p in used if p in live}
    moved, stuck = [], []
    for r in dead:
        port = next((p for p in sorted(live) if p not in used and live[p] not in taken_ips), None)
        if port is None:
            stuck.append(r['label'])
            if r['kind'] == 'anon':
                conn.execute("UPDATE accounts SET status = 'disabled' WHERE id = %s", (r['id'],))
            continue
        used.add(port)
        taken_ips.add(live[port])
        conn.execute("UPDATE accounts SET proxy = %s, status = 'active', down_at = NULL, down_alerted = false "
                     'WHERE id = %s', (proxy_for(port), r['id']))
        conn.execute("UPDATE endpoint_health SET cooldown_until = NULL WHERE account_id = %s "
                     "AND status IN ('proxy_dead', 'net_error')", (r['id'],))
        moved.append((r['label'], port, live[port]))
    return moved, stuck

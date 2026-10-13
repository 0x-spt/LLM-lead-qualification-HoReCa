"""A dedicated internet exit for each Instagram account, via a VPN (xray) subscription.

The subscription only serves ready-made xray configs to the Happ client, so the request
goes out with its headers. Each server gets its own SOCKS port on 127.0.0.1; the port is
pinned to the server's name in secrets/xray_ports.json and never changes when the
subscription is rebuilt — otherwise an account would jump to a different IP and trigger
a security check.

    python -m leadgen.access.vpn refresh   # fetch the subscription, rebuild secrets/xray.json
    python -m leadgen.access.vpn run       # refresh (on error — keep the previous config), then launch xray
    python -m leadgen.access.vpn list      # ports, servers and exit IPs

Environment: VPN_SUB_URL (one or several comma-separated — .env is read by the shell, a
space breaks it), VPN_SUB_HWID (any stable hex string, acting as a "device" id).
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(ROOT, 'secrets', 'xray.json')
SUB_CACHE = os.path.join(ROOT, 'secrets', 'vpn_subs')  # last successful response per subscription
RETRY_PAUSE = 5
PORTS = os.path.join(ROOT, 'secrets', 'xray_ports.json')
XRAY = os.path.join(ROOT, 'deploy', 'local', 'bin', 'xray')
# 10808 is taken by Happ itself (its internal SOCKS for TUN): stay off it.
BASE_PORT = 21800
# "Auto" hops between servers, so the account's IP wouldn't stay fixed; the Russian exit
# is useless; LTE is for bypassing whitelists and has a traffic cap (client request, 2026-09-30).
SKIP = ('авто', 'россия', 'lte',
        # alternate connection methods to the same server (provider A: TLS / XHTTP / WS / gRPC) — one IP, we take TLS
        'xhttp', '— ws', 'grpc',
        # whitelist-bypass node — a Russian node shared by everyone, with a traffic cap
        'обход')
HAPP_HEADERS = {'user-agent': 'Happ/3.5.0', 'x-device-os': 'macOS', 'x-ver-os': '15.6',
                'x-device-model': 'MacBookPro18,3'}


PROXY_PROTOCOLS = ('vless', 'vmess', 'trojan', 'shadowsocks', 'hysteria')


def _proxy_outbound(server):
    for o in server.get('outbounds', []):
        if o.get('tag') == 'proxy' and o.get('protocol') in PROXY_PROTOCOLS:
            return o
    return None


def _exits(server):
    """[(name, outbound)]. Usually a single outbound, tagged "proxy" and named after the
    subscription entry. With provider B (2026-10-05) the tags are server names, and a
    load-balanced entry has several, on different hosts: each becomes its own named exit."""
    name = (server.get('remarks') or '').strip()
    out = _proxy_outbound(server)
    if out is not None:
        return [(name, out)]
    return [((o.get('tag') or '').strip(), o) for o in server.get('outbounds', [])
            if o.get('protocol') in PROXY_PROTOCOLS]


DIRECT = 'direct:'
DIRECT_KEY = '_direct'


def build(servers, ports, direct_if=None):
    """An xray config (SOCKS inbound → its own outbound to the server) and the updated name → port map."""
    ports = dict(ports)
    inbounds, outbounds, rules = [], [], []
    seen = set()
    for server in servers:
        remark = (server.get('remarks') or '').strip().lower()
        for name, out in _exits(server):
            if not name or any(s in name.lower() or s in remark for s in SKIP):
                continue
            base, n = name, 1
            while name in seen:  # same-named servers from two subscriptions — each gets its own port
                n += 1
                name = '%s #%d' % (base, n)
            seen.add(name)
            if server.get(DIRECT_KEY) and direct_if:
                out = copy.deepcopy(out)
                stream = out.setdefault('streamSettings', {})
                stream['sockopt'] = dict(stream.get('sockopt') or {}, interface=direct_if)
            _add(name, out, ports, inbounds, outbounds, rules)
    cfg = {'log': {'loglevel': 'warning'}, 'inbounds': inbounds,
           'outbounds': outbounds + [{'tag': 'direct', 'protocol': 'freedom'}],
           'routing': {'rules': rules}}
    return cfg, ports


def _add(name, out, ports, inbounds, outbounds, rules):
    if name not in ports:
        ports[name] = max(list(ports.values()) + [BASE_PORT]) + 1
    port = ports[name]
    out = copy.deepcopy(out)
    out['tag'] = 'out%d' % port
    outbounds.append(out)
    inbounds.append({'tag': 'in%d' % port, 'listen': '127.0.0.1', 'port': port,
                     'protocol': 'socks', 'settings': {'udp': False}})
    rules.append({'type': 'field', 'inboundTag': ['in%d' % port], 'outboundTag': out['tag']})


def fetch(url, hwid):
    headers = dict(HAPP_HEADERS, **{'x-hwid': hwid})
    r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    r.raise_for_status()
    servers = r.json()
    if not isinstance(servers, list):
        raise ValueError('подписка отдала не список конфигов xray')
    return servers


def _cache_path(url):
    return os.path.join(SUB_CACHE, hashlib.sha256(url.encode()).hexdigest()[:16] + '.json')


def _fetch_retry(url, hwid, tries=3):
    for attempt in range(tries):
        try:
            return fetch(url, hwid)
        except Exception:  # noqa: BLE001 — the subscription server often drops TLS ("Broken pipe")
            if attempt == tries - 1:
                raise
            time.sleep(RETRY_PAUSE)


def fetch_all(urls, hwid):
    """Several subscriptions, comma-separated. Each gets up to three attempts; if it
    doesn't respond, its last successful list (the cache) is used instead — otherwise
    its exits would simply vanish from the config. If all are silent and there's no
    cache, it's an error (run() will fall back to the previous config)."""
    servers, errors = [], []
    for url in [u for u in urls.replace(',', ' ').split() if u]:
        direct = url.startswith(DIRECT)
        url = url[len(DIRECT):] if direct else url
        try:
            got = _fetch_retry(url, hwid)
            _write(_cache_path(url), got)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            got = _read(_cache_path(url), None)
            if got is None:
                continue
        servers.extend(dict(s, **{DIRECT_KEY: True}) if direct else s for s in got)
    if not servers and errors:
        raise errors[0]
    return servers


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = os.umask(0o077)
    try:
        with open(path + '.tmp', 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(path + '.tmp', path)
    finally:
        os.umask(old)


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def refresh():
    servers = fetch_all(os.environ['VPN_SUB_URL'], os.environ['VPN_SUB_HWID'])
    cfg, ports = build(servers, _read(PORTS, {}), direct_if=os.environ.get('VPN_DIRECT_IF', 'en0'))
    if not cfg['inbounds']:
        raise ValueError('в подписке нет ни одного подходящего сервера')
    _write(PORTS, ports)
    _write(CONFIG, cfg)
    return cfg, ports


def run():
    try:
        cfg, _ = refresh()
        print('подписка обновлена: %d выходов' % len(cfg['inbounds']), flush=True)
    except Exception as e:  # subscription unreachable — run on the previous config
        if not os.path.exists(CONFIG):
            raise
        print('подписка не обновилась (%s), беру прошлый конфиг' % type(e).__name__, flush=True)
    os.execv(XRAY, [XRAY, 'run', '-c', CONFIG])


def list_exits():
    live = {i['port'] for i in _read(CONFIG, {}).get('inbounds', [])}
    for name, port in sorted(_read(PORTS, {}).items(), key=lambda kv: kv[1]):
        if port not in live:
            print(port, name, '— нет в подписке')
            continue
        r = subprocess.run(['curl', '-s', '--max-time', '15', '-x', 'socks5h://127.0.0.1:%d' % port,
                            'https://ipinfo.io/json'], capture_output=True, text=True).stdout
        try:
            j = json.loads(r)
            exit_ip = '%s %s %s' % (j.get('ip'), j.get('country'), (j.get('org') or '')[:30])
        except ValueError:
            exit_ip = 'нет ответа'
        print(port, name, exit_ip, flush=True)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    {'refresh': lambda: print('%d выходов' % len(refresh()[0]['inbounds'])),
     'run': run, 'list': list_exits}[cmd]()

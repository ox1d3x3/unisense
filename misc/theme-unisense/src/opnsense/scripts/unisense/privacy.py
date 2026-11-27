#!/usr/local/bin/python3
"""
    UniSense Privacy backend (configd: "unisense privacy ...")

    privacy.py summary              queries, blocked, 24 h series, top domains / blocked / clients (JSON)
    privacy.py log [n]              the latest queries (JSON)
    privacy.py test                 can the provider be reached? (JSON)
    privacy.py rule <allow|block|remove> <domain>
                                    AdGuard Home: change one of your own rules
    privacy.py protection <on|off>  AdGuard Home: switch filtering
    privacy.py gaming               AdGuard Home: add the gaming-safe allow rules

    One shape for both providers, so the page does not care which one runs:
      - built in: OPNsense's own Unbound statistics (scripts/unbound/stats.py,
        a DuckDB of every query), switched on by UniSense;
      - AdGuard Home: its HTTP API (/control/...), with the address and login
        saved on the Privacy page.

    Copyright (c) 2026 Ox1d3x3 — BSD 2-Clause (see LICENSE)
"""
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CONFIG_XML = os.environ.get('UNISENSE_CONFIG_XML', '/conf/config.xml')
STATS = os.environ.get('UNISENSE_UNBOUND_STATS', '/usr/local/opnsense/scripts/unbound/stats.py')
UNBOUND_XML = os.environ.get('UNISENSE_UNBOUND_MODEL',
                             '/usr/local/opnsense/mvc/app/models/OPNsense/Unbound/Unbound.xml')
PYTHON = os.environ.get('UNISENSE_PYTHON', '/usr/local/bin/python3')

# same list as models/OPNsense/Unisense/Privacy.php (a test keeps them equal)
GAMING = [
    'xboxlive.com', 'xbox.com', 'playstation.com', 'playstation.net', 'sonyentertainmentnetwork.com',
    'steampowered.com', 'steamcommunity.com', 'steamserver.net', 'steamcontent.com', 'steamstatic.com',
    'battle.net', 'blizzard.com', 'ubi.com', 'ubisoft.com', 'ubisoftconnect.com',
    'activision.com', 'callofduty.com', 'demonware.net', 'ea.com', 'epicgames.com', 'unrealengine.com',
    'riotgames.com', 'battleye.com', 'easyanticheat.net', 'nintendo.net', 'nintendo.com',
    'discord.com', 'discord.gg', 'discord.media', 'discordapp.com', 'discordapp.net',
]
DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$', re.I)


def out(obj):
    print(json.dumps(obj, separators=(',', ':')))
    return 0


def settings():
    try:
        root = ET.parse(CONFIG_XML).getroot()
    except (OSError, ET.ParseError):
        root = None
    node = root.find('OPNsense/unisense/privacy') if root is not None else None

    def text(path, default=''):
        el = node.find(path) if node is not None else None
        return (el.text or '').strip() if el is not None and el.text else default
    return {'provider': text('provider', 'off'), 'url': text('agh_url').rstrip('/'),
            'user': text('agh_user'), 'password': text('agh_pass')}


# ------------------------------------------------------------------ built in
def list_names():
    """blocklist code -> its name in Unbound's model ("[hagezi] Multi PRO - …" -> "hagezi PRO …")"""
    names = {'Custom': 'Your blocks'}
    try:
        for el in ET.parse(UNBOUND_XML).getroot().iter('type'):
            ov = el.find('OptionValues')
            if ov is not None:
                for o in ov:
                    names[o.tag] = re.sub(r'^\[(\w+)\]\s*', r'\1 ', (o.text or o.tag).strip())
                break
    except (OSError, ET.ParseError):
        pass
    return names


def stats(*args):
    r = subprocess.run([PYTHON, STATS] + list(args), capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout or 'null')
    except ValueError:
        return None


def unbound_summary():
    names = list_names()
    tot = stats('totals', '--max', '10')
    if not isinstance(tot, dict):
        return {'ok': False, 'provider': 'unbound',
                'error': 'Unbound has no statistics yet. They start with the first query after Privacy is switched on.'}
    roll = stats('rolling', '--interval', '600', '--timeperiod', '24') or {}
    clients = stats('rolling', '--interval', '600', '--timeperiod', '24', '--clients') or {}
    series = [{'t': int(float(t)), 'total': int(v.get('total', 0)), 'blocked': int(v.get('blocked', 0))}
              for t, v in sorted(roll.items(), key=lambda kv: float(kv[0]))]
    per = {}
    for _t, cl in clients.items():
        for ip, v in (cl or {}).items():
            c = per.setdefault(ip, {'client': ip, 'name': v.get('hostname') or '', 'count': 0})
            c['count'] += int(v.get('count', 0))
    total = int(tot.get('total', 0))
    blocked = int((tot.get('blocked') or {}).get('total', 0))
    return {
        'ok': True, 'provider': 'unbound', 'total': total, 'blocked': blocked,
        'pct': round(100.0 * blocked / total, 1) if total else 0.0,
        'listed': int(tot.get('blocklist_size', 0)), 'since': int(tot.get('start_time', 0)),
        'series': series,
        'top_domains': [{'domain': d, 'count': int(v.get('total', 0))} for d, v in (tot.get('top') or {}).items()],
        'top_blocked': [{'domain': d, 'count': int(v.get('total', 0)),
                         'list': names.get(v.get('blocklist'), v.get('blocklist') or '')}
                        for d, v in (tot.get('top_blocked') or {}).items()],
        'top_clients': sorted(per.values(), key=lambda c: -c['count'])[:10],
    }


def unbound_log(n):
    names = list_names()
    rows = stats('details', '--limit', str(n))
    if not isinstance(rows, list):
        return {'ok': False, 'error': 'no statistics yet'}
    res = []
    for r in rows:
        res.append({'t': int(r.get('time') or 0), 'client': r.get('client') or '', 'domain': (r.get('domain') or '').rstrip('.'),
                    'type': r.get('type') or '', 'blocked': r.get('action') in ('Block', 'Drop'),
                    'list': names.get(r.get('blocklist'), r.get('blocklist') or '') if r.get('blocklist') else '',
                    'source': r.get('source') or '', 'rcode': r.get('rcode') or '', 'ms': r.get('resolve_time_ms') or 0})
    return {'ok': True, 'rows': res}


# ------------------------------------------------------------- AdGuard Home
class AGH:
    def __init__(self, cfg):
        if not re.match(r'^https?://', cfg['url'] or ''):
            raise ValueError('Enter the AdGuard Home address on the Privacy page.')
        self.base = cfg['url']
        self.auth = None
        if cfg['user']:
            self.auth = 'Basic ' + base64.b64encode(('%s:%s' % (cfg['user'], cfg['password'])).encode()).decode()

    def call(self, path, body=None, timeout=10):
        req = urllib.request.Request(self.base + path, method='POST' if body is not None else 'GET',
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header('Accept', 'application/json')
        if body is not None:
            req.add_header('Content-Type', 'application/json')
        if self.auth:
            req.add_header('Authorization', self.auth)
        ctx = None
        if self.base.startswith('https'):
            # usually a self-signed certificate on the LAN
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise IOError('AdGuard Home refused the login: check the user name and password.')
            raise IOError('AdGuard Home answered %d for %s' % (e.code, path))
        except (urllib.error.URLError, OSError) as e:
            raise IOError('Cannot reach AdGuard Home at %s (%s)' % (self.base, getattr(e, 'reason', e)))
        try:
            return json.loads(data) if data.strip() else {}
        except ValueError:
            return {}

    def rules(self):
        return list(self.call('/control/filtering/status').get('user_rules') or [])

    def set_rules(self, rules):
        self.call('/control/filtering/set_rules', {'rules': rules})


def pairs(items, key):
    """AdGuard's [{"example.com": 12}, …] -> [{key: "example.com", "count": 12}, …]"""
    res = []
    for it in items or []:
        if isinstance(it, dict):
            for k, v in it.items():
                res.append({key: k, 'count': int(v) if not isinstance(v, dict) else int(v.get('count', 0))})
    return res


def agh_summary(cfg):
    a = AGH(cfg)
    st = a.call('/control/status')
    s = a.call('/control/stats')
    total = int(s.get('num_dns_queries', 0))
    blocked = int(s.get('num_blocked_filtering', 0)) + int(s.get('num_replaced_safebrowsing', 0)) + \
        int(s.get('num_replaced_parental', 0))
    hours = s.get('dns_queries') or []
    blk = s.get('blocked_filtering') or []
    now = int(time.time()) // 3600 * 3600
    series = [{'t': now - (len(hours) - 1 - i) * 3600, 'total': int(v), 'blocked': int(blk[i]) if i < len(blk) else 0}
              for i, v in enumerate(hours)] if s.get('time_units', 'hours') == 'hours' else []
    clients = pairs(s.get('top_clients'), 'client')
    for c in clients:
        c['name'] = ''
    return {
        'ok': True, 'provider': 'adguard', 'protection': bool(st.get('protection_enabled')),
        'version': st.get('version', ''), 'total': total, 'blocked': blocked,
        'pct': round(100.0 * blocked / total, 1) if total else 0.0,
        'ms': round(float(s.get('avg_processing_time', 0)) * 1000, 1), 'series': series,
        'top_domains': pairs(s.get('top_queried_domains'), 'domain')[:10],
        'top_blocked': pairs(s.get('top_blocked_domains'), 'domain')[:10],
        'top_clients': clients[:10], 'url': cfg['url'],
    }


def agh_log(cfg, n):
    d = AGH(cfg).call('/control/querylog?limit=%d' % n)
    res = []
    for r in d.get('data') or []:
        q = r.get('question') or {}
        reason = r.get('reason') or ''
        try:
            t = int(time.mktime(time.strptime(r.get('time', '')[:19], '%Y-%m-%dT%H:%M:%S')) -
                    (time.altzone if time.daylight else time.timezone))
        except ValueError:
            t = 0
        rule = (r.get('rules') or [{}])[0].get('text', '') if r.get('rules') else r.get('rule', '')
        res.append({'t': t, 'client': (r.get('client_info') or {}).get('name') or r.get('client', ''),
                    'ip': r.get('client', ''), 'domain': (q.get('name') or '').rstrip('.'), 'type': q.get('type', ''),
                    'blocked': reason.startswith('Filtered') and 'Allowlist' not in reason,
                    'list': rule, 'source': reason, 'rcode': r.get('status', ''), 'ms': float(r.get('elapsedMs') or 0)})
    return {'ok': True, 'rows': res}


def rule_change(cfg, action, domain):
    domain = domain.strip().lower().rstrip('.')
    if not DOMAIN.match(domain):
        return {'ok': False, 'error': 'invalid domain'}
    a = AGH(cfg)
    rules = a.rules()
    allow, block = '@@||%s^' % domain, '||%s^' % domain
    rules = [r for r in rules if r not in (allow, block)]
    if action == 'allow':
        rules.append(allow)
    elif action == 'block':
        rules.append(block)
    a.set_rules(rules)
    return {'ok': True}


# ------------------------------------------------------------------ main
def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'summary'
    cfg = settings()
    prov = cfg['provider']
    try:
        if cmd == 'summary':
            if prov == 'unbound':
                return out(unbound_summary())
            if prov == 'adguard':
                return out(agh_summary(cfg))
            return out({'ok': False, 'provider': 'off'})
        if cmd == 'log':
            n = max(10, min(1000, int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 200))
            if prov == 'unbound':
                return out(unbound_log(n))
            if prov == 'adguard':
                return out(agh_log(cfg, n))
            return out({'ok': False, 'rows': []})
        if cmd == 'test':
            if prov == 'adguard':
                st = AGH(cfg).call('/control/status')
                return out({'ok': True, 'version': st.get('version', ''), 'protection': bool(st.get('protection_enabled'))})
            if prov == 'unbound':
                return out({'ok': os.path.exists(STATS)})
            return out({'ok': False, 'error': 'Privacy is off'})
        if prov != 'adguard':
            return out({'ok': False, 'error': 'only for AdGuard Home'})
        if cmd == 'rule':
            return out(rule_change(cfg, argv[2] if len(argv) > 2 else '', argv[3] if len(argv) > 3 else ''))
        if cmd == 'protection':
            AGH(cfg).call('/control/protection', {'enabled': (argv[2] if len(argv) > 2 else '') == 'on'})
            return out({'ok': True})
        if cmd == 'gaming':
            a = AGH(cfg)
            rules = a.rules()
            add = ['@@||%s^' % d for d in GAMING if '@@||%s^' % d not in rules]
            a.set_rules(rules + add)
            return out({'ok': True, 'added': len(add)})
    except (IOError, ValueError) as e:
        return out({'ok': False, 'error': str(e)})
    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Privacy backend tests: the built-in provider (OPNsense's Unbound statistics,
faked) and AdGuard Home (a fake HTTP server with a login) give the page the
same shape; rule changes, protection switch, gaming-safe rules, errors.

    python3 tests/test-privacy.py
"""
import base64
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'misc/theme-unisense/src/opnsense/scripts/unisense'
MODEL = ROOT / 'misc/theme-unisense/src/opnsense/mvc/app/models/OPNsense/Unisense/Privacy.php'

PASS = FAIL = 0


def t(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   ' + name)
    else:
        FAIL += 1
        print('  FAIL ' + name + (('  -> ' + str(detail)) if detail else ''))


FAKE_STATS = r'''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[0] == 'totals':
    print(json.dumps({"total": 1000, "blocklist_size": 250000, "passed": 860,
        "resolved": {"total": 300, "pcnt": "30"}, "blocked": {"total": 140, "pcnt": "14"},
        "local": {"total": 0, "pcnt": "0"}, "start_time": 1795400000,
        "top": {"www.google.com": {"total": 90, "pcnt": "10"}},
        "top_blocked": {"ads.example.net": {"total": 40, "pcnt": "28", "blocklist": "hgz003", "latest_policy_uuid": "x"},
                        "tiktok.com": {"total": 3, "pcnt": "2", "blocklist": "Custom", "latest_policy_uuid": "x"}}}))
elif a[0] == 'rolling' and '--clients' in a:
    print(json.dumps({"1795480000": {"10.10.0.20": {"count": 30, "hostname": "desktop"}, "10.10.0.30": {"count": 5, "hostname": ""}},
                      "1795480600": {"10.10.0.20": {"count": 12, "hostname": "desktop"}}}))
elif a[0] == 'rolling':
    print(json.dumps({"1795480600": {"total": 50, "blocked": 8}, "1795480000": {"total": 40, "blocked": 5}}))
elif a[0] == 'details':
    print(json.dumps([
        {"time": 1795480700, "client": "desktop", "family": "IPv4", "type": "A", "domain": "ads.example.net.",
         "action": "Block", "source": "Recursion", "blocklist": "hgz003", "rcode": "NOERROR", "resolve_time_ms": 0},
        {"time": 1795480699, "client": "10.10.0.30", "family": "IPv4", "type": "AAAA", "domain": "www.google.com.",
         "action": "Pass", "source": "Cache", "blocklist": None, "rcode": "NOERROR", "resolve_time_ms": 1}]))
'''

UNBOUND_XML = '''<model><items><dnsbl><blocklist type="ArrayField"><type type="OptionField"><OptionValues>
<hgz003>[hagezi] Multi PRO - Extended protection</hgz003><oisd2>[oisd] NSFW Blocklist</oisd2>
</OptionValues></type></blocklist></dnsbl></items></model>'''


class AGH(BaseHTTPRequestHandler):
    state = {'rules': ['||old.example^'], 'protection': True, 'calls': []}

    def log_message(self, *a):
        pass

    def _auth(self):
        want = 'Basic ' + base64.b64encode(b'admin:s3cret').decode()
        if self.headers.get('Authorization') != want:
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._auth():
            return
        p = self.path
        if p == '/control/status':
            return self._json({'protection_enabled': self.state['protection'], 'version': 'v0.107.55', 'running': True})
        if p == '/control/stats':
            return self._json({'time_units': 'hours', 'num_dns_queries': 2000, 'num_blocked_filtering': 300,
                               'num_replaced_safebrowsing': 5, 'num_replaced_parental': 0, 'avg_processing_time': 0.0123,
                               'top_queried_domains': [{'www.google.com': 120}], 'top_blocked_domains': [{'ads.x': 70}],
                               'top_clients': [{'10.10.0.20': 900}], 'dns_queries': [10] * 24, 'blocked_filtering': [2] * 24})
        if p.startswith('/control/querylog'):
            return self._json({'data': [{'time': '2026-11-27T12:00:01.5+08:00', 'client': '10.10.0.20',
                                         'client_info': {'name': 'desktop'}, 'question': {'name': 'ads.x', 'type': 'A'},
                                         'reason': 'FilteredBlackList', 'rules': [{'text': '||ads.x^'}], 'elapsedMs': '0.4',
                                         'status': 'NOERROR'},
                                        {'time': '2026-11-27T12:00:00Z', 'client': '10.10.0.30', 'question': {'name': 'ok.y', 'type': 'A'},
                                         'reason': 'NotFilteredNotFound', 'elapsedMs': '12', 'status': 'NOERROR'}]})
        if p == '/control/filtering/status':
            return self._json({'enabled': True, 'user_rules': self.state['rules']})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        self.state['calls'].append((self.path, body))
        if self.path == '/control/filtering/set_rules':
            self.state['rules'] = body['rules']
        elif self.path == '/control/protection':
            self.state['protection'] = body['enabled']
        self._json({})


def config(provider, url='', user='', pw=''):
    return f'''<?xml version="1.0"?><opnsense><OPNsense><unisense><privacy>
<provider>{provider}</provider><agh_url>{url}</agh_url><agh_user>{user}</agh_user><agh_pass>{pw}</agh_pass>
</privacy></unisense></OPNsense></opnsense>'''


def main():
    work = Path(tempfile.mkdtemp(prefix='unisense-privacy-test-'))
    srv = HTTPServer(('127.0.0.1', 0), AGH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        run(work, 'http://127.0.0.1:%d' % srv.server_port)
    finally:
        srv.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


def run(work, url):
    (work / 'stats.py').write_text(FAKE_STATS)
    (work / 'Unbound.xml').write_text(UNBOUND_XML)
    cfgp = work / 'config.xml'
    os.environ.update(UNISENSE_CONFIG_XML=str(cfgp), UNISENSE_UNBOUND_STATS=str(work / 'stats.py'),
                      UNISENSE_UNBOUND_MODEL=str(work / 'Unbound.xml'), UNISENSE_PYTHON=sys.executable)
    sys.path.insert(0, str(SCRIPTS))
    import privacy as P
    importlib.reload(P)

    def call(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.main(['privacy.py'] + list(args))
        return json.loads(buf.getvalue())

    print('gaming-safe list')
    php = re.search(r'const GAMING = \[(.*?)\];', MODEL.read_text(), re.S).group(1)
    t('the same domains in Privacy.php and privacy.py', re.findall(r"'([^']+)'", php) == P.GAMING)

    print('off')
    cfgp.write_text(config('off'))
    t('nothing to show', call('summary') == {'ok': False, 'provider': 'off'})

    print('built in (Unbound statistics)')
    cfgp.write_text(config('unbound'))
    s = call('summary')
    t('totals and share blocked', s['ok'] and s['total'] == 1000 and s['blocked'] == 140 and s['pct'] == 14.0 and
      s['listed'] == 250000, s)
    t('24 h series in time order', [x['t'] for x in s['series']] == [1795480000, 1795480600] and s['series'][1]['blocked'] == 8)
    t('top blocked with the list by name', s['top_blocked'][0] == {'domain': 'ads.example.net', 'count': 40,
                                                                   'list': 'hagezi Multi PRO - Extended protection'}
      and s['top_blocked'][1]['list'] == 'Your blocks', s['top_blocked'])
    t('top clients summed over the day, with names', s['top_clients'][0] == {'client': '10.10.0.20', 'name': 'desktop', 'count': 42})
    lg = call('log', '50')
    t('query log: blocked flag, list, domain without the dot', lg['ok'] and lg['rows'][0]['blocked'] and
      lg['rows'][0]['domain'] == 'ads.example.net' and lg['rows'][0]['list'].startswith('hagezi') and
      not lg['rows'][1]['blocked'], lg['rows'][:2])
    P.STATS = str(work / 'missing.py')
    s = call('summary')
    t('no statistics yet: a clear message', not s['ok'] and 'statistics' in s['error'], s)
    P.STATS = str(work / 'stats.py')
    t('AdGuard-only commands refused', call('rule', 'allow', 'x.com')['ok'] is False)

    print('AdGuard Home')
    cfgp.write_text(config('adguard', url, 'admin', 's3cret'))
    s = call('summary')
    t('totals incl. safe browsing, share, speed', s['ok'] and s['total'] == 2000 and s['blocked'] == 305 and
      s['pct'] == 15.2 and s['ms'] == 12.3 and s['protection'], s)
    t('24 hourly points', len(s['series']) == 24 and s['series'][-1]['blocked'] == 2)
    t('top lists in the same shape', s['top_blocked'] == [{'domain': 'ads.x', 'count': 70}] and
      s['top_clients'][0]['client'] == '10.10.0.20')
    lg = call('log')
    t('query log normalised', lg['rows'][0]['blocked'] and lg['rows'][0]['client'] == 'desktop' and
      lg['rows'][0]['list'] == '||ads.x^' and not lg['rows'][1]['blocked'] and lg['rows'][1]['ms'] == 12.0, lg['rows'])
    t('connection test', call('test')['version'] == 'v0.107.55')
    r = call('rule', 'allow', 'Gigabyte.com.')
    t('allow a domain: an @@ rule, old rules kept', r['ok'] and AGH.state['rules'] == ['||old.example^', '@@||gigabyte.com^'],
      AGH.state['rules'])
    call('rule', 'block', 'gigabyte.com')
    t('block replaces the allow rule', AGH.state['rules'] == ['||old.example^', '||gigabyte.com^'], AGH.state['rules'])
    call('rule', 'remove', 'gigabyte.com')
    t('remove', AGH.state['rules'] == ['||old.example^'])
    t('invalid domain refused', call('rule', 'allow', 'not a domain')['ok'] is False)
    call('protection', 'off')
    t('protection switched off', AGH.state['protection'] is False)
    r = call('gaming')
    r2 = call('gaming')
    t('gaming-safe rules added once', r['added'] == len(P.GAMING) and r2['added'] == 0 and
      '@@||xboxlive.com^' in AGH.state['rules'])
    cfgp.write_text(config('adguard', url, 'admin', 'wrong'))
    s = call('summary')
    t('wrong password: says so', not s['ok'] and 'password' in s['error'], s)
    cfgp.write_text(config('adguard', 'http://127.0.0.1:9', 'admin', 's3cret'))
    s = call('summary')
    t('unreachable: says so', not s['ok'] and 'Cannot reach' in s['error'], s)
    cfgp.write_text(config('adguard', ''))
    t('no address yet: says so', 'address' in call('summary')['error'])


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Static + mock-API server for the UniSense visual test harness.

  /ui/themes/unisense/...  -> theme from this repository
  /ui/...                  -> opnsense/core src/opnsense/www
  /pages/...               -> rendered pages (render.py)
  /api/...                 -> canned JSON / server-sent events
"""
import argparse
import json
import random
import threading
import time
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
THEME = HERE.parents[1] / 'misc/theme-unisense/src/opnsense/www/themes/unisense'


def dashboard_modules(core_www):
    mods = []
    for f in sorted((core_www / 'js/widgets/Metadata').glob('*.xml')):
        for node in ET.parse(f).getroot():
            tn = node.find('translations')
            tr = {c.tag: (c.text or '') for c in tn} if tn is not None else {}
            mods.append({'id': node.tag, 'module': node.findtext('filename'), 'link': node.findtext('link') or '',
                         'multiple': node.find('multiple') is not None, 'translations': tr})
    return mods


DASHBOARD = {'options': {}, 'widgets': [
    {'id': 'systeminformation', 'x': 0, 'y': 0, 'w': 4},
    {'id': 'memory', 'x': 4, 'y': 0, 'w': 2},
    {'id': 'disk', 'x': 6, 'y': 0, 'w': 2},
    {'id': 'cpu', 'x': 8, 'y': 0, 'w': 4},
    {'id': 'gateways', 'x': 0, 'y': 1, 'w': 4},
    {'id': 'interfaces', 'x': 4, 'y': 1, 'w': 4},
    {'id': 'traffic', 'x': 8, 'y': 1, 'w': 4},
    {'id': 'services', 'x': 0, 'y': 2, 'w': 4},
    {'id': 'interfacestatistics', 'x': 4, 'y': 2, 'w': 4},
    {'id': 'announcements', 'x': 8, 'y': 2, 'w': 4},
]}

IFACES = {
    'lan': {'name': 'LAN', 'bytes received': '81234567890', 'bytes transmitted': '412345678901',
            'packets received': '91234567', 'packets transmitted': '312345678', 'input errors': '0',
            'output errors': '0', 'collisions': '0'},
    'wan': {'name': 'WAN', 'bytes received': '512345678901', 'bytes transmitted': '71234567890',
            'packets received': '412345678', 'packets transmitted': '81234567', 'input errors': '2',
            'output errors': '0', 'collisions': '0'},
    'opt1': {'name': 'IOT', 'bytes received': '1234567890', 'bytes transmitted': '9234567890',
             'packets received': '4234567', 'packets transmitted': '7234567', 'input errors': '0',
             'output errors': '0', 'collisions': '0'},
    'opt2': {'name': 'GUEST', 'bytes received': '234567890', 'bytes transmitted': '934567890',
             'packets received': '434567', 'packets transmitted': '734567', 'input errors': '0',
             'output errors': '0', 'collisions': '0'},
}


def grid(rows):
    return {'rows': rows, 'rowCount': len(rows), 'total': len(rows), 'current': 1}


MOCKS = {
    '/api/core/menu/search/': [
        {'Url': '/ui/core/dashboard', 'breadcrumb': 'Lobby / Dashboard'},
        {'Url': '/ui/cron', 'breadcrumb': 'System / Settings / Cron'},
        {'Url': '/ui/syslog', 'breadcrumb': 'System / Settings / Logging'},
        {'Url': '/system_general.php', 'breadcrumb': 'System / Settings / General'},
    ],
    '/api/core/system/status': {
        'metadata': {'system': {'status': 'NOTICE'},
                     'translations': {'dialogTitle': 'System Status', 'dialogCloseButton': 'Close'}},
        'subsystems': {'firmware': {'status': 'NOTICE', 'title': 'Firmware', 'message': 'There are updates available.',
                                    'persistent': False, 'isBanner': False, 'location': '/ui/core/firmware'}},
    },
    '/api/diagnostics/system/system_information': {
        'name': 'opnsense.home.arpa', 'versions': ['OPNsense 26.7.4-amd64', 'FreeBSD 14.3-RELEASE-p4',
                                                   'OpenSSL 3.0.17'],
        'updates': 'Click to check for updates.'},
    '/api/diagnostics/system/system_time': {'uptime': '12 days 06:41:12', 'loadavg': '0.16, 0.21, 0.18',
                                            'datetime': 'Wed Nov 18 08:47:00 AWST 2026',
                                            'config': 'Tue Nov 17 22:10:31 AWST 2026'},
    '/api/diagnostics/system/system_resources': {'memory': {'total': '8361738240', 'total_frmt': '7974',
                                                            'used': '2462162944', 'used_frmt': '2348',
                                                            'arc': '1073741824', 'arc_frmt': '1024'}},
    '/api/diagnostics/system/system_disk': {'devices': [
        {'device': 'zroot/ROOT/default', 'type': 'zfs', 'mountpoint': '/', 'used_bytes': 3221225472,
         'total_bytes': 115964116992, 'used_pct': 3, 'used': '3.0G', 'blocks': '108G', 'available': '105G'},
        {'device': 'tmpfs', 'type': 'tmpfs', 'mountpoint': '/var/log', 'used_bytes': 134217728,
         'total_bytes': 1073741824, 'used_pct': 12, 'used': '128M', 'blocks': '1.0G', 'available': '896M'}]},
    '/api/diagnostics/cpu_usage/getcputype': 'Intel(R) N100 (4 cores, 4 threads)',
    '/api/diagnostics/traffic/interface': {'interfaces': IFACES, 'time': 0},
    '/api/diagnostics/interface/get_interface_names': {'lan': 'LAN', 'wan': 'WAN', 'opt1': 'IOT', 'opt2': 'GUEST'},
    '/api/routing/settings/search_gateway': grid([
        {'uuid': 'gw1', 'name': 'WAN_DHCP', 'gateway': '100.64.12.1', 'status': 'Online', 'loss': '0.0 %',
         'delay': '4.2 ms', 'stddev': '0.4 ms', 'defaultgw': True},
        {'uuid': 'gw2', 'name': 'WAN_DHCP6', 'gateway': 'fe80::1%igc0', 'status': 'Offline', 'loss': '100.0 %',
         'delay': '~', 'stddev': '~', 'defaultgw': False},
        {'uuid': 'gw3', 'name': 'WG_VPN', 'gateway': '10.10.0.1', 'status': 'Latency', 'loss': '0.0 %',
         'delay': '212.4 ms', 'stddev': '12.1 ms', 'defaultgw': False}]),
    '/api/interfaces/overview/interfaces_info': grid([
        {'identifier': 'lan', 'description': 'LAN', 'status': 'up', 'enabled': True, 'config': {},
         'media': '2500baseT <full-duplex>', 'addr4': '192.168.1.1/24', 'addr6': 'fd00:1::1/64'},
        {'identifier': 'wan', 'description': 'WAN', 'status': 'up', 'enabled': True, 'config': {},
         'media': '1000baseT <full-duplex>', 'addr4': '100.64.12.34/22', 'addr6': ''},
        {'identifier': 'opt1', 'description': 'IOT', 'status': 'up', 'enabled': True, 'config': {},
         'media': 'Ethernet autoselect', 'addr4': '192.168.20.1/24', 'addr6': ''},
        {'identifier': 'opt2', 'description': 'GUEST', 'status': 'down', 'enabled': True, 'config': {},
         'media': 'no carrier', 'addr4': '192.168.30.1/24', 'addr6': ''}]),
    '/api/core/service/search': grid([
        {'id': 'configd', 'name': 'configd', 'description': 'System Configuration Daemon', 'running': 1, 'locked': 1},
        {'id': 'unbound', 'name': 'unbound', 'description': 'Unbound DNS', 'running': 1, 'locked': 0},
        {'id': 'kea-dhcp4', 'name': 'kea-dhcp4', 'description': 'KEA DHCPv4 server', 'running': 1, 'locked': 0},
        {'id': 'ntpd', 'name': 'ntpd', 'description': 'Network Time Daemon', 'running': 1, 'locked': 0},
        {'id': 'openssh', 'name': 'openssh', 'description': 'Secure Shell Daemon', 'running': 0, 'locked': 0},
        {'id': 'syslog-ng', 'name': 'syslog-ng', 'description': 'Syslog-ng Daemon', 'running': 1, 'locked': 0}]),
    '/api/core/dashboard/product_info_feed': {'items': [
        {'title': 'OPNsense 26.7.4 released', 'description': 'This update brings fixes and improvements.',
         'link': '#', 'pubDate': 'Tue, 10 Nov 2026', 'guid': '1'},
        {'title': 'OPNsense 26.7.3 released', 'description': 'Security and stability fixes.',
         'link': '#', 'pubDate': 'Tue, 27 Oct 2026', 'guid': '2'}]},
    '/api/cron/settings/search_jobs': grid([
        {'uuid': 'a1', 'enabled': '1', 'minutes': '0', 'hours': '3', 'days': '*', 'months': '*', 'weekdays': '*',
         'description': 'Update and reload firewall aliases', 'command': 'Update and reload firewall aliases'},
        {'uuid': 'a2', 'enabled': '1', 'minutes': '30', 'hours': '4', 'days': '*', 'months': '*', 'weekdays': '1',
         'description': 'Firmware update check', 'command': 'Firmware update check'},
        {'uuid': 'a3', 'enabled': '0', 'minutes': '*/15', 'hours': '*', 'days': '*', 'months': '*', 'weekdays': '*',
         'description': 'Periodic interface reset', 'command': 'Periodic interface reset'},
        {'uuid': 'a4', 'enabled': '1', 'minutes': '0', 'hours': '1', 'days': '1', 'months': '*', 'weekdays': '*',
         'description': 'Monthly configuration backup to Nextcloud', 'command': 'Backup configuration'}]),
    '/api/syslog/settings/search_destinations': grid([
        {'uuid': 'd1', 'enabled': '1', 'transport': 'udp4', 'hostname': '192.168.1.10', 'port': '514',
         'description': 'NAS syslog'},
        {'uuid': 'd2', 'enabled': '0', 'transport': 'tls4', 'hostname': 'logs.example.net', 'port': '6514',
         'description': 'Graylog (TLS)'}]),
    '/api/monit/settings/search_service': grid([
        {'uuid': 's1', 'enabled': '1', 'name': '$HOST', 'type': 'System', 'description': 'System health'},
        {'uuid': 's2', 'enabled': '1', 'name': 'RootFs', 'type': 'Filesystem', 'description': 'Root filesystem'}]),
}


class Handler(SimpleHTTPRequestHandler):
    core_www = None
    pages = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, gen, count=30, interval=0.5):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            for i in range(count):
                self.wfile.write(f'data: {json.dumps(gen(i))}\n\n'.encode())
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def api(self):
        path = urlparse(self.path).path
        if path in ('/api/core/dashboard/get_dashboard', '/api/core/dashboard/get_defaults'):
            return self._json({'modules': dashboard_modules(self.core_www), 'dashboard': DASHBOARD})
        if path == '/api/diagnostics/cpu_usage/stream':
            return self._sse(lambda i: {'total': 8 + random.randint(0, 9), 'user': 4 + random.randint(0, 4),
                                        'sys': 2 + random.randint(0, 3), 'intr': random.randint(0, 2)})
        if path.startswith('/api/diagnostics/traffic/stream'):
            t0 = time.time()
            return self._sse(lambda i: {'time': t0 + i, 'interfaces': {
                k: {'inbytes': random.randint(200000, 6000000), 'outbytes': random.randint(50000, 1500000)}
                for k in IFACES}})
        if path == '/api/diagnostics/firewall/stream_log':
            rules = [('r1', 'Default deny / state violation rule', 'block', 'wan'),
                     ('r2', 'Allow LAN to any', 'pass', 'lan'),
                     ('r3', 'Block IOT to LAN', 'block', 'opt1'),
                     ('r4', 'Allow GUEST DNS', 'pass', 'opt2')]
            cnt = {}

            def ev(i):
                rid, label, act, intf = random.choice(rules)
                cnt[rid] = cnt.get(rid, 0) + random.randint(1, 30)
                return {'rid': rid, 'label': label, 'action': act, 'interface': intf, 'counter': cnt[rid],
                        'rulenr': '10', '__timestamp__': time.strftime('%Y-%m-%dT%H:%M:%S'),
                        'src': f'192.168.1.{random.randint(2, 200)}', 'dst': f'1.1.{random.randint(1, 9)}.1',
                        'dstport': str(random.choice([53, 443, 80, 123]))}
            return self._sse(ev, count=40, interval=0.2)
        for key in (path, path.rstrip('/'), path.rstrip('/') + '/'):
            if key in MOCKS:
                return self._json(MOCKS[key])
        if '/search' in path or path.rstrip('/').endswith('/stats') or self.command == 'POST':
            return self._json(grid([]))
        return self._json({})

    def translate_path(self, path):
        p = urlparse(path).path
        if p.startswith('/ui/themes/unisense/'):
            return str(THEME / p[len('/ui/themes/unisense/'):])
        if p.startswith('/ui/'):
            return str(self.core_www / p[4:])
        if p.startswith('/pages/'):
            return str(self.pages / p[7:])
        return str(self.pages / 'missing')

    def do_GET(self):
        if self.path.startswith('/api/'):
            return self.api()
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        if self.path.startswith('/api/'):
            return self.api()
        self.send_error(405)


def serve(core, pages, port=8765):
    Handler.core_www = Path(core) / 'src/opnsense/www'
    Handler.pages = Path(pages)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--pages', default=str(HERE / 'site/pages'))
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    serve(a.core, a.pages, a.port)
    print(f'serving on http://127.0.0.1:{a.port}/pages/dashboard.html')
    while True:
        time.sleep(3600)

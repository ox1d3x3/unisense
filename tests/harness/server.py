#!/usr/bin/env python3
"""Static + mock-API server for the UniSense visual test harness.

  /ui/themes/unisense/...  -> theme from this repository
  /ui/...                  -> opnsense/core src/opnsense/www
  /pages/...               -> rendered pages (render.py)
  /api/...                 -> canned JSON / server-sent events
"""
import argparse
import json
import os
import random
import re
import uuid
import threading
import time
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PLUGIN_WWW = HERE.parents[1] / 'misc/theme-unisense/src/opnsense/www'
THEME = PLUGIN_WWW / 'themes/unisense'


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
        {'identifier': 'lan', 'description': 'LAN', 'status': 'up', 'enabled': True, 'config': {}, 'device': 'igc1',
         'macaddr': '00:0d:b9:aa:00:01', 'link_type': 'static', 'gateways': [],
         'media': '2500baseT <full-duplex>', 'addr4': '192.168.1.1/24', 'addr6': 'fd00:1::1/64'},
        {'identifier': 'wan', 'description': 'WAN', 'status': 'up', 'enabled': True, 'config': {}, 'device': 'igc0',
         'macaddr': '00:0d:b9:aa:00:00', 'link_type': 'dhcp', 'gateways': ['100.64.12.1'],
         'media': '1000baseT <full-duplex>', 'addr4': '100.64.12.34/22', 'addr6': ''},
        {'identifier': 'opt1', 'description': 'IOT', 'status': 'up', 'enabled': True, 'config': {}, 'device': 'igc1_vlan20',
         'macaddr': '00:0d:b9:aa:00:01', 'link_type': 'static', 'gateways': [], 'vlan_tag': 20,
         'media': 'Ethernet autoselect', 'addr4': '192.168.20.1/24', 'addr6': ''},
        {'identifier': 'opt2', 'description': 'GUEST', 'status': 'down', 'enabled': True, 'config': {}, 'device': 'igc1_vlan30',
         'macaddr': '00:0d:b9:aa:00:01', 'link_type': 'static', 'gateways': [], 'vlan_tag': 30,
         'media': 'no carrier', 'addr4': '192.168.30.1/24', 'addr6': ''},
        {'identifier': 'lo0', 'description': 'Loopback', 'status': 'up', 'enabled': True, 'config': {}, 'device': 'lo0',
         'macaddr': '', 'link_type': 'none', 'gateways': [], 'media': '', 'addr4': '127.0.0.1/8', 'addr6': '::1/128'}]),
    '/api/routes/gateway/status': {'status': 'ok', 'items': [
        {'name': 'WAN_DHCP', 'address': '100.64.12.1', 'status': 'none', 'status_translated': 'Online',
         'loss': '0.0 %', 'delay': '4.2 ms', 'stddev': '0.4 ms'},
        {'name': 'WAN_DHCP6', 'address': 'fe80::1', 'status': 'down', 'status_translated': 'Offline',
         'loss': '100.0 %', 'delay': '~', 'stddev': '~'}]},
    '/api/diagnostics/interface/get_arp': [
        {'mac': '00:0d:b9:aa:00:01', 'ip': '192.168.1.1', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': True,
         'expired': False, 'manufacturer': 'PC Engines', 'hostname': ''},
        {'mac': 'a4:83:e7:11:22:33', 'ip': '192.168.1.20', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'Apple, Inc.', 'hostname': 'mahabubs-macbook'},
        {'mac': 'f0:2f:74:44:55:66', 'ip': '192.168.1.10', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'ASUSTek COMPUTER INC.', 'hostname': 'zimaos'},
        {'mac': '3c:5a:b4:77:88:99', 'ip': '192.168.1.31', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'Google, Inc.', 'hostname': 'pixel-9'},
        {'mac': '00:11:32:aa:bb:cc', 'ip': '192.168.1.5', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'Synology Incorporated', 'hostname': 'diskstation'},
        {'mac': 'b4:fb:e4:12:34:56', 'ip': '192.168.1.2', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'Ubiquiti Inc', 'hostname': 'u6-lite'},
        {'mac': '7c:2e:0d:de:ad:01', 'ip': '192.168.1.42', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': False, 'manufacturer': 'Sony Interactive Entertainment', 'hostname': 'ps5'},
        {'mac': '24:62:ab:00:00:01', 'ip': '192.168.20.11', 'intf': 'igc1_vlan20', 'intf_description': 'IOT', 'permanent': False,
         'expired': False, 'manufacturer': 'Espressif Inc.', 'hostname': 'shelly-plug-1'},
        {'mac': 'ec:71:db:00:00:02', 'ip': '192.168.20.15', 'intf': 'igc1_vlan20', 'intf_description': 'IOT', 'permanent': False,
         'expired': False, 'manufacturer': 'Reolink Innovation Limited', 'hostname': 'driveway-cam'},
        {'mac': '00:1e:58:00:00:03', 'ip': '100.64.12.1', 'intf': 'igc0', 'intf_description': 'WAN', 'permanent': False,
         'expired': False, 'manufacturer': 'D-Link Corporation', 'hostname': ''},
        {'mac': 'de:ad:be:ef:00:07', 'ip': '192.168.1.66', 'intf': 'igc1', 'intf_description': 'LAN', 'permanent': False,
         'expired': True, 'manufacturer': '', 'hostname': ''}],
    '/api/kea/leases4/search': grid([
        {'address': '192.168.1.31', 'hwaddr': '3c:5a:b4:77:88:99', 'hostname': 'Pixel-9.', 'if': 'igc1', 'if_descr': 'LAN'},
        {'address': '192.168.1.77', 'hwaddr': '8c:85:90:00:00:09', 'hostname': 'Rehenomas-iPad', 'if': 'igc1', 'if_descr': 'LAN',
         'mac_info': 'Apple, Inc.'},
        {'address': '192.168.30.20', 'hwaddr': '9a:00:00:00:00:30', 'hostname': 'guest-laptop', 'if': 'igc1_vlan30',
         'if_descr': 'GUEST'}]),
    '/api/dnsmasq/leases/search': grid([]),
    # ISC DHCP (core up to 25.7, plugin later): different field names
    '/api/dhcpv4/leases/search_lease': grid([
        {'address': '192.168.1.5', 'mac': '00:11:32:aa:bb:cc', 'hostname': 'diskstation', 'man': 'Synology Incorporated',
         'if_descr': 'LAN', 'status': 'online'}]),
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


LABELS = {}
NAME_RE = re.compile(r'^[^\x00-\x1f\x7f<>]{0,64}$')
ICON_RE = re.compile(r'^(|auto|laptop|desktop|mobile|tablet|tv|game|server|nas|printer|camera|ap|switch|router|iot|speaker|watch|car|other)$')
NOT_FOUND = set()   # endpoints that should answer 404 (none by default)


def validate_label(dev, own_uuid):
    """mirror of the NetworkMap.xml model constraints"""
    errs = {}
    mac = str(dev.get('mac', ''))
    if not re.match(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$', mac):
        errs['device.mac'] = 'Invalid MAC address.'
    elif any(v['mac'] == mac.lower() and u != own_uuid for u, v in LABELS.items()):
        errs['device.mac'] = 'This device already has a label.'
    if not NAME_RE.match(str(dev.get('name', ''))):
        errs['device.name'] = 'Use up to 64 characters (no < or >).'
    if not ICON_RE.match(str(dev.get('icon', ''))):
        errs['device.icon'] = 'Unknown icon.'
    return errs


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
        if path.startswith('/api/unisense/trafficmap/'):
            return self.trafficmap_api(path[len('/api/unisense/trafficmap/'):])
        if path.startswith('/api/unisense/networkmap/'):
            return self.labels_api(path[len('/api/unisense/networkmap/'):])
        if path in NOT_FOUND:
            return self._json({'errorMessage': 'Endpoint not found', 'errorTitle': 'Error'}, 404)
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
        if path.startswith('/api/diagnostics/traffic/top/'):
            ips = {'lan': ['192.168.1.20', '192.168.1.10', '192.168.1.31', '192.168.1.42'],
                   'opt1': ['192.168.20.15']}
            out = {}
            for intf in path.rsplit('/', 1)[1].split(','):
                # like real traffic: devices come and go between polls
                out[intf] = {'records': [{'address': ip, 'rate_bits_in': random.randint(20000, 90000000),
                                          'rate_bits_out': random.randint(10000, 9000000)}
                                         for ip in ips.get(intf, []) if random.random() < 0.6]}
            return self._json(out)
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
        if path == '/api/kea/leases4/search' and os.environ.get('UNISENSE_HARNESS_XSS'):
            data = json.loads(json.dumps(MOCKS[path]))
            data['rows'][-1]['hostname'] = '<img src=x onerror=alert(1)>'
            return self._json(data)
        for key in (path, path.rstrip('/'), path.rstrip('/') + '/'):
            if key in MOCKS:
                return self._json(MOCKS[key])
        if '/search' in path or path.rstrip('/').endswith('/stats') or self.command == 'POST':
            return self._json(grid([]))
        return self._json({})

    def labels_api(self, action):
        parts = action.strip('/').split('/')
        name, arg = parts[0], (parts[1] if len(parts) > 1 else None)
        body = {}
        if getattr(self, 'body', b''):
            try:
                body = json.loads(self.body.decode())
            except ValueError:
                body = {}
        if name == 'search_device':
            return self._json(grid([dict(uuid=u, **v) for u, v in sorted(LABELS.items())]))
        if self.command != 'POST':
            return self._json({'errorMessage': 'POST required'}, 405)
        if name in ('add_device', 'set_device'):
            dev = body.get('device') or {}
            errs = validate_label(dev, None if name == 'add_device' else arg)
            if errs:
                return self._json({'result': 'failed', 'validations': errs})
            uid = arg if name == 'set_device' else str(uuid.uuid4())
            if name == 'set_device' and uid not in LABELS:
                return self._json({'result': 'failed'})
            LABELS[uid] = {'mac': dev['mac'].lower(), 'name': dev.get('name', ''), 'icon': dev.get('icon', '')}
            return self._json({'result': 'saved', 'uuid': uid} if name == 'add_device' else {'result': 'saved'})
        if name == 'del_device':
            if arg in LABELS:
                del LABELS[arg]
                return self._json({'result': 'deleted'})
            return self._json({'result': 'not found'})
        return self._json({'errorMessage': 'Endpoint not found'}, 404)

    def translate_path(self, path):
        p = urlparse(path).path
        if p.startswith('/ui/themes/unisense/'):
            return str(THEME / p[len('/ui/themes/unisense/'):])
        if p.startswith('/ui/') and (PLUGIN_WWW / p[4:]).is_file():
            return str(PLUGIN_WWW / p[4:])
        if p.startswith('/ui/'):
            return str(self.core_www / p[4:])
        if p.startswith('/pages/'):
            return str(self.pages / p[7:])
        return str(self.pages / 'missing')

    # OPNsense's real Content-Security-Policy headers. MVC pages
    # (ControllerBase) allow data:/blob: images; legacy PHP pages and the
    # sign-in page (guiconfig.inc) do not — "default-src 'self'" only.
    CSP_MVC = ("default-src 'self' ;img-src 'self' data: blob: ;script-src 'self' 'unsafe-inline' 'unsafe-eval' ;"
               "style-src 'self' 'unsafe-inline' 'unsafe-eval' ;")
    CSP_LEGACY = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                  "style-src 'self' 'unsafe-inline' 'unsafe-eval';")
    LEGACY_PAGES = ('legacy.html', 'login.html')

    def end_headers(self):
        p = urlparse(self.path).path
        if p.startswith('/pages/') and p.endswith('.html'):
            legacy = p.rsplit('/', 1)[-1] in self.LEGACY_PAGES
            self.send_header('Content-Security-Policy', self.CSP_LEGACY if legacy else self.CSP_MVC)
        super().end_headers()

    # ---- Traffic Map (scripts/unisense/trafficmap.py output) ----
    TM_COUNTRIES = [
        # cc, typical down/up (bit/s), connections, networks
        ('US', 48e6, 3.1e6, 142, [(15169, 'GOOGLE'), (16509, 'AMAZON-02'), (2906, 'AS-SSI'), (8075, 'MICROSOFT-CORP-MSN-AS-BLOCK')]),
        ('AU', 31e6, 1.2e6, 88, [(1221, 'ASN-TELSTRA Telstra Limited'), (13335, 'CLOUDFLARENET'), (4826, 'VOCUS-BACKBONE-AS')]),
        ('SG', 12e6, 0.9e6, 41, [(16509, 'AMAZON-02'), (32590, 'VALVE-CORPORATION')]),
        ('JP', 6.5e6, 0.4e6, 22, [(2516, 'KDDI KDDI CORPORATION'), (9370, 'SAKURA-B SAKURA Internet Inc.')]),
        ('NL', 2.2e6, 0.6e6, 17, [(60781, 'LEASEWEB-NL-AMS-01'), (1136, 'KPN')]),
        ('DE', 1.4e6, 0.3e6, 15, [(24940, 'HETZNER-AS'), (3320, 'DTAG Internet service provider operations')]),
        ('GB', 0.9e6, 0.2e6, 11, [(5089, 'NTL Virgin Media Limited')]),
        ('HK', 0.7e6, 0.1e6, 9, [(4515, 'ERX-STAR HKT PCCW-HKT DataCom Services')]),
        ('IN', 0.4e6, 0.08e6, 6, [(55836, 'RELIANCEJIO-IN Reliance Jio Infocomm Limited')]),
        ('FR', 0.25e6, 0.05e6, 5, [(16276, 'OVH')]),
        ('BR', 0.12e6, 0.03e6, 4, [(28573, 'Claro NXT Telecomunicacoes Ltda')]),
        ('CA', 0.09e6, 0.02e6, 3, [(812, 'ROGERS-COMMUNICATIONS')]),
        ('KR', 0.05e6, 0.01e6, 2, [(4766, 'KIXS-AS-KR Korea Telecom')]),
        ('NZ', 0.02e6, 0.004e6, 2, [(9500, 'VODAFONE-TRANSIT-AS-AP Vodafone NZ Ltd.')]),
        ('ZA', 0, 0, 1, [(37100, 'SEACOM-AS')]),
        ('??', 0.01e6, 0.002e6, 1, [(0, '')]),
    ]
    TM_TICK = [0]

    def trafficmap_api(self, action):
        if action == 'status':
            return self._json({'ok': True, 'db': self.tm_db()})
        if action == 'update':
            return self._json({'status': 'started'})
        if action != 'flows':
            return self._json({'ok': False, 'error': 'unknown action'})
        self.TM_TICK[0] += 1
        first = self.TM_TICK[0] == 1
        countries = []
        tot = {'in_bps': 0, 'out_bps': 0, 'conns': 0, 'bytes_in': 0, 'bytes_out': 0}
        for cc, dn, up, conns, nets in self.TM_COUNTRIES:
            f = 0 if first else random.uniform(0.55, 1.45)
            c = {'cc': cc, 'in_bps': int(dn * f), 'out_bps': int(up * f), 'conns': conns,
                 'bytes_in': int(dn * 60), 'bytes_out': int(up * 60), 'asns': []}
            for i, (asn, name) in enumerate(nets):
                share = 0.6 / (i + 1)
                c['asns'].append({'asn': asn, 'name': name, 'in_bps': int(c['in_bps'] * share),
                                  'out_bps': int(c['out_bps'] * share), 'conns': max(1, conns // (i + 2)),
                                  'bytes': int((dn + up) * 60 * share)})
            countries.append(c)
            for k in tot:
                tot[k] += c[k]
        countries.sort(key=lambda c: (c['in_bps'] + c['out_bps'], c['bytes_in'] + c['bytes_out']), reverse=True)
        return self._json({'ok': True, 'ts': int(time.time()), 'interval': 0 if first else 3.0, 'home': 'AU',
                           'db': self.tm_db(), 'totals': tot, 'countries': countries})

    def tm_db(self):
        if os.environ.get('UNISENSE_HARNESS_NODB'):
            return {'ready': False, 'geo': None, 'asn': None, 'checked': None, 'errors': []}
        now = int(time.time())
        return {'ready': True, 'geo': {'month': time.strftime('%Y-%m'), 'updated': now - 86400 * 3, 'ranges': 812345},
                'asn': {'updated': now - 3600 * 13, 'ranges': 498765}, 'checked': now - 3600 * 13, 'errors': []}

    def do_GET(self):
        if self.path.startswith('/api/'):
            return self.api()
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.body = self.rfile.read(length) if length else b''
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

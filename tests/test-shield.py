#!/usr/bin/env python3
"""Shield backend tests: list parsing and safety filters, country tables,
allow-list exceptions, list refresh (intervals, broken downloads), pf table
loading, open connections ended for newly blocked countries, block counting
from the firewall log (incremental, rotation), alerts, IP check and checkup.

    python3 tests/test-shield.py
"""
import importlib
import ipaddress
import json
import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True   # never leave __pycache__ inside the plugin tree
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'misc/theme-unisense/src/opnsense/scripts/unisense'

PASS = 0
FAIL = 0


def t(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   ' + name)
    else:
        FAIL += 1
        print('  FAIL ' + name + (('  -> ' + str(detail)) if detail else ''))


# a pfctl stand-in: tables live in files, -k calls are logged
FAKE_PFCTL = r'''#!/usr/bin/env python3
import os, sys
d = os.environ['FAKE_PF_DIR']
a = sys.argv[1:]
open(os.path.join(d, 'calls'), 'a').write(' '.join(a) + '\n')
if a[:1] == ['-sm']:
    print('states        hard limit   400000\ntable-entries hard limit  %s' % os.environ.get('FAKE_PF_LIMIT', '1000000'))
elif a[:1] == ['-t']:
    path = os.path.join(d, 'table-' + a[1])
    if a[3] == 'show':
        if not os.path.exists(path):
            sys.stderr.write('pfctl: Table does not exist.\n'); sys.exit(1)
        sys.stdout.write(''.join('   %s\n' % l.strip() for l in open(path) if l.strip()))
    elif a[3] == 'replace':
        lines = [l for l in open(a[5]) if l.strip()]
        if len(lines) > int(os.environ.get('FAKE_PF_LIMIT', '1000000')):
            sys.stderr.write('pfctl: Cannot allocate memory.\n'); sys.exit(1)
        open(path, 'w').write(''.join(lines))
    elif a[3] == 'flush':
        open(path, 'w').write('')
'''

U = {k: '11111111-1111-4111-8111-1111111111%02d' % i for i, k in enumerate(
    ('country-in', 'country-out', 'threat-in', 'threat-out', 'scanner-in'))}


def config_xml(enabled=True, countries='SG', feeds='spamhaus,feodo,cins', allow='23.10.5.5,10.0.0.1,bogus',
               extra=''):
    rules = ','.join('%s:%s' % (k, v) for k, v in U.items())
    return f'''<?xml version="1.0"?>
<opnsense>
  <system>
    <webgui><protocol>http</protocol></webgui>
    <ssh><enabled>enabled</enabled><permitrootlogin>1</permitrootlogin><passwordauth>1</passwordauth></ssh>
    <timezone>Australia/Perth</timezone>
  </system>
  <interfaces>
    <wan><if>igc0</if><descr/><blockpriv>1</blockpriv><blockbogons>1</blockbogons></wan>
    <lan><if>igc1</if><descr>Home</descr></lan>
  </interfaces>
  <nat><rule><interface>wan</interface></rule><rule><disabled>1</disabled></rule></nat>
  <OPNsense>
    <unboundplus><general><enabled>1</enabled><dnssec>1</dnssec></general>
      <dnsbl><blocklist uuid="x"><enabled>0</enabled><type>ag</type></blocklist></dnsbl></unboundplus>
    <unisense><shield>
      <enabled>{1 if enabled else 0}</enabled><countries>{countries}</countries><feeds>{feeds}</feeds>
      <allowlist>{allow}</allowlist><vtkey/><abuseipdbkey/><rules>{rules}</rules>{extra}
    </shield></unisense>
  </OPNsense>
</opnsense>
'''


def write_geo(db):
    g4 = struct.Struct('>II2s')
    g6 = struct.Struct('>16s16s2s')
    rows4 = sorted([
        (int(ipaddress.IPv4Address('8.8.8.0')), int(ipaddress.IPv4Address('8.8.8.255')), b'US'),
        (int(ipaddress.IPv4Address('23.10.0.0')), int(ipaddress.IPv4Address('23.10.255.255')), b'SG'),
        (int(ipaddress.IPv4Address('23.20.0.0')), int(ipaddress.IPv4Address('23.20.1.9')), b'SG'),
        (int(ipaddress.IPv4Address('115.64.0.0')), int(ipaddress.IPv4Address('115.64.255.255')), b'AU'),
    ])
    rows6 = [(ipaddress.IPv6Address('2406:da00::').packed, ipaddress.IPv6Address('2406:da00::ffff').packed, b'SG')]
    os.makedirs(db, exist_ok=True)
    Path(db, 'geo4.bin').write_bytes(b''.join(g4.pack(*r) for r in rows4))
    Path(db, 'geo6.bin').write_bytes(b''.join(g6.pack(*r) for r in rows6))


IFCONFIG = """igc0: flags=8863<UP> metric 0 mtu 1500
\tinet 115.64.181.190 netmask 0xffffff00
igc1: flags=8863<UP> metric 0 mtu 1500
\tinet 10.10.0.1 netmask 0xffffff00
"""

STATES = """all tcp 115.64.181.190:64321 (10.10.0.20:50544) -> 23.10.4.4:443       ESTABLISHED:ESTABLISHED
   age 00:00:31, expires in 23:59:59, 26:25 pkts, 1000:5000 bytes, rule 7
all udp 115.64.181.190:3074 (10.10.0.30:3074) -> 8.8.8.8:3074       MULTIPLE:MULTIPLE
   age 00:10:00, expires in 00:00:59, 26:25 pkts, 1000:5000 bytes, rule 7
"""

SPAMHAUS4 = '\n'.join([
    '{"cidr":"1.10.16.0/20","sblid":"SBL256894","rir":"apnic"}',
    '{"cidr":"1.10.20.0/22","sblid":"SBL1","rir":"apnic"}',          # inside the /20: collapsed
    '{"cidr":"10.0.0.0/8","sblid":"SBL2","rir":"x"}',                # private: never
    '{"cidr":"0.0.0.0/0","sblid":"SBL3","rir":"x"}',                 # everything: never
    '{"cidr":"not-an-ip"}',
    '{"type":"metadata","timestamp":1732400000,"size":3,"records":3}',
]) + '\n'
SPAMHAUS6 = '{"cidr":"2a06:e480::/29","sblid":"SBL9","rir":"ripencc"}\n{"type":"metadata"}\n'
FEODO = """#####
# abuse.ch Feodo Tracker Botnet C2 IP Blocklist (recommended)
# DstIP
50.16.16.211
192.168.1.5
224.0.0.5
# END 1 entries
"""
CINS = '\n'.join('45.%d.%d.%d' % (i // 65536, (i // 256) % 256, i % 256) for i in range(2000)) + '\n'
DSHIELD = """#   DShield.org Recommended Block List
Start\tEnd\tNetblock\tAttacks\tName\tCountry\temail
185.224.128.0\t185.224.128.255\t24\t1902\tExample\tNL\tabuse@example.org
"""


def filterlog(ts, uuid, iface, direction, src, dst, sport='50544', dport='443', proto='tcp', v=4):
    if v == 4:
        num = '6' if proto == 'tcp' else '17'
        csv = f'82,,,{uuid},{iface},match,block,{direction},4,0x0,,64,41234,0,DF,{num},{proto},60,{src},{dst},{sport},{dport},0'
        if proto == 'tcp':
            csv += ',S,123,,64240,,mss'
    else:
        csv = f'82,,,{uuid},{iface},match,block,{direction},6,0x00,0x00000,64,{proto},17,60,{src},{dst},{sport},{dport},0'
    return f'<134>1 {ts} OPNsense.localdomain filterlog 12345 - [meta sequenceId="5"] {csv}\n'


def main():
    work = Path(tempfile.mkdtemp(prefix='unisense-shield-test-'))
    try:
        run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


def run(work):
    os.environ['TZ'] = 'Australia/Perth'    # days are counted in the firewall's local time
    time.tzset()
    pf = work / 'pf'
    pf.mkdir()
    (work / 'pfctl').write_text(FAKE_PFCTL)
    (work / 'pfctl').chmod(0o755)
    (work / 'ifconfig').write_text('#!/bin/sh\ncat "%s"\n' % (work / 'ifconfig.txt'))
    (work / 'ifconfig').chmod(0o755)
    (work / 'ifconfig.txt').write_text(IFCONFIG)
    (work / 'states.txt').write_text(STATES)
    (work / 'config.xml').write_text(config_xml())
    logs = work / 'log'
    logs.mkdir()
    write_geo(work / 'db')
    feeds = work / 'feeds'
    feeds.mkdir()
    for name, data in (('drop4.json', SPAMHAUS4), ('drop6.json', SPAMHAUS6), ('feodo.txt', FEODO),
                       ('cins.txt', CINS), ('dshield.txt', DSHIELD)):
        (feeds / name).write_text(data)

    os.environ.update(FAKE_PF_DIR=str(pf), UNISENSE_PFCTL=str(work / 'pfctl'),
                      UNISENSE_IFCONFIG=str(work / 'ifconfig'), UNISENSE_DB_DIR=str(work / 'db'),
                      UNISENSE_SHIELD_DIR=str(work / 'db/shield'), UNISENSE_CONFIG_XML=str(work / 'config.xml'),
                      UNISENSE_STATES_FILE=str(work / 'states.txt'), UNISENSE_FILTER_LOG_DIR=str(logs),
                      UNISENSE_CURL=str(work / 'no-curl'), UNISENSE_FLOW_CACHE=str(work / 'flow.json'))
    sys.path.insert(0, str(SCRIPTS))
    import trafficmap  # noqa: F401
    importlib.reload(trafficmap)
    import shield as S
    importlib.reload(S)
    S.FEED_BY_ID['spamhaus']['urls'] = [(feeds / 'drop4.json').as_uri(), (feeds / 'drop6.json').as_uri()]
    S.FEED_BY_ID['feodo']['urls'] = [(feeds / 'feodo.txt').as_uri()]
    S.FEED_BY_ID['cins']['urls'] = [(feeds / 'cins.txt').as_uri()]
    S.FEED_BY_ID['dshield']['urls'] = [(feeds / 'dshield.txt').as_uri()]

    def call(*args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.main(['shield.py'] + list(args))
        return json.loads(buf.getvalue())

    def table(name):
        p = pf / ('table-' + name)
        return [ln.strip() for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else None

    print('list parsing')
    nets = [str(n) for n in S.parse_feed(SPAMHAUS4 + SPAMHAUS6)]
    t('Spamhaus JSON lines, IPv4 and IPv6', nets == ['1.10.16.0/20', '2a06:e480::/29'], nets)
    nets = [str(n) for n in S.parse_feed(FEODO)]
    t('plain list: comments, private and multicast dropped', nets == ['50.16.16.211/32'], nets)
    nets = [str(n) for n in S.parse_feed(DSHIELD, 'dshield')]
    t('DShield start/end/prefix columns', nets == ['185.224.128.0/24'], nets)
    nets = S.parse_feed('1.2.3.4,comment\n5.6.7.0/24 ; x\n100.64.1.1\n127.0.0.1\n::1\n2001:db8::1\n')
    t('CSV/semicolon lines; CGNAT, loopback and documentation ranges dropped',
      [str(n) for n in nets] == ['1.2.3.4/32', '5.6.7.0/24'], nets)
    nets = S.parse_feed('192.168.0.0/15\n192.160.0.0/12\n100.0.0.0/8\n172.0.0.0/8\n2000::/3\n8.8.0.0/16\n')
    t('a block that only partly covers private / CGNAT space is dropped too', [str(n) for n in nets] == ['8.8.0.0/16'], nets)

    print('settings')
    cfg = S.settings()
    t('countries, lists and allow list read from config.xml',
      cfg['enabled'] and cfg['countries'] == ['SG'] and cfg['feeds'] == ['spamhaus', 'feodo', 'cins'], cfg)
    t('rule ids mapped to what they block', cfg['rules'].get(U['threat-out']) == 'threat-out' and len(cfg['rules']) == 5)
    (work / 'config2.xml').write_text(config_xml().replace('<feeds>spamhaus,feodo,cins</feeds>', ''))
    os.environ['UNISENSE_CONFIG_XML'] = str(work / 'config2.xml')
    importlib.reload(trafficmap)
    t('no list setting yet: the recommended lists', S.settings()['feeds'] ==
      [f['id'] for f in S.FEEDS if f['default']])
    (work / 'config2.xml').write_text(config_xml(feeds=''))
    t('every list switched off stays off (empty setting)', S.settings()['feeds'] == [], S.settings()['feeds'])
    os.environ['UNISENSE_CONFIG_XML'] = str(work / 'config.xml')
    importlib.reload(trafficmap)

    print('country table')
    nets = [str(n) for n in S.country_networks(['SG'])]
    t('every range of the country, as CIDR blocks (IPv4 + IPv6)',
      nets == ['23.10.0.0/16', '23.20.0.0/24', '23.20.1.0/29', '23.20.1.8/31', '2406:da00::/112'], nets)
    allow = S.allow_entries(cfg, {'115.64.181.190'})
    t('allow list + own address become exceptions; private and invalid ignored',
      allow == ['!115.64.181.190/32', '!23.10.5.5/32'], allow)

    print('inert until used')
    (work / 'config.xml').write_text(config_xml(enabled=False, countries=''))
    r = call('apply')
    t('nothing blocked: no pf table is created', r['ok'] and not list(pf.glob('table-*')), list(pf.glob('table-*')))
    (work / 'config.xml').write_text(config_xml())

    print('update')
    r = call('update')
    t('lists downloaded', r['ok'] and sorted(r['updated']) == ['cins', 'feodo', 'spamhaus'], r)
    th = table('unisense_threats')
    t('threat table = Spamhaus + Feodo (+ exceptions)', th is not None and '1.10.16.0/20' in th and
      '50.16.16.211/32' in th and '2a06:e480::/29' in th and '!23.10.5.5/32' in th, th)
    t('private, CGNAT, link-local and ULA always excepted in every table', all(
      x in (th or []) and x in (table('unisense_geoblock') or [])
      for x in ('!10.0.0.0/8', '!192.168.0.0/16', '!100.64.0.0/10', '!169.254.0.0/16', '!fc00::/7', '!fe80::/10')))
    sc = table('unisense_scanners')
    t('scanner table: CINS, collapsed', sc is not None and len([x for x in sc if not x.startswith('!')]) < 2000
      and '45.0.0.0/22' in sc, (sc or [])[:4])
    geo = table('unisense_geoblock')
    t('country table loaded', geo is not None and '23.10.0.0/16' in geo and '!23.10.5.5/32' in geo, geo)
    flags = trafficmap.Flags()
    t('Traffic Map marks listed addresses (threat, scanner, none)',
      (flags('1.10.17.1'), flags('45.0.0.7'), flags('8.8.8.8'), flags('2a06:e480::1')) ==
      ('threat', 'scanner', '', 'threat'))
    t('Traffic Map knows the blocked countries', trafficmap.shield_countries() == ['SG'])
    calls = (pf / 'calls').read_text()
    r = call('update')
    t('nothing due: no download, no table reload', r['updated'] == [] and
      (pf / 'calls').read_text().count('-T replace') == calls.count('-T replace'))
    before = table('unisense_scanners')
    (feeds / 'cins.txt').write_text('<html>Service unavailable</html>\n')
    r = call('update', '--force')
    st = json.loads((work / 'db/shield/state.json').read_text())
    t('a broken download keeps the previous list', 'cins' not in r['updated'] and
      st['feeds']['cins']['error'] and table('unisense_scanners') == before and '45.0.0.0/22' in before, st['feeds']['cins'])
    (feeds / 'feodo.txt').write_text('# empty today\n')
    r = call('update', '--force')
    t('a small list may legitimately empty', 'feodo' in r['updated'] and '50.16.16.211/32' not in table('unisense_threats'))
    (pf / 'table-unisense_threats').write_text('')   # a reboot empties pf's tables
    r = call('apply')
    t('tables refilled after a reboot', '1.10.16.0/20' in table('unisense_threats'), r)

    print('blocking a country ends its open connections')
    (pf / 'calls').write_text('')
    (work / 'config.xml').write_text(config_xml(countries='SG,US'))
    r = call('apply', '--kill')
    kills = [ln for ln in (pf / 'calls').read_text().splitlines() if ln.startswith('-k')]
    t('states to the newly blocked US address ended (SG was already blocked)',
      r['killed'] == 1 and '-k 0.0.0.0/0 -k 8.8.8.8' in kills and not any('23.10.4.4' in k for k in kills), kills)
    r = call('apply', '--kill')
    t('applying again ends nothing', r['killed'] == 0)

    print('table limit')
    os.environ['FAKE_PF_LIMIT'] = '3'
    (work / 'config.xml').write_text(config_xml(countries='SG,US,AU'))
    r = call('apply')
    t('too many addresses: a clear message, not a crash', not r['ok'] and
      'Maximum Table Entries' in ' '.join(r['errors']), r)
    del os.environ['FAKE_PF_LIMIT']
    r = call('apply')
    t('…and it recovers once the limit is raised', r['ok'], r)

    print('blocks from the firewall log')
    (work / 'config.xml').write_text(config_xml())
    day = logs / 'filter_20261124.log'
    day.write_text(
        filterlog('2026-11-24T10:00:01+08:00', U['threat-out'], 'igc1', 'in', '10.10.0.20', '1.10.16.9') +
        filterlog('2026-11-24T10:00:02+08:00', '99999999-9999-4999-8999-999999999999', 'igc0', 'in', '1.2.3.4', '115.64.181.190') +
        filterlog('2026-11-24T10:00:03+08:00', U['scanner-in'], 'igc0', 'in', '45.0.0.7', '115.64.181.190',
                  '40000', '22') +
        filterlog('2026-11-24T10:00:04+08:00', U['country-out'], 'igc1', 'in', '2001:db8:1::20', '2406:da00::1',
                  '5000', '3478', 'udp', 6) +
        filterlog('2026-11-24T10:00:05+08:00', U['threat-out'], 'igc1', 'in', '10.10.0.20', '1.10.16.9'))
    S.NOW = 1795485610   # 2026-11-24 10:00:10 Perth
    r = call('hits')
    hits = r['hits']
    t('only Shield rules counted (4 of 5 lines)', len(hits) == 4, hits)
    t('newest first; direction decides device vs remote',
      hits[0]['device'] == '10.10.0.20' and hits[0]['remote'] == '1.10.16.9' and hits[0]['port'] == '443' and
      hits[2]['remote'] == '45.0.0.7' and hits[2]['port'] == '22', hits[:3])
    t('IPv6 + UDP parsed', hits[1]['kind'] == 'country-out' and hits[1]['remote'] == '2406:da00::1' and
      hits[1]['proto'] == 'udp' and hits[1]['port'] == '3478', hits[1])
    t('interfaces shown by name', hits[0]['iface'] == 'Home' and hits[2]['iface'] == 'WAN', hits[0]['iface'])
    t('country looked up', hits[1]['cc'] == 'SG', hits[1])
    stf = work / 'db/shield/state.json'
    m1 = stf.stat().st_mtime_ns
    call('status')
    call('alerts')
    t('reading again with nothing new does not rewrite the state', stf.stat().st_mtime_ns == m1)
    a = call('alerts')
    t('alerts say whether the lists are on (the menu badge stops polling without them)', a['enabled'] is True)
    t('one alert: a device tried to reach a botnet/criminal address twice',
      a['unseen'] == 1 and len(a['alerts']) == 1 and a['alerts'][0]['count'] == 2 and
      a['alerts'][0]['lists'] == ['spamhaus'], a)
    with day.open('a') as f:
        f.write(filterlog('2026-11-24T10:01:00+08:00', U['scanner-in'], 'igc0', 'in', '45.0.0.8', '115.64.181.190'))
    r = call('hits')
    t('incremental: only the new line is added', len(r['hits']) == 5 and r['hits'][0]['remote'] == '45.0.0.8')
    (logs / 'filter_20261125.log').write_text(
        filterlog('2026-11-25T00:00:01+08:00', U['threat-out'], 'igc1', 'in', '10.10.0.21', '50.16.16.211'))
    r = call('hits')
    t('carries on into the next day\'s file', len(r['hits']) == 6 and r['hits'][0]['device'] == '10.10.0.21')
    S.NOW = 1795536600   # 2026-11-25 00:10 Perth
    call('ack')
    a = call('alerts')
    t('alerts marked as seen', a['unseen'] == 0 and len(a['alerts']) == 2, a)
    s = call('status')
    t('status: counters today and this week', s['stats']['today']['threat-out'] == 1 and
      s['stats']['week']['scanner-in'] == 2 and s['stats']['week']['threat-out'] == 3, s['stats'])
    t('status: tables, lists, rules', s['tables']['threat']['entries'] > 0 and
      len(s['feeds']) == len(S.FEEDS) and s['rules'] == sorted(U), s['tables'])

    print('check an address')
    c = call('check', '1.10.16.9')
    t('known-bad address: which list, blocked', c['ok'] and c['blocked'] and
      [x['id'] for x in c['lists']] == ['spamhaus'] and 'virustotal.com' in c['links']['virustotal'], c)
    c = call('check', '23.10.5.5')
    t('allow-listed address in a blocked country: not blocked', c['cc'] == 'SG' and c['country_blocked']
      and c['allowed'] and not c['blocked'], c)
    t('invalid address refused', call('check', '1.2.3;rm')['ok'] is False)

    print('timed exceptions (Allow for …)')
    os.environ['UNISENSE_BOOT_ID'] = 'boot-A'
    r = call('allow', '1.10.16.9', '1h')
    t('allow for an hour: taken out of every table at once', r['ok'] and '!1.10.16.9/32' in table('unisense_threats')
      and '!1.10.16.9/32' in table('unisense_geoblock'), r)
    c = call('check', '1.10.16.9')
    t('the check shows it allowed, with its end time', c['allowed'] and not c['blocked'] and
      c['allow_until'] == int(S.NOW) + 3600 and c['lists'], c)
    s1 = call('status')
    t('listed on the page with its duration', any(a['addr'] == '1.10.16.9' and a['dur'] == '1h' for a in s1['allows'])
      and any(a['forever'] and a['addr'] == '23.10.5.5' for a in s1['allows']), s1['allows'])
    r = call('allow', '50.16.16.0/24', 'restart')
    t('a small network until the next restart', r['ok'] and '!50.16.16.0/24' in table('unisense_threats'), r)
    for bad, dur, why in (('10.0.0.1', '1h', 'private'), ('1.0.0.0/8', '1h', 'too wide'), ('1.10.16.9', 'week', 'duration'),
                          ('1.2.3;rm', '1h', 'not an address')):
        r = call('allow', bad, dur)
        t('refused: ' + why, r['ok'] is False, r)
    S.NOW += 3601
    s1 = call('status')
    t('after an hour it expires by itself (on the next poll or cron run)',
      not any(a['addr'] == '1.10.16.9' for a in s1['allows']) and '!1.10.16.9/32' not in table('unisense_threats'), s1['allows'])
    t('…while "until restart" stays', '!50.16.16.0/24' in table('unisense_threats'))
    os.environ['UNISENSE_BOOT_ID'] = 'boot-B'
    call('apply')
    t('after a restart it is gone', '!50.16.16.0/24' not in table('unisense_threats'))
    call('allow', '85.203.46.168', '1mo')
    r = call('unallow', '85.203.46.168')
    t('removed on request', r['ok'] and r['removed'] and '!85.203.46.168/32' not in table('unisense_threats'), r)

    print('unblock requests')
    hook = work / 'hook.log'
    for name in ('php', 'py'):
        (work / ('fake-' + name)).write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, hook))
        (work / ('fake-' + name)).chmod(0o755)
    S.PRIVACY_PHP, S.PRIVACY_PY = str(work / 'fake-php'), str(work / 'fake-py')
    hooks = lambda: hook.read_text().splitlines() if hook.exists() else []   # noqa: E731
    base = {'ip': '10.10.0.20', 'name': 'Mahabub', 'site': 'gigabyte.com', 'reason': 'driver downloads for my board'}
    t('a valid request is kept', S.add_request(base) == 'ok')
    r = call('requests')
    t('listed as pending', r['pending'] == 1 and r['requests'][0]['status'] == 'pending' and
      r['requests'][0]['kind'] == 'domain', r)
    t('asking again for the same site keeps one request', S.add_request(dict(base, reason='still need it')) == 'ok'
      and len(call('requests')['requests']) == 1)
    t('a reason of 101 words is refused', S.add_request(dict(base, site='a.com', reason='w ' * 101)) == 'invalid')
    t('a name of 61 characters is refused', S.add_request(dict(base, site='a.com', name='x' * 61)) == 'invalid')
    t('a site that is neither a domain nor an address is refused',
      S.add_request(dict(base, site='<script>')) == 'invalid')
    S.add_request(dict(base, name='a\x00b<i>', site='b.com'))
    rq = {x['site']: x for x in call('requests')['requests']}
    t('control characters dropped, the rest kept as written (the page escapes it)', rq['b.com']['name'] == 'ab<i>', rq['b.com'])
    for i in range(3):
        S.add_request(dict(base, site='s%d.com' % i))
    t('the 6th new request in an hour from one device is refused', S.add_request(dict(base, site='s9.com')) == 'rate')
    t('…another device can still ask', S.add_request(dict(base, ip='10.10.0.21', site='s9.com')) == 'ok')
    t('status counts pending requests for the badge', call('status')['requests_pending'] == 6)
    gid = rq['gigabyte.com']['id']
    r = call('request', 'approve', gid, '1h')
    st = json.loads((work / 'db/shield/state.json').read_text())
    t('approve a website for an hour: kept with its end time', r['ok'] and
      st['domain_allows']['gigabyte.com']['until'] == int(S.NOW) + 3600, st.get('domain_allows'))
    t('…and Unbound is told once', hooks() == ['php sync apply'], hooks())
    call('request', 'approve', gid, '1h')
    t('approving again does not resync', hooks() == ['php sync apply'] and
      {x['id']: x for x in call('requests')['requests']}[gid]['status'] == 'approved', hooks())
    call('request', 'decline', rq['b.com']['id'])
    t('declined', {x['id']: x for x in call('requests')['requests']}[rq['b.com']['id']]['status'] == 'declined')
    S.add_request(dict(base, site='1.10.16.9', ip='10.10.0.22'))
    aid = next(x['id'] for x in call('requests')['requests'] if x['site'] == '1.10.16.9')
    r = call('request', 'approve', aid, '24h')
    t('an address request uses Allow', r['ok'] and any(a['addr'] == '1.10.16.9' and a['dur'] == '24h' for a in r['allows']), r)
    t('unknown duration refused', call('request', 'approve', aid, 'week')['ok'] is False)
    S.NOW += 3601
    call('status')
    st = json.loads((work / 'db/shield/state.json').read_text())
    t('an hour later the website approval ends and Unbound is told', 'gigabyte.com' not in st.get('domain_allows', {})
      and hooks()[-1] == 'php sync apply' and len(hooks()) == 2, hooks())
    t('…and the request shows expired', {x['id']: x for x in call('requests')['requests']}[gid]['status'] == 'expired')
    S.NOW += 31 * 24 * 3600
    S.add_request(dict(base, site='new.com', ip='10.10.0.30'))
    t('declined requests are dropped after 30 days', not any(x['site'] == 'b.com' for x in call('requests')['requests']))
    S.NOW -= 31 * 24 * 3600

    print('requests: review fixes')
    fid = next(x['id'] for x in call('requests')['requests'] if x['site'] == 's0.com')
    call('request', 'approve', fid, 'forever')
    st = json.loads((work / 'db/shield/state.json').read_text())
    t('"always" for a website is not kept a second time out of sight', 's0.com' not in st.get('domain_allows', {}), st.get('domain_allows'))
    n0 = len(hooks())
    sid = next(x['id'] for x in call('requests')['requests'] if x['site'] == 's1.com')
    call('request', 'approve', sid, '1h')
    S.NOW += 3601
    os.environ['UNISENSE_BOOT_ID'] = 'boot-B'
    call('update')
    st = json.loads((work / 'db/shield/state.json').read_text())
    t('the cron job (update) ends timed website approvals, and tells Privacy', 's1.com' not in st.get('domain_allows', {})
      and len(hooks()) == n0 + 2 and hooks()[-1] == 'php sync apply', hooks()[n0:])
    inbox = work / 'db/blockpage/inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    victim = work / 'victim'
    victim.mkdir()
    (victim / 'keep.json').write_text('{}')
    os.mkfifo(str(inbox / 'fifo.json'))
    (inbox / 'link.json').symlink_to(victim / 'keep.json')
    (inbox / 'badt.json').write_text(json.dumps(dict(base, site='bad-t.com', t=[1])))
    (inbox / 'list.json').write_text('[1, 2]')
    (inbox / 'good.json').write_text(json.dumps(dict(base, ip='10.10.0.40', site='good.com', t=int(S.NOW))))
    import threading
    res = {}
    th = threading.Thread(target=lambda: res.setdefault('r', call('requests')), daemon=True)
    th.start()
    th.join(10)
    t('odd files in the inbox (a FIFO, a symlink, a bad time, a list) never hang or break it', 'r' in res and
      any(x['site'] == 'good.com' for x in res['r']['requests']) and not any(x['site'] == 'bad-t.com' for x in res['r']['requests']), res)
    t('…they are dropped, and a symlink\'s target is left alone', not list(inbox.iterdir()) and (victim / 'keep.json').exists())
    shutil.rmtree(inbox)
    (work / 'db/blockpage/inbox').symlink_to(victim)
    call('requests')
    t('an inbox that is a symlink is not read at all', (victim / 'keep.json').exists())
    (work / 'db/blockpage/inbox').unlink()

    print('dismiss alerts')
    a = call('alerts')['alerts'][0]
    n = len(call('alerts')['alerts'])
    r = call('dismiss', a['device'], a['remote'])
    t('dismissed: the alert is hidden', r['ok'] and len(call('alerts')['alerts']) == n - 1)
    t('unknown alert refused', call('dismiss', '1.1.1.1', '2.2.2.2')['ok'] is False)
    ts = time.strftime('%Y-%m-%dT%H:%M:%S+08:00', time.localtime(S.NOW))
    with (logs / ('filter_%s.log' % time.strftime('%Y%m%d', time.localtime(S.NOW)))).open('a') as f:
        f.write(filterlog(ts, U['threat-out'], 'igc1', 'in', a['device'], a['remote']))
    al = call('alerts')
    t('a new try brings it back as new', any(x['remote'] == a['remote'] and x['new'] for x in al['alerts']), al)

    print('checkup')
    k = {x['id']: x for x in call('checkup')['checks']}
    t('HTTP GUI flagged', not k['https']['ok'] and k['https']['level'] == 'warn')
    t('no ">" in any message (the API escapes it)', not any('>' in x['detail'] + x['title'] for x in k.values()))
    t('root SSH with password flagged', not k['ssh']['ok'])
    t('WAN bogons/private ok, DNSSEC ok', k['bogons']['ok'] and k['dnssec']['ok'])
    t('DNS blocklist suggested (info only)', not k['dnsbl']['ok'] and k['dnsbl']['level'] == 'info')
    t('Shield threat lists on', k['shield']['ok'])
    t('one port forward reported', not k['exposed']['ok'] and '1 port forward' in k['exposed']['detail'], k['exposed'])

    print('switching the lists off')
    (work / 'config.xml').write_text(config_xml(enabled=False))
    r = call('apply')
    t('list tables emptied, countries still blocked', r['ok'] and table('unisense_threats') == [] and
      table('unisense_scanners') == [] and '23.10.0.0/16' in table('unisense_geoblock'), r)
    r = call('update', 'force')
    t('…and no list is downloaded', r['updated'] == [])
    c = call('check', '1.10.16.9')
    t('a listed address is not blocked while the lists are off', not c['blocked'] and c['lists'], c)
    (work / 'config.xml').write_text(config_xml(enabled=False, countries=''))
    r = call('apply')
    t('no countries: the country table is empty too', table('unisense_geoblock') == [], r)


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Traffic Map backend tests: database compile + lookups, pf state parsing,
NAT de-duplication, direction and rate calculation.

    python3 tests/test-trafficmap.py
"""
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True   # never leave __pycache__ inside the plugin tree
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'misc/theme-unisense/src/opnsense/scripts/unisense/trafficmap.py'

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


# iptoasn "ip2asn-combined": first, last, asn, country (of the AS), description
ASN_ROWS = [
    ('0.0.0.0', '0.255.255.255', '0', 'None', 'Not routed'),
    ('1.1.1.0', '1.1.1.255', '13335', 'US', 'CLOUDFLARENET'),
    ('8.8.8.0', '8.8.8.255', '15169', 'US', 'GOOGLE'),
    ('23.10.0.0', '23.10.255.255', '20940', 'NL', 'AKAMAI-ASN1'),
    ('103.10.0.0', '103.10.255.255', '13335', 'US', 'CLOUDFLARENET'),
    ('115.64.0.0', '115.64.255.255', '1221', 'AU', 'ASN-TELSTRA Telstra Limited'),
    ('2001:4860::', '2001:4860:ffff:ffff:ffff:ffff:ffff:ffff', '15169', 'US', 'GOOGLE'),
]
# pad so the "looks wrong" guard (>= 1000 IPv4 ranges) is satisfied
for i in range(1100):
    ASN_ROWS.append(('200.%d.%d.0' % (i // 256, i % 256), '200.%d.%d.255' % (i // 256, i % 256), '64512', 'BR', 'TEST-%d' % i))

# DB-IP country lite: start,end,country (where the address is)
GEO_ROWS = [
    ('1.1.1.0', '1.1.1.255', 'AU'),          # Cloudflare anycast seen from Perth: AU
    ('8.8.8.0', '8.8.8.255', 'US'),
    ('23.10.0.0', '23.10.255.255', 'SG'),
    ('103.10.0.0', '103.10.255.255', 'JP'),
    ('115.64.0.0', '115.64.255.255', 'AU'),
    ('2001:4860::', '2001:4860:ffff:ffff:ffff:ffff:ffff:ffff', 'US'),
]
for i in range(1100):
    GEO_ROWS.append(('200.%d.%d.0' % (i // 256, i % 256), '200.%d.%d.255' % (i // 256, i % 256), 'BR'))

IFCONFIG = """igc0: flags=8863<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tether 00:0d:b9:aa:00:01
\tinet 115.64.181.190 netmask 0xffffff00 broadcast 115.64.181.255
\tinet6 fe80::20d:b9ff:feaa:1%igc0 prefixlen 64 scopeid 0x1
igc1: flags=8863<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tinet 10.10.0.1 netmask 0xffffff00 broadcast 10.10.0.255
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> metric 0 mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
"""


def states(scale=1):
    """pfctl -vvs state, in the layout core's lib/states.py parses"""
    s = scale
    return f"""all tcp 8.8.8.8:443 <- 10.10.0.20:50544       ESTABLISHED:ESTABLISHED
   [3471337521 + 4194304] wscale 8  [1858862349 + 65535] wscale 8
   age 00:00:31, expires in 23:59:59, 26:25 pkts, {1000 * s}:{50000 * s} bytes, rule 98
   id: 00000000655f4ac1 creatorid: 9dac8f5a
all tcp 115.64.181.190:64321 (10.10.0.20:50544) -> 8.8.8.8:443       ESTABLISHED:ESTABLISHED
   [3471337521 + 4194304] wscale 8  [1858862349 + 65535] wscale 8
   age 00:00:31, expires in 23:59:59, 26:25 pkts, {1000 * s}:{50000 * s} bytes, rule 7
   id: 00000000655f4ac2 creatorid: 9dac8f5a
all udp 1.1.1.1:53 <- 10.10.0.21:40000       MULTIPLE:SINGLE
   age 00:00:02, expires in 00:00:58, 2:2 pkts, {100 * s}:{400 * s} bytes, rule 98
   id: 00000000655f4ac3 creatorid: 9dac8f5a
all udp 115.64.181.190:41000 (10.10.0.21:40000) -> 1.1.1.1:53       MULTIPLE:SINGLE
   age 00:00:02, expires in 00:00:58, 2:2 pkts, {100 * s}:{400 * s} bytes, rule 7
   id: 00000000655f4ac4 creatorid: 9dac8f5a
all tcp 10.10.0.5:443 (115.64.181.190:443) <- 23.10.4.4:51111       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:01:00, expires in 23:59:00, 10:10 pkts, {70000 * s}:{2000 * s} bytes, rule 12
   id: 00000000655f4ac5 creatorid: 9dac8f5a
all tcp 23.10.4.4:51111 -> 10.10.0.5:443       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:01:00, expires in 23:59:00, 10:10 pkts, {70000 * s}:{2000 * s} bytes, rule 99
   id: 00000000655f4ac6 creatorid: 9dac8f5a
all tcp 115.64.181.190:30000 -> 103.10.1.1:443       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:00:10, expires in 23:59:50, 5:5 pkts, {300 * s}:{9000 * s} bytes, rule 3
   id: 00000000655f4ac7 creatorid: 9dac8f5a
all tcp 2001:4860:4860::8888[443] <- 2403:5800:aaaa::20[51000]       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:00:10, expires in 23:59:50, 5:5 pkts, {200 * s}:{6000 * s} bytes, rule 98
   id: 00000000655f4ac8 creatorid: 9dac8f5a
all icmp 115.64.181.190:5555 -> 8.8.8.8:5555       0:0
   age 00:00:01, expires in 00:00:09, 1:1 pkts, {84 * s}:{84 * s} bytes, rule 3
   id: 00000000655f4ac9 creatorid: 9dac8f5a
all tcp 10.10.0.20:51515 -> 10.10.0.5:22       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:00:10, expires in 23:59:50, 5:5 pkts, 999999:999999 bytes, rule 98
   id: 00000000655f4aca creatorid: 9dac8f5a
all udp 10.10.0.20:5353 -> 224.0.0.251:5353       SINGLE:NO_TRAFFIC
   age 00:00:10, expires in 00:00:50, 5:0 pkts, 500:0 bytes, rule 98
   id: 00000000655f4acb creatorid: 9dac8f5a
all tcp 100.64.12.1:179 <- 115.64.181.190:40000       ESTABLISHED:ESTABLISHED
   [1 + 1] wscale 8  [1 + 1] wscale 8
   age 00:00:10, expires in 23:59:50, 5:5 pkts, 400:400 bytes, rule 98
   id: 00000000655f4acc creatorid: 9dac8f5a
"""


def run(args, env):
    r = subprocess.run([sys.executable, str(SCRIPT)] + args, capture_output=True, text=True, env=env, timeout=120)
    return r


def main():
    work = Path(tempfile.mkdtemp(prefix='usn-tm-'))
    asn_gz = work / 'ip2asn-combined.tsv.gz'
    with gzip.open(asn_gz, 'wt') as f:
        for r in ASN_ROWS:
            f.write('\t'.join(r) + '\n')
    geo_dir = work / 'geo'
    geo_dir.mkdir()
    month = time.strftime('%Y-%m', time.gmtime())
    with gzip.open(geo_dir / ('dbip-country-lite-%s.csv.gz' % month), 'wt') as f:
        for r in GEO_ROWS:
            f.write('%s,%s,%s\n' % r)
    ifc = work / 'ifconfig'
    ifc.write_text('#!/bin/sh\ncat <<"EOF"\n' + IFCONFIG + 'EOF\n')
    ifc.chmod(0o755)
    sfile = work / 'states.txt'
    env = dict(os.environ,
               UNISENSE_DB_DIR=str(work / 'db'),
               UNISENSE_FLOW_CACHE=str(work / 'cache.json'),
               UNISENSE_ASN_URL=asn_gz.as_uri(),
               UNISENSE_GEO_URL=(geo_dir.as_uri() + '/dbip-country-lite-{month}.csv.gz'),
               UNISENSE_IFCONFIG=str(ifc),
               UNISENSE_STATES_FILE=str(sfile),
               UNISENSE_CONFIG_XML=str(work / 'nope.xml'))

    print('status before any download')
    r = run(['status'], env)
    st = json.loads(r.stdout)
    t('status works without a database', st['ok'] and not st['db']['ready'], r.stdout + r.stderr)

    print('flows without a database')
    sfile.write_text(states(1))
    r = run(['flows'], env)
    fl = json.loads(r.stdout)
    t('flows work without a database (country unknown)', fl['ok'] and fl['countries'] and fl['countries'][0]['cc'] == '??', r.stdout[:300] + r.stderr)
    os.unlink(work / 'cache.json')

    print('update')
    r = run(['update'], env)
    t('update succeeds', r.returncode == 0 and 'OK' in r.stdout, r.stdout + r.stderr)
    meta = json.loads((work / 'db/meta.json').read_text())
    t('networks compiled', meta.get('asn', {}).get('ranges', 0) > 1000, meta)
    t('countries compiled (current month)', meta.get('geo', {}).get('month') == month, meta)
    for f in ('asn4.bin', 'asn6.bin', 'asn-names.txt', 'geo4.bin', 'geo6.bin'):
        t('%s written' % f, (work / 'db' / f).stat().st_size > 0)
    r = run(['update'], env)
    t('same month is not downloaded again', 'is current' in r.stdout, r.stdout)

    print('lookups')
    sys.path.insert(0, str(SCRIPT.parent))
    os.environ['UNISENSE_DB_DIR'] = str(work / 'db')
    import importlib
    tm = importlib.import_module('trafficmap')
    look = tm.Lookup()
    t('location from DB-IP wins over the AS country', look('1.1.1.1') == ('AU', 13335, 'CLOUDFLARENET'), look('1.1.1.1'))
    t('AS organisation', look('8.8.8.8') == ('US', 15169, 'GOOGLE'), look('8.8.8.8'))
    t('IPv6', look('2001:4860:4860::8888')[0:2] == ('US', 15169), look('2001:4860:4860::8888'))
    t('range edges', look('23.10.255.255')[0] == 'SG' and look('23.10.0.0')[0] == 'SG')
    t('unknown address', look('9.9.9.9') == ('', 0, ''), look('9.9.9.9'))
    t('padding rows', look('200.4.75.1') == ('BR', 64512, 'TEST-1099'), look('200.4.75.1'))

    print('state parsing')
    tm.IFCONFIG = str(ifc)
    own = tm.local_addresses()
    t('own addresses from ifconfig', {'115.64.181.190', '10.10.0.1', '127.0.0.1'} <= own, own)
    conns = tm.parse_states(states(1), own)
    remotes = sorted(v[2] for v in conns.values())
    t('LAN and NAT states of one connection are merged', remotes.count('8.8.8.8') == 2 and remotes.count('1.1.1.1') == 1, remotes)
    t('LAN-only, multicast and CGNAT peers are ignored', not any(r.startswith(('10.', '224.', '100.64.')) for r in remotes), remotes)
    g = conns['tcp|10.10.0.20:50544|8.8.8.8:443']
    t('outbound: download/upload the right way round', g[0] == 50000 and g[1] == 1000, g)
    pf = conns['tcp|10.10.0.5:443|23.10.4.4:51111']
    t('inbound port forward: remote is the client', pf[2] == '23.10.4.4' and pf[0] == 70000 and pf[1] == 2000, pf)
    t('firewall-originated traffic counted', 'tcp|115.64.181.190:30000|103.10.1.1:443' in conns)
    t('IPv6 connection parsed', any(v[2] == '2001:4860:4860::8888' for v in conns.values()))

    print('rates')
    sfile.write_text(states(1))
    r1 = json.loads(run(['flows'], env).stdout)
    t('first call: baseline only (no rates yet)', r1['interval'] == 0 and r1['totals']['in_bps'] == 0, r1['totals'])
    # pretend the first call was 2 s ago
    c = json.loads((work / 'cache.json').read_text())
    c['ts'] -= 2.0
    (work / 'cache.json').write_text(json.dumps(c))
    sfile.write_text(states(3))
    r2 = json.loads(run(['flows'], env).stdout)
    by = {x['cc']: x for x in r2['countries']}
    us = by.get('US', {})
    # US: 8.8.8.8 tcp (down 50000->150000) + icmp (84->252) + v6 (6000->18000) over ~2 s
    exp_in = (100000 + 168 + 12000) * 8 / 2.0
    t('rates per country', us and abs(us['in_bps'] - exp_in) / exp_in < 0.05, (us.get('in_bps'), exp_in))
    t('home country from the WAN address', r2['home'] == 'AU', r2['home'])
    # behind CGNAT: no public address of its own -> the configured time zone
    cg = work / 'ifconfig-cgnat'
    cg.write_text('#!/bin/sh\necho "igc0: flags=8843<UP>"\necho "\tinet 100.70.1.2 netmask 0xffc00000"\n')
    cg.chmod(0o755)
    (work / 'config.xml').write_text('<?xml version="1.0"?>\n<opnsense><system><timezone>Australia/Perth</timezone></system></opnsense>\n')
    (work / 'zone.tab').write_text('# comment\nAU\t-3157+11551\tAustralia/Perth\tWestern Australia\nUS\t+404251-0740023\tAmerica/New_York\tEastern\n')
    cgenv = dict(env, UNISENSE_IFCONFIG=str(cg), UNISENSE_CONFIG_XML=str(work / 'config.xml'),
                 UNISENSE_ZONE_TAB=str(work / 'zone.tab'), UNISENSE_FLOW_CACHE=str(work / 'cache-cg.json'))
    rc = json.loads(run(['flows'], cgenv).stdout)
    t('home country from the time zone behind CGNAT', rc['home'] == 'AU', rc['home'])
    rc = json.loads(run(['flows'], dict(cgenv, UNISENSE_CONFIG_XML=str(work / 'nope.xml'))).stdout)
    t('no home country without a public address or time zone', rc['home'] == '', rc['home'])
    rates = [x['in_bps'] + x['out_bps'] for x in r2['countries']]
    t('countries sorted by current rate', rates == sorted(rates, reverse=True) and r2['countries'][0]['cc'] == 'SG',
      [x['cc'] for x in r2['countries']])
    t('top networks per country', us['asns'][0]['name'] == 'GOOGLE' and us['asns'][0]['conns'] == 3, us.get('asns'))
    t('connections counted once', r2['totals']['conns'] == 6, r2['totals'])
    sg = by.get('SG', {})
    t('inbound traffic as download', sg and sg['in_bps'] > sg['out_bps'] > 0, sg)
    # counters going backwards (state replaced) must not produce negative or huge rates
    c = json.loads((work / 'cache.json').read_text())
    c['ts'] -= 2.0
    (work / 'cache.json').write_text(json.dumps(c))
    sfile.write_text(states(1))
    r3 = json.loads(run(['flows'], env).stdout)
    t('counter reset gives no negative rate', all(x['in_bps'] >= 0 and x['out_bps'] >= 0 for x in r3['countries']))

    print('history (all countries ever seen)')
    hist = work / 'hist.json'
    henv = dict(env, UNISENSE_FLOW_CACHE=str(work / 'cache-h.json'), UNISENSE_HISTORY=str(hist),
                UNISENSE_HISTORY_FLUSH='0')
    sfile.write_text(states(1))
    base = {x['cc']: x for x in json.loads(run(['flows'], dict(henv, UNISENSE_FLOW_CACHE=str(work / 'cache-x.json'),
                                                                UNISENSE_HISTORY=str(work / 'hist-x.json'))).stdout)['countries']}
    r = run(['sample'], henv)
    t('sample runs quietly', r.returncode == 0 and r.stdout == '', r.stdout + r.stderr)
    h = json.loads(hist.read_text())
    us = h['c'].get('US')
    t('first sample counts every connection in full', us and us[0] == base['US']['bytes_in'] and us[1] == base['US']['bytes_out'] and us[2] == 3,
      (us, base.get('US')))
    t('history start recorded', h['since'] > 0)
    sfile.write_text(states(3))
    run(['sample'], henv)
    us3 = json.loads(hist.read_text())['c']['US']
    t('later samples add only what moved since (no double counting)', us3[0] == base['US']['bytes_in'] * 3 and us3[2] == 3, us3)
    sfile.write_text(states(1))
    run(['sample'], henv)
    us4 = json.loads(hist.read_text())['c']['US']
    t('a replaced state (counters went back) counts as a new connection', us4[2] == 6 and us4[0] == us3[0] + base['US']['bytes_in'], us4)
    lazy = dict(henv, UNISENSE_HISTORY_FLUSH='3600')
    sfile.write_text(states(2))
    run(['sample'], lazy)
    t('disk writes are batched (at most once a minute)', json.loads(hist.read_text())['c']['US'] == us4)
    hv = json.loads(run(['history'], lazy).stdout)
    hus = next((x for x in hv['countries'] if x['cc'] == 'US'), {})
    t('history output includes the not yet written part', hus.get('bytes_in') == us4[0] + base['US']['bytes_in'], hus)
    t('history lists every country, busiest first', len(hv['countries']) >= 3 and
      [x['bytes_in'] + x['bytes_out'] for x in hv['countries']] == sorted([x['bytes_in'] + x['bytes_out'] for x in hv['countries']], reverse=True),
      [x['cc'] for x in hv['countries']])
    t('first and last seen', hus.get('first', 0) <= hus.get('last', 0) and hus.get('last', 0) > 0, hus)
    r = run(['reset'], lazy)
    hv = json.loads(run(['history'], lazy).stdout)
    t('reset starts afresh', json.loads(r.stdout)['ok'] and not hist.exists() and hv['countries'] == [], hv)

    print('robustness')
    sfile.write_text('garbage line\n  age broken\nall tcp 1.2.3.4\n')
    r = run(['flows'], env)
    t('malformed pf output does not crash', r.returncode == 0 and json.loads(r.stdout)['ok'], r.stderr)
    bad = dict(env, UNISENSE_ASN_URL=(work / 'nope.gz').as_uri(), UNISENSE_GEO_URL=(work / 'nope-{month}.gz').as_uri())
    r = run(['update', '--force'], bad)
    t('failed download keeps the old database', r.returncode == 0 and (work / 'db/asn4.bin').exists(), r.stdout + r.stderr)
    meta = json.loads((work / 'db/meta.json').read_text())
    t('errors are reported in the status', len(meta['errors']) >= 2, meta.get('errors'))

    print('\n%d passed, %d failed' % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()

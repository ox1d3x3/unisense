#!/usr/local/bin/python3
"""
    UniSense Traffic Map backend (configd: "unisense trafficmap ..." / "unisense geoip update")

    trafficmap.py update [--force]   download + compile the IP databases
    trafficmap.py flows              live traffic per country / network (JSON)
    trafficmap.py status             database status (JSON)

    Databases (in /var/db/unisense, about 40 MB):
      * iptoasn.com "ip2asn-combined" (public domain): network (AS) number,
        organisation and the AS's registration country, refreshed daily
      * DB-IP "IP to Country Lite" (CC BY 4.0, https://db-ip.com): where an
        address is actually located, refreshed when a new month is published

    Both are compiled into sorted fixed-size binary records that are searched
    in place (mmap + binary search), so a lookup never loads the database.

    Live rates come from pf: `pfctl -vvs state` lists every connection with its
    byte counters. A connection usually shows up twice (the LAN-side state and
    the NAT-ed WAN-side state); they are merged by (protocol, client, remote).
    Counters of the previous call are kept in a small cache file and the
    difference over the elapsed time is the rate.

    Copyright (c) 2026 Ox1d3x3 — BSD 2-Clause (see LICENSE)
"""
import gzip
import ipaddress
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

DB_DIR = os.environ.get('UNISENSE_DB_DIR', '/var/db/unisense')
CACHE_FILE = os.environ.get('UNISENSE_FLOW_CACHE', '/tmp/unisense-trafficmap.json')
ASN_URL = os.environ.get('UNISENSE_ASN_URL', 'https://iptoasn.com/data/ip2asn-combined.tsv.gz')
GEO_URL = os.environ.get('UNISENSE_GEO_URL', 'https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz')
PFCTL = os.environ.get('UNISENSE_PFCTL', '/sbin/pfctl')
IFCONFIG = os.environ.get('UNISENSE_IFCONFIG', '/sbin/ifconfig')

# record layouts (big-endian, so byte order == numeric order)
GEO4 = struct.Struct('>II2s')            # start, end, country
GEO6 = struct.Struct('>16s16s2s')
ASN4 = struct.Struct('>IIII2s')          # start, end, asn, name offset, country
ASN6 = struct.Struct('>16s16sII2s')

TOP_ASNS = 5


# --------------------------------------------------------------- helpers
def log(msg):
    sys.stderr.write('unisense trafficmap: %s\n' % msg)


def write_atomic(path, data, mode='w'):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', prefix='.tmp-')
    try:
        with os.fdopen(fd, mode) as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def download(url, dest):
    """fetch(1) on FreeBSD (system CA store), urllib elsewhere / for file:// in tests"""
    if url.startswith('http') and os.path.exists('/usr/bin/fetch'):
        r = subprocess.run(['/usr/bin/fetch', '-q', '-T', '120', '-o', dest, url], capture_output=True, text=True)
        if r.returncode != 0:
            raise IOError((r.stderr or 'fetch failed').strip())
    else:
        req = urllib.request.Request(url, headers={'User-Agent': 'UniSense-OPNsense'})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, 'wb') as f:
            shutil.copyfileobj(resp, f)
    if os.path.getsize(dest) < 1024:
        raise IOError('download too small')


def ip_bytes(text):
    """'1.2.3.4' -> (4, int) ; '2001:db8::1' -> (6, 16 bytes)"""
    a = ipaddress.ip_address(text)
    if a.version == 4:
        return 4, int(a)
    return 6, a.packed


def load_meta():
    try:
        with open(os.path.join(DB_DIR, 'meta.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------- compile
def compile_asn(src_gz):
    """ip2asn-combined.tsv.gz -> asn4.bin, asn6.bin, asn-names.txt"""
    v4, v6 = [], []
    names = {}
    blob = []
    offset = 0
    with gzip.open(src_gz, 'rt', encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 5:
                continue
            first, last, asn, cc, desc = parts[0], parts[1], parts[2], parts[3], parts[4]
            if not asn.isdigit() or asn == '0':
                continue              # "Not routed"
            cc = cc.strip().upper()
            if not re.match(r'^[A-Z]{2}$', cc):
                cc = '--'
            desc = desc.strip()[:96]
            if desc not in names:
                names[desc] = offset
                enc = desc.encode('utf-8') + b'\n'
                blob.append(enc)
                offset += len(enc)
            try:
                fam, s = ip_bytes(first)
                fam2, e = ip_bytes(last)
            except ValueError:
                continue
            if fam != fam2:
                continue
            rec = (s, e, int(asn), names[desc], cc.encode('ascii'))
            (v4 if fam == 4 else v6).append(rec)
    if len(v4) < 1000:
        raise ValueError('ip2asn data looks wrong (%d IPv4 ranges)' % len(v4))
    v4.sort()
    v6.sort()
    write_atomic(os.path.join(DB_DIR, 'asn4.bin'), b''.join(ASN4.pack(*r) for r in v4), 'wb')
    write_atomic(os.path.join(DB_DIR, 'asn6.bin'), b''.join(ASN6.pack(*r) for r in v6), 'wb')
    write_atomic(os.path.join(DB_DIR, 'asn-names.txt'), b''.join(blob), 'wb')
    return len(v4) + len(v6)


def compile_geo(src_gz):
    """dbip-country-lite CSV (start,end,cc) -> geo4.bin, geo6.bin"""
    v4, v6 = [], []
    with gzip.open(src_gz, 'rt', encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) < 3:
                continue
            cc = parts[2].strip().strip('"').upper()
            if not re.match(r'^[A-Z]{2}$', cc) or cc == 'ZZ':
                continue
            try:
                fam, s = ip_bytes(parts[0].strip('"'))
                fam2, e = ip_bytes(parts[1].strip('"'))
            except ValueError:
                continue
            if fam != fam2:
                continue
            (v4 if fam == 4 else v6).append((s, e, cc.encode('ascii')))
    if len(v4) < 1000:
        raise ValueError('country data looks wrong (%d IPv4 ranges)' % len(v4))
    v4.sort()
    v6.sort()
    write_atomic(os.path.join(DB_DIR, 'geo4.bin'), b''.join(GEO4.pack(*r) for r in v4), 'wb')
    write_atomic(os.path.join(DB_DIR, 'geo6.bin'), b''.join(GEO6.pack(*r) for r in v6), 'wb')
    return len(v4) + len(v6)


def month_candidates(now=None):
    t = time.gmtime(now or time.time())
    y, m = t.tm_year, t.tm_mon
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    return ['%04d-%02d' % (y, m), '%04d-%02d' % (py, pm)]


def cmd_update(force=False):
    os.makedirs(DB_DIR, mode=0o755, exist_ok=True)
    meta = load_meta()
    errors = []
    work = tempfile.mkdtemp(prefix='unisense-geo-')
    try:
        # networks (daily)
        try:
            src = os.path.join(work, 'asn.tsv.gz')
            download(ASN_URL, src)
            n = compile_asn(src)
            meta['asn'] = {'updated': int(time.time()), 'ranges': n, 'source': ASN_URL}
            print('networks: %d ranges' % n)
        except Exception as e:  # keep the previous database
            errors.append('networks: %s' % e)
        # locations (monthly file)
        have = meta.get('geo', {}).get('month')
        for month in month_candidates():
            if have == month and not force:
                print('countries: %s is current' % month)
                break
            try:
                src = os.path.join(work, 'geo.csv.gz')
                url = GEO_URL.format(month=month)
                download(url, src)
                n = compile_geo(src)
                meta['geo'] = {'updated': int(time.time()), 'ranges': n, 'month': month, 'source': url}
                print('countries: %d ranges (%s)' % (n, month))
                break
            except Exception as e:
                errors.append('countries %s: %s' % (month, e))
        meta['checked'] = int(time.time())
        meta['errors'] = errors
        write_atomic(os.path.join(DB_DIR, 'meta.json'), json.dumps(meta))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for e in errors:
        log(e)
    ok = os.path.exists(os.path.join(DB_DIR, 'asn4.bin')) or os.path.exists(os.path.join(DB_DIR, 'geo4.bin'))
    print('OK' if ok else 'FAILED')
    return 0 if ok else 1


# ---------------------------------------------------------------- lookup
class Table:
    """binary search over fixed-size sorted records in an mmap'ed file"""

    def __init__(self, name, layout):
        self.layout = layout
        self.mm = None
        self.n = 0
        path = os.path.join(DB_DIR, name)
        try:
            if os.path.getsize(path) >= layout.size:
                self.f = open(path, 'rb')
                self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
                self.n = len(self.mm) // layout.size
        except OSError:
            pass

    def find(self, key):
        lo, hi = 0, self.n - 1
        size, unpack = self.layout.size, self.layout.unpack_from
        while lo <= hi:
            mid = (lo + hi) // 2
            rec = unpack(self.mm, mid * size)
            if rec[0] > key:
                hi = mid - 1
            elif rec[1] < key:
                lo = mid + 1
            else:
                return rec
        return None


class Lookup:
    def __init__(self):
        self.geo4 = Table('geo4.bin', GEO4)
        self.geo6 = Table('geo6.bin', GEO6)
        self.asn4 = Table('asn4.bin', ASN4)
        self.asn6 = Table('asn6.bin', ASN6)
        self.names = None
        try:
            with open(os.path.join(DB_DIR, 'asn-names.txt'), 'rb') as f:
                self.names = f.read()
        except OSError:
            pass
        self.cache = {}

    @property
    def ready(self):
        return bool(self.geo4.n or self.asn4.n)

    def name_at(self, off):
        if self.names is None:
            return ''
        end = self.names.find(b'\n', off)
        return self.names[off:end if end >= 0 else None].decode('utf-8', 'replace')

    def __call__(self, addr):
        """-> (country, asn, organisation)"""
        if addr in self.cache:
            return self.cache[addr]
        try:
            fam, key = ip_bytes(addr)
        except ValueError:
            return ('', 0, '')
        geo = (self.geo4 if fam == 4 else self.geo6).find(key)
        asn = (self.asn4 if fam == 4 else self.asn6).find(key)
        cc = geo[2].decode() if geo else ''
        if asn:
            if not cc and asn[4] != b'--':
                cc = asn[4].decode()
            res = (cc, asn[2], self.name_at(asn[3]))
        else:
            res = (cc, 0, '')
        self.cache[addr] = res
        return res


# ---------------------------------------------------------------- states
def split_addr(text):
    """'1.2.3.4:443' / '2001:db8::1[443]' -> ('1.2.3.4', '443')"""
    if text.count(':') > 1:
        if '[' in text:
            a, p = text.split('[', 1)
            return a, p.rstrip(']')
        return text, ''
    a, _, p = text.partition(':')
    return a, p


def local_addresses():
    own = set()
    try:
        out = subprocess.run([IFCONFIG, '-a'], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return own
    for m in re.finditer(r'^\s+inet6?\s+([0-9a-fA-F:.]+)', out, re.M):
        own.add(m.group(1).split('%')[0])
    return own


_GLOBAL = {}


def is_remote(addr, own):
    """a public address that is not one of the firewall's own"""
    if addr in own:
        return False
    g = _GLOBAL.get(addr)
    if g is None:
        try:
            a = ipaddress.ip_address(addr)
            g = a.is_global and not a.is_multicast
        except ValueError:
            g = False
        _GLOBAL[addr] = g
    return g


def parse_states(text, own):
    """
    -> {key: [down_bytes, up_bytes, remote_addr]} with one entry per
       connection; down = towards us, up = towards the remote side.
    """
    conns = {}
    rec = None
    for line in text.split('\n'):
        if not line:
            continue
        if not line.startswith(' '):
            parts = line.split()
            rec = None
            if len(parts) < 6 or parts[-3] not in ('->', '<-'):
                continue
            proto = parts[1]
            left, right = parts[2], parts[-2]
            nat = parts[3][1:-1] if parts[3].startswith('(') else None
            # like core's lib/states.py: "->" lists src first, "<-" dst first
            if parts[-3] == '->':
                src, dst = left, right
            else:
                src, dst = right, left
            src_a, _ = split_addr(src)
            dst_a, _ = split_addr(dst)
            # the inner client: the pre-NAT source when this is the WAN-side state
            client = nat if (nat and parts[-3] == '->') else src
            if is_remote(dst_a, own):
                # we (or a client) opened it: byte counter 0 = src -> dst = upload
                rec = {'key': '%s|%s|%s' % (proto, client, dst), 'remote': dst_a, 'up_first': True}
            elif is_remote(src_a, own):
                # opened from the Internet (port forward, VPN, ...): counter 0 = download
                rec = {'key': '%s|%s|%s' % (proto, dst, src), 'remote': src_a, 'up_first': False}
        elif rec is not None and line.lstrip().startswith('age '):
            m = re.search(r'(\d+):(\d+) bytes', line)
            if not m:
                rec = None
                continue
            b0, b1 = int(m.group(1)), int(m.group(2))
            down, up = (b1, b0) if rec['up_first'] else (b0, b1)
            prev = conns.get(rec['key'])
            if prev is None or down + up > prev[0] + prev[1]:
                conns[rec['key']] = [down, up, rec['remote']]
            rec = None
    return conns


def read_states():
    path = os.environ.get('UNISENSE_STATES_FILE')
    if path:
        with open(path) as f:
            return f.read()
    r = subprocess.run([PFCTL, '-vvs', 'state'], capture_output=True, text=True, timeout=60)
    return r.stdout


def cmd_flows():
    now = time.time()
    own = local_addresses()
    conns = parse_states(read_states(), own)
    look = Lookup()

    try:
        with open(CACHE_FILE) as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    prev_ts = prev.get('ts', 0)
    prev_c = prev.get('c', {})
    dt = now - prev_ts if prev_ts else 0
    valid = 0.5 < dt < 120

    countries = {}
    totals = {'in_bps': 0.0, 'out_bps': 0.0, 'conns': 0, 'bytes_in': 0, 'bytes_out': 0}
    for key, (down, up, remote) in conns.items():
        cc, asn, org = look(remote) if look.ready else ('', 0, '')
        cc = cc or '??'
        c = countries.get(cc)
        if c is None:
            c = countries[cc] = {'cc': cc, 'in_bps': 0.0, 'out_bps': 0.0, 'conns': 0,
                                 'bytes_in': 0, 'bytes_out': 0, '_asn': {}}
        din = dup = 0.0
        p = prev_c.get(key)
        if valid and p is not None and down >= p[0] and up >= p[1]:
            din = (down - p[0]) * 8.0 / dt
            dup = (up - p[1]) * 8.0 / dt
        c['in_bps'] += din
        c['out_bps'] += dup
        c['conns'] += 1
        c['bytes_in'] += down
        c['bytes_out'] += up
        a = c['_asn'].get(asn)
        if a is None:
            a = c['_asn'][asn] = {'asn': asn, 'name': org, 'in_bps': 0.0, 'out_bps': 0.0, 'conns': 0, 'bytes': 0}
        a['in_bps'] += din
        a['out_bps'] += dup
        a['conns'] += 1
        a['bytes'] += down + up
        totals['in_bps'] += din
        totals['out_bps'] += dup
        totals['conns'] += 1
        totals['bytes_in'] += down
        totals['bytes_out'] += up

    for c in countries.values():
        asns = sorted(c.pop('_asn').values(), key=lambda x: (x['in_bps'] + x['out_bps'], x['bytes']), reverse=True)
        c['asns'] = [dict(x, in_bps=round(x['in_bps']), out_bps=round(x['out_bps'])) for x in asns[:TOP_ASNS]]
        c['in_bps'] = round(c['in_bps'])
        c['out_bps'] = round(c['out_bps'])
    totals['in_bps'] = round(totals['in_bps'])
    totals['out_bps'] = round(totals['out_bps'])

    # where the firewall itself is: the country of its public addresses
    home = ''
    if look.ready:
        for a in sorted(own):
            if is_remote(a, set()):
                home = look(a)[0]
                if home:
                    break

    try:
        write_atomic(CACHE_FILE, json.dumps({'ts': now, 'c': {k: v[:2] for k, v in conns.items()}}))
    except OSError:
        pass

    meta = load_meta()
    out = {
        'ok': True,
        'ts': int(now),
        'interval': round(dt, 2) if valid else 0,
        'home': home,
        'db': db_info(meta, look),
        'totals': totals,
        'countries': sorted(countries.values(), key=lambda c: (c['in_bps'] + c['out_bps'], c['bytes_in'] + c['bytes_out']),
                            reverse=True),
    }
    print(json.dumps(out, separators=(',', ':')))
    return 0


def db_info(meta, look=None):
    look = look or Lookup()
    return {
        'ready': look.ready,
        'geo': meta.get('geo'),
        'asn': meta.get('asn'),
        'checked': meta.get('checked'),
        'errors': meta.get('errors', []),
    }


def cmd_status():
    print(json.dumps({'ok': True, 'db': db_info(load_meta())}, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'update':
        sys.exit(cmd_update('--force' in sys.argv))
    elif cmd == 'flows':
        sys.exit(cmd_flows())
    elif cmd == 'status':
        sys.exit(cmd_status())
    print('usage: trafficmap.py update [--force] | flows | status')
    sys.exit(2)

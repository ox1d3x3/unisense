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
import fcntl
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
CONFIG_XML = os.environ.get('UNISENSE_CONFIG_XML', '/conf/config.xml')
ZONE_TAB = os.environ.get('UNISENSE_ZONE_TAB', '/usr/share/zoneinfo/zone.tab')
# every country the firewall has talked to: written at most once a minute
HISTORY_FILE = os.environ.get('UNISENSE_HISTORY', os.path.join(DB_DIR, 'trafficmap-history.json'))
HISTORY_FLUSH = int(os.environ.get('UNISENSE_HISTORY_FLUSH', '60'))  # seconds between writes
HISTORY_KEEP = 90 * 86400   # forget countries not seen for 90 days

# record layouts (big-endian, so byte order == numeric order)
GEO4 = struct.Struct('>II2s')            # start, end, country
GEO6 = struct.Struct('>16s16s2s')
ASN4 = struct.Struct('>IIII2s')          # start, end, asn, name offset, country
ASN6 = struct.Struct('>16s16sII2s')

TOP_ASNS = 5
CONNS_MAX = 300             # connections listed per country


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
    -> {key: [down_bytes, up_bytes, remote_addr, age_seconds]} with one
       entry per connection; down = towards us, up = towards the remote side.
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
            a = re.search(r'age (\d+):(\d+):(\d+)', line)
            age = int(a.group(1)) * 3600 + int(a.group(2)) * 60 + int(a.group(3)) if a else 0
            prev = conns.get(rec['key'])
            if prev is None or down + up > prev[0] + prev[1]:
                conns[rec['key']] = [down, up, rec['remote'], age]
            rec = None
    return conns


def read_states():
    path = os.environ.get('UNISENSE_STATES_FILE')
    if path:
        with open(path) as f:
            return f.read()
    r = subprocess.run([PFCTL, '-vvs', 'state'], capture_output=True, text=True, timeout=60)
    return r.stdout


def timezone_country():
    """
    The country of the firewall's configured time zone (System > Settings >
    General), for when it has no public address of its own (CGNAT, a modem
    in router mode, ...). zone.tab maps 'Australia/Perth' -> 'AU'.
    """
    try:
        with open(CONFIG_XML, errors='replace') as f:
            m = re.search(r'<timezone>\s*([^<\s]+)\s*</timezone>', f.read(262144))
        if not m:
            return ''
        tz = m.group(1)
        with open(ZONE_TAB, errors='replace') as f:
            for line in f:
                parts = line.split('\t')
                if len(parts) >= 3 and not line.startswith('#') and parts[2].strip() == tz:
                    return parts[0].strip().upper()
    except OSError:
        pass
    return ''


class Locked:
    """serialise the page's polls and the cron sampler (they share the cache)"""

    def __enter__(self):
        try:
            self.f = open(CACHE_FILE + '.lock', 'a')
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except OSError:
            self.f = None
        return self

    def __exit__(self, *a):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


def load_json(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def load_history():
    h = load_json(HISTORY_FILE, {})
    h.setdefault('since', 0)
    h.setdefault('c', {})
    return h


def snapshot():
    """
    Read the state table once and compare it with the previous snapshot:
    rates for the live view, and per-country byte/connection counts added to
    the history. Bytes are counted from the previous snapshot's counters, so a
    connection is never counted twice; connections that start and finish
    between two snapshots are missed (the cron sampler runs every minute).
    """
    now = time.time()
    own = local_addresses()
    conns = parse_states(read_states(), own)
    look = Lookup()

    prev = load_json(CACHE_FILE, {})
    prev_ts = prev.get('ts', 0)
    prev_c = prev.get('c', {})
    dt = now - prev_ts if prev_ts else 0
    valid = 0.5 < dt < 120
    pending = prev.get('p', {})          # history not yet written to disk
    flushed = prev.get('f', 0)

    countries = {}
    totals = {'in_bps': 0.0, 'out_bps': 0.0, 'conns': 0, 'bytes_in': 0, 'bytes_out': 0}
    for key, (down, up, remote, _age) in conns.items():
        cc, asn, org = look(remote) if look.ready else ('', 0, '')
        cc = cc or '??'
        c = countries.get(cc)
        if c is None:
            c = countries[cc] = {'cc': cc, 'in_bps': 0.0, 'out_bps': 0.0, 'conns': 0,
                                 'bytes_in': 0, 'bytes_out': 0, '_asn': {}}
        din = dup = 0.0
        p = prev_c.get(key)
        if p is not None and down >= p[0] and up >= p[1]:
            gin, gout, new = down - p[0], up - p[1], 0
            if valid:
                din = gin * 8.0 / dt
                dup = gout * 8.0 / dt
        else:
            gin, gout, new = down, up, 1
        if look.ready and (gin or gout or new):
            h = pending.get(cc)
            if h is None:
                h = pending[cc] = [0, 0, 0]
            h[0] += gin
            h[1] += gout
            h[2] += new
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

    # countries seen in this snapshot are "last seen now", even when idle
    for cc in countries:
        if look.ready:
            pending.setdefault(cc, [0, 0, 0])
    seen = sorted(countries)

    if now - flushed >= HISTORY_FLUSH:
        if merge_history(pending, seen if look.ready else [], now):
            pending, flushed = {}, now
    try:
        write_atomic(CACHE_FILE, json.dumps({'ts': now, 'c': {k: v[:2] for k, v in conns.items()},
                                             'p': pending, 'f': flushed, 's': seen}))
    except OSError:
        pass
    return now, dt, valid, countries, totals, own, look


def merge_history(pending, seen, now):
    h = load_history()
    if not h['since']:
        h['since'] = int(now)
    for cc, (bin_, bout, nc) in pending.items():
        e = h['c'].get(cc)
        if e is None:
            e = h['c'][cc] = [0, 0, 0, int(now), int(now)]
        e[0] += int(bin_)
        e[1] += int(bout)
        e[2] += int(nc)
        e[4] = int(now)
    for cc in seen:
        if cc in h['c']:
            h['c'][cc][4] = int(now)
    h['c'] = {cc: e for cc, e in h['c'].items() if now - e[4] < HISTORY_KEEP}
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        write_atomic(HISTORY_FILE, json.dumps(h, separators=(',', ':')))
        return True
    except OSError:
        return False


def cmd_flows():
    with Locked():
        now, dt, valid, countries, totals, own, look = snapshot()

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
    if not home:
        home = timezone_country()

    meta = load_meta()
    out = {
        'ok': True,
        'ts': int(now),
        'interval': round(dt, 2) if valid else 0,
        'home': home,
        'blocked': shield_countries(),
        'db': db_info(meta, look),
        'totals': totals,
        'countries': sorted(countries.values(), key=lambda c: (c['in_bps'] + c['out_bps'], c['bytes_in'] + c['bytes_out']),
                            reverse=True),
    }
    print(json.dumps(out, separators=(',', ':')))
    return 0


SERVICES = {
    20: 'FTP data', 21: 'FTP', 22: 'SSH', 25: 'SMTP', 53: 'DNS', 80: 'HTTP', 110: 'POP3', 123: 'NTP', 143: 'IMAP',
    443: 'HTTPS', 465: 'SMTPS', 587: 'Mail submission', 853: 'DNS over TLS', 993: 'IMAPS', 995: 'POP3S',
    1194: 'OpenVPN', 1883: 'MQTT', 3074: 'Xbox Live', 3478: 'STUN/TURN', 3479: 'STUN/TURN', 3480: 'STUN/TURN',
    5222: 'XMPP', 5223: 'Apple push', 5228: 'Google push', 8080: 'HTTP alt', 8443: 'HTTPS alt', 8883: 'MQTT TLS',
    9993: 'ZeroTier', 19302: 'Google STUN', 51820: 'WireGuard', 500: 'IPsec IKE', 4500: 'IPsec NAT-T',
}


def service_name(proto, port):
    try:
        p = int(port)
    except ValueError:
        return ''
    if p in SERVICES:
        return SERVICES[p]
    if 27000 <= p <= 27100:
        return 'Steam'
    if 3478 <= p <= 3497:
        return 'Game/voice (STUN)'
    return ''


class Flags:
    """addresses on a Shield list (shield.py writes the lists as sorted ranges)"""

    def __init__(self):
        self.t = [(Table(os.path.join('shield', '%s%d.bin' % (k, v)), GEO4 if v == 4 else GEO6), v, k)
                  for k in ('threat', 'scanner') for v in (4, 6)]

    def __call__(self, addr):
        try:
            fam, key = ip_bytes(addr)
        except ValueError:
            return ''
        for table, v, kind in self.t:
            if v == fam and table.n and table.find(key):
                return kind
        return ''


def shield_countries():
    """the countries Shield blocks (as last applied)"""
    st = load_json(os.path.join(DB_DIR, 'shield', 'state.json'), {})
    c = st.get('countries', [])
    return [x for x in c if isinstance(x, str) and re.match(r'^[A-Z]{2}$', x)] if isinstance(c, list) else []


def cmd_conns(cc):
    """the connections behind one country (or 'all'): who, to where, how much"""
    cc = (cc or '').upper()
    if not re.match(r'^([A-Z]{2}|\?\?|ALL)$', cc):
        print(json.dumps({'ok': False, 'error': 'invalid country'}))
        return 0
    now = time.time()
    own = local_addresses()
    conns = parse_states(read_states(), own)
    look = Lookup()
    prev = load_json(CACHE_FILE, {})
    prev_c = prev.get('c', {})
    dt = now - prev.get('ts', 0) if prev.get('ts') else 0
    valid = 0.5 < dt < 120
    rows = []
    flags = Flags()
    for key, (down, up, remote, age) in conns.items():
        rcc, asn, org = look(remote) if look.ready else ('', 0, '')
        rcc = rcc or '??'
        if cc != 'ALL' and rcc != cc:
            continue
        proto, local, rem = key.split('|', 2)
        laddr, lport = split_addr(local)
        raddr, rport = split_addr(rem)
        rin = rout = 0
        p = prev_c.get(key)
        if valid and p is not None and down >= p[0] and up >= p[1]:
            rin = round((down - p[0]) * 8.0 / dt)
            rout = round((up - p[1]) * 8.0 / dt)
        rows.append({'proto': proto, 'local': laddr, 'lport': lport, 'remote': raddr, 'rport': rport,
                     'service': service_name(proto, rport), 'cc': rcc, 'asn': asn, 'org': org,
                     'bytes_in': down, 'bytes_out': up, 'in_bps': rin, 'out_bps': rout, 'age': age,
                     'flag': flags(raddr)})
    rows.sort(key=lambda r: (r['in_bps'] + r['out_bps'], r['bytes_in'] + r['bytes_out']), reverse=True)
    out = {'ok': True, 'ts': int(now), 'cc': cc, 'total': len(rows), 'interval': round(dt, 2) if valid else 0,
           'conns': rows[:CONNS_MAX]}
    print(json.dumps(out, separators=(',', ':')))
    return 0


def cmd_sample():
    """cron, every minute: keep the history going while nobody has the page open"""
    with Locked():
        snapshot()
    return 0


def cmd_history():
    """every country seen since the history started, newest data included"""
    h = load_history()
    cache = load_json(CACHE_FILE, {})
    now = int(time.time())
    merged = {cc: list(e) for cc, e in h['c'].items()}
    for cc, (bin_, bout, nc) in cache.get('p', {}).items():
        e = merged.get(cc)
        if e is None:
            e = merged[cc] = [0, 0, 0, int(cache.get('ts', now)), int(cache.get('ts', now))]
        e[0] += int(bin_)
        e[1] += int(bout)
        e[2] += int(nc)
        e[4] = max(e[4], int(cache.get('ts', now)))
    out = {
        'ok': True,
        'ts': now,
        'since': h['since'] or (int(cache['ts']) if cache.get('ts') and merged else 0),
        'countries': sorted(({'cc': cc, 'bytes_in': e[0], 'bytes_out': e[1], 'conns': e[2], 'first': e[3], 'last': e[4]}
                             for cc, e in merged.items()), key=lambda c: c['bytes_in'] + c['bytes_out'], reverse=True),
    }
    print(json.dumps(out, separators=(',', ':')))
    return 0


def cmd_reset():
    try:
        os.unlink(HISTORY_FILE)
    except OSError:
        pass
    with Locked():
        cache = load_json(CACHE_FILE, {})
        cache['p'] = {}
        cache['f'] = time.time()
        try:
            write_atomic(CACHE_FILE, json.dumps(cache))
        except OSError:
            pass
    print(json.dumps({'ok': True}))
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
    elif cmd == 'sample':
        sys.exit(cmd_sample())
    elif cmd == 'history':
        sys.exit(cmd_history())
    elif cmd == 'reset':
        sys.exit(cmd_reset())
    elif cmd == 'conns':
        sys.exit(cmd_conns(sys.argv[2] if len(sys.argv) > 2 else ''))
    elif cmd == 'status':
        sys.exit(cmd_status())
    print('usage: trafficmap.py update [--force] | flows | conns CC | sample | history | reset | status')
    sys.exit(2)

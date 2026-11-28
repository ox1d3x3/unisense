#!/usr/local/bin/python3
"""
    UniSense Shield backend (configd: "unisense shield ...")

    shield.py status            settings, lists, table sizes, block counters (JSON)
    shield.py apply [kill]      rebuild the pf tables from the settings and the
                                downloaded lists; --kill also ends the open
                                connections to newly blocked countries
    shield.py update [--force]  download the threat lists that are due, then apply
    shield.py hits              the latest blocks (JSON)
    shield.py alerts            devices that tried to reach a known-bad address (JSON)
    shield.py ack               mark the alerts as seen
    shield.py check <ip>        everything known about one address (JSON)
    shield.py checkup           a short security check of the firewall's settings (JSON)
    shield.py clear             empty every Shield table
    shield.py allow <ip> <for>  let an address through: 1h, 6h, 24h, 1mo or restart
    shield.py unallow <ip>      take a timed exception out
    shield.py dismiss <device> <remote>   hide an alert until that device tries again
    shield.py requests          unblock requests from the block page
    shield.py request add       (JSON on stdin) a new request; request approve <id> <for|forever>; request decline <id>
    shield.py unallow-domain <domain>     end an approved website early

    How it blocks. Three pf tables, each behind "External" aliases and quick
    floating rules that UniSense adds to the firewall (see Shield.php):

      unisense_geoblock   blocked countries        dropped inbound, refused for
      unisense_threats    botnet / criminal lists  your devices (instant error,
                                                   so nothing hangs)
      unisense_scanners   scanner / attacker lists dropped inbound only, so a
                                                   false positive can never break
                                                   an outgoing (game) connection

    Blocked countries apply whenever the list is not empty; the threat lists have
    their own switch ("enabled").

    A table lookup is a radix-tree search inside pf: no packet inspection and no
    measurable latency. Private, reserved and the firewall's own addresses are
    never added, and allow-listed addresses are added as "!address" exceptions.

    Countries come from the Traffic Map's DB-IP country database. The lists are
    free public feeds (see FEEDS); each has its own refresh interval and a bad
    download never replaces a good copy. Blocks are counted from the firewall
    log: every Shield rule logs, and its log lines carry the rule's id.

    Copyright (c) 2026 Ox1d3x3 — BSD 2-Clause (see LICENSE)
"""
import bisect
import datetime
import fcntl
import glob
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import ssl
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trafficmap as tm  # noqa: E402  (IP databases, pf state parsing)

SHIELD_DIR = os.environ.get('UNISENSE_SHIELD_DIR', os.path.join(tm.DB_DIR, 'shield'))
STATE_FILE = os.path.join(SHIELD_DIR, 'state.json')
LOOKUPS_FILE = os.path.join(SHIELD_DIR, 'lookups.json')
REQUESTS_FILE = os.path.join(SHIELD_DIR, 'requests.json')
PRIVACY_PHP = os.environ.get('UNISENSE_PRIVACY_PHP', '/usr/local/opnsense/scripts/unisense/setup_privacy.php')
PRIVACY_PY = os.environ.get('UNISENSE_PRIVACY_PY', '/usr/local/opnsense/scripts/unisense/privacy.py')
LOG_DIR = os.environ.get('UNISENSE_FILTER_LOG_DIR', '/var/log/filter')
CURL = os.environ.get('UNISENSE_CURL', '/usr/local/bin/curl')
NOW = None  # tests pin the clock

TABLES = {'country': 'unisense_geoblock', 'threat': 'unisense_threats', 'scanner': 'unisense_scanners'}
KINDS = ('country-in', 'country-out', 'threat-in', 'threat-out', 'scanner-in')

M = 60
H = 3600
FEEDS = [
    {'id': 'spamhaus', 'name': 'Spamhaus DROP', 'kind': 'threat', 'default': True, 'every': 6 * H,
     'about': 'Hijacked and criminal networks that should never be reached at all.',
     'home': 'https://www.spamhaus.org/drop/',
     'urls': ['https://www.spamhaus.org/drop/drop_v4.json', 'https://www.spamhaus.org/drop/drop_v6.json']},
    {'id': 'feodo', 'name': 'abuse.ch Feodo Tracker', 'kind': 'threat', 'default': True, 'every': 15 * M,
     'about': 'Active botnet command servers (Emotet, QakBot, Dridex …); often only a handful at a time.',
     'home': 'https://feodotracker.abuse.ch/blocklist/',
     'urls': ['https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt']},
    {'id': 'et', 'name': 'Emerging Threats compromised hosts', 'kind': 'scanner', 'default': True, 'every': H,
     'about': 'Hosts known to be compromised and attacking others.',
     'home': 'https://rules.emergingthreats.net/',
     'urls': ['https://rules.emergingthreats.net/blockrules/compromised-ips.txt']},
    {'id': 'cins', 'name': 'CINS Army', 'kind': 'scanner', 'default': True, 'every': H,
     'about': 'Scanners and attackers seen by the Sentinel IPS network.',
     'home': 'https://cinsscore.com/',
     'urls': ['https://cinsscore.com/list/ci-badguys.txt']},
    {'id': 'dshield', 'name': 'DShield top attackers', 'kind': 'scanner', 'default': True, 'every': H,
     'about': 'The networks attacking the most right now (SANS Internet Storm Center).',
     'home': 'https://isc.sans.edu/', 'format': 'dshield',
     'urls': ['https://feeds.dshield.org/block.txt']},
    {'id': 'blocklistde', 'name': 'blocklist.de', 'kind': 'scanner', 'default': False, 'every': H,
     'about': 'Hosts reported for SSH, mail and web attacks in the last 48 hours.',
     'home': 'https://www.blocklist.de/',
     'urls': ['https://lists.blocklist.de/lists/all.txt']},
    {'id': 'tor', 'name': 'Tor exit nodes', 'kind': 'scanner', 'default': False, 'every': H,
     'about': 'Connections coming from the Tor network (your own Tor use is not affected).',
     'home': 'https://www.torproject.org/',
     'urls': ['https://check.torproject.org/torbulkexitlist']},
]
FEED_BY_ID = {f['id']: f for f in FEEDS}

RECENT_MAX = 200
ALERTS_MAX = 100
REQ_NAME_MAX = 60
REQ_WORDS_MAX = 100
REQ_CHARS_MAX = 800
REQ_PER_HOUR = 5
REQ_PENDING_PER_IP = 10
REQ_MAX = 200
REQ_KEEP = 30 * 24 * 3600
DOMAIN_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$')
CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
DAYS_KEEP = 30
READ_MAX = 32 * 1024 * 1024        # log bytes read per run
FIRST_READ = 4 * 1024 * 1024       # on the very first run: the tail of today's log
LOOKUP_TTL = 24 * H


def log(msg):
    sys.stderr.write('unisense shield: %s\n' % msg)


def now():
    return NOW if NOW is not None else time.time()


def out(obj):
    print(json.dumps(obj, separators=(',', ':')))
    return 0


def load_json(path, default):
    return tm.load_json(path, default)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tm.write_atomic(path, json.dumps(obj, separators=(',', ':')))


class Locked:
    """one writer at a time: the cron job, the page's polls and apply"""

    def __enter__(self):
        os.makedirs(SHIELD_DIR, exist_ok=True)
        self.f = open(os.path.join(SHIELD_DIR, '.lock'), 'a')
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---------------------------------------------------------------- settings
def config_root():
    try:
        return ET.parse(tm.CONFIG_XML).getroot()
    except (OSError, ET.ParseError):
        return None


def text(node, path, default=''):
    if node is None:
        return default
    el = node.find(path)
    return (el.text or '').strip() if el is not None and el.text is not None else default


def split_list(value):
    return [v for v in re.split(r'[\s,]+', value or '') if v]


def settings(root=None):
    root = config_root() if root is None else root
    node = root.find('OPNsense/unisense/shield') if root is not None else None
    # no <feeds> element yet: the recommended lists; an empty one: none
    feeds = text(node, 'feeds') if node is not None and node.find('feeds') is not None else None
    rules = {}
    for item in split_list(text(node, 'rules')):
        kind, _, uuid = item.partition(':')
        if kind in KINDS and re.match(r'^[0-9a-f-]{36}$', uuid):
            rules[uuid] = kind
    return {
        'enabled': text(node, 'enabled') == '1',
        'countries': sorted({c.upper() for c in split_list(text(node, 'countries')) if re.match(r'^[A-Za-z]{2}$', c)}),
        'feeds': [f for f in (split_list(feeds) if feeds is not None else
                              [f['id'] for f in FEEDS if f['default']]) if f in FEED_BY_ID],
        'allow': split_list(text(node, 'allowlist')),
        'vtkey': text(node, 'vtkey'),
        'abusekey': text(node, 'abuseipdbkey'),
        'rules': rules,
    }


def interface_names(root):
    """igb0 -> 'WAN' (the description, or the interface key)"""
    names = {}
    ifs = root.find('interfaces') if root is not None else None
    if ifs is not None:
        for el in ifs:
            dev = text(el, 'if')
            if dev:
                names[dev] = text(el, 'descr') or el.tag.upper()
    return names


# ---------------------------------------------------------------- networks
# never blocked, whatever a list says: private, shared (CGNAT), link-local,
# loopback and multicast space (also added to every table as "!" exceptions)
NEVER = [ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4',
    '::1/128', 'fc00::/7', 'fe80::/10', 'ff00::/8')]
NEVER_ENTRIES = ['!' + str(n) for n in NEVER]


def usable(net):
    """a public network that is safe to put in a block table (no overlap with NEVER)"""
    if net.version == 4 and net.prefixlen < 8:
        return False
    if net.version == 6 and net.prefixlen < 19:
        return False
    if not net.is_global or net.is_multicast:
        return False
    return not any(net.version == n.version and net.overlaps(n) for n in NEVER)


def parse_feed(data, fmt='', stats=None):
    """the networks in one downloaded list (text, JSON lines or DShield's tab format)"""
    nets = []
    bad = 0
    for line in data.splitlines():
        line = line.strip()
        if not line or line[0] in '#;/':
            continue
        cand = None
        if line.startswith('{'):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cand = rec.get('cidr') if isinstance(rec, dict) else None
        elif fmt == 'dshield':
            parts = line.split('\t')
            if len(parts) >= 3 and parts[2].strip().isdigit():
                cand = '%s/%s' % (parts[0].strip(), parts[2].strip())
        else:
            cand = re.split(r'[\s,;|]+', line, 1)[0]
        if not cand:
            continue
        try:
            net = ipaddress.ip_network(cand, strict=False)
        except ValueError:
            bad += 1
            continue
        if usable(net):
            nets.append(net)
    if stats is not None:
        stats['bad'] = stats.get('bad', 0) + bad
        stats['read'] = stats.get('read', 0) + len(nets)
    return collapse(nets)


def collapse(nets):
    v4 = [n for n in nets if n.version == 4]
    v6 = [n for n in nets if n.version == 6]
    return list(ipaddress.collapse_addresses(v4)) + list(ipaddress.collapse_addresses(v6))


def country_networks(countries):
    """every range of the given countries in the Traffic Map's DB-IP database"""
    want = {c.encode('ascii') for c in countries}
    nets = []
    if not want:
        return nets
    look = tm.Lookup()
    for table, ver in ((look.geo4, 4), (look.geo6, 6)):
        size, unpack, mm = table.layout.size, table.layout.unpack_from, table.mm
        for i in range(table.n):
            s, e, cc = unpack(mm, i * size)
            if cc not in want:
                continue
            if ver == 4:
                first, last = ipaddress.IPv4Address(s), ipaddress.IPv4Address(e)
            else:
                first, last = ipaddress.IPv6Address(s), ipaddress.IPv6Address(e)
            nets.extend(n for n in ipaddress.summarize_address_range(first, last) if usable(n))
    return collapse(nets)


# timed exceptions ("Allow for …"): seconds; 0 = until the firewall restarts
DURATIONS = {'1h': H, '6h': 6 * H, '24h': 24 * H, '1mo': 30 * 24 * H, 'restart': 0}


def boot_id():
    """identifies this boot (FreeBSD kern.boottime; tests set UNISENSE_BOOT_ID)"""
    if os.environ.get('UNISENSE_BOOT_ID'):
        return os.environ['UNISENSE_BOOT_ID']
    try:
        r = subprocess.run(['/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, timeout=5)
        m = re.search(r'sec = (\d+)', r.stdout)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with open('/proc/stat') as f:
            for line in f:
                if line.startswith('btime'):
                    return line.split()[1]
    except OSError:
        pass
    return ''


def prune_allows(st):
    """drop timed exceptions that ran out, and "until restart" ones from an earlier boot; -> changed"""
    allows = st.get('allows', {})
    boot = None
    keep = {}
    for net, a in allows.items():
        if a.get('until') and a['until'] <= now():
            continue
        if a.get('dur') == 'restart':
            boot = boot_id() if boot is None else boot
            if a.get('boot') != boot:
                continue
        keep[net] = a
    changed = len(keep) != len(allows)
    if changed:
        st['allows'] = keep
    return changed


def prune_domains(st):
    """timed website approvals that ran out -> their domains"""
    allows = st.get('domain_allows', {})
    gone = [d for d, a in allows.items() if a.get('until') and a['until'] <= now()]
    for d in gone:
        del allows[d]
    return gone


def privacy_provider():
    root = config_root()
    return text(root.find('OPNsense/unisense/privacy') if root is not None else None, 'provider', 'off')


def privacy_changed(domains, action):
    """tell Privacy about approved / expired websites: Unbound re-reads them from the state, AdGuard Home gets rules"""
    if not domains:
        return
    try:
        if privacy_provider() == 'adguard':
            for d in domains:
                subprocess.run([sys.executable, PRIVACY_PY, 'rule', action, d], capture_output=True, timeout=60)
        else:
            subprocess.run([PRIVACY_PHP, 'sync', 'apply'], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log('could not update Privacy: %s' % e)


def allow_entries(cfg, own=(), temp=()):
    """'!address' exceptions: the allow list, timed exceptions and the firewall's own public addresses"""
    res = []
    for a in list(cfg['allow']) + list(temp) + sorted(own):
        try:
            net = ipaddress.ip_network(a, strict=False)
        except ValueError:
            continue
        if net.is_global:
            res.append('!' + str(net))
    return sorted(set(res))


class Lists:
    """which downloaded list contains an address (binary search per list)"""

    def __init__(self, feed_ids):
        self.idx = []
        for fid in feed_ids:
            ranges = {4: [], 6: []}
            for line in read_lines(feed_path(fid)):
                try:
                    n = ipaddress.ip_network(line, strict=False)
                except ValueError:
                    continue
                ranges[n.version].append((int(n.network_address), int(n.broadcast_address)))
            for v in ranges:
                ranges[v].sort()
            self.idx.append((fid, {v: ([r[0] for r in rs], rs) for v, rs in ranges.items()}))

    def find(self, addr):
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            return []
        k = int(a)
        hits = []
        for fid, fam in self.idx:
            starts, rs = fam[a.version]
            i = bisect.bisect_right(starts, k) - 1
            if i >= 0 and rs[i][0] <= k <= rs[i][1]:
                hits.append(fid)
        return hits


# ---------------------------------------------------------------- downloads
def feed_path(fid):
    return os.path.join(SHIELD_DIR, 'feeds', fid + '.txt')


def read_lines(path):
    try:
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return []


def fetch_text(url, headers=None, timeout=60):
    """FreeBSD fetch(1) or curl on the firewall (system CA store); urllib elsewhere"""
    if url.startswith('http') and not headers and os.path.exists('/usr/bin/fetch'):
        r = subprocess.run(['/usr/bin/fetch', '-q', '-T', str(timeout), '-o', '-', url],
                           capture_output=True, timeout=timeout + 15)
        if r.returncode != 0:
            raise IOError((r.stderr.decode('utf-8', 'replace') or 'download failed').strip()[:200])
        return r.stdout.decode('utf-8', 'replace')
    if url.startswith('http') and os.path.exists(CURL):
        cmd = [CURL, '-sS', '-f', '-L', '--max-time', str(timeout), '-A', 'UniSense-OPNsense']
        for k, v in (headers or {}).items():
            cmd += ['-H', '%s: %s' % (k, v)]
        r = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 15)
        if r.returncode != 0:
            raise IOError((r.stderr.decode('utf-8', 'replace') or 'download failed').strip()[:200])
        return r.stdout.decode('utf-8', 'replace')
    req = urllib.request.Request(url, headers=dict({'User-Agent': 'UniSense-OPNsense'}, **(headers or {})))
    ctx = None
    for ca in ('/usr/local/etc/ssl/cert.pem', '/etc/ssl/cert.pem', '/usr/local/share/certs/ca-root-nss.crt'):
        if url.startswith('https') and os.path.exists(ca):
            ctx = ssl.create_default_context(cafile=ca)
            break
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode('utf-8', 'replace')


def update_feed(feed, meta):
    """download one list; keep the previous copy when the new one looks broken"""
    nets = []
    seen = {}
    for url in feed['urls']:
        nets.extend(parse_feed(fetch_text(url), feed.get('format', ''), seen))
    nets = collapse(nets)
    if seen.get('bad', 0) > seen.get('read', 0):
        # an error page, a login page, a changed format: not a list
        raise IOError('the download is not an address list; keeping the previous list')
    prev = meta.get('entries', 0)
    if prev >= 1000 and len(nets) < prev // 10:
        raise IOError('only %d entries (was %d); keeping the previous list' % (len(nets), prev))
    os.makedirs(os.path.dirname(feed_path(feed['id'])), exist_ok=True)
    tm.write_atomic(feed_path(feed['id']), ''.join(str(n) + '\n' for n in nets))
    return len(nets)


# ---------------------------------------------------------------- pf
def pfctl(*args, data=None):
    r = subprocess.run([tm.PFCTL] + list(args), input=data, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout or ''), (r.stderr or '')


def table_limit():
    rc, so, _ = pfctl('-sm')
    m = re.search(r'table-entries\s+hard limit\s+(\d+)', so)
    return int(m.group(1)) if m else 0


def own_public():
    return {a for a in tm.local_addresses() if tm.is_remote(a, set())}


def build_tables(cfg, own, temp=()):
    """-> {table: [entries]} for the current settings (countries and lists switch on separately)"""
    allow = allow_entries(cfg, own, temp)
    lists = {'threat': [], 'scanner': []}
    for fid in cfg['feeds'] if cfg['enabled'] else []:
        lists[FEED_BY_ID[fid]['kind']].extend(ipaddress.ip_network(x, strict=False) for x in read_lines(feed_path(fid)))
    res = {}
    for key, table in TABLES.items():
        nets = country_networks(cfg['countries']) if key == 'country' else collapse(lists[key])
        res[table] = [str(n) for n in nets] + (allow + NEVER_ENTRIES if nets else [])
    return res


def load_tables(tables, st):
    """pfctl -T replace, only for tables that changed (or are empty in pf after a reboot)"""
    errors = []
    applied = st.setdefault('tables', {})
    for table, entries in tables.items():
        digest = hashlib.sha1('\n'.join(entries).encode()).hexdigest()
        rc, so, _ = pfctl('-t', table, '-T', 'show')
        loaded = len([ln for ln in so.splitlines() if ln.strip()]) if rc == 0 else -1
        want = len(entries)
        if not want and loaded <= 0:
            continue                      # nothing to block and nothing loaded: leave pf alone
        if applied.get(table, {}).get('sha') == digest and loaded == want:
            continue
        path = os.path.join(SHIELD_DIR, 'tables', table + '.txt')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tm.write_atomic(path, ''.join(e + '\n' for e in entries))
        rc, so, se = pfctl('-t', table, '-T', 'replace', '-f', path)
        if rc != 0:
            msg = (se.strip().splitlines() or ['pfctl failed'])[-1]
            if 'memory' in msg.lower() or 'limit' in msg.lower():
                msg = ('too many addresses for the firewall (%d): raise Firewall > Settings > Advanced > '
                       'Firewall Maximum Table Entries' % want)
            errors.append('%s: %s' % (table, msg))
            applied[table] = {'sha': '', 'n': 0, 'error': msg}
        else:
            applied[table] = {'sha': digest, 'n': want, 'ts': int(now())}
    return errors


def write_flags(tables):
    """
    the list tables as sorted binary ranges, in the Traffic Map's record layout,
    so its connection list can mark listed addresses without loading the lists
    """
    for key, code in (('threat', b'TH'), ('scanner', b'SC')):
        nets = []
        for e in tables[TABLES[key]]:
            if not e.startswith('!'):
                nets.append(ipaddress.ip_network(e, strict=False))
        for ver, layout in ((4, tm.GEO4), (6, tm.GEO6)):
            recs = []
            for n in ipaddress.collapse_addresses([x for x in nets if x.version == ver]):
                if ver == 4:
                    recs.append(layout.pack(int(n.network_address), int(n.broadcast_address), code))
                else:
                    recs.append(layout.pack(n.network_address.packed, n.broadcast_address.packed, code))
            os.makedirs(SHIELD_DIR, exist_ok=True)
            tm.write_atomic(os.path.join(SHIELD_DIR, '%s%d.bin' % (key, ver)), b''.join(recs), 'wb')


def kill_states(countries):
    """end the open connections to the given countries (their next packet is refused)"""
    if not countries:
        return 0
    own = tm.local_addresses()
    look = tm.Lookup()
    remotes = set()
    for _key, (_d, _u, remote, _age) in tm.parse_states(tm.read_states(), own).items():
        if look.ready and look(remote)[0] in countries:
            remotes.add(remote)
    for r in sorted(remotes)[:500]:
        anyaddr = '::/0' if ':' in r else '0.0.0.0/0'
        pfctl('-k', anyaddr, '-k', r)
        pfctl('-k', r)
    return len(remotes)


# ---------------------------------------------------------------- log
LINE_TS = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:?\d\d|Z)?)')


def parse_hit(line, rules):
    """a filterlog line of a Shield rule -> hit dict, else None"""
    if 'filterlog' not in line:
        return None
    if re.search(r'filterlog\[\d*\]:', line):
        csv = re.split(r'filterlog[^:]*:', line, 1)[1]     # RFC 3164
    else:
        csv = line.split()[-1]                              # RFC 5424: the record is the last field
    f = csv.strip().split(',')
    if len(f) < 9 or f[3] not in rules:
        return None
    kind = rules[f[3]]
    try:
        if f[8] == '4' and len(f) >= 20:
            proto, src, dst, rest = f[16], f[18], f[19], f[20:]
        elif f[8] == '6' and len(f) >= 17:
            proto, src, dst, rest = f[12], f[15], f[16], f[17:]
        else:
            return None
    except IndexError:
        return None
    sport = dport = ''
    if proto in ('tcp', 'udp') and len(rest) >= 2:
        sport, dport = rest[0], rest[1]
    ts = now()
    m = LINE_TS.search(line[:64])
    if m:
        try:
            ts = datetime.datetime.fromisoformat(m.group(1).replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    out_dir = kind.endswith('-out')
    return {'ts': int(ts), 'kind': kind, 'iface': f[4], 'proto': proto,
            'device': src if out_dir else dst, 'remote': dst if out_dir else src,
            'port': dport, 'sport': sport}


def log_files():
    return sorted(glob.glob(os.path.join(LOG_DIR, 'filter_*.log')))


def collect(st, cfg):
    """read the firewall log from where the last run stopped; count and keep Shield's blocks"""
    rules = cfg['rules']
    files = log_files()
    pos = st.setdefault('log', {})
    if not files or not rules:
        return 0
    if pos.get('file') not in files:
        later = [f for f in files if pos.get('file') and f > pos['file']]
        if later:
            # the file we were reading was rotated away: go on with the next one
            pos['file'], pos['pos'] = later[0], 0
        else:
            # first run (or the log was cleared): only the tail of today's log
            pos['file'], pos['pos'] = files[-1], max(0, os.path.getsize(files[-1]) - FIRST_READ)
    uid = re.compile('|'.join(re.escape(u) for u in rules))
    hits = []
    budget = READ_MAX
    i = files.index(pos['file'])
    while i < len(files) and budget > 0:
        path = files[i]
        size = os.path.getsize(path)
        off = pos['pos'] if path == pos['file'] else 0
        if off > size:
            off = 0                       # truncated or rotated in place
        with open(path, 'rb') as fh:
            fh.seek(off)
            chunk = fh.read(budget)
        cut = chunk.rfind(b'\n') + 1      # only whole lines
        for raw in chunk[:cut].split(b'\n'):
            if raw and uid.search(raw.decode('utf-8', 'replace')):
                h = parse_hit(raw.decode('utf-8', 'replace'), rules)
                if h:
                    hits.append(h)
        budget -= cut
        pos['file'], pos['pos'] = path, off + cut
        if off + cut < size:
            break                         # budget spent mid-file: carry on next time
        i += 1
    record(st, hits, cfg)
    return len(hits)


def record(st, hits, cfg):
    if not hits:
        return
    days = st.setdefault('days', {})
    recent = st.setdefault('recent', [])
    alerts = st.setdefault('alerts', [])
    lists = None
    for h in hits:
        day = time.strftime('%Y-%m-%d', time.localtime(h['ts']))
        d = days.setdefault(day, {})
        d[h['kind']] = d.get(h['kind'], 0) + 1
    look = tm.Lookup()
    for h in hits[-RECENT_MAX:]:
        cc, asn, org = look(h['remote']) if look.ready else ('', 0, '')
        h['cc'], h['org'] = cc, org
    recent[:0] = list(reversed(hits[-RECENT_MAX:]))
    del recent[RECENT_MAX:]
    # alerts: one of your devices tried to reach a known-bad address
    for h in hits:
        if h['kind'] != 'threat-out':
            continue
        a = next((x for x in alerts if x['device'] == h['device'] and x['remote'] == h['remote']), None)
        if a is None:
            if lists is None:
                lists = Lists([f for f in cfg['feeds'] if FEED_BY_ID[f]['kind'] == 'threat'])
            cc, asn, org = look(h['remote']) if look.ready else ('', 0, '')
            a = {'device': h['device'], 'remote': h['remote'], 'port': h['port'], 'proto': h['proto'],
                 'first': h['ts'], 'last': h['ts'], 'count': 0, 'cc': cc, 'org': org,
                 'lists': lists.find(h['remote'])}
            alerts.append(a)
        a['count'] += 1
        a['last'] = max(a['last'], h['ts'])
        a['port'] = h['port'] or a['port']
    alerts.sort(key=lambda x: x['last'], reverse=True)
    del alerts[ALERTS_MAX:]
    for k in sorted(days)[:-DAYS_KEEP]:
        del days[k]


# ---------------------------------------------------------------- commands
def feed_info(st, cfg):
    meta = st.get('feeds', {})
    res = []
    for f in FEEDS:
        m = meta.get(f['id'], {})
        res.append({'id': f['id'], 'name': f['name'], 'about': f['about'], 'kind': f['kind'],
                    'home': f['home'], 'every': f['every'], 'default': f['default'],
                    'enabled': f['id'] in cfg['feeds'], 'entries': m.get('entries', 0),
                    'updated': m.get('updated', 0), 'checked': m.get('checked', 0), 'error': m.get('error', '')})
    return res


def stats(st):
    days = st.get('days', {})
    today = time.strftime('%Y-%m-%d', time.localtime(now()))
    week = [time.strftime('%Y-%m-%d', time.localtime(now() - i * 86400)) for i in range(7)]
    tot = {k: 0 for k in KINDS}
    for d in week:
        for k, v in days.get(d, {}).items():
            if k in tot:
                tot[k] += v
    return {'today': {k: days.get(today, {}).get(k, 0) for k in KINDS}, 'week': tot,
            'days': [{'day': d, 'counts': days.get(d, {})} for d in reversed(week)]}


def home_country():
    """where the firewall is: its public address, else its time zone"""
    look = tm.Lookup()
    if look.ready:
        for a in sorted(own_public()):
            cc = look(a)[0]
            if cc:
                return cc
    return tm.timezone_country()


def read_log(cfg):
    """the state, with the firewall log read up to now; saved only when something changed"""
    with Locked():
        st = load_json(STATE_FILE, {})
        before = json.dumps(st, sort_keys=True)
        gone = []
        try:
            if prune_allows(st):
                apply(cfg, st)            # a timed exception ran out: take it out of the tables
            gone = prune_domains(st)
            collect(st, cfg)
            if json.dumps(st, sort_keys=True) != before:
                save_json(STATE_FILE, st)
        except OSError:
            pass
    privacy_changed(gone, 'remove')
    return st


def visible_alerts(st):
    """alerts without the dismissed ones (a dismissed alert comes back on a newer try)"""
    dis = st.get('dismissed', {})
    return [a for a in st.get('alerts', []) if a['last'] > dis.get(a['device'] + '|' + a['remote'], 0)]


def cmd_status():
    cfg = settings()
    st = read_log(cfg)
    tables = st.get('tables', {})
    shown = visible_alerts(st)
    unseen = len([a for a in shown if a['last'] > st.get('ack', 0)])
    return out({
        'ok': True, 'ts': int(now()), 'enabled': cfg['enabled'], 'countries': cfg['countries'],
        'allow': cfg['allow'], 'feeds': feed_info(st, cfg),
        'tables': {k: {'name': t, 'entries': tables.get(t, {}).get('n', 0), 'error': tables.get(t, {}).get('error', '')}
                   for k, t in TABLES.items()},
        'limit': st.get('limit', 0), 'rules': sorted(set(cfg['rules'].values())),
        'updated': st.get('updated', 0), 'stats': stats(st), 'alerts': unseen,
        'alerts_total': len(shown), 'requests_pending': pending_count(), 'keys': {'vt': bool(cfg['vtkey']), 'abuseipdb': bool(cfg['abusekey'])},
        'db': bool(tm.Lookup().geo4.n), 'errors': st.get('errors', []), 'home': home_country(),
        'allows': allow_list(st, cfg),
    })


def apply(cfg, st, kill=False):
    own = own_public()
    prune_allows(st)
    tables = build_tables(cfg, own, sorted(st.get('allows', {})))
    errors = load_tables(tables, st)
    try:
        write_flags(tables)
    except OSError as e:
        log('could not write the flag files: %s' % e)
    newly = [c for c in cfg['countries'] if c not in st.get('countries', [])]
    killed = kill_states(set(newly)) if kill and newly else 0
    st['countries'] = cfg['countries']
    st['errors'] = errors
    st['limit'] = table_limit()
    return errors, killed


def cmd_apply(kill=False):
    cfg = settings()
    with Locked():
        st = load_json(STATE_FILE, {})
        errors, killed = apply(cfg, st, kill)
        save_json(STATE_FILE, st)
    return out({'ok': not errors, 'errors': errors, 'killed': killed,
                'tables': {t: v.get('n', 0) for t, v in st.get('tables', {}).items()}})


def cmd_update(force=False):
    cfg = settings()
    with Locked():
        st = load_json(STATE_FILE, {})
        meta = st.setdefault('feeds', {})
        done = []
        for fid in cfg['feeds'] if cfg['enabled'] else []:
            feed, m = FEED_BY_ID[fid], meta.setdefault(fid, {})
            have = os.path.exists(feed_path(fid))
            if have and not force and now() - m.get('checked', 0) < feed['every'] - 60:
                continue
            m['checked'] = int(now())
            try:
                m['entries'] = update_feed(feed, m)
                m['updated'] = int(now())
                m['error'] = ''
                done.append(fid)
            except Exception as e:  # a list that is down must not stop the others
                m['error'] = str(e)[:200] or e.__class__.__name__
                log('%s: %s' % (fid, m['error']))
        st['updated'] = int(now()) if done else st.get('updated', 0)
        errors, _ = apply(cfg, st)
        gone = prune_domains(st)          # the cron job ends timed website approvals too
        try:
            collect(st, cfg)
        except OSError:
            pass
        save_json(STATE_FILE, st)
    privacy_changed(gone, 'remove')
    return out({'ok': not errors, 'updated': done, 'errors': errors})


def cmd_hits():
    cfg = settings()
    st = read_log(cfg)
    names = interface_names(config_root())
    hits = [dict(h, iface=names.get(h.get('iface'), h.get('iface'))) for h in st.get('recent', [])]
    return out({'ok': True, 'hits': hits})


def cmd_alerts():
    cfg = settings()
    st = read_log(cfg)
    ack = st.get('ack', 0)
    alerts = [dict(a, new=a['last'] > ack) for a in visible_alerts(st)]
    # "enabled": alerts only come from the threat lists; the menu badge stops polling without them
    bp = load_json(os.path.join(os.path.dirname(inbox_dir()), 'conf.json'), {})
    return out({'ok': True, 'enabled': cfg['enabled'], 'unseen': len([a for a in alerts if a['new']]), 'alerts': alerts,
                'requests': pending_count(), 'blockpage': bool(bp.get('enabled'))})


def cmd_ack():
    with Locked():
        st = load_json(STATE_FILE, {})
        st['ack'] = int(now())
        save_json(STATE_FILE, st)
    return out({'ok': True})


def cmd_dismiss(device, remote):
    with Locked():
        st = load_json(STATE_FILE, {})
        key = device + '|' + remote
        a = next((x for x in st.get('alerts', []) if x['device'] + '|' + x['remote'] == key), None)
        if a is None:
            return out({'ok': False, 'error': 'no such alert'})
        dis = st.setdefault('dismissed', {})
        dis[key] = a['last']
        known = {x['device'] + '|' + x['remote'] for x in st.get('alerts', [])}
        for k in [k for k in dis if k not in known]:
            del dis[k]
        save_json(STATE_FILE, st)
    return out({'ok': True})


# ---------------------------------------------------------------- unblock requests
def clean(value, limit):
    return CONTROL_RE.sub('', str(value or '')).strip()[:limit + 1]


def site_kind(site):
    try:
        ipaddress.ip_address(site)
        return 'address'
    except ValueError:
        return 'domain' if DOMAIN_RE.match(site) else ''


def prune_requests(reqs):
    return [r for r in reqs if r['status'] == 'pending' or now() - r.get('done', r['t']) < REQ_KEEP]


def validate_request(req):
    """a request as typed on the block page -> the cleaned request, or None"""
    name = clean(req.get('name'), REQ_NAME_MAX)
    site = clean(req.get('site'), 253).lower().rstrip('.')
    reason = clean(req.get('reason'), REQ_CHARS_MAX)
    ip = clean(req.get('ip'), 45)
    kind = site_kind(site)
    if (not name or len(name) > REQ_NAME_MAX or not reason or len(reason) > REQ_CHARS_MAX or
            len(reason.split()) > REQ_WORDS_MAX or not kind or not ip):
        return None
    return {'ip': ip, 'name': name, 'site': site, 'kind': kind, 'reason': reason, 'why': clean(req.get('why'), 120)}


def admit(reqs, r, t):
    """may this request join the list? -> 'ok' | 'again' (same pending site) | 'rate' | 'full'"""
    mine = [x for x in reqs if x['ip'] == r['ip']]
    if any(x['status'] == 'pending' and x['site'] == r['site'] for x in mine):
        return 'again'
    if len([x for x in mine if t - x['t'] < 3600]) >= REQ_PER_HOUR:
        return 'rate'
    if len([x for x in mine if x['status'] == 'pending']) >= REQ_PENDING_PER_IP or len(reqs) >= REQ_MAX:
        return 'full'
    return 'ok'


def add_request(req, now_ts=None):
    """a request from the block page -> 'ok' | 'rate' | 'full' | 'invalid'"""
    t = int(now_ts if now_ts is not None else now())
    r = validate_request(req)
    if r is None:
        return 'invalid'
    with Locked():
        reqs = prune_requests(load_json(REQUESTS_FILE, []))
        res = admit(reqs, r, t)
        if res == 'again':            # asked again: keep one request, with the newest words
            same = next(x for x in reqs if x['ip'] == r['ip'] and x['status'] == 'pending' and x['site'] == r['site'])
            same.update(name=r['name'], reason=r['reason'], t=t)
        elif res == 'ok':
            reqs.append(dict(r, id=secrets.token_hex(6), t=t, status='pending', until=0))
        else:
            return res
        save_json(REQUESTS_FILE, reqs)
    return 'ok'


def inbox_dir():
    return os.environ.get('UNISENSE_BLOCKPAGE_INBOX', os.path.join(tm.DB_DIR, 'blockpage', 'inbox'))


def read_inbox(remove=True):
    """the requests the block page (www) left in its inbox, read with care: regular files only,
    no symlinks, nothing that blocks, at most 8 KB each -> [dict with an int 't']"""
    d = inbox_dir()
    try:
        st = os.lstat(d)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    res = []
    for name in sorted(os.listdir(d))[:500]:
        if not name.endswith('.json'):
            continue
        path = os.path.join(d, name)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    req = json.loads(os.read(fd, 8192).decode('utf-8', 'replace'))
                    if isinstance(req, dict) and isinstance(req.get('t'), int):
                        res.append(req)
            finally:
                os.close(fd)
        except Exception:   # anything odd in a file www wrote: skip it (and drop it below)
            pass
        if remove:
            try:
                os.unlink(path)
            except OSError:
                pass
    return res


def ingest():
    """requests the block page left in its inbox (it runs as www and never writes Shield's files)"""
    for req in read_inbox():
        add_request(req, req['t'])


def pending_count():
    ingest()
    return len([r for r in load_json(REQUESTS_FILE, []) if r.get('status') == 'pending'])


def cmd_requests():
    ingest()
    reqs = sorted(load_json(REQUESTS_FILE, []), key=lambda r: (r['status'] != 'pending', -r['t']))
    st = load_json(STATE_FILE, {})
    live = set(st.get('domain_allows', {})) | {n.split('/')[0] for n in st.get('allows', {})}
    for r in reqs:
        if r['status'] == 'approved' and r.get('until') and r['site'] not in live:
            r['status'] = 'expired'
    return out({'ok': True, 'pending': len([r for r in reqs if r['status'] == 'pending']), 'requests': reqs})


def cmd_request(action, rid, dur=''):
    if action == 'add':               # from the block page, JSON on stdin
        try:
            req = json.loads(sys.stdin.read(4096) or '{}')
        except ValueError:
            req = {}
        res = add_request(req if isinstance(req, dict) else {})
        return out({'ok': res == 'ok', 'error': '' if res == 'ok' else res})
    if action not in ('approve', 'decline'):
        return out({'ok': False, 'error': 'unknown action'})
    if action == 'approve' and dur not in DURATIONS and dur != 'forever':
        return out({'ok': False, 'error': 'unknown duration'})
    with Locked():
        reqs = load_json(REQUESTS_FILE, [])
        r = next((x for x in reqs if x['id'] == rid), None)
        if r is None:
            return out({'ok': False, 'error': 'no such request'})
        r['status'] = 'approved' if action == 'approve' else 'declined'
        r['done'] = int(now())
        r['dur'] = dur if action == 'approve' else ''
        r['until'] = int(now() + DURATIONS[dur]) if action == 'approve' and DURATIONS.get(dur) else 0
        save_json(REQUESTS_FILE, reqs)
    if action == 'decline':
        return out({'ok': True})
    if dur == 'forever':              # the controller put it on the permanent list, where it can be seen and removed
        return out({'ok': True})
    if r['kind'] == 'address':
        return cmd_allow(r['site'], dur)
    with Locked():
        st = load_json(STATE_FILE, {})
        da = st.setdefault('domain_allows', {})
        new = r['site'] not in da
        da[r['site']] = {'dur': dur, 'added': int(now()), 'until': r['until'],
                         'boot': boot_id() if dur == 'restart' else ''}
        save_json(STATE_FILE, st)
    if new:
        privacy_changed([r['site']], 'allow')
    return out({'ok': True})


def cmd_unallow_domain(domain):
    with Locked():
        st = load_json(STATE_FILE, {})
        found = st.get('domain_allows', {}).pop(domain, None) is not None
        save_json(STATE_FILE, st)
    if found:
        privacy_changed([domain], 'remove')
    return out({'ok': True, 'removed': found})


def allow_list(st, cfg):
    """every exception, timed and permanent, for the page"""
    res = []
    for net, a in sorted(st.get('allows', {}).items()):
        res.append({'net': net, 'addr': net[:-3] if net.endswith('/32') else (net[:-4] if net.endswith('/128') else net),
                    'dur': a.get('dur', ''), 'until': a.get('until', 0), 'added': a.get('added', 0), 'forever': False})
    for x in cfg['allow']:
        res.append({'net': x, 'addr': x, 'dur': 'forever', 'until': 0, 'added': 0, 'forever': True})
    return res


def cmd_allow(addr, dur):
    """let an address through for a while (it stays on its lists; Shield just skips it)"""
    try:
        net = ipaddress.ip_network(addr, strict=False)
    except ValueError:
        return out({'ok': False, 'error': 'invalid address'})
    if dur not in DURATIONS:
        return out({'ok': False, 'error': 'unknown duration'})
    if not net.is_global or (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 32):
        return out({'ok': False, 'error': 'allow single addresses or small public networks only'})
    cfg = settings()
    with Locked():
        st = load_json(STATE_FILE, {})
        st.setdefault('allows', {})[str(net)] = {
            'dur': dur, 'added': int(now()),
            'until': int(now() + DURATIONS[dur]) if DURATIONS[dur] else 0,
            'boot': boot_id() if dur == 'restart' else ''}
        errors, _ = apply(cfg, st)
        save_json(STATE_FILE, st)
    return out({'ok': not errors, 'errors': errors, 'allows': allow_list(st, cfg)})


def cmd_unallow(addr):
    try:
        net = str(ipaddress.ip_network(addr, strict=False))
    except ValueError:
        return out({'ok': False, 'error': 'invalid address'})
    cfg = settings()
    with Locked():
        st = load_json(STATE_FILE, {})
        found = st.get('allows', {}).pop(net, None) is not None
        errors, _ = apply(cfg, st)
        save_json(STATE_FILE, st)
    return out({'ok': not errors, 'removed': found, 'errors': errors, 'allows': allow_list(st, cfg)})


def cmd_clear():
    with Locked():
        st = load_json(STATE_FILE, {})
        for t in TABLES.values():
            pfctl('-t', t, '-T', 'flush')
        st['tables'] = {}
        st['countries'] = []
        save_json(STATE_FILE, st)
    return out({'ok': True})


def reputation(ip, cfg):
    """VirusTotal / AbuseIPDB, when the user added a free API key (cached for a day)"""
    cache = load_json(LOOKUPS_FILE, {})
    cache = {k: v for k, v in cache.items() if now() - v.get('ts', 0) < LOOKUP_TTL}
    res = {}
    if cfg['vtkey']:
        hit = cache.get('vt|' + ip)
        if hit is None:
            try:
                d = json.loads(fetch_text('https://www.virustotal.com/api/v3/ip_addresses/' + ip,
                                          {'x-apikey': cfg['vtkey']}, 20))
                a = d.get('data', {}).get('attributes', {})
                s = a.get('last_analysis_stats', {})
                hit = {'ok': True, 'malicious': s.get('malicious', 0), 'suspicious': s.get('suspicious', 0),
                       'harmless': s.get('harmless', 0), 'undetected': s.get('undetected', 0),
                       'reputation': a.get('reputation', 0), 'owner': a.get('as_owner', '')}
            except Exception as e:
                hit = {'ok': False, 'error': str(e)[:160]}
            hit['ts'] = int(now())
            if hit['ok']:
                cache['vt|' + ip] = hit
        res['virustotal'] = hit
    if cfg['abusekey']:
        hit = cache.get('ab|' + ip)
        if hit is None:
            try:
                d = json.loads(fetch_text('https://api.abuseipdb.com/api/v2/check?maxAgeInDays=90&ipAddress=' + ip,
                                          {'Key': cfg['abusekey'], 'Accept': 'application/json'}, 20)).get('data', {})
                hit = {'ok': True, 'score': d.get('abuseConfidenceScore', 0), 'reports': d.get('totalReports', 0),
                       'usage': d.get('usageType') or '', 'isp': d.get('isp') or '', 'tor': bool(d.get('isTor'))}
            except Exception as e:
                hit = {'ok': False, 'error': str(e)[:160]}
            hit['ts'] = int(now())
            if hit['ok']:
                cache['ab|' + ip] = hit
        res['abuseipdb'] = hit
    if cache:
        try:
            save_json(LOOKUPS_FILE, dict(sorted(cache.items(), key=lambda kv: kv[1]['ts'])[-500:]))
        except OSError:
            pass
    return res


def cmd_check(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return out({'ok': False, 'error': 'invalid address'})
    ip = str(a)
    cfg = settings()
    look = tm.Lookup()
    cc, asn, org = look(ip) if look.ready else ('', 0, '')
    lists = Lists([f['id'] for f in FEEDS]).find(ip)
    allowed = False
    allow_until = None
    st = load_json(STATE_FILE, {})
    prune_allows(st)
    for x in cfg['allow']:
        try:
            if a in ipaddress.ip_network(x, strict=False):
                allowed = True
                allow_until = -1
        except ValueError:
            pass
    for x, v in st.get('allows', {}).items():
        try:
            if a in ipaddress.ip_network(x, strict=False) and not allowed:
                allowed = True
                allow_until = v.get('until', 0)
        except ValueError:
            pass
    res = {'ok': True, 'ip': ip, 'public': a.is_global, 'cc': cc, 'asn': asn, 'org': org,
           'lists': [{'id': f, 'name': FEED_BY_ID[f]['name'], 'kind': FEED_BY_ID[f]['kind'],
                      'enabled': f in cfg['feeds']} for f in lists],
           'country_blocked': bool(cc) and cc in cfg['countries'], 'allowed': allowed, 'allow_until': allow_until,
           'blocked': not allowed and ((bool(cc) and cc in cfg['countries']) or
                                       (cfg['enabled'] and any(f in cfg['feeds'] for f in lists))),
           'links': {'virustotal': 'https://www.virustotal.com/gui/ip-address/' + ip,
                     'abuseipdb': 'https://www.abuseipdb.com/check/' + ip}}
    if a.is_global:
        res.update(reputation(ip, cfg))
    return out(res)


def cmd_checkup():
    """a few settings that matter most for a home firewall; read-only"""
    root = config_root()
    cfg = settings(root)
    checks = []

    def add(cid, ok, title, detail, url, level='warn'):
        checks.append({'id': cid, 'ok': bool(ok), 'level': 'ok' if ok else level, 'title': title,
                       'detail': detail, 'url': url})

    sysn = root.find('system') if root is not None else None
    add('https', text(sysn, 'webgui/protocol', 'https') == 'https', 'Web GUI over HTTPS',
        'Logins to this page are encrypted.' if text(sysn, 'webgui/protocol', 'https') == 'https' else
        'Logins to this page travel unencrypted. Switch the protocol to HTTPS.', '/system_advanced_admin.php')
    ssh_on = text(sysn, 'ssh/enabled') in ('enabled', '1')
    ssh_root = text(sysn, 'ssh/permitrootlogin') == '1'
    ssh_pw = text(sysn, 'ssh/passwordauth') == '1'
    add('ssh', not (ssh_on and ssh_root and ssh_pw), 'SSH root password login',
        'SSH is off or uses keys.' if not ssh_on or not (ssh_root and ssh_pw) else
        'SSH allows root to log in with a password. Use a key, or turn password login off.',
        '/system_advanced_admin.php')
    wan = root.find('interfaces/wan') if root is not None else None
    bogons = text(wan, 'blockbogons') == '1' and text(wan, 'blockpriv') == '1'
    add('bogons', bogons, 'Private and bogon addresses on WAN',
        'Spoofed and unroutable sources are dropped on WAN.' if bogons else
        'Turn on "Block private networks" and "Block bogon networks" on the WAN interface (leave private '
        'off if your modem gives you a private WAN address).', '/interfaces.php?if=wan')
    ub = root.find('OPNsense/unboundplus') if root is not None else None
    unbound_on = text(ub, 'general/enabled') == '1'
    add('dnssec', not unbound_on or text(ub, 'general/dnssec') == '1', 'DNSSEC validation',
        'Forged DNS answers are rejected.' if text(ub, 'general/dnssec') == '1' else
        'Turn on DNSSEC in Unbound DNS so forged DNS answers are rejected.' if unbound_on else
        'Unbound DNS is not in use.', '/ui/unbound/general', 'info')
    dnsbl = False
    if ub is not None:
        for el in ub.iter():
            if el.tag == 'blocklist' and text(el, 'enabled') == '1' and (text(el, 'type') or text(el, 'lists')):
                dnsbl = True
        if text(ub, 'dnsbl/enabled') == '1':
            dnsbl = True
    agh = text(root.find('OPNsense/unisense/privacy') if root is not None else None, 'provider') == 'adguard'
    if agh:
        add('dnsbl', True, 'DNS blocklist', 'AdGuard Home blocks ads, trackers and malware domains (Lobby: Privacy).',
            '/ui/unisense/privacy')
    else:
        add('dnsbl', dnsbl or not unbound_on, 'DNS blocklist',
            'Unbound blocks known malware and tracking domains.' if dnsbl else
            'Shield blocks bad addresses; ad, tracker and malware domains can be blocked too: turn on '
            'Lobby: Privacy.' if unbound_on else 'Unbound DNS is not in use.',
            '/ui/unisense/privacy', 'info')
    shield_ok = cfg['enabled'] and any(FEED_BY_ID[f]['kind'] == 'threat' for f in cfg['feeds'])
    add('shield', shield_ok, 'Shield threat lists',
        'Botnet and criminal networks are blocked for every device.' if shield_ok else
        'Turn on Shield with the recommended lists.', '/ui/unisense/shield')
    # what the Internet can reach: WAN pass rules from anywhere and port forwards
    open_rules = 0
    for r in (root.findall('filter/rule') if root is not None else []):
        if text(r, 'interface') == 'wan' and text(r, 'type') == 'pass' and r.find('disabled') is None \
                and r.find('source/any') is not None:
            open_rules += 1
    for r in (root.findall('OPNsense/Firewall/Filter/rules/rule') if root is not None else []):
        if 'wan' in text(r, 'interface').split(',') and text(r, 'action') == 'pass' and \
                text(r, 'enabled', '1') == '1' and text(r, 'source_net', 'any') == 'any':
            open_rules += 1
    forwards = len([r for r in (root.findall('nat/rule') if root is not None else [])
                    if r.find('disabled') is None])
    forwards += len([r for r in (root.findall('OPNsense/Firewall/DNat/rule') if root is not None else [])
                     if text(r, 'disabled') != '1'])
    exposed = open_rules + forwards
    add('exposed', exposed == 0, 'Open to the Internet',
        'No WAN rule or port forward accepts connections from anywhere.' if exposed == 0 else
        '%d port forward(s) and %d WAN rule(s) accept connections from anywhere. Country blocking and the '
        'scanner lists protect them; limit the source where you can.' % (forwards, open_rules),
        '/ui/firewall/d_nat' if forwards else '/firewall_rules.php?if=wan', 'info')
    return out({'ok': True, 'checks': checks,
                'score': len([c for c in checks if c['ok']]), 'total': len(checks)})


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'status'
    if cmd == 'status':
        return cmd_status()
    if cmd == 'apply':
        return cmd_apply('kill' in argv or '--kill' in argv)
    if cmd == 'update':
        return cmd_update('force' in argv or '--force' in argv)
    if cmd == 'hits':
        return cmd_hits()
    if cmd == 'alerts':
        return cmd_alerts()
    if cmd == 'ack':
        return cmd_ack()
    if cmd == 'check':
        return cmd_check(argv[2] if len(argv) > 2 else '')
    if cmd == 'checkup':
        return cmd_checkup()
    if cmd == 'clear':
        return cmd_clear()
    if cmd == 'allow':
        return cmd_allow(argv[2] if len(argv) > 2 else '', argv[3] if len(argv) > 3 else '')
    if cmd == 'unallow':
        return cmd_unallow(argv[2] if len(argv) > 2 else '')
    if cmd == 'dismiss':
        return cmd_dismiss(argv[2] if len(argv) > 2 else '', argv[3] if len(argv) > 3 else '')
    if cmd == 'requests':
        return cmd_requests()
    if cmd == 'request':
        return cmd_request(*(argv[2:5] + [''] * (3 - len(argv[2:5]))))
    if cmd == 'unallow-domain':
        return cmd_unallow_domain(argv[2] if len(argv) > 2 else '')
    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))

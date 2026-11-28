#!/usr/local/bin/python3

# Copyright (C) 2026 ox1d3x3
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""UniSense block page: what people see when Shield or Privacy stops a site.

    blockpage.py start | stop | restart | status
    blockpage.py serve        (in the foreground; start runs this in the background)

Blocked names (Privacy) and web visits to known-bad addresses (Shield, 26.1+)
are answered with a private address on the firewall's loopback; this small
server answers there on ports 80 and 443. It binds as root, then runs as www.

- a page visit gets "Stopped by UniSense Shield", the reason and a Request to
  unblock button; anything else (ad scripts, images) gets an empty answer
- requests go into an inbox that Shield takes in (Lobby: Shield, Requests)
- HTTPS only when a CA is chosen: each site gets a certificate signed by it,
  so devices that trust that CA see the page; without one, 443 is closed

Settings: /var/db/unisense/blockpage/conf.json, written by the Shield settings.
"""
import collections
import html
import json
import os
import re
import secrets
import signal
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shield  # noqa: E402

DIR = os.environ.get('UNISENSE_BLOCKPAGE_DIR', '/var/db/unisense/blockpage')
CONF = os.path.join(DIR, 'conf.json')
PIDFILE = os.environ.get('UNISENSE_BLOCKPAGE_PID', '/var/run/unisense-blockpage.pid')
OPENSSL = os.environ.get('UNISENSE_OPENSSL', '/usr/bin/openssl')
PRIVACY_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'privacy.py')
HOST_RE = re.compile(r'^(?=.{1,253}$)[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$')
BODY_MAX = 4096
CERTS_MAX = 2000
RUN_AS = os.environ.get('UNISENSE_BLOCKPAGE_USER', 'www')

PAGE = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>%(title)s</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0f1115;color:#e6e8ec;padding:24px}
.card{width:100%%;max-width:520px;background:#171a21;border:1px solid #262a33;border-radius:16px;padding:32px;
box-shadow:0 20px 60px rgba(0,0,0,.45)}
.badge{width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;
background:rgba(255,120,30,.14);color:#ff8a3d;margin-bottom:18px}
h1{font-size:22px;margin:0 0 10px}p{margin:0 0 14px;color:#b7bcc7}b{color:#fff;word-break:break-all}
.why{background:#11141a;border:1px solid #262a33;border-radius:10px;padding:10px 14px;margin:16px 0;color:#cfd3db}
a.btn,button{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:10px;padding:10px 18px;
font:600 14px system-ui,sans-serif;background:#ff7a1a;color:#111;text-decoration:none;cursor:pointer}
label{display:block;font-weight:600;margin:14px 0 6px;color:#e6e8ec}
input,textarea{width:100%%;background:#0f1115;border:1px solid #2c313b;border-radius:10px;padding:10px 12px;
color:#e6e8ec;font:14px system-ui,sans-serif}textarea{min-height:110px;resize:vertical}
input:focus,textarea:focus{outline:2px solid #ff7a1a;border-color:transparent}
.err{color:#ff8a8a}.ok{color:#7ddc9a}.foot{margin-top:22px;font-size:12px;color:#6f7582}
</style></head><body><main class="card">
<div class="badge"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"
stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
<line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg></div>
%(body)s
<div class="foot">UniSense Shield</div></main></body></html>'''


def esc(v):
    return html.escape(str(v or ''), quote=True)


def load_conf():
    try:
        with open(CONF) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ reasons
_why_cache = {}


def why(host, client):
    """why a name was blocked, from Unbound's query log (cached a minute)"""
    key = (host, client)
    hit = _why_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    reason = 'it is on one of the block lists'
    try:
        r = subprocess.run([sys.executable, PRIVACY_PY, 'why', host, client],
                           capture_output=True, text=True, timeout=4)
        d = json.loads(r.stdout or '{}')
        if d.get('list'):
            reason = 'it is on the %s list' % d['list'] if d['list'] != 'Your blocks' else 'the network admin blocked it'
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if len(_why_cache) > 500:
        _why_cache.clear()
    _why_cache[key] = (time.time(), reason)
    return reason


# ------------------------------------------------------------------ requests
def submit(fields, client):
    """-> (http status, message)"""
    r = shield.validate_request({'name': fields.get('name'), 'site': fields.get('site'),
                                 'reason': fields.get('reason'), 'ip': client})
    if r is None:
        words = len(str(fields.get('reason') or '').split())
        if words > shield.REQ_WORDS_MAX or len(str(fields.get('reason') or '')) > shield.REQ_CHARS_MAX:
            return 400, 'Please keep the reason to 100 words.'
        return 400, 'Please fill in your name, the website and why you need it.'
    inbox = shield.inbox_dir()
    now = int(time.time())
    known = shield.prune_requests(shield.load_json(shield.REQUESTS_FILE, []))
    for x in shield.read_inbox(remove=False):
        r2 = shield.validate_request(x)
        if r2:
            known.append(dict(r2, t=x['t'], status='pending'))
    res = shield.admit(known, r, now)
    if res in ('rate', 'full'):
        return 429, 'Too many requests from this device. Please try again later.'
    os.makedirs(inbox, exist_ok=True)
    path = os.path.join(inbox, '%d-%s.json' % (now, secrets.token_hex(4)))
    with open(path + '.tmp', 'w') as f:
        json.dump(dict(r, t=now), f)
    os.replace(path + '.tmp', path)
    return 200, 'Sent. The network admin will look at it soon.'


# ------------------------------------------------------------------ pages
def page(title, body):
    return (PAGE % {'title': esc(title), 'body': body}).encode()


def block_page(host, client):
    return page('Blocked: ' + host, (
        '<h1>Stopped by UniSense Shield</h1>'
        '<p><b>%s</b> is blocked on this network.</p>'
        '<div class="why">Why: %s.</div>'
        '<p>If you need it, ask the network admin to unblock it.</p>'
        '<a class="btn" href="/?request=1">Request to unblock</a>') % (esc(host), esc(why(host, client))))


def form_page(site='', msg='', ok=False, values=None):
    v = values or {}
    if ok:
        return page('Request sent', '<h1>Request sent</h1><p class="ok">%s</p>' % esc(msg))
    return page('Request to unblock', (
        '<h1>Request to unblock</h1>'
        '<p>Tell the network admin which website you need and why.</p>%s'
        '<form method="post" action="/request">'
        '<label for="n">Your name</label><input id="n" name="name" maxlength="60" required value="%s">'
        '<label for="s">Website</label><input id="s" name="site" maxlength="253" required placeholder="example.com" value="%s">'
        '<label for="r">Why do you need it? (100 words max)</label>'
        '<textarea id="r" name="reason" maxlength="800" required>%s</textarea>'
        '<p style="margin-top:16px"><button type="submit">Send request</button></p></form>') % (
            '<p class="err">%s</p>' % esc(msg) if msg else '', esc(v.get('name')), esc(v.get('site') or site),
            esc(v.get('reason'))))


class Handler(BaseHTTPRequestHandler):
    server_version = 'UniSense'
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 15

    def log_message(self, *a):
        pass

    def host(self):
        h = (self.headers.get('Host') or '').strip().lower()
        h = h.rsplit(':', 1)[0] if h.count(':') == 1 else h
        sni = getattr(self.connection, 'server_hostname', None)
        h = h or (sni or '')
        return h if HOST_RE.match(h) else 'this site'

    def send(self, status, body=b'', ctype='text/html; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self.close_connection = True

    def is_page(self):
        dest = self.headers.get('Sec-Fetch-Dest')
        if dest:
            return dest == 'document'
        return 'text/html' in (self.headers.get('Accept') or '')

    def do_HEAD(self):
        self.send(204)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if not self.is_page():
            return self.send(204)
        host = self.host()
        conf = self.server.conf
        if host == 'unblock.' + conf.get('domain', '') or host == conf.get('address') or \
                'request=1' in (url.query or ''):
            site = '' if host.startswith('unblock.') or host == conf.get('address') or host == 'this site' else host
            return self.send(200, form_page(site))
        return self.send(200, block_page(host, self.client_address[0]))

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != '/request':
            return self.send(404, page('Not found', '<h1>Not found</h1>'))
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = -1
        if 'chunked' in (self.headers.get('Transfer-Encoding') or '').lower():
            n = -1
        if n < 0 or n > BODY_MAX:
            return self.send(413, page('Too long', '<h1>Too long</h1><p>That request is too long.</p>'))
        try:
            raw = self.rfile.read(n).decode('utf-8', 'replace')
            fields = {k: v[0] for k, v in urllib.parse.parse_qs(raw, max_num_fields=10).items()}
        except (ValueError, OSError):
            return self.send(400, form_page(msg='That request could not be read. Please try again.'))
        try:
            status, msg = submit(fields, self.client_address[0])
        except OSError:
            status, msg = 503, 'The request could not be saved. Please try again later.'
        return self.send(status, form_page(msg=msg, ok=status == 200, values=fields))


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


# ------------------------------------------------------------------ HTTPS
class CertStore:
    """a certificate per site, signed by the chosen CA and kept on disk"""

    def __init__(self, base):
        self.base = base
        self.dir = os.path.join(base, 'certs')
        self.key = os.path.join(base, 'leaf.key')
        self.lock = threading.Lock()
        self.ctx = collections.OrderedDict()

    def prepare(self):
        """as root, before the drop: certs/ for www, the leaf key readable by group www"""
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.key):
            subprocess.run([OPENSSL, 'ecparam', '-genkey', '-name', 'prime256v1', '-noout', '-out', self.key],
                           check=True, capture_output=True, timeout=30)
        os.chmod(self.key, 0o640)
        if os.getuid() == 0:
            import pwd
            pw = pwd.getpwnam(RUN_AS)
            os.chown(self.key, 0, pw.pw_gid)
            os.chown(self.dir, pw.pw_uid, pw.pw_gid)
            os.makedirs(shield.inbox_dir(), exist_ok=True)
            os.chown(shield.inbox_dir(), pw.pw_uid, pw.pw_gid)

    def mint(self, host):
        pem = os.path.join(self.dir, host + '.pem')
        if os.path.exists(pem):
            return pem
        csr = subprocess.run([OPENSSL, 'req', '-new', '-key', self.key, '-subj', '/CN=' + host],
                             check=True, capture_output=True, timeout=15).stdout
        ext = os.path.join(self.dir, '.ext-' + secrets.token_hex(4))
        with open(ext, 'w') as f:
            f.write('subjectAltName=DNS:%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\n'
                    'extendedKeyUsage=serverAuth\n' % host)
        try:
            crt = subprocess.run([OPENSSL, 'x509', '-req', '-days', '397', '-CA', os.path.join(self.base, 'ca.crt'),
                                  '-CAkey', os.path.join(self.base, 'ca.key'), '-set_serial',
                                  str(int.from_bytes(os.urandom(8), 'big')), '-extfile', ext],
                                 input=csr, check=True, capture_output=True, timeout=15).stdout
        finally:
            os.unlink(ext)
        with open(os.path.join(self.base, 'ca.crt'), 'rb') as f:
            chain = crt + f.read()
        with open(pem + '.tmp', 'wb') as f:
            f.write(chain)
        os.replace(pem + '.tmp', pem)
        files = sorted(os.listdir(self.dir))
        if len(files) > CERTS_MAX:
            for old in sorted(files, key=lambda n: os.path.getmtime(os.path.join(self.dir, n)))[:len(files) - CERTS_MAX]:
                os.unlink(os.path.join(self.dir, old))
        return pem

    def context(self, host):
        with self.lock:
            if host in self.ctx:
                self.ctx.move_to_end(host)
                return self.ctx[host]
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            c.load_cert_chain(self.mint(host), self.key)
            self.ctx[host] = c
            if len(self.ctx) > 256:
                self.ctx.popitem(last=False)
            return c


class TLSServer(Server):
    def __init__(self, addr, handler, store):
        self.store = store
        self.base_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.base_ctx.sni_callback = self.sni
        super().__init__(addr, handler)

    def sni(self, sock, name, _ctx):
        name = (name or '').lower()
        if not HOST_RE.match(name):
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        try:
            sock.context = self.store.context(name)
        except (OSError, subprocess.SubprocessError, ssl.SSLError):
            return ssl.ALERT_DESCRIPTION_INTERNAL_ERROR
        return None

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(10)
        return self.base_ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def process_request_thread(self, request, client_address):
        try:
            request.do_handshake()
        except (ssl.SSLError, OSError):
            self.shutdown_request(request)
            return
        super().process_request_thread(request, client_address)


# ------------------------------------------------------------------ running
def start_servers(conf, http_port=80, https_port=443, before_serving=None):
    """bind (as root), get ready, run before_serving (the drop to www), then answer"""
    servers = [Server((conf['address'], http_port), Handler)]
    if conf.get('ca'):
        store = CertStore(DIR)
        store.prepare()
        servers.append(TLSServer((conf['address'], https_port), Handler, store))
    for s in servers:
        s.conf = conf
    if before_serving:
        before_serving()
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return servers


def drop_privileges():
    if os.getuid() != 0:
        return
    import pwd
    pw = pwd.getpwnam(RUN_AS)
    os.setgroups([])
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)


def serve():
    conf = load_conf()
    if not conf.get('enabled') or not conf.get('address'):
        print('the block page is off')
        return 1
    servers = start_servers(conf, before_serving=drop_privileges)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    stop.wait()
    for s in servers:
        s.shutdown()
    return 0


def running():
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def stop():
    pid = running()
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not running():
                break
            time.sleep(0.1)
    try:
        os.unlink(PIDFILE)
    except OSError:
        pass


def start():
    if running():
        return
    conf = load_conf()
    if not conf.get('enabled'):
        return
    subprocess.run(['/usr/sbin/daemon', '-f', '-p', PIDFILE, sys.executable, os.path.abspath(__file__), 'serve'],
                   check=False, timeout=10)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'status'
    if cmd == 'serve':
        return serve()
    if cmd == 'start':
        start()
    elif cmd == 'stop':
        stop()
    elif cmd == 'restart':
        stop()
        start()
    elif cmd != 'status':
        sys.stderr.write(__doc__)
        return 2
    time.sleep(0.3 if cmd in ('start', 'restart') else 0)
    conf = load_conf()
    print(json.dumps({'ok': True, 'running': bool(running()), 'pid': running(), 'enabled': bool(conf.get('enabled')),
                      'address': conf.get('address', ''), 'https': bool(conf.get('ca'))}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

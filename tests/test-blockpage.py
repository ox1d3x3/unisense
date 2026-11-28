#!/usr/bin/env python3
"""Block page tests: the page and the request form over HTTP, a request saved
to the inbox and taken in by Shield, limits on what is typed, HTTPS with a
certificate signed on the fly by the chosen CA, refused straight away
without one, and host names that must never reach openssl.

    python3 tests/test-blockpage.py
"""
import http.client
import importlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.dont_write_bytecode = True
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
        print('  FAIL ' + name + (('  -> ' + str(detail)[:300]) if detail else ''))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port, host, path='/', doc=True, method='GET', body=None, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    h = {'Host': host}
    if doc:
        h.update({'Accept': 'text/html', 'Sec-Fetch-Dest': 'document'})
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read().decode('utf-8', 'replace')
    c.close()
    return r.status, data


def post(port, host, fields):
    body = urllib.parse.urlencode(fields)
    return get(port, host, '/request', method='POST', body=body,
               headers={'Content-Type': 'application/x-www-form-urlencoded'})


def make_ca(d):
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30',
                    '-subj', '/CN=Test CA', '-keyout', str(d / 'ca.key'), '-out', str(d / 'ca.crt'),
                    '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign'],
                   check=True, capture_output=True)


def main():
    work = Path(tempfile.mkdtemp(prefix='unisense-blockpage-test-'))
    try:
        run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


def run(work):
    bp_dir = work / 'blockpage'
    bp_dir.mkdir()
    make_ca(bp_dir)
    os.environ.update(UNISENSE_BLOCKPAGE_USER='nobody', UNISENSE_DB_DIR=str(work / 'db'), UNISENSE_SHIELD_DIR=str(work / 'db/shield'),
                      UNISENSE_BLOCKPAGE_DIR=str(bp_dir), UNISENSE_BLOCKPAGE_INBOX=str(bp_dir / 'inbox'),
                      UNISENSE_CONFIG_XML=str(work / 'config.xml'))
    (work / 'config.xml').write_text('<?xml version="1.0"?><opnsense><OPNsense/></opnsense>')
    sys.path.insert(0, str(SCRIPTS))
    import trafficmap  # noqa: F401
    importlib.reload(trafficmap)
    import shield
    importlib.reload(shield)
    import blockpage as B
    importlib.reload(B)
    B.why = lambda host, client: 'it is on the ads and trackers list (hagezi PRO)'

    conf = {'address': '127.0.0.1', 'domain': 'lan.internal', 'ca': True}
    hp, sp = free_port(), free_port()
    servers = B.start_servers(conf, hp, sp)
    time.sleep(0.2)

    print('the page')
    st, body = get(hp, 'ads.example')
    t('block page for a blocked site', st == 200 and 'Stopped by UniSense Shield' in body and 'ads.example' in body
      and 'hagezi PRO' in body, body[:200])
    t('…with a Request to unblock button', 'Request to unblock' in body)
    st, body = get(hp, 'ads.example', '/pixel.gif', doc=False)
    t('images, scripts and the like get an empty answer', st == 204 and body == '', st)
    st, body = get(hp, 'unblock.lan.internal')
    t('the request form at unblock.<domain>', st == 200 and 'Your name' in body and 'Why do you need it?' in body, body[:200])
    st, body = get(hp, 'ads.example', '/?request=1')
    t('the form from the button, with the site filled in', st == 200 and 'value="ads.example"' in body)
    st, body = get(hp, '<script>x</script>')
    t('a hostile Host header is never echoed raw', '<script>x</script>' not in body)

    print('requests')
    st, body = post(hp, 'unblock.lan.internal', {'name': 'Mahabub', 'site': 'gigabyte.com', 'reason': 'BIOS update'})
    t('sent', st == 200 and 'Sent. The network admin will look at it soon.' in body, body[:300])
    r = json.loads(shell(['requests']))
    t('Shield takes it in from the inbox', r['pending'] == 1 and r['requests'][0]['site'] == 'gigabyte.com'
      and r['requests'][0]['ip'] == '127.0.0.1' and not list((bp_dir / 'inbox').glob('*.json')), r)
    st, body = post(hp, 'ads.example', {'name': '<script>alert(1)</script>', 'site': 'ads.example', 'reason': 'x'})
    t('a name with markup is kept as text, never as markup', st == 200 and '<script>alert(1)' not in body)
    st, body = post(hp, 'ads.example', {'name': 'A', 'site': 'ads.example', 'reason': 'word ' * 101})
    t('a reason over 100 words is refused and not stored', st == 400 and '100 words' in body and
      json.loads(shell(['requests']))['pending'] == 2, body[:200])
    c = http.client.HTTPConnection('127.0.0.1', hp, timeout=5)
    try:
        c.request('POST', '/request', body=b'x' * (1024 * 1024),
                  headers={'Host': 'a.b', 'Content-Type': 'application/x-www-form-urlencoded'})
        st = c.getresponse().status
    except (BrokenPipeError, ConnectionResetError):
        st = 413               # refused before the whole body was sent
    c.close()
    t('a 1 MB post is refused', st == 413, st)
    t('…and the server still answers', get(hp, 'ads.example')[0] == 200)
    st, body = post(hp, 'ads.example', {'f%d' % i: 'x' for i in range(11)})
    t('a post with too many fields gets an answer, not a dropped connection', st == 400, st)
    c = http.client.HTTPConnection('127.0.0.1', hp, timeout=5)
    c.putrequest('POST', '/request')
    c.putheader('Host', 'a.b')
    c.putheader('Transfer-Encoding', 'chunked')
    c.endheaders()
    c.send(b'5\r\nhello\r\n0\r\n\r\n')
    st = c.getresponse().status
    c.close()
    t('a chunked post is refused', st == 413, st)
    for i in range(10):
        post(hp, 'ads.example', {'name': 'A', 'site': 'x%d.com' % i, 'reason': 'r'})
    st, body = post(hp, 'ads.example', {'name': 'A', 'site': 'y.com', 'reason': 'r'})
    t('too many from one device: told to wait', st == 429 and 'later' in body, body[:200])

    print('HTTPS')
    ctx = ssl.create_default_context(cafile=str(bp_dir / 'ca.crt'))
    with socket.create_connection(('127.0.0.1', sp), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname='ads.example') as tls:
            cert = tls.getpeercert()
            tls.sendall(b'GET / HTTP/1.1\r\nHost: ads.example\r\nAccept: text/html\r\nSec-Fetch-Dest: document\r\n'
                        b'Connection: close\r\n\r\n')
            page = b''
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                page += chunk
    t('a certificate for the site, signed by the chosen CA', ('DNS', 'ads.example') in cert.get('subjectAltName', ()), cert)
    t('…and the page over HTTPS', b'Stopped by UniSense Shield' in page)
    try:
        with socket.create_connection(('127.0.0.1', sp), timeout=5) as raw:
            with ssl._create_unverified_context().wrap_socket(raw, server_hostname='evil;rm') as tls:
                tls.recv(1)
        bad = True
    except (ssl.SSLError, OSError):
        bad = False
    t('a name that is not a host name gets no certificate', not bad and not list(bp_dir.rglob('*evil*')))
    for s in servers:
        s.shutdown()
        s.server_close()

    print('as root: binds, then runs as an ordinary user')
    if os.getuid() == 0:
        import pwd
        user = 'nobody'
        root_dir = work / 'asroot'
        root_dir.mkdir()
        make_ca(root_dir)
        hp3, sp3 = free_port(), free_port()
        (root_dir / 'conf.json').write_text(json.dumps({'enabled': True, 'address': '127.0.0.1', 'domain': 'lan.internal', 'ca': True}))
        gid = pwd.getpwnam(user).pw_gid
        os.chown(root_dir / 'ca.key', 0, gid)
        os.chmod(root_dir / 'ca.key', 0o640)
        os.chmod(work, 0o755)
        env = dict(os.environ, UNISENSE_BLOCKPAGE_DIR=str(root_dir), UNISENSE_BLOCKPAGE_USER=user,
                   UNISENSE_BLOCKPAGE_INBOX=str(root_dir / 'inbox'), PYTHONDONTWRITEBYTECODE='1')
        code = ('import sys, threading; sys.path.insert(0, %r); import blockpage as B; '
                'B.why = lambda h, c: "x"; c = B.load_conf(); '
                'B.start_servers(c, %d, %d, before_serving=B.drop_privileges); threading.Event().wait()') % (str(SCRIPTS), hp3, sp3)
        proc = subprocess.Popen([sys.executable, '-c', code], env=env, cwd=str(root_dir))
        time.sleep(1.5)
        try:
            ctx3 = ssl.create_default_context(cafile=str(root_dir / 'ca.crt'))
            with socket.create_connection(('127.0.0.1', sp3), timeout=5) as raw:
                with ctx3.wrap_socket(raw, server_hostname='ads.example') as tls:
                    ok = ('DNS', 'ads.example') in tls.getpeercert().get('subjectAltName', ())
        except (ssl.SSLError, OSError) as e:
            ok = str(e)
        st, body = post(hp3, 'ads.example', {'name': 'A', 'site': 'ads.example', 'reason': 'r'})
        owner = sorted({pwd.getpwuid(p.stat().st_uid).pw_name for p in (root_dir / 'certs').iterdir()}) if (root_dir / 'certs').exists() else []
        proc.terminate()
        proc.wait(5)
        t('HTTPS still works after the drop (certificates written by the ordinary user)', ok is True and owner == [user], (ok, owner))
        t('…and requests too', st == 200, (st, body[:200]))
    else:
        print('  (skipped: not root)')

    print('no CA chosen')
    hp2, sp2 = free_port(), free_port()
    servers = B.start_servers(dict(conf, ca=False), hp2, sp2)
    time.sleep(0.2)
    t0 = time.time()
    try:
        socket.create_connection(('127.0.0.1', sp2), timeout=5).close()
        refused = False
    except ConnectionRefusedError:
        refused = True
    t('HTTPS is refused at once, no waiting', refused and time.time() - t0 < 1)
    t('HTTP still works', get(hp2, 'ads.example')[0] == 200)
    for s in servers:
        s.shutdown()
        s.server_close()


def shell(args):
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(SCRIPTS / 'shield.py')] + args, capture_output=True, text=True, env=env)
    return r.stdout


if __name__ == '__main__':
    sys.exit(main())

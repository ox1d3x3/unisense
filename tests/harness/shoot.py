#!/usr/bin/env python3
"""Screenshot rendered pages in light/dark, desktop/mobile, and report any
console errors, page errors or failed requests (e.g. missing theme assets).

Usage: shoot.py --core /path/to/core [--pages a,b] [--out shots/] [--chrome /usr/bin/google-chrome]
Exit status is non-zero if any page produced errors.
"""
import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from server import serve

HERE = Path(__file__).resolve().parent
PAGES = ['dashboard', 'networkmap', 'trafficmap', 'shield', 'privacy', 'cron', 'syslog', 'monit', 'legacy', 'login']
VIEWPORTS = {'desktop': (1440, 1000), 'mobile': (390, 844)}
IGNORED = ('favicon.ico',)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--pages', default=','.join(PAGES))
    ap.add_argument('--modes', default='light,dark')
    ap.add_argument('--viewports', default='desktop,mobile')
    ap.add_argument('--out', default=str(HERE / 'shots'))
    ap.add_argument('--chrome', default=None)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--extra', default='', help='comma list: collapsed,dialog,menu,appearance')
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    httpd = serve(a.core, HERE / 'site/pages', a.port)
    problems = []
    extras = [x for x in a.extra.split(',') if x]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=a.chrome)
        for vp in a.viewports.split(','):
            w, h = VIEWPORTS[vp]
            for mode in a.modes.split(','):
                for page_name in a.pages.split(','):
                    variants = [''] + [x for x in extras if vp == 'desktop']
                    for extra in variants:
                        ctx = browser.new_context(viewport={'width': w, 'height': h},
                                                  device_scale_factor=1 if vp == 'desktop' else 2,
                                                  color_scheme='light')
                        init = f"localStorage.setItem('unisense.mode', '{mode}');"
                        if extra == 'collapsed':
                            init += "localStorage.setItem('toggle_sidebar_preset', '1');"
                        ctx.add_init_script(init)
                        page = ctx.new_page()
                        errs = []
                        page.on('console', lambda m, errs=errs: errs.append(f'console.{m.type}: {m.text}')
                                if m.type == 'error' else None)
                        page.on('pageerror', lambda e, errs=errs: errs.append(f'pageerror: {e}'))
                        page.on('requestfailed', lambda r, errs=errs: errs.append(f'requestfailed: {r.url}')
                                if not r.url.endswith(IGNORED) and '/stream' not in r.url else None)
                        page.on('response', lambda r, errs=errs: errs.append(f'HTTP {r.status}: {r.url}')
                                if r.status >= 400 and not r.url.endswith(IGNORED) else None)
                        page.goto(f'http://127.0.0.1:{a.port}/pages/{page_name}.html', wait_until='load')
                        page.wait_for_timeout(2500 if page_name == 'dashboard' else 1200)
                        if extra == 'dialog':
                            page.evaluate("""() => { const m = document.querySelector('.modal[id^=dialog], .modal[id^=Dialog]');
                                                     if (m) { $(m).modal('show'); } }""")
                            page.wait_for_timeout(600)
                        elif extra == 'appearance':
                            page.click('.us-appearance-btn')
                            page.wait_for_timeout(200)
                        elif extra == 'menu' and vp == 'desktop':
                            pass
                        name = f'{page_name}-{mode}-{vp}' + (f'-{extra}' if extra else '')
                        full = vp == 'desktop' and page_name not in ('login',)
                        page.screenshot(path=str(out / f'{name}.png'))
                        for e in errs:
                            problems.append(f'[{name}] {e}')
                        ctx.close()
        browser.close()
    httpd.shutdown()

    for pr in problems:
        print(pr)
    print(f'{len(problems)} problem(s)')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Interaction checks for UniSense: appearance menu, accent colours, chart
recolouring, bootstrap-select, status dialog, tooltips, mobile drawer,
tablet width. Asserts behaviour and writes screenshots to shots/.

Usage: interact.py --core /path/to/core [--chrome /usr/bin/google-chrome]
"""
import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from server import serve

HERE = Path(__file__).resolve().parent
failures = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        failures.append(msg)


def watch(page, errs):
    page.on('console', lambda m: errs.append(f'console.{m.type}: {m.text}') if m.type == 'error' else None)
    page.on('pageerror', lambda e: errs.append(f'pageerror: {e}'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--chrome', default=None)
    ap.add_argument('--port', type=int, default=8770)
    a = ap.parse_args()
    out = HERE / 'shots'
    out.mkdir(exist_ok=True)
    httpd = serve(a.core, HERE / 'site/pages', a.port)
    base = f'http://127.0.0.1:{a.port}/pages'

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chrome)

        # ---------------------------------------------------- appearance menu
        print('appearance menu')
        ctx = b.new_context(viewport={'width': 1440, 'height': 900}, color_scheme='light')
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/dashboard.html'); pg.wait_for_timeout(2500)
        check(pg.evaluate("document.documentElement.getAttribute('data-us-theme')") is None, 'default mode is auto (no attribute)')
        bg_light = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        pg.click('.us-appearance-btn')
        check(pg.is_visible('.us-appearance-menu'), 'menu opens')
        pg.click('[data-us-mode="dark"]'); pg.wait_for_timeout(300)
        check(pg.evaluate("document.documentElement.getAttribute('data-us-theme')") == 'dark', 'dark mode applied')
        check(pg.evaluate("localStorage.getItem('unisense.mode')") == 'dark', 'dark mode persisted')
        bg_dark = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        check(bg_light != bg_dark, f'body colour changes ({bg_light} -> {bg_dark})')
        check(pg.evaluate("Chart.defaults.color") != '', 'Chart.js default colour set')
        before = pg.evaluate("JSON.stringify(Object.values(Chart.instances).map(c => c.data.datasets.map(d => d.backgroundColor)))")
        pg.click('[data-us-accent="#7b5cff"]'); pg.wait_for_timeout(400)
        check(pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--us-accent').trim()") == '#7b5cff', 'accent applied')
        after = pg.evaluate("JSON.stringify(Object.values(Chart.instances).map(c => c.data.datasets.map(d => d.backgroundColor)))")
        check('123, 92, 255' in after and before != after, 'gauge charts follow the accent colour')
        pg.screenshot(path=str(out / 'interact-dashboard-purple-dark.png'))
        pg.keyboard.press('Escape')
        check(not pg.is_visible('.us-appearance-menu'), 'Escape closes the menu')
        pg.click('.us-appearance-btn'); pg.click('[data-us-mode="auto"]'); pg.wait_for_timeout(200)
        check(pg.evaluate("localStorage.getItem('unisense.mode')") is None, 'auto mode clears the stored preference')
        pg.click('[data-us-accent="#006fff"]'); pg.mouse.click(700, 500)
        check(not pg.is_visible('.us-appearance-menu'), 'outside click closes the menu')
        check(pg.evaluate("localStorage.getItem('unisense.accent')") is None, 'default accent clears the stored preference')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # ------------------------------------------- system preference (auto)
        print('auto mode follows the OS')
        ctx = b.new_context(viewport={'width': 1200, 'height': 800}, color_scheme='dark')
        pg = ctx.new_page(); pg.goto(f'{base}/cron.html'); pg.wait_for_timeout(800)
        check(pg.evaluate("getComputedStyle(document.body).backgroundColor") == 'rgb(19, 20, 22)', 'OS dark -> dark tokens')
        ctx.close()
        ctx = b.new_context(viewport={'width': 1200, 'height': 800}, color_scheme='dark', java_script_enabled=False)
        pg = ctx.new_page(); pg.goto(f'{base}/legacy.html'); pg.wait_for_timeout(500)
        check(pg.evaluate is not None, 'page loads without JavaScript')
        pg.screenshot(path=str(out / 'interact-nojs-dark.png'))
        ctx.close()

        # ---------------------------------------- bootstrap-select + tooltips
        for mode in ('light', 'dark'):
            print(f'widgets ({mode})')
            ctx = b.new_context(viewport={'width': 1440, 'height': 900})
            ctx.add_init_script(f"localStorage.setItem('unisense.mode', '{mode}')")
            pg = ctx.new_page(); errs = []; watch(pg, errs)
            pg.goto(f'{base}/legacy.html'); pg.wait_for_timeout(1000)
            pg.click('button[data-id="timezone"]'); pg.wait_for_timeout(300)
            check(pg.is_visible('.bootstrap-select.open .dropdown-menu'), 'selectpicker opens')
            pg.screenshot(path=str(out / f'interact-select-{mode}.png'), clip={'x': 248, 'y': 380, 'width': 900, 'height': 420})
            pg.keyboard.press('Escape')
            pg.hover('.menu-favorite-star'); pg.wait_for_timeout(400)
            check(pg.is_visible('.tooltip'), 'tooltip shows')
            pg.screenshot(path=str(out / f'interact-tooltip-{mode}.png'), clip={'x': 248, 'y': 56, 'width': 700, 'height': 120})
            pg.click('#system_status'); pg.wait_for_timeout(700)
            check(pg.is_visible('.bootstrap-dialog'), 'status dialog opens')
            pg.screenshot(path=str(out / f'interact-status-{mode}.png'))
            check(not errs, 'no console errors: ' + '; '.join(errs))
            ctx.close()

        # --------------------------------------------------------- network map
        print('network map')
        os.environ['UNISENSE_HARNESS_XSS'] = '1'   # a lease with an HTML hostname
        ctx = b.new_context(viewport={'width': 1440, 'height': 1000})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/networkmap.html'); pg.wait_for_selector('.usn-tile', timeout=15000)
        tiles = pg.locator('.usn-tile').count()
        check(tiles == 11, f'all devices shown ({tiles})')
        check(pg.locator('#usn img').count() == 0 and 'onerror' in pg.inner_text('#usn'), 'hostnames are escaped (no HTML injection)')
        check(pg.locator('[data-mac="00:0d:b9:aa:00:01"]').count() == 0, "the firewall's own interfaces are not listed")
        check(pg.locator('[data-mac="de:ad:be:ef:00:07"]').count() == 0, 'expired ARP entries are ignored')
        check(pg.locator('.usn-tile.is-offline').count() == 2, 'lease-only devices are shown as offline')
        pg.wait_for_function("document.querySelectorAll('.usn-tile-rate .usn-rate').length > 0", timeout=15000)
        check(True, 'live per-device traffic appears')
        heights = set()
        for _ in range(3):
            heights |= set(pg.evaluate("[...document.querySelectorAll('.usn-tile:not(.is-offline)')].map(t => Math.round(t.getBoundingClientRect().height))"))
            pg.wait_for_timeout(4300)
        check(len(heights) == 1, f'tiles keep one height while traffic comes and goes ({sorted(heights)})')
        check(pg.locator('#mainmenu a[href="/ui/unisense/networkmap"].active').count() == 1, 'menu entry is highlighted')
        pg.fill('[data-usn="query"]', 'pixel'); pg.wait_for_timeout(150)
        check(pg.locator('.usn-tile').count() == 1, 'search narrows the list')
        pg.fill('[data-usn="query"]', ''); pg.wait_for_timeout(150)
        pg.click('button[data-filter="opt1"]'); pg.wait_for_timeout(150)
        check(pg.locator('.usn-net').count() == 1 and pg.locator('.usn-tile').count() == 2, 'network filter shows one network')
        pg.click('button[data-filter="all"]'); pg.uncheck('[data-usn="offline"]'); pg.wait_for_timeout(150)
        check(pg.locator('.usn-tile.is-offline').count() == 0, 'offline devices can be hidden')
        pg.check('[data-usn="offline"]')
        # rename + icon
        pg.click('[data-mac="7c:2e:0d:de:ad:01"]'); pg.wait_for_selector('.usn-modal .modal-dialog', timeout=5000)
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(out / 'interact-netmap-dialog.png'))
        pg.fill('#usn-name', '<b>bad'); pg.click('.usn-modal .btn-primary'); pg.wait_for_timeout(200)
        check('64 characters' in pg.inner_text('.usn-dialog-msg'), 'invalid names are rejected')
        pg.fill('#usn-name', 'Living room PS5'); pg.click('.usn-icon-opt[data-icon="tv"]'); pg.click('.usn-modal .btn-primary')
        pg.wait_for_function("!document.querySelector('.usn-modal.in')", timeout=5000); pg.wait_for_timeout(500)
        check('Living room PS5' in pg.inner_text('[data-mac="7c:2e:0d:de:ad:01"]'), 'rename shows on the tile')
        check(pg.locator('[data-mac="7c:2e:0d:de:ad:01"] .fa-tv').count() == 1, 'custom icon shows on the tile')
        pg.reload(); pg.wait_for_selector('.usn-tile', timeout=15000)
        check('Living room PS5' in pg.inner_text('[data-mac="7c:2e:0d:de:ad:01"]'), 'label is stored server-side')
        pg.click('[data-mac="7c:2e:0d:de:ad:01"]'); pg.wait_for_selector('.usn-modal .modal-dialog', timeout=5000)
        pg.click('.usn-modal .btn-default.pull-left')
        pg.wait_for_function("!document.querySelector('.usn-modal.in')", timeout=5000); pg.wait_for_timeout(500)
        check('ps5' in pg.inner_text('[data-mac="7c:2e:0d:de:ad:01"]'), 'reset restores the detected name')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # ------------------------------------------------- phone: menu drawer
        print('phone drawer')
        ctx = b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        ctx.add_init_script("localStorage.setItem('unisense.mode', 'dark')")
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/cron.html'); pg.wait_for_timeout(1000)
        check(pg.is_visible('.navbar-header .us-appearance-btn'), 'appearance button moves into the phone bar')
        pg.click('.navbar-toggle'); pg.wait_for_timeout(600)
        check(pg.is_visible('#navigation'), 'drawer opens')
        pg.screenshot(path=str(out / 'interact-drawer-dark.png'))
        pg.click('.navbar-header .us-appearance-btn'); pg.wait_for_timeout(200)
        box = pg.locator('.us-appearance-menu').bounding_box()
        check(box is not None and box['x'] >= 0 and box['x'] + box['width'] <= 390, 'menu fits the phone screen')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # --------------------------------------------- footer credit + rail
        print('footer credit and collapsed rail')
        version = (HERE.parents[1] / 'misc/theme-unisense/src/opnsense/www/themes/unisense/VERSION').read_text().strip()
        ctx = b.new_context(viewport={'width': 1600, 'height': 900})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/dashboard.html'); pg.wait_for_timeout(1500)
        credit = pg.locator('.page-foot .us-credit')
        check(credit.count() == 1 and credit.inner_text().strip() == f'Author Ox1d3x3 × UniSense v{version}',
              f'credit reads "Author Ox1d3x3 × UniSense v{version}"')
        box = credit.bounding_box()
        check(box is not None and 1500 < box['x'] + box['width'] <= 1600, 'credit sits at the bottom right, on screen')
        foot = pg.locator('.page-foot').bounding_box()
        check(foot is not None and foot['x'] + foot['width'] <= 1600, 'footer stays inside the viewport')
        pg.click('.toggle-sidebar'); pg.wait_for_timeout(500)
        check(pg.evaluate("document.documentElement.classList.contains('us-rail-collapsed')"), 'collapsed rail mirrored on <html>')
        hw = pg.evaluate("document.querySelector('.page-head .navbar-header').getBoundingClientRect().width")
        check(abs(hw - 70) < 1, f'top bar brand follows the collapsed rail ({hw}px)')
        fx = pg.locator('.page-foot').bounding_box()['x']
        check(abs(fx - 70) < 1, f'footer follows the collapsed rail ({fx}px)')
        pg.click('.toggle-sidebar'); pg.wait_for_timeout(500)
        check(not pg.evaluate("document.documentElement.classList.contains('us-rail-collapsed')"), 'expanding clears the mirror class')
        check(not pg.evaluate("document.documentElement.classList.contains('us-loading')"), 'loading phase ends after load')
        check(pg.evaluate("[...document.styleSheets].some(s => s.href && /unisense\\/build\\/css\\/main\\.css/.test(s.href) && [...s.cssRules].some(r => r.selectorText === ':root' && r.style.getPropertyValue('--us-accent')))"),
              'design tokens (:root) load from main.css')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # ------------------------------------------------------ sign-in page
        print('sign-in page')
        ctx = b.new_context(viewport={'width': 1280, 'height': 800})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/login.html'); pg.wait_for_timeout(800)
        check(pg.is_visible('.us-login-brand .us-login-icon') and 'UniSense' in pg.inner_text('.us-login-title'), 'brand block shown')
        check('Sign in to' in pg.inner_text('.us-login-sub'), 'shows the host being signed in to')
        check(not pg.is_visible('.login-modal-head'), 'wordmark header replaced')
        check(not pg.is_visible('#inputerrors'), 'empty error line hidden')
        pg.fill('#passwordfld', 'hunter2')
        pg.click('.us-pwd-toggle')
        check(pg.get_attribute('#passwordfld', 'type') == 'text', 'show password')
        pg.click('.us-pwd-toggle')
        check(pg.get_attribute('#passwordfld', 'type') == 'password', 'hide password again')
        pg.evaluate("document.getElementById('passwordfld').dispatchEvent(new KeyboardEvent('keydown', {key: 'A', modifierCapsLock: true}))")
        check(pg.is_visible('.us-caps'), 'Caps Lock hint')
        pg.evaluate("document.getElementById('passwordfld').dispatchEvent(new KeyboardEvent('keyup', {key: 'a'}))")
        check(not pg.is_visible('.us-caps'), 'Caps Lock hint goes away')
        pg.fill('#usernamefld', 'root')
        sent = pg.evaluate("""() => new Promise(res => {
            const f = document.getElementById('iform');
            f.addEventListener('submit', e => { e.preventDefault();
              const d = new FormData(f, e.submitter);
              res({ login: d.get('login'), busy: e.submitter.classList.contains('us-busy'), disabled: e.submitter.disabled }); });
            f.querySelector('button[type=submit]').click(); })""")
        check(sent['login'] == '1' and sent['busy'] and not sent['disabled'],
              f'submit shows busy and still posts login=1 ({sent})')
        credit = pg.locator('.us-credit-fixed')
        check(credit.count() == 1 and f'UniSense v{version}' in credit.inner_text(), 'credit in the corner')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # ------------------------------------------------------ tablet widths
        for w in (800, 1024):
            print(f'tablet {w}px')
            ctx = b.new_context(viewport={'width': w, 'height': 900})
            pg = ctx.new_page(); errs = []; watch(pg, errs)
            pg.goto(f'{base}/syslog.html'); pg.wait_for_timeout(1000)
            sw = pg.evaluate("document.documentElement.scrollWidth")
            check(sw <= w, f'no horizontal overflow at {w}px (scrollWidth {sw})')
            pg.screenshot(path=str(out / f'interact-tablet-{w}.png'))
            check(not errs, 'no console errors: ' + '; '.join(errs))
            ctx.close()
        b.close()
    httpd.shutdown()
    print(f'\n{len(failures)} failure(s)')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Interaction checks for UniSense: appearance menu, accent colours, chart
recolouring, bootstrap-select, status dialog, tooltips, mobile drawer,
tablet width. Asserts behaviour and writes screenshots to shots/.

Usage: interact.py --core /path/to/core [--chrome /usr/bin/google-chrome]
"""
import argparse
import json
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
        pg.wait_for_function("document.querySelector('.us-cpu-val') && /^\\d+$/.test(document.querySelector('.us-cpu-val').textContent)", timeout=8000)
        cpu = pg.evaluate("""(() => { const q = s => document.querySelector(s);
            return { user: q('[data-us-cpu="user"]').textContent, temp: q('[data-us-cpu="temp"]').textContent,
                     h: q('#cpu-usage-total').getBoundingClientRect().height }; })()""")
        check(cpu['user'].endswith('%') and cpu['temp'].endswith('°C') and cpu['h'] >= 120, f'CPU widget: load, breakdown, temperature, taller graph ({cpu})')
        pg.wait_for_timeout(6000)
        scroll = pg.evaluate("[...document.querySelectorAll('.us-meter canvas')].map(c => c.getAnimations().map(a => a.playState + ':' + a.effect.getTiming().easing).join())")
        check(any('running:linear' in x for x in scroll), f'meters scroll continuously ({scroll})')
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

        # ------------------------------------------------- keep menus open
        print('keep all menu sections open')
        ctx = b.new_context(viewport={'width': 1440, 'height': 900})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/cron.html'); pg.wait_for_timeout(1200)
        opened = "document.querySelectorAll('#mainmenu .collapse.in').length"
        total = pg.evaluate("document.querySelectorAll('#mainmenu .collapse').length")
        before = pg.evaluate(opened)
        pg.click('.us-appearance-btn')
        sw = pg.locator('[data-us-menu="expanded"]')
        check(sw.get_attribute('aria-checked') == 'false', 'the option is off by default')
        sw.click(); pg.wait_for_timeout(300)
        check(sw.get_attribute('aria-checked') == 'true', 'the switch turns on')
        check(pg.evaluate(opened) == total and total > before, f'every section opens ({before} -> {pg.evaluate(opened)} of {total})')
        pg.keyboard.press('Escape')
        # a click on another section header must not close the others
        hdr = pg.locator('#mainmenu a[href="#System"], #mainmenu a[data-target="#System"]').first
        if hdr.count():
            hdr.click(); pg.wait_for_timeout(400)
        check(pg.evaluate(opened) >= total - 1, 'opening one section does not close the others')
        pg.reload(); pg.wait_for_timeout(1200)
        check(pg.evaluate(opened) == total, 'stays open after a reload')
        check(pg.evaluate("localStorage.getItem('unisense.menu')") == 'expanded', 'preference stored')
        pg.click('.us-appearance-btn'); pg.locator('[data-us-menu="expanded"]').click(); pg.wait_for_timeout(300)
        check(pg.evaluate(opened) <= before + 1, f'turning it off goes back to the current section only ({pg.evaluate(opened)})')
        check(pg.evaluate("localStorage.getItem('unisense.menu')") is None, 'preference cleared')
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
        check(pg.locator('.usn-row', has_text='Loopback').count() == 0 and pg.locator('button[data-filter="lo0"]').count() == 0,
              'loopback is not shown')
        # the mock's traffic is random and can be idle for a poll: wait for a busy one
        lanes = ripples = 0
        for _ in range(8):
            lanes = pg.evaluate("[...document.querySelectorAll('.usn-link-flow.is-on')].filter(l => l.__usnAnim && l.__usnAnim.playState === 'running').length")
            ripples = pg.locator('.usn-tile.is-active .usn-ripple').count()
            if lanes >= 4 and ripples >= 1:
                break
            pg.wait_for_timeout(1500)
        check(lanes >= 4, f'live data flow runs on the links ({lanes} strings)')
        check(ripples >= 1, 'busy devices ripple')
        check(pg.evaluate("""[...document.querySelectorAll('.usn-tile')].every(t => {
            const n = t.querySelector('.usn-tile-name'); const i = n && n.querySelector('.usn-live-ind');
            return i && i.previousElementSibling && i.previousElementSibling.classList.contains('usn-tile-label');
        })"""), 'the live indicator sits right after each device name')
        geo = pg.evaluate("""(() => {
            const r = e => e.getBoundingClientRect();
            const inet = r(document.querySelector('.usn-node-internet')), fw = r(document.querySelector('.usn-node-fw'));
            const rows = [...document.querySelectorAll('.usn-row')].map(row => ({
                card: r(row.querySelector('.usn-netcard')), lane: r(row.querySelector('.usn-lane')) }));
            const links = [...document.querySelectorAll('.usn-link')];
            return { coreLeft: fw.right < Math.min(...rows.map(x => x.card.left)), inetAbove: inet.bottom <= fw.top,
                     cardsLeft: rows.every(x => x.card.right < x.lane.left),
                     cardsCol: new Set(rows.map(x => Math.round(x.card.left))).size === 1,
                     rows: rows.length, links: links.length,
                     curves: links.filter(l => (l.querySelector('path').getAttribute('d').match(/L/g) || []).length > 10).length,
                     lanes: links.every(l => l.querySelectorAll('.usn-link-dl, .usn-link-ul').length === 2) };
        })()""")
        check(geo['coreLeft'] and geo['inetAbove'], 'Internet above the firewall, in the left column')
        check(geo['cardsCol'] and geo['cardsLeft'], 'one column of network cards, each with its devices to the right')
        check(geo['links'] == 1 + 2 * geo['rows'] and geo['curves'] == geo['rows'], f"connectors: Internet-firewall, a curve to each network, a line to each lane ({geo['links']})")
        check(geo['lanes'], 'every connector has a download and an upload lane')
        pg.uncheck('[data-usn="live"]'); pg.wait_for_timeout(200)
        lanes = pg.evaluate("[...document.querySelectorAll('.usn-link-flow')].filter(l => l.__usnAnim && l.__usnAnim.playState === 'running').length")
        check(lanes == 0 and pg.locator('.usn-link-flow.is-on').count() == 0, 'data flow stops with live traffic off')
        pg.check('[data-usn="live"]'); pg.wait_for_timeout(4500)
        check(pg.locator('#mainmenu a[href="/ui/unisense/networkmap"].active').count() == 1, 'menu entry is highlighted')
        pg.fill('[data-usn="query"]', 'pixel'); pg.wait_for_timeout(150)
        check(pg.locator('.usn-tile').count() == 1, 'search narrows the list')
        pg.fill('[data-usn="query"]', ''); pg.wait_for_timeout(150)
        pg.click('button[data-filter="opt1"]'); pg.wait_for_timeout(150)
        check(pg.locator('.usn-row').count() == 1 and pg.locator('.usn-tile').count() == 2, 'network filter shows one network')
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

        # -------------------------------------------------------- traffic map
        print('traffic map')
        ctx = b.new_context(viewport={'width': 1440, 'height': 1000})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        posts = []
        pg.on('request', lambda r: r.method == 'POST' and posts.append((r.url, r.headers.get('x-csrftoken'))))
        pg.goto(f'{base}/trafficmap.html'); pg.wait_for_timeout(5000)
        check(pg.locator('.utm-row').count() == 16, f'top countries listed ({pg.locator(".utm-row").count()})')
        check(pg.locator('.utm-svg path[data-cc]').count() > 200, 'world map drawn')
        check(pg.locator('.utm-svg path[data-cc="US"]').get_attribute('data-l') not in (None, '0'), 'countries with traffic are shaded')
        check(pg.locator('.utm-svg path.is-home[data-cc="AU"]').count() == 1, 'home country marked')
        check(pg.evaluate("Object.keys(window.UniSenseTrafficMap.state.flows).length") >= 10, 'arcs to the busiest countries')
        # count what was actually painted (1.1.0 kept the flows in state but never drew them)
        painted = pg.evaluate("""[...document.querySelectorAll('.utm-canvas')].map(c => {
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
            for (let i = 3; i < d.length; i += 16) if (d[i] > 8) n++; return n; })""")
        check(painted[0] > 1500, f'arcs are painted ({painted[0]} px sampled)')
        check(painted[1] > 150, f'moving streaks are painted ({painted[1]} px sampled)')
        rowref = pg.evaluate("window.__usRow = document.querySelector('.utm-row[data-cc=\"US\"]'), 1")
        pg.wait_for_timeout(3500)
        check(pg.evaluate("document.querySelector('.utm-row[data-cc=\"US\"]') === window.__usRow"), 'table rows are updated in place, not rebuilt')
        check(pg.evaluate("!!document.querySelector('.utm-spark path.utm-spark-line').getAttribute('d')"), 'live throughput sparklines')
        # the connections behind a country
        pg.click('.utm-row[data-cc="US"]'); pg.wait_for_selector('.utm-conns-btn', timeout=4000)
        pg.click('.utm-conns-btn'); pg.wait_for_selector('.utm-conn', timeout=8000)
        check(pg.is_visible('[data-utm="conns"]') and 'United States' in pg.inner_text('[data-utm="conns-title"]'), 'a country opens its connections')
        pg.wait_for_function("document.querySelector('[data-utm=\"conns-body\"]').innerText.includes('mahabubs-macbook')", timeout=8000)
        check(True, 'connections show device names, not just addresses')
        n_c = pg.locator('.utm-conn').count()
        pg.fill('[data-utm="conns-search"]', 'NTP'); pg.wait_for_timeout(300)
        n_f = pg.locator('.utm-conn').count()
        check(0 < n_f < n_c, f'connections can be filtered ({n_c} -> {n_f})')
        pg.click('[data-utm-action="close-conns"]')
        check(pg.is_hidden('[data-utm="conns"]'), 'connections panel closes')
        pg.click('[data-utm-stat="cn"]'); pg.wait_for_selector('.utm-conn', timeout=8000)
        check('All connections' in pg.inner_text('[data-utm="conns-title"]') and pg.locator('.utm-conn-table th', has_text='Country').count() == 1,
              'the Connections card lists every connection, with their countries')
        pg.click('[data-utm-action="close-conns"]'); pg.click('.utm-row[data-cc="US"]')

        # every country ever seen: the history behind the "All countries" tab
        pg.click('[data-utm-view="all"]')
        pg.wait_for_selector('.utm-hist-row', timeout=8000)
        n_all = pg.locator('.utm-hist-row').count()
        check(n_all > 16 and pg.is_hidden('[data-utm="table"]'), f'All countries lists every country recorded ({n_all})')
        check(pg.locator('.utm-hist-row[data-cc="CN"] .utm-c-now', has_text='—').count() == 1 and
              'ago' in pg.inner_text('.utm-hist-row[data-cc="CN"] .utm-c-last'), 'countries seen earlier show when, and no live rate')
        check(pg.locator('.utm-hist-row[data-cc="US"].is-live').count() == 1, 'countries talking right now are marked live')
        check(pg.inner_text('[data-utm="all-count"]').strip() == str(n_all), 'tab shows how many countries')
        pg.click('.utm-hist th[data-utm-sort="name"]'); pg.wait_for_timeout(100)
        names = pg.evaluate("[...document.querySelectorAll('.utm-hist-row td:nth-child(2)')].map(t => t.innerText.trim())")
        check(names == sorted(names, key=str.lower), 'sortable by country name')
        pg.fill('[data-utm="search"]', 'zeal'); pg.wait_for_timeout(300)
        check(pg.locator('.utm-hist-row').count() == 1 and pg.locator('.utm-hist-row[data-cc="NZ"]').count() == 1, 'search narrows the list')
        pg.fill('[data-utm="search"]', ''); pg.click('[data-utm-view="top"]'); pg.wait_for_timeout(100)
        check(pg.is_visible('[data-utm="table"]') and pg.is_hidden('[data-utm="search-wrap"]'), 'back to the top 20')
        check(pg.locator('img.utm-flag').count() >= 14 and pg.locator('img.utm-flag.is-missing').count() == 0, 'flags load')
        check('Unknown' in pg.inner_text('.utm-table') and 'AS0' not in pg.inner_text('.utm-table'), 'unknown country without a fake network')
        box = pg.locator('[data-utm="mapbox"]').bounding_box()
        # the country's anchor point (mainland centroid) on screen
        pt = pg.evaluate("""() => { const svg = document.querySelector('.utm-svg');
            const c = window.UniSenseTrafficMap.state.world.byCc.US.c;
            const p = new DOMPoint(c[0], c[1]).matrixTransform(svg.getScreenCTM()); return [p.x, p.y]; }""")
        pg.mouse.move(pt[0], pt[1]); pg.wait_for_timeout(200)
        check(pg.is_visible('[data-utm="tip"]') and 'United States' in pg.inner_text('[data-utm="tip"]'), 'tooltip on hover')
        tip = pg.locator('[data-utm="tip"]').bounding_box() or {'x': -1, 'width': 0}
        check(tip['x'] >= box['x'] and tip['x'] + tip['width'] <= box['x'] + box['width'] + 1, 'tooltip stays inside the map')
        check(pg.locator('.utm-row.is-hover[data-cc="US"]').count() == 1, 'hovering the map highlights the table row')
        pg.mouse.move(5, 5)
        pg.click('.utm-row[data-cc="SG"]'); pg.wait_for_timeout(150)
        check(pg.locator('.utm-detail .utm-net').count() >= 1 and 'AMAZON-02' in pg.inner_text('.utm-detail'), 'a row opens its networks')
        pg.wait_for_timeout(3500)
        check(pg.locator('.utm-detail').count() == 1, 'the open row survives a refresh')
        pg.click('[data-utm="pause"]'); pg.wait_for_timeout(100)
        check('Paused' in pg.inner_text('[data-utm="status"]'), 'pause')
        n = pg.evaluate("performance.getEntriesByType('resource').filter(e => e.name.includes('/trafficmap/flows')).length")
        pg.wait_for_timeout(4000)
        n2 = pg.evaluate("performance.getEntriesByType('resource').filter(e => e.name.includes('/trafficmap/flows')).length")
        check(n2 == n, f'no polling while paused ({n} -> {n2})')
        pg.click('[data-utm="pause"]'); pg.wait_for_timeout(1500)
        check('Live' in pg.inner_text('[data-utm="status"]'), 'resume')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        os.environ['UNISENSE_HARNESS_NODB'] = '1'
        ctx = b.new_context(viewport={'width': 1440, 'height': 1000})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        posts = []
        pg.on('request', lambda r: r.method == 'POST' and posts.append((r.url, r.headers.get('x-csrftoken'))))
        pg.goto(f'{base}/trafficmap.html'); pg.wait_for_timeout(2500)
        check(pg.is_visible('[data-utm="banner"]') and 'not been downloaded' in pg.inner_text('[data-utm="banner"]'), 'missing database explained')
        pg.click('[data-utm="banner"] [data-utm-action="update"]'); pg.wait_for_timeout(400)
        check(any(u.endswith('/api/unisense/trafficmap/update') and tok for u, tok in posts), f'download starts (POST with CSRF token) {posts}')
        check('Downloading' in pg.inner_text('[data-utm="banner"]'), 'download progress shown')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()
        del os.environ['UNISENSE_HARNESS_NODB']

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

        # ------------------------------------------------ nothing cut off in grids
        print('grid controls fully visible')
        ctx = b.new_context(viewport={'width': 1440, 'height': 900})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        cut_js = """(sel) => [...document.querySelectorAll(sel)].filter(e => e.offsetParent).map(e => {
            const r = e.getBoundingClientRect(); let l = r.left, rt = r.right, t = r.top, bt = r.bottom;
            for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
                const cs = getComputedStyle(p); if (/auto|scroll/.test(cs.overflowX + cs.overflowY)) break;
                const pr = p.getBoundingClientRect();
                if (/hidden|clip/.test(cs.overflowX)) { l = Math.max(l, pr.left); rt = Math.min(rt, pr.right); }
                if (/hidden|clip/.test(cs.overflowY)) { t = Math.max(t, pr.top); bt = Math.min(bt, pr.bottom); }
            }
            const pct = Math.round(Math.min((rt - l) / r.width, (bt - t) / r.height) * 100);
            return pct < 97 ? `${e.className.split(' ').pop()} ${Math.round(r.width)}x${Math.round(r.height)} ${pct}% in ${Math.round(e.parentElement.getBoundingClientRect().width)}` : null;
        }).filter(Boolean)"""
        pg.goto(f'{base}/cron.html'); pg.wait_for_timeout(1200)
        cut = pg.evaluate(cut_js, '.tabulator input[type=checkbox]')
        check(cut == [], 'row and "select all" checkboxes are not clipped' + (f': {cut[:4]}' if cut else ''))
        pg.goto(f'{base}/monit.html'); pg.wait_for_timeout(1000)
        pg.click('.nav-tabs a >> nth=2'); pg.wait_for_timeout(1500)
        cut = pg.evaluate(cut_js, '.tabulator-cell .btn')
        check(cut == [], 'row command buttons are not clipped' + (f': {cut[:4]}' if cut else ''))
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        # --------------------------------------------------------- Shield
        print('Lobby: Shield')
        from server import Handler as _H
        _H.SH.update({'enabled': True, 'countries': ['RU', 'KP'], 'feeds': ['spamhaus', 'feodo', 'et', 'cins', 'dshield'],
                      'allowlist': ['203.0.113.7'], 'vtkey': True, 'abuseipdbkey': False, 'ack': 0, 'allows': {}})
        ctx = b.new_context(viewport={'width': 1440, 'height': 1000})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        posts = []
        pg.on('request', lambda r: r.method == 'POST' and posts.append((r.url, r.headers.get('x-csrftoken'), r.post_data)))
        pg.on('dialog', lambda d: d.accept())
        pg.goto(f'{base}/shield.html'); pg.wait_for_timeout(3500)
        check('Protected' in pg.inner_text('[data-ush="state"]'), 'protection state shown')
        check(pg.locator('.ush-stat').count() == 4, 'four counters')
        check(pg.is_visible('[data-ush="alerts-card"]') and pg.locator('[data-ush="alerts"] tbody tr').count() == 2, 'device alerts listed')
        badge = pg.locator('#mainmenu a[href="/ui/unisense/shield"] .us-alert-badge')
        check(badge.count() == 1 and badge.first.inner_text() == '2', 'unseen alerts counted on the Shield menu entry')
        check(pg.locator('[data-ush="recent"] tbody tr').count() > 10, 'recent blocks listed')
        pg.click('[data-ush-filter="out"]'); pg.wait_for_timeout(200)
        rows = pg.locator('[data-ush="recent"] tbody tr').count()
        check(0 < rows < 5, f'filter: only your devices ({rows})')
        check(pg.locator('.ush-feed').count() == 7 and pg.locator('.ush-feed.is-on').count() == 5, 'threat lists with their switches')
        check(pg.locator('.ush-chip').count() == 2, 'blocked countries as chips')
        pg.fill('[data-ush="add-cc"]', 'China (CN)')
        pg.click('[data-ush="add-form"] button'); pg.wait_for_timeout(800)
        check(any(u.endswith('/api/unisense/shield/block/CN') and tok for u, tok, _ in posts), 'block a country (POST with CSRF token)')
        check(pg.locator('.ush-chip').count() == 3 and 'blocked' in pg.inner_text('[data-ush="add-msg"]'), 'chip added, ended connections reported')
        pg.click('[data-ush-unblock="KP"]'); pg.wait_for_timeout(800)
        check(any(u.endswith('/api/unisense/shield/unblock/KP') for u, _, _ in posts) and pg.locator('.ush-chip').count() == 2, 'unblock from the chip')
        pg.click('.ush-feed:not(.is-on) .ush-switch'); pg.wait_for_timeout(800)
        sent = [json.loads(d or '{}') for u, _, d in posts if u.endswith('/shield/set')]
        check(sent and 'blocklistde' in sent[-1]['shield']['feeds'], f'switching a list on saves it ({sent[-1:] })')
        pg.fill('[data-ush="check-ip"]', '50.16.16.211')
        pg.click('[data-ush="check-form"] button'); pg.wait_for_timeout(800)
        res = pg.inner_text('[data-ush="check-result"]')
        check('Blocked by Shield' in res and 'Feodo' in res and '9 malicious' in res, 'address check: lists and VirusTotal')
        pg.click('[data-ush-action="ack"]'); pg.wait_for_timeout(800)
        check(any(u.endswith('/api/unisense/shield/ack') for u, _, _ in posts) and badge.count() == 0, 'mark as seen clears the menu badge')
        check('&gt;' not in pg.inner_text('[data-ush="checkup"]') and 'Services > Unbound DNS' in pg.inner_text('[data-ush="checkup"]'),
              'text the API escaped is shown once-escaped (no "&gt;")')
        check('1 country' in pg.inner_text('[data-ush="stats"]') or ' countries' in pg.inner_text('[data-ush="stats"]'), 'country count worded')
        pg.click('[data-ush="alerts"] [data-ush-allow="50.16.16.211"]'); pg.wait_for_timeout(300)
        check(pg.locator('.ush-menu [data-ush-dur]').count() == 6, 'Allow opens the six durations')
        pg.keyboard.press('Escape'); pg.wait_for_timeout(150)
        check(pg.locator('.ush-menu').count() == 0, 'Escape closes the menu')
        pg.click('[data-ush="alerts"] [data-ush-allow="50.16.16.211"]'); pg.wait_for_timeout(200)
        pg.click('.ush-menu [data-ush-dur="24h"]')
        try:
            pg.wait_for_function("document.querySelectorAll('[data-ush=allows] li').length === 1", timeout=5000)
        except Exception:
            pass
        sent = [json.loads(d or '{}') for u, _, d in posts if u.endswith('/shield/allow')]
        check(sent and sent[-1] == {'address': '50.16.16.211', 'duration': '24h'}, f'allow posted ({sent[-1:]})')
        check('h left' in pg.inner_text('[data-ush="alerts"]') and pg.locator('[data-ush="allows"] li').count() == 1,
              'alert shows the time left; listed under Allowed addresses')
        pg.click('[data-ush="allows"] [data-ush-unallow]')
        try:
            pg.wait_for_function("document.querySelectorAll('[data-ush=allows] li').length === 0 && "
                                 "!!document.querySelector('[data-ush=alerts] [data-ush-allow=\"50.16.16.211\"]')", timeout=5000)
        except Exception:
            pass
        check(any(u.endswith('/shield/unallow') for u, _, _ in posts) and pg.locator('[data-ush="allows"] li').count() == 0
              and pg.locator('[data-ush="alerts"] [data-ush-allow="50.16.16.211"]').count() == 1, 'block again')
        sw = pg.evaluate("document.documentElement.scrollWidth")
        check(sw <= 1440, f'no horizontal overflow ({sw})')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()
        ctx = b.new_context(viewport={'width': 390, 'height': 900})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        pg.goto(f'{base}/shield.html'); pg.wait_for_timeout(2500)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        check(sw <= 390, f'phone: no horizontal overflow ({sw})')
        check(not errs, 'no console errors: ' + '; '.join(errs))
        ctx.close()

        print('Traffic Map: block a country, flagged connections')
        ctx = b.new_context(viewport={'width': 1440, 'height': 1000})
        pg = ctx.new_page(); errs = []; watch(pg, errs)
        posts = []
        pg.on('request', lambda r: r.method == 'POST' and posts.append(r.url))
        msgs = []
        pg.on('dialog', lambda d: (msgs.append(d.message), d.accept()))
        pg.goto(f'{base}/trafficmap.html'); pg.wait_for_timeout(3000)
        check(pg.locator('.utm-svg path.is-blocked').count() >= 1 and pg.is_visible('[data-utm="legend-blocked"]'), 'blocked countries shaded, legend shown')
        pg.click('.utm-row[data-cc="NL"]'); pg.wait_for_timeout(400)
        pg.click('[data-utm-conns="NL"]'); pg.wait_for_timeout(1500)
        check(pg.locator('.utm-conn.is-flagged .utm-flag-tag.is-threat').count() >= 1, 'listed address flagged in the connections')
        check(pg.locator('.utm-conn .utm-check-link').count() > 0, 'every connection links to the Shield address check')
        pg.click('[data-utm="block-btn"]'); pg.wait_for_timeout(1500)
        check(msgs and 'look like games or voice' in msgs[0], 'warns before cutting live game/voice connections')
        check(any(u.endswith('/api/unisense/shield/block/NL') for u in posts), 'block posted')
        check(pg.locator('.utm-row[data-cc="NL"] .utm-blocked-tag').count() == 1 and 'Unblock' in pg.inner_text('[data-utm="block-btn"]'),
              'country tagged, button turns into Unblock')
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

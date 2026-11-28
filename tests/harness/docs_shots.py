#!/usr/bin/env python3
"""Regenerate the README screenshots in docs/screenshots/.

Usage: docs_shots.py --core /path/to/core [--chrome /usr/bin/google-chrome]
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from server import serve

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / 'docs/screenshots'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--chrome', default=None)
    ap.add_argument('--port', type=int, default=8780)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    httpd = serve(a.core, HERE / 'site/pages', a.port)
    base = f'http://127.0.0.1:{a.port}/pages'

    def shot(b, name, page, mode='dark', size=(1440, 900), init='', action=None, wait=1200, clip=None, mobile=False):
        ctx = b.new_context(viewport={'width': size[0], 'height': size[1]},
                            device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile)
        ctx.add_init_script(f"localStorage.setItem('unisense.mode', '{mode}');" + init)
        pg = ctx.new_page()
        page, _, frag = page.partition('#')
        pg.goto(f'{base}/{page}.html' + ('#' + frag if frag else ''))
        pg.wait_for_timeout(wait)
        if action:
            action(pg)
        pg.mouse.move(size[0] - 5, size[1] - 5) if not action else None
        pg.screenshot(path=str(OUT / f'{name}.png'), clip=clip)
        ctx.close()
        print('wrote', name)

    def flyout(pg):
        pg.hover('#mainmenu > div > a[href="#System"]'); pg.wait_for_timeout(400)
        pg.hover('#System > a[href="#System_Settings"]'); pg.wait_for_timeout(500)

    def dialog(pg):
        pg.evaluate("() => $('.modal[id^=dialog]').first().modal('show')"); pg.wait_for_timeout(600)

    def appearance(pg):
        pg.click('.us-appearance-btn'); pg.wait_for_timeout(250)

    def drawer(pg):
        pg.click('.navbar-toggle'); pg.wait_for_timeout(600)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chrome)
        shot(b, 'dashboard-dark', 'dashboard', 'dark', wait=4500)
        shot(b, 'dashboard-light', 'dashboard', 'light', wait=4500)
        shot(b, 'grid-light', 'cron', 'light')
        shot(b, 'trafficmap-dark', 'trafficmap', 'dark', wait=6000)
        shot(b, 'trafficmap-light', 'trafficmap', 'light', size=(1440, 1400), wait=6000)
        shot(b, 'shield-dark', 'shield', 'dark', size=(1440, 1200), wait=3500)
        shot(b, 'privacy-dark', 'shield#privacy', 'dark', size=(1440, 1200), wait=3500)
        shot(b, 'requests-dark', 'shield#requests', 'dark', size=(1440, 700), wait=2500)

        def block_panel(pg):
            pg.click('.utm-row[data-cc="NL"]'); pg.wait_for_timeout(400)
            pg.click('[data-utm-conns="NL"]'); pg.wait_for_timeout(1500)
            pg.evaluate("document.querySelector('[data-utm=conns]').scrollIntoView({block: 'start'}); window.scrollBy(0, -76)")
            pg.wait_for_timeout(400)
            pg.mouse.move(1435, 895)
        shot(b, 'trafficmap-block', 'trafficmap', 'light', wait=3500, action=block_panel)
        shot(b, 'networkmap-dark', 'networkmap', 'dark', wait=7000)
        shot(b, 'networkmap-light', 'networkmap', 'light', wait=7000)
        shot(b, 'networkmap-device', 'networkmap', 'light', wait=2500,
             action=lambda pg: (pg.click('[data-mac="7c:2e:0d:de:ad:01"]'), pg.wait_for_timeout(700)))
        shot(b, 'sidebar-collapsed', 'cron', 'dark', init="localStorage.setItem('toggle_sidebar_preset','1');", action=flyout)
        shot(b, 'dialog-light', 'cron', 'light', action=dialog)
        shot(b, 'legacy-dark', 'legacy', 'dark')
        shot(b, 'appearance', 'legacy', 'light', action=appearance, clip={'x': 560, 'y': 0, 'width': 880, 'height': 330})
        shot(b, 'login-dark', 'login', 'dark', size=(1200, 800))
        shot(b, 'login-light', 'login', 'light', size=(1200, 800))
        shot(b, 'mobile-dashboard', 'dashboard', 'dark', size=(390, 844), wait=3500, mobile=True)
        shot(b, 'mobile-menu', 'cron', 'light', size=(390, 844), action=drawer, mobile=True)
        shot(b, 'mobile-networkmap', 'networkmap', 'dark', size=(390, 844), wait=4000, mobile=True)
        b.close()
    httpd.shutdown()


if __name__ == '__main__':
    main()

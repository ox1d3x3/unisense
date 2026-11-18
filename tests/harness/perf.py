#!/usr/bin/env python3
"""Measure how smooth the theme is: frame times and main-thread work while
scrolling, collapsing the sidebar, hovering the menu, switching light/dark and
watching the Network Map with live traffic.

  python3 perf.py --core /path/to/core [--throttle 4] [--json out.json]

Each scenario reports the frame interval p50/p95/max (from requestAnimationFrame),
frames slower than 25 ms ("long"), style/layout/paint time from the DevTools
Performance domain, and cumulative layout shift. With --budget the script fails
when a scenario exceeds the budgets below (used by CI)."""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from server import serve

HERE = Path(__file__).resolve().parent

# a large desktop screen at 125 % scaling, like a typical 1440p laptop/monitor
VIEW = {'width': 1840, 'height': 1040}
DSF = 1.25

FRAME_PROBE = """
() => {
  window.__frames = [];
  window.__cls = 0;
  let last = performance.now();
  const tick = (t) => { window.__frames.push(t - last); last = t; window.__raf = requestAnimationFrame(tick); };
  window.__raf = requestAnimationFrame(tick);
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
      .observe({ type: 'layout-shift', buffered: false });
  } catch (e) {}
}
"""
FRAME_STOP = "() => { cancelAnimationFrame(window.__raf); return { frames: window.__frames.slice(1), cls: window.__cls }; }"

METRICS = ('RecalcStyleDuration', 'LayoutDuration', 'ScriptDuration', 'TaskDuration', 'LayoutCount', 'RecalcStyleCount')

# p95 frame interval (ms) and long-frame share allowed per scenario at 4x CPU throttle
BUDGET = {'p95': 40.0, 'long_pct': 20.0, 'cls': 0.02}


def metrics(cdp):
    m = {x['name']: x['value'] for x in cdp.send('Performance.getMetrics')['metrics']}
    return {k: m.get(k, 0.0) for k in METRICS}


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


TRACE_CATS = ['devtools.timeline', 'disabled-by-default-devtools.timeline', 'cc', 'gpu']


def trace_costs(raw):
    """main-thread Paint and raster-thread RasterTask time (ms) from a Chrome trace"""
    try:
        events = json.loads(raw)['traceEvents']
    except Exception:
        return 0.0, 0.0
    paint = raster = 0.0
    for e in events:
        if e.get('ph') != 'X':
            continue
        name = e.get('name')
        if name == 'Paint':
            paint += e.get('dur', 0) / 1000.0
        elif name in ('RasterTask', 'TileManager::RasterTask'):
            raster += e.get('dur', 0) / 1000.0
    return round(paint, 1), round(raster, 1)


def measure(page, cdp, action, browser=None):
    if browser is not None:
        browser.start_tracing(page=page, categories=TRACE_CATS)
    page.evaluate(FRAME_PROBE)
    page.wait_for_timeout(100)
    before = metrics(cdp)
    action()
    res = page.evaluate(FRAME_STOP)
    after = metrics(cdp)
    paint_ms = raster_ms = 0.0
    if browser is not None:
        paint_ms, raster_ms = trace_costs(browser.stop_tracing())
    frames = res['frames']
    delta = {k: after[k] - before[k] for k in METRICS}
    long_frames = [f for f in frames if f > 25]
    return {
        'frames': len(frames),
        'p50': round(pct(frames, 50), 1),
        'p95': round(pct(frames, 95), 1),
        'max': round(max(frames) if frames else 0, 1),
        'long_pct': round(100.0 * len(long_frames) / max(1, len(frames)), 1),
        'style_ms': round(delta['RecalcStyleDuration'] * 1000, 1),
        'layout_ms': round(delta['LayoutDuration'] * 1000, 1),
        'script_ms': round(delta['ScriptDuration'] * 1000, 1),
        'layouts': int(delta['LayoutCount']),
        'paint_ms': paint_ms,
        'raster_ms': raster_ms,
        'cls': round(res['cls'], 4),
    }


def wheel(page, total, steps=40, delay=16):
    for _ in range(steps):
        page.mouse.wheel(0, total / steps)
        page.wait_for_timeout(delay)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--chrome', default=None)
    ap.add_argument('--port', type=int, default=8771)
    ap.add_argument('--throttle', type=float, default=4.0)
    ap.add_argument('--json', default=None)
    ap.add_argument('--budget', action='store_true', help='fail when a scenario is over budget')
    ap.add_argument('--mode', default='light')
    ap.add_argument('--css', default='', help='extra CSS injected into every page (A/B experiments)')
    ap.add_argument('--init', default='', help='extra script run before page scripts (A/B experiments)')
    ap.add_argument('--only', default='', help='comma list of scenario prefixes to run')
    a = ap.parse_args()

    serve(a.core, HERE / 'site/pages', a.port)
    only = [x for x in a.only.split(',') if x]

    def want(name):
        return not only or any(name.startswith(x) for x in only)
    base = f'http://127.0.0.1:{a.port}/pages'
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=a.chrome, args=['--enable-gpu-rasterization'])

        def open_page(name, collapsed=False):
            ctx = browser.new_context(viewport=VIEW, device_scale_factor=DSF, color_scheme='light')
            init = f"localStorage.setItem('unisense.mode', '{a.mode}');"
            if collapsed:
                init += "localStorage.setItem('toggle_sidebar_preset', '1');"
            if a.css:
                init += ("document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');"
                         " s.textContent = " + json.dumps(a.css) + "; document.head.appendChild(s); });")
            ctx.add_init_script(init + a.init)
            page = ctx.new_page()
            page.goto(f'{base}/{name}.html', wait_until='load')
            page.wait_for_timeout(2500 if name in ('dashboard', 'networkmap', 'trafficmap') else 1200)
            cdp = ctx.new_cdp_session(page)
            cdp.send('Performance.enable')
            if a.throttle > 1:
                cdp.send('Emulation.setCPUThrottlingRate', {'rate': a.throttle})
            page.mouse.move(VIEW['width'] * 0.6, VIEW['height'] * 0.6)
            return ctx, page, cdp

        # 1. scrolling long pages
        for name in [n for n in ('dashboard', 'legacy', 'networkmap', 'trafficmap') if want('scroll:' + n)]:
            ctx, page, cdp = open_page(name)
            results[f'scroll:{name}'] = measure(page, cdp, browser=browser, action=lambda: (wheel(page, 2400), wheel(page, -2400)))
            ctx.close()

        # 2. sidebar collapse / expand
        if want('sidebar:toggle'):
            ctx, page, cdp = open_page('dashboard')
            toggle = '#toggle_sidebar, .toggle-sidebar'

            def collapse():
                for _ in range(2):
                    page.click(toggle)
                    page.wait_for_timeout(450)
            results['sidebar:toggle'] = measure(page, cdp, browser=browser, action=collapse)
            ctx.close()

        # 3. hovering down the menu
        if want('menu:hover'):
            ctx, page, cdp = open_page('dashboard')

            def hover_menu():
                items = page.query_selector_all('#mainmenu > div > a, #mainmenu .list-group-item')[:12]
                for el in items:
                    box = el.bounding_box()
                    if box:
                        page.mouse.move(box['x'] + 30, box['y'] + box['height'] / 2, steps=4)
                        page.wait_for_timeout(40)
            results['menu:hover'] = measure(page, cdp, browser=browser, action=hover_menu)
            ctx.close()

        # 4. collapsed rail fly-outs
        if want('rail:hover'):
            ctx, page, cdp = open_page('dashboard', collapsed=True)

            def hover_rail():
                for y in range(130, 700, 48):
                    page.mouse.move(30, y, steps=4)
                    page.wait_for_timeout(60)
                page.mouse.move(900, 500, steps=4)
            results['rail:hover'] = measure(page, cdp, browser=browser, action=hover_rail)
            ctx.close()

        # 5. light <-> dark
        if want('mode:switch'):
            ctx, page, cdp = open_page('dashboard')

            def switch_mode():
                for m in ('dark', 'light'):
                    page.evaluate(f"() => window.UniSense && window.UniSense.setMode('{m}')")
                    page.wait_for_timeout(500)
            results['mode:switch'] = measure(page, cdp, browser=browser, action=switch_mode)
            ctx.close()

        # 7. Traffic Map: canvas flows + polls
        if want('trafficmap:live'):
            ctx, page, cdp = open_page('trafficmap')
            results['trafficmap:live'] = measure(page, cdp, lambda: page.wait_for_timeout(10000), browser=browser)
            ctx.close()

        # 6. Network Map with live traffic (several polls) — nothing should move
        if want('networkmap:live'):
            ctx, page, cdp = open_page('networkmap')
            results['networkmap:live'] = measure(page, cdp, browser=browser, action=lambda: page.wait_for_timeout(13000))
            ctx.close()

        browser.close()

    over = []
    print(f"{'scenario':22} {'frames':>6} {'p50':>6} {'p95':>6} {'max':>7} {'long%':>6} "
          f"{'style':>7} {'layout':>7} {'script':>7} {'layouts':>7} {'paint':>7} {'raster':>7} {'cls':>7}")
    for k, r in results.items():
        print(f"{k:22} {r['frames']:6} {r['p50']:6} {r['p95']:6} {r['max']:7} {r['long_pct']:6} "
              f"{r['style_ms']:7} {r['layout_ms']:7} {r['script_ms']:7} {r['layouts']:7} {r['paint_ms']:7} {r['raster_ms']:7} {r['cls']:7}")
        if r['p95'] > BUDGET['p95'] or r['long_pct'] > BUDGET['long_pct'] or r['cls'] > BUDGET['cls']:
            over.append(k)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2))
    if over:
        print('over budget: ' + ', '.join(over))
    if a.budget and over:
        sys.exit(1)


if __name__ == '__main__':
    main()

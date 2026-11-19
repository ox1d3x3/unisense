#!/usr/bin/env python3
"""Layout audit: find cut-off text and boxes on every rendered page.

    python3 render.py --core CORE --all          # renders ~117 core pages into site/pages/auto
    python3 audit_layout.py --core CORE [--pages auto] [--width 1440,1024,390] [--json out.json]

For each page (with every tab pane shown and every edit dialog opened in turn)
the script reports elements that

  clipped-x / clipped-y   hide part of their content (overflow hidden/clip)
  ellipsis                truncate text with an ellipsis (often intended: listed separately)
  spills                  let text run outside their box
  beyond-viewport         stick out of the page horizontally (outside scroll areas)

and measures the sizes of form controls (inputs, selects, buttons, checkboxes,
toggle switches) so outliers stand out. Findings are grouped by a CSS
signature across pages, most frequent first.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

from server import serve

HERE = Path(__file__).resolve().parent

DETECT = r"""
(scope) => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  const sig = (el) => {
    const parts = [];
    let e = el;
    for (let i = 0; i < 3 && e && e !== document.body; i++, e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      const cls = [...e.classList].filter(c => !/^(col-|ng-|active|in|show|open|fade|is-)/.test(c)).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const scrollParentX = (el) => {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e).overflowX;
      if (o === 'auto' || o === 'scroll') return true;
    }
    return false;
  };
  /* how much of a control is actually visible: clipped by any ancestor that
     hides overflow (scroll containers are fine: the user can scroll) */
  const visibleRect = (el) => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'contents') continue;
      const ox = cs.overflowX, oy = cs.overflowY;
      if (ox === 'auto' || ox === 'scroll' || oy === 'auto' || oy === 'scroll') break;
      const er = e.getBoundingClientRect();
      if (ox === 'hidden' || ox === 'clip') { l = Math.max(l, er.left); rt = Math.min(rt, er.right); }
      if (oy === 'hidden' || oy === 'clip') { t = Math.max(t, er.top); b = Math.min(b, er.bottom); }
    }
    return { w: Math.max(0, rt - l), h: Math.max(0, b - t) };
  };
  const CONTROL = 'input:not([type=hidden]), select, textarea, button, .btn, .bootstrap-select > .dropdown-toggle, ' +
    '.fa-toggle-on, .fa-toggle-off, .command-toggle, .label, .badge, .usn-chip, .us-sw';
  const ownText = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
  const root = scope ? document.querySelector(scope) : document.body;
  if (!root) return out;
  for (const el of root.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'svg', 'path', 'OPTION', 'CANVAS', 'BR', 'TEMPLATE'].includes(el.tagName)) continue;
    if (el.closest('svg, .modal:not(.in), .dropdown-menu:not(.show):not(.open), .tooltip, .popover, [hidden]')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const text = (el.innerText || '').trim().slice(0, 60);
    const add = (kind, extra) => out.push(Object.assign({ kind, sig: sig(el), text, w: Math.round(r.width) }, extra || {}));
    if (r.right > vw + 1 && !scrollParentX(el) && cs.position !== 'fixed') add('beyond-viewport', { right: Math.round(r.right) });
    if (el.matches(CONTROL) && cs.opacity !== '0' && r.width >= 6 && r.height >= 6 && !el.closest('.selectpicker, select.bs-select-hidden')) {
      const v = visibleRect(el);
      if (v.w > 0 && v.h > 0 && (v.w < r.width - 1.5 || v.h < r.height - 1.5)) {
        out.push({ kind: 'cut-control', sig: sig(el), text: (el.getAttribute('aria-label') || el.title || el.type || text || el.tagName).slice(0, 40),
                   w: Math.round(r.width), need: Math.round(r.width) + 'x' + Math.round(r.height), have: Math.round(v.w) + 'x' + Math.round(v.h) });
      }
    }
    const ox = cs.overflowX, oy = cs.overflowY;
    if ((ox === 'hidden' || ox === 'clip') && el.scrollWidth > el.clientWidth + 2 && text) {
      add(cs.textOverflow === 'ellipsis' ? 'ellipsis' : 'clipped-x', { need: el.scrollWidth, have: el.clientWidth });
    }
    if ((oy === 'hidden' || oy === 'clip') && el.scrollHeight > el.clientHeight + 2 && text && el.clientHeight > 4) {
      add('clipped-y', { need: el.scrollHeight, have: el.clientHeight });
    }
    if (ox === 'visible' && ownText(el) && el.scrollWidth > el.clientWidth + 3 && cs.display !== 'inline' && cs.whiteSpace === 'nowrap') {
      add('spills', { need: el.scrollWidth, have: el.clientWidth });
    }
  }
  return out;
}
"""

SIZES = r"""
(scope) => {
  const root = scope ? document.querySelector(scope) : document.body;
  const pick = (sel) => [...root.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect(); return r.width && r.height && !e.closest('.modal:not(.in), [hidden]');
  }).map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; });
  return {
    input: pick('input.form-control, input[type=text], input[type=password], input[type=number]'),
    select: pick('.bootstrap-select > .dropdown-toggle, select.form-control:not(.selectpicker)'),
    button: pick('.btn:not(.btn-xs):not(.dropdown-toggle)'),
    checkbox: pick('input[type=checkbox]'),
    toggle: pick('.fa-toggle-on, .fa-toggle-off'),
  };
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--chrome', default='/usr/bin/google-chrome')
    ap.add_argument('--port', type=int, default=8811)
    ap.add_argument('--pages', default='main,auto', help='main (hand-picked pages), auto (all core pages)')
    ap.add_argument('--width', default='1440,1024,390')
    ap.add_argument('--only', default='', help='substring filter on page names')
    ap.add_argument('--json', default='')
    ap.add_argument('--top', type=int, default=40)
    a = ap.parse_args()

    serve(a.core, HERE / 'site/pages', a.port)
    pages = []
    if 'main' in a.pages:
        pages += [('', p.stem) for p in sorted((HERE / 'site/pages').glob('*.html'))]
    if 'auto' in a.pages:
        pages += [('auto/', p.stem) for p in sorted((HERE / 'site/pages/auto').glob('*.html'))]
    if a.only:
        pages = [p for p in pages if a.only in p[1]]
    widths = [int(w) for w in a.width.split(',')]

    findings = defaultdict(lambda: {'count': 0, 'pages': set(), 'example': None})
    sizes = defaultdict(list)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chrome)
        for w in widths:
            ctx = b.new_context(viewport={'width': w, 'height': 900})
            ctx.add_init_script("localStorage.setItem('unisense.mode','dark');")
            page = ctx.new_page()
            errs = []
            page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
            page.on('pageerror', lambda e: errs.append(str(e)))
            for sub, name in pages:
                errs.clear()
                try:
                    page.goto(f'http://127.0.0.1:{a.port}/pages/{sub}{name}.html', wait_until='load', timeout=30000)
                    page.wait_for_timeout(700)
                    # every tab pane visible, so each form is laid out
                    page.add_style_tag(content='.tab-content > .tab-pane { display: block !important; opacity: 1 !important; }')
                    page.wait_for_timeout(100)
                    scopes = [None]
                    modals = page.evaluate("[...document.querySelectorAll('.modal[id]')].map(m => m.id)")
                    results = [(None, page.evaluate(DETECT, None), page.evaluate(SIZES, None))]
                    for mid in modals[:6]:
                        page.evaluate("(id) => window.jQuery && jQuery(document.getElementById(id)).modal('show')", mid)
                        page.wait_for_timeout(350)
                        sc = f'[id="{mid}"] .modal-dialog'
                        results.append((mid, page.evaluate(DETECT, sc), page.evaluate(SIZES, sc)))
                        page.evaluate("(id) => window.jQuery && jQuery(document.getElementById(id)).modal('hide')", mid)
                        page.wait_for_timeout(250)
                    del scopes
                except Exception as e:
                    print(f'  {name}@{w}: {type(e).__name__}: {str(e)[:80]}', file=sys.stderr)
                    continue
                for msg in errs:
                    if 'favicon' in msg or 'net::ERR' in msg or 'Failed to load resource' in msg:
                        continue
                    results.append((None, [{'kind': 'console-error', 'sig': msg[:90], 'text': msg[:50], 'w': 0}], {}))
                for mid, found, sz in results:
                    for f in found:
                        key = (f['kind'], f['sig'], w)
                        g = findings[key]
                        g['count'] += 1
                        g['pages'].add(name + (f' [{mid}]' if mid else ''))
                        g['example'] = g['example'] or f
                    for k, v in sz.items():
                        sizes[(k, w)] += v
            ctx.close()
        b.close()

    order = {'cut-control': 0, 'console-error': 1, 'beyond-viewport': 2, 'clipped-x': 3, 'clipped-y': 4, 'spills': 5, 'ellipsis': 6}
    rows = sorted(findings.items(), key=lambda kv: (order.get(kv[0][0], 9), -len(kv[1]['pages']), -kv[1]['count']))
    print(f'{len(rows)} finding groups')
    for (kind, sig, w), g in rows[:a.top]:
        ex = g['example']
        detail = ''
        if 'need' in ex:
            detail = f" need {ex['need']} have {ex['have']}"
        elif 'right' in ex:
            detail = f" right {ex['right']}"
        pages_s = ', '.join(sorted(g['pages'])[:3]) + (' …' if len(g['pages']) > 3 else '')
        print(f"{kind:15} @{w:<4} x{g['count']:<4} {len(g['pages']):>3}p  {sig[:90]}\n      {detail}  \"{ex['text'][:50]}\"  [{pages_s}]")
    print('\ncontrol sizes (height px: min / median / max, count)')
    for (k, w), v in sorted(sizes.items()):
        if not v:
            continue
        hs = sorted(h for _, h in v)
        ws = sorted(x for x, _ in v)
        print(f"  {k:9} @{w:<4} h {hs[0]}/{hs[len(hs) // 2]}/{hs[-1]}  w {ws[0]}/{ws[len(ws) // 2]}/{ws[-1]}  n={len(v)}")
    if a.json:
        Path(a.json).write_text(json.dumps([
            {'kind': k[0], 'sig': k[1], 'width': k[2], 'count': g['count'], 'pages': sorted(g['pages']), 'example': g['example']}
            for k, g in rows], indent=1))


if __name__ == '__main__':
    main()

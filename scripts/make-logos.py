#!/usr/bin/env python3
"""Generate UniSense logo SVGs (text converted to outlines so they render
identically everywhere). Requires fontTools and the Inter typeface (OFL).
Usage: make-logos.py <Inter-Bold.otf> <Inter-Medium.otf> <outdir>"""
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen


def text_path(font_path, text, size, x, y, tracking=0.0):
    f = TTFont(font_path)
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    upm = f['head'].unitsPerEm
    s = size / upm
    pen = SVGPathPen(gs)
    cx = 0.0
    for ch in text:
        g = cmap[ord(ch)]
        tp = TransformPen(pen, (s, 0, 0, -s, x + cx, y))
        gs[g].draw(tp)
        cx += gs[g].width * s + tracking
    return pen.getCommands(), cx


MARK = '''<defs>
    <linearGradient id="us-bg" x1="6" y1="2" x2="44" y2="46" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#2f8dff"/>
      <stop offset=".55" stop-color="#006fff"/>
      <stop offset="1" stop-color="#0052d4"/>
    </linearGradient>
    <linearGradient id="us-sheen" x1="24" y1="0" x2="24" y2="20" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fff" stop-opacity=".16"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <g{transform}>
    <rect width="48" height="48" rx="12" fill="url(#us-bg)"/>
    <rect x="1" y="1" width="46" height="46" rx="11" fill="none" stroke="#fff" stroke-opacity=".14"/>
    <rect width="48" height="20" rx="12" fill="url(#us-sheen)"/>
    <path d="M14.5 15.5v10a9.5 9.5 0 0 0 19 0v-10" fill="none" stroke="#fff" stroke-width="4.6" stroke-linecap="round"/>
    <path d="M24 9.2l4.6 1.7v3.2c0 2.9-1.9 5.1-4.6 6-2.7-.9-4.6-3.1-4.6-6v-3.2z" fill="#fff"/>
  </g>'''


def main(bold, medium, out):
    icon = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" role="img" aria-label="UniSense">\n  '
            + MARK.replace('{transform}', '') + '\n</svg>\n')
    open(f'{out}/icon-logo.svg', 'w').write(icon)

    name, w1 = text_path(bold, 'UniSense', 19, 46, 21, tracking=0.15)
    sub, w2 = text_path(medium, 'OPNsense', 10.5, 46.5, 35, tracking=0.35)
    width = int(46 + max(w1, w2) + 4)
    logo = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 40" role="img" aria-label="UniSense">\n  '
            + MARK.replace('{transform}', ' transform="translate(2 2) scale(.75)"')
            + f'\n  <path d="{name}" fill="#ffffff"/>\n  <path d="{sub}" fill="#8a929c"/>\n</svg>\n')
    open(f'{out}/default-logo.svg', 'w').write(logo)


if __name__ == '__main__':
    main(*sys.argv[1:4])

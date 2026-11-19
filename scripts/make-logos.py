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
    <linearGradient id="us-bg" x1="4" y1="2" x2="44" y2="46" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#3d9bff"/>
      <stop offset=".5" stop-color="#0a66ff"/>
      <stop offset="1" stop-color="#3b3fe0"/>
    </linearGradient>
    <radialGradient id="us-glow" cx="14" cy="8" r="30" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fff" stop-opacity=".28"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <g{transform}>
    <rect width="48" height="48" rx="13" fill="url(#us-bg)"/>
    <rect width="48" height="48" rx="13" fill="url(#us-glow)"/>
    <rect x=".75" y=".75" width="46.5" height="46.5" rx="12.25" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="1.5"/>
    <path d="M24 23 13.5 13M24 23 34.5 13" stroke="#fff" stroke-opacity=".45" stroke-width="1.6"/>
    <path d="M13.5 13v10.5a10.5 10.5 0 0 0 21 0V13" fill="none" stroke="#fff" stroke-width="4.2" stroke-linecap="round"/>
    <circle cx="13.5" cy="13" r="3.3" fill="#fff"/>
    <circle cx="34.5" cy="13" r="3.3" fill="#fff"/>
    <circle cx="24" cy="23" r="2.6" fill="#fff" fill-opacity=".9"/>
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

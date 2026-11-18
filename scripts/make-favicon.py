#!/usr/bin/env python3
"""Render build/images/icon-logo.svg to favicon.png (64x64, transparent)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

img = Path(sys.argv[1])
svg = (img / 'icon-logo.svg').read_text()
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=sys.argv[2] if len(sys.argv) > 2 else None)
    pg = b.new_page(viewport={'width': 64, 'height': 64})
    pg.set_content(f'<html><body style="margin:0;background:transparent">'
                   f'<div style="width:64px;height:64px">{svg.replace("<svg ", "<svg width=\"64\" height=\"64\" ")}</div>'
                   f'</body></html>')
    pg.screenshot(path=str(img / 'favicon.png'), omit_background=True, clip={'x': 0, 'y': 0, 'width': 64, 'height': 64})
    b.close()

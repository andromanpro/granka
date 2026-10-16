"""Render the editable SVG brand assets and Windows multi-resolution icon."""
from pathlib import Path
import argparse
import os
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'static/brand'


def wordmark(font_path):
    font = TTFont(font_path)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    units = font['head'].unitsPerEm
    paths, x = [], 0
    for char in 'Гранка':
        glyph = glyphs[cmap[ord(char)]]
        pen = SVGPathPen(glyphs)
        glyph.draw(pen)
        paths.append(f'<path transform="translate({x} 0)" d="{pen.getCommands()}"/>')
        x += glyph.width
    return ''.join(paths), x, units


def build(font=None):
    mark = (BRAND / 'mark.svg').read_text(encoding='utf-8')
    if font:
        paths, width, units = wordmark(font)
        scale = 43 / units
        for name, color in [('logo', '#f1eade'), ('logo-ink', '#14242d')]:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {82 + width*scale:.2f} 64">'
                   '<title>Гранка</title>' + mark.replace('<svg ', '<svg x="0" y="0" width="64" height="64" ', 1) +
                   f'<g fill="{color}" transform="translate(80 45) scale({scale} {-scale})">{paths}</g></svg>')
            (BRAND / f'{name}.svg').write_text(svg, encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get('GRANKA_BROWSER_CHANNEL') or ('msedge' if os.name=='nt' else None), headless=True)
        page = browser.new_page(viewport={'width':1024,'height':1024}, device_scale_factor=1)
        def render(svg, filename, size):
            page.set_viewport_size({'width':size,'height':size})
            page.set_content('<style>html,body{margin:0;background:transparent}svg{display:block;width:100%;height:100%}</style>'+svg)
            page.screenshot(path=str(BRAND / filename), omit_background=True)
        render(mark, 'app-icon.png', 512)
        render(mark, 'apple-touch-icon.png', 180)
        render(mark, 'favicon-32.png', 32)
        page.set_viewport_size({'width':620,'height':160})
        page.set_content('<style>html,body{margin:0;background:#14242d}body{display:grid;place-items:center;height:160px}svg{width:390px;height:104px}</style>'+(BRAND/'logo.svg').read_text(encoding='utf-8'))
        page.screenshot(path=str(BRAND/'brand-preview.png'))
        browser.close()
    with Image.open(BRAND / 'app-icon.png') as icon:
        icon.save(BRAND / 'granka.ico', sizes=[(n,n) for n in [16,24,32,48,64,128,256]])
    print('Brand assets built in', BRAND)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,help='Optional local TTF for rebuilding the wordmark; existing SVG is used otherwise')
    build(parser.parse_args().font)

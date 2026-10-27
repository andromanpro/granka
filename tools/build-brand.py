"""Render the editable SVG brand assets and Windows multi-resolution icon."""
from pathlib import Path
import argparse
import os
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'static/brand'


def wordmark(font_path):
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    cap = getattr(font['OS/2'], 'sCapHeight', font['head'].unitsPerEm * .7)
    paths, x = [], 0
    for char in 'Гранка':
        glyph = glyphs[cmap[ord(char)]]
        pen = SVGPathPen(glyphs)
        glyph.draw(pen)
        paths.append(f'<path transform="translate({x} 0)" d="{pen.getCommands()}"/>')
        x += glyph.width
    return ''.join(paths), x, cap


def build(font=None):
    mark = (BRAND / 'mark.svg').read_text(encoding='utf-8')
    favicon = (BRAND / 'favicon.svg').read_text(encoding='utf-8')
    if font:
        paths, width, cap = wordmark(font)
        scale = 35 / cap
        for name, symbol, color in [('logo', 'symbol', '#ece8d9'), ('logo-ink', 'symbol-ink', '#263020'),
                                    ('logo-mono', 'symbol-mono', '#ece8d9'), ('logo-mono-ink', 'symbol-mono-ink', '#263020')]:
            symbol_svg = (BRAND / (symbol + '.svg')).read_text(encoding='utf-8')
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {80 + width*scale:.3f} 64">'
                   '<title>Гранка — Скобочки</title>' + symbol_svg.replace('<svg ', '<svg x="0" y="0" width="64" height="64" ', 1) +
                   f'<g fill="{color}" transform="translate(80 46) scale({scale} {-scale})">{paths}</g></svg>')
            (BRAND / f'{name}.svg').write_text(svg, encoding='utf-8')
    for file in BRAND.glob('*.svg'):
        ET.parse(file)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get('GRANKA_BROWSER_CHANNEL') or ('msedge' if os.name=='nt' else None), headless=True)
        page = browser.new_page(viewport={'width':1024,'height':1024}, device_scale_factor=1)
        def render(svg, filename, size):
            page.set_viewport_size({'width':size,'height':size})
            page.set_content('<style>html,body{margin:0;background:transparent}svg{display:block;width:100%;height:100%}</style>'+svg)
            page.screenshot(path=str(BRAND / filename), omit_background=True)
        native = []
        for size in [16, 24, 32, 48, 64, 128, 256]:
            filename = f'favicon-{size}.png' if size <= 32 else f'icon-{size}.png'
            render(favicon if size <= 32 else mark, filename, size)
            native.append((size, filename))
        render(mark, 'app-icon.png', 512)
        render(mark, 'apple-touch-icon.png', 180)
        page.set_viewport_size({'width':620,'height':160})
        page.set_content('<style>html,body{margin:0;background:#181b18}body{display:grid;place-items:center;height:160px}svg{width:390px;height:104px}</style>'+(BRAND/'logo.svg').read_text(encoding='utf-8'))
        page.screenshot(path=str(BRAND/'brand-preview.png'))
        browser.close()
    with Image.open(BRAND / 'app-icon.png') as icon:
        frames = [Image.open(BRAND / filename).copy() for _, filename in native]
        icon.save(BRAND / 'granka.ico', sizes=[(size,size) for size,_ in native], append_images=frames)
    with Image.open(BRAND / 'granka.ico') as ico:
        if ico.ico.sizes() != {(size,size) for size,_ in native}:
            raise ValueError('ICO: missing native icon sizes')
        for size, filename in native:
            with Image.open(BRAND / filename) as png:
                if ico.ico.getimage((size,size)).convert('RGBA').tobytes() != png.convert('RGBA').tobytes():
                    raise ValueError(f'ICO {size}: pixels differ from native PNG')
    print('Brand assets built in', BRAND)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,help='Optional local TTF for rebuilding the wordmark; existing SVG is used otherwise')
    build(parser.parse_args().font)

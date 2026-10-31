"""Generate derived brand applications and sync HACS assets. No network.

Optional --source imports/resizes the user's PNG (requires Pillow, dev only).
Default only regenerates SVG applications and copies delivery assets.
--render-png renders the vector on a transparent canvas (dev Playwright required).
"""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'arandu_stack/app/static/assets/brand'
HA = ROOT / 'custom_components/local_nlu/brand'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--render-png', action='store_true')
    args = parser.parse_args()
    if args.source:
        from PIL import Image
        shutil.copyfile(args.source, BRAND / 'arandu-icon-original.png')
        image = Image.open(args.source).convert('RGB')
        for size in (32, 64, 192, 512):
            image.resize((size, size), Image.Resampling.LANCZOS).save(
                BRAND / f'arandu-icon-original-{size}.png', optimize=True)
    icon = (BRAND / 'arandu-icon.svg').read_text(encoding='utf-8')
    if args.render_png:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 512, 'height': 512})
            page.set_content('<style>html,body{margin:0;background:transparent}'
                             'svg{display:block;width:512px;height:512px}</style>' + icon)
            page.screenshot(path=str(BRAND / 'arandu-icon.png'), omit_background=True)
            browser.close()
    mono = icon.replace('fill="#07575a"', 'fill="none"').replace('url(#gold)', '#07575a')
    (BRAND / 'arandu-monochrome.svg').write_text(mono, encoding='utf-8')
    inner = icon.replace('viewBox="0 0 512 512"', 'viewBox="0 0 512 512" width="96" height="96"')
    horizontal = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 96">' + inner
                  + '<text x="116" y="52" fill="#07575a" font-family="Inter,system-ui,sans-serif" '
                    'font-size="46" letter-spacing="-2">arandu</text><text x="118" y="77" '
                    'fill="#07575a" font-family="system-ui,sans-serif" font-size="12" '
                    'letter-spacing="4">ASSISTANT</text></svg>')
    (BRAND / 'arandu-horizontal.svg').write_text(horizontal, encoding='utf-8')
    HA.mkdir(exist_ok=True)
    for file in [*BRAND.glob('*.svg'), BRAND / 'arandu-icon-original-512.png']:
        shutil.copyfile(file, HA / file.name)
    shutil.copyfile(BRAND / 'arandu-icon.png', ROOT / 'arandu_stack/icon.png')


if __name__ == '__main__':
    main()

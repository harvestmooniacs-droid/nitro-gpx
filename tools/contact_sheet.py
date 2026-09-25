#!/usr/bin/env python3
"""Contact sheet of a dump: python tools/contact_sheet.py DUMP_DIR [kind] [--max N]"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

THUMB = 160


def sheet(dump, kind=None, max_n=48, cols=8, out=None):
    dump = Path(dump)
    man = json.loads((dump / 'manifest.json').read_text(encoding='utf-8'))
    rs = [r for r in man['resources'] if 'png' in r and (not kind or r['kind'] == kind)][:max_n]
    rows = max(1, -(-len(rs) // cols))
    canvas = Image.new('RGB', (cols * (THUMB + 4), rows * (THUMB + 18)), (40, 40, 48))
    d = ImageDraw.Draw(canvas)
    for i, r in enumerate(rs):
        im = Image.open(dump / r['png']).convert('RGBA')
        bg = Image.new('RGBA', im.size, (90, 90, 100, 255))
        bg.alpha_composite(im)
        bg.thumbnail((THUMB, THUMB), Image.NEAREST if max(im.size) <= THUMB else Image.BILINEAR)
        x, y = (i % cols) * (THUMB + 4), (i // cols) * (THUMB + 18)
        canvas.paste(bg.convert('RGB'), (x, y))
        label = f"{r['kind']} {Path(r['name']).name}"[:26]
        d.text((x + 2, y + THUMB + 3), label, fill=(230, 230, 230))
    out = out or dump / f'_sheet_{kind or "all"}.png'
    canvas.save(out)
    return out


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    mx = int(sys.argv[sys.argv.index('--max') + 1]) if '--max' in sys.argv else 48
    print(sheet(args[0], args[1] if len(args) > 1 else None, mx))

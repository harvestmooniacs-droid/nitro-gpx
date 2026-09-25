#!/usr/bin/env python3
"""nitrogfx - generic NDS graphics scan / dump / insert.

  python nitrogfx.py scan     ROM.nds [--filter GLOB]
  python nitrogfx.py dump     ROM.nds OUT_DIR [--filter GLOB] [--limit N] [--no-3d]
  python nitrogfx.py insert   ROM.nds OUT_DIR NEW.nds
  python nitrogfx.py roundtrip ROM.nds [--filter GLOB] [--limit N]
  python nitrogfx.py health   ROM.nds [--filter GLOB] [--limit N]
  python nitrogfx.py analyze  ROM.nds [--filter GLOB] [--out DUMP_DIR]

dump writes OUT_DIR/manifest.json + one indexed PNG per resource. Edit the
PNGs keeping them indexed (same palette, same size) and run insert: only
PNGs whose pixels differ from the dumped hash are reinserted.
"""
import argparse
import fnmatch
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nitrokit.rom.ndsrom import NDSRom, verify_rebuild       # noqa: E402
from nitrokit.pipeline.scan import scan                     # noqa: E402
from nitrokit.pipeline.source import Session, EditError      # noqa: E402
from nitrokit.pipeline import resource as R                  # noqa: E402
from nitrokit.pipeline import workflow as W                  # noqa: E402

import numpy as np                                           # noqa: E402


def _filter(pattern):
    if not pattern:
        return None
    return lambda p: fnmatch.fnmatch(p.lower(), pattern.lower())


def cmd_scan(a):
    rom = NDSRom.open(a.rom)
    ses = Session(rom)
    t = time.time()
    res, stats = scan(rom, ses, include_3d=not a.no_3d, path_filter=_filter(a.filter),
                      progress=lambda n, tot, p: print(f'\r  {n}/{tot} {p[:60]:60}', end='', file=sys.stderr))
    print(file=sys.stderr)
    kinds = {}
    for r in res:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    print(f'{rom.title} [{rom.code}] {len(res)} resources in {time.time()-t:.1f}s')
    print('  containers:', dict(stats))
    print('  kinds     :', kinds)
    print('  no palette:', sum(1 for r in res if r['kind'] != 'tex' and r.get('pal') is None))
    return rom, ses, res


def cmd_dump(a):
    rom, ses, res = cmd_scan(a)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.limit:
        # keep variety: take the first N of each kind
        per = {}
        keep = []
        for r in res:
            if per.get(r['kind'], 0) < a.limit:
                per[r['kind']] = per.get(r['kind'], 0) + 1
                keep.append(r)
        res = keep
    # same writer as the GUI: a CLI dump must be insertable from the GUI and
    # vice versa (the old hand-rolled manifest here was refused by Insert)
    try:
        report = W.dump_resources(rom, ses, res, out, W.png_name)
    except EditError as e:
        sys.exit(f'dump failed: {e}')
    print(f"dumped {report['dumped']} PNGs ({len(report['errors'])} errors) -> {out}")


def cmd_insert(a):
    # single source of truth with the GUI: workflow.insert_dump validates
    # the ENTIRE batch (every PNG present, every hash/palette matching,
    # every edit representable) before publishing anything - a missing or
    # stale PNG aborts the whole run instead of being silently skipped,
    # which a hand-rolled per-file loop here used to do.
    rom = NDSRom.open(a.rom)
    try:
        report = W.insert_dump(rom, a.out, a.new)
    except EditError as e:
        sys.exit(f'insert failed: {e}')
    print(f"{report['changed']} PNG(s) changed, {report['files']} ROM file(s) rewritten -> {report['output']}")


def cmd_roundtrip(a):
    rom, ses, res = cmd_scan(a)
    if a.limit:
        res = res[:a.limit]
    bad, err, skipped = [], 0, 0
    for r in res:
        try:
            ok = R.roundtrip_ok(r, ses)
            if ok is None:      # preview-only: never exercised, so never 'ok'
                skipped += 1
            elif not ok:
                bad.append(r['name'])
        except Exception as e:
            err += 1
            if err <= 5:
                print('  error', r['name'], type(e).__name__, e)
    print(f'pixel round-trip: {len(res)-len(bad)-err-skipped} ok, {len(bad)} mismatch, '
          f'{err} errors, {skipped} preview-only (not exercised)')
    for b in bad[:10]:
        print('  mismatch', b)
    # zero-edit ROM rebuild
    new = rom.rebuild({})
    probs = verify_rebuild(rom.data, new)
    print('zero-edit ROM rebuild:', 'OK' if not probs else probs[:5])


def cmd_health(a):
    """SKILL 5b.2: flag single-colour exports and palette-less ones."""
    rom, ses, res = cmd_scan(a)
    single, grey, err, total = [], [], [], 0
    for r in res:
        if a.limit and total >= a.limit:
            break
        total += 1
        try:
            im, info = R.to_png(r, ses)
        except Exception as e:
            err.append((r['name'], str(e)[:80]))
            continue
        arr = np.array(im)
        if arr.size and (arr == arr.flat[0]).all():
            single.append(r['name'])
        if info.get('grey'):
            grey.append(r['name'])
    print(f'health: {total} rendered, {len(single)} single-colour, {len(grey)} no-palette (grey), {len(err)} errors')
    for n in single[:8]:
        print('  single-colour', n)
    for n, e in err[:8]:
        print('  error', n, e)


def cmd_analyze(a):
    """Recompute the atlas/raw_atlas/raw_tex width heuristic over every
    resource that needs one (SKILL: 'atlas' resources carry no layout info
    at all - width is fundamentally undefined by the format in many games,
    the same ambiguity a raw tile viewer like CrystalTile2 makes a human
    resolve by hand). Reports what changed vs. the file's own declared
    value and flags low-confidence (<0.15) picks for a manual look via the
    GUI's width override. With --apply, writes the results into
    manifest.json (OUT_DIR) so a subsequent dump/insert reuses them without
    recomputing."""
    rom, ses, res = cmd_scan(a)
    ambiguous, changed, checked = [], [], 0
    for r in res:
        if r['kind'] not in ('atlas', 'raw_atlas', 'raw_tex'):
            continue
        try:
            R.to_png(r, ses)
        except Exception as e:
            print('  error', r['name'], e)
            continue
        info = r.get('atlas')
        if not info:
            continue
        checked += 1
        key = 'width_px' if 'width_px' in info else 'width_tiles'
        if info.get('declared') and info[key] != info['declared']:
            changed.append((r, info, key))
        if info['confidence'] < 0.15:
            ambiguous.append((r, info, key))
    print(f'analyze: {checked} atlas-like resources checked')
    print(f"  {len(changed)} differ from the file's own declared width (likely fixed a broken export)")
    for r, info, key in changed[:15]:
        print(f"    {r['name']}: declared {info['declared']} -> chosen {info[key]} "
              f"(confidence {info['confidence']})")
    print(f'  {len(ambiguous)} low-confidence / ambiguous - worth a manual look (GUI: select + "Set width...")')
    for r, info, key in ambiguous[:15]:
        print(f"    {r['name']}: chosen {info[key]} (confidence {info['confidence']}, declared {info['declared']})")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        man_path = out / 'manifest.json'
        man = json.loads(man_path.read_text(encoding='utf-8')) if man_path.exists() else             {'rom': str(Path(a.rom).resolve()), 'code': rom.code, 'tables': ses.tables, 'resources': res}
        by_id = {x['id']: x for x in man['resources']}
        for r in res:
            if r['id'] in by_id and r.get('atlas'):
                by_id[r['id']]['atlas'] = r['atlas']
        man_path.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding='utf-8')
        print(f'applied -> {man_path}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('scan', 'dump', 'insert', 'roundtrip', 'health', 'analyze'):
        s = sub.add_parser(name)
        s.add_argument('rom')
        if name in ('dump', 'insert'):
            s.add_argument('out')
        if name == 'insert':
            s.add_argument('new')
        s.add_argument('--filter')
        s.add_argument('--limit', type=int)
        s.add_argument('--no-3d', action='store_true')
        if name == 'analyze':
            s.add_argument('--out', help='dump folder whose manifest.json gets the results written back')
    a = ap.parse_args()
    globals()['cmd_' + a.cmd](a)


if __name__ == '__main__':
    main()

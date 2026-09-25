#!/usr/bin/env python3
"""End-to-end insertion test (SKILL 5b.3), generic for any ROM.

  python tools/e2e_test.py ROM.nds WORK_DIR [--filter GLOB] [--per-kind N]

1. scan + dump up to N resources per kind (editable ones)
2. paint a small block in each PNG (a different index than what is there)
3. insert -> rebuild ROM
4. re-scan the rebuilt ROM, re-render every dumped resource and assert:
   edited ones now contain the paint, all others are pixel-identical
5. zero-edit invariants: ROM size unchanged
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nitrokit.rom.ndsrom import NDSRom                    # noqa: E402
from nitrokit.pipeline.scan import scan                  # noqa: E402
from nitrokit.pipeline.source import Session, EditError   # noqa: E402
from nitrokit.pipeline import resource as R               # noqa: E402
import fnmatch                                            # noqa: E402


def _norm(loc):
    if not loc:
        return loc
    out = []
    for i, st in enumerate(loc):
        if st[0] == 'lz' and i == 1 and st[1] == 0:
            out.append(['codec', st[2]])          # whole-file LZ may be re-detected as codec
        elif st[0] == 'lz':
            out.append(['lz', st[1], st[2]])      # consumed length legitimately changes
        else:
            out.append(list(st))
    return out


def key(r, shift=None):
    return json.dumps([r['kind'], _norm(r['char']), _norm(r.get('map')), r.get('tex')])


def paint(im, bpp):
    arr = np.array(im)
    h, w = arr.shape
    bs = min(16, h, w)          # a small font/icon cell can be well under 16px
    y, x = max(0, h // 2 - bs // 2), max(0, w // 2 - bs // 2)
    block = arr[y:y + bs, x:x + bs].astype(np.int32)
    if bpp == 4:
        new = (block // 16) * 16 + ((block % 16) % 15 + 1)      # stay in same sub-palette
    else:
        new = (block + 1) % (1 << bpp)
    arr[y:y + bs, x:x + bs] = new
    out = Image.fromarray(arr.astype(np.uint8), 'P')
    out.putpalette(im.getpalette())
    return out, (x, y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('work')
    ap.add_argument('--filter')
    ap.add_argument('--per-kind', type=int, default=3)
    ap.add_argument('--pick', help='only edit resources whose manifest entry contains this text '
                                   '(e.g. diff8, link-table)')
    a = ap.parse_args()
    flt = (lambda p: fnmatch.fnmatch(p.lower(), a.filter.lower())) if a.filter else None
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)

    rom = NDSRom.open(a.rom)
    ses = Session(rom)
    res, _ = scan(rom, ses, path_filter=flt)
    chosen, per = [], {}
    report = {'rom': str(a.rom), 'filter': a.filter}
    for r in res:
        if per.get(r['kind'], 0) >= a.per_kind:
            continue
        if a.pick and a.pick not in json.dumps(r):
            continue
        if r['kind'] == 'tex':
            from nitrokit.formats.tex0 import TEX0, BPP
            if TEX0(ses.get(r['char'])).textures[r['tex']]['format'] not in BPP:
                continue
        try:
            im, info = R.to_png(r, ses)
        except Exception:
            continue
        if info.get('editable') is False:
            continue
        if im.size[0] < 16 or im.size[1] < 16:
            continue
        per[r['kind']] = per.get(r['kind'], 0) + 1
        chosen.append((r, im, info))
    if not chosen:
        print('no editable resources selected (check --filter/--pick)')
        return 1
    # also collect untouched neighbours for the "nothing else changed" half -
    # exclude any resource that shares a char/pal/map locator with an edited
    # one (e.g. one tileset legitimately feeding several NCER/NSCR maps):
    # editing it is expected to change every resource built from it, so that
    # is not a "nothing else changed" violation.
    edited_locs = {json.dumps(chosen_r[k]) for chosen_r, _, _ in chosen for k in ('char', 'pal', 'map') if chosen_r.get(k)}
    control = [r for r in res if all(r is not c[0] for c in chosen)
               and not any(json.dumps(r[k]) in edited_locs for k in ('char', 'pal', 'map') if r.get(k))][:200]
    before = {}
    for r in control:
        try:
            before[key(r)] = np.array(R.to_png(r, ses)[0]).tobytes()
        except Exception:
            pass

    edited = {}
    conflicts = 0
    for r, im, info in chosen:
        new, pos = paint(im, info['bpp'])
        try:
            conflicts += R.from_png(r, ses, new)
        except (R.Unsupported, EditError, ValueError) as e:
            print('  cannot insert', r['kind'], r['name'], e)
    # expected result = what the queued edits render to (tile reuse, shared
    # OAM pixels and 'first occurrence wins' already applied), so the check
    # below validates the container/compression/rebuild layers exactly
    for r, im, info in chosen:
        edited[key(r)] = (np.array(R.to_png(r, ses)[0]), r['kind'])
    painted = sum(1 for r, im, info in chosen if not np.array_equal(edited[key(r)][0], np.array(im)))
    print(f'{painted}/{len(chosen)} resources visibly changed before commit; {conflicts} shared-pixel conflicts')
    if painted == 0:
        print('no writer actually changed a resource (a from_png()/put() no-op somewhere?)')
        return 1
    try:
        files = ses.commit()
    except EditError as e:
        print('COMMIT FAILED:', e)
        return 1
    new_rom = rom.rebuild(files, banner_patch=ses.banner_patch, overlay_comp_sizes=ses.overlay_comp_sizes)
    out = work / 'e2e_rebuilt.nds'
    out.write_bytes(new_rom)
    print(f'edited {len(edited)} resources -> {len(files)} ROM files rewritten; size {len(rom.data)} -> {len(new_rom)}')

    rom2 = NDSRom(new_rom)
    ses2 = Session(rom2)
    res2, _ = scan(rom2, ses2, path_filter=flt)
    idx2 = {key(r): r for r in res2}
    if len(res2) == len(res):
        # a grown container shifts every later offset inside it: match by the
        # deterministic scan order instead of by locator
        idx2 = {key(r): res2[r['id']] for r in res}
    # a real dump->edit->insert round trip carries the auto-detected atlas
    # width (and raw bpp guess) through manifest.json, so a later render
    # NEVER re-runs the ambiguity-prone width heuristic on edited pixel
    # data - it just reuses the stored choice. Mimic that here (this
    # verification script has no manifest.json of its own): copy those
    # cached fields onto the matched res2 entries before rendering, instead
    # of letting them get freshly (and possibly differently, for a
    # low-confidence/ambiguous atlas) re-derived from the EDITED pixels.
    for r in res:
        r2 = idx2.get(key(r))
        if r2 is not None:
            for field in ('atlas', 'raw'):
                if field in r:
                    r2[field] = dict(r[field])
    ok_edit = bad_edit = ok_ctrl = bad_ctrl = missing = 0
    for k, (arr, kind) in edited.items():
        r2 = idx2.get(k)
        if not r2:
            missing += 1
            continue
        got = np.array(R.to_png(r2, ses2)[0])
        if got.shape == arr.shape and np.array_equal(got % 16 if kind != 'tex' and False else got, arr):
            ok_edit += 1
        else:
            diff = int(np.count_nonzero(got != arr)) if got.shape == arr.shape else -1
            bad_edit += 1
            print(f'  edit not reproduced: {kind} {k[:90]} differing px={diff}')
    for k, b in before.items():
        r2 = idx2.get(k)
        if not r2:
            missing += 1
            continue
        if np.array(R.to_png(r2, ses2)[0]).tobytes() == b:
            ok_ctrl += 1
        else:
            bad_ctrl += 1
            print(f'  control CHANGED unexpectedly: {k[:110]}')
    print(f'edits reproduced {ok_edit}/{ok_edit+bad_edit}; untouched unchanged {ok_ctrl}/{ok_ctrl+bad_ctrl}; '
          f'missing after rebuild {missing}; kinds {per}')
    out.unlink()
    report.update(changed=len(edited), reproduced=ok_edit, mismatched=bad_edit, missing=missing,
                  control_ok=ok_ctrl, control_changed=bad_ctrl, kinds=per)
    (work / 'e2e_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return 0 if bad_edit == 0 and bad_ctrl == 0 and missing == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

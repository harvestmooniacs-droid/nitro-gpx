"""Fingerprint every file of every ROM -> docs/survey/<game>.json + summary.
Usage: python tools/survey.py ROM.nds [out_dir]"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nitrokit.rom.ndsrom import NDSRom
from nitrokit import compression
from nitrokit.containers import narc

NITRO = [b'RLCN', b'RGCN', b'RCSN', b'RECN', b'RNAN', b'RCMN', b'RAMN', b'RTFN',
         b'BMD0', b'BTX0', b'BCA0', b'BTP0', b'BTA0', b'BMA0', b'BVA0', b'NARC',
         b'SDAT', b'NCLR', b'NCGR', b'NSCR', b'NCER', b'NANR', b'NFTR', b'MODS']
NITRO_RE = re.compile(b'|'.join(re.escape(m) for m in NITRO))


def magic_of(b):
    m = b[:4]
    if len(m) < 4:
        return 'tiny'
    if all(32 <= c < 127 for c in m):
        return m.decode()
    return 'x' + m.hex()


def inner_scan(b):
    """Nitro magics anywhere at 4-byte aligned offsets (bundle detection)."""
    c = collections.Counter()
    for mo in NITRO_RE.finditer(b):
        if mo.start() % 4 == 0 and mo.start() > 0:
            c[mo.group().decode()] += 1
    return c


def classify(b, depth=0):
    rec = {'magic': magic_of(b)}
    if len(b) > 3_000_000:
        return rec
    payload, codec = compression.unwrap(b)
    if codec:
        rec['codec'] = codec
        rec['inner_magic'] = magic_of(payload)
    if narc.is_narc(payload) and depth < 2:
        try:
            files = narc.unpack(payload)
            sub = collections.Counter()
            for f in files:
                r = classify(f, depth + 1)
                key = (r.get('codec', '') + ':' if r.get('codec') else '') + r.get('inner_magic', r['magic'])
                sub[key] += 1
            rec['narc'] = dict(sub)
        except Exception as e:
            rec['narc_err'] = str(e)
    else:
        sc = inner_scan(payload)
        if sc:
            rec['inner'] = dict(sc)
    return rec


def main():
    rom_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / 'docs' / 'survey'
    rom = NDSRom.open(rom_path)
    ext_stats = collections.defaultdict(collections.Counter)
    files = {}
    for fid, path in rom.all_files():
        b = rom.read_fid(fid)
        rec = classify(b)
        ext = Path(path).suffix.lower() or '(none)'
        key = rec['magic'] + ('/' + rec['codec'] + ':' + rec['inner_magic'] if 'codec' in rec else '')
        ext_stats[ext][key] += 1
        if 'narc' in rec:
            for k, v in rec['narc'].items():
                ext_stats[ext + ' [in NARC]'][k] += v
        if 'inner' in rec:
            ext_stats[ext + ' [inner magics]'][','.join(sorted(rec['inner']))] += 1
        rec['size'] = len(b)
        files[path] = rec
    res = {'rom': rom_path.name, 'title': rom.title, 'code': rom.code, 'n_files': rom.n_files,
           'ext_stats': {k: dict(v.most_common(12)) for k, v in sorted(ext_stats.items())},
           'files': files}
    name = re.sub(r'[^\w]+', '_', rom_path.stem)[:40]
    (out_dir / f'{name}.json').write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding='utf-8')
    print(rom_path.name, rom.code, rom.n_files, 'files')


if __name__ == '__main__':
    main()

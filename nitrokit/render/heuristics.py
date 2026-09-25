"""Automatic width/stride detection for resources that carry NO real layout
information at all: 'atlas' (NCGR with no NSCR/NCER) and 'raw_atlas'/
'raw_tex' (headerless dumps). This is the exact ambiguity a raw tile viewer
like CrystalTile2/Tinke makes the user resolve by hand (nudging a "tiles
per row" slider until the picture looks right) - here it is answered
automatically first, with a manual override only for the residue.

Technique: the same one used by generic raw-image/steganography viewers to
guess a framebuffer's scanline width from undifferentiated bytes - try
every plausible stride and score how visually "smooth" the reconstructed
image is row-to-row (mean absolute difference between vertically adjacent
rows of PALETTE INDICES, not colours - works even on a grey/no-palette
dump). The correct stride reassembles real image content (faces, icons,
gradients), which is far smoother row-to-row than the diagonal shear a
wrong stride produces; a wrong guess routinely scores 1.5-4x worse.

Validated on a genuinely broken export (Radiant Historia `Data.bin@44d8`,
a 256x64 bitmap NCGR declared with w_tiles=32): the declared width scored
45.0, a wrong guess of 32 tiles/row scored 96.1, while the real width
(64px, NOT what the file declared) scored 20.1 - a clean, isolated global
minimum among the divisors of the pixel count - and visually reassembles a
correct portrait bust where the declared width shows scrambled static.
"""
import numpy as np


def _row_score(img):
    """Lower is smoother/more likely correct. `img`: 2D array of indices."""
    if img.shape[0] < 2:
        return float('inf')
    d = np.abs(img[1:].astype(np.int16) - img[:-1].astype(np.int16))
    return float(d.mean())


def _candidates(n, unit, lo, hi):
    """Divisors of `n` that are multiples of `unit`, between lo and hi
    (inclusive), smallest first."""
    out = []
    for w in range(unit, min(hi, n) + 1, unit):
        if n % w == 0:
            out.append(w)
        if w >= hi:
            break
    return [w for w in out if lo <= w <= hi]


def best_bitmap_width(pixels, declared_w=None, lo=8, hi=512, unit=8):
    """For a flat linear-bitmap pixel buffer (already 1 index/pixel).
    Returns (best_width, ranked) where ranked is [(width, score), ...]
    sorted best-first, always including `declared_w` if given so callers
    can compare/tie-break against it."""
    px = np.frombuffer(bytes(pixels), dtype=np.uint8)
    n = px.size
    cands = set(_candidates(n, unit, lo, min(hi, n)))
    if declared_w and n % declared_w == 0:
        cands.add(declared_w)          # only meaningful if it actually divides n
    if not cands:
        return declared_w or n, []
    ranked = []
    for w in sorted(cands):
        img = px.reshape(-1, w)
        ranked.append((w, _row_score(img)))
    ranked.sort(key=lambda x: x[1])
    return ranked[0][0], ranked


def best_atlas_tile_width(compose_fn, n_tiles, declared_cols=None, lo=1, hi=32):
    """For a TILE-grid atlas (each unit is a whole 8x8 tile, not a raw
    pixel): `compose_fn(cols)` must return a 2D index array for that many
    tile-columns (see pipeline.resource / render.compose.layout_atlas).
    Tries divisors of n_tiles plus a few common sheet widths."""
    cands = {c for c in range(lo, min(hi, n_tiles) + 1) if n_tiles % c == 0}
    cands.update(c for c in (4, 8, 16, 32) if c <= n_tiles)
    if declared_cols:
        cands.add(declared_cols)
    ranked = []
    for cols in sorted(cands):
        try:
            img = compose_fn(cols)
        except Exception:
            continue
        ranked.append((cols, _row_score(img)))
    if not ranked:
        return declared_cols or n_tiles, []
    ranked.sort(key=lambda x: x[1])
    return ranked[0][0], ranked


def best_screen_dims(entries, declared=None, lo=8, hi=64):
    """For a headerless NSCR-style u16 tile-map with unknown cols/rows
    (Magical Starsign `map_amp.dat` etc: a real screen array, but no
    width/height field survives outside the game's own code). Same
    row-continuity idea as best_bitmap_width, scored on the TILE INDEX
    (low 10 bits - the sub-palette/flip bits in the top 6 are per-tile
    noise, not part of the picture) instead of a pixel value, and over
    divisor pairs of the entry count instead of a single stride (a screen
    is exactly cols*rows, never padded).
    Returns (best_(cols, rows), ranked) where ranked is
    [((cols, rows), score), ...] best first; `declared` is a (cols, rows)
    pair to always include so callers can compare against it."""
    idx = np.array([e & 0x3FF for e in entries], dtype=np.int32)
    n = idx.size
    if n < 2:
        return declared or (n, 1), []
    cands = {(w, n // w) for w in range(lo, min(hi, n) + 1) if n % w == 0}
    if declared and declared[0] * declared[1] == n:
        cands.add(tuple(declared))
    ranked = []
    for cols, rows in sorted(cands):
        if rows < 2:
            continue
        ranked.append(((cols, rows), _row_score(idx.reshape(rows, cols))))
    if not ranked:
        return declared or (n, 1), []
    ranked.sort(key=lambda x: x[1])
    return ranked[0][0], ranked


def _guess_raw_tile_bpp(data, bpp, unit, lo, hi):
    from ..formats.g2d import unpack_indices
    indices = unpack_indices(data, bpp)
    if len(indices) < unit * unit:            # too small to say anything
        return None
    width, ranked = best_bitmap_width(indices, lo=lo, hi=hi, unit=unit)
    if not ranked or len(indices) % width:
        return None
    height = len(indices) // width
    if height < 2:
        return None
    return {'bpp': bpp, 'width': width, 'height': height, 'confidence': round(confidence(ranked), 2)}


def guess_raw_tile(data, unit=8, lo=8, hi=256, max_bytes=4 << 20):
    """Best-effort 'does this look like a raw, headerless tile/bitmap
    dump?' check for files outside the small whitelist of extensions the
    scanner already treats as raw graphics (.nbfc/.nbfp/.nbfs/...) - e.g. a
    game's own .bin/.dat/.TS8 that carries pixel data with zero header.

    Reuses the same validated row-smoothness scoring as best_bitmap_width
    (real image content reassembles far smoother row-to-row than a wrong
    stride) as a CANDIDATE signal, not a verified decode - the caller
    should present this as a suggestion (pre-fill the manual Raw Lab), not
    silently add it as a resource, since unlike a real magic/header match
    this can still be wrong. Tries both 4bpp and 8bpp (the only two Nitro
    pixel depths a raw dump like this realistically uses) and returns
    whichever fits better.

    Returns None if the file is empty/too large/inconclusive, else
    {'bpp', 'width', 'height', 'confidence'}.
    """
    if not data or len(data) > max_bytes:
        return None
    candidates = [g for g in (_guess_raw_tile_bpp(data, bpp, unit, lo, hi) for bpp in (4, 8)) if g]
    if not candidates:
        return None
    return max(candidates, key=lambda g: g['confidence'])


def confidence(ranked):
    """Rough 0..1 confidence: how much better the winner is than the
    runner-up. Low confidence (<0.15) means the result is genuinely
    ambiguous and worth flagging for a human to double check / override."""
    if len(ranked) < 2:
        return 1.0
    best, second = ranked[0][1], ranked[1][1]
    if second <= 0:
        return 1.0
    return max(0.0, min(1.0, 1 - best / second))

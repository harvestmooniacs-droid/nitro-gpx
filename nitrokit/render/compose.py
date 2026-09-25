"""Compose editable PNGs from Nitro data and decompose them back.

Every composer builds a *placement list* first - a pure function of the
map data - and both compose and decompose walk that same list, so the
PNG <-> pixel mapping is deterministic and needs no side manifest.

A placement is (dst_x, dst_y, w, h, src_offsets) where src_offsets is a
numpy int array (h, w) of pixel indices into the NCGR pixel buffer (-1 =
nothing), plus the sub-palette. Flips are baked into src_offsets.

PNG output is indexed ('P'), full 256-entry palette; transparency (tRNS)
on index 0 (8bpp) / every idx%16==0 (4bpp) - display only, insert reads raw
indices.
"""
import numpy as np
from PIL import Image

SHEET_W = 512
GAP = 8


# ------------------------------------------------------------------ palette
def make_png(indices, palette, bpp, transparent=True):
    """indices: 2D uint8 array. palette: list of RGB (>=16)."""
    im = Image.fromarray(np.ascontiguousarray(indices, dtype=np.uint8), 'P')
    flat = []
    for c in (list(palette) + [(0, 0, 0)] * 256)[:256]:
        flat += list(c[:3])
    im.putpalette(flat)
    if transparent:
        alpha = bytearray(b'\xff' * 256)
        step = 16 if bpp == 4 else 256
        for i in range(0, 256, step):
            alpha[i] = 0
        im.info['transparency'] = bytes(alpha)
    return im


def grey_palette(bpp):
    if bpp == 4:
        return [(i * 17, i * 17, i * 17) for i in range(16)] * 16
    return [(i, i, i) for i in range(256)]


def read_png_indices(path_or_im, size=None):
    im = path_or_im if isinstance(path_or_im, Image.Image) else Image.open(path_or_im)
    if im.mode != 'P':
        raise ValueError(f'{getattr(im, "filename", "image")}: PNG must stay indexed (mode P), got {im.mode}. '
                         'Edit with a palette-preserving editor.')
    arr = np.array(im, dtype=np.uint8)
    if size and (arr.shape[1], arr.shape[0]) != size:
        raise ValueError(f'PNG size changed: expected {size}, got {(arr.shape[1], arr.shape[0])}')
    return arr


# ------------------------------------------------------------------ layouts
def _tile_offsets(tile, hflip, vflip):
    base = tile * 64
    grid = np.arange(64).reshape(8, 8) + base
    if hflip:
        grid = grid[:, ::-1]
    if vflip:
        grid = grid[::-1, :]
    return grid


def layout_bg(nscr, n_tiles, bpp, tile_base=0):
    """Placement grid for an NSCR screen: (h_px, w_px) index map + pal map."""
    cols, rows = nscr.cols, nscr.rows
    src = np.full((rows * 8, cols * 8), -1, dtype=np.int64)
    pal = np.zeros((rows * 8, cols * 8), dtype=np.uint8)
    for i, e in enumerate(nscr.entries[:cols * rows]):
        t = (e & 0x3FF) - tile_base
        if not 0 <= t < n_tiles:
            continue
        y, x = divmod(i, cols)
        src[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = _tile_offsets(t, e >> 10 & 1, e >> 11 & 1)
        pal[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = e >> 12
    return src, pal


def layout_tiles(entries, cols, rows, n_tiles, tile_base=0, blank=None):
    """Like layout_bg but for plain tile indices with no flip/sub-palette
    bits and no 10-bit field cap (Cing CBP1 tile pools routinely exceed the
    1024 tiles an NSCR entry's `& 0x3FF` field could address)."""
    src = np.full((rows * 8, cols * 8), -1, dtype=np.int64)
    pal = np.zeros((rows * 8, cols * 8), dtype=np.uint8)
    for i, e in enumerate(entries[:cols * rows]):
        if e == blank:
            continue
        t = e - tile_base
        if not 0 <= t < n_tiles:
            continue
        y, x = divmod(i, cols)
        src[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = _tile_offsets(t, 0, 0)
    return src, pal


def layout_atlas(n_pixels, width_tiles=16, bitmap_w=None):
    """Raw dump: tiles in a grid, or a linear bitmap `bitmap_w` px wide."""
    if bitmap_w:
        h = -(-n_pixels // bitmap_w)
        src = np.arange(h * bitmap_w, dtype=np.int64).reshape(h, bitmap_w)
        src[src >= n_pixels] = -1
        return src, np.zeros_like(src, dtype=np.uint8)
    nt = n_pixels // 64
    cols = max(1, min(width_tiles, nt))
    rows = -(-nt // cols)
    src = np.full((rows * 8, cols * 8), -1, dtype=np.int64)
    for t in range(nt):
        y, x = divmod(t, cols)
        src[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = _tile_offsets(t, 0, 0)
    return src, np.zeros_like(src, dtype=np.uint8)


def _cell_bbox(cell):
    if not cell['oams']:
        return 0, 0, 8, 8
    x0 = min(o['x'] for o in cell['oams'])
    y0 = min(o['y'] for o in cell['oams'])
    x1 = max(o['x'] + o['w'] for o in cell['oams'])
    y1 = max(o['y'] + o['h'] for o in cell['oams'])
    return x0, y0, max(8, x1 - x0), max(8, y1 - y0)


def pick_mapping(ncer, n_pixels, bpp, bitmap):
    """The NCER mapping-mode field is garbage in some files (Elite Beat
    Agents: 0x0012FDE4, uninitialised memory). A declared 0..4 that fits is
    trusted. Otherwise every mode that fits is scored by how many OAM pixel
    ranges PARTIALLY overlap (real tile runs are either shared exactly or
    disjoint); the lowest score wins. 'Fits' alone is not enough: mode 0
    always fits when mode 1 does."""
    unit_px = 256 // bpp
    oams = {(o['tile'], o['w'] * o['h']) for c in ncer.cells for o in c['oams']}

    def fits(m):
        return all((t << m) * unit_px + n <= n_pixels for t, n in oams)

    declared = ncer.mapping if 0 <= ncer.mapping <= 4 else None
    if declared is not None and fits(declared):
        return declared
    best = None
    for m in range(5):
        if not fits(m):
            continue
        rng = sorted({((t << m) * unit_px, (t << m) * unit_px + n) for t, n in oams})
        bad = sum(1 for (a0, a1), (b0, b1) in zip(rng, rng[1:]) if b0 < a1 and (a0, a1) != (b0, b1))
        if best is None or bad < best[0]:
            best = (bad, m)
    return best[1] if best else (declared or 0)


def grid_fits(ncer, n_pixels, bitmap_w):
    """True if every OAM, read as a rectangle at tile (index % cols,
    index // cols) of a bitmap `bitmap_w` px wide, stays inside it."""
    cols, rows = bitmap_w // 8, n_pixels // bitmap_w // 8
    return bool(cols) and all(o['tile'] % cols * 8 + o['w'] <= bitmap_w and o['tile'] // cols < rows
                              and o['tile'] // cols * 8 + o['h'] <= rows * 8
                              for c in ncer.cells for o in c['oams'])


def oams_overlap(ncer):
    """True when any two OAMs of the same cell cover the same pixel.

    A flat sheet can only hold one value per pixel, so for those cells the
    composed export silently loses whatever the top piece covers - the
    'chopped sprite' look. `isolated=True` (the OAM sheet) is the lossless
    way to edit them."""
    for cell in ncer.cells:
        oams = cell['oams']
        for i, a in enumerate(oams):
            for b in oams[i + 1:]:
                if (a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w']
                        and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']):
                    return True
    return False


def layout_cells(ncer, n_pixels, bpp, bitmap, sheet_w=SHEET_W, gap=GAP, grid_w=None, pixels=None,
                 isolated=False):
    """Every cell of the NCER assembled, packed left-to-right on one sheet.
    OAM tile index -> pixel offset: (tile << mapping) * (64 px in 4bpp,
    32 px in 8bpp - one index unit is 32 bytes of data). In bitmap mode the
    OAM is a linear w*h image; in tile mode it is a (w/8)x(h/8) tile run.
    `grid_w` (bitmap NCGRs only): the index is instead an 8x8-tile position
    in a bitmap that many px wide and the OAM copies the w x h rectangle
    there (Radiant Historia field sprites - see resource._cell_grid_width).
    `pixels`: when given, an OAM pixel with value 0 does not cover the OAMs
    under it (index 0 is never drawn on hardware), so overlapping parts show
    through exactly as in game.
    `isolated`: one box per OAM instead of one box per cell (the OAM sheet).
    Nothing can then hide anything else, so every pixel of every piece is
    visible and editable - the right mode for layered/overlapping sprites
    (see oams_overlap). Deterministic, so insert rebuilds the same sheet."""
    unit_px = 256 // bpp
    mapping = 0 if grid_w else pick_mapping(ncer, n_pixels, bpp, bitmap)
    vram = getattr(ncer, 'vram_src', None)
    # (cell_index, [oams to draw], bbox) per box on the sheet
    units = []
    for ci, cell in enumerate(ncer.cells):
        if isolated:
            for o in cell['oams']:
                units.append((ci, [o], (o['x'], o['y'], max(8, o['w']), max(8, o['h']))))
        else:
            units.append((ci, list(cell['oams']), _cell_bbox(cell)))
    places, x, y, row_h, width = [], 0, 0, 0, 0
    for _ci, _oams, (bx, by, bw, bh) in units:
        if x and x + bw > sheet_w:
            x, y, row_h = 0, y + row_h + gap, 0
        places.append((x, y, bx, by, bw, bh))
        width = max(width, x + bw)
        x += bw + gap
        row_h = max(row_h, bh)
    W, H = max(8, width), max(8, y + row_h)
    src = np.full((H, W), -1, dtype=np.int64)
    pal = np.zeros((H, W), dtype=np.uint8)
    boxes = []
    buf = np.frombuffer(bytes(pixels), dtype=np.uint8) if pixels is not None else None
    for (ci, oams, _bbox), (px, py, bx, by, bw, bh) in zip(units, places):
        boxes.append((px, py, bw, bh))
        src_off = vram[ci] * (2 if bpp == 4 else 1) if vram and ci < len(vram) else 0
        # draw in reverse so the FIRST OAM ends on top (DS priority order)
        for o in reversed(oams):
            w, h = o['w'], o['h']
            start = (o['tile'] << mapping) * unit_px + src_off
            if grid_w and not bitmap:
                # tile-mode NCGR: the index is a tile position in a grid
                # `grid_w` px wide, but each tile's 64 px are stored together
                cols = grid_w // 8
                r0, c0 = divmod(o['tile'], cols)
                yy, xx = np.arange(h, dtype=np.int64)[:, None], np.arange(w, dtype=np.int64)[None, :]
                grid = ((r0 + yy // 8) * cols + c0 + xx // 8) * 64 + (yy % 8) * 8 + xx % 8
            elif grid_w:
                cols = grid_w // 8
                org = o['tile'] // cols * 8 * grid_w + o['tile'] % cols * 8
                grid = (np.arange(h, dtype=np.int64)[:, None] * grid_w
                        + np.arange(w, dtype=np.int64)[None, :] + org)
            elif bitmap:
                grid = np.arange(h * w, dtype=np.int64).reshape(h, w) + start
            else:
                tw, th = w // 8, h // 8
                grid = np.zeros((h, w), dtype=np.int64)
                for ty in range(th):
                    for tx in range(tw):
                        grid[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8] = \
                            np.arange(64).reshape(8, 8) + start + (ty * tw + tx) * 64
            if o['hflip']:
                grid = grid[:, ::-1]
            if o['vflip']:
                grid = grid[::-1, :]
            if grid.max(initial=-1) >= n_pixels:
                grid = np.where(grid < n_pixels, grid, -1)
            dx, dy = px + o['x'] - bx, py + o['y'] - by
            region = src[dy:dy + h, dx:dx + w]
            g = grid[:region.shape[0], :region.shape[1]]
            m = g >= 0
            if buf is not None:
                m &= (buf[np.where(m, g, 0)] != 0) | (region < 0)
            region[m] = g[m]
            pal[dy:dy + h, dx:dx + w][m] = o['pal']
    return src, pal, boxes


# ------------------------------------------------------------------ compose
def compose(pixels, src, pal, bpp):
    """Return 2D uint8 of palette indices (sub-palette applied for 4bpp)."""
    buf = np.frombuffer(bytes(pixels), dtype=np.uint8)
    out = np.zeros(src.shape, dtype=np.uint8)
    m = src >= 0
    vals = buf[src[m]]
    out[m] = vals if bpp == 8 else (pal[m].astype(np.uint16) * 16 + vals).astype(np.uint8)
    return out


def decompose(indices, pixels, src, bpp):
    """Write edited indices back into a copy of `pixels`. Where the same
    source pixel is shown several times (tile reuse / flips / repeated
    cells), the FIRST occurrence in raster order wins; `conflicts` counts
    positions whose edited value disagrees with the winner so the UI can
    warn."""
    new = bytearray(pixels)
    m = src >= 0
    offs = src[m]
    vals = indices[m]
    if bpp == 4:
        vals = vals & 0xF
    offs_rev, vals_rev = offs[::-1], vals[::-1]
    arr = np.frombuffer(new, dtype=np.uint8).copy()
    arr[offs_rev] = vals_rev          # later writes win -> reversed = first wins
    # conflict check
    check = arr[offs]
    conflicts = int(np.count_nonzero(check != vals))
    return bytearray(arr.tobytes()), conflicts

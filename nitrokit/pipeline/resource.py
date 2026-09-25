"""Resource <-> PNG. One function pair per resource kind, all sharing the
compose/decompose placement model from render.compose."""
import numpy as np
from PIL import Image

from ..formats import g2d, raw as rawfmt, banner as bannerfmt, nftr as nftrfmt, monolith, sir0, layton_arc
from ..formats.tex0 import TEX0, BPP as TEX_BPP
from ..render import compose as C
from ..render import heuristics as H


class Unsupported(Exception):
    pass


GAP = 8


def _palette(res, session, bpp):
    if res.get('pal') is None:
        return C.grey_palette(bpp), True
    data = session.get(res['pal'])
    if res['kind'].startswith('raw'):
        return g2d.decode_colors(data[:len(data) // 2 * 2]), False
    return g2d.NCLR(data).palette256(), False


def _font_grey_palette(bpp):
    n = 1 << bpp
    step = 255 // (n - 1) if n > 1 else 0
    return [(i * step, i * step, i * step) for i in range(n)]


def _font_sheet(font, max_cols=32):
    """Deterministic grid layout for a font's glyph sheet - same purpose
    as render.compose.layout_atlas/layout_cells but glyph cells are not
    8x8, so it gets its own tiny layout function."""
    cols = min(max_cols, font.n_glyphs) or 1
    rows = -(-font.n_glyphs // cols)
    return cols, rows


def _resolve_bitmap_width(res, pixels, declared, unit=8, lo=8, hi=512):
    """Auto-detect a linear-bitmap atlas/texture's real width (SKILL:
    'atlas' resources carry no layout info at all - the NCGR's own
    w_tiles field is frequently just a placeholder, not the true image
    shape; see render.heuristics for the row-continuity technique and the
    Radiant Historia case that validated it). Caches the result AND its
    confidence into `res['atlas']` so a later insert (possibly a fresh
    process reading only manifest.json) uses the exact same width."""
    w, ranked = H.best_bitmap_width(pixels, declared_w=declared, lo=lo, hi=min(hi, len(pixels)), unit=unit)
    conf = H.confidence(ranked)
    # genuinely ambiguous (Valkyrie Profile CPK: orphan chunks that are not
    # a single continuous image at all - every candidate width scores
    # about the same): a narrow winner here is usually a few-px-wide noise
    # strip, worse to look at than the header's own declared width
    if conf < 0.05 and declared and len(pixels) % declared == 0:
        w = declared
    elif declared and w * 4 <= declared and len(pixels) % declared == 0:
        # a width under 1/4 of the declared one is almost always noise: busy,
        # multi-sprite content spuriously favors tiny widths on row-continuity
        # (any row 'matches' the one above when both are mostly background).
        # Under 1/8th, the resulting sliver is implausible enough to always
        # override; between 1/8 and 1/4, only when declared isn't a clear loser
        scores = dict(ranked)
        if w * 8 <= declared or scores.get(declared, float('inf')) < 3 * scores[w]:
            w = declared
    info = res.setdefault('atlas', {})
    info['width_px'] = w
    info['confidence'] = round(conf, 2)
    info['declared'] = declared
    info['auto'] = True
    return w


def _resolve_tile_width(res, pixels, bpp, n_tiles, declared):
    """Same idea as _resolve_bitmap_width but for TILE-grid atlases (each
    candidate width is scored on the actually-composed index image, since
    a tile is an indivisible 8x8 block, not a raw pixel run)."""
    def compose_fn(cols):
        src, palmap = C.layout_atlas(len(pixels), width_tiles=cols)
        return C.compose(pixels, src, palmap, bpp)
    # a 1-3 tile wide strip is always 'smooth' row to row and never a real
    # sheet once there are more than a few rows of tiles
    lo = 4 if n_tiles >= 32 else 1
    cols, ranked = H.best_atlas_tile_width(compose_fn, n_tiles, declared_cols=declared, lo=lo)
    conf = H.confidence(ranked)
    if conf < 0.05 and declared and n_tiles % declared == 0:
        cols = declared     # genuinely ambiguous: trust the header over noise
    elif declared and cols * 4 <= declared and n_tiles % declared == 0:
        scores = dict(ranked)  # see _resolve_bitmap_width: don't trust a tiny
        if cols * 8 <= declared or scores.get(declared, float('inf')) < 3 * scores[cols]:
            cols = declared     # winner over the header unless it wins decisively
    elif conf < 0.15 and not declared and n_tiles >= 256 and n_tiles % 32 == 0:
        cols = 32          # undecided on a big sheet: one DS screen (256px) per row
    info = res.setdefault('atlas', {})
    info['width_tiles'] = cols
    info['confidence'] = round(conf, 2)
    info['declared'] = declared
    info['auto'] = True
    return cols


def _cell_grid_width(res, ncer, ch, pixels, bpp):
    """Bitmap NCGR + NCER: two index conventions exist. Linear (Atelier:
    the OAM is a w-wide run starting at tile << mapping) and 2D grid
    (Radiant Historia: the index is a tile position in the bitmap and the
    OAM copies that rectangle). When both are geometrically possible, the
    one whose composed sheet is smoother row to row wins, the same score the
    width detector uses. Cached in res['cells'] so insert uses the same one."""
    info = res.setdefault('cells', {})
    if 'grid_w' in info:
        return info['grid_w']
    width = ch.w_tiles * 8 if ch.w_tiles and ch.h_tiles else 0
    choice = None
    unit = 256 // bpp
    # judged with the DECLARED mode: pick_mapping would fall back to mode 0,
    # which always "fits" a 2D index set and reads it as garbage runs
    m = ncer.mapping if 0 <= ncer.mapping <= 4 else C.pick_mapping(ncer, len(pixels), bpp, ch.bitmap)
    linear_fits = all((o['tile'] << m) * unit + o['w'] * o['h'] <= len(pixels)
                      for c in ncer.cells for o in c['oams'])
    if width and len(pixels) % width == 0 and C.grid_fits(ncer, len(pixels), width):
        if not linear_fits:
            # tile-mode too (Luminous Arc ADVOBJ: 2D tile positions with a
            # declared mapping of 4 that would run far past the data)
            choice = width
        elif ch.bitmap:
            grid = C.layout_cells(ncer, len(pixels), bpp, True, grid_w=width, pixels=pixels)
            linear = C.layout_cells(ncer, len(pixels), bpp, True, pixels=pixels)
            score = lambda L: H._row_score(C.compose(pixels, L[0], L[1], bpp))
            if score(grid) < score(linear):
                choice = width
    info['grid_w'] = choice
    return choice


def _resolve_screen_dims(res, entries, declared):
    """Same idea as _resolve_bitmap_width but for a headerless NSCR-style
    screen (no width/height anywhere in the file - Magical Starsign
    `map_amp.dat`): row-continuity over the TILE INDEX picks cols/rows out
    of every divisor pair of the entry count. Cached in res['screen'] so a
    later insert (or a fresh process from manifest.json) reuses the exact
    same rectangle instead of re-deriving it from possibly-edited data."""
    (cols, rows), ranked = H.best_screen_dims(entries, declared=declared)
    info = res.setdefault('screen', {})
    info['cols'], info['rows'] = cols, rows
    info['confidence'] = round(H.confidence(ranked), 2)
    info['declared'] = declared
    info['auto'] = True
    return cols, rows


def _raw_char(res, session):
    data = session.get(res['char'])
    if res.get('map'):
        info = res.get('screen')
        entries, cols, rows = rawfmt.screen_layout_declared(session.get(res['map']))
        if cols is None:
            cols, rows = (info['cols'], info['rows']) if info and 'cols' in info \
                else _resolve_screen_dims(res, entries, None)
        elif info is None:
            res['screen'] = {'cols': cols, 'rows': rows, 'confidence': 1.0, 'declared': (cols, rows), 'auto': False}
        scr = (entries, cols, rows)
    else:
        scr = None
    pal_len = len(session.get(res['pal'])) if res.get('pal') else None
    params = res.setdefault('raw', {})
    bpp = params.get('bpp')
    if bpp is None:
        bpp = 4
        if pal_len and pal_len >= 512 and not (scr and any(e >> 12 for e in scr[0])):
            bpp = 8
            if not scr and len(data) % 64 == 0:
                # a 256-colour palette is also 16 sub-palettes of a 4bpp set
                # (Magical Starsign maps): keep the smoother reading - smooth
                # in real colour, since raw 4bpp indices (0-15) always look
                # "smoother" than 8bpp ones (0-255); Kaiji's 8bpp *_img.bin
                # were read as 4bpp garbage by an index-space score
                raw_pal = session.get(res['pal'])
                v = np.frombuffer(raw_pal[:len(raw_pal) // 2 * 2], '<u2').astype(np.int32)
                luma = (v & 31) * 3 + (v >> 5 & 31) * 6 + (v >> 10 & 31)
                luma = np.concatenate([luma, np.zeros(256, np.int32)])[:256]

                def score(b):
                    px = g2d.unpack_indices(data, b)
                    src, pm = C.layout_atlas(len(px), width_tiles=32)
                    idx = C.compose(px, src, pm, b) & (0xF if b == 4 else 0xFF)
                    return H._row_score(luma[idx])
                if score(4) <= score(8):
                    bpp = 4
        params['bpp'] = bpp
    return data, bpp, scr


def _bitmaps():
    from ..formats import bitmaps
    return bitmaps


def cell_boxes(res, session):
    """OAM/cell rectangles of a 'cell' sheet in sheet coordinates, for the
    preview overlay. Same call and parameters as layout(), so the boxes are
    exactly where the pixels are."""
    if res.get('kind') != 'cell' or not res.get('map'):
        return []
    ch = g2d.NCGR(session.get(res['char']))
    ncer = g2d.NCER(session.get(res['map']))
    grid_w = _cell_grid_width(res, ncer, ch, ch.pixels, ch.bpp)
    info = res.setdefault('cells', {})
    _src, _pal, boxes = C.layout_cells(
        ncer, len(ch.pixels), ch.bpp, ch.bitmap, grid_w=grid_w, pixels=ch.pixels,
        isolated=bool(info.get('isolated')),
        sheet_w=int(info.get('sheet_w') or C.SHEET_W), gap=int(info.get('gap', C.GAP)))
    return boxes


def layout(res, session):
    """-> dict(src, pal_map, bpp, pixels, palette, grey, writer)"""
    kind = res['kind']
    palette = None
    if kind == 'obp':
        ob = monolith.OBP1(session.get(res['char']))
        pixels, bpp = ob.pixels, ob.bpp
        src = np.full((ob.h, ob.w), -1, dtype=np.int64)
        per_row = ob.w // ob.bw
        for i in range(per_row * (ob.h // ob.bh)):
            y, x = divmod(i, per_row)
            base = i * ob.bw * ob.bh
            src[y * ob.bh:(y + 1) * ob.bh, x * ob.bw:(x + 1) * ob.bw] =                 np.arange(base, base + ob.bw * ob.bh).reshape(ob.bh, ob.bw)
        palmap = np.zeros(src.shape, dtype=np.uint8)
        palette = ob.palette()
        writer = lambda px: session.put(res['char'], ob.rebuild(px))
    elif kind == 'cbp':
        cbp = _bitmaps().CBP1(session.get(res['char']))
        pixels, bpp = cbp.pixels, cbp.bpp
        src, palmap = C.layout_tiles(cbp.entries, cbp.cols, cbp.rows, cbp.n_tiles)
        palette = cbp.palette()
        writer = lambda px: session.put(res['char'], cbp.rebuild(px))
    elif kind == 'bitmap':
        from ..formats import bitmaps
        bm = bitmaps.open_bitmap(session.get(res['char']), res['format'])
        pixels, bpp = bm.pixels, bm.bpp
        src, palmap = C.layout_atlas(bm.w * bm.h, bitmap_w=bm.w)
        palette = bm.palette()
        writer = lambda px: session.put(res['char'], bm.rebuild(px))
    elif kind == 'sir':
        im = sir0.open_image(session.get(res['char']))
        pixels, bpp = im.pixels, im.bpp
        if im.tiled:
            src, palmap = C.layout_atlas(im.w * im.h, width_tiles=im.w // 8)
        else:
            src, palmap = C.layout_atlas(im.w * im.h, bitmap_w=im.w)
        palette = im.palette()
        writer = lambda px: session.put(res['char'], im.rebuild(px))
    elif kind == 'sir_sprite':
        sp = sir0.SirSprite(session.get(res['char']))
        pixels, bpp = sp.pixels, 8
        x0 = min(x for _, _, x, _, _ in sp.pieces)
        y0 = min(y for _, _, _, y, _ in sp.pieces)
        body_w = max(x + w for w, _, x, _, _ in sp.pieces) - x0
        body_h = max(y + h for _, h, _, y, _ in sp.pieces) - y0
        extra_w = extra_h = 0
        if sp.extra_len:
            info = res.setdefault('atlas', {})
            extra_w = info.get('width_px') or _resolve_bitmap_width(
                res, pixels[sp.extra_off:], None, unit=1, lo=8, hi=max(8, min(body_w, 256)))
            extra_h = -(-sp.extra_len // extra_w)
        gap = GAP if sp.extra_len else 0
        src = np.full((body_h + gap + extra_h, max(body_w, extra_w)), -1, dtype=np.int64)
        for w, h, x, y, off in sp.pieces:
            src[y - y0:y - y0 + h, x - x0:x - x0 + w] = np.arange(off, off + w * h).reshape(h, w)
        if sp.extra_len:
            strip = np.full(extra_w * extra_h, -1, dtype=np.int64)
            strip[:sp.extra_len] = np.arange(sp.extra_off, sp.extra_off + sp.extra_len)
            src[body_h + gap:, :extra_w] = strip.reshape(extra_h, extra_w)
        palmap = np.zeros(src.shape, dtype=np.uint8)
        palette = sp.palette()
        writer = lambda px: session.put(res['char'], sp.rebuild(px))
    elif kind == 'bgp':
        bg = monolith.BGP1(session.get(res['char']))
        pixels, bpp = bg.pixels, bg.bpp
        fake = type('S', (), {'entries': bg.entries, 'cols': bg.cols, 'rows': bg.rows})
        src, palmap = C.layout_bg(fake, bg.n_tiles, bpp)
        palette = bg.palette()
        writer = lambda px: session.put(res['char'], bg.rebuild(px))
    elif kind == 'layton_bg':
        bg = layton_arc.LaytonBG(session.get(res['char']))
        pixels, bpp = bg.pixels, bg.bpp
        fake = type('S', (), {'entries': bg.entries, 'cols': bg.cols, 'rows': bg.rows})
        src, palmap = C.layout_bg(fake, bg.n_tiles, bpp)
        palette = bg.palette()
        writer = lambda px: session.put(res['char'], bg.rebuild(px))
    elif kind == 'layton_ani':
        ani = layton_arc.LaytonAni(session.get(res['char']), arj=res['char'][-1][1].lower().endswith('.arj')
                                   if res['char'][-1][0] == 'file' else False)
        frame = res['tex']
        pixels, bpp = ani.frame_pixels(frame), ani.bpp
        w, h = ani.frame_size(frame)
        src = np.full((h, w), -1, dtype=np.int64)
        off = 0
        for p in ani.images[frame]['parts']:
            src[p['y']:p['y'] + p['h'], p['x']:p['x'] + p['w']] = \
                np.arange(off, off + p['w'] * p['h']).reshape(p['h'], p['w'])
            off += p['w'] * p['h']
        palmap = np.zeros(src.shape, dtype=np.uint8)
        palette = ani.palette()
        writer = lambda px: session.put(res['char'], ani.rebuild(frame, px))
    elif kind in ('bg', 'cell', 'atlas'):
        ch = g2d.NCGR(session.get(res['char']))
        pixels, bpp = ch.pixels, ch.bpp
        if kind == 'bg':
            src, palmap = C.layout_bg(g2d.NSCR(session.get(res['map'])), ch.n_tiles, bpp)
        elif kind == 'cell':
            ncer = g2d.NCER(session.get(res['map']))
            grid_w = _cell_grid_width(res, ncer, ch, pixels, bpp)
            # OAM sheet: opt-in per resource and remembered in the manifest,
            # so a later insert rebuilds the very same sheet (see
            # render.compose.layout_cells / oams_overlap)
            info = res.setdefault('cells', {})
            if 'overlap' not in info:
                info['overlap'] = bool(C.oams_overlap(ncer))
            src, palmap, _ = C.layout_cells(
                ncer, len(pixels), bpp, ch.bitmap, grid_w=grid_w, pixels=pixels,
                isolated=bool(info.get('isolated')),
                sheet_w=int(info.get('sheet_w') or C.SHEET_W), gap=int(info.get('gap', C.GAP)))
        else:
            info = res.setdefault('atlas', {})
            if ch.bitmap:
                declared = ch.w_tiles * 8 if ch.w_tiles else None
                w = info.get('width_px') or _resolve_bitmap_width(res, pixels, declared)
                src, palmap = C.layout_atlas(len(pixels), bitmap_w=w)
            else:
                declared = ch.w_tiles or None
                cols = info.get('width_tiles') or _resolve_tile_width(res, pixels, bpp, ch.n_tiles, declared)
                src, palmap = C.layout_atlas(len(pixels), width_tiles=cols)
        writer = lambda px: session.put(res['char'], ch.rebuild(px))
    elif kind in ('raw_bg', 'raw_atlas'):
        data, bpp, scr = _raw_char(res, session)
        pixels = g2d.unpack_indices(data, bpp)
        if scr:
            entries, cols, rows = scr
            fake = type('S', (), {'entries': entries, 'cols': cols, 'rows': rows})
            src, palmap = C.layout_bg(fake, len(pixels) // 64, bpp)
        else:
            info = res.setdefault('atlas', {})
            cols = info.get('width_tiles') or _resolve_tile_width(res, pixels, bpp, len(pixels) // 64, None)
            src, palmap = C.layout_atlas(len(pixels), width_tiles=cols)
        writer = lambda px: session.put(res['char'], g2d.pack_indices(px, bpp))
    elif kind == 'raw_tex':
        data = session.get(res['char'])
        pal_len = len(session.get(res['pal'])) if res.get('pal') else 32
        bpp = res.setdefault('raw', {}).get('bpp') or (8 if pal_len > 32 else 4)
        res['raw']['bpp'] = bpp
        pixels = g2d.unpack_indices(data, bpp)
        info = res.setdefault('atlas', {})
        w = info.get('width_px')
        if w is None:
            declared_w, declared_h = rawfmt.texture_dims(len(pixels))
            w = _resolve_bitmap_width(res, pixels, declared_w, unit=1)
        res['raw'].update(w=w, h=len(pixels) // w if w else 1)
        src, palmap = C.layout_atlas(len(pixels), bitmap_w=w)
        writer = lambda px: session.put(res['char'], g2d.pack_indices(px, bpp))
    else:
        raise Unsupported(kind)
    if palette is None:
        palette, grey = _palette(res, session, bpp)
    else:
        grey = False
    return {'src': src, 'pal_map': palmap, 'bpp': bpp, 'pixels': pixels,
            'palette': palette, 'grey': grey, 'writer': writer}


def to_png(res, session):
    """-> (PIL.Image, info dict)"""
    if res['kind'] == 'font':
        font = nftrfmt.NFTR(session.get(res['char']))
        cols, rows = _font_sheet(font)
        w, h = font.cell_w, font.cell_h
        idx = np.zeros((rows * h, cols * w), dtype=np.uint8)
        for gi in range(font.n_glyphs):
            gy, gx = divmod(gi, cols)
            px = np.array(font.glyph_pixels(gi), dtype=np.uint8).reshape(h, w)
            idx[gy * h:gy * h + h, gx * w:gx * w + w] = px
        im = C.make_png(idx, _font_grey_palette(font.bpp), font.bpp)
        return im, {'editable': True, 'bpp': font.bpp, 'grey': True, 'size': im.size,
                    'note': f'{font.n_glyphs} glyphs, {w}x{h}px each, {cols} per row'}
    if res['kind'] == 'banner':
        b = bannerfmt.Banner(session.get(res['char']))
        idx = np.array(b.icon_pixels(), dtype=np.uint8)
        im = C.make_png(idx, b.palette(), 4)
        return im, {'editable': True, 'bpp': 4, 'grey': False, 'size': im.size}
    if res['kind'] == 'tex':
        t = TEX0(session.get(res['char']))
        mode, arr, colors = t.decode(res['tex'])
        tex = t.textures[res['tex']]
        if mode == 'P':
            bpp = TEX_BPP[tex['format']]
            im = C.make_png(arr, colors or C.grey_palette(8), 8, transparent=bool(tex['color0']))
            return im, {'editable': True, 'bpp': bpp, 'grey': not colors}
        return Image.fromarray(arr, 'RGBA'), {'editable': False, 'bpp': 16,
                                              'note': '4x4-compressed/direct: preview only'}
    if res['kind'] == 'bitmap' and res.get('format') in _bitmaps().PREVIEW_ONLY:
        bm = _bitmaps().open_bitmap(session.get(res['char']), res['format'])
        return Image.fromarray(bm.rgba(), 'RGBA'), {'editable': False, 'bpp': 16,
                                                    'note': '4x4-compressed/direct: preview only'}
    if res['kind'] == 'bitmap' and res.get('format') == 'WinBMP':
        bm = _bitmaps().WinBMP(session.get(res['char']))
        if not bm.indexed:
            # a real direct-colour .BMP: no palette to index against, but still
            # fully editable - the RGB image IS the data (see formats.bitmaps)
            return Image.fromarray(bm.rgb_image(), 'RGB'), {
                'editable': True, 'bpp': bm.bpp, 'grey': False, 'rgb': True,
                'size': (bm.w, bm.h), 'note': f'Windows BMP {bm.bpp}bpp (cor direta)'}
    if res['kind'] == 'photo':
        from ..formats import photo as photofmt
        im = photofmt.open_image(session.get(res['char']))
        return im, {'editable': False, 'bpp': 24, 'grey': False, 'size': im.size,
                    'note': f"{res.get('format')} embutido: somente prévia (regravar seria com perda)"}
    if res['kind'] == 'raw_screen':
        entries, declared_cols, declared_rows = rawfmt.screen_layout_declared(session.get(res['map']))
        info = res.get('screen')
        if info and 'cols' in info:
            cols, rows = info['cols'], info['rows']
        elif declared_cols:
            cols, rows = declared_cols, declared_rows
        else:
            cols, rows = _resolve_screen_dims(res, entries, None)
        idx = (np.array(entries, dtype=np.uint32) & 0xFF).astype(np.uint8).reshape(rows, cols)
        im = C.make_png(idx, C.grey_palette(8), 8, transparent=False)
        return im, {'editable': False, 'bpp': 8, 'grey': True, 'size': im.size,
                    'note': f'{cols}x{rows} tile map, no tileset linked yet - use "Set tileset..."'}
    L = layout(res, session)
    idx = C.compose(L['pixels'], L['src'], L['pal_map'], L['bpp'])
    im = C.make_png(idx, L['palette'], L['bpp'])
    return im, {'editable': True, 'bpp': L['bpp'], 'grey': L['grey'], 'size': im.size}


def from_png(res, session, png):
    """Queue the edit of `png` into the session. Returns conflict count."""
    if res['kind'] == 'font':
        font = nftrfmt.NFTR(session.get(res['char']))
        cols, rows = _font_sheet(font)
        w, h = font.cell_w, font.cell_h
        arr = C.read_png_indices(png, (cols * w, rows * h))
        all_px = []
        for gi in range(font.n_glyphs):
            gy, gx = divmod(gi, cols)
            all_px.extend(arr[gy * h:gy * h + h, gx * w:gx * w + w].reshape(-1).tolist())
        session.put(res['char'], font.rebuild(all_px))
        return 0
    if res['kind'] == 'banner':
        b = bannerfmt.Banner(session.get(res['char']))
        arr = C.read_png_indices(png, (32, 32))
        session.put(res['char'], b.rebuild(arr.tolist()))
        return 0
    if res['kind'] == 'bitmap' and res.get('format') in _bitmaps().PREVIEW_ONLY:
        raise Unsupported('4x4-compressed / direct-colour bitmaps are preview-only')
    if res['kind'] == 'bitmap' and res.get('format') == 'WinBMP':
        bm = _bitmaps().WinBMP(session.get(res['char']))
        if not bm.indexed:
            arr = np.array(png.convert('RGB'))
            if arr.shape[:2] != (bm.h, bm.w):
                raise Unsupported(f'BMP is {bm.w}x{bm.h}; the PNG is '
                                  f'{arr.shape[1]}x{arr.shape[0]}')
            session.put(res['char'], bm.rebuild(arr))
            return 0
    if res['kind'] == 'tex':
        t = TEX0(session.get(res['char']))
        tex = t.textures[res['tex']]
        if tex['format'] not in TEX_BPP:
            raise Unsupported('4x4-compressed / direct-colour textures are preview-only')
        arr = C.read_png_indices(png, (tex['w'], tex['h']))
        session.put(res['char'], t.replace_indices(res['tex'], arr))
        return 0
    if res['kind'] == 'photo':
        raise Unsupported('JPEG/PNG/GIF/TGA embutido: somente prévia')
    if res['kind'] == 'raw_screen':
        raise Unsupported('no tileset linked yet - use "Set tileset..." to make this editable')
    L = layout(res, session)
    arr = C.read_png_indices(png, (L['src'].shape[1], L['src'].shape[0]))
    new, conflicts = C.decompose(arr, L['pixels'], L['src'], L['bpp'])
    if bytes(new) != bytes(L['pixels']):
        L['writer'](new)
    return conflicts


def link_tileset(res, char_loc, pal_loc=None):
    """Turn a 'raw_screen' (a real tile map found with no tileset in its own
    archive - Magical Starsign `map_amp.dat`) into an editable 'raw_bg' by
    pointing it at a tileset/palette the user (or a future auto-matcher)
    picked, e.g. from the GUI's AdvancedTab.link_tileset. Drops the cached
    dimensions/bpp guesses so they are re-derived validated against the
    now-known tile count, same as a fresh scan would."""
    res['kind'] = 'raw_bg'
    res['char'] = char_loc
    res['pal'] = pal_loc
    res.pop('screen', None)
    res.pop('raw', None)


def roundtrip_ok(res, session):
    """Zero-edit check. None means preview-only/not exercised, never success
    (nitrogfx.py's `roundtrip` command counts those separately instead of
    folding them into the pass count)."""
    if res['kind'] == 'font':
        font = nftrfmt.NFTR(session.get(res['char']))
        return font.rebuild(font.all_pixels()) == font.data
    if res['kind'] == 'banner':
        b = bannerfmt.Banner(session.get(res['char']))
        return b.rebuild(b.icon_pixels()) == b.data
    if res['kind'] == 'tex':
        t = TEX0(session.get(res['char']))
        if t.textures[res['tex']]['format'] not in TEX_BPP:
            return None
        im, _ = to_png(res, session)
        return t.replace_indices(res['tex'], np.array(im)) == t.data
    if res['kind'] in ('raw_screen', 'photo') or res.get('format') in _bitmaps().PREVIEW_ONLY:
        return None
    L = layout(res, session)
    idx = C.compose(L['pixels'], L['src'], L['pal_map'], L['bpp'])
    new, conflicts = C.decompose(idx, L['pixels'], L['src'], L['bpp'])
    return bytes(new) == bytes(L['pixels']) and conflicts == 0

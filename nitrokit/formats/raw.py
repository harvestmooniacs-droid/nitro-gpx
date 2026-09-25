"""Headerless NITRO-System 'binary' exports (no magic, no dimensions):

  .nbfc  raw character data (tiles, 4bpp or 8bpp)   ~ NCGR payload
  .nbfp  raw BGR555 palette                          ~ NCLR payload
  .nbfs  raw u16 screen entries                      ~ NSCR payload
  .ntft  raw texture texels (3D, no dims)            ~ TEX0 texdata
  .ntfp  raw texture palette                         ~ TEX0 paldata

Same idea, other names (Luminous Arc 3, usually inside LZE 'Le' streams):
  .imb  tiles        .plb / pal*.bin  palette      .LZE  tiles (object sheets)
  .scb  screen WITH a 16-byte header: u32 cols, u32 rows, u32 header_size,
        u32 ? - detected generically by screen_layout().

Same idea again (Summon Night: Twin Age, `dat/icon.char`+`dat/icon.plt`):
  .char  tiles        .plt  palette - identical stem-pairing as any other
  raw char/pal sibling pair, just a different, game-chosen extension.

And a bitmap trio WITH real dims (Shepherd's Crossing 2):
  .sbmph  'SBMP' header (bpp, width, height)   .sbmpic  pixels (SOLCOMP-LZ10)
  .sbmpp  raw BGR555 palette

Seen in Etrian Odyssey, Drawn to Life, The World Ends with You. Because
nothing is stored besides the payload, dims/bpp are *inferred* - every
guess is recorded in the resource manifest so a reinsert uses the same
layout the dump used.
"""
import math
import struct

RAW_EXT = {'.nbfc': 'char', '.nbfp': 'pal', '.nbfs': 'screen', '.ntft': 'texel', '.ntfp': 'texpal',
           '.ncbr': None, '.imb': 'char', '.plb': 'pal', '.scb': 'screen', '.lze': 'char',
           '.char': 'char', '.plt': 'pal',
           '.ncg': 'char', '.ncl': 'pal', '.nsc': 'screen',
           '.sbmpic': 'texel', '.sbmpp': 'texpal', '.sbmph': 'bmphdr'}


def sbmp_header(data):
    """Shepherd's Crossing 2 `.sbmph` (10 bytes): 'SBMP', u8 0, u8 bpp,
    u16 width, u16 height - the dims for the sibling `.sbmpic` pixels."""
    if len(data) < 10 or data[:4] != b'SBMP' or data[5] not in (4, 8):
        return None
    bpp = data[5]
    w, h = struct.unpack_from('<HH', data, 6)
    return (bpp, w, h) if w and h else None


LITE_MAGICS = {b'NCG\0': 'char', b'NCL\0': 'pal', b'NSC\0': 'screen'}


def nitro_lite(data):
    """Stripped-down, forward-magic Nitro exports (Kaijuu Busters
    `*_ncg.bin`/`*_ncl.bin`/`*_nsc.bin`, often LZ10-wrapped):
      NCG\0 u16 tiles, u8 is_8bpp, u8 0, tile data          (8 + n*32|64)
      NCL\0 u16 colours, u16 0, BGR555                        (8 + n*2)
      NSC\0 u16 entries, u16 flags, u8 cols, u8 rows, u16 0, u16 entries
                                                             (12 + n*2)
    Only accepted when the size equation is exact. -> (kind, header_len, bpp)"""
    kind = LITE_MAGICS.get(bytes(data[:4]))
    if kind is None or len(data) < 12:
        return None
    n = struct.unpack_from('<H', data, 4)[0]
    if kind == 'char':
        bpp = 8 if data[6] == 1 else 4 if data[6] == 0 else None
        if bpp and n and len(data) == 8 + n * (64 if bpp == 8 else 32):
            return kind, 8, bpp
    elif kind == 'pal':
        if n and len(data) == 8 + n * 2:
            return kind, 8, None
    elif n and data[8] * data[9] == n and len(data) == 12 + n * 2:
        return kind, 12, None
    return None


def is_palette_named(path, size):
    """A '.bin' named like a palette (pal*.bin, *_pal.bin, *_pl.bin) with a
    palette-sized payload (Luminous Arc 3 comm/palA0008.bin)."""
    import re
    from pathlib import PurePosixPath
    stem = PurePosixPath(path).stem.lower()
    return (bool(re.match(r'^pal[^a-z]|^pal$|.*_(pal|pl|plt)$', stem))
            and 32 <= size <= 1024 and size % 32 == 0)


def is_tiles_named(path, size):
    """A '.bin' named *_img with a whole number of 8x8 tiles: raw character
    data (Kaiji bg_adv_c01_sub_img.bin = 768 8bpp tiles, 32 per row, next
    to bg_adv_c01_sub_pal.bin). Read as a linear bitmap it comes out as
    repeated horizontal stripes."""
    import re
    from pathlib import PurePosixPath
    stem = PurePosixPath(path).stem.lower()
    return bool(re.search(r'_img$', stem)) and size >= 256 and size % 32 == 0


def screen_entries(data):
    n = len(data) // 2
    return list(struct.unpack_from(f'<{n}H', data))


def screen_layout(data):
    """(entries, cols, rows) of a raw screen, skipping a leading
    `u32 cols, u32 rows, u32 header_size` header when those three fields
    describe the rest of the file exactly (.scb); a blind 32-wide guess
    otherwise (kept for callers that only need *a* rectangle, e.g. the
    tile-fit check - use screen_layout_auto for a real guess)."""
    entries, cols, rows = screen_layout_declared(data)
    if cols is not None:
        return entries, cols, rows
    cols, rows = screen_dims(len(entries))
    return entries, cols, rows


def screen_layout_declared(data):
    """(entries, cols, rows) if a real header declares them, else
    (entries, None, None) - the honest version of screen_layout for callers
    that want to tell "the file says so" apart from "nothing to go on",
    e.g. to decide whether to run best_screen_dims (see render.heuristics)."""
    lite = nitro_lite(data)
    if lite and lite[0] == 'screen':
        return screen_entries(data[12:]), data[8], data[9]
    if len(data) >= 16:
        cols, rows, hdr = struct.unpack_from('<3I', data, 0)
        if 8 <= hdr <= 64 and hdr % 4 == 0 and 0 < cols <= 256 and 0 < rows <= 256 \
                and hdr + cols * rows * 2 == len(data):
            return screen_entries(data[hdr:]), cols, rows
    return screen_entries(data), None, None


def screen_dims(n_entries):
    """DS screens are 32 tiles wide; 256x192 = 1024 entries."""
    for cols in (32, 64, 16):
        if n_entries % cols == 0:
            return cols, n_entries // cols
    return 32, -(-n_entries // 32)


def texture_dims(n_texels):
    """Power-of-two square if possible, else widest pow2 <= 256 that divides."""
    s = int(math.isqrt(n_texels))
    if s * s == n_texels and s & (s - 1) == 0:
        return s, s
    for w in (256, 128, 64, 32, 16, 8):
        if n_texels % w == 0 and n_texels // w <= 1024:
            return w, n_texels // w
    return 64, -(-n_texels // 64)

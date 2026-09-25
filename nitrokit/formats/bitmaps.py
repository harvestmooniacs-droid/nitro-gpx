"""Self-describing paletted bitmaps (dimensions + palette + pixels in one
file) from publisher engines. Each class exposes w, h, bpp, pixels (one
index per pixel), palette() -> [(r, g, b)], rebuild(pixels) -> bytes, and
refuses anything whose sizes don't add up exactly.

  BPG1 (Cing, Last Window `.bpg`, 722 files):
    'BPG1' u16 colours, u8 bpp, u8 0, u16 width, u16 height, u16 block_w,
    u16 block_h | colours x BGR555 | pixels stored BLOCK by block (row-major
    blocks, each block row-major, edge blocks clipped) - read as one bitmap
    it comes out in 32px vertical stripes
  EBP (Cing, Last Window `.ebp`): u32 width, u32 height, u32 format, then
    palette + pixels - see EBP.FORMATS
  CBP1 (Cing, Last Window `.cbp`, calendar/map screens, 79 files): a big
    canvas cut into deduplicated blocks - see CBP1 below. Not a flat
    permutation like BPG1 (blocks legitimately repeat), so it plugs into
    the generic tile-reuse compose/decompose machinery (render.compose.
    layout_tiles) instead of the _Bitmap.order trick.
"""
import struct

import numpy as np

from . import g2d


def _bgr555(data, off, n):
    out = []
    for i in range(n):
        v = struct.unpack_from('<H', data, off + i * 2)[0]
        out.append(((v & 31) * 255 // 31, (v >> 5 & 31) * 255 // 31, (v >> 10 & 31) * 255 // 31))
    return out


class _Bitmap:
    pal_off = pal_n = px_off = 0

    def palette(self):
        return _bgr555(self.data, self.pal_off, self.pal_n)

    def rebuild(self, pixels):
        packed = g2d.pack_indices(np.asarray(pixels, dtype=np.uint8), self.bpp)
        n = len(self.packed)
        return self.data[:self.px_off] + packed[:n] + self.data[self.px_off + n:]

    def _pixels(self):
        n = self.w * self.h * self.bpp // 8
        self.packed = self.data[self.px_off:self.px_off + n]
        self.pixels = g2d.unpack_indices(self.packed, self.bpp)[:self.w * self.h]


class BPG1(_Bitmap):
    def __init__(self, data):
        self.data = bytes(data)
        if self.data[:4] != b'BPG1' or len(data) < 16:
            raise ValueError('not BPG1')
        self.pal_n, self.bpp, _, self.w, self.h = struct.unpack_from('<HBBHH', data, 4)
        if self.bpp not in (4, 8) or not self.w or not self.h or not 0 < self.pal_n <= 256:
            raise ValueError('BPG1 header')
        self.pal_off = 16
        self.px_off = 16 + self.pal_n * 2
        self.bw, self.bh = struct.unpack_from('<HH', data, 12)
        if not self.bw or not self.bh:
            raise ValueError('BPG1 blocks')
        if self.px_off + self.w * self.h * self.bpp // 8 != len(data):
            raise ValueError('BPG1 size')
        self._pixels()
        stored = np.frombuffer(bytes(self.pixels), np.uint8)
        self.order = self._block_order()
        img = np.empty(self.w * self.h, np.uint8)
        img[self.order] = stored
        self.pixels = bytearray(img.tobytes())

    def _block_order(self):
        # order[i] = image position of the i-th stored pixel
        idx = np.arange(self.w * self.h, dtype=np.int64).reshape(self.h, self.w)
        cols, rows = -(-self.w // self.bw), -(-self.h // self.bh)
        # edge blocks are clipped to the image (192x513: a 1-pixel last row)
        blocks = [idx[y * self.bh:(y + 1) * self.bh, x * self.bw:(x + 1) * self.bw].reshape(-1)
                  for y in range(rows) for x in range(cols)]
        return np.concatenate(blocks)

    def rebuild(self, pixels):
        img = np.asarray(pixels, dtype=np.uint8).reshape(-1)
        return super().rebuild(img[self.order])


class EBP(_Bitmap):
    """u32 width, u32 height, u32 format | palette | pixels (row-major):
      0x35 8bpp + 32 x BGR555     0x53 8bpp + 8 x BGR555
      0x08 8bpp + 256 x RGBX8888  0x04 4bpp + 16 x RGBX8888
    (0x4444 = hardware 4x4 compression and 0x18 = 16bpp direct colour:
    EBPPreview, not editable)"""
    FORMATS = {0x35: (8, 32, 2), 0x53: (8, 8, 2), 0x08: (8, 256, 4), 0x04: (4, 16, 4)}

    def __init__(self, data):
        self.data = bytes(data)
        if len(data) < 12:
            raise ValueError('EBP short')
        self.w, self.h, fmt = struct.unpack_from('<3I', data, 0)
        if fmt not in self.FORMATS or not 0 < self.w <= 1024 or not 0 < self.h <= 1024:
            raise ValueError('EBP header')
        self.bpp, self.pal_n, self.entry = self.FORMATS[fmt]
        self.pal_off = 12
        self.px_off = 12 + self.pal_n * self.entry
        if self.px_off + (self.w * self.h * self.bpp + 7) // 8 != len(data):
            raise ValueError('EBP size')
        self._pixels()

    def palette(self):
        if self.entry == 2:
            return _bgr555(self.data, self.pal_off, self.pal_n)
        return [tuple(self.data[self.pal_off + i * 4:self.pal_off + i * 4 + 3]) for i in range(self.pal_n)]


class EBPPreview:
    """EBP 0x4444: u32 w, h, 0x4444, palette_bytes, index_bytes, texel_bytes
    | BGR555 palette | u32 texel per 4x4 block | u16 mode/palette per block.
    EBP 0x18: 16bpp BGR555, bit 15 = opaque."""
    editable = False

    def __init__(self, data):
        self.data = bytes(data)
        self.w, self.h, fmt = struct.unpack_from('<3I', data, 0)
        if not 0 < self.w <= 1024 or not 0 < self.h <= 1024:
            raise ValueError('EBP header')
        if fmt == 0x4444:
            pb, ib, tb = struct.unpack_from('<3I', data, 12)
            blocks = (self.w // 4) * (self.h // 4)
            if ib != blocks * 2 or tb != blocks * 4 or 24 + pb + tb + ib != len(data):
                raise ValueError('EBP 4x4 size')
            self.fmt, self.spans = fmt, (pb, tb, ib)
        elif fmt == 0x18 and 12 + self.w * self.h * 2 == len(data):
            self.fmt = fmt
        else:
            raise ValueError('EBP preview format')

    def rgba(self):
        from .tex0 import decode_4x4
        if self.fmt == 0x4444:
            pb, tb, ib = self.spans
            o = 24 + pb
            return decode_4x4(self.data[o:o + tb], self.data[o + tb:o + tb + ib], self.data[24:o],
                              self.w, self.h, pal_unit=2)
        v = np.frombuffer(self.data, '<u2', self.w * self.h, 12).reshape(self.h, self.w)
        out = np.zeros((self.h, self.w, 4), np.uint8)
        for k, sh in enumerate((0, 5, 10)):
            c = (v >> sh & 31).astype(np.uint8)
            out[..., k] = c << 3 | c >> 2
        out[..., 3] = np.where(v & 0x8000, 255, 0)
        return out


class CBP1:
    """'CBP1' u16 pal_n, u8 bpp, u8 0, u16 w, u16 h, u16 bw, u16 bh (20 bytes)
    | pal_n x BGR555, right after the header (a long run of black/unused
    entries at the front, seen in most samples, is real palette data, NOT
    padding - found the hard way: an earlier reading skipped it as padding
    and every dumped image came out in the wrong colours entirely) | a
    (w/bw)*(h/bh)-entry table of u16 ABSOLUTE byte offsets (from file start)
    into the tile pool below, one per block | the pool: each block is
    bw x bh 8bpp pixels stored as (bh/8 x bw/8) 8x8 tiles in row-major
    tile order (like NCGR tile mode). Checked on all 79 files: bpp=8,
    bw=bh=32 always, every non-zero offset is block-size-aligned and lands
    inside the pool, tiles never exceed 1023 (unlike NSCR's 10-bit field)
    only because no single file happens to need it - the composer does not
    assume that cap (see render.compose.layout_tiles).

    An offset of 0 is a real, reachable file position (the header) that the
    table uses as a "this cell shows nothing" sentinel - reading pixels
    from there would be harmless (the header bytes happen to map to a
    near-black palette entry) but WRITING there would corrupt the CBP1
    magic, so those cells are marked unmapped (like an out-of-range NSCR
    tile) rather than ever resolved to a real tile index.

    Some files have unreferenced bytes past every table offset (the pool
    is not always fully packed) - rebuild() only ever touches the pool
    span it read from, so that trailing data survives untouched."""

    def __init__(self, data):
        self.data = bytes(data)
        if self.data[:4] != b'CBP1' or len(data) < 20:
            raise ValueError('not CBP1')
        pal_n, bpp, _res, w, h, bw, bh = struct.unpack_from('<HBBHHHH', data, 4)
        if bpp != 8 or not w or not h or not bw or not bh or bw % 8 or bh % 8 or not 0 < pal_n <= 256:
            raise ValueError('CBP1 header')
        self.bpp, self.pal_off, self.pal_n = bpp, 20, pal_n
        table_off = 20 + pal_n * 2
        block_cols, block_rows = w // bw, h // bh
        n_blocks = block_cols * block_rows
        if not n_blocks or table_off + n_blocks * 2 > len(data):
            raise ValueError('CBP1 table does not fit')
        table = struct.unpack_from(f'<{n_blocks}H', data, table_off)
        pool_off = table_off + n_blocks * 2
        tpb_w, tpb_h = bw // 8, bh // 8
        tiles_per_block = tpb_w * tpb_h
        block_bytes = bw * bh
        # tile numbering is ABSOLUTE from the start of the file (a block's
        # own byte offset is always a multiple of 1024, hence of 64, even
        # though pool_off itself is usually not 64-aligned) - never mapped
        # relative to pool_off, which would misalign every tile index
        self.n_tiles = len(data) // 64
        self.cols, self.rows = block_cols * tpb_w, block_rows * tpb_h
        entries = [-1] * (self.cols * self.rows)
        for bi, off in enumerate(table):
            if off < pool_off:
                # "no tile here" sentinel: 0 (a real position - the header),
                # 1 (never a valid offset at all) and 1024 (inside the
                # table itself for files with a bigger table) all show up
                # across the corpus - anything landing before the real pool
                # can never be a genuine block, whatever the exact value
                continue
            if off % 64 or off + block_bytes > len(data):
                raise ValueError('CBP1 block offset out of range')
            by, bx = divmod(bi, block_cols)
            tile_base = off // 64
            for t in range(tiles_per_block):
                ty, tx = divmod(t, tpb_w)
                gy, gx = by * tpb_h + ty, bx * tpb_w + tx
                entries[gy * self.cols + gx] = tile_base + t
        self.entries = entries

    def palette(self):
        return _bgr555(self.data, self.pal_off, self.pal_n)

    @property
    def pixels(self):
        return g2d.unpack_indices(self.data, self.bpp)

    def rebuild(self, pixels):
        raw = g2d.pack_indices(np.asarray(pixels, dtype=np.uint8), self.bpp)
        return bytes(raw) + self.data[len(raw):]


class WinBMP(_Bitmap):
    """A real Windows .BMP file sitting in the ROM.

    Some games ship the authoring format by accident instead of converting
    it (Viewtiful Joe: Double Trouble, `obj/*.bmp`: 49 files, 256x64
    16bpp bitfields). Nothing to reverse engineer - this is the public
    format - but it does need its own reader because it is NOT a Nitro
    resource: rows are stored bottom-up, there is no NCLR, and 16/24/32bpp
    variants carry the colour in the pixel itself.

    Supported for edit: 4/8bpp (indexed, a real palette) and 16/24/32bpp
    (direct colour, exported as RGB). Every write keeps the original
    header, dimensions and bit depth byte for byte - only pixel bytes
    change, same invariant as everywhere else in this project.
    """

    def __init__(self, data):
        self.data = bytes(data)
        if self.data[:2] != b'BM' or len(self.data) < 54:
            raise ValueError('not BMP')
        file_size, _res, self.px_off = struct.unpack_from('<IIi', self.data, 2)
        dib = struct.unpack_from('<I', self.data, 14)[0]
        if dib < 40 or file_size != len(self.data) or not 54 <= self.px_off <= len(self.data):
            raise ValueError('BMP header')
        w, h, planes, bpp, comp = struct.unpack_from('<iiHHI', self.data, 18)
        if planes != 1 or bpp not in (4, 8, 16, 24, 32) or comp not in (0, 3):
            raise ValueError(f'unsupported BMP: {bpp}bpp comp={comp}')
        self.w, self.h = abs(w), abs(h)
        self.bottom_up = h > 0
        self.bpp = bpp
        self.indexed = bpp in (4, 8)
        self.stride = ((self.w * bpp + 31) // 32) * 4    # rows are 4-byte aligned
        if self.px_off + self.stride * self.h > len(self.data):
            raise ValueError('BMP truncated')
        self.pal_n = (1 << bpp) if self.indexed else 0
        self.pal_off = 14 + dib + (12 if comp == 3 and dib == 40 else 0)
        self._load()

    def _rows(self):
        return range(self.h - 1, -1, -1) if self.bottom_up else range(self.h)

    def _load(self):
        rows = []
        for y in self._rows():
            row = self.data[self.px_off + y * self.stride:self.px_off + y * self.stride + self.stride]
            if self.indexed:
                rows.append(g2d.unpack_indices(row, self.bpp)[:self.w])
            else:
                rows.append(np.frombuffer(row, dtype=np.uint8)[:self.w * (self.bpp // 8)])
        self.pixels = np.concatenate(rows) if rows else np.zeros(0, dtype=np.uint8)

    def palette(self):
        """BMP palettes are BGRA (not BGR555 like Nitro)."""
        if not self.indexed:
            return []
        out = []
        for i in range(self.pal_n):
            # a BMP's own header count (pal_n) occasionally overruns what
            # the file actually has (seen in The Sims 3's UI bitmaps) - pad
            # rather than crash the whole preview over the tail few colors
            chunk = self.data[self.pal_off + i * 4:self.pal_off + i * 4 + 4]
            if len(chunk) < 4:
                chunk = chunk.ljust(4, b'\0')
            b, g, r, _a = chunk
            out.append((r, g, b))
        return out

    def rgb_image(self):
        """Direct-colour BMPs (16/24/32bpp) as an (h, w, 3) uint8 array."""
        raw = self.pixels.reshape(self.h, self.w, self.bpp // 8)
        if self.bpp == 16:                       # RGB565 little-endian
            v = raw[:, :, 0].astype(np.uint16) | (raw[:, :, 1].astype(np.uint16) << 8)
            r = ((v >> 11 & 31) * 255 // 31).astype(np.uint8)
            g = ((v >> 5 & 63) * 255 // 63).astype(np.uint8)
            b = ((v & 31) * 255 // 31).astype(np.uint8)
            return np.stack((r, g, b), axis=2)
        return raw[:, :, 2::-1]                  # BGR(A) -> RGB

    def rebuild(self, pixels):
        """`pixels`: indices for 4/8bpp, or an (h, w, 3) RGB array otherwise."""
        arr = np.asarray(pixels)
        out = bytearray(self.data)
        if self.indexed:
            arr = arr.reshape(self.h, self.w).astype(np.uint8)
        else:
            if arr.ndim != 3:
                raise ValueError('direct-colour BMP needs an RGB image')
            if self.bpp == 16:
                # round-trip exact: expanding is x*255//max, so narrowing has
                # to ROUND (x*max+127)//255, not truncate
                r, g, b = (arr[:, :, i].astype(np.uint16) for i in range(3))
                v = (((r * 31 + 127) // 255) << 11) | (((g * 63 + 127) // 255) << 5)                     | ((b * 31 + 127) // 255)
                arr = np.stack((v & 0xFF, v >> 8), axis=2).astype(np.uint8)
            else:
                rgb = arr[:, :, ::-1].astype(np.uint8)       # RGB -> BGR
                if self.bpp == 32:
                    # keep each pixel's original alpha byte: it is not part of
                    # the edited PNG and overwriting it would silently change
                    # transparency the game may rely on
                    old = self.pixels.reshape(self.h, self.w, 4)
                    rgb = np.concatenate((rgb, old[:, :, 3:4]), axis=2)
                arr = rgb
        for i, y in enumerate(self._rows()):
            start = self.px_off + y * self.stride
            row = arr[i]
            packed = g2d.pack_indices(row, self.bpp) if self.indexed else row.tobytes()
            out[start:start + len(packed)] = bytes(packed)
        return bytes(out)

PREVIEW_ONLY = ('EBPPreview',)


def detect(data, name=''):
    """-> class name for a bitmap this module can open, else None. EBP has no
    magic (only a size equation), so it is only tried on a `.ebp` name."""
    if data[:2] == b'BM':
        try:
            WinBMP(data)
            return 'WinBMP'
        except (ValueError, struct.error):
            return None
    for cls in (BPG1, EBP, EBPPreview) if name.lower().endswith('.ebp') else (BPG1,):
        try:
            cls(data)
            return cls.__name__
        except (ValueError, struct.error):
            pass
    return None


def open_bitmap(data, fmt):
    return {'BPG1': BPG1, 'EBP': EBP, 'EBPPreview': EBPPreview, 'WinBMP': WinBMP}[fmt](data)

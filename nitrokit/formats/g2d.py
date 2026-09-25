"""NCLR / NCGR / NSCR / NCER codecs.

Design rule (insertion invariant): parsers record WHERE the editable data
lives inside the original bytes; writers return the original bytes with
only that region replaced. Zero-edit round-trip is byte-identical by
construction, and no header/size/count ever changes.

Pixels are handled as a flat `bytearray` of palette indices (one entry per
pixel, 4bpp already expanded), regardless of tile/bitmap mode.
"""
import struct

from . import nitro


# ---------------------------------------------------------------- colours
def bgr555_to_rgb(v):
    r, g, b = v & 31, v >> 5 & 31, v >> 10 & 31
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2)


def rgb_to_bgr555(c):
    r, g, b = c[:3]
    return (r >> 3) | (g >> 3) << 5 | (b >> 3) << 10


def decode_colors(raw):
    n = len(raw) // 2
    return [bgr555_to_rgb(v) for v in struct.unpack_from(f'<{n}H', raw)]


def encode_colors(colors, original=None):
    """Keep the original bit 15 of each entry (some games use it)."""
    out = bytearray()
    for i, c in enumerate(colors):
        v = rgb_to_bgr555(c)
        if original is not None and 2 * i + 1 < len(original):
            old = original[2 * i] | original[2 * i + 1] << 8
            if bgr555_to_rgb(old & 0x7FFF) == tuple(c[:3]):
                v = old          # unchanged colour: exact original word
        out += struct.pack('<H', v)
    return bytes(out)


def unpack_indices(raw, bpp):
    if bpp == 8:
        return bytearray(raw)
    out = bytearray(len(raw) * 2)
    out[0::2] = bytes(b & 0xF for b in raw)
    out[1::2] = bytes(b >> 4 for b in raw)
    return out


def pack_indices(idx, bpp):
    if bpp == 8:
        return bytes(idx)
    lo, hi = idx[0::2], idx[1::2]
    return bytes((a & 0xF) | (b & 0xF) << 4 for a, b in zip(lo, hi))


def _block(data, magic):
    for m, off, size in nitro.blocks(data):
        if m in (magic, magic[::-1]):
            return off, size
    return None, None


# ------------------------------------------------------------------ NCLR
class NCLR:
    """TTLP: 8 bpp_flag(u32 3=4bpp 4=8bpp) 12 ext_pal(u32) 16 data_size
    20 data_offset(rel. to +8)  ... BGR555 colours.  Optional PMCP block
    lists which palette slot each 16-colour sub-palette really is."""

    def __init__(self, data):
        self.data = bytes(data)
        off, size = _block(data, b'TTLP')
        if off is None:
            raise ValueError('NCLR without TTLP')
        self.bpp = 8 if struct.unpack_from('<I', data, off + 8)[0] == 4 else 4
        declared = struct.unpack_from('<I', data, off + 16)[0]
        rel = struct.unpack_from('<I', data, off + 20)[0]
        start = off + 8 + rel if 0 < rel < size else off + 24
        # SKILL: never trust data_size; clamp to block and file
        avail = min(off + size, len(data)) - start
        n = declared if 0 < declared <= avail else avail
        n -= n % 2
        self.col_off, self.col_len = start, n
        self.colors = decode_colors(data[start:start + n])
        self.pmcp = None
        poff, _ = _block(data, b'PMCP')
        if poff is not None:
            cnt = struct.unpack_from('<H', data, poff + 8)[0]
            base = poff + 8 + struct.unpack_from('<I', data, poff + 12)[0]
            self.pmcp = list(struct.unpack_from(f'<{cnt}H', data, base))

    def palette256(self, sub_count=16):
        """Palette as a list laid out so that 4bpp sub-palette `p` lives at
        p*16. Honours PMCP (compressed palettes)."""
        cols = list(self.colors)
        if self.pmcp:
            per = 16 if self.bpp == 4 else 256
            out = [(0, 0, 0)] * (max(self.pmcp) + 1) * per
            for i, slot in enumerate(self.pmcp):
                out[slot * per:(slot + 1) * per] = (cols[i * per:(i + 1) * per] + [(0, 0, 0)] * per)[:per]
            cols = out
        return (cols + [(0, 0, 0)] * 256)[:max(256, len(cols))]

    def rebuild(self, colors):
        new = bytearray(self.data)
        raw = encode_colors(colors[:self.col_len // 2], self.data[self.col_off:self.col_off + self.col_len])
        new[self.col_off:self.col_off + len(raw)] = raw
        return bytes(new)


# ------------------------------------------------------------------ NCGR
class NCGR:
    """RAHC: 8 h_tiles u16, 10 w_tiles u16, 12 bpp_flag u32, 16/18 grid u16s,
    20 char_flags u32 (bit0 = BITMAP/linear mode, bit8 = 'sparse'),
    24 data_size u32, 28 data_offset u32 (rel. to +8).
    SOPC (optional): 12 w u16, 14 h u16 in tiles."""

    def __init__(self, data):
        self.data = bytes(data)
        off, size = _block(data, b'RAHC')
        if off is None:
            raise ValueError('NCGR without RAHC')
        self.h_tiles, self.w_tiles = struct.unpack_from('<HH', data, off + 8)
        self.bpp = 8 if struct.unpack_from('<I', data, off + 12)[0] == 4 else 4
        self.flags = struct.unpack_from('<I', data, off + 20)[0]
        self.bitmap = bool(self.flags & 1)
        dsize, doff = struct.unpack_from('<II', data, off + 24)
        start = off + 8 + doff if 0 < doff < size else off + 32
        avail = min(off + size, len(data)) - start
        n = dsize if 0 < dsize <= avail else avail
        self.pix_off, self.pix_len = start, n
        self.pixels = unpack_indices(data[start:start + n], self.bpp)
        self.sopc = None
        soff, _ = _block(data, b'SOPC')
        if soff is not None:
            self.sopc = struct.unpack_from('<HH', data, soff + 12)
        if self.w_tiles in (0, 0xFFFF) or self.h_tiles in (0, 0xFFFF):
            nt = len(self.pixels) // 64
            self.w_tiles = min(32, nt) or 1
            self.h_tiles = -(-nt // self.w_tiles)

    @property
    def n_tiles(self):
        return len(self.pixels) // 64

    def tile(self, i):
        return self.pixels[i * 64:(i + 1) * 64]

    def rebuild(self, pixels):
        new = bytearray(self.data)
        raw = pack_indices(pixels, self.bpp)
        if len(raw) != self.pix_len:
            raise ValueError(f'pixel payload size changed {self.pix_len} -> {len(raw)}')
        new[self.pix_off:self.pix_off + len(raw)] = raw
        return bytes(new)


# ------------------------------------------------------------------ NSCR
class NSCR:
    """NRCS: 8 width_px u16, 10 height_px u16, 12 u32 (bg type), 16 size
    u32, 20 entries u16: tile 0-9 | hflip 10 | vflip 11 | palette 12-15."""

    def __init__(self, data):
        self.data = bytes(data)
        off, size = _block(data, b'NRCS')
        if off is None:
            raise ValueError('NSCR without NRCS')
        self.w_px, self.h_px = struct.unpack_from('<HH', data, off + 8)
        dsize = struct.unpack_from('<I', data, off + 16)[0]
        avail = min(off + size, len(data)) - (off + 20)
        n = (dsize if 0 < dsize <= avail else avail) // 2
        self.entries = list(struct.unpack_from(f'<{n}H', data, off + 20))
        cols = max(1, self.w_px // 8)
        if cols * (self.h_px // 8) > n:      # bogus dims: fall back
            self.h_px = -(-n // cols) * 8

    @property
    def cols(self):
        return max(1, self.w_px // 8)

    @property
    def rows(self):
        return self.h_px // 8


# ------------------------------------------------------------------ NCER
SHAPE_SIZE = {
    (0, 0): (8, 8), (0, 1): (16, 16), (0, 2): (32, 32), (0, 3): (64, 64),
    (1, 0): (16, 8), (1, 1): (32, 8), (1, 2): (32, 16), (1, 3): (64, 32),
    (2, 0): (8, 16), (2, 1): (8, 32), (2, 2): (16, 32), (2, 3): (32, 64),
    (3, 0): (8, 8), (3, 1): (16, 16), (3, 2): (32, 32), (3, 3): (64, 64),
}


class NCER:
    """KBEC: 8 n_cells u16, 10 cell_type u16 (0: 8B/cell, 1: 16B with bbox),
    12 cells_offset u32 (rel +8), 16 mapping_mode u32 (tile index << mode),
    20 vram_transfer off, 24 partition off (UCAT/LBAL may follow).
    OAM: attr0 y/rotscale/shape, attr1 x/hflip/vflip/size, attr2 tile/pal."""

    def __init__(self, data):
        self.data = bytes(data)
        off, size = _block(data, b'KBEC')
        if off is None:
            raise ValueError('NCER without KBEC')
        n_cells, ctype = struct.unpack_from('<HH', data, off + 8)
        coff, self.mapping = struct.unpack_from('<II', data, off + 12)
        vt_off = struct.unpack_from('<I', data, off + 20)[0] if size >= 24 else 0
        self.vram_src = self._vram_table(data, off, n_cells, vt_off)
        base = off + 8 + coff
        step = 16 if ctype == 1 else 8
        oam_base = base + n_cells * step
        self.cells = []
        for c in range(n_cells):
            n_oam, attr, oam_off = struct.unpack_from('<HHI', data, base + c * step)
            oams = []
            for o in range(n_oam):
                p = oam_base + oam_off + o * 6
                if p + 6 > len(data):
                    break
                a0, a1, a2 = struct.unpack_from('<HHH', data, p)
                y = a0 & 0xFF
                y -= 256 if y >= 128 else 0
                x = a1 & 0x1FF
                x -= 512 if x >= 256 else 0
                rs = a0 >> 8 & 1
                w, h = SHAPE_SIZE[(a0 >> 14, a1 >> 14)]
                oams.append({'x': x, 'y': y, 'w': w, 'h': h, 'tile': a2 & 0x3FF,
                             'pal': a2 >> 12, 'rotscale': rs,
                             'hflip': 0 if rs else a1 >> 12 & 1,
                             'vflip': 0 if rs else a1 >> 13 & 1})
            self.cells.append({'index': c, 'attr': attr, 'oams': oams})

    @staticmethod
    def _vram_table(data, kbec_off, n_cells, vt_off):
        """KBEC's VRAM-transfer block (Kimi no Yuusha, Radiant Historia):
        per-cell (srcDataOffset, szByte) at base+arrayOffset - OAM tile
        indices inside that cell are relative to srcDataOffset, not to the
        start of the NCGR. None when the block is absent (offset 0, the
        common case)."""
        if not vt_off:
            return None
        base = kbec_off + 8 + vt_off
        try:
            _, arr_off = struct.unpack_from('<II', data, base)
            out = []
            for i in range(n_cells):
                p = base + arr_off + i * 8
                if p + 8 > len(data):
                    out.append(0)
                    continue
                src, _sz = struct.unpack_from('<II', data, p)
                out.append(src)
            return out
        except struct.error:
            return None

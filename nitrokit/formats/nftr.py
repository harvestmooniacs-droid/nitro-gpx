"""NFTR - the DS bitmap font format (magic 'RTFN', reversed like every
other Nitro container). Public, community-documented format (the
FINF/CGLP/CWDH/CMAP sub-block names, again stored reversed on disk as
FNIF/PLGC/HDWC/PAMC, are the well-known ones from the DS font-hacking
community) - not implemented anywhere in this toolkit before.

Reverse-engineered/validated for THIS project from real files (6 fonts
across 3 games, header layout cross-checked via exact block-size
divisibility on two independent samples, pixel bit order confirmed
unambiguously by decoding a real capital 'A' in Witch's Wish's LC10.NFTR):

FINF body (20 bytes, right after the FNIF magic+size):
  u8  unknown0            u8 line_height
  u16 unknown (width-ish, often 0)
  u8  ascent              u8 unknown5      u8 unknown6   u8 pad
  u32 cglp_data_offset    (absolute file offset, points at CGLP's body
                            start - i.e. block_offset + 8, not the glyph
                            data start; harmless to ignore, kept for
                            round-trip-through-the-container purposes)
  u32 cwdh_data_offset    (same convention)
  u32 cmap_data_offset    (first CMAP block's body start; CMAP blocks can
                            chain via their own `next` pointer to cover
                            more character ranges)

CGLP body:
  u8  cell_width          u8 cell_height
  u16 tile_size           (bytes per glyph - TRUST this stored value; it
                            equals ceil(cell_width*cell_height*bpp/8), a
                            CONTINUOUS bitstream, not per-row-byte-aligned
                            like NCGR - confirmed because the row-aligned
                            hypothesis fails the exact-division check on
                            non-8-divisible widths, e.g. an 11x11 1bpp
                            glyph: continuous=ceil(121/8)=16 matches the
                            stored field, row-aligned=ceil(11/8)*11=22
                            does not)
  u8  render_width        u8 render_height  (both usually >= cell dims -
                            NOT the storage size, ignored here)
  u8  bpp                 u8 rotation/flags (0 = no rotation, seen so far)
  u16 reserved
  then N glyphs of `tile_size` bytes each, back-to-back, N = however many
  fit in the rest of the block (trailing bytes are alignment padding,
  NOT a partial glyph - always floor-divide).
  Pixel order within a glyph: row-major (y then x), MSB-first per byte,
  as ONE continuous bitstream across the whole glyph (bit index
  y*cell_width+x, byte = bitstream[i//8], value = (byte >> (7-i%8)) & 1).
  A pixel VALUE is a palette index in [0, 2**bpp), 0 = background/
  transparent; there is normally no companion NCLR - render as greyscale
  (bpp=1: black/white; bpp=2/4: grey ramp, i.e. anti-aliased glyphs).

CWDH body: u16 first_glyph, u16 last_glyph, u32 next_cwdh_offset (0 = end
  of chain), then (last-first+1) entries of 3 bytes: pixel_left,
  char_width (ink width), total_width (advance). Not pixel data - kept
  read-only here (exposed for a nicer glyph-sheet label / advance-aware
  layout later, not required for dump/insert).

CMAP body: u16 first_code, u16 last_code, u32 map_type, u32 next_offset
  (0 = end of chain; multiple CMAP blocks commonly cover disjoint
  unicode ranges, e.g. ASCII + a symbol range + a JIS range):
    type 0 (direct range): glyph_index = code - first_code
    type 1 (table): u16 glyph_index per code, first..last, in order
    type 2 (scan list): u16 count, then count * (u16 code, u16 glyph)
  Used only to LABEL glyphs in the dumped sheet (e.g. 'A' instead of
  '#65') - dump/insert works from the raw glyph index either way, so a
  CMAP parsing gap never blocks editing, only the label.

Insertion invariant, same as every other format here: only the glyph
BITMAP bytes are ever rewritten. Cell size, glyph count, widths and the
character map are never touched, so nothing needs to move or resize.
"""
import struct

from . import nitro


def _block(data, magic):
    for m, off, size in nitro.blocks(data):
        if m in (magic, magic[::-1]):
            return off, size
    return None, None


class NFTR:
    def __init__(self, data):
        self.data = bytes(data)
        finf_off, finf_size = _block(data, b'FINF')
        cglp_off, cglp_size = _block(data, b'CGLP')
        if finf_off is None or cglp_off is None:
            raise ValueError('NFTR missing FINF or CGLP block')
        self.line_height = data[finf_off + 8 + 1]
        w, h, tile_size, rw, rh, bpp, flags = struct.unpack_from(
            '<BBHBBBB', data, cglp_off + 8)
        self.cell_w, self.cell_h, self.bpp = w, h, bpp
        self.tile_size = tile_size
        self.glyph_off = cglp_off + 8 + 10
        avail = cglp_off + cglp_size - self.glyph_off
        self.n_glyphs = avail // tile_size if tile_size else 0
        self.glyph_data_len = self.n_glyphs * tile_size
        self.widths = self._parse_cwdh(data)
        self.char_map = self._parse_cmap(data)   # {code: glyph_index}

    # ------------------------------------------------------------- CWDH
    def _parse_cwdh(self, data):
        # a chained second CWDH block (rare - most fonts have one) is
        # skipped: widths are read-only supplementary info here, never
        # needed for pixel dump/insert, so under-reporting a few is safe.
        off, size = _block(data, b'CWDH')
        widths = {}
        if off is None:
            return widths
        first, last = struct.unpack_from('<HH', data, off + 8)
        body = off + 16
        for g in range(first, last + 1):
            p = body + (g - first) * 3
            if p + 3 <= off + size:
                widths[g] = tuple(data[p:p + 3])   # (left, char_w, advance)
        return widths

    # ------------------------------------------------------------- CMAP
    def _parse_cmap(self, data):
        out = {}
        off, _size = _block(data, b'CMAP')
        seen = set()
        while (off is not None and off not in seen and 0 < off + 16 <= len(data)
               and data[off:off + 4] in (b'CMAP', b'PAMC')):
            seen.add(off)
            first, last, map_type, nxt = struct.unpack_from('<HHII', data, off + 8)
            body = off + 24
            try:
                if map_type == 0:
                    for code in range(first, last + 1):
                        out[code] = code - first
                elif map_type == 1:
                    n = last - first + 1
                    tbl = struct.unpack_from(f'<{n}H', data, body)
                    for i, code in enumerate(range(first, last + 1)):
                        if tbl[i] != 0xFFFF:
                            out[code] = tbl[i]
                elif map_type == 2:
                    n = struct.unpack_from('<H', data, body)[0]
                    for i in range(n):
                        code, gi = struct.unpack_from('<HH', data, body + 2 + i * 4)
                        out[code] = gi
            except struct.error:
                pass
            off = nxt if nxt else None
        return out

    def label(self, glyph_index):
        for code, gi in self.char_map.items():
            if gi == glyph_index:
                if 0x20 <= code < 0x7F:
                    return chr(code)
                return f'U+{code:04X}'
        return f'#{glyph_index}'

    # ------------------------------------------------------------- pixels
    def glyph_pixels(self, i):
        """Flat list of cell_w*cell_h indices (row-major), MSB-first."""
        base = self.glyph_off + i * self.tile_size
        raw = self.data[base:base + self.tile_size]
        bpp = self.bpp
        out = []
        bit_i = 0
        n_px = self.cell_w * self.cell_h
        for _ in range(n_px):
            v = 0
            for _ in range(bpp):
                byte = raw[bit_i >> 3]
                v = v << 1 | (byte >> (7 - (bit_i & 7)) & 1)
                bit_i += 1
            out.append(v)
        return out

    def all_pixels(self):
        """One flat bytearray of the whole glyph-data span, unpacked to
        1 index/byte (n_glyphs * cell_w * cell_h entries)."""
        out = bytearray()
        for i in range(self.n_glyphs):
            out += bytes(self.glyph_pixels(i))
        return out

    def rebuild(self, all_pixels):
        """`all_pixels`: same flat layout as all_pixels() (possibly
        edited). Returns full NFTR file bytes with only the glyph bitmap
        region replaced."""
        bpp = self.bpp
        n_px = self.cell_w * self.cell_h
        # start from the ORIGINAL bytes so any trailing padding bits past
        # cell_w*cell_h*bpp (tile_size is usually a couple of bits roomier
        # than the glyph needs) round-trip byte-identical on a zero-edit
        # dump - only the bits that are REAL pixels get explicitly set or
        # cleared, never just OR'd in.
        out = bytearray(self.data[self.glyph_off:self.glyph_off + self.glyph_data_len])
        for i in range(self.n_glyphs):
            px = all_pixels[i * n_px:(i + 1) * n_px]
            base = i * self.tile_size
            bit_i = 0
            for v in px:
                v &= (1 << bpp) - 1
                for b in range(bpp - 1, -1, -1):
                    mask = 1 << (7 - (bit_i & 7))
                    if (v >> b) & 1:
                        out[base + (bit_i >> 3)] |= mask
                    else:
                        out[base + (bit_i >> 3)] &= ~mask & 0xFF
                    bit_i += 1
        new = bytearray(self.data)
        new[self.glyph_off:self.glyph_off + self.glyph_data_len] = out
        return bytes(new)

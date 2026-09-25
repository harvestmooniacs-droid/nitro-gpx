"""Monolith Soft's DS 2D formats (Soma Bringer, inside its nested data.srl).
Not Nitro. Reverse-engineered here from the files alone; every layout below
was checked by an exact size equation over all samples and visually.

OBP1 (.obp/.ntp/.obps, and concatenated inside .img) - one sprite/icon:
  0x00 'OBP1'
  0x04 u16 a, u16 b     block size = 8 << (a & 3) by 8 << (b & 3); the high
                        bits are flags not needed for pixels
  0x08 u16 width, u16 height
  0x0C u16 flags        bit0 = 8bpp (else 4bpp, low nibble first)
                        bit9 (0x200) = extended header with a block table
                        (portraits, stage maps; see _extended)
  0x0E u16 palette byte size
  0x10 BGR555 palette, then width*height pixels stored BLOCK BY BLOCK
       (each block its own little raster, blocks in row-major order; a
       block larger than the image is clipped to it).
  every plain (non-0x200) file satisfies 16 + pal + w*h*bpp/8 == size exactly.

BGP1 (.bgp) - a background = NCLR + NCGR (tile mode) + NSCR in one file:
  0x04 u32 size   0x08 u16 width, u16 height   0x0C u8 tile(8) u8 bpp u8 u8
  0x10 u32 pal_off, pal_size, tile_off, tile_size, map_off, map_size
  map entries are NSCR-style u16 (tile 10 bits, hflip, vflip, sub-palette).
  All 23 files: map_off + map_size == size exactly.

Insertion invariant as everywhere else: only pixel/tile bytes are rewritten.
"""
import re
import struct

from . import g2d

MAGICS = (b'OBP1', b'BGP1')
_RE = re.compile(b'OBP1|BGP1')


class Unsupported(ValueError):
    pass


class OBP1:
    def __init__(self, data):
        self.data = bytes(data)
        a, b, self.w, self.h, self.flags, ps = struct.unpack_from('<6H', data, 4)
        self.bpp = 8 if self.flags & 1 else 4
        if not self.w or not self.h:
            raise Unsupported('OBP1 with an empty size')
        self.px_len = self.w * self.h * self.bpp // 8
        if self.flags & 0x200:
            self._extended(data)
            if (8 << (a & 3), 8 << (b & 3)) != (self.bw, self.bh):
                # dangerico.obp: header and table disagree and neither layout
                # renders correctly - refuse rather than show a wrong image
                raise Unsupported('OBP1 block size field contradicts its block table')
        else:
            self.bw, self.bh = min(8 << (a & 3), self.w), min(8 << (b & 3), self.h)
            if self.w % self.bw or self.h % self.bh:
                raise Unsupported('OBP1 size is not a whole number of blocks')
            self.pal_off, self.pal_size = 16, ps
            self.px_off = 16 + ps
        if self.px_off + self.px_len > len(data):
            raise Unsupported('OBP1 truncated')

    def _extended(self, data):
        """Flag 0x200 (face.img portraits, stage maps): u32 cell_off,
        n_cells, block_off, n_blocks, pal_off, pal_size, px_off, px_size at
        0x10; each block record = u32 flags, u16 data_offset/32, u8 x, u8 y.
        Accepted only when the blocks form the plain row-major grid AND agree
        with the 0x04 block-size field (116 of 117 samples; dangerico.obp
        does not and is refused) - anything else needs real cell composition."""
        (_, _, blk_off, n_blk, self.pal_off, self.pal_size,
         self.px_off, px_size) = struct.unpack_from('<8I', data, 16)
        if not n_blk or blk_off + n_blk * 8 > len(data):
            raise Unsupported('OBP1 extended header has no block table')
        per_row = sum(1 for i in range(n_blk) if data[blk_off + i * 8 + 7] == 0)
        rows = n_blk // per_row
        if per_row * rows != n_blk or self.w % per_row or self.h % rows:
            raise Unsupported('OBP1 blocks are not a plain grid')
        self.bw, self.bh = self.w // per_row, self.h // rows
        if px_size != self.px_len:
            raise Unsupported('OBP1 extended header does not match its size')
        block_bytes = self.bw * self.bh * self.bpp // 8
        for i in range(n_blk):
            _, off32, x, y = struct.unpack_from('<IHBB', data, blk_off + i * 8)
            by, bx = divmod(i, per_row)
            if off32 * 32 != i * block_bytes or x != (bx * self.bw) & 0xFF or y != (by * self.bh) & 0xFF:
                raise Unsupported('OBP1 blocks are not a plain grid')

    @property
    def size(self):
        return self.px_off + self.px_len

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_size])

    @property
    def pixels(self):
        return g2d.unpack_indices(self.data[self.px_off:self.px_off + self.px_len], self.bpp)

    def rebuild(self, pixels):
        raw = g2d.pack_indices(pixels, self.bpp)
        return self.data[:self.px_off] + raw + self.data[self.px_off + self.px_len:]


class BGP1:
    def __init__(self, data):
        self.data = bytes(data)
        self.total, self.w, self.h, tile, self.bpp = struct.unpack_from('<IHHBB', data, 4)
        (self.pal_off, self.pal_size, self.tile_off, self.tile_size,
         self.map_off, self.map_size) = struct.unpack_from('<6I', data, 0x10)
        if tile != 8 or self.bpp not in (4, 8) or self.map_off + self.map_size > len(data):
            raise Unsupported('unexpected BGP1 header')
        self.cols, self.rows = self.w // 8, self.h // 8
        self.entries = struct.unpack_from(f'<{self.map_size // 2}H', data, self.map_off)

    @property
    def size(self):
        return self.map_off + self.map_size

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_size])

    @property
    def pixels(self):
        return g2d.unpack_indices(self.data[self.tile_off:self.tile_off + self.tile_size], self.bpp)

    @property
    def n_tiles(self):
        return len(self.pixels) // 64

    def rebuild(self, pixels):
        raw = g2d.pack_indices(pixels, self.bpp)
        return self.data[:self.tile_off] + raw + self.data[self.tile_off + self.tile_size:]


def records(data):
    """(offset, size, magic) of every OBP1/BGP1 record in a blob. .img files
    concatenate records padded to 512 bytes; a record's span runs to the
    next record (or the end), which keeps its padding inside its slice."""
    offs = [m.start() for m in _RE.finditer(data) if m.start() % 4 == 0]
    out = []
    for i, off in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else len(data)
        out.append((off, end - off, data[off:off + 4].decode()))
    return out

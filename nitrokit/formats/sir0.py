"""SIR0 - Chunsoft's relocatable container (999; the same container is used
by other Chunsoft/Spike Chunsoft DS titles), plus the background image
record found inside 999's AT6P-compressed `bg/**.dat` files.

Container (little-endian):
  0x00 'SIR0'   0x04 u32 content_offset   0x08 u32 pointer_table_offset
  0x0C u32 0
  pointer table: big-endian 7-bit varints (high bit = more bytes follow),
  each one a DELTA to the next file offset that holds a pointer; 0 ends it.
  Editing pixels in place never moves anything, so the table is only read
  (it is what makes a record findable without per-game knowledge).

Background image record at content_offset (layout from 9H9P9DTools'
SirBg.cs, which reads only the two offsets; the rectangle fields were
identified here and give the size, which that tool takes by hand):
  u32 x0, y0, x1, y1      visible rectangle in 8px tiles (inclusive)
  u32 ?, ?
  u32 pixels_offset, palette_offset, palette_end
  width = (x1-x0+1)*8, height = (y1-y0+1)*8
  pixels = palette_offset - pixels_offset bytes = width*height (8bpp) or
  half that (4bpp); palette = BGR555, palette_end - palette_offset bytes.
Checked on all 5034 999 backgrounds (pixel size matches exactly on every
one) and against the 950 hand-entered sizes of 9H9P9DTools' export list
(934 agree; the 16 that differ are wrong in the list - their byte counts
cannot hold the listed size).
"""
import struct

from . import g2d

MAGIC = b'SIR0'


class Unsupported(ValueError):
    pass


def is_sir0(data):
    return len(data) >= 16 and data[:4] == MAGIC


def pointers(data):
    """File offsets that hold pointers, from the relocation table."""
    table = struct.unpack_from('<I', data, 8)[0]
    out, pos, acc, i = [], 0, 0, table
    while i < len(data):
        b = data[i]
        i += 1
        acc = acc << 7 | b & 0x7F
        if b & 0x80:
            continue
        if acc == 0:
            break
        pos += acc
        out.append(pos)
        acc = 0
    return out


class SirImage:
    tiled = False

    def __init__(self, data):
        self.data = bytes(data)
        if not is_sir0(data):
            raise Unsupported('not SIR0')
        content = struct.unpack_from('<I', data, 4)[0]
        if content + 36 > len(data):
            raise Unsupported('SIR0 content record out of range')
        x0, y0, x1, y1, _, _, px, pal, pal_end = struct.unpack_from('<9I', data, content)
        if not (x0 <= x1 < 256 and y0 <= y1 < 256 and 16 <= px < pal < pal_end <= len(data)):
            raise Unsupported('SIR0 content is not an image record')
        self.w, self.h = (x1 - x0 + 1) * 8, (y1 - y0 + 1) * 8
        self.px_off, self.px_len = px, pal - px
        if self.px_len == self.w * self.h:
            self.bpp = 8
        elif self.px_len * 2 == self.w * self.h:
            self.bpp = 4
        else:
            raise Unsupported('SIR0 pixel size does not match its rectangle')
        self.pal_off, self.pal_size = pal, pal_end - pal
        if self.pal_size % 2 or not 32 <= self.pal_size <= 512:
            raise Unsupported('SIR0 palette size is implausible')

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_size])

    @property
    def pixels(self):
        return g2d.unpack_indices(self.data[self.px_off:self.px_off + self.px_len], self.bpp)

    def rebuild(self, pixels):
        raw = g2d.pack_indices(pixels, self.bpp)
        return self.data[:self.px_off] + raw + self.data[self.px_off + self.px_len:]


class SirIcon(SirImage):
    """Square icon (999 `item/*.dat`, 148 files): the content record is
    just `u32 pixels_offset, palette_offset` and the palette runs to the
    record itself. No size is stored: pixels must form a perfect square
    (4bpp for a 16-colour palette, else 8bpp) or the file is refused.
    Pixels are stored as 8x8 tiles in row order (like NCGR tile mode), not
    as one raster - `tiled` tells the composer."""
    tiled = True

    def __init__(self, data):
        self.data = bytes(data)
        if not is_sir0(data):
            raise Unsupported('not SIR0')
        content = struct.unpack_from('<I', data, 4)[0]
        if content + 8 > len(data):
            raise Unsupported('SIR0 content record out of range')
        px, pal = struct.unpack_from('<2I', data, content)
        if not 16 <= px < pal < content:
            raise Unsupported('SIR0 content is not an icon record')
        self.pal_off, self.pal_size = pal, content - pal
        if self.pal_size not in (32, 512):
            raise Unsupported('SIR0 icon palette size is implausible')
        self.bpp = 4 if self.pal_size == 32 else 8
        self.px_off, self.px_len = px, pal - px
        n = self.px_len * 8 // self.bpp
        side = int(n ** 0.5)
        if side * side != n or side % 8:
            raise Unsupported('SIR0 icon is not a square of whole 8x8 tiles')
        self.w = self.h = side


def open_image(data):
    """SirImage or SirIcon, whichever layout the file has."""
    try:
        return SirImage(data)
    except Unsupported:
        return SirIcon(data)


class SirSprite:
    """999 character sprite (`cha/*.dat`, uncompressed SIR0). Found through
    the pointer table: three adjacent pointers (palette, pixels, pieces) with
    a u32 pixel-byte count 20 bytes before them equal to pieces - pixels.
    Pieces are 10-byte records `u16 w, h, x, y, byte_offset` (8bpp) laid
    out on the sprite canvas, ending at a zeroed record. Pixel bytes past the
    last piece are the frames of animated parts (e.g. 5 x 8x8 mouth frames);
    their shape lives in a part table this reader does not decode, so they
    are exposed as one extra strip."""

    def __init__(self, data):
        self.data = bytes(data)
        if not is_sir0(data):
            raise Unsupported('not SIR0')
        ptrs = set(pointers(data))
        found = None
        for q in sorted(ptrs):
            if q + 4 in ptrs and q + 8 in ptrs and q >= 36:
                pal, pix, pcs = struct.unpack_from('<3I', data, q)
                size = struct.unpack_from('<I', data, q - 20)[0]
                if 16 <= pal < pix < pcs <= len(data) and 0 < size == pcs - pix:
                    found = (pal, pix, pcs, size)
                    break
        if not found:
            raise Unsupported('no SIR0 sprite record')
        pal, self.px_off, pcs, self.px_len = found
        self.pal_off, self.pal_size = pal, self.px_off - pal
        if self.pal_size % 2 or not 32 <= self.pal_size <= 512:
            raise Unsupported('SIR0 sprite palette size is implausible')
        self.pieces, total, o = [], 0, pcs
        while total < self.px_len and o + 10 <= len(data):
            w, h, x, y, off = struct.unpack_from('<5H', data, o)
            o += 10
            if w == 0 or h == 0:
                break
            if off != total & 0xFFFF or total + w * h > self.px_len:
                raise Unsupported('SIR0 sprite pieces are not contiguous')
            self.pieces.append((w, h, x, y, total))
            total += w * h
        if not self.pieces:
            raise Unsupported('SIR0 sprite has no pieces')
        self.extra_off, self.extra_len = total, self.px_len - total
        self.bpp = 8

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_size])

    @property
    def pixels(self):
        return bytearray(self.data[self.px_off:self.px_off + self.px_len])

    def rebuild(self, pixels):
        return self.data[:self.px_off] + bytes(pixels) + self.data[self.px_off + self.px_len:]

"""Level-5 Professor Layton graphics (.arc backgrounds, .arc/.arj animations).

Not Nintendo NARC despite the extension: every file is a u32 codec type
(1 RLE, 2 LZ10, 3/4 Huffman) followed by that DS BIOS stream (Tinke's LAYTON plugin, Bg.cs / Ani.cs - cross-checked by
rendering real files). Shared by Layton 1-3 (A5FE/YLTE/C2AJ...), so it is
detected by content, not by game code.

Background (decompressed):
  u32 n_colors, n_colors x BGR555
  u32 n_tiles,  n_tiles x 64 bytes (8bpp tiles)
  u16 width_tiles, u16 height_tiles, width*height u16 NSCR-style entries

Animation (decompressed), .arc (type 0) / .arj (type 1):
  u16 n_images, u16 depth (3 = 4bpp, 4 = 8bpp) [, u32 n_colors  (.arj)]
  per image: u16 w, u16 h, u16 n_parts, u16 pad
    per part: [u16 glbX, u16 glbY (.arj)] u16 x, u16 y,
              u16 log2(w/8), u16 log2(h/8), linear pixels (w*h*bpp/8)
  palette: [u32 n_colors (.arc)] n_colors x BGR555
  rest (animation names/scripts) - kept verbatim

Insertion edits pixels in place (same tile/part count and sizes), then
re-compresses with the file's own codec - the same thing Tinke's Write does.
"""
import struct

from .. import compression
from ..compression import lz10
from . import g2d


class Unsupported(ValueError):
    pass


# leading u32 = which DS BIOS codec follows (all 4 seen in Layton 1:
# 1770 LZ10, 58 RLE, 47 Huffman-8, 2 Huffman-4), each checked against the
# stream's own first byte
CODECS = {1: ('rle', 0x30), 2: ('lz10', 0x10), 3: ('huff4', 0x24), 4: ('huff8', 0x28)}


def _unwrap(data):
    """-> (decompressed payload, codec type)."""
    data = bytes(data)
    kind = struct.unpack_from('<I', data, 0)[0] if len(data) >= 8 else None
    if kind not in CODECS or data[4] != CODECS[kind][1]:
        raise Unsupported('not a Layton container (u32 codec type + DS BIOS stream expected)')
    codec = CODECS[kind][0]
    try:
        out = compression.decompress_as(data[4:], codec)
    except (ValueError, IndexError) as exc:
        raise Unsupported(f'{codec} stream broken: {exc}') from exc
    if codec == 'lz10':
        tail = data[4 + lz10.compressed_length(data[4:]):]
        # our own compressor pads to 4 bytes; anything else means another format
        if len(tail) > 3 or any(tail):
            raise Unsupported('bytes left over after the LZ10 stream')
    return out, kind


def _wrap(payload, kind):
    return struct.pack('<I', kind) + compression.rewrap(payload, CODECS[kind][0])


class LaytonBG:
    def __init__(self, data):
        out, self.codec_type = _unwrap(data)
        self.data = out
        if len(out) < 12:
            raise Unsupported('too short')
        self.pal_n = struct.unpack_from('<I', out, 0)[0]
        self.pal_off = 4
        pal_end = 4 + self.pal_n * 2
        if not 0 < self.pal_n <= 256 or pal_end + 8 > len(out):
            raise Unsupported(f'palette count {self.pal_n} out of range')
        self.tile_n = struct.unpack_from('<I', out, pal_end)[0]
        self.tile_off = pal_end + 4
        self.tile_size = self.tile_n * 64
        map_off = self.tile_off + self.tile_size
        if not self.tile_n or map_off + 4 > len(out):
            raise Unsupported(f'tile count {self.tile_n} out of range')
        self.cols, self.rows = struct.unpack_from('<HH', out, map_off)
        n = self.cols * self.rows
        if not n or map_off + 4 + n * 2 != len(out):
            raise Unsupported('map size does not match the file')
        self.entries = struct.unpack_from(f'<{n}H', out, map_off + 4)
        self.bpp = 8

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_n * 2])

    @property
    def pixels(self):
        return g2d.unpack_indices(self.data[self.tile_off:self.tile_off + self.tile_size], 8)

    @property
    def n_tiles(self):
        return self.tile_n

    def rebuild(self, pixels):
        raw = g2d.pack_indices(pixels, 8)
        if len(raw) != self.tile_size:
            raise Unsupported('tile data size changed')
        return _wrap(self.data[:self.tile_off] + raw + self.data[self.tile_off + self.tile_size:],
                     self.codec_type)


class LaytonAni:
    """`arj`: the .arj variant (per-part global offset, palette count in the
    header). Parts are linear bitmaps placed at (x, y) inside their image."""

    def __init__(self, data, arj=False):
        out, self.codec_type = _unwrap(data)
        self.data = out
        try:
            self._parse(out, arj)
        except struct.error as exc:
            raise Unsupported(f'truncated animation: {exc}') from exc

    def _parse(self, out, arj):
        n_images, depth = struct.unpack_from('<HH', out, 0)
        if depth not in (3, 4) or not 0 < n_images <= 512:
            raise Unsupported('not a Layton animation header')
        self.bpp = 4 if depth == 3 else 8
        pos = 4
        if arj:
            n_colors = struct.unpack_from('<I', out, pos)[0]
            pos += 4
        self.images = []
        for _ in range(n_images):
            w, h, n_parts, _pad = struct.unpack_from('<4H', out, pos)
            pos += 8
            if not n_parts or n_parts > 256:
                raise Unsupported('bad part count')
            parts = []
            for _ in range(n_parts):
                if arj:
                    pos += 4                                      # glbX, glbY
                x, y, lw, lh = struct.unpack_from('<4H', out, pos)
                if lw > 3 or lh > 3:                               # 8..64 px, OBJ sizes
                    raise Unsupported('bad part size')
                pw, ph = 8 << lw, 8 << lh
                size = pw * ph * self.bpp // 8
                if pos + 8 + size > len(out):
                    raise Unsupported('part runs past the end')
                parts.append({'x': x, 'y': y, 'w': pw, 'h': ph, 'off': pos + 8, 'size': size})
                pos += 8 + size
            self.images.append({'w': w, 'h': h, 'parts': parts})
        if not arj:
            n_colors = struct.unpack_from('<I', out, pos)[0]
            pos += 4
        if not 0 < n_colors <= 256 or pos + n_colors * 2 > len(out):
            raise Unsupported('bad palette')
        self.pal_off, self.pal_n = pos, n_colors

    def palette(self):
        return g2d.decode_colors(self.data[self.pal_off:self.pal_off + self.pal_n * 2])

    def frame_pixels(self, i):
        out = bytearray()
        for p in self.images[i]['parts']:
            out += g2d.unpack_indices(self.data[p['off']:p['off'] + p['size']], self.bpp)
        return out

    def frame_size(self, i):
        img = self.images[i]
        w = max([img['w']] + [p['x'] + p['w'] for p in img['parts']])
        h = max([img['h']] + [p['y'] + p['h'] for p in img['parts']])
        return w, h

    def rebuild(self, i, pixels):
        out = bytearray(self.data)
        pos = 0
        for p in self.images[i]['parts']:
            n = p['w'] * p['h']
            raw = g2d.pack_indices(pixels[pos:pos + n], self.bpp)
            out[p['off']:p['off'] + p['size']] = raw
            pos += n
        return _wrap(bytes(out), self.codec_type)


def open_any(data, path):
    """-> ('bg', LaytonBG) | ('ani', LaytonAni) | None."""
    ext = path.rsplit('.', 1)[-1].lower()
    if ext == 'arj':
        candidates = ((LaytonAni, {'arj': True}, 'ani'),)
    elif ext == 'arc':
        candidates = ((LaytonBG, {}, 'bg'), (LaytonAni, {}, 'ani'))
    else:
        return None
    for cls, kw, kind in candidates:
        try:
            return kind, cls(data, **kw)
        except Unsupported:
            continue
    return None

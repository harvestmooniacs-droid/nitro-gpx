"""TEX0 textures (inside NSBTX 'BTX0' and NSBMD 'BMD0' files).

G3D files use a different outer layout from G2D: after the 16-byte header
comes a table of u32 block OFFSETS (not inline blocks).

TEX0 block (offsets relative to 'TEX0'):
  0x0E u16 tex info-dict offset     0x14 u32 tex data offset
  0x1C u16 4x4 data size (<<3)      0x24 u32 4x4 data offset
  0x28 u32 4x4 palette-index data offset
  0x30 u32 pal data size (<<3)      0x34 u32 pal dict offset  0x38 pal data
Dict: u8 0, u8 count, u16 size | unk block (u16 hdr, u16 size ...) |
      u16 unit, u16 size, count*unit entries | count*16 names.
Texture entry (8B): u16 offset<<3, u16 params:
  bits 4-6 log2(w/8), bits 7-9 log2(h/8), bits 10-12 format
  (2=2bpp 3=4bpp 1/4/6=8bpp 5=4x4 7=direct), bit 13 color0 transparent;
  then u8 w, u8, u8, u8 (redundant, not trusted).
Palette entry (4B): u16 offset<<3, u16 flag (bit0: 4-colour).
"""
import struct

import numpy as np

from .g2d import bgr555_to_rgb

G3D_MAGICS = (b'BTX0', b'BMD0')
# format id (params bits 10-12) -> bits per texel. EMPIRICAL: solved from
# TEX0 data-size fields over 1855 files (Okamiden, Witch's Wish, Londonian):
# 4x4 matched 1855/1855, paletted 1182/1186.
BPP = {2: 2, 3: 4, 1: 8, 4: 8, 6: 8}


def g3d_blocks(data):
    if data[:4] not in G3D_MAGICS + (b'BCA0', b'BTP0', b'BTA0', b'BMA0', b'BVA0'):
        return []
    n = struct.unpack_from('<H', data, 14)[0]
    offs = struct.unpack_from(f'<{n}I', data, 16)
    return [(data[o:o + 4], o, struct.unpack_from('<I', data, o + 4)[0]) for o in offs if o + 8 <= len(data)]


def _dict(t, o):
    cnt = t[o + 1]
    p = o + struct.unpack_from('<H', t, o + 6)[0]     # unknown block size counts from dict start
    unit = struct.unpack_from('<H', t, p)[0]
    p += 4
    ents = [bytes(t[p + i * unit:p + (i + 1) * unit]) for i in range(cnt)]
    p += cnt * unit
    names = [t[p + i * 16:p + i * 16 + 16].split(b'\0')[0].decode('ascii', 'replace') for i in range(cnt)]
    return ents, names


class TEX0:
    def __init__(self, data):
        """`data`: whole BTX0/BMD0 file."""
        self.data = bytes(data)
        base = None
        for m, off, _ in g3d_blocks(data):
            if m == b'TEX0':
                base = off
        if base is None:
            raise ValueError('no TEX0 block')
        t = self.data
        self.base = base
        r = lambda fmt, o: struct.unpack_from(fmt, t, base + o)[0]
        tex_dict, tex_off = r('<H', 0x0E), r('<I', 0x14)
        c_off, ci_off = r('<I', 0x24), r('<I', 0x28)
        pal_size, pal_dict, pal_off = r('<I', 0x30) << 3, r('<I', 0x34), r('<I', 0x38)
        self.tex_data = base + tex_off
        self.cmp_data, self.cmp_idx = base + c_off, base + ci_off
        self.pal_data, self.pal_size = base + pal_off, pal_size
        te, tn = _dict(t, base + tex_dict)
        pe, pn = _dict(t, base + pal_dict) if pal_dict else ([], [])
        self.textures = []
        for e, name in zip(te, tn):
            off, params, w = struct.unpack_from('<HHB', e)
            fmt = params >> 10 & 7
            w = 8 << (params >> 4 & 7)      # verified vs data sizes (Okamiden)
            h = 8 << (params >> 7 & 7)
            self.textures.append({'name': name, 'offset': off << 3, 'params': params,
                                  'format': fmt, 'w': w, 'h': h, 'color0': params >> 13 & 1})
        self.palettes = []
        for e, name in zip(pe, pn):
            off, flag = struct.unpack_from('<HH', e)
            self.palettes.append({'name': name, 'offset': off << 3, 'flag': flag})

    # ---------------------------------------------------------- helpers
    def pal_for(self, i):
        """Palette pairing: '<tex>_pl' name, else same index, else first."""
        tex = self.textures[i]
        for p in self.palettes:
            if p['name'] in (tex['name'] + '_pl', tex['name'][:13] + '_pl', tex['name']):
                return p
        if i < len(self.palettes):
            return self.palettes[i]
        return self.palettes[0] if self.palettes else None

    def colors(self, pal, n):
        o = self.pal_data + pal['offset']
        n = max(0, min(n, (self.pal_data + self.pal_size - o) // 2, (len(self.data) - o) // 2))
        return [bgr555_to_rgb(v) for v in struct.unpack_from(f'<{n}H', self.data, o)]

    def data_span(self, tex):
        """(abs_offset, n_bytes) of the texel data for this texture."""
        fmt, w, h = tex['format'], tex['w'], tex['h']
        if fmt in BPP:
            return self.tex_data + tex['offset'], w * h * BPP[fmt] // 8
        if fmt == 5:
            return self.cmp_data + tex['offset'], (w // 4) * (h // 4) * 4
        if fmt == 7:
            return self.tex_data + tex['offset'], w * h * 2
        return None, 0

    # ---------------------------------------------------------- decode
    def decode(self, i):
        """-> ('P', indices uint8 HxW, palette list) or ('RGBA', array HxWx4)."""
        tex = self.textures[i]
        fmt, w, h = tex['format'], tex['w'], tex['h']
        o, n = self.data_span(tex)
        raw = np.frombuffer(self.data, dtype=np.uint8, count=n, offset=o)
        pal = self.pal_for(i)
        if fmt in BPP:
            bpp = BPP[fmt]
            if bpp == 8:
                idx = raw
            elif bpp == 4:
                idx = np.stack([raw & 0xF, raw >> 4], 1).reshape(-1)
            else:
                idx = np.stack([raw & 3, raw >> 2 & 3, raw >> 4 & 3, raw >> 6], 1).reshape(-1)
            colors = self.colors(pal, 1 << bpp) if pal else []
            return 'P', idx[:w * h].reshape(h, w).astype(np.uint8), colors
        if fmt == 7:
            v = np.frombuffer(self.data, dtype='<u2', count=w * h, offset=o).reshape(h, w)
            rgba = np.zeros((h, w, 4), np.uint8)
            for k, sh in enumerate((0, 5, 10)):
                c = (v >> sh & 31).astype(np.uint8)
                rgba[..., k] = c << 3 | c >> 2
            rgba[..., 3] = np.where(v & 0x8000, 255, 0)
            return 'RGBA', rgba, None
        if fmt == 5:
            return 'RGBA', self._decode_4x4(tex, pal), None
        raise ValueError(f'unknown texture format {fmt}')

    def _decode_4x4(self, tex, pal):
        w, h = tex['w'], tex['h']
        n = (w // 4) * (h // 4)
        o = self.cmp_data + tex['offset']
        pi = self.cmp_idx + tex['offset'] // 2
        pbase = self.pal_data + (pal['offset'] if pal else 0)
        return decode_4x4(self.data[o:o + n * 4], self.data[pi:pi + n * 2], self.data[pbase:], w, h)

    # ---------------------------------------------------------- encode
    def replace_indices(self, i, idx):
        """Paletted formats only: returns new file bytes (same size)."""
        tex = self.textures[i]
        bpp = BPP.get(tex['format'])
        if not bpp:
            raise NotImplementedError('only 2/4/8bpp paletted textures can be reinserted')
        flat = np.asarray(idx, np.uint8).reshape(-1)
        mask = (1 << bpp) - 1
        flat = flat & mask
        if bpp == 8:
            raw = flat.tobytes()
        elif bpp == 4:
            raw = (flat[0::2] | flat[1::2] << 4).astype(np.uint8).tobytes()
        else:
            q = flat.reshape(-1, 4)
            raw = (q[:, 0] | q[:, 1] << 2 | q[:, 2] << 4 | q[:, 3] << 6).astype(np.uint8).tobytes()
        o, n = self.data_span(tex)
        if len(raw) != n:
            raise ValueError('texture size mismatch')
        new = bytearray(self.data)
        new[o:o + n] = raw
        return bytes(new)


def decode_4x4(texel, pidx, palette, w, h, pal_unit=1):
    """DS hardware 4x4 block compression -> RGBA HxWx4. `texel`: u32 per
    4x4 block (2 bits per pixel), `pidx`: u16 per block (mode << 14 |
    palette offset), `palette`: the BGR555 bytes those offsets index. The
    offset counts colours in TEX0, pairs of colours in Cing's EBP
    (pal_unit=2 - found by image smoothness, the only reading that gives a
    clean picture)."""
    bw, bh = w // 4, h // 4
    texel = np.frombuffer(bytes(texel), '<u4', bw * bh)
    pidx = np.frombuffer(bytes(pidx), '<u2', bw * bh)
    d = bytes(palette)
    out = np.zeros((h, w, 4), np.uint8)

    def col(k):
        p = k * 2
        if p + 2 > len(d):
            return (0, 0, 0)
        return bgr555_to_rgb(d[p] | d[p + 1] << 8)
    for b in range(bw * bh):
        pi = int(pidx[b])
        mode, poff = pi >> 14, (pi & 0x3FFF) * pal_unit
        c0, c1 = col(poff), col(poff + 1)
        if mode == 0:
            cs = [c0 + (255,), c1 + (255,), col(poff + 2) + (255,), (0, 0, 0, 0)]
        elif mode == 1:
            cs = [c0 + (255,), c1 + (255,), tuple((a + b_) // 2 for a, b_ in zip(c0, c1)) + (255,), (0, 0, 0, 0)]
        elif mode == 2:
            cs = [c0 + (255,), c1 + (255,), col(poff + 2) + (255,), col(poff + 3) + (255,)]
        else:
            cs = [c0 + (255,), c1 + (255,),
                  tuple((5 * a + 3 * b_) // 8 for a, b_ in zip(c0, c1)) + (255,),
                  tuple((3 * a + 5 * b_) // 8 for a, b_ in zip(c0, c1)) + (255,)]
        cs = np.array(cs, np.uint8)
        tv = int(texel[b])
        codes = np.array([(tv >> (2 * k)) & 3 for k in range(16)]).reshape(4, 4)
        by, bx = divmod(b, bw)
        out[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4] = cs[codes]
    return out

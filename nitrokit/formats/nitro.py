"""Generic Nitro file header + block walker + carver.

Nitro file header (16 bytes, LE):
  0 magic (reversed on disk: 'RLCN' = NCLR)   4 BOM FF FE   6 version u16
  8 file size u32   12 header size u16 (=16)   14 n_blocks u16
then n_blocks blocks: magic(4) size(u32) body...

`carve(data)` finds every VALID Nitro file inside an arbitrary blob (a
publisher bundle, a big archive, a NARC member...), so custom containers
work without reverse-engineering their index. `carve_lz(data)` does the
same for LZ10/LZ11 streams whose decompressed payload starts with a known
magic (partial decompress of just the first bytes, so it is cheap).
"""
import re
import struct

# on-disk magic -> canonical name
G2D = {b'RLCN': 'NCLR', b'RGCN': 'NCGR', b'RCSN': 'NSCR', b'RECN': 'NCER',
       b'RNAN': 'NANR', b'RCMN': 'NMCR', b'RAMN': 'NMAR', b'RTFN': 'NFTR',
       b'RPCN': 'NCPR', b'NCLR': 'NCLR', b'NCGR': 'NCGR', b'NSCR': 'NSCR',
       b'NCER': 'NCER', b'NANR': 'NANR', b'NFTR': 'NFTR'}
G3D = {b'BMD0': 'NSBMD', b'BTX0': 'NSBTX', b'BCA0': 'NSBCA', b'BTP0': 'NSBTP',
       b'BTA0': 'NSBTA', b'BMA0': 'NSBMA', b'BVA0': 'NSBVA'}
OTHER = {b'NARC': 'NARC', b'SDAT': 'SDAT'}
ALL = {**G2D, **G3D, **OTHER}
_RE = re.compile(b'|'.join(re.escape(m) for m in ALL))


def header(data, off=0):
    """Return dict or None if the bytes at `off` are not a sane Nitro header."""
    if off + 16 > len(data):
        return None
    magic = bytes(data[off:off + 4])
    if magic not in ALL:
        return None
    bom, ver, size, hsize, nblk = struct.unpack_from('<HHIHH', data, off + 4)
    if bom not in (0xFEFF, 0xFFFE):
        return None
    if not (16 <= hsize <= 64) or not (1 <= nblk <= 16) or size < hsize + 8:
        return None
    return {'magic': magic, 'kind': ALL[magic], 'version': ver, 'size': size,
            'hsize': hsize, 'n_blocks': nblk, 'bom': bom}


def blocks(data, off=0):
    """[(block_magic, abs_offset, size)] for a Nitro file at `off`."""
    h = header(data, off)
    if not h:
        return []
    out, pos = [], off + h['hsize']
    end = off + min(h['size'], len(data) - off)
    for _ in range(h['n_blocks']):
        if pos + 8 > end:
            break
        m = bytes(data[pos:pos + 4])
        sz = struct.unpack_from('<I', data, pos + 4)[0]
        out.append((m, pos, sz))
        if sz < 8:
            break
        pos += sz
    return out


def kind(data):
    h = header(data)
    return h['kind'] if h else None


def carve(data, align=1, kinds=None, strict_blocks=True):
    """Yield (offset, size, kind) of Nitro files embedded in `data`.
    Nested hits inside an already-carved file are skipped (NARC members are
    handled by the NARC reader, not by the carver)."""
    covered = 0
    for mo in _RE.finditer(data):
        o = mo.start()
        if o < covered or o % align:
            continue
        h = header(data, o)
        if not h or o + h['size'] > len(data) + 3:
            continue
        if kinds and h['kind'] not in kinds:
            continue
        if strict_blocks and h['kind'] in ('NCLR', 'NCGR', 'NSCR', 'NCER', 'NANR'):
            bl = blocks(data, o)
            if not bl or bl[0][2] > h['size']:
                continue
        yield o, min(h['size'], len(data) - o), h['kind']
        if h['kind'] != 'NARC' or True:
            covered = o + h['size']


def _partial_lz(data, off, want):
    """Decompress only the first `want` bytes of an LZ10/LZ11/RLE stream."""
    t = data[off]
    out = bytearray()
    i = off + 4
    n = len(data)
    if t == 0x30:
        try:
            while len(out) < want:
                f = data[i]
                i += 1
                if f & 0x80:
                    out += bytes([data[i]]) * ((f & 0x7F) + 3)
                    i += 1
                else:
                    out += data[i:i + (f & 0x7F) + 1]
                    i += (f & 0x7F) + 1
        except IndexError:
            return None
        return bytes(out[:want])
    try:
        while len(out) < want:
            flags = data[i]
            i += 1
            for bit in range(7, -1, -1):
                if len(out) >= want:
                    break
                if flags >> bit & 1:
                    if t == 0x10:
                        ln = (data[i] >> 4) + 3
                        d = ((data[i] & 0xF) << 8 | data[i + 1]) + 1
                        i += 2
                    else:
                        hi = data[i] >> 4
                        if hi == 0:
                            ln = ((data[i] << 4) | (data[i + 1] >> 4)) + 0x11
                            i += 1
                        elif hi == 1:
                            ln = (((data[i] & 0xF) << 12) | (data[i + 1] << 4) | (data[i + 2] >> 4)) + 0x111
                            i += 2
                        else:
                            ln = hi + 1
                        d = ((data[i] & 0xF) << 8 | data[i + 1]) + 1
                        i += 2
                    s = len(out) - d
                    if s < 0:
                        return None
                    for k in range(ln):
                        out.append(out[s + k])
                else:
                    out.append(data[i])
                    i += 1
            if i > n:
                return None
    except IndexError:
        return None
    return bytes(out[:want])


def carve_lz(data, align=4):
    """Yield (offset, codec, decompressed_size, kind) for LZ10/LZ11/RLE streams in `data`
    whose payload starts with a valid Nitro header. Only candidate offsets
    whose first byte is 0x10/0x11 and whose declared size is sane are tried."""
    n = len(data)
    for o in range(0, n - 8, align):
        t = data[o]
        if t not in (0x10, 0x11, 0x30):
            continue
        size = data[o + 1] | data[o + 2] << 8 | data[o + 3] << 16
        if not 24 <= size <= 16 << 20:
            continue
        # the stream must start with literals (a header can't be a back-ref);
        # for RLE the first chunk must be a raw run long enough for a header
        if t == 0x30:
            if data[o + 4] & 0x80 or (data[o + 4] & 0x7F) < 15:
                continue
        elif data[o + 4] & 0x80:
            continue
        head = _partial_lz(data, o, 16)
        if not head:
            continue
        h = header(head)
        if h and h['size'] <= size <= h['size'] + 4:   # decl. size may include 4-byte alignment
            yield o, {0x10: 'lz10', 0x11: 'lz11', 0x30: 'rle'}[t], size, h['kind']

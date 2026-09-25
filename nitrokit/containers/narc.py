"""NARC (Nitro ARChive). Keeps the original BTNF (names) on repack so a
zero-edit round-trip is byte-identical, and exposes file names if present."""
import struct


def is_narc(data):
    return data[:4] == b'NARC'


def parse(data):
    if not is_narc(data):
        raise ValueError('not NARC')
    hdr_size, n_blocks = struct.unpack_from('<HH', data, 12)
    pos = hdr_size
    fat, btnf, gmif = [], None, None
    for _ in range(n_blocks):
        magic = data[pos:pos + 4]
        size = struct.unpack_from('<I', data, pos + 4)[0]
        if magic == b'BTAF':
            n = struct.unpack_from('<I', data, pos + 8)[0]
            fat = [struct.unpack_from('<II', data, pos + 12 + i * 8) for i in range(n)]
        elif magic == b'BTNF':
            btnf = bytes(data[pos:pos + size])
        elif magic == b'GMIF':
            gmif = pos + 8
        pos += size
    if gmif is None:
        raise ValueError('NARC without GMIF')
    files = [bytes(data[gmif + s:gmif + e]) for s, e in fat]
    names = _names(btnf[8:], len(files)) if btnf else [None] * len(files)
    return {'files': files, 'names': names, 'btnf': btnf, 'fat': fat}


def _names(fnt, n):
    names = [None] * n
    if len(fnt) < 8:
        return names

    def walk(dir_id, path):
        e = (dir_id & 0xFFF) * 8
        if e + 6 > len(fnt):
            return
        sub, first = struct.unpack_from('<IH', fnt, e)
        p, fid = sub, first
        while p < len(fnt):
            t = fnt[p]
            p += 1
            if t == 0:
                break
            ln = t & 0x7F
            nm = fnt[p:p + ln].decode('cp932', 'replace')
            p += ln
            if t & 0x80:
                sd = struct.unpack_from('<H', fnt, p)[0]
                p += 2
                walk(sd, f'{path}{nm}/')
            else:
                if fid < n:
                    names[fid] = path + nm
                fid += 1
    try:
        walk(0xF000, '')
    except struct.error:
        pass
    return names


def unpack(data):
    return parse(data)['files']


def pack(files, original=None):
    """Rebuild. If `original` (the source NARC bytes) is given, its BTNF and
    alignment padding byte are reused."""
    btnf, pad_byte = None, 0xFF
    if original is not None:
        o = parse(original)
        btnf = o['btnf']
        if len(o['fat']) > 1:
            gap = o['fat'][1][0] - o['fat'][0][1]
            if gap > 0:
                gstart = original.find(b'GMIF') + 8 + o['fat'][0][1]
                pad_byte = original[gstart]
    body, fat = bytearray(), bytearray()
    for f in files:
        start = len(body)
        body += f
        fat += struct.pack('<II', start, start + len(f))
        body += bytes([pad_byte]) * ((-len(body)) % 4)
    if btnf is None:
        b = struct.pack('<IHH', 4, 0, 1)
        btnf = b'BTNF' + struct.pack('<I', 8 + len(b)) + b
    btaf = b'BTAF' + struct.pack('<II', 12 + len(fat), len(files)) + fat
    gmif = b'GMIF' + struct.pack('<I', 8 + len(body)) + body
    total = 16 + len(btaf) + len(btnf) + len(gmif)
    return b'NARC\xfe\xff\x00\x01' + struct.pack('<IHH', total, 16, 3) + btaf + btnf + gmif

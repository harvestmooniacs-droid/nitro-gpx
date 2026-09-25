"""Mini NitroFS: a file system image with the same FNT/FAT tables as the
cartridge itself, behind a 16-byte header:

  u32 fnt_off, u32 fnt_size, u32 fat_off, u32 fat_size
  FNT  (directory table rooted at 0xF000, same encoding as the ROM's)
  FAT  (start, end) u32 pairs, absolute offsets into this file
  data (4-byte aligned, 0xFF padded)

Every Nintendo Wi-Fi Connection game ships one as `dwc/utility.bin` (the
WFC setup screens, with their own fonts and 2D graphics), and some games
reuse the layout for their own archives. Layout and write-back rules
follow Tinke's Pack plugin (Utility.cs). Because the FAT is rebuilt on
pack(), members may grow - which the old carve-only handling of this file
could not do (Rune Factory 3's WFC font).
"""
import struct


def _walk(d, fnt_off, fnt_end, dir_id, path, out, depth=0):
    if depth > 32:
        raise ValueError('FNT loop')
    e = fnt_off + (dir_id & 0xFFF) * 8
    if e + 8 > fnt_end:
        raise ValueError('FNT dir entry out of range')
    sub_off, fid = struct.unpack_from('<IH', d, e)
    p = fnt_off + sub_off
    while True:
        if p >= fnt_end:
            raise ValueError('FNT subtable out of range')
        t = d[p]
        p += 1
        if t == 0:
            return
        ln = t & 0x7F
        if not ln or p + ln > fnt_end:
            raise ValueError('bad FNT name')
        name = d[p:p + ln].decode('cp932', 'replace')
        p += ln
        rel = f'{path}/{name}' if path else name
        if t & 0x80:
            sub = struct.unpack_from('<H', d, p)[0]
            p += 2
            if sub & 0xF000 != 0xF000:
                raise ValueError('bad FNT subdirectory id')
            _walk(d, fnt_off, fnt_end, sub, rel, out, depth + 1)
        else:
            out[fid] = rel
            fid += 1


def parse(data):
    """-> [(name, offset, size)] in FAT order, or None if `data` is not a
    mini NitroFS. Strict: every table must fit and every FAT entry must be
    named by the FNT, so random data never passes."""
    if len(data) < 0x20:
        return None
    fnt_off, fnt_size, fat_off, fat_size = struct.unpack_from('<4I', data, 0)
    n = len(data)
    if (fnt_off < 0x10 or fat_off < 0x10 or fat_size % 8 or not fat_size or not fnt_size
            or fnt_off + fnt_size > n or fat_off + fat_size > n):
        return None
    try:
        names = {}
        _walk(data, fnt_off, fnt_off + fnt_size, 0xF000, '', names)
    except (ValueError, struct.error, IndexError):
        return None
    count = fat_size // 8
    if len(names) != count or set(names) != set(range(count)):
        return None
    tables_end = max(fnt_off + fnt_size, fat_off + fat_size)
    out = []
    for i in range(count):
        s, e = struct.unpack_from('<II', data, fat_off + i * 8)
        if not tables_end <= s <= e <= n:
            return None
        out.append((names[i], s, e - s))
    return out


def read(data, i):
    _, off, size = parse(data)[i]
    return data[off:off + size]


def pack(base, changes):
    """`changes`: {index: bytes}. Keeps header + FNT + everything before the
    first file byte-exact, re-lays the files in FAT order (4-byte aligned,
    0xFF padding - Tinke's rule) and rewrites the FAT."""
    entries = parse(base)
    if entries is None:
        raise ValueError('not a mini NitroFS')
    fat_off = struct.unpack_from('<I', base, 8)[0]
    start = min(off for _, off, _ in entries)
    head = bytearray(base[:start])
    body = bytearray()
    for i, (_, off, size) in enumerate(entries):
        blob = changes.get(i, base[off:off + size])
        pos = start + len(body)
        struct.pack_into('<II', head, fat_off + i * 8, pos, pos + len(blob))
        body += blob
        body += b'\xff' * ((-len(body)) % 4)
    return bytes(head + body)

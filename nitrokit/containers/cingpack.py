"""Cing `.pack` (Last Window: 272 files, 114 MB - almost every asset).
Checked on all 272: the block sizes chain exactly to the end of the file.

  0x00 u32 0
  0x04 u32 BE entry_count
  0x08 u32 BE offset of the LAST size field (the data starts 4 bytes later)
  0x0C u32 BE work-buffer size (>= what the entries need; not an exact sum)
  0x10 entry_count x (u8 name_len, name, u32 BE block_size)
  then the blocks back to back, in entry order. A block is either
    u32 LE decoded_size + a zlib stream       (most: images, scripts, text)
    raw bytes                                  (.bra, .mtc, .wpf)
  - told apart by the zlib stream actually decoding to decoded_size.

An entry may grow on insert: sizes live in the table, bodies are
contiguous, the whole pack is rewritten. The work-buffer field grows by
the same amount (rounded to 0x200) so the game never under-allocates.
"""
import struct
import zlib


def parse(data):
    """[{name, offset, size, zlib, dsize}] or None if not a pack."""
    if len(data) < 32 or data[:4] != b'\0\0\0\0':
        return None
    n, names_end = struct.unpack_from('>II', data, 4)
    if not 0 < n <= 20000 or not 16 < names_end < len(data):
        return None
    q = 16
    ents = []
    for _ in range(n):
        if q >= names_end:
            return None
        ln = data[q]
        name = data[q + 1:q + 1 + ln]
        if not ln or not all(32 <= c < 127 for c in name):
            return None
        size = struct.unpack_from('>I', data, q + 1 + ln)[0]
        ents.append([name.decode('ascii'), size])
        q += 5 + ln
    if q - 4 != names_end:
        return None
    off = names_end + 4
    out = []
    for name, size in ents:
        out.append({'name': name, 'offset': off, 'size': size, 'zlib': False, 'dsize': size})
        off += size
    if off != len(data):
        return None
    for e in out:
        blk = data[e['offset']:e['offset'] + e['size']]
        if len(blk) > 6 and blk[4] == 0x78:
            try:
                z = zlib.decompressobj()
                body = z.decompress(blk[4:])
            except zlib.error:
                continue
            if len(body) == struct.unpack_from('<I', blk, 0)[0] and not z.unused_data.strip(b'\0'):
                e['zlib'], e['dsize'] = True, len(body)
    return out


def read(data, e):
    blk = data[e['offset']:e['offset'] + e['size']]
    return zlib.decompress(blk[4:]) if e['zlib'] else bytes(blk)


def pack(original, replacements):
    ents = parse(original)
    if ents is None:
        raise ValueError('not a Cing pack')
    work = struct.unpack_from('>I', original, 12)[0]
    blocks = []
    for i, e in enumerate(ents):
        if i not in replacements:
            blocks.append(original[e['offset']:e['offset'] + e['size']])
            continue
        v = bytes(replacements[i])
        if e['zlib']:
            blocks.append(struct.pack('<I', len(v)) + zlib.compress(v, 9))
        else:
            blocks.append(v)
        grow = len(v) - e['dsize']
        if grow > 0:
            work += (grow + 0x1FF) & ~0x1FF
    head = bytearray(original[:16])
    struct.pack_into('>I', head, 12, work)
    for e, blk in zip(ents, blocks):
        nm = e['name'].encode('ascii')
        head += bytes([len(nm)]) + nm + struct.pack('>I', len(blk))
    struct.pack_into('>I', head, 8, len(head) - 4)
    return bytes(head) + b''.join(blocks)

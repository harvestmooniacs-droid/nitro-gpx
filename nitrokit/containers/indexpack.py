"""Index-table packs, one file per Nitro type sharing ONE index space
(Theresia: data/ncgpack.dat, nclpack.dat, nscpack.dat, ncepack.dat,
nanpack.dat - entry #i of every pack is part of resource #i).

  0x00 u32 0, u32 0
  0x08 n x (u32 flags, u32 offset, u32 size)      size 0 = empty slot
  table padded to a 32-byte boundary, then the bodies (Nitro files, some
  LZ10/Huffman wrapped). Checked on all 5 packs: every live span is inside
  the file and the padding after the table is < 32 bytes.

A member that grows past its slot is appended at the end of the pack and
its entry repointed; nothing else moves.
"""
import re
import struct

TYPE_TOKEN = re.compile(r'(ncgr|nclr|nscr|ncer|nanr|ncg|ncl|nsc|nce|nan)', re.I)


def parse(data):
    """[(flags, offset, size)] or None."""
    if len(data) < 40 or data[:8] != bytes(8):
        return None
    first = len(data)
    ents = []
    i = 8
    while i + 12 <= first:
        flags, off, size = struct.unpack_from('<III', data, i)
        if size:
            if off < 20 or off + size > len(data):
                return None
            first = min(first, off)
        ents.append((flags, off, size))
        i += 12
    n = (first - 8) // 12
    if n <= 0 or first - (8 + 12 * n) >= 32 or not any(s for _, _, s in ents[:n]):
        return None
    return ents[:n]


def group_name(path):
    """'data/ncgpack.dat' -> 'data/pack': the part every sibling pack shares."""
    head, _, name = path.rpartition('/')
    stem = name.rsplit('.', 1)[0]
    shared = TYPE_TOKEN.sub('', stem, count=1) or 'pack'
    return f'{head}/{shared}' if head else shared


def read(data, index):
    _, off, size = parse(data)[index]
    return data[off:off + size]


def pack(original, replacements):
    ents = parse(original)
    out = bytearray(original)
    for i, body in sorted(replacements.items()):
        flags, off, size = ents[i]
        body = bytes(body)
        if len(body) <= size:
            out[off:off + size] = body + bytes(size - len(body))
            new_off = off
        else:
            out += bytes((-len(out)) % 4)
            new_off = len(out)
            out += body
        struct.pack_into('<III', out, 8 + 12 * i, flags, new_off, len(body))
    return bytes(out)

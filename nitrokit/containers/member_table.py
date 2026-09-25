"""Offset-table archive with an IMPLICIT count (Touhai Densetsu Akagi DS
`bg.b`/`chr.b`/`obj.b`/`adt.b`/`bmp.b`/`mes.b`): no count field at all -
the table is `count` absolute u32 offsets, and `count` is simply inferred
from the first offset itself (`count = table[0] // 4`, since the table is
exactly `count * 4` bytes and the first entry starts right after it).
Entries run to the next offset, the last to EOF. Different from
`offset_archive` (Magical Starsign), which has an explicit count field
followed by count+1 offsets ending in the file length.

Each entry is itself framed by `compression.aklz`'s leading flag+size word
(compressed or stored raw) - decompressing is just the normal
`compression.unwrap` cascade, nothing container-specific.

Detection is strict since there's no magic: the inferred table must fit,
every offset must be non-decreasing and land past the table, and this is
only tried on whole files (depth 0) that aren't already something else.
"""
import struct

MAX_COUNT = 1 << 16


def parse(data):
    """[(offset, size)] or None."""
    if len(data) < 8:
        return None
    first = struct.unpack_from('<I', data, 0)[0]
    if first % 4 or not 4 <= first <= len(data):
        return None
    n = first // 4
    if not 1 <= n <= MAX_COUNT:
        return None
    offs = struct.unpack_from(f'<{n}I', data, 0)
    if offs[0] != first or any(b < a for a, b in zip(offs, offs[1:])) or offs[-1] > len(data):
        return None
    ends = list(offs[1:]) + [len(data)]
    return list(zip(offs, (e - o for o, e in zip(offs, ends))))


def pack(original, replacements):
    entries = parse(original)
    if entries is None:
        raise ValueError('not a member_table archive')
    n = len(entries)
    bodies = [bytes(replacements[i]) if i in replacements else original[o:o + s]
              for i, (o, s) in enumerate(entries)]
    table = bytearray()
    data = bytearray()
    pos = n * 4
    for b in bodies:
        table += struct.pack('<I', pos)
        data += b
        pos += len(b)
    return bytes(table) + bytes(data)

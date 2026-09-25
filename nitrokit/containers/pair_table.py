"""Magic-less archive: u32 count, then count x (u32 offset, u32 size),
offsets absolute and in order, members after the table (Level-5's
Inazuma Eleven 2D/3D blobs inside .pkb; a very common hand-rolled layout).

Detection is strict because there is no magic: every entry must start
after the table, not overlap the previous one, stay inside the data, and
the gaps between entries (alignment padding) must be small.
"""
import struct

MAX_COUNT = 4096
MAX_GAP = 32


def parse(data):
    """[(offset, size)] or None."""
    if len(data) < 12:
        return None
    n = struct.unpack_from('<I', data, 0)[0]
    table_end = 4 + 8 * n
    if not 1 <= n <= MAX_COUNT or table_end > len(data):
        return None
    ents = list(struct.iter_unpack('<II', data[4:table_end]))
    last = table_end
    for off, size in ents:
        if off < last or off - last > MAX_GAP or off + size > len(data):
            return None
        last = off + size
    if len(data) - last > MAX_GAP:
        return None
    return ents


def read(data, i):
    off, size = parse(data)[i]
    return data[off:off + size]


def pack(original, changes):
    """`changes`: {index: bytes}. Members re-laid in order, 4-byte aligned,
    table rewritten; a member may grow or shrink."""
    ents = parse(original)
    if ents is None:
        raise ValueError('not a pair-table archive')
    table_end = 4 + 8 * len(ents)
    head = bytearray(original[:table_end])
    body = bytearray(original[table_end:ents[0][0]])      # keep original leading padding
    for i, (off, size) in enumerate(ents):
        blob = changes.get(i, original[off:off + size])
        pos = table_end + len(body)
        struct.pack_into('<II', head, 4 + 8 * i, pos, len(blob))
        body += blob
        if i + 1 < len(ents):
            body += bytes((-len(body)) % 4)
    tail_start = ents[-1][0] + ents[-1][1]
    return bytes(head + body + original[tail_start:])

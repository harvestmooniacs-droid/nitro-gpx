"""Offset-table archive with no magic (Magical Starsign `hiraishi/*.dat`,
Brownie Brown): u32 count, then count+1 u32 absolute offsets. The last one
is the file length; an empty slot stores 0. An entry runs to the next
non-zero offset. Entries are usually LZ10 streams of headerless data.

Detection is strict because there is no magic: every non-zero offset must
lie after the table and increase, and the final offset must equal the file
length exactly.
"""
import struct

MAX_COUNT = 1 << 16


def parse(data):
    """[(offset, size)] with (0, 0) for empty slots, or None."""
    if len(data) < 12:
        return None
    n = struct.unpack_from('<I', data, 0)[0]
    table_end = 4 + (n + 1) * 4
    if not 1 <= n <= MAX_COUNT or table_end > len(data):
        return None
    offs = struct.unpack_from(f'<{n + 1}I', data, 4)
    if offs[-1] != len(data):
        return None
    live = [o for o in offs[:-1] if o]
    if not live or live[0] < table_end or any(b < a for a, b in zip(live, live[1:])):
        return None
    out, nxt = [], len(data)
    for o in reversed(offs[:-1]):
        if o:
            out.append((o, nxt - o))       # may be 0 bytes: kept as a live slot
            nxt = o
        else:
            out.append((0, 0))
    out.reverse()
    return out


def pack(original, replacements):
    """Rebuild with {index: bytes}; empty slots stay 0, offsets recomputed."""
    entries = parse(original)
    n = len(entries)
    body = bytearray()
    offs = []
    base = 4 + (n + 1) * 4
    for i, (o, s) in enumerate(entries):
        if o == 0 and i not in replacements:
            offs.append(0)
            continue
        blob = replacements.get(i, original[o:o + s])
        offs.append(base + len(body))
        body += blob
    offs.append(base + len(body))
    return struct.pack(f'<I{n + 1}I', n, *offs) + bytes(body)

"""Generic detection of an (offset[, size]) table in an unknown container.

Most publisher archives are "header with a u32 table + data": Suikoden
Tierkreis .bin (offset, packed size, raw size), Ni no Kuni NPCK
(offset, size), Luminous Arc .iear 'JTBL' (offset-0x10, size), Harvest
Moon .xbb (offset, size, ?, hash)... The carver already knows where every
item starts and how long it is, so the table can be *found* instead of
reverse-engineered: look in the header region (before the first item) for
u32 values equal to item_start - base, with a u32 equal to the item length
next to it.

With the table known, an item can GROW: bytes after it are shifted, every
offset field pointing past it is increased, its size field rewritten, and
a field equal to the container length (if any) updated.
"""
import collections
import struct

SIZE_NEIGHBOURS = (4, 8, -4)


def detect(blob, spans, min_cover=0.8):
    """spans: [(start, length)] of carved items. Returns table dict or None."""
    if len(spans) < 2:
        return None
    first = min(s for s, _ in spans)
    hdr_end = min(first, len(blob)) & ~3
    if hdr_end < 8:
        return None
    n = hdr_end // 4
    vals = struct.unpack_from(f'<{n}I', blob, 0)
    where = collections.defaultdict(list)
    for i, v in enumerate(vals):
        where[v].append(i * 4)
    # candidate bases: deltas between the first item start and header values
    # (base may be 0, a small header delta, or the start of the data area:
    # Nostalgia's SSAM stores offsets relative to the end of its name table)
    base_votes = collections.Counter()
    uniq = set(vals)
    for s, _ in spans[:64]:
        for v in uniq:
            d = s - v
            if 0 <= d <= hdr_end + 0x1000:
                base_votes[d] += 1
    best = None
    for base, _ in base_votes.most_common(6):
        fields, sizes = {}, {}
        for s, ln in spans:
            pos = where.get(s - base)
            if not pos:
                continue
            p = pos[0]
            fields[s] = p
            for dn in SIZE_NEIGHBOURS:
                q = p + dn
                if 0 <= q < hdr_end:
                    sv = vals[q // 4]
                    if sv in (ln, (ln + 3) & ~3):
                        sizes[s] = (q, sv == ln)
                        break
        cover = len(fields) / len(spans)
        if cover >= min_cover and (best is None or cover > best['cover']):
            best = {'base': base, 'cover': cover, 'offsets': fields, 'sizes': sizes}
    if not best:
        return None
    # every u32 in the header that looks like an offset into the data area
    # (monotonic with the matched ones) is shifted too - including offsets of
    # non-graphic entries the carver never saw
    matched = sorted(best['offsets'].values())
    stride = collections.Counter(b - a for a, b in zip(matched, matched[1:])).most_common(1)[0][0] if len(matched) > 1 else 4
    lo, hi = matched[0], matched[-1]
    all_offsets = []
    for p in range(lo % stride if stride else lo, hdr_end, stride or 4):
        v = vals[p // 4] + best['base']
        if first <= v <= len(blob) and (lo - 4 * stride <= p <= hi + 64 * stride):
            all_offsets.append(p)
    total_fields = [i * 4 for i, v in enumerate(vals) if v == len(blob)]
    return {'base': best['base'], 'stride': stride, 'offset_fields': sorted(set(all_offsets) | set(matched)),
            'size_fields': {str(s): list(v) for s, v in best['sizes'].items()},
            'total_fields': total_fields, 'cover': round(best['cover'], 3), 'hdr_end': hdr_end}


def grow(blob, table, start, old_len, new_data, align=4):
    """Replace the item at `start` (old_len bytes) with new_data, shifting
    the rest of the container by a multiple of `align` (so every later item
    keeps its alignment - items are NOT always aligned themselves, e.g.
    Lufia's mcd.dat). Returns (new blob, delta)."""
    delta = (len(new_data) - old_len + align - 1) & -align
    slot = old_len + delta
    out = bytearray(blob[:start]) + new_data + bytes(slot - len(new_data)) + bytearray(blob[start + old_len:])
    base = table['base']
    for p in table['offset_fields']:
        v = struct.unpack_from('<I', out, p)[0]
        if v + base > start:
            struct.pack_into('<I', out, p, v + delta)
    sf = table['size_fields'].get(str(start))
    if sf:
        q, exact = sf
        struct.pack_into('<I', out, q, len(new_data) if exact else (len(new_data) + 3) & ~3)
    for p in table['total_fields']:
        struct.pack_into('<I', out, p, len(out))
    return bytes(out), delta



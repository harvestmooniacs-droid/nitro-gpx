"""Backward LZ ("LZOvl"/BLZ): how the Nintendo SDK compresses overlays and
the ARM9 image. The stream is read from the END of the file:

  ... uncompressed prefix | backwards LZ data | pad | u24 enc_len, u8 hdr_len, u32 inc_len

inc_len = how much the file grows when decompressed (0 = not compressed);
enc_len = bytes at the end covered by compression (header included);
hdr_len = footer size (8 + 0xFF padding). LZ10-like tokens, flags MSB
first, match = u16 BE (length-3 << 12 | disp-3), copied backwards.
Same algorithm as dsdecmp's LZOvl and CUE's BLZ; validated against the
overlay table (the decompressed size must equal the overlay's RAM size).
"""
import struct

MAX_MATCH, MAX_DIST, MIN_DIST, MAX_CHAIN = 18, 4098, 3, 64


def footer(data):
    """-> (enc_len, hdr_len, inc_len) or None if not BLZ-shaped."""
    if len(data) < 8:
        return None
    inc_len = struct.unpack_from('<I', data, len(data) - 4)[0]
    hdr_len = data[-5]
    enc_len = int.from_bytes(data[-8:-5], 'little')
    if not inc_len or not 8 <= hdr_len <= 0x0B or not hdr_len <= enc_len <= len(data):
        return None
    if any(b != 0xFF for b in data[-hdr_len:-8]):
        return None
    return enc_len, hdr_len, inc_len


def decompress(data, arm9_tail=0):
    """`arm9_tail`: the ARM9 image keeps 12 extra bytes after the footer
    (module params pointer) - pass 12 to ignore them."""
    data = bytes(data)
    tail = data[len(data) - arm9_tail:] if arm9_tail else b''
    body = data[:len(data) - arm9_tail] if arm9_tail else data
    f = footer(body)
    if f is None:
        raise ValueError('not a BLZ stream')
    enc_len, hdr_len, inc_len = f
    dec_len = len(body) - enc_len               # untouched prefix
    pak_end, pak = dec_len, dec_len + enc_len - hdr_len
    raw_len = dec_len + enc_len + inc_len
    raw = bytearray(raw_len)
    raw[:dec_len] = body[:dec_len]
    out, raw_end = raw_len, dec_len
    mask = flags = 0
    while out > raw_end:
        mask >>= 1
        if not mask:
            if pak == pak_end:
                break
            pak -= 1
            flags, mask = body[pak], 0x80
        if flags & mask:
            if pak - 2 < pak_end:
                break
            pos = body[pak - 1] << 8 | body[pak - 2]
            pak -= 2
            length = min((pos >> 12) + 3, out - raw_end)
            disp = (pos & 0xFFF) + 3
            if out + disp > raw_len:
                raise ValueError('BLZ back-reference out of range')
            for _ in range(length):
                out -= 1
                raw[out] = raw[out + disp]
        else:
            if pak == pak_end:
                break
            pak -= 1
            out -= 1
            raw[out] = body[pak]
    if out != raw_end:
        raise ValueError('BLZ stream truncated')
    return bytes(raw) + tail


def _find_matches(rev):
    """Greedy hash-chain match search over `rev` (forward LZ77, min distance
    MIN_DIST since BLZ encodes disp-3 in 12 bits - unlike LZ10, distance 1
    or 2 is not representable). Yields ('lit', byte) or ('match', length, dist)
    in processing order."""
    n = len(rev)
    heads, prev = {}, [-1] * n
    i = 0
    while i < n:
        best_len = best_dist = 0
        if i + 3 <= n:
            key = rev[i] | rev[i + 1] << 8 | rev[i + 2] << 16
            j = heads.get(key, -1)
            limit, chain = i - MAX_DIST, 0
            cap = min(MAX_MATCH, n - i)
            while j >= 0 and j >= limit and chain < MAX_CHAIN:
                if i - j >= MIN_DIST and rev[j + best_len] == rev[i + best_len] if best_len < cap else False:
                    ln = 0
                    while ln < cap and rev[j + ln] == rev[i + ln]:
                        ln += 1
                    if ln > best_len and i - j >= MIN_DIST:
                        best_len, best_dist = ln, i - j
                        if ln == cap:
                            break
                j = prev[j]
                chain += 1
        step = best_len if best_len >= 3 else 1
        for k in range(i, min(i + step, n - 2)):
            h = rev[k] | rev[k + 1] << 8 | rev[k + 2] << 16
            prev[k] = heads.get(h, -1)
            heads[h] = k
        if best_len >= 3:
            yield 'match', best_len, best_dist
        else:
            yield 'lit', rev[i]
        i += step


def compress(data, dec_len=0):
    """BLZ-compress data[dec_len:], keeping data[:dec_len] as a literal
    prefix untouched. Raises ValueError if the result would not actually
    shrink the file (inc_len<=0) - BLZ's footer cannot represent that."""
    tail = data[dec_len:]
    rev = tail[::-1]
    groups, group = [], []
    for unit in _find_matches(rev):
        group.append(unit)
        if len(group) == 8:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    # file (ascending address) order = groups in REVERSE emission order;
    # within each group, units in reverse order (byte-atomic), flag last
    # (see nitrokit docs/SKILL_ADDITIONS.md - derived and verified against the
    # decompressor above, byte address by byte address, not guessed)
    out = bytearray()
    for group in reversed(groups):
        flag = 0
        pieces = []
        for bit, unit in enumerate(group):
            if unit[0] == 'match':
                _, length, dist = unit
                flag |= 1 << (7 - bit)
                pos = (length - 3) << 12 | (dist - 3)
                pieces.append(bytes([pos & 0xFF, pos >> 8]))
            else:
                pieces.append(bytes([unit[1]]))
        for piece in reversed(pieces):
            out += piece
        out.append(flag)
    T = len(out)
    hdr_len = 8
    enc_len = T + hdr_len
    inc_len = len(tail) - enc_len
    if inc_len <= 0:
        raise ValueError(f'BLZ: compressed size ({enc_len}) does not beat the original '
                         f'({len(tail)}) - nothing to gain from BLZ here')
    footer = struct.pack('<I', enc_len)[:3] + bytes([hdr_len]) + struct.pack('<I', inc_len)
    return data[:dec_len] + bytes(out) + footer

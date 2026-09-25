"""In-house LZSS variant used by Touhai Densetsu Akagi DS (`bg.b`, `chr.b`,
`obj.b`, `adt.b`, `bmp.b`, `mes.b` - see containers.member_table for the
archive format these live in). Not a public/documented codec; recovered by
emulating the game's own decompression routine (ARM9 `0x02003918`) with
Unicorn against real compressed members and matching a hand-derived Python
decoder against that emulated ground truth byte-for-byte (321/321 real
compressed members across 3 files, zero mismatches) - the same standard of
proof as this project's other from-scratch codecs (LZE, bitlz).

Container framing (not part of the codec's own bitstream): each archive
member starts with `u32 flagged_size` - bit 31 set means "this member is
aklz-compressed" and bits 0-30 repeat the codec's own decompressed size
(redundant, checked); bit 31 clear means the member is stored raw and the
whole word is its byte length. `is_aklz`/`decompress` below operate on the
member INCLUDING that leading word - decompress() skips it internally.

Bitstream (after the leading u32): u32 LE decompressed size, then flag
bytes (MSB-first, bit=1 -> back-reference) each covering up to 8 tokens:
  bit=0        : literal byte
  bit=1, then a token byte T:
    T & 0x80 == 0 : SHORT copy - always exactly 2 bytes, one at a time,
                    each from `out[-1 - T]` (T: 0-127, distance 1-128) -
                    NOT a fixed-length memcpy: the second byte is read
                    after the first is appended, so a distance of 1 (T=0)
                    correctly repeats the byte just written twice
    T & 0x80 != 0 : LONG copy - one more byte U follows; length = ((T &
                    0x7F) >> 4) + 1, distance = (T & 0xF) << 8 | U, copied
                    from `out[-1 - distance]` one byte at a time (so an
                    overlapping copy, distance < length, repeats correctly)
"""
import struct


def header(data):
    if len(data) < 4:
        return None, None
    w = struct.unpack_from('<I', data, 0)[0]
    return bool(w & 0x80000000), w & 0x7FFFFFFF


def is_aklz(data):
    compressed, size = header(data)
    if not compressed or len(data) < 8 or size == 0:
        return False
    return struct.unpack_from('<I', data, 4)[0] == size


def decompress(data, consumed=False):
    """`data` includes the leading flag+size word (see module docstring)."""
    size = struct.unpack_from('<I', data, 4)[0]
    src = data[4:]
    out = bytearray()
    i = 4  # skip the codec's own (redundant) size field
    mask = 0
    flags = 0
    try:
        while len(out) < size:
            if mask == 0:
                flags = src[i]
                i += 1
                mask = 0x80
            if not flags & mask:
                out.append(src[i])
                i += 1
            else:
                t = src[i]
                i += 1
                if not t & 0x80:
                    p = len(out) - 1 - t
                    if p < 0:
                        raise ValueError('aklz: bad short back-reference')
                    for _ in range(2):
                        if len(out) >= size:
                            break
                        out.append(out[p])
                        p += 1
                else:
                    u = src[i]
                    i += 1
                    length = ((t & 0x7F) >> 4) + 1
                    dist = (t & 0xF) << 8 | u
                    p = len(out) - 1 - dist
                    if p < 0:
                        raise ValueError('aklz: bad long back-reference')
                    for _ in range(length):
                        if len(out) >= size:
                            break
                        out.append(out[p])
                        p += 1
            mask >>= 1
    except IndexError:
        raise ValueError('aklz: stream truncated')
    return (bytes(out), 4 + i) if consumed else bytes(out)


def _find_matches(data):
    """Greedy hash-chain match finder. Two encodable shapes: SHORT (exactly
    2 bytes, distance 1-128 - cheaper than 2 literals by 1 flag bit + 1
    byte) and LONG (2-8 bytes, distance 1-4096)."""
    n = len(data)
    heads, prev = {}, [-1] * n
    i = 0
    while i < n:
        best_len, best_dist = 0, 0
        if i + 2 <= n:
            key = data[i] | data[i + 1] << 8
            j = heads.get(key, -1)
            chain = 0
            limit = i - 4096
            cap = min(8, n - i)
            while j >= 0 and j >= limit and chain < 64:
                if data[j] == data[i]:
                    ln = 0
                    while ln < cap and data[j + ln] == data[i + ln]:
                        ln += 1
                    if ln > best_len:
                        best_len, best_dist = ln, i - j
                        if ln == cap:
                            break
                j = prev[j]
                chain += 1
        if best_len == 2 and best_dist > 128:
            best_len = 0  # a 2-byte match only pays off within SHORT's reach
        step = best_len if best_len >= 2 else 0
        if not step:
            yield i, 0, 0, False
            step = 1
        else:
            yield i, step, best_dist, step == 2 and best_dist <= 128
        for k in range(i, min(i + step, n - 1)):
            key = data[k] | data[k + 1] << 8
            prev[k] = heads.get(key, -1)
            heads[key] = k
        i += step


def compress(data):
    """Returns the full member bytes (leading flag+size word included)."""
    n = len(data)
    out = bytearray(struct.pack('<I', n))
    flag_pos, bit = None, -1
    for i, ln, dist, short in _find_matches(data):
        if bit < 0:
            flag_pos = len(out)
            out.append(0)
            bit = 7
        if ln == 0:
            out.append(data[i])
        elif short:
            out[flag_pos] |= 1 << bit
            out.append(dist - 1)
        else:
            out[flag_pos] |= 1 << bit
            d = dist - 1
            t = 0x80 | (ln - 1) << 4 | (d >> 8)
            out += bytes([t, d & 0xFF])
        bit -= 1
    body = bytes(out)
    return struct.pack('<I', 0x80000000 | n) + body

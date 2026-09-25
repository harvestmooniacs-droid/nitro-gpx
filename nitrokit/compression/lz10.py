"""Nitro LZ77 type 0x10 (BIOS LZ77UnComp). Hash-chain compressor.

Header: 0x10, u24 decompressed size (if 0, u32 size follows - rare).
Blocks: flag byte MSB-first, bit=1 -> 2-byte ref (len-3:4 | disp-1:12).
"""
MIN_MATCH, MAX_MATCH, MAX_DIST, MAX_CHAIN = 3, 18, 4096, 64


def _header(data, magic):
    if len(data) < 4 or data[0] != magic:
        raise ValueError(f'not type 0x{magic:02X}')
    size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if size == 0 and len(data) >= 8:
        size = int.from_bytes(data[4:8], 'little')
        pos = 8
    return size, pos


def decompress(data, strict=True):
    size, i = _header(data, 0x10)
    out = bytearray()
    n = len(data)
    while len(out) < size:
        if i >= n:
            if strict:
                raise ValueError('LZ10 stream truncated')
            break
        flags = data[i]
        i += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if flags >> bit & 1:
                if i + 1 >= n:
                    raise ValueError('LZ10 stream truncated')
                b0, b1 = data[i], data[i + 1]
                i += 2
                length = (b0 >> 4) + 3
                start = len(out) - (((b0 & 0xF) << 8 | b1) + 1)
                if start < 0:
                    raise ValueError('LZ10 bad back-reference')
                for k in range(length):
                    out.append(out[start + k])
            else:
                if i >= n:
                    raise ValueError('LZ10 stream truncated')
                out.append(data[i])
                i += 1
    return bytes(out[:size])


def compressed_length(data):
    """Bytes actually consumed by the stream (to find trailing padding)."""
    size, i = _header(data, 0x10)
    produced = 0
    while produced < size:
        flags = data[i]
        i += 1
        for bit in range(7, -1, -1):
            if produced >= size:
                break
            if flags >> bit & 1:
                produced += (data[i] >> 4) + 3
                i += 2
            else:
                produced += 1
                i += 1
    return i


def _find_matches(data, max_match, max_dist):
    """Generator helper shared by LZ10/LZ11: yields (i, best_len, best_dist)
    decisions greedily using hash chains."""
    n = len(data)
    heads, prev = {}, [-1] * n

    def key(p):
        return data[p] | data[p + 1] << 8 | data[p + 2] << 16

    i = 0
    while i < n:
        best_len = best_dist = 0
        if i + 3 <= n:
            cap = min(max_match, n - i)
            j = heads.get(key(i), -1)
            limit, chain = i - max_dist, 0
            while j >= 0 and j >= limit and chain < MAX_CHAIN:
                if data[j + best_len] == data[i + best_len] if best_len < cap else False:
                    ln = 0
                    while ln < cap and data[j + ln] == data[i + ln]:
                        ln += 1
                    if ln > best_len:
                        best_len, best_dist = ln, i - j
                        if ln == cap:
                            break
                j = prev[j]
                chain += 1
        step = best_len if best_len >= 3 else 1
        for k in range(i, min(i + step, n - 2)):
            h = key(k)
            prev[k] = heads.get(h, -1)
            heads[h] = k
        yield i, (best_len if best_len >= 3 else 0), best_dist
        i += step


def compress(data, pad=True):
    n = len(data)
    out = bytearray([0x10, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    flag_pos, bit = None, -1
    for i, ln, dist in _find_matches(data, MAX_MATCH, MAX_DIST):
        if bit < 0:
            flag_pos = len(out)
            out.append(0)
            bit = 7
        if ln:
            out[flag_pos] |= 1 << bit
            out += bytes([(ln - 3) << 4 | (dist - 1) >> 8, (dist - 1) & 0xFF])
        else:
            out.append(data[i])
        bit -= 1
    out += b'\0' * ((-len(out)) % 4)
    return bytes(out)

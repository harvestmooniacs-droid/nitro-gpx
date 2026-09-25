"""Nitro LZ11 (type 0x11). Hash-chain compressor with all 3 token sizes."""
from .lz10 import _header, _find_matches

MAX_MATCH, MAX_DIST = 0x10110, 4096


def decompress(data, strict=True):
    size, src = _header(data, 0x11)
    out = bytearray()
    try:
        while len(out) < size:
            flags = data[src]
            src += 1
            for _ in range(8):
                if len(out) >= size:
                    break
                if flags & 0x80:
                    hi = data[src] >> 4
                    if hi == 0:
                        length = ((data[src] << 4) | (data[src + 1] >> 4)) + 0x11
                        src += 1
                    elif hi == 1:
                        length = (((data[src] & 0xF) << 12) | (data[src + 1] << 4) | (data[src + 2] >> 4)) + 0x111
                        src += 2
                    else:
                        length = hi + 1
                    disp = ((data[src] & 0xF) << 8 | data[src + 1]) + 1
                    src += 2
                    start = len(out) - disp
                    if start < 0:
                        raise ValueError('LZ11 bad back-reference')
                    for k in range(length):
                        out.append(out[start + k])
                else:
                    out.append(data[src])
                    src += 1
                flags = flags << 1 & 0xFF
    except IndexError:
        if strict:
            raise ValueError('LZ11 stream truncated')
    return bytes(out[:size])


def compress(data, pad=True):
    n = len(data)
    out = bytearray([0x11, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    flag_pos, bit = None, -1
    for i, ln, dist in _find_matches(data, MAX_MATCH, MAX_DIST):
        if bit < 0:
            flag_pos = len(out)
            out.append(0)
            bit = 7
        if ln:
            out[flag_pos] |= 1 << bit
            d = dist - 1
            if ln <= 0x10:
                out += bytes([(ln - 1) << 4 | d >> 8, d & 0xFF])
            elif ln <= 0x110:
                v = ln - 0x11
                out += bytes([v >> 4, (v & 0xF) << 4 | d >> 8, d & 0xFF])
            else:
                v = ln - 0x111
                out += bytes([0x10 | v >> 12, v >> 4 & 0xFF, (v & 0xF) << 4 | d >> 8, d & 0xFF])
        else:
            out.append(data[i])
        bit -= 1
    out += b'\0' * ((-len(out)) % 4)
    return bytes(out)

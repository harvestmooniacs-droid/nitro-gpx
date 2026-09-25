"""LZE ('Le') - the LZ variant of Luminous Arc 3 (Imageepoch). Read from the
game's own decoder (ITCM routine at 0x01FF831C, called with the 'L','e'
check), not guessed:

  0x00 'Le'   0x02 u24 decoded size   (0x05 is 0)   0x06 stream
  flag byte = 4 two-bit codes, low bits first:
    0  long copy:  v = u16 LE; length (v >> 12) + 3; distance (v & 0xFFF) + 5
    1  short copy: b = u8;     length (b >> 2) + 2;  distance (b & 3) + 1
    2  1 literal byte
    3  3 literal bytes
Decoding stops as soon as the declared size is reached (even mid-group).
"""
MAGIC = b'Le'


def is_lze(data):
    return len(data) >= 7 and data[:2] == MAGIC and data[5] == 0


def decoded_size(data):
    return data[2] | data[3] << 8 | data[4] << 16


def decompress(data):
    if not is_lze(data):
        raise ValueError('not an LZE stream')
    size = decoded_size(data)
    out = bytearray()
    i = 6
    while len(out) < size:
        flags = data[i]
        i += 1
        for _ in range(4):
            if len(out) >= size:
                break
            code = flags & 3
            flags >>= 2
            if code == 0:
                v = data[i] | data[i + 1] << 8
                i += 2
                length, dist = (v >> 12) + 3, (v & 0xFFF) + 5
            elif code == 1:
                b = data[i]
                i += 1
                length, dist = (b >> 2) + 2, (b & 3) + 1
            else:
                n = 1 if code == 2 else 3
                out += data[i:i + n]
                i += n
                continue
            if dist > len(out):
                raise ValueError('LZE: back-reference before start')
            for _ in range(min(length, size - len(out))):
                out.append(out[-dist])
    if len(out) != size:
        raise ValueError('LZE: stream truncated')
    return bytes(out)


def consumed(data):
    """Bytes of `data` used by the stream (for carving)."""
    size = decoded_size(data)
    produced, i = 0, 6
    while produced < size:
        flags = data[i]
        i += 1
        for _ in range(4):
            if produced >= size:
                break
            code = flags & 3
            flags >>= 2
            if code == 0:
                produced += ((data[i + 1] << 8) >> 12) + 3
                i += 2
            elif code == 1:
                produced += (data[i] >> 2) + 2
                i += 1
            else:
                n = 1 if code == 2 else 3
                produced += n
                i += n
    return i


def _longest(data, pos, table, max_len):
    """Best (length, distance) at `pos` from a hash chain of 3-byte keys."""
    best_len, best_dist = 0, 0
    end = min(len(data), pos + max_len)
    for cand in reversed(table.get(data[pos:pos + 3], ())[-48:]):
        dist = pos - cand
        if dist > 4100:
            break
        n = 0
        while pos + n < end and data[cand + n] == data[pos + n]:
            n += 1
        if n > best_len:
            best_len, best_dist = n, dist
            if n == max_len:
                break
    return best_len, best_dist


def compress(data):
    size = len(data)
    out = bytearray(b'Le' + bytes([size & 0xFF, size >> 8 & 0xFF, size >> 16 & 0xFF, 0]))
    tokens = []                          # (code, payload bytes)
    table = {}
    pos = 0

    def index(p):
        if p + 3 <= size:
            table.setdefault(data[p:p + 3], []).append(p)

    while pos < size:
        s_len = 0
        for dist in range(1, 5):          # short copy: distance 1..4, length 2..65
            if dist > pos:
                break
            n = 0
            while pos + n < size and n < 65 and data[pos + n - dist] == data[pos + n]:
                n += 1
            if n > s_len:
                s_len, s_dist = n, dist
        l_len, l_dist = _longest(data, pos, table, 18) if pos >= 5 else (0, 0)
        if l_len >= 3 and l_dist >= 5 and l_len > s_len:
            v = (l_len - 3) << 12 | (l_dist - 5)
            tokens.append((0, bytes([v & 0xFF, v >> 8])))
            step = l_len
        elif s_len >= 2:
            tokens.append((1, bytes([(s_len - 2) << 2 | (s_dist - 1)])))
            step = s_len
        else:
            tokens.append((2, data[pos:pos + 1]))
            step = 1
        for p in range(pos, pos + step):
            index(p)
        pos += step
    merged = []
    k = 0
    while k < len(tokens):                # three single literals -> one '3' code
        if k + 2 < len(tokens) and all(tokens[k + j][0] == 2 for j in range(3)):
            merged.append((3, tokens[k][1] + tokens[k + 1][1] + tokens[k + 2][1]))
            k += 3
        else:
            merged.append(tokens[k])
            k += 1
    for g in range(0, len(merged), 4):
        group = merged[g:g + 4]
        flags = 0
        for j, (code, _) in enumerate(group):
            flags |= code << (2 * j)
        out.append(flags)
        for _, payload in group:
            out += payload
    return bytes(out)

"""Bit-packed LZ with unary-selected field widths (Kaijuu Busters `*.lz`).

Recovered from the game's own ARM9 loader (read file -> size at 0x0201820C
-> decode at 0x020181C0 -> core loop 0x02017F18), not guessed:

  bit reader: 32-bit LE words, bits taken LSB first (= bytes LSB first)
  header    : 8 bits type, 32 bits decoded size
  type 0    : stored, payload at byte 5
  type 1/2  : until `size` bytes are out:
                bit 1 -> literal, 8 bits
                bit 0 -> copy: distance = unary n, then WIDTHS[n] bits
                        (type 1: a fixed 10 bits instead)
                        length   = unary n, then WIDTHS[n] bits
                        from out[pos - distance - 1], byte by byte
  WIDTHS = 2, 4, 6, 10 (table at 0x020C9774); unary = zero bits before a 1.

Nothing in the file names the codec; detection is strict (type 1/2, every
copy inside the output, the stream ends within the last word of the file).
"""
from .optimal import longest_matches

WIDTHS = (2, 4, 6, 10)
MAX_FIELD = (1 << WIDTHS[-1]) - 1


def header(data):
    if len(data) < 5:
        return None, None
    return data[0], data[1] | data[2] << 8 | data[3] << 16 | data[4] << 24


def decompress(data, consumed=False):
    typ, size = header(data)
    if typ == 0:
        out = bytes(data[5:5 + size])
        return (out, 5 + size) if consumed else out
    if typ not in (1, 2):
        raise ValueError('bitlz: unknown type')
    # fields are read from a 96-bit window refilled when fewer than the
    # worst-case token (1 + 2 * (4 + 10) = 29 bits) remain
    d = bytes(data) + bytes(12)
    end = len(data) * 8
    out = bytearray(size)
    pos = 0
    p = 40
    cache_at = -96
    cache = 0
    W = WIDTHS
    while pos < size:
        if p - cache_at > 64:
            q = p >> 3
            cache_at = q << 3
            cache = int.from_bytes(d[q:q + 12], 'little')
            if p > end:
                raise ValueError('bitlz: truncated')
        v = cache >> (p - cache_at)
        if v & 1:
            out[pos] = v >> 1 & 0xFF
            pos += 1
            p += 9
            continue
        v >>= 1
        p += 1
        if typ == 1:
            dist = v & 0x3FF
            v >>= 10
            p += 10
        else:
            n = 0
            while not v & 1:
                v >>= 1
                n += 1
                if n == 4:
                    raise ValueError('bitlz: bad prefix')
            w = W[n]
            dist = v >> 1 & ((1 << w) - 1)
            v >>= w + 1
            p += n + 1 + w
        n = 0
        while not v & 1:
            v >>= 1
            n += 1
            if n == 4:
                raise ValueError('bitlz: bad prefix')
        w = W[n]
        ln = v >> 1 & ((1 << w) - 1)
        p += n + 1 + w
        src = pos - dist - 1
        if src < 0 or ln == 0 or pos + ln > size:
            raise ValueError('bitlz: bad copy')
        if ln <= dist + 1:
            out[pos:pos + ln] = out[src:src + ln]
        else:
            for i in range(ln):
                out[pos + i] = out[src + i]
        pos += ln
    if p > end:
        raise ValueError('bitlz: truncated')
    return (bytes(out), (p + 7) // 8) if consumed else bytes(out)


def looks_like(data):
    typ, size = header(data)
    return typ in (1, 2) and 16 <= size <= max(64, len(data) * 16) and len(data) >= 16


def _field_cost(v):
    for n, w in enumerate(WIDTHS):
        if v < 1 << w:
            return n + 1 + w
    return None


class _Writer:
    def __init__(self):
        self.acc = 0
        self.n = 0
        self.out = bytearray()

    def put(self, v, n):
        self.acc |= v << self.n
        self.n += n
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def unary_field(self, v):
        for n, w in enumerate(WIDTHS):
            if v < 1 << w:
                self.put(1 << n, n + 1)
                self.put(v, w)
                return
        raise ValueError(v)

    def bytes(self):
        if self.n:
            self.out.append(self.acc & 0xFF)
        return bytes(self.out)


def compress(data, typ=2):
    """Optimal parse (same idea as optimal.py) over this codec's bit costs."""
    n = len(data)
    if typ == 0:
        return bytes([0]) + n.to_bytes(4, 'little') + bytes(data)
    L, D = longest_matches(data, MAX_FIELD, MAX_FIELD + 1)
    cost = [0] * (n + 1)
    choice = [0] * n
    for i in range(n - 1, -1, -1):
        best = cost[i + 1] + 9
        ch = 0
        li = L[i]
        if li:
            dc = 10 if typ == 1 else _field_cost(D[i] - 1)
            for ln in {li, min(li, 3), min(li, 15), min(li, 63)}:
                if ln < 2:
                    continue
                c = cost[i + ln] + 1 + dc + _field_cost(ln)
                if c < best:
                    best, ch = c, ln
        cost[i] = best
        choice[i] = ch
    w = _Writer()
    w.put(typ, 8)
    w.put(n, 32)
    i = 0
    while i < n:
        ln = choice[i]
        if ln:
            w.put(0, 1)
            if typ == 1:
                w.put(D[i] - 1, 10)
            else:
                w.unary_field(D[i] - 1)
            w.unary_field(ln)
            i += ln
        else:
            w.put(1, 1)
            w.put(data[i], 8)
            i += 1
    out = w.bytes()
    return out + bytes((-len(out)) % 4)

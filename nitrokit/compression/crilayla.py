"""CRILAYLA - CRI Middleware's back-to-front LZ (CPK entries).

Layout (little-endian header, the publicly documented CRI format):
  0x00 'CRILAYLA'  (Valkyrie Profile: Covenant of the Plume stores 8 zero
                    bytes here instead - same codec, magic blanked)
  0x08 u32 uncompressed_size   (NOT counting the 0x100-byte plain header)
  0x0C u32 compressed_size
  0x10 compressed bitstream (compressed_size bytes)
  0x10+compressed_size  0x100 bytes stored plain = the FIRST 0x100 bytes of
                        the output
Output = those 0x100 bytes + uncompressed_size bytes. The bitstream is read
from its last byte backwards, bits most-significant first, and fills the
output from its end backwards:
  bit 0 -> literal: next 8 bits
  bit 1 -> copy: 13 bits distance (+3), length 3 + a chain of 2,3,5,8-bit
           fields (a field of all ones continues), then 8-bit fields while
           each is 255.
An earlier version in this repo used a self-consistent but wrong reading
(prefix at the tail, LSB-first); it was only ever tested against its own
encoder. This one decodes all 2063 compressed entries of Valkyrie Profile.
"""
import struct

MAGIC = b'CRILAYLA'
PLAIN = 0x100
_VLE = (2, 3, 5, 8)


def header(data):
    """(uncompressed_size, compressed_size) or None. Accepts the real magic
    or 8 zero bytes, and requires the sizes to describe `data` exactly."""
    if len(data) < 0x10 + PLAIN or data[:8] not in (MAGIC, bytes(8)):
        return None
    usize, csize = struct.unpack_from('<II', data, 8)
    if 0x10 + csize + PLAIN != len(data) or usize == 0:
        return None
    return usize, csize


def is_crilayla(data):
    return header(data) is not None


def decompress(data):
    h = header(data)
    if h is None:
        raise ValueError('not a CRILAYLA stream')
    usize, csize = h
    out = bytearray(PLAIN + usize)
    out[:PLAIN] = data[0x10 + csize:0x10 + csize + PLAIN]
    pos = 0x10 + csize - 1                # current input byte (moving back)
    pool = left = 0

    def bits(n):
        nonlocal pos, pool, left
        v = 0
        while n:
            if left == 0:
                if pos < 0x10:
                    raise ValueError('CRILAYLA: stream underrun')
                pool, left = data[pos], 8
                pos -= 1
            take = min(left, n)
            v = v << take | (pool >> (left - take)) & ((1 << take) - 1)
            left -= take
            n -= take
        return v

    end = PLAIN + usize - 1
    done = 0
    while done < usize:
        if bits(1):
            src = end - done + bits(13) + 3
            length = 3
            for nb in _VLE:
                v = bits(nb)
                length += v
                if v != (1 << nb) - 1:
                    break
            else:
                while True:
                    v = bits(8)
                    length += v
                    if v != 255:
                        break
            if length > usize - done:
                raise ValueError('CRILAYLA: copy past the end')
            for _ in range(length):
                out[end - done] = out[src]
                src -= 1
                done += 1
        else:
            out[end - done] = bits(8)
            done += 1
    return bytes(out)


class _BitWriter:
    """Collects bits MSB-first into bytes that are later stored reversed."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value, count):
        for i in range(count - 1, -1, -1):
            self.acc = self.acc << 1 | (value >> i & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                self.acc = self.n = 0

    def flush(self):
        if self.n:
            self.out.append(self.acc << (8 - self.n))
            self.acc = self.n = 0
        return self.out


def _put_length(bw, length):
    rest = length - 3
    for nb in _VLE:
        full = (1 << nb) - 1
        if rest < full:
            bw.put(rest, nb)
            return
        bw.put(full, nb)
        rest -= full
    while True:
        v = min(rest, 255)
        bw.put(v, 8)
        rest -= v
        if v != 255:
            return


def _length_bits(length):
    rest, bits = length - 3, 0
    for nb in _VLE:
        full = (1 << nb) - 1
        bits += nb
        if rest < full:
            return bits
        rest -= full
    while True:
        bits += 8
        if rest < 255:
            return bits
        rest -= 255


MAX_COPY = 4096


def _common(rev, a, b, limit):
    """Length of the common prefix of rev[a:] and rev[b:], up to `limit`,
    by doubling + binary search on slice compares (memcmp in C) - runs of
    identical bytes would make a byte loop quadratic."""
    step = 8
    k = 0
    while k < limit:
        n = min(step, limit - k)
        if rev[a + k:a + k + n] != rev[b + k:b + k + n]:
            lo, hi = k, k + n
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if rev[a + lo:a + mid] == rev[b + lo:b + mid]:
                    lo = mid
                else:
                    hi = mid
            return lo if rev[a + lo] != rev[b + lo] else lo + 1
        k += n
        step *= 2
    return limit


def _matches(rev):
    """Longest (length, distance) at every position, distance 3..8194."""
    n = len(rev)
    table = {}
    best = [(0, 0)] * n
    for i in range(n - 2):
        key = rev[i:i + 3]
        chain = table.setdefault(key, [])
        bl = bd = 0
        limit = min(MAX_COPY, n - i)
        for cand in reversed(chain[-32:]):
            dist = i - cand
            if dist > 8194:
                break
            if dist < 3:
                continue
            k = _common(rev, cand, i, limit)
            if k > bl:
                bl, bd = k, dist
                if k == limit:
                    break
        best[i] = (bl, bd)
        chain.append(i)
        if len(chain) > 64:
            del chain[:32]
    return best


def compress(data, magic=MAGIC, target_len=None):
    """Valid CRILAYLA stream with an optimal parse over bit costs (literal
    9 bits, copy 14 + length field). The body (data after the first 0x100
    bytes) is coded from its END: the decoder writes output backwards and a
    copy reads bytes that sit AFTER the destination (distance 3..8194)."""
    if len(data) <= PLAIN:
        raise ValueError('CRILAYLA needs more than 0x100 bytes')
    plain, body = data[:PLAIN], data[PLAIN:]
    rev = body[::-1]                      # decoding order: rev[0] is written first
    n = len(rev)
    best = _matches(rev)
    INF = float('inf')
    cost = [INF] * (n + 1)
    choice = [None] * n
    cost[n] = 0
    breaks = (3, 5, 6, 12, 13, 43, 44, 298)
    for i in range(n - 1, -1, -1):
        cost[i] = 9 + cost[i + 1]
        choice[i] = (1, 0)
        bl, bd = best[i]
        if bl >= 3:
            lengths = {bl} | {b for b in breaks if b <= bl}
            for length in lengths:
                c = 14 + _length_bits(length) + cost[i + length]
                if c < cost[i]:
                    cost[i], choice[i] = c, (length, bd)
    bw = _BitWriter()
    i = 0
    while i < n:
        length, dist = choice[i]
        if dist:
            bw.put(1, 1)
            bw.put(dist - 3, 13)
            _put_length(bw, length)
        else:
            bw.put(0, 1)
            bw.put(rev[i], 8)
        i += length
    comp = bw.flush()
    comp.reverse()                        # decoder consumes bytes from the end
    if target_len is not None:
        pad = target_len - (0x10 + len(comp) + PLAIN)
        if pad < 0:
            raise ValueError(f'CRILAYLA: {0x10 + len(comp) + PLAIN} B does not fit {target_len} B')
        comp = bytes(pad) + bytes(comp)   # leading bytes are read last: never reached
    return magic + struct.pack('<II', len(body), len(comp)) + bytes(comp) + plain

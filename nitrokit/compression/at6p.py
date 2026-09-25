"""AT6P - Chunsoft's byte-delta codec (999: Nine Hours, Nine Persons, Nine
Doors; the same family ships in other Chunsoft DS titles). Not an LZ: every
output byte is coded relative to the previous one, which suits smooth
indexed images.

Algorithm as implemented in Tinke's 999 plugin and matheuscardoso96's
9H9P9DTools (Lib999/Compression/ATP6.cs); reimplemented here from that
description.

Header (0x14 bytes):
  0x00 'AT6P'
  0x04 u32 (file_length << 8) | tag    - tag byte is kept as found
  0x10 u32 decoded length (low 24 bits)
Stream (LSB-first bit reader) starting at 0x14:
  - the first 8 bits are the first output byte, then 8 padding bits;
  - each following symbol: k = number of 0 bits before a 1 (0..8), then a
    k-bit value v; n = (2**k - 1) + v; 2k+1 bits consumed.
      n == 0 -> repeat the current byte
      n == 1 -> emit the byte before it, and swap current/previous
      n >= 2 -> previous = current; current += n//2 (n even) or -n//2 (odd)
"""
import struct

MAGIC = b'AT6P'


def is_at6p(data):
    return len(data) >= 0x16 and data[:4] == MAGIC


def decoded_size(data):
    return struct.unpack_from('<I', data, 0x10)[0] & 0xFFFFFF


def decompress(data):
    if not is_at6p(data):
        raise ValueError('not an AT6P stream')
    size = decoded_size(data)
    if size == 0:
        return b''
    out = bytearray(size)
    code = data[0x14]
    out[0] = code
    prev = -1
    stream = bytes(data[0x16:]) + bytes(16)
    limit = (len(stream) - 16) * 8 + 16     # past the end reads as zeros
    bit = 0
    i = 1
    from_bytes = int.from_bytes
    while i < size:
        byte = bit >> 3
        window = from_bytes(stream[byte:byte + 8], 'little') >> (bit & 7)
        if window & 1:
            # run of 'repeat current byte' symbols (1 bit each)
            run = min((~window & (window + 1)).bit_length() - 1, 56, size - i)
            out[i:i + run] = bytes((code,)) * run
            i += run
            bit += run
        else:
            if not window & 0x1FF:
                raise ValueError('AT6P: invalid control mask')
            k = (window & -window).bit_length() - 1
            n = (1 << k) - 1 + (window >> (k + 1) & ((1 << k) - 1))
            if n == 1:
                if prev < 0:
                    raise ValueError('AT6P: back-reference before any previous byte')
                out[i] = prev
                prev, code = code, prev
            else:
                prev = code
                code = (code - (n >> 1) if n & 1 else code + (n >> 1)) & 0xFF
                out[i] = code
            i += 1
            bit += 2 * k + 1
        if bit > limit:
            raise ValueError('AT6P: stream truncated')
    return bytes(out)


class _Bits:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value, count):
        self.acc |= value << self.n
        self.n += count
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def flush(self):
        if self.n:
            self.out.append(self.acc & 0xFF)
        return bytes(self.out)


def _symbol(bits, n):
    k = (n + 1).bit_length() - 1          # 2**k - 1 <= n < 2**(k+1) - 1
    bits.put(1 << k, k + 1)               # k zeros then a one
    bits.put(n - ((1 << k) - 1), k)


def compress(payload, original=None):
    """`original`: the AT6P file this payload came from - its tag byte is
    kept so an unedited payload re-encodes to the same header."""
    bits = _Bits()
    if payload:
        bits.put(payload[0], 8)
        bits.put(0, 8)
        code, prev = payload[0], -1
        for b in payload[1:]:
            if b == code:
                _symbol(bits, 0)
            elif b == prev:
                _symbol(bits, 1)
                prev, code = code, prev
            else:
                up = (b - code) & 0xFF
                down = (code - b) & 0xFF
                n = up * 2 if up <= down else down * 2 + 1
                _symbol(bits, n)
                prev, code = code, b
    body = bits.flush()
    tag = original[4] if original is not None and is_at6p(original) else 0
    total = 0x14 + len(body)
    header = MAGIC + struct.pack('<I', total << 8 | tag) + bytes(8) + struct.pack('<I', len(payload))
    return header + body

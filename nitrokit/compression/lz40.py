"""Nintendo LZ40/LZ60 (tags 0x40/0x60) - an LZ11-shaped codec seen in some DS
titles, distinct from the SDK's BIOS LZ10/LZ11 (0x10/0x11). Cross-checked
against two independent open-source implementations (CUE's `lzx.c`,
PeterLemon/Nintendo_DS_Compressors - the "LZ40" branch of that tool; and
Venomalia/AuroraLib.Compression's `LZ40.cs`/`LZ60.cs`), which agree exactly
on the byte layout below. LZ60 is byte-identical to LZ40 except for the tag
and an optional 32-bit length field when the file is bigger than 16 MB (not
needed for anything the size of a single ROM resource).

  header: tag (0x40 or 0x60), u24 LE decoded size (u32 follows if that's 0)
  flag byte: read MSB-first like LZ10/LZ11, but stored on disk as the
    ARITHMETIC NEGATION (mod 256) of the logical byte - i.e. XOR/complement
    would NOT round-trip, only `(-b) & 0xFF` does (2's-complement negation
    is its own inverse mod 256, so decode and encode use the same formula)
  bit=0 -> literal byte
  bit=1 -> copy token, distance is the RAW value (no -1, unlike LZ10/LZ11),
    max window 0x1000 (4096), min match length 3:
      u16 LE word = (distance << 4) | selector
      selector 2-15 : that IS the match length (3-15), token is 2 bytes
      selector 0    : length = next u8  + 16                  (16-271)
      selector 1    : length = next u16 LE + 272               (272-65807)
"""
from .optimal import longest_matches

MAX_MATCH, MAX_DIST = 0x10110, 0x1000


def _header(data, tag):
    if len(data) < 4 or data[0] != tag:
        raise ValueError(f'not tag 0x{tag:02X}')
    size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if size == 0 and len(data) >= 8:
        size = int.from_bytes(data[4:8], 'little')
        pos = 8
    return size, pos


def decompress(data, tag, strict=True, consumed=False):
    size, i = _header(data, tag)
    out = bytearray()
    try:
        while len(out) < size:
            flags = (-data[i]) & 0xFF
            i += 1
            for bit in range(7, -1, -1):
                if len(out) >= size:
                    break
                if flags >> bit & 1:
                    word = data[i] | data[i + 1] << 8
                    i += 2
                    sel = word & 0xF
                    dist = word >> 4
                    if sel == 0:
                        length = data[i] + 16
                        i += 1
                    elif sel == 1:
                        length = (data[i] | data[i + 1] << 8) + 272
                        i += 2
                    else:
                        length = sel
                    start = len(out) - dist
                    if start < 0:
                        raise ValueError('LZ40 bad back-reference')
                    for k in range(length):
                        out.append(out[start + k])
                else:
                    out.append(data[i])
                    i += 1
    except IndexError:
        if strict:
            raise ValueError('LZ40 stream truncated')
    return (bytes(out[:size]), i) if consumed else bytes(out[:size])


def compress(data, tag=0x40, pad=True):
    n = len(data)
    out = bytearray([tag, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    L, D = longest_matches(data, MAX_MATCH, MAX_DIST)
    flag_positions = []
    flag_pos, bit = None, -1

    def flag_bit(is_match):
        nonlocal flag_pos, bit
        if bit < 0:
            flag_pos = len(out)
            flag_positions.append(flag_pos)
            out.append(0)
            bit = 7
        if is_match:
            out[flag_pos] |= 1 << bit
        bit -= 1

    i = 0
    while i < n:
        ln, dist = L[i], D[i]
        if ln >= 3:
            flag_bit(True)
            if ln < 16:
                v = dist << 4 | ln
                out += bytes([v & 0xFF, v >> 8])
            elif ln < 272:
                v = dist << 4
                out += bytes([v & 0xFF, v >> 8, ln - 16])
            else:
                v = dist << 4 | 1
                lv = ln - 272
                out += bytes([v & 0xFF, v >> 8, lv & 0xFF, lv >> 8])
            i += ln
        else:
            flag_bit(False)
            out.append(data[i])
            i += 1
    for p in flag_positions:
        out[p] = (-out[p]) & 0xFF
    if pad:
        out += bytes((-len(out)) % 4)
    return bytes(out)

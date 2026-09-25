"""Compression detection + dispatch.

`unwrap(data)` -> (payload, codec) where codec is one of
None, 'lz10', 'lz11', 'rle', 'huff4', 'huff8', 'lz10_hdr' (LZ10 behind a
'LZ77' 4-byte ASCII prefix, used by some games), 'lz10_solcomp' (LZ10 behind
an 8-byte 'SOLCOMP\0' prefix - Shepherd's Crossing 2, Nora to Toki no
Koubou), 'bitlz' (unary-width bit LZ, Kaijuu Busters - see compression.bitlz),
'diff8'/'diff16' (the SDK's
byte/halfword delta filter, tag 0x81/0x82 - see compression.others).
`rewrap(payload, codec)` recompresses with the SAME codec (SKILL: never
upgrade LZ10 <-> LZ11).

Detection is strict: a candidate codec must decompress without error and
consume nearly the whole input, and the output size must be plausible.
"""
from . import lz10, lz11, others, optimal, at6p, lze, bitlz, lz40, aklz

CODECS = ('lz10', 'lz11', 'rle', 'huff4', 'huff8', 'lz10_hdr', 'lz10_solcomp', 'at6p', 'lze', 'diff8', 'diff16',
          'bitlz', 'lz40', 'lz60', 'aklz')


SOLCOMP = b'SOLCOMP\0'


def _is_diff(data, size, tag):
    if data[0] != tag or len(data) < 4 or size > len(data) - 4:
        return False
    # both sides round up to a multiple of 4 (the header is padded to that)
    return (len(data) - 4 + 3) & ~3 == (size + 3) & ~3


def _plausible_size(data):
    return data[1] | data[2] << 8 | data[3] << 16


def try_decompress(data):
    if len(data) < 5:
        return None, None
    if lze.is_lze(data):
        try:
            out = lze.decompress(data)
        except (ValueError, IndexError):
            return None, None
        if len(data) - lze.consumed(data) <= 4:
            return out, 'lze'
        return None, None
    if at6p.is_at6p(data):
        try:
            out = at6p.decompress(data)
        except (ValueError, IndexError):
            return None, None
        return out, 'at6p'
    b0 = data[0]
    if b0 in (1, 2) and bitlz.looks_like(data):
        try:
            out, used = bitlz.decompress(data, consumed=True)
        except (ValueError, IndexError):
            out = None
        if out is not None and 0 <= len(data) - used < 4:
            return out, 'bitlz'
    size = _plausible_size(data)
    if b0 == 0x81 and _is_diff(data, size, 0x81):
        return others.diff8_decompress(data), 'diff8'
    if b0 == 0x82 and _is_diff(data, size, 0x82) and len(data) % 2 == 0:
        return others.diff16_decompress(data), 'diff16'
    # compressed streams of graphics rarely expand more than ~40x
    if b0 in (0x10, 0x11, 0x30, 0x24, 0x28, 0x40, 0x60) and not (0 < size <= max(64, len(data) * 64)):
        return None, None
    if aklz.is_aklz(data):
        try:
            out, used = aklz.decompress(data, consumed=True)
        except (ValueError, IndexError):
            out = None
        if out is not None and 0 <= len(data) - used < 8:
            return out, 'aklz'
        return None, None
    if b0 in (0x40, 0x60):
        try:
            out, used = lz40.decompress(data, b0, consumed=True)
        except (ValueError, IndexError):
            out = None
        # 0x40/0x60 are common leading bytes of unrelated data - only trust
        # it when the stream both matches the declared size AND consumes
        # nearly the whole file (same bar as lz10/lz11 below)
        if out is not None and len(out) == size and 0 <= len(data) - used < 4:
            return out, 'lz40' if b0 == 0x40 else 'lz60'
        return None, None
    try:
        if b0 == 0x10:
            out = lz10.decompress(data)
            used = lz10.compressed_length(data)
            # whole-file stream: the rest may only be alignment/padding junk
            if len(out) == size and (used >= len(data) - 16 or not data[used:].strip(bytes([0, 255]))):
                return out, 'lz10'
        elif b0 == 0x11:
            out = lz11.decompress(data)
            if len(out) == size:
                return out, 'lz11'
        elif b0 == 0x30:
            out = others.rle_decompress(data)
            if len(out) == size:
                return out, 'rle'
        elif b0 in (0x24, 0x28):
            out = others.huff_decompress(data)
            if len(out) == size:
                return out, 'huff4' if b0 == 0x24 else 'huff8'
        elif data[:8] == SOLCOMP and len(data) > 12 and data[8] == 0x10:
            inner = data[8:]
            out = lz10.decompress(inner)
            if len(out) == _plausible_size(inner):
                return out, 'lz10_solcomp'
        elif data[:4] == b'LZ77' and len(data) > 8 and data[4] == 0x10:
            out = lz10.decompress(data[4:])
            return out, 'lz10_hdr'
    except (ValueError, IndexError):
        pass
    return None, None


def decompress_as(data, codec):
    if codec == 'lz10':
        return lz10.decompress(data)
    if codec == 'lz11':
        return lz11.decompress(data)
    if codec == 'rle':
        return others.rle_decompress(data)
    if codec == 'lz10_hdr':
        return lz10.decompress(data[4:])
    if codec == 'lz10_solcomp':
        return lz10.decompress(data[8:])
    if codec in ('huff4', 'huff8'):
        return others.huff_decompress(data)
    if codec == 'at6p':
        return at6p.decompress(data)
    if codec == 'lze':
        return lze.decompress(data)
    if codec == 'bitlz':
        return bitlz.decompress(data)
    if codec in ('lz40', 'lz60'):
        return lz40.decompress(data, 0x40 if codec == 'lz40' else 0x60)
    if codec == 'aklz':
        return aklz.decompress(data)
    if codec == 'diff8':
        return others.diff8_decompress(data)
    if codec == 'diff16':
        return others.diff16_decompress(data)
    raise ValueError(f'unknown codec {codec}')


def unwrap(data):
    out, codec = try_decompress(data)
    return (out, codec) if codec else (data, None)


def rewrap(payload, codec, pad=True, original=None):
    if codec is None:
        return payload
    if codec == 'at6p':
        return at6p.compress(payload, original)
    if codec == 'lze':
        out = lze.compress(payload)
        return out + bytes((-len(out)) % 4) if pad else out
    if codec == 'lz10':
        return optimal.compress_lz10(payload, pad) if len(payload) < 4 << 20 else lz10.compress(payload, pad)
    if codec == 'lz11':
        return optimal.compress_lz11(payload, pad) if len(payload) < 4 << 20 else lz11.compress(payload, pad)
    if codec == 'rle':
        return others.rle_compress(payload, pad)
    if codec in ('huff4', 'huff8'):
        return others.huff_compress(payload, 4 if codec == 'huff4' else 8)
    if codec == 'lz10_hdr':
        return b'LZ77' + optimal.compress_lz10(payload, pad)
    if codec == 'lz10_solcomp':
        return SOLCOMP + optimal.compress_lz10(payload, pad)
    if codec == 'bitlz':
        return bitlz.compress(payload, original[0] if original else 2)
    if codec in ('lz40', 'lz60'):
        return lz40.compress(payload, tag=0x40 if codec == 'lz40' else 0x60, pad=pad)
    if codec == 'aklz':
        return aklz.compress(payload)
    if codec == 'diff8':
        return others.diff8_compress(payload)
    if codec == 'diff16':
        return others.diff16_compress(payload)
    raise NotImplementedError(f'recompress {codec}: use lz10 instead is NOT safe; '
                              'add a real encoder first')

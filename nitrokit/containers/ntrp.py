"""NTRP: Gravity/GungHo's resource bundle in Ragnarok DS (`2d_u/**/*.ntrp`,
311 files, always LZ10-wrapped).

    'NTRP' u8 version[4]        (04 04 04 00 in every sample)
    u32 ?                       (0 in every sample)
    then (u32 offset, u32 size) pairs until an entry reads 0/out of range

Each member is a complete graphics file with its own header, of either
family:
  * standard Nitro, reversed magic - RGCN/RLCN/RECN/RNAN (the 306 icon and
    object bundles). These were already found by the generic carver, so
    nothing changes for them;
  * the same stripped-down "lite" Nitro that Kaijuu Busters uses -
    NCG\\0/NCL\\0/NSC\\0 (the 5 full-screen backgrounds: credits + title).
    `formats.raw.nitro_lite` already reads those, but the carver never saw
    them because they carry no reversed magic, so the whole file was
    dropped: 23 NCG + 23 NCL + 23 NSC across 5 files, every one matching
    its size equation exactly (8 + n*64 / 8 + n*2 / 12 + n*2).

Members keep their order, so the pairing rule that applies to any other
ordered container applies here too.
"""
import struct

MAGIC = b'NTRP'
HEADER = 12
MAX_MEMBERS = 4096


def parse(data):
    """[(offset, size)] or None."""
    if len(data) < HEADER + 8 or data[:4] != MAGIC:
        return None
    entries = []
    pos = HEADER
    while pos + 8 <= len(data) and len(entries) < MAX_MEMBERS:
        off, size = struct.unpack_from('<II', data, pos)
        if not off or not size or off < HEADER or off + size > len(data):
            break
        if entries and off < entries[-1][0]:
            break                      # members are stored in order
        entries.append((off, size))
        pos += 8
    if not entries or entries[0][0] < pos:
        return None                    # the table would overlap the first member
    return entries


def read(data, i):
    entries = parse(data)
    if entries is None or not 0 <= i < len(entries):
        raise ValueError('not an NTRP member')
    off, size = entries[i]
    return data[off:off + size]


def pack(original, replacements):
    """Rebuild with the given members replaced.

    A same-size replacement (the normal case here - this project only ever
    rewrites pixel data) is patched IN PLACE, so the file stays byte-exact
    apart from those bytes: the members are padded/aligned in ways the
    header does not describe, and a blind repack would silently drop that
    padding. Only a size change forces a relayout, and then the original
    alignment of the first member is kept.
    """
    entries = parse(original)
    if entries is None:
        raise ValueError('not an NTRP container')
    if all(len(replacements[i]) == entries[i][1] for i in replacements):
        out = bytearray(original)
        for i, body in replacements.items():
            off, size = entries[i]
            out[off:off + size] = bytes(body)
        return bytes(out)
    bodies = [bytes(replacements[i]) if i in replacements else original[o:o + s]
              for i, (o, s) in enumerate(entries)]
    align = 4
    out = bytearray(original[:entries[0][0]])
    pos = len(out)
    for i, body in enumerate(bodies):
        struct.pack_into('<II', out, HEADER + i * 8, pos, len(body))
        out += body
        pad = -len(body) % align
        out += bytes(pad)
        pos += len(body) + pad
    return bytes(out)

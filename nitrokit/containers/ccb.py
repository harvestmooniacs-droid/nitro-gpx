"""CCB - CyberConnect2's DS archive (Solatorobo: 1881 files, nearly every
asset of the game). Checked on all 1881: bodies are contiguous and end
exactly at the file end, every entry decompresses to its declared size.

  0x00 'CCB '   0x04 u8 0, u8 1, u16 entry_count
  entry (32 bytes):
    char[24] name ('c1air1.nsbmd', NUL padded)
    u32 flags | compressed_size << 8     (flags byte kept as found)
    u32 codec | decompressed_size << 8   (codec = the Nitro compression
                                          tag: 0x10 LZ10, 0x11 LZ11, 0x30
                                          RLE, 0 = stored)
  then every body back to back, in entry order. A compressed body is the
  stream WITHOUT its usual 4-byte header - that header is the second u32
  of the entry.

Because sizes live in the table and bodies are contiguous, an entry may
grow on insert: the archive is simply rewritten.
"""
import struct

from .. import compression

MAGIC = b'CCB '
CODECS = {0x10: 'lz10', 0x11: 'lz11', 0x30: 'rle', 0: None}


def is_ccb(data):
    return len(data) >= 8 and data[:4] == MAGIC


def parse(data):
    """[{name, flags, codec, dsize, offset, csize}]; raises ValueError."""
    if not is_ccb(data):
        raise ValueError('not a CCB archive')
    n = struct.unpack_from('<H', data, 6)[0]
    pos = 8 + n * 32
    out = []
    for i in range(n):
        base = 8 + i * 32
        name = data[base:base + 24].split(b'\0')[0].decode('ascii', 'replace')
        a, b = struct.unpack_from('<II', data, base + 24)
        codec = b & 0xFF
        if codec not in CODECS:
            raise ValueError(f'CCB: unknown codec {codec:#x}')
        out.append({'name': name, 'flags': a & 0xFF, 'csize': a >> 8, 'codec': codec,
                    'dsize': b >> 8, 'offset': pos})
        pos += a >> 8
    if pos != len(data):
        raise ValueError('CCB: bodies do not end at the file end')
    return out


def read(data, entry):
    body = data[entry['offset']:entry['offset'] + entry['csize']]
    if not entry['codec']:
        return bytes(body)
    stream = bytes([entry['codec']]) + entry['dsize'].to_bytes(3, 'little') + body
    return compression.decompress_as(stream, CODECS[entry['codec']])


def unpack(data):
    return [read(data, e) for e in parse(data)]


def pack(original, replacements):
    """`replacements`: {entry_index: decompressed bytes}. Untouched entries
    keep their original compressed bytes."""
    entries = parse(original)
    table, bodies = bytearray(original[:8]), bytearray()
    for i, e in enumerate(entries):
        if i in replacements:
            payload = replacements[i]
            if e['codec']:
                body = compression.rewrap(payload, CODECS[e['codec']], pad=False)[4:]
            else:
                body = bytes(payload)
            dsize = len(payload)
        else:
            body = original[e['offset']:e['offset'] + e['csize']]
            dsize = e['dsize']
        base = 8 + i * 32
        table += original[base:base + 24]
        table += struct.pack('<II', e['flags'] | len(body) << 8, e['codec'] | dsize << 8)
        bodies += body
    return bytes(table + bodies)

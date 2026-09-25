"""CRI CPK container: the @UTF table format used by CPK/ADX/USM etc.

Public, widely reverse-engineered binary format (CriPakTools, vgmtoolbox,
kripp, etc.) - not a copy of any tool's source, just the documented byte
layout, reimplemented here.

A CPK is 'CPK ' + a big-endian @UTF packet (the header row), which points
at more @UTF packets: usually a TOC (per-file name/offset/size) or an
ITOC (id-based, offsets implicit/derived) plus an ETOC (extra per-file
info) and sometimes a GTOC (group toc). We only need enough to enumerate
files with their absolute offset + size.

CRILAYLA is CRI's own back-to-front LZ (like the BLZ used for ARM9
overlays elsewhere in this skill) - implemented for decompression only;
files stored uncompressed (extract_size == file_size, the common case in
many CPKs) reinsert directly.
"""
import struct


# ------------------------------------------------------------------ @UTF
COL_TYPE_MASK = 0x0F
COL_STORAGE_MASK = 0xF0
STORAGE_NONE = 0x00       # unused (rare)
STORAGE_ZERO = 0x10       # value is implicitly zero/absent for every row - NOT stored
STORAGE_CONST = 0x30      # single value shared by every row, stored once
STORAGE_ROW = 0x50        # per-row value, stored in each row
STORAGE_CONST2 = 0x70     # same as CONST, alternate flag seen in the wild
NOT_STORED = (STORAGE_NONE, STORAGE_ZERO)


WRAPPER_MAGICS = (b'CPK ', b'TOC ', b'ITOC', b'ETOC', b'GTOC')


def parse_utf(data, offset=0):
    """Return {'name', 'columns': [...], 'rows': [ {col: value}, ... ]}.

    `offset` may point either straight at '@UTF', or at one of CRI's own
    16-byte wrapper headers (magic + FF000000 + u32 size + 8 reserved) that
    precede the @UTF packet for every top-level table (CPK/TOC/ITOC/ETOC/
    GTOC all use the same wrapper) - both are accepted so callers never have
    to special-case which kind of pointer they were given."""
    if data[offset:offset + 4] in WRAPPER_MAGICS:
        offset += 0x10
    if data[offset:offset + 4] != b'@UTF':
        raise ValueError('not an @UTF packet')
    table_size = struct.unpack_from('>I', data, offset + 4)[0]
    base = offset + 8              # all internal offsets are relative to here
    rows_off, strings_off, data_off = struct.unpack_from('>III', data, base)
    table_name_off = struct.unpack_from('>I', data, base + 12)[0]
    n_columns, row_length, n_rows = struct.unpack_from('>HHI', data, base + 16)

    def cstr(o):
        o += base + strings_off
        end = data.index(b'\0', o)
        return data[o:end].decode('utf-8', 'replace')

    pos = base + 24
    columns = []
    for _ in range(n_columns):
        flags = data[pos]
        pos += 1
        name_off = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        columns.append({'flags': flags, 'type': flags & COL_TYPE_MASK,
                        'storage': flags & COL_STORAGE_MASK, 'name': cstr(name_off)})

    def read_value(col, p):
        t = col['type']
        if t in (0x00, 0x01):
            return data[p], p + 1
        if t in (0x02, 0x03):
            return struct.unpack_from('>H', data, p)[0], p + 2
        if t in (0x04, 0x05):
            return struct.unpack_from('>I', data, p)[0], p + 4
        if t in (0x06, 0x07):
            return struct.unpack_from('>Q', data, p)[0], p + 8
        if t == 0x08:
            return struct.unpack_from('>f', data, p)[0], p + 4
        if t == 0x0A:                       # string
            so = struct.unpack_from('>I', data, p)[0]
            return cstr(so), p + 4
        if t == 0x0B:                       # data blob: (offset, size) into data area
            do, sz = struct.unpack_from('>II', data, p)
            return (base + data_off + do, sz), p + 8
        raise ValueError(f'unknown UTF column type {t:#x}')

    const_vals = {}
    p = pos
    for col in columns:
        if col['storage'] in (STORAGE_CONST, STORAGE_CONST2):
            const_vals[col['name']], p = read_value(col, p)
        elif col['storage'] in NOT_STORED:
            const_vals[col['name']] = None

    rows = []
    for r in range(n_rows):
        row_pos = base + rows_off + r * row_length
        p = row_pos
        row = {}
        where = {}
        for col in columns:
            if col['storage'] in NOT_STORED or col['storage'] in (STORAGE_CONST, STORAGE_CONST2):
                row[col['name']] = const_vals[col['name']]
            else:
                where[col['name']] = (p, col['type'])
                row[col['name']], p = read_value(col, p)
        row['__where__'] = where          # absolute byte position of each per-row value
        rows.append(row)
    return {'name': cstr(table_name_off), 'columns': [c['name'] for c in columns],
            'rows': rows, 'size': 8 + table_size}


def is_cpk(data):
    return data[:4] == b'CPK '


def list_files(data):
    """[(name, abs_offset, packed_size, extract_size)] using whichever TOC
    the CPK actually has (Toc/Itoc), absolute offsets into `data`."""
    return [f[:4] for f in _entries(data)]


_INT_FMT = {0x00: '>B', 0x01: '>B', 0x02: '>H', 0x03: '>H', 0x04: '>I', 0x05: '>I', 0x06: '>Q', 0x07: '>Q'}


def grow_entry(data, abs_off, new_size):
    """Let the packed file at `abs_off` take `new_size` bytes without moving
    anything: allowed while it still ends before the next file (ITOC CPKs
    align every file to `Align`, TOC CPKs have their own gaps). Rewrites the
    FileSize value of its table row in place. Returns the new CPK bytes, or
    None if it does not fit / the size field cannot be patched."""
    entries = _entries(data)
    starts = sorted(e[1] for e in entries)
    for name, off, size, esize, loc in entries:
        if off != abs_off:
            continue
        nxt = next((st for st in starts if st > off), None)
        limit = nxt if nxt is not None else len(data)
        if new_size <= size:
            limit = off + size
        if off + new_size > limit or loc is None:
            return None
        pos, typ = loc
        fmt = _INT_FMT.get(typ)
        if fmt is None or new_size >= 1 << (8 * struct.calcsize(fmt)):
            return None
        out = bytearray(data)
        struct.pack_into(fmt, out, pos, new_size)
        return bytes(out)
    return None


def _entries(data):
    """list_files plus, per file, where its FileSize value is stored."""
    hdr = parse_utf(data, 0x10)
    row = hdr['rows'][0]

    def toc_ptr(field):
        v = row.get(field)
        return v[0] if isinstance(v, tuple) else v

    files = []
    toc_off = toc_ptr('TocOffset')
    if toc_off:
        toc = parse_utf(data, toc_off)
        content_off = row.get('ContentOffset') or toc_off
        for r in toc['rows']:
            name = r.get('FileName') or '?'
            dir_ = r.get('DirName') or ''
            full = f'{dir_}/{name}'.strip('/') if dir_ else name
            base_off = r.get('FileOffset')
            size = r.get('FileSize')
            esize = r.get('ExtractSize') or size
            if base_off is None or size is None:
                continue
            # FileOffset is usually relative to the smaller of ContentOffset/TocOffset
            abs_off = base_off if base_off > content_off else base_off + min(content_off, toc_off)
            files.append((full, abs_off, size, esize, r['__where__'].get('FileSize')))
        return files

    itoc_off = toc_ptr('ItocOffset')
    content_off = row.get('ContentOffset') or 0
    align = row.get('Align') or 2048
    if itoc_off:
        itoc = parse_utf(data, itoc_off)
        irow = itoc['rows'][0]
        sizes = {}
        for tag in ('DataL', 'DataH'):
            blob = irow.get(tag)
            if not blob:
                continue
            sub = parse_utf(data, blob[0])
            for r in sub['rows']:
                fid = r.get('ID')
                sizes[fid] = (r.get('FileSize'), r.get('ExtractSize') or r.get('FileSize'),
                              r['__where__'].get('FileSize'))
        off = content_off
        for fid in sorted(sizes):
            sz, esz, loc = sizes[fid]
            files.append((f'{fid:08d}.bin', off, sz, esz, loc))
            off += -(-sz // align) * align
    return files


# ---------------------------------------------------------------- CRILAYLA
# the codec lives in nitrokit.compression.crilayla; these names stay for
# existing callers
from ..compression import crilayla as _cl


def is_crilayla(data):
    return _cl.is_crilayla(data)


def crilayla_decompress(data):
    return _cl.decompress(data)


def crilayla_compress(data, magic=b'CRILAYLA', target_len=None):
    return _cl.compress(data, magic=magic, target_len=target_len)

"""Plain PKZIP archives used as a game filesystem (Code Lyoko `vfsflat.zip`:
7706 deflate entries named by a 32-bit hash of the original path, full of
NCGR/NCLR/NCER/NANR/BMD0).

The engine (ARM9 0x020B2B20 + 0x020B2C18) names an entry `%08x` of
CRC32(seed 0, no final xor) over the path lower-cased, '/' -> '\\', no
leading or doubled separators. `path_hash` reproduces it so a caller holding
real paths can map them back; nothing here depends on it.

Rewriting keeps entry order, names and each entry's method (stored stays
stored), recompressing deflate at level 9.
"""
import io
import zipfile

MAGIC = b'PK\x03\x04'


def is_zip(data):
    return data[:4] == MAGIC


def entries(data):
    """[ZipInfo] of the files (directories skipped) or None."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        return [i for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, ValueError):
        return None


_last = [None, None, None]      # data, ZipFile, file infos - scans read thousands of entries


def read(data, index):
    if _last[0] is not data:
        zf = zipfile.ZipFile(io.BytesIO(data))
        _last[:] = [data, zf, [i for i in zf.infolist() if not i.is_dir()]]
    return _last[1].read(_last[2][index])


def pack(original, replacements):
    src = zipfile.ZipFile(io.BytesIO(original))
    files = [i for i in src.infolist() if not i.is_dir()]
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as dst:
        pos = {id(info): n for n, info in enumerate(files)}
        for info in src.infolist():
            n = pos.get(id(info))
            body = replacements[n] if n in replacements else src.read(info)
            new = zipfile.ZipInfo(info.filename, info.date_time)
            new.compress_type = info.compress_type
            new.external_attr = info.external_attr
            dst.writestr(new, body, compresslevel=9 if info.compress_type == zipfile.ZIP_DEFLATED else None)
    return out.getvalue()


def path_hash(path):
    crc = 0
    prev = None
    for ch in path:
        if ch == '/':
            ch = '\\'
        if ch == '\\' and (prev is None or prev == '\\'):
            continue
        b = ord(ch.lower())
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        prev = ch
    return crc

"""NDS cartridge filesystem: header, FNT/FAT walk, overlays, safe rebuild.

Generic for any NDS ROM. No game-specific knowledge.

Rebuild rules (from the Atelier projects, see SKILL section 1):
  * the untracked gap between FAT end and the first post-FAT file (banner
    etc.) is kept verbatim - the rebuild only relocates FAT-tracked files
    that start after that gap;
  * header 0x80 gets the new used size, output padded with 0xFF back up to
    the original cartridge length (never truncated);
  * header CRC16 (0x15E over 0x000..0x15D) recomputed.
"""
import fnmatch
import struct
from pathlib import Path


HEADER_FIELDS = {'title': (0x00, 12), 'code': (0x0C, 4)}


def crc16_modbus(data, crc=0xFFFF):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class NDSRom:
    def __init__(self, data, path=None):
        self.data = bytes(data)
        self.path = Path(path) if path else None
        d = self.data
        self.title = d[0:12].rstrip(b'\0').decode('ascii', 'replace')
        self.code = d[12:16].decode('ascii', 'replace')
        (self.arm9_off, self.arm9_entry, self.arm9_ram, self.arm9_size,
         self.arm7_off, self.arm7_entry, self.arm7_ram, self.arm7_size,
         self.fnt_off, self.fnt_size, self.fat_off, self.fat_size,
         self.ov9_off, self.ov9_size, self.ov7_off, self.ov7_size) = struct.unpack_from('<16I', d, 0x20)
        self.n_files = self.fat_size // 8
        self.fat = [struct.unpack_from('<II', d, self.fat_off + i * 8) for i in range(self.n_files)]
        self.files = {}          # posix path -> fid
        self.names = {}          # fid -> posix path
        self._walk(0xF000, '')
        self.overlays9 = self._overlays(self.ov9_off, self.ov9_size)
        self.overlays7 = self._overlays(self.ov7_off, self.ov7_size)
        for tag, ovs in (('overlay9', self.overlays9), ('overlay7', self.overlays7)):
            for ov in ovs:
                self.names.setdefault(ov['file_id'], f"{tag}/overlay_{ov['id']:04d}.bin")

    @classmethod
    def open(cls, path):
        return cls(Path(path).read_bytes(), path)

    # ------------------------------------------------------------ FNT walk
    def _walk(self, dir_id, path):
        d = self.data
        e = self.fnt_off + (dir_id & 0xFFF) * 8
        sub_off, first_id = struct.unpack_from('<IH', d, e)
        p = self.fnt_off + sub_off
        fid = first_id
        while True:
            t = d[p]
            p += 1
            if t == 0:
                break
            ln = t & 0x7F
            name = d[p:p + ln].decode('cp932', 'replace')
            p += ln
            rel = f'{path}/{name}' if path else name
            if t & 0x80:
                sub = struct.unpack_from('<H', d, p)[0]
                p += 2
                self._walk(sub, rel)
            else:
                self.files[rel] = fid
                self.names[fid] = rel
                fid += 1

    def _overlays(self, off, size):
        out = []
        for i in range(size // 32):
            v = struct.unpack_from('<8I', self.data, off + i * 32)
            out.append({'id': v[0], 'ram': v[1], 'size': v[2], 'bss': v[3],
                        'file_id': v[6], 'comp_size': v[7] & 0xFFFFFF,
                        'compressed': bool(v[7] >> 24 & 1)})
        return out

    # ------------------------------------------------------------ access
    def read_fid(self, fid):
        s, e = self.fat[fid]
        return self.data[s:e]

    def fid_of(self, path):
        p = str(path).strip('/')
        if p in self.files:
            return self.files[p]
        if not hasattr(self, '_by_name'):
            self._by_name = {v: k for k, v in self.names.items()}
        return self._by_name[p]

    def read(self, path):
        return self.read_fid(self.fid_of(path))

    def glob(self, pattern='*'):
        for p in self.files:
            if fnmatch.fnmatch(p.lower(), pattern.lower()):
                yield p

    def arm9(self):
        return self.data[self.arm9_off:self.arm9_off + self.arm9_size]

    def arm_binary(self, which):
        """The ARM9 (9) or ARM7 (7) executable image. NOT a FAT file: it
        lives in a header-pointed region, so graphics carved out of it are
        readable but not (yet) writable - see pipeline.source."""
        off, size = ((self.arm9_off, self.arm9_size) if which == 9
                     else (self.arm7_off, self.arm7_size))
        return self.data[off:off + size]

    def banner_offset(self):
        return struct.unpack_from('<I', self.data, 0x68)[0]

    def all_files(self):
        """(fid, path) for FNT files, then overlays. Skips empty entries."""
        for fid in range(self.n_files):
            name = self.names.get(fid)
            if name is None:
                continue
            s, e = self.fat[fid]
            if e > s:
                yield fid, name

    # ------------------------------------------------------------ rebuild
    def rebuild(self, replacements, banner_patch=None, overlay_comp_sizes=None, header_patch=None):
        """replacements: {path_or_fid: bytes}. `overlay_comp_sizes`:
        {file_id: new_compressed_size} - a compressed overlay's FAT file can
        simply be resized like any other file, but the overlay TABLE (a
        separate header-pointed region, not FAT-tracked) also caches that
        size in its own entry; a BLZ-recompressed overlay must patch BOTH
        or the console decompresses the wrong number of bytes.
        `header_patch`: {'title': str (<= 12 ASCII), 'code': str (4 ASCII)} -
        written before the header CRC is recomputed."""
        by_fid = {}
        for k, v in replacements.items():
            fid = k if isinstance(k, int) else self.fid_of(k)
            by_fid[fid] = bytes(v)
        rom = self.data
        fat_end = self.fat_off + self.n_files * 8
        post = [s for s, e in self.fat if s >= fat_end and e > s]
        data_start = min(post, default=len(rom))
        out = bytearray(rom[:data_start])
        region = bytearray()
        new_fat = bytearray()
        for fid in range(self.n_files):
            s, e = self.fat[fid]
            if s < fat_end and fid not in by_fid:
                new_fat += struct.pack('<II', s, e)
                continue
            content = by_fid.get(fid, rom[s:e])
            start = data_start + len(region)
            region += content
            region += b'\xff' * ((-len(region)) % 4)
            new_fat += struct.pack('<II', start, start + len(content))
        out[self.fat_off:self.fat_off + len(new_fat)] = new_fat
        if banner_patch is not None:
            off, blob = banner_patch
            out[off:off + len(blob)] = blob
        for table_off, ovs in ((self.ov9_off, self.overlays9), (self.ov7_off, self.overlays7)):
            for i, ov in enumerate(ovs):
                new_size = (overlay_comp_sizes or {}).get(ov['file_id'])
                if new_size is not None:
                    struct.pack_into('<I', out, table_off + i * 32 + 28, new_size | 1 << 24)
        for key, (off, size) in HEADER_FIELDS.items():
            value = (header_patch or {}).get(key)
            if value is not None:
                out[off:off + size] = value.encode('ascii').ljust(size, b'\0')
        out += region
        struct.pack_into('<I', out, 0x80, len(out))
        struct.pack_into('<H', out, 0x15E, crc16_modbus(out[:0x15E]))
        if len(out) < len(rom):
            out += b'\xff' * (len(rom) - len(out))
        return bytes(out)


def looks_like_rom(data):
    """A nested NDS image (download-play .srl, Soma Bringer's data.srl)."""
    if len(data) < 0x4000:
        return False
    fnt, fnt_size, fat, fat_size = struct.unpack_from('<4I', data, 0x40)
    return (0x200 <= fnt < len(data) and 0x200 <= fat < len(data) and fat_size % 8 == 0
            and 8 <= fnt_size < 0x100000 and fat + fat_size <= len(data)
            and struct.unpack_from('<I', data, fnt + 4)[0] & 0xFFFF < 0xF000)


def verify_rebuild(old, new):
    """Zero-edit smoke test helper: same size and every FAT file identical."""
    a, b = NDSRom(old), NDSRom(new)
    problems = []
    if len(old) != len(new):
        problems.append(f'size {len(old)} -> {len(new)}')
    for fid in range(a.n_files):
        if a.read_fid(fid) != b.read_fid(fid):
            problems.append(f'fid {fid} differs')
    return problems

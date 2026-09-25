"""The .nds cartridge banner: the 32x32 icon + titles shown in the DS/DSi
menu, at the fixed offset the header names (0x68). NOT a FAT file - it
lives in the untracked gap between the FAT and the first FAT-tracked file
(see rom.ndsrom's rebuild rules), so it needs its own locator step
('banner') rather than a normal ['file', path].

Layout (public, documented in GBATEK; the icon+palette+6-title core is
IDENTICAL across every version - later versions only append more title
languages after it):
  0x000  u16 version (1=JP/EN/FR/DE/IT/ES, 2=+zh, 3=+ko, 0x103=DSi animated)
  0x002  u16 CRC16/MODBUS over 0x020..0x840; v2+ add CRCs at 0x004/0x006
         (longer spans) and DSi 0x103 one at 0x008 (animation) - see CRCS
  0x020  512 bytes - 32x32 icon, 4bpp, 16 tiles (4x4 grid of 8x8 tiles)
  0x220  32 bytes  - 16-colour BGR555 palette (index 0 is transparent)
  0x240  6x256 bytes UTF-16LE title blocks (jp,en,fr,de,it,es)
  0x840  end of the version-1 core; v2/v3/v0x103 append more data here,
         kept verbatim (untouched) by this module.
"""
import struct

CORE_SIZE = 0x840
ICON_OFF, ICON_SIZE = 0x20, 512
PAL_OFF, PAL_SIZE = 0x220, 32
TITLES_OFF, TITLE_LEN, N_TITLES = 0x240, 256, 6
CRC_SPAN = (0x20, 0x840)
SIZE_BY_VERSION = {1: 0x840, 2: 0x940, 3: 0xA40, 0x103: 0x23C0}
# every CRC a version carries: header offset -> span it covers (GBATEK;
# confirmed on the 41 banners of the test library, v1 and DSi 0x103).
# All but the DSi one cover the icon, so an icon edit must refresh them all.
CRCS = {2: (0x20, 0x840), 4: (0x20, 0x940), 6: (0x20, 0xA40), 8: (0x1240, 0x23C0)}
CRCS_BY_VERSION = {1: (2,), 2: (2, 4), 3: (2, 4, 6), 0x103: (2, 4, 6, 8)}
TITLES_BY_VERSION = {1: 6, 2: 7, 3: 8, 0x103: 8}


def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def total_size(data, off):
    v = struct.unpack_from('<H', data, off)[0]
    return SIZE_BY_VERSION.get(v, CORE_SIZE)


def is_banner(data, off=0):
    if off + 4 > len(data):
        return False
    v = struct.unpack_from('<H', data, off)[0]
    return v in SIZE_BY_VERSION


class Banner:
    def __init__(self, data):
        """`data`: the banner's own bytes (already sliced from the ROM)."""
        self.data = bytes(data)
        self.version = struct.unpack_from('<H', self.data, 0)[0]

    @property
    def n_tiles(self):
        return ICON_SIZE // 32           # 4bpp, 32 bytes/8x8 tile = 16

    def icon_pixels(self):
        """Flat list of 1024 palette indices (32x32), tiles unpacked into
        raster order (4x4 grid of 8x8 tiles, standard Nitro tile order)."""
        raw = self.data[ICON_OFF:ICON_OFF + ICON_SIZE]
        tiles = []
        for t in range(16):
            base = t * 32
            tile = [[0] * 8 for _ in range(8)]
            for y in range(8):
                row = raw[base + y * 4: base + y * 4 + 4]
                for x, byte in enumerate(row):
                    tile[y][x * 2] = byte & 0xF
                    tile[y][x * 2 + 1] = byte >> 4
            tiles.append(tile)
        img = [[0] * 32 for _ in range(32)]
        for t in range(16):
            ty, tx = divmod(t, 4)
            for y in range(8):
                for x in range(8):
                    img[ty * 8 + y][tx * 8 + x] = tiles[t][y][x]
        return img

    def palette(self):
        colors = []
        for i in range(16):
            v = struct.unpack_from('<H', self.data, PAL_OFF + i * 2)[0]
            r, g, b = (v & 31) << 3, (v >> 5 & 31) << 3, (v >> 10 & 31) << 3
            colors.append((r | r >> 5, g | g >> 5, b | b >> 5))
        return colors

    def titles(self):
        langs = ('ja', 'en', 'fr', 'de', 'it', 'es')
        out = {}
        for i, lang in enumerate(langs):
            raw = self.data[TITLES_OFF + i * TITLE_LEN: TITLES_OFF + (i + 1) * TITLE_LEN]
            out[lang] = raw.decode('utf-16-le', 'ignore').split('\0')[0]
        return out

    def rebuild(self, icon_pixels):
        """Replace only the icon graphic (32x32 indices); palette, titles
        and every trailing byte (extra languages, DSi animation data) are
        kept byte-for-byte, then the CRC is recomputed."""
        new = bytearray(self.data)
        for t in range(16):
            ty, tx = divmod(t, 4)
            base = ICON_OFF + t * 32
            for y in range(8):
                row = bytearray(4)
                for x in range(4):
                    lo = icon_pixels[ty * 8 + y][tx * 8 + x * 2] & 0xF
                    hi = icon_pixels[ty * 8 + y][tx * 8 + x * 2 + 1] & 0xF
                    row[x] = lo | hi << 4
                new[base + y * 4: base + y * 4 + 4] = row
        return self._with_crcs(new)

    def with_title(self, text):
        """Same title in every language slot this version has (the DS
        menu shows up to 3 lines; '\n' separates them). 127 characters
        max - one UTF-16 slot is 256 bytes including the terminator."""
        if len(text) > 127:
            raise ValueError('banner title longer than 127 characters')
        blob = text.encode('utf-16-le').ljust(TITLE_LEN, b'\0')
        new = bytearray(self.data)
        for i in range(TITLES_BY_VERSION.get(self.version, N_TITLES)):
            new[TITLES_OFF + i * TITLE_LEN:TITLES_OFF + (i + 1) * TITLE_LEN] = blob
        return self._with_crcs(new)

    def _with_crcs(self, new):
        for pos in CRCS_BY_VERSION.get(self.version, (2,)):
            a, b = CRCS[pos]
            struct.pack_into('<H', new, pos, crc16_modbus(new[a:b]))
        return bytes(new)

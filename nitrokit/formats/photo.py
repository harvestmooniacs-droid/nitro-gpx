"""Photographic / desktop image files found inside DS ROMs.

Visual novels and adventure ports (Myst, Touch Detective-style games)
often ship full-screen pictures instead of Nitro tiles. Two cases:

  * PUBLIC formats (JPEG, PNG, GIF, TGA) - decodable with Pillow. Exposed
    as 'photo' resources, PREVIEW ONLY: re-encoding JPEG is lossy and a
    PNG/GIF usually changes size, so writing them back is not the same
    pixel-only operation as everything else in this project.
    (Windows BMP is handled - and editable - in formats.bitmaps.WinBMP.)
  * PROPRIETARY photo codecs - recognised by signature only, so the scan
    and the coverage panel can say "this game uses codec X" instead of
    silently finding nothing. Decoding them is per-codec research.
"""
import io

from PIL import Image

PUBLIC = (
    (b'\xff\xd8\xff', 'JPEG'),
    (b'\x89PNG\r\n\x1a\n', 'PNG'),
    (b'GIF87a', 'GIF'),
    (b'GIF89a', 'GIF'),
)

# signature at offset -> name; only codecs positively seen in the corpus
PROPRIETARY = (
    (4, b'pu\x01\x01', 'Myst .pu (codec fotográfico próprio)'),
)


def _tga(data):
    """TGA has no magic: accept only a fully consistent uncompressed/RLE
    true-colour or paletted header whose pixel data fits the file."""
    if len(data) < 18:
        return False
    idlen, cmap, itype = data[0], data[1], data[2]
    w = data[12] | data[13] << 8
    h = data[14] | data[15] << 8
    bpp = data[16]
    return (itype in (1, 2, 3, 9, 10, 11) and cmap in (0, 1) and 0 < w <= 1024
            and 0 < h <= 1024 and bpp in (8, 15, 16, 24, 32)
            and (itype in (9, 10, 11) or 18 + idlen + w * h * bpp // 8 <= len(data)))


def detect(data, name=''):
    """-> ('public', fmt) | ('proprietary', label) | None"""
    for magic, fmt in PUBLIC:
        if data.startswith(magic):
            return 'public', fmt
    for off, sig, label in PROPRIETARY:
        if data[off:off + len(sig)] == sig:
            return 'proprietary', label
    if name.lower().endswith('.tga') and _tga(data):
        return 'public', 'TGA'
    return None


def open_image(data):
    """Decode a PUBLIC photo -> RGBA PIL image (raises on anything else)."""
    im = Image.open(io.BytesIO(bytes(data)))
    im.load()
    return im.convert('RGBA')

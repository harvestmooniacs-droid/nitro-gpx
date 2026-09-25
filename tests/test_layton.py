import struct
import unittest

from nitrokit.formats import layton_arc as L


def _bg_payload(cols=2, rows=1):
    pal = struct.pack('<I', 4) + struct.pack('<4H', 0, 0x1F, 0x3E0, 0x7C00)
    tiles = struct.pack('<I', 2) + bytes(range(64)) + bytes(64)
    grid = struct.pack('<HH', cols, rows) + struct.pack(f'<{cols * rows}H', *range(cols * rows))
    return pal + tiles + grid


def _ani_payload(arj=False):
    head = struct.pack('<HH', 1, 3) + (struct.pack('<I', 2) if arj else b'')
    part = (b'\0' * 4 if arj else b'') + struct.pack('<4H', 8, 0, 0, 0) + bytes([0x21] * 32)
    img = struct.pack('<4H', 16, 8, 1, 0) + part
    pal = (b'' if arj else struct.pack('<I', 2)) + struct.pack('<2H', 0, 0x7FFF)
    return head + img + pal + b'ANIMATION-SCRIPTS'


class LaytonArcTest(unittest.TestCase):
    def test_bg_every_codec_round_trips_an_edit(self):
        for kind in L.CODECS:
            with self.subTest(codec=L.CODECS[kind][0]):
                bg = L.LaytonBG(L._wrap(_bg_payload(), kind))
                self.assertEqual((bg.cols, bg.rows, bg.n_tiles, bg.codec_type), (2, 1, 2, kind))
                px = bytearray(bg.pixels)
                px[5] = 3
                again = L.LaytonBG(bg.rebuild(px))
                self.assertEqual(bytes(again.pixels), bytes(px))
                self.assertEqual(again.data[:bg.tile_off], bg.data[:bg.tile_off])     # palette kept
                self.assertEqual(again.entries, bg.entries)                           # map kept

    def test_animation_arc_and_arj_keep_scripts(self):
        for arj in (False, True):
            with self.subTest(arj=arj):
                ani = L.LaytonAni(L._wrap(_ani_payload(arj), 2), arj=arj)
                self.assertEqual(ani.frame_size(0), (16, 8))       # part at x=8 widens nothing
                self.assertEqual(ani.pal_n, 2)
                px = bytearray(ani.frame_pixels(0))
                px[0] = 0
                again = L.LaytonAni(ani.rebuild(0, px), arj=arj)
                self.assertEqual(bytes(again.frame_pixels(0)), bytes(px))
                self.assertTrue(again.data.endswith(b'ANIMATION-SCRIPTS'))

    def test_rejects_other_formats(self):
        self.assertIsNone(L.open_any(b'NARC' + bytes(60), 'x.arc'))
        self.assertIsNone(L.open_any(struct.pack('<I', 2) + b'\x10' + bytes(40), 'x.arc'))
        self.assertIsNone(L.open_any(L._wrap(_bg_payload(), 2), 'x.bin'))       # extension gate
        # map count that doesn't match the file size must not be accepted
        broken = _bg_payload()[:-2]
        with self.assertRaises(L.Unsupported):
            L.LaytonBG(L._wrap(broken, 2))


if __name__ == '__main__':
    unittest.main()

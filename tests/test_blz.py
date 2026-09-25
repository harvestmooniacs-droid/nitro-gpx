import random
import unittest

from nitrokit.compression import blz


def _sample(kind, n):
    if kind == 'repeat':
        return bytes([0x42]) * n
    if kind == 'text':
        return (b'the quick brown fox jumps over the lazy dog ' * (n // 40 + 1))[:n]
    rnd = random.Random(n)
    return bytes(rnd.randrange(256) for _ in range(n))


class BlzTest(unittest.TestCase):
    def test_round_trip_many_shapes(self):
        for n in (16, 17, 100, 257, 1000, 4099, 5000):
            for kind in ('repeat', 'text', 'random'):
                data = _sample(kind, n)
                try:
                    comp = blz.compress(data)
                except ValueError:
                    continue        # no size gain (near-random small input): legitimate
                self.assertEqual(blz.decompress(comp), data, (kind, n))

    def test_footer_round_trip_metadata(self):
        data = _sample('text', 2000)
        comp = blz.compress(data)
        enc_len, hdr_len, inc_len = blz.footer(comp)
        self.assertEqual(len(comp), enc_len)      # dec_len=0 here: file length == enc_len
        self.assertEqual(len(comp) + inc_len, len(data))
        self.assertGreater(inc_len, 0)
        self.assertEqual(hdr_len, 8)

    def test_rejects_incompressible(self):
        with self.assertRaises(ValueError):
            blz.compress(bytes(range(3)))         # too small to ever beat overhead

    def test_footer_rejects_non_blz(self):
        self.assertIsNone(blz.footer(b'not a blz stream at all'))
        self.assertIsNone(blz.footer(b''))


if __name__ == '__main__':
    unittest.main()

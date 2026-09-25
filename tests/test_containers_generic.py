import struct
import unittest

from nitrokit.compression import lz10
from nitrokit.containers import lzchain, nitrofs, pair_table


def _mini_nitrofs(files):
    """Build a one-directory mini NitroFS with the given {name: bytes}."""
    names = list(files)
    sub = b''.join(bytes([len(n)]) + n.encode() for n in names) + b'\0'
    fnt = struct.pack('<IHH', 8, 0, 1) + sub
    fnt_off, fat_off = 0x10, 0x10 + len(fnt)
    fat_size = 8 * len(names)
    data_off = fat_off + fat_size
    data_off += (-data_off) % 4
    body, fat = bytearray(), bytearray()
    for n in names:
        start = data_off + len(body)
        fat += struct.pack('<II', start, start + len(files[n]))
        body += files[n] + b'\xff' * ((-len(files[n])) % 4)
    head = struct.pack('<4I', fnt_off, len(fnt), fat_off, fat_size) + fnt + fat
    return head + bytes(data_off - len(head)) + bytes(body)


class NitroFSTest(unittest.TestCase):
    def test_names_read_and_growth(self):
        blob = _mini_nitrofs({'a.ncg.l': b'A' * 10, 'b.ncl.l': b'B' * 32})
        ents = nitrofs.parse(blob)
        self.assertEqual([e[0] for e in ents], ['a.ncg.l', 'b.ncl.l'])
        self.assertEqual(nitrofs.read(blob, 1), b'B' * 32)
        self.assertEqual(nitrofs.pack(blob, {}), blob)                 # zero edit: identical
        grown = nitrofs.pack(blob, {0: b'Z' * 100})
        self.assertEqual(nitrofs.read(grown, 0), b'Z' * 100)
        self.assertEqual(nitrofs.read(grown, 1), b'B' * 32)            # later member moved, intact

    def test_rejects_random(self):
        self.assertIsNone(nitrofs.parse(bytes(range(256)) * 4))


class PairTableTest(unittest.TestCase):
    def _blob(self):
        a, b = b'\x1f\x00' * 16, b'\x01' * 64
        table = struct.pack('<I', 2) + struct.pack('<II', 20, len(a)) + struct.pack('<II', 20 + len(a), len(b))
        return table + a + b

    def test_parse_and_pack(self):
        blob = self._blob()
        self.assertEqual(pair_table.parse(blob), [(20, 32), (52, 64)])
        self.assertEqual(pair_table.pack(blob, {}), blob)
        grown = pair_table.pack(blob, {0: b'\x00\x7c' * 20})
        self.assertEqual(pair_table.read(grown, 0), b'\x00\x7c' * 20)
        self.assertEqual(pair_table.read(grown, 1), b'\x01' * 64)

    def test_rejects_overlap_and_junk(self):
        bad = struct.pack('<I', 2) + struct.pack('<II', 20, 40) + struct.pack('<II', 30, 8) + bytes(60)
        self.assertIsNone(pair_table.parse(bad))
        self.assertIsNone(pair_table.parse(b'\x03\0\0\0' + bytes(8)))


class LzChainTest(unittest.TestCase):
    def test_chain_with_padding(self):
        s1, s2 = lz10.compress(b'hello world' * 20), lz10.compress(b'\x05' * 300)
        blob = s1 + b'\0' * ((-len(s1)) % 16) + s2
        chain = lzchain.parse(blob)
        self.assertEqual(len(chain), 2)
        off, codec, used = chain[1]
        self.assertEqual(lz10.decompress(blob[off:off + used]), b'\x05' * 300)

    def test_single_stream_is_not_a_chain(self):
        self.assertIsNone(lzchain.parse(lz10.compress(b'abc' * 100)))
        self.assertIsNone(lzchain.parse(lz10.compress(b'abc' * 100) + b'\x42' * 40))


if __name__ == '__main__':
    unittest.main()

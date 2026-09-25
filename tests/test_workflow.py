"""Regression tests using a synthetic NDS filesystem/banner, no game assets."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from nitrokit.rom.ndsrom import NDSRom, crc16_modbus
from nitrokit.formats.banner import Banner
from nitrokit.pipeline import workflow as W, resource as R
from nitrokit.pipeline.source import Session, EditError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from e2e_test import paint  # noqa: E402


def fixture_rom():
    data = bytearray(0x4000)
    data[:12] = b'NITRO TEST\0\0'
    data[12:16] = b'TEST'
    struct.pack_into('<4I', data, 0x40, 0x200, 15, 0x220, 8)
    struct.pack_into('<IHH', data, 0x200, 8, 0, 1)
    data[0x208:0x20f] = b'\x05x.bin\0'
    struct.pack_into('<II', data, 0x220, 0xc00, 0xc04)
    data[0xc00:0xc04] = b'KEEP'
    struct.pack_into('<I', data, 0x68, 0x240)
    struct.pack_into('<H', data, 0x240, 1)
    banner = Banner(data[0x240:0xa80])
    data[0x240:0xa80] = banner.rebuild([[0] * 32 for _ in range(32)])
    struct.pack_into('<I', data, 0x80, 0xc04)
    struct.pack_into('<H', data, 0x15e, crc16_modbus(data[:0x15e]))
    return bytes(data)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / 'original.nds'
        self.original.write_bytes(fixture_rom())
        self.rom = NDSRom.open(self.original)
        self.res = {'id': 0, 'kind': 'banner', 'name': 'icon', 'char': [['banner']], 'pal': None, 'map': None}
        self.dump = self.root / 'dump'
        self.output = self.root / 'edited.nds'
        self.make_dump()

    def tearDown(self):
        self.temp.cleanup()

    def make_dump(self):
        return W.dump_resources(self.rom, Session(self.rom), [self.res], self.dump, lambda r: 'icon.png')

    def edit(self, value=1):
        p = self.dump / 'icon.png'
        with Image.open(p) as opened:
            im = opened.copy()
        im.putpixel((0, 0), value)
        im.save(p)

    def manifest(self, modify):
        path = self.dump / 'manifest.json'
        man = json.loads(path.read_text())
        modify(man)
        path.write_text(json.dumps(man))

    def assert_rejected(self, exception=EditError):
        with self.assertRaises(exception):
            W.insert_dump(self.rom, self.dump, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.original.read_bytes(), fixture_rom())

    def test_real_banner_edit_and_fat_preserved(self):
        self.edit()
        report = W.insert_dump(self.rom, self.dump, self.output)
        self.assertEqual(report['changed'], 1)
        rebuilt = NDSRom.open(self.output)
        self.assertEqual(rebuilt.read('x.bin'), b'KEEP')
        self.assertEqual(R.to_png(self.res, Session(rebuilt))[0].getpixel((0, 0)), 1)
        self.assertTrue(R.roundtrip_ok(self.res, Session(rebuilt)))
        self.assertEqual(self.original.read_bytes(), fixture_rom())

    def test_rom_info_only(self):
        info = {'title': 'MY HACK', 'code': 'abcd', 'banner_title': 'Linha 1\nLinha 2'}
        W.insert_staged(self.rom, [], self.output, rom_info=info)
        rebuilt = NDSRom.open(self.output)
        self.assertEqual((rebuilt.title, rebuilt.code), ('MY HACK', 'ABCD'))
        banner = Banner(rebuilt.data[0x240:0xa80])
        self.assertEqual(set(banner.titles().values()), {'Linha 1\nLinha 2'})
        self.assertEqual(struct.unpack_from('<H', rebuilt.data, 0x242)[0],
                         crc16_modbus(rebuilt.data[0x260:0xa80]))
        self.assertEqual(struct.unpack_from('<H', rebuilt.data, 0x15e)[0], crc16_modbus(rebuilt.data[:0x15e]))
        self.assertEqual(rebuilt.read('x.bin'), b'KEEP')
        self.assertEqual(self.original.read_bytes(), fixture_rom())

    def test_rom_info_together_with_icon_edit(self):
        self.edit(3)
        man = json.loads((self.dump / 'manifest.json').read_text())
        rec = dict(man['resources'][0], _dump=str(self.dump))
        W.insert_staged(self.rom, [(rec, self.dump / 'icon.png')], self.output,
                        rom_info={'banner_title': 'Novo'})
        rebuilt = NDSRom.open(self.output)
        self.assertEqual(R.to_png(self.res, Session(rebuilt))[0].getpixel((0, 0)), 3)
        self.assertEqual(Banner(rebuilt.data[0x240:0xa80]).titles()['en'], 'Novo')

    def test_export_files_only_changed(self):
        self.edit(3)
        man = json.loads((self.dump / 'manifest.json').read_text())
        rec = dict(man['resources'][0], _dump=str(self.dump))
        out = self.output.with_suffix('')
        report = W.export_files(self.rom, [(rec, self.dump / 'icon.png')], out)
        self.assertEqual(report['files'], ['banner.bin'])
        blob = (out / 'banner.bin').read_bytes()
        self.assertEqual(struct.unpack_from('<H', blob, 2)[0], crc16_modbus(blob[0x20:0x840]))
        with self.assertRaises(EditError):              # non-empty destination
            W.export_files(self.rom, [(rec, self.dump / 'icon.png')], out)
        with self.assertRaises(EditError):              # nothing edited
            W.export_files(self.rom, [], self.output.with_name('other'))

    def test_rom_info_rejected(self):
        for bad in ({'title': 'ACENTUAÇÃO'}, {'title': 'X' * 13}, {'code': 'AB'},
                    {'banner_title': 'a\nb\nc\nd'}):
            with self.assertRaises(EditError):
                W.insert_staged(self.rom, [], self.output, rom_info=bad)
        with self.assertRaises(EditError):              # nothing to do at all
            W.insert_staged(self.rom, [], self.output, rom_info={})
        self.assertFalse(self.output.exists())

    def test_dsi_banner_refreshes_every_crc(self):
        from nitrokit.formats import banner as B
        blob = bytearray(0x23C0)
        struct.pack_into('<H', blob, 0, 0x103)
        new = B.Banner(bytes(blob)).with_title('DSi')
        for pos, (a, b) in B.CRCS.items():
            self.assertEqual(struct.unpack_from('<H', new, pos)[0], B.crc16_modbus(new[a:b]), hex(pos))
        self.assertEqual(set(B.Banner(new).titles().values()), {'DSi'})

    def test_wrong_rom_same_game_code(self):
        data = bytearray(self.rom.data)
        data[-1] ^= 1
        self.rom = NDSRom(data)
        self.assert_rejected()

    def test_legacy_manifest(self):
        self.manifest(lambda m: m.update(schema_version=1))
        self.assert_rejected()

    def test_missing_png(self):
        (self.dump / 'icon.png').unlink()
        self.assert_rejected(FileNotFoundError)

    def test_palette_only_edit(self):
        p = self.dump / 'icon.png'
        with Image.open(p) as opened:
            im = opened.copy()
        pal = im.getpalette()
        pal[0] = 255
        im.putpalette(pal)
        im.save(p)
        self.assert_rejected()

    def test_transparency_only_edit(self):
        p = self.dump / 'icon.png'
        with Image.open(p) as opened:
            im = opened.copy()
        im.info.pop('transparency', None)
        im.save(p)
        self.assert_rejected()

    def test_invalid_index_is_not_silently_masked(self):
        self.edit(16)
        self.assert_rejected()

    def test_preview_edit(self):
        self.edit()
        self.manifest(lambda m: m['resources'][0]['info'].update(editable=False))
        self.assert_rejected()

    def test_output_equal_input(self):
        with self.assertRaises(EditError):
            W.insert_dump(self.rom, self.dump, self.original)
        self.assertEqual(self.original.read_bytes(), fixture_rom())

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'EXISTING')
        with self.assertRaises(EditError):
            W.insert_dump(self.rom, self.dump, self.output)
        self.assertEqual(self.output.read_bytes(), b'EXISTING')

    def test_path_escape(self):
        self.manifest(lambda m: m['resources'][0].update(png='../icon.png'))
        self.assert_rejected()

    def test_late_batch_failure_publishes_nothing(self):
        self.edit()
        def add_missing(m):
            record = copy.deepcopy(m['resources'][0])
            record['png'] = 'missing.png'
            m['resources'].append(record)
        self.manifest(add_missing)
        self.assert_rejected(FileNotFoundError)

    def test_conflicting_shared_pixels_abort(self):
        self.edit()
        with patch.object(R, 'from_png', return_value=3):
            self.assert_rejected()

    def test_commit_overflow_aborts(self):
        self.edit()
        with patch.object(Session, 'commit', side_effect=EditError('slot overflow')):
            self.assert_rejected()

    def test_dump_does_not_overwrite_edits(self):
        self.edit()
        with self.assertRaises(EditError):
            self.make_dump()
        with Image.open(self.dump / 'icon.png') as im:
            self.assertEqual(im.getpixel((0, 0)), 1)

    def test_atomic_publish_never_replaces_existing(self):
        self.output.write_bytes(b'KEEP')
        with self.assertRaises(FileExistsError):
            W.publish_new(self.output, b'WRONG')
        self.assertEqual(self.output.read_bytes(), b'KEEP')
        self.assertFalse(list(self.root.glob('.nitrogfx-*')))

    def test_verification_catches_nonfat_corruption(self):
        new = bytearray(self.rom.rebuild({}))
        new[0x180] ^= 1
        with self.assertRaises(EditError):
            W.verify_candidate(self.rom, new, {}, None)

    def test_roundtrip_preview_is_skipped(self):
        self.assertIsNone(R.roundtrip_ok({'kind': 'raw_screen'}, Session(self.rom)))

    def test_paint_changes_index_one(self):
        im = Image.fromarray(np.ones((32, 32), dtype=np.uint8))
        im.putpalette([0] * 768)
        painted, _ = paint(im, 4)
        self.assertFalse(np.array_equal(np.array(im), np.array(painted)))

    def run_command(self, script, *args):
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / script),
                               str(self.original), *map(str, args)], capture_output=True, text=True)

    def test_cli_missing_png_exits_nonzero(self):
        (self.dump / 'icon.png').unlink()
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'nitrogfx.py'),
                   'insert', str(self.original), str(self.dump), str(self.output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.output.exists())

    def test_e2e_empty_selection_fails(self):
        result = self.run_command('tools/e2e_test.py', self.root / 'e2e-empty', '--filter', 'no-match')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('no editable resources selected', result.stdout)

    def test_e2e_valid_edit_succeeds(self):
        result = self.run_command('tools/e2e_test.py', self.root / 'e2e-valid', '--filter', '__banner__')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / 'e2e-valid/e2e_report.json').read_text())
        self.assertEqual((report['changed'], report['reproduced']), (1, 1))

    def test_e2e_noop_writer_fails(self):
        import e2e_test
        with patch.object(sys, 'argv', ['tools/e2e_test.py', str(self.original), str(self.root / 'noop'),
                                        '--filter', '__banner__']), patch.object(R, 'from_png', return_value=0):
            self.assertEqual(e2e_test.main(), 1)


if __name__ == '__main__':
    unittest.main()

"""Tests for loose sources and manual preview introduced by the modular GUI."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image

from nitrokit.gui.advanced import render
from nitrokit.io import FolderSource, SingleFileSource
from nitrokit.pipeline import resource as R, workflow as W
from nitrokit.pipeline.scan import scan
from nitrokit.pipeline.source import Session, EditError


class LooseSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_dir = self.root / 'source'
        self.source_dir.mkdir()
        # One 8x8 4bpp tile and a 16-colour BGR555 palette.
        (self.source_dir / 'sample.nbfc').write_bytes(bytes(range(32)))
        (self.source_dir / 'sample.nbfp').write_bytes(b''.join((i * 0x421).to_bytes(2, 'little') for i in range(16)))

    def tearDown(self):
        self.temp.cleanup()

    def test_folder_scan_dump_insert(self):
        source = FolderSource.open(self.source_dir)
        session = Session(source)
        found, stats = scan(source, session)
        self.assertEqual(stats['raw'], 2)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'raw_atlas')
        dump = self.root / 'dump'
        W.dump_resources(source, session, found, dump, W.png_name)
        manifest = json.loads((dump / 'manifest.json').read_text())
        self.assertEqual(manifest['source']['kind'], 'folder')
        record = manifest['resources'][0]
        png = dump / record['png']
        with Image.open(png) as opened:
            edited = opened.copy()
        edited.putpixel((0, 0), (edited.getpixel((0, 0)) + 1) % 16)
        edited.save(png)
        output = self.root / 'output'
        report = W.insert_dump(source, dump, output)
        self.assertEqual(report['changed'], 1)
        rebuilt = FolderSource.open(output)
        found2, _ = scan(rebuilt, Session(rebuilt))
        found2[0]['atlas'] = record['atlas']
        self.assertEqual(W.pixel_hash(R.to_png(found2[0], Session(rebuilt))[0]), W.pixel_hash(edited))
        self.assertEqual((self.source_dir / 'sample.nbfc').read_bytes(), bytes(range(32)))

    def test_folder_identity_detects_dependency_change(self):
        source = FolderSource.open(self.source_dir)
        session = Session(source)
        found, _ = scan(source, session)
        dump = self.root / 'dump'
        W.dump_resources(source, session, found, dump, W.png_name)
        (self.source_dir / 'sample.nbfp').write_bytes(bytes(32))
        with self.assertRaises(EditError):
            W.insert_dump(FolderSource.open(self.source_dir), dump, self.root / 'output')

    def test_single_file_scan(self):
        source = SingleFileSource.open(self.source_dir / 'sample.nbfc')
        found, stats = scan(source, Session(source))
        self.assertEqual((len(found), stats['raw']), (1, 1))

    def test_manual_preview_uncompressed_and_missing_palette(self):
        image, info = render(self.source_dir / 'sample.nbfc', 'Sem compressão', 4, 8, 8, 0, 8)
        self.assertEqual(image.size, (8, 8))
        self.assertEqual((info['codec'], info['palette']), ('none', None))

    def test_manual_preview_reports_short_palette(self):
        short = self.root / 'short.pal'
        short.write_bytes(b'\0\0')
        with self.assertRaisesRegex(ValueError, 'Paleta precisa'):
            render(self.source_dir / 'sample.nbfc', 'Sem compressão', 4, 8, 8, 0, 8, short)


if __name__ == '__main__':
    unittest.main()

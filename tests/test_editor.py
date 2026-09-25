import tkinter as tk
import unittest

import numpy as np
from PIL import Image

from nitrokit.gui.editor import PixelEditor, render_text


def _image(two_rows=False):
    a = np.zeros((32, 64), np.uint8)
    if two_rows:
        a[:, 32:] = 16                       # right half drawn with palette row 1
    im = Image.fromarray(a, 'P')
    im.putpalette([(i * 7 + c * 50) % 256 for i in range(256) for c in range(3)])
    im.info['transparency'] = 0
    return im


class _Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


class EditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest('no display')
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.ed = PixelEditor(self.root)
        self.ed.pack()
        self.root.update()

    def tearDown(self):
        self.ed.destroy()

    def at(self, px, py):
        s = self.ed.scale
        return _Ev(int(px * s - self.ed.canvas.canvasx(0) + s / 2), int(py * s - self.ed.canvas.canvasy(0) + s / 2))

    def test_pencil_eraser_undo_and_output(self):
        ed = self.ed
        ed.set_image(_image(), editable=True, bpp=8)
        ed.set_zoom(4)
        ed.primary, ed.tool = 7, ed.tool
        ed.brush.set(3)
        ed._down(self.at(5, 5)); ed._move(self.at(15, 5)); ed._up(None)
        self.assertTrue((ed.idx[4:7, 4:17] == 7).all())
        ed.tool.set('eraser'); ed.brush.set(1)
        ed._down(self.at(10, 5)); ed._up(None)
        self.assertEqual(ed.idx[5, 10], 0)
        ed.do_undo()
        self.assertEqual(ed.idx[5, 10], 7)
        out = ed.get_image()
        self.assertEqual((out.mode, out.size), ('P', (64, 32)))
        self.assertEqual(out.getpalette(), _image().getpalette())

    def test_selection_move_and_text(self):
        ed = self.ed
        ed.set_image(_image(), editable=True, bpp=8)
        ed.set_zoom(4)
        ed.idx[4:7, 4:10] = 3
        ed.tool.set('select')
        ed._down(self.at(4, 4)); ed._move(self.at(9, 6)); ed._up(None)
        ed._down(self.at(5, 5)); ed._move(self.at(5, 15)); ed._up(None); ed.commit()
        self.assertTrue((ed.idx[4:7, 4:10] == 0).all())
        self.assertTrue((ed.idx[14:17, 4:10] == 3).all())
        ed.tool.set('text'); ed.primary, ed.secondary = 5, 9
        ed.text_var.set('AB'); ed.text_outline.set(True)
        ed._down(self.at(30, 10)); ed.commit()
        self.assertTrue({5, 9} <= set(np.unique(ed.idx).tolist()))

    def test_4bpp_keeps_palette_row(self):
        ed = self.ed
        ed.set_image(_image(two_rows=True), editable=True, bpp=4)
        ed.set_zoom(4)
        self.assertTrue(ed.subpal)
        ed.primary = 3
        ed._down(self.at(40, 2)); ed._up(None)
        self.assertEqual(ed.idx[2, 40], 16 + 3)
        ed.tool.set('eraser')
        ed._down(self.at(40, 2)); ed._up(None)
        self.assertEqual(ed.idx[2, 40], 16)

    def test_read_only(self):
        ed = self.ed
        ed.set_image(_image(), editable=False, bpp=8)
        ed._down(self.at(3, 3)); ed._up(None)
        self.assertEqual(ed.idx[3, 3], 0)
        self.assertFalse(ed.dirty)

    def test_render_text_has_outline_ring(self):
        codes, mask = render_text('A', 12, True)
        self.assertTrue((codes == 1).any() and (codes == 2).any())
        self.assertEqual(codes.shape, mask.shape)


if __name__ == '__main__':
    unittest.main()

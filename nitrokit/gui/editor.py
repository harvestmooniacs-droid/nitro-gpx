"""Built-in indexed pixel editor for the Insert tab.

Edits palette INDICES only (never RGB), so the result keeps the dump's
palette and size - exactly what reinsertion requires. Paint-style model:
one image plus at most one FLOATING element (a lifted selection, a line of
text or a pasted picture) that can be dragged until it is committed
(Enter / click outside / tool change) or cancelled (Esc).

4bpp graphics with several sub-palettes: a tile can only use the 16
colours of its own palette row, so every write keeps the destination
pixel's row and changes only the colour inside it (the row is what the
reinsertion keeps; anything else would change colour silently).
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import theme
from .views import ZoomView
from .widgets import Tooltip, help_icon

TOOLS = (('pencil', '✏ Lápis', 'Pinta com a cor principal. Tamanho = lado do pincel em pixels.'),
         ('eraser', '⌫ Borracha', 'Apaga para a cor transparente (índice 0). Usa o mesmo tamanho do pincel.'),
         ('picker', '💧 Conta-gotas', 'Clique num pixel para usar a cor dele (atalho: botão direito em '
                                     'qualquer ferramenta).'),
         ('select', '⬚ Seleção', 'Arraste para selecionar; arraste de dentro da seleção para mover o '
                                 'pedaço. Enter confirma, Esc cancela, Delete apaga, Ctrl+C/Ctrl+V copia.'),
         ('text', 'T Texto', 'Digite o texto, clique na imagem para colocar e arraste para posicionar. '
                             'Enter confirma, Esc cancela.'))
UNDO_LIMIT = 40


def _font(size):
    try:
        return ImageFont.load_default(size=size)          # Pillow >= 10.1, bundled font
    except (TypeError, OSError, AttributeError):
        pass
    for name in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_text(text, size, outline):
    """-> (codes, mask): codes 1 = letter, 2 = outline; no anti-aliasing
    (an indexed image has no in-between colours)."""
    font = _font(size)
    l, t, r, b = font.getbbox(text)
    w, h = max(1, r - l) + 4, max(1, b - t) + 4
    im = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(im)
    draw.fontmode = '1'
    draw.text((2 - l, 2 - t), text, fill=255, font=font)
    body = np.array(im) > 127
    codes = body.astype(np.uint8)
    if outline:
        ring = np.zeros_like(body)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ring |= np.roll(np.roll(body, dy, 0), dx, 1)
        codes[ring & ~body] = 2
    mask = codes > 0
    if not mask.any():
        return np.zeros((1, 1), np.uint8), np.zeros((1, 1), bool)
    ys, xs = np.nonzero(mask)
    sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    return codes[sl], mask[sl]


class PixelEditor(ZoomView):
    def __init__(self, parent, on_apply=None, on_dirty=None, on_save=None, on_discard=None):
        super().__init__(parent, show_oam_toggle=False)
        self.on_apply, self.on_dirty = on_apply, on_dirty
        self.on_save, self.on_discard = on_save, on_discard
        self.idx = self.palette = self.transparency = None
        self.subpal = False
        self.editable = False
        self.dirty = False
        self.undo, self.redo = [], []
        self.floating = None           # {'idx','mask','x','y','kind', ...}
        self.sel = None                # (x0, y0, x1, y1) exclusive end
        self.clip = None
        self._drag = None
        self._last = None
        self.primary, self.secondary = 1, 0
        self._build_controls()
        c = self.canvas
        for seq in ('<ButtonPress-1>', '<B1-Motion>', '<Double-Button-1>'):
            c.unbind(seq)
        c.bind('<ButtonPress-1>', self._down)
        c.bind('<B1-Motion>', self._move)
        c.bind('<ButtonRelease-1>', self._up)
        c.bind('<ButtonPress-3>', self._pick_event)
        c.bind('<ButtonPress-2>', lambda e: c.scan_mark(e.x, e.y))
        c.bind('<B2-Motion>', self._drag_pan)
        c.bind('<Motion>', self._hover)
        c.bind('<Leave>', lambda _e: c.delete('cursor'))
        for seq, fn in (('<Control-z>', self.do_undo), ('<Control-y>', self.do_redo),
                        ('<Return>', self.commit), ('<Escape>', self.cancel),
                        ('<Delete>', self.delete_selection), ('<Control-c>', self.copy),
                        ('<Control-v>', self.paste)):
            c.bind(seq, lambda _e, f=fn: f())

    # ------------------------------------------------------------ UI
    def _build_controls(self):
        bar = ttk.Frame(self, style='Panel.TFrame')
        bar.pack(fill='x', before=self.canvas.master, pady=(0, 6))
        self.tool = tk.StringVar(value='pencil')
        for key, label, tip in TOOLS:
            b = ttk.Radiobutton(bar, text=label, value=key, variable=self.tool, style='Toolbutton',
                                command=self._tool_changed)
            b.pack(side='left', padx=(0, 2))
            Tooltip(b, tip)
        ttk.Label(bar, text='  Pincel', style='Muted.TLabel').pack(side='left')
        self.brush = tk.IntVar(value=1)
        ttk.Spinbox(bar, from_=1, to=32, width=3, textvariable=self.brush).pack(side='left', padx=(4, 0))
        help_icon(bar, 'Tamanho do pincel e da borracha, em pixels da imagem (quadrado).').pack(side='left')
        self.transparent_sel = tk.BooleanVar(value=True)
        b = ttk.Checkbutton(bar, text='Transparente', variable=self.transparent_sel, command=self._render)
        b.pack(side='left', padx=(10, 0))
        Tooltip(b, 'Seleção transparente: ao mover um pedaço, os pixels transparentes dele não '
                   'cobrem o que está embaixo. Desmarque para o pedaço cobrir tudo.')

        self.text_bar = ttk.Frame(self, style='Panel.TFrame')
        self.text_var = tk.StringVar(value='Texto')
        self.text_size = tk.IntVar(value=12)
        self.text_outline = tk.BooleanVar(value=True)
        ttk.Label(self.text_bar, text='Texto', style='Muted.TLabel').pack(side='left')
        ttk.Entry(self.text_bar, textvariable=self.text_var, width=28).pack(side='left', padx=4)
        ttk.Label(self.text_bar, text='Tamanho', style='Muted.TLabel').pack(side='left', padx=(6, 0))
        ttk.Spinbox(self.text_bar, from_=6, to=96, width=4, textvariable=self.text_size).pack(side='left', padx=4)
        ttk.Checkbutton(self.text_bar, text='Contorno', variable=self.text_outline).pack(side='left', padx=(6, 0))
        help_icon(self.text_bar, 'Contorno de 1 pixel na cor secundária (botão direito numa cor da '
                                 'paleta). O texto usa a cor principal.').pack(side='left')
        ttk.Button(self.text_bar, text='Aplicar texto', style='Tool.TButton',
                   command=self.commit).pack(side='left', padx=(8, 0))
        for var in (self.text_var, self.text_size, self.text_outline):
            var.trace_add('write', lambda *_: self._retext())

        bottom = ttk.Frame(self, style='Panel.TFrame')
        bottom.pack(fill='x', side='bottom', pady=(6, 0), before=self.canvas.master)
        for text, cmd, tip in (('↶ Desfazer', self.do_undo, 'Ctrl+Z'), ('↷ Refazer', self.do_redo, 'Ctrl+Y'),
                               ('Colar imagem…', self.paste_file,
                                'Traz uma imagem de fora já convertida para a paleta deste gráfico.')):
            b = ttk.Button(bottom, text=text, style='Tool.TButton', command=cmd)
            b.pack(side='left', padx=(0, 4))
            Tooltip(b, tip)
        # edit state lives in the preview's title bar, where there is room
        self.state_label = ttk.Label(self.title.master, text='', style='Warn.TLabel')
        self.state_label.pack(side='left', padx=(12, 0), after=self.zoom_label)
        for text, cmd, style, tip in (
                ('Aplicar edição', self.apply, 'Success.TButton',
                 'Guarda a edição numa cópia de trabalho da tool e põe o gráfico na lista de '
                 'reinserção. Não mexe no PNG do dump nem no PNG que você importou.'),
                ('Salvar no PNG', self.save, 'TButton',
                 'Grava a edição POR CIMA do PNG carregado (o que você importou, ou o do dump se '
                 'ainda não houver outro) e põe o gráfico na lista de reinserção.'),
                ('Descartar', self.discard, 'TButton', 'Volta ao estado antes das edições não salvas.')):
            b = ttk.Button(bottom, text=text, style=style, command=cmd)
            b.pack(side='right', padx=(6, 0))
            Tooltip(b, tip)

        # palette column, to the right of the canvas
        body = self.canvas.master
        self.pal_frame = ttk.Frame(body, style='Panel.TFrame', padding=(8, 0, 0, 0))
        self.pal_frame.pack(side='right', fill='y', before=self.ys)
        head = ttk.Frame(self.pal_frame, style='Panel.TFrame')
        head.pack(fill='x')
        ttk.Label(head, text='Paleta', style='Section.TLabel').pack(side='left')
        help_icon(head, 'Clique: cor principal (lápis/texto). Botão direito: cor secundária '
                        '(contorno do texto). Botão direito na imagem = conta-gotas.').pack(side='left')
        self.pal_canvas = tk.Canvas(self.pal_frame, width=16 * 13 + 4, height=60, bg=theme.PANEL,
                                    highlightthickness=0)
        self.pal_canvas.pack(pady=(6, 0))
        self.pal_canvas.bind('<ButtonPress-1>', lambda e: self._pal_click(e, True))
        self.pal_canvas.bind('<ButtonPress-3>', lambda e: self._pal_click(e, False))
        self.swatch = tk.Canvas(self.pal_frame, width=60, height=34, bg=theme.PANEL, highlightthickness=0)
        self.swatch.pack(pady=(6, 0), anchor='w')
        self.pal_note = ttk.Label(self.pal_frame, text='', style='Muted.TLabel', wraplength=210,
                                  justify='left')
        self.pal_note.pack(fill='x', pady=(4, 0))

    def _tool_changed(self):
        self.commit()
        if self.tool.get() == 'text':
            self.text_bar.pack(fill='x', before=self.canvas.master, pady=(0, 6))
        else:
            self.text_bar.pack_forget()
        cursor = {'picker': 'tcross', 'select': 'crosshair', 'text': 'xterm'}.get(self.tool.get(), 'pencil')
        self.canvas.configure(cursor=cursor)

    # ------------------------------------------------------------ image in/out
    def set_image(self, image, boxes=None, keep_view=False, editable=True, bpp=None, dirty=False):
        """`dirty`: the image carries edits that were never saved (restored
        from the Insert tab's pending list)."""
        self._load(image, keep_view, editable, bpp)
        if dirty and self.editable:
            self._set_dirty(True)

    def _load(self, image, keep_view, editable, bpp):
        self.floating = self.sel = None
        self.undo, self.redo = [], []
        if image is None:
            self.idx = None
            super().set_image(None)
            self._set_dirty(False)
            self._draw_palette()
            return
        self.editable = editable and image.mode == 'P'
        if image.mode == 'P':
            self.idx = np.array(image, dtype=np.uint8)
            pal = image.getpalette() or []
            pal = (pal + [0] * 768)[:768]
            self.palette = np.array(pal, dtype=np.uint8).reshape(256, 3)
            self.transparency = image.info.get('transparency')
            self.n_colors = 16 if int(self.idx.max()) < 16 and (bpp or 4) == 4 else 256
            self.subpal = bpp == 4 and self.n_colors > 16
        else:
            self.idx = None
        self._set_dirty(False)
        self._draw_palette()
        super().set_image(image if self.idx is None else self._composed(), keep_view=keep_view)
        if not self.editable:
            self.state_label.configure(
                text='Somente visualização' + ('' if image.mode == 'P'
                                               else ' — imagem em cor direta (sem paleta indexada)'))

    def get_image(self):
        self.commit()
        im = Image.fromarray(self.idx, 'P')
        im.putpalette(self.palette.reshape(-1).tolist())
        if self.transparency is not None:
            im.info['transparency'] = self.transparency
        return im

    def _set_dirty(self, value):
        self.dirty = value
        if self.editable:
            self.state_label.configure(text='● edição não aplicada' if value else 'Pronto para editar')
        if self.on_dirty:
            self.on_dirty(value)

    def apply(self):
        self._hand_over(self.on_apply)

    def save(self):
        self._hand_over(self.on_save)

    def _hand_over(self, callback):
        """Give the edited image to `callback`; a callback returning False
        (e.g. the user cancelled an overwrite) keeps the edits pending."""
        if self.idx is None or not self.editable or callback is None:
            return
        if callback(self.get_image()) is False:
            return
        self.undo, self.redo = [], []          # the saved file is the new baseline
        self._set_dirty(False)

    def discard(self):
        if self.undo:
            self.idx = self.undo[0]
        self.floating = self.sel = None
        self.undo, self.redo = [], []
        self._set_dirty(False)
        self._render()
        if self.on_discard:
            self.on_discard()

    # ------------------------------------------------------------ rendering
    def _lut(self):
        lut = np.empty((256, 4), dtype=np.uint8)
        lut[:, :3] = self.palette
        lut[:, 3] = 255
        t = self.transparency
        if isinstance(t, (bytes, bytearray)):
            lut[:len(t), 3] = np.frombuffer(bytes(t), dtype=np.uint8)
        elif isinstance(t, int):
            lut[t, 3] = 0
        return lut

    def _composed(self):
        shown = self.idx
        if self.floating:
            shown = shown.copy()
            self._stamp(shown, self.floating)
        return Image.fromarray(self._lut()[shown], 'RGBA')

    def _render(self):
        if self.idx is None:
            return
        self.image = self._composed()
        self.redraw()

    def redraw(self):
        super().redraw()
        if self.idx is None:
            return
        s, c = self.scale, self.canvas
        for rect, color in ((self.sel, theme.ACCENT_2),
                            (self._float_rect(), theme.WARNING)):
            if rect:
                x0, y0, x1, y1 = rect
                c.create_rectangle(x0 * s, y0 * s, x1 * s, y1 * s, outline=color, dash=(4, 3))

    def _float_rect(self):
        f = self.floating
        if not f:
            return None
        h, w = f['idx'].shape
        return f['x'], f['y'], f['x'] + w, f['y'] + h

    # ------------------------------------------------------------ pixel writes
    def _write(self, region, values):
        """values into region, keeping each pixel's palette row in 4bpp mode."""
        if self.subpal:
            return (region & 0xF0) | (values & 0x0F)
        return values

    def _erased(self, region):
        return region & 0xF0 if self.subpal else np.zeros_like(region)

    def _stamp(self, target, f):
        H, W = target.shape
        h, w = f['idx'].shape
        x0, y0 = max(0, f['x']), max(0, f['y'])
        x1, y1 = min(W, f['x'] + w), min(H, f['y'] + h)
        if x1 <= x0 or y1 <= y0:
            return
        src = f['idx'][y0 - f['y']:y1 - f['y'], x0 - f['x']:x1 - f['x']]
        mask = f['mask'][y0 - f['y']:y1 - f['y'], x0 - f['x']:x1 - f['x']]
        if f['kind'] == 'sel' and not self.transparent_sel.get():
            mask = np.ones_like(mask)
        dst = target[y0:y1, x0:x1]
        dst[mask] = self._write(dst, src)[mask] if f['kind'] != 'sel' else src[mask]

    def _brush(self, x, y):
        n = max(1, int(self.brush.get() or 1))
        x0, y0 = x - (n - 1) // 2, y - (n - 1) // 2
        H, W = self.idx.shape
        xs, ys = slice(max(0, x0), min(W, x0 + n)), slice(max(0, y0), min(H, y0 + n))
        region = self.idx[ys, xs]
        if self.tool.get() == 'eraser':
            region[...] = self._erased(region)
        else:
            region[...] = self._write(region, np.full_like(region, self.primary))

    def _line(self, a, b):
        (x0, y0), (x1, y1) = a, b
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            self._brush(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))

    # ------------------------------------------------------------ undo
    def _push(self):
        self.undo.append(self.idx.copy())
        del self.undo[:-UNDO_LIMIT]
        self.redo.clear()
        self._set_dirty(True)

    def do_undo(self):
        if self.floating:
            self.cancel()
            return
        if self.undo:
            self.redo.append(self.idx)
            self.idx = self.undo.pop()
            self._set_dirty(bool(self.undo))
            self._render()

    def do_redo(self):
        if self.redo:
            self.undo.append(self.idx)
            self.idx = self.redo.pop()
            self._set_dirty(True)
            self._render()

    # ------------------------------------------------------------ mouse
    def _pos(self, e):
        s = self.scale or 1
        return int(self.canvas.canvasx(e.x) // s), int(self.canvas.canvasy(e.y) // s)

    def _inside(self, rect, x, y):
        return rect is not None and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

    def _down(self, e):
        self.canvas.focus_set()
        if self.idx is None or not self.editable:
            return
        x, y = self._pos(e)
        tool = self.tool.get()
        if tool in ('pencil', 'eraser'):
            self._push()
            self._last = (x, y)
            self._brush(x, y)
            self._render()
        elif tool == 'picker':
            self._pick(x, y, True)
        elif tool == 'select':
            if self._inside(self._float_rect(), x, y):
                self._drag = ('move', x, y)
            elif self._inside(self.sel, x, y):
                self._lift()
                self._drag = ('move', x, y)
            else:
                self.commit()
                self.sel = None
                self._drag = ('rect', x, y)
        elif tool == 'text':
            if self._inside(self._float_rect(), x, y):
                self._drag = ('move', x, y)
            else:
                self.commit()
                self._new_text(x, y)
                self._drag = ('move', x, y)

    def _move(self, e):
        if self.idx is None or not self.editable:
            return
        x, y = self._pos(e)
        tool = self.tool.get()
        if tool in ('pencil', 'eraser') and self._last:
            self._line(self._last, (x, y))
            self._last = (x, y)
            self._render()
        elif self._drag and self._drag[0] == 'move' and self.floating:
            _, px, py = self._drag
            self.floating['x'] += x - px
            self.floating['y'] += y - py
            self._drag = ('move', x, y)
            self._render()
        elif self._drag and self._drag[0] == 'rect':
            _, sx, sy = self._drag
            H, W = self.idx.shape
            x0, x1 = max(0, min(sx, x)), min(W, max(sx, x) + 1)
            y0, y1 = max(0, min(sy, y)), min(H, max(sy, y) + 1)
            self.sel = (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None
            self.redraw()

    def _up(self, _e):
        self._last = None
        self._drag = None
        if self.sel and (self.sel[2] - self.sel[0] < 1 or self.sel[3] - self.sel[1] < 1):
            self.sel = None
            self.redraw()

    def _drag_pan(self, e):
        self.canvas.scan_dragto(e.x, e.y, gain=1)
        self.redraw()

    def _hover(self, e):
        c = self.canvas
        c.delete('cursor')
        if self.idx is None or self.tool.get() not in ('pencil', 'eraser'):
            return
        x, y = self._pos(e)
        n, s = max(1, int(self.brush.get() or 1)), self.scale
        x0, y0 = x - (n - 1) // 2, y - (n - 1) // 2
        c.create_rectangle(x0 * s, y0 * s, (x0 + n) * s, (y0 + n) * s, outline='#ffffff', tags='cursor')

    # ------------------------------------------------------------ colours
    def _pick_event(self, e):
        if self.idx is not None:
            self._pick(*self._pos(e), True)

    def _pick(self, x, y, primary):
        H, W = self.idx.shape
        if 0 <= x < W and 0 <= y < H:
            self._set_color(int(self.idx[y, x]), primary)

    def _set_color(self, index, primary):
        if primary:
            self.primary = index
        else:
            self.secondary = index
        self._draw_palette()
        self._retext()

    def _pal_click(self, e, primary):
        if self.palette is None:
            return
        col, row = int((e.x - 2) // 13), int((e.y - 2) // 13)
        i = row * 16 + col
        if 0 <= col < 16 and 0 <= i < self.n_colors:
            self._set_color(i, primary)

    def _draw_palette(self):
        c = self.pal_canvas
        c.delete('all')
        if self.idx is None or self.palette is None:
            self.pal_note.configure(text='Sem paleta indexada.')
            return
        n = self.n_colors
        rows = (n + 15) // 16
        c.configure(height=rows * 13 + 4)
        for i in range(n):
            x, y = (i % 16) * 13 + 2, (i // 16) * 13 + 2
            rgb = '#%02x%02x%02x' % tuple(int(v) for v in self.palette[i])
            outline = '#ffffff' if i == self.primary else '#ffb454' if i == self.secondary else ''
            c.create_rectangle(x, y, x + 11, y + 11, fill=rgb, outline=outline, width=2 if outline else 0)
        s = self.swatch
        s.delete('all')
        for idx, (x, y) in ((self.secondary, (16, 12)), (self.primary, (2, 2))):   # primary on top
            rgb = '#%02x%02x%02x' % tuple(int(v) for v in self.palette[idx])
            s.create_rectangle(x, y, x + 20, y + 20, fill=rgb, outline='#ffffff')
        note = f'Principal: índice {self.primary}  •  secundária: {self.secondary}'
        if self.subpal:
            note += ('\n4bpp com várias sub-paletas: cada tile usa só as 16 cores da própria '
                     'linha — ao pintar, a linha do pixel é mantida.')
        self.pal_note.configure(text=note)

    # ------------------------------------------------------------ floating elements
    def _lift(self):
        x0, y0, x1, y1 = self.sel
        self._push()
        region = self.idx[y0:y1, x0:x1]
        piece = region.copy()
        mask = (piece & 0x0F) != 0 if self.subpal else piece != 0
        region[...] = self._erased(region)
        self.floating = {'kind': 'sel', 'idx': piece, 'mask': mask, 'x': x0, 'y': y0}
        self.sel = None

    def _new_text(self, x, y):
        text = self.text_var.get()
        if not text.strip():
            return
        self.floating = {'kind': 'text', 'x': x, 'y': y}
        self._retext()

    def _retext(self):
        f = self.floating
        if not f or f['kind'] != 'text' or self.idx is None:
            return
        try:
            size = max(4, int(self.text_size.get()))
        except (tk.TclError, ValueError):
            return
        codes, mask = render_text(self.text_var.get() or ' ', size, self.text_outline.get())
        idx = np.zeros(codes.shape, np.uint8)
        idx[codes == 1] = self.primary
        idx[codes == 2] = self.secondary
        f.update(idx=idx, mask=mask)
        self._render()

    def commit(self):
        if self.floating and self.idx is not None:
            if self.floating['kind'] != 'sel':
                self._push()
            self._stamp(self.idx, self.floating)
            self.floating = None
            self._set_dirty(True)
            self._render()

    def cancel(self):
        if self.floating:
            if self.floating['kind'] == 'sel' and self.undo:
                self.idx = self.undo.pop()
            self.floating = None
        self.sel = None
        self._render()

    def delete_selection(self):
        if self.floating and self.floating['kind'] == 'sel':
            self.floating = None
            self._render()
        elif self.sel and self.idx is not None:
            x0, y0, x1, y1 = self.sel
            self._push()
            region = self.idx[y0:y1, x0:x1]
            region[...] = self._erased(region)
            self._render()

    def copy(self):
        src = self.floating
        if src:
            self.clip = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in src.items()}
        elif self.sel and self.idx is not None:
            x0, y0, x1, y1 = self.sel
            piece = self.idx[y0:y1, x0:x1].copy()
            mask = (piece & 0x0F) != 0 if self.subpal else piece != 0
            self.clip = {'kind': 'sel', 'idx': piece, 'mask': mask}

    def paste(self):
        if not self.clip or self.idx is None or not self.editable:
            return
        self.commit()
        self._push()
        x = int(self.canvas.canvasx(0) // (self.scale or 1))
        y = int(self.canvas.canvasy(0) // (self.scale or 1))
        self.floating = dict(self.clip, kind='sel', x=max(0, x), y=max(0, y))
        self.floating['idx'] = self.floating['idx'].copy()
        self.tool.set('select')
        self._tool_changed_quiet()
        self._render()

    def _tool_changed_quiet(self):
        self.text_bar.pack_forget()
        self.canvas.configure(cursor='crosshair')

    def paste_file(self):
        """A picture from disk becomes a floating element, converted to the
        CURRENT palette (nearest colour; in 4bpp only the primary colour's
        row, so the result is representable in a tile)."""
        if self.idx is None or not self.editable:
            return
        path = filedialog.askopenfilename(title='Imagem para colar',
                                          filetypes=[('Imagens', '*.png *.bmp *.gif *.jpg'), ('Todos', '*.*')])
        if not path:
            return
        try:
            with Image.open(path) as im:
                rgba = np.array(im.convert('RGBA')).astype(np.int32)
        except Exception as exc:
            messagebox.showerror('Colar imagem', str(exc))
            return
        if self.subpal or self.n_colors == 16:
            base = self.primary & 0xF0 if self.subpal else 0
            choices = np.arange(base + 1, base + 16)
        else:
            choices = np.arange(1, 256)
        pal = self.palette[choices].astype(np.int32)
        rgb = rgba[..., :3].reshape(-1, 3)
        nearest = np.empty(len(rgb), np.uint8)
        for i in range(0, len(rgb), 4096):            # chunks: pixels x colours stays small
            d = ((rgb[i:i + 4096, None, :] - pal[None]) ** 2).sum(-1)
            nearest[i:i + 4096] = choices[d.argmin(1)]
        idx = nearest.reshape(rgba.shape[:2])
        mask = rgba[..., 3] >= 128
        self.commit()
        self._push()
        self.floating = {'kind': 'paste', 'idx': idx, 'mask': mask, 'x': 0, 'y': 0}
        self.tool.set('select')
        self._tool_changed_quiet()
        self._render()

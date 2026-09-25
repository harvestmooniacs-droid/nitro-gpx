"""Widgets of the redesigned interface.

ZoomView      preview: wheel zoom anchored at the cursor, drag to pan,
              scrollbars, checkerboard behind transparency, tile grid and
              OAM-box overlays. Only the VISIBLE part of the image is ever
              scaled, so a 16x zoom on a 512x512 sheet stays instant.
PaletteChooser identified palette + every other palette of the game in a
              dropdown; picking one re-renders the preview live.
HealthView    % integrity / identified / 2D / 3D of the loaded source.
GameLog       compatibility list (read from GUIA.md, like Dolphin's list)
              with the loaded game highlighted.
RomTree       Tinke-like file tree of the ROM, lazily expanded; after an
              analysis each file lists the graphics found inside it.
"""
import json
import os
import re
import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from . import theme


class Card(ttk.Frame):
    def __init__(self, parent, title=None, **kwargs):
        super().__init__(parent, style='Panel.TFrame', padding=10, **kwargs)
        if title:
            ttk.Label(self, text=title, style='Section.TLabel').pack(fill='x', pady=(0, 8))


def wrap_label(parent, text, style='Muted.TLabel', **kwargs):
    """A label whose wraplength tracks its own width instead of a fixed
    guess - a label packed at a guessed wraplength clips/overflows its
    panel whenever the real column ends up narrower (e.g. the right column
    on a smaller window), which is what was cutting off the manifest.json
    warning under Dump. Re-wraps on every resize instead."""
    label = ttk.Label(parent, text=text, style=style, justify='left', **kwargs)
    label.bind('<Configure>', lambda e: label.configure(wraplength=max(60, e.width)))
    return label


# ------------------------------------------------------------------ preview
def _checker(w, h, size=8):
    img = Image.new('RGB', (w, h), (38, 42, 54))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, size):
        for x in range((y // size) % 2 * size, w, size * 2):
            draw.rectangle((x, y, x + size - 1, y + size - 1), fill=(48, 53, 67))
    return img


class ZoomView(ttk.Frame):
    MIN, MAX = 0.25, 32.0

    def __init__(self, parent, on_toggle_oam=None, show_oam_toggle=True):
        super().__init__(parent, style='Panel.TFrame', padding=8)
        bar = ttk.Frame(self, style='Panel.TFrame')
        bar.pack(fill='x', pady=(0, 6))
        self.title = ttk.Label(bar, text='Preview', style='Section.TLabel')
        self.title.pack(side='left')
        for text, cmd in (('－', lambda: self.zoom_by(1 / 1.25)), ('＋', lambda: self.zoom_by(1.25)),
                          ('1:1', lambda: self.set_zoom(1)), ('Ajustar', self.fit)):
            ttk.Button(bar, text=text, style='Tool.TButton', command=cmd).pack(side='left', padx=(6, 0))
        self.zoom_label = ttk.Label(bar, text='', style='Muted.TLabel', width=7)
        self.zoom_label.pack(side='left', padx=8)
        self.grid_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='Grade 8×8', variable=self.grid_var,
                        command=self.redraw).pack(side='right', padx=(8, 0))
        self.oam_var = tk.BooleanVar(value=False)
        self.boxes_var = tk.BooleanVar(value=True)
        if show_oam_toggle:
            ttk.Checkbutton(bar, text='Caixas OAM', variable=self.boxes_var,
                            command=self.redraw).pack(side='right', padx=(8, 0))
            ttk.Checkbutton(bar, text='Modo OAM (peças isoladas)', variable=self.oam_var,
                            command=lambda: on_toggle_oam and on_toggle_oam(self.oam_var.get())
                            ).pack(side='right')
        self.size_label = ttk.Label(bar, text='', style='Muted.TLabel')
        self.size_label.pack(side='right', padx=10)

        body = ttk.Frame(self, style='Panel.TFrame')
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, bg=theme.CANVAS, highlightthickness=0, cursor='fleur')
        self.xs = ttk.Scrollbar(body, orient='horizontal', command=self._xview)
        self.ys = ttk.Scrollbar(body, orient='vertical', command=self._yview)
        self.canvas.configure(xscrollcommand=self.xs.set, yscrollcommand=self.ys.set)
        self.xs.pack(side='bottom', fill='x')
        self.ys.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.image, self.photo, self.scale, self.boxes = None, None, 1.0, []
        self._fit_pending = True
        c = self.canvas
        c.bind('<Configure>', lambda _e: self._on_configure())
        c.bind('<MouseWheel>', self._wheel)                         # Windows / macOS
        c.bind('<Button-4>', lambda e: self._wheel(e, 120))          # X11
        c.bind('<Button-5>', lambda e: self._wheel(e, -120))
        c.bind('<ButtonPress-1>', lambda e: c.scan_mark(e.x, e.y))
        c.bind('<B1-Motion>', self._drag)
        c.bind('<Double-Button-1>', lambda _e: self.fit())

    # ---------------------------------------------------------- public
    def set_image(self, image, boxes=None, keep_view=False):
        self.image = image.convert('RGBA') if image is not None else None
        self.boxes = boxes or []
        if self.image is None:
            self.canvas.delete('all')
            self.canvas.create_text(24, 24, anchor='nw', fill=theme.MUTED, font=theme.FONT,
                                    text='Selecione um gráfico para visualizar.\n'
                                         'Roda do mouse: zoom no cursor  •  arrastar: mover  •  '
                                         'duplo clique: ajustar')
            self.size_label.configure(text='')
            return
        self.size_label.configure(text=f'{self.image.width} × {self.image.height} px')
        if keep_view:
            self._update_region()
            self.redraw()
        else:
            self.fit()

    def fit(self):
        if self.image is None:
            return
        cw, ch = max(64, self.canvas.winfo_width() - 16), max(64, self.canvas.winfo_height() - 16)
        if cw <= 64 and ch <= 64:          # not laid out yet: retry once it is
            self._fit_pending = True
            return
        self._fit_pending = False
        self.set_zoom(min(cw / self.image.width, ch / self.image.height, 8))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def set_zoom(self, value, anchor=None):
        if self.image is None:
            return
        old = self.scale
        value = max(self.MIN, min(self.MAX, value))
        if anchor is None:
            anchor = (self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)
        ax, ay = anchor
        # image coordinate currently under the anchor point
        ix = (self.canvas.canvasx(ax)) / old
        iy = (self.canvas.canvasy(ay)) / old
        self.scale = value
        self._update_region()
        W, H = self.image.width * value, self.image.height * value
        # scroll so the same image point stays under the cursor
        if W > 0:
            self.canvas.xview_moveto(max(0, (ix * value - ax) / W))
        if H > 0:
            self.canvas.yview_moveto(max(0, (iy * value - ay) / H))
        self.zoom_label.configure(text=f'{value * 100:.0f}%')
        self.redraw()

    def zoom_by(self, factor, anchor=None):
        self.set_zoom(self.scale * factor, anchor)

    # ---------------------------------------------------------- internals
    def _on_configure(self):
        if self._fit_pending and self.image is not None:
            self.fit()
        else:
            self.redraw()

    def _update_region(self):
        W = int(self.image.width * self.scale)
        H = int(self.image.height * self.scale)
        self.canvas.configure(scrollregion=(0, 0, max(W, 1), max(H, 1)))

    def _wheel(self, event, delta=None):
        d = delta if delta is not None else event.delta
        self.zoom_by(1.25 if d > 0 else 1 / 1.25, (event.x, event.y))

    def _drag(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self.redraw()

    def _xview(self, *args):
        self.canvas.xview(*args)
        self.redraw()

    def _yview(self, *args):
        self.canvas.yview(*args)
        self.redraw()

    def redraw(self):
        c = self.canvas
        c.delete('all')
        if self.image is None:
            return
        s = self.scale
        cw, ch = c.winfo_width(), c.winfo_height()
        x0, y0 = c.canvasx(0), c.canvasy(0)
        # visible window in IMAGE coordinates (a pixel of margin for rounding)
        ix0, iy0 = max(0, int(x0 / s)), max(0, int(y0 / s))
        ix1 = min(self.image.width, int((x0 + cw) / s) + 2)
        iy1 = min(self.image.height, int((y0 + ch) / s) + 2)
        if ix1 <= ix0 or iy1 <= iy0:
            return
        crop = self.image.crop((ix0, iy0, ix1, iy1))
        w, h = max(1, int((ix1 - ix0) * s)), max(1, int((iy1 - iy0) * s))
        scaled = crop.resize((w, h), Image.Resampling.NEAREST)
        base = _checker(w, h)
        base.paste(scaled, (0, 0), scaled)
        self.photo = ImageTk.PhotoImage(base)
        ox, oy = ix0 * s, iy0 * s
        c.create_image(ox, oy, anchor='nw', image=self.photo)
        if self.grid_var.get() and s >= 3:
            gx0 = (ix0 // 8) * 8
            for gx in range(gx0, ix1 + 1, 8):
                c.create_line(gx * s, oy, gx * s, oy + h, fill='#3a4460')
            gy0 = (iy0 // 8) * 8
            for gy in range(gy0, iy1 + 1, 8):
                c.create_line(ox, gy * s, ox + w, gy * s, fill='#3a4460')
        if self.boxes and self.boxes_var.get():
            for bx, by, bw, bh in self.boxes:
                if bx + bw < ix0 or by + bh < iy0 or bx > ix1 or by > iy1:
                    continue
                c.create_rectangle(bx * s, by * s, (bx + bw) * s, (by + bh) * s,
                                   outline=theme.ACCENT_2, dash=(3, 2))


# ------------------------------------------------------------------ palette
class PaletteChooser(Card):
    """Identified palette + every other palette found in the game."""

    def __init__(self, parent, on_change):
        super().__init__(parent, 'Paleta')
        self.on_change = on_change
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(self, textvariable=self.choice, state='readonly')
        self.combo.pack(fill='x')
        self.combo.bind('<<ComboboxSelected>>', lambda _e: self._picked())
        self.canvas = tk.Canvas(self, height=132, bg=theme.PANEL, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=(8, 0))
        self.note = ttk.Label(self, text='', style='Muted.TLabel')
        self.note.pack(fill='x')
        self.options = []

    def set_options(self, options, current):
        """options: [(label, loc)]; current: loc of the resource's palette."""
        import json
        self.options = options
        labels = [label for label, _loc in options]
        self.combo.configure(values=labels)
        key = json.dumps(current) if current else None
        for label, loc in options:
            if key and json.dumps(loc) == key:
                self.choice.set(label)
                return
        self.choice.set('(sem paleta — prévia em cinza)' if current is None else '(paleta identificada)')

    def _picked(self):
        for label, loc in self.options:
            if label == self.choice.get():
                self.on_change(loc)
                return

    def show(self, image, grey=False):
        c = self.canvas
        c.delete('all')
        if image is None or image.mode != 'P' or image.getpalette() is None:
            self.note.configure(text='Imagem sem paleta indexada (cor direta)')
            return
        pal = image.getpalette()
        used = max(image.getdata(), default=0)
        colors = 16 if used < 16 else 256
        cell = 16 if colors == 16 else 8
        for i in range(colors):
            x, y = (i % 16) * (cell + 2) + 2, (i // 16) * (cell + 2) + 2
            rgb = tuple(pal[i * 3:i * 3 + 3]) if i * 3 + 2 < len(pal) else (0, 0, 0)
            c.create_rectangle(x, y, x + cell, y + cell, fill='#%02x%02x%02x' % rgb, outline='')
        self.note.configure(text=f'{colors} cores' + (' • sem paleta no jogo: prévia em cinza' if grey else ''))


# ------------------------------------------------------------------ health
class HealthView(Card):
    ROWS = (('integrity', 'Integridade', 'recursos montados com paleta e encaixe válidos'),
            ('identified', 'Arquivos identificados', 'arquivos com gráfico / arquivos que podem ter'),
            ('g2d', 'Gráficos 2D', 'parte 2D dos recursos'),
            ('g3d', 'Gráficos 3D', 'texturas de modelos 3D'))

    def __init__(self, parent):
        super().__init__(parent, 'Saúde dos gráficos')
        head = ttk.Frame(self, style='Panel.TFrame')
        head.pack(fill='x', pady=(0, 6))
        self.game_icon_label = ttk.Label(head, style='Panel.TLabel')
        self.game_icon_label.pack(side='left', padx=(0, 6))
        self.game_title = ttk.Label(head, text='Nenhuma ROM carregada', style='Panel.TLabel')
        self.game_title.pack(side='left')
        self._game_photo = None
        self.bars = {}
        for key, label, hint in self.ROWS:
            row = ttk.Frame(self, style='Panel.TFrame')
            row.pack(fill='x', pady=3)
            top = ttk.Frame(row, style='Panel.TFrame')
            top.pack(fill='x')
            ttk.Label(top, text=label, style='Panel.TLabel').pack(side='left')
            value = ttk.Label(top, text='—', style='Section.TLabel')
            value.pack(side='right')
            bar = ttk.Progressbar(row, maximum=100)
            bar.pack(fill='x', pady=(2, 0))
            self.bars[key] = (bar, value)
        self.note = ttk.Label(self, text='Carregue a ROM e clique em Buscar/Analisar gráficos.',
                              style='Muted.TLabel', wraplength=260, justify='left')
        self.note.pack(fill='x', pady=(6, 0))

    def set_game(self, icon, title):
        self._game_photo = ImageTk.PhotoImage(icon) if icon is not None else None
        self.game_icon_label.configure(image=self._game_photo or '')
        self.game_title.configure(text=title or 'Nenhuma ROM carregada')

    def set_values(self, values, note=''):
        for key, (bar, label) in self.bars.items():
            v = values.get(key)
            if v is None:
                bar['value'] = 0
                label.configure(text='—')
                continue
            bar['value'] = v
            bar.configure(style=('Good' if v >= 85 else 'Mid' if v >= 50 else 'Bad') + '.Horizontal.TProgressbar')
            label.configure(text=f'{v:.0f}%')
        self.note.configure(text=note)


# ------------------------------------------------------------------ game log
STATUS = (('✅', 'Perfeito', theme.SUCCESS), ('🟢', 'Ótimo', '#8be0b5'), ('🟡', 'Parcial', theme.WARNING),
          ('🟠', 'Limitado', '#ff9f5a'), ('❌', 'Não funciona', theme.DANGER))


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def load_compat(guia_path):
    """Rows of the per-game tables of GUIA.md section 3 (single source of
    truth - the same table the documentation keeps up to date)."""
    rows = []
    try:
        text = Path(guia_path).read_text(encoding='utf-8')
    except OSError:
        return rows
    start = text.find('## 3.')
    end = text.find('### Validação feita', start)
    block = text[start:end if end > 0 else None] if start >= 0 else ''
    for line in block.splitlines():
        if not line.startswith('| ') or line.startswith('| Jogo') or line.startswith('|---'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) < 5:
            continue
        name, _how, count, integrity, status = cells[:5]
        label, color = 'Parcial', theme.WARNING
        for emoji, text_label, col in STATUS:
            if status.startswith(emoji) or emoji in status[:3]:
                label, color = text_label, col
                break
        detail = re.sub(r'[✅🟢🟡🟠❌·]', '', status).strip(' ·')
        rows.append({'name': name, 'resources': count, 'integrity': integrity,
                     'status': label, 'color': color, 'detail': detail})
    return rows


class GameLog(Card):
    def __init__(self, parent, guia_path):
        super().__init__(parent, 'Jogos Suportados (testados)')
        self.rows = load_compat(guia_path)
        cols = (('status', 'Status', 92), ('integrity', 'Integr.', 60))
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show='tree headings', height=9)
        self.tree.heading('#0', text='Jogo')
        self.tree.column('#0', width=170)
        for key, label, width in cols:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor='center')
        scroll = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        for _emoji, label, color in STATUS:
            self.tree.tag_configure(label, foreground=color)
        self.tree.tag_configure('current', background='#2b2352')
        for i, row in enumerate(sorted(self.rows, key=lambda r: r['name'].lower())):
            self.tree.insert('', 'end', iid=f'g{i}', text=row['name'],
                             values=(row['status'], row['integrity']), tags=(row['status'],))
            row['iid'] = f'g{i}'
        self.detail = ttk.Label(self, text=f'{len(self.rows)} jogos testados', style='Muted.TLabel',
                                wraplength=300, justify='left')
        self.detail.pack(fill='x', pady=(6, 0))
        self.tree.bind('<<TreeviewSelect>>', self._select)

    def _select(self, _e=None):
        sel = self.tree.selection()
        row = next((r for r in self.rows if r.get('iid') in sel), None)
        if row:
            self.detail.configure(text=f"{row['name']}: {row['resources']} recursos • {row['detail']}")

    def highlight(self, *names):
        keys = [_norm(n) for n in names if n]
        best = None
        for row in self.rows:
            rn = _norm(row['name'])
            if any(k and (rn in k or k in rn) for k in keys):
                best = row
                break
        for row in self.rows:
            tags = [row['status']] + (['current'] if row is best else [])
            self.tree.item(row['iid'], tags=tags)
        if best:
            self.tree.see(best['iid'])
            self.tree.selection_set(best['iid'])
        else:
            self.detail.configure(text='Jogo carregado ainda não está na lista de testados.')
        return best


# ------------------------------------------------------------------ ROM tree
def _human(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def resource_file(res):
    """The ROM file a resource lives in (first 'file' step of its pixels)."""
    loc = res.get('char') or []
    if loc and loc[0][0] == 'file':
        return loc[0][1]
    if loc and loc[0][0] == 'banner':
        return '(banner do cartucho)'
    if loc and loc[0][0] == 'arm':
        return f'arm{loc[0][1]}.bin'
    return res.get('owner') or res['name']


class RomTree(Card):
    """Folder tree of the ROM (lazy); resources hang under their file."""

    def __init__(self, parent, on_resource=None, on_file=None, title='Estrutura da ROM'):
        super().__init__(parent, title)
        self.on_resource, self.on_file = on_resource, on_file
        ttk.Label(self, text='Buscar por nome de arquivo ou pasta:', style='Muted.TLabel'
                  ).pack(fill='x', pady=(0, 2))
        filt = ttk.Frame(self, style='Panel.TFrame')
        filt.pack(fill='x', pady=(0, 6))
        self.search = tk.StringVar()
        entry = ttk.Entry(filt, textvariable=self.search)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<KeyRelease>', lambda _e: self.rebuild())
        self.only_gfx = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text='Mostrar só o que tem gráfico', variable=self.only_gfx,
                        command=self.rebuild).pack(side='left', padx=(6, 0))
        filt2 = ttk.Frame(self, style='Panel.TFrame')
        filt2.pack(fill='x', pady=(0, 6))
        self.show_2d = tk.BooleanVar(value=True)
        self.show_3d = tk.BooleanVar(value=True)
        ttk.Checkbutton(filt2, text='Gráficos 2D', variable=self.show_2d,
                        command=self.rebuild).pack(side='left')
        ttk.Checkbutton(filt2, text='Gráficos 3D (tex)', variable=self.show_3d,
                        command=self.rebuild).pack(side='left', padx=(10, 0))
        self.tree = ttk.Treeview(self, columns=('kind', 'size'), show='tree headings')
        self.tree.heading('#0', text='Arquivo / gráfico')
        self.tree.heading('kind', text='Tipo')
        self.tree.heading('size', text='Tamanho')
        self.tree.column('#0', width=260)
        self.tree.column('kind', width=70, anchor='center')
        self.tree.column('size', width=74, anchor='e')
        scroll = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('dir', foreground=theme.ACCENT_2)
        self.tree.tag_configure('gfx', foreground=theme.SUCCESS)
        self.tree.tag_configure('res', foreground=theme.TEXT)
        self.tree.tag_configure('pending', foreground=theme.WARNING)
        self.tree.tag_configure('saved', foreground=theme.SUCCESS)
        self.res_tag = None        # optional fn(resource) -> 'pending' | 'saved' | None
        self.tree.bind('<<TreeviewOpen>>', self._expand)
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.files = []            # [(path, size)]
        self._all_resources = []
        self.gfx_per_dir = {}
        self.res_by_file = {}      # path -> [resource]
        self.nodes = {}            # iid -> ('dir', path) | ('file', path) | ('res', resource)
        self.dir_children = {}         # dir path -> (subdirs, files)

    def load(self, files, resources=None):
        self.files = list(files)
        self._all_resources = list(resources or [])
        self.rebuild()

    def rebuild(self):
        t = self.tree
        t.delete(*t.get_children())
        self.nodes.clear()
        self.dir_children.clear()
        show_2d, show_3d = self.show_2d.get(), self.show_3d.get()
        self.res_by_file = {}
        for r in self._all_resources:
            is_3d = r['kind'] == 'tex'
            if is_3d and not show_3d:
                continue
            if not is_3d and not show_2d:
                continue
            self.res_by_file.setdefault(resource_file(r), []).append(r)
        # graphics per folder, computed ONCE (was re-summed over every file
        # for every folder shown: 16 s on Ragnarok DS's 21k files)
        self.gfx_per_dir = {}
        for path, found in self.res_by_file.items():
            parts = PurePosixPath(path).parts
            for i in range(1, len(parts)):
                d = '/'.join(parts[:i])
                self.gfx_per_dir[d] = self.gfx_per_dir.get(d, 0) + len(found)
        text = self.search.get().strip().lower()
        only = self.only_gfx.get()
        known = {f for f, _s in self.files}
        extra = [p for p in self.res_by_file if p not in known]
        entries = self.files + [(p, 0) for p in extra]
        for path, size in entries:
            if only and path not in self.res_by_file:
                continue
            if text and text not in path.lower() and not any(
                    text in r['name'].lower() for r in self.res_by_file.get(path, ())):
                continue
            parts = PurePosixPath(path).parts
            for i in range(len(parts)):
                d = '/'.join(parts[:i])
                sub = self.dir_children.setdefault(d, (set(), []))
                if i < len(parts) - 1:
                    sub[0].add('/'.join(parts[:i + 1]))
                else:
                    sub[1].append((path, size))
        self._fill('', '')

    def _fill(self, parent_iid, dir_path):
        subdirs, files = self.dir_children.get(dir_path, (set(), []))
        for d in sorted(subdirs, key=str.lower):
            n_gfx = self.gfx_per_dir.get(d, 0)
            iid = self.tree.insert(parent_iid, 'end', text='📁 ' + PurePosixPath(d).name,
                                   values=(f'{n_gfx} gráf.' if n_gfx else '', ''), tags=('dir',))
            self.nodes[iid] = ('dir', d)
            self.tree.insert(iid, 'end', text='…')          # placeholder: lazy
        for path, size in sorted(files, key=lambda f: f[0].lower()):
            res = self.res_by_file.get(path, [])
            iid = self.tree.insert(parent_iid, 'end', text=PurePosixPath(path).name,
                                   values=(f'{len(res)} gráf.' if res else '', _human(size) if size else ''),
                                   tags=('gfx' if res else 'file',))
            self.nodes[iid] = ('file', path)
            if res:
                self.tree.insert(iid, 'end', text='…')

    def _expand(self, _e=None):
        iid = self.tree.focus()
        node = self.nodes.get(iid)
        kids = self.tree.get_children(iid)
        if not node or not kids or self.tree.item(kids[0], 'text') != '…':
            return
        self.tree.delete(kids[0])
        if node[0] == 'dir':
            self._fill(iid, node[1])
        elif node[0] == 'file':
            for r in self.res_by_file.get(node[1], []):
                label = r['name'].split('@')[-1] if '@' in r['name'] else PurePosixPath(r['name']).name
                if r['kind'] == 'tex':
                    label = f"{label} #{r.get('tex')}"
                cid = self.tree.insert(iid, 'end', text='  🖼 ' + label, values=(r['kind'], ''),
                                       tags=(self._tag(r),))
                self.nodes[cid] = ('res', r)

    def _tag(self, r):
        return (self.res_tag(r) if self.res_tag else None) or 'res'

    def retag(self):
        """Re-colour the graphics already shown (edit state changed)."""
        for iid, node in self.nodes.items():
            if node[0] == 'res':
                self.tree.item(iid, tags=(self._tag(node[1]),))

    def _select(self, _e=None):
        sel = self.tree.selection()
        node = self.nodes.get(sel[0]) if sel else None
        if not node:
            return
        if node[0] == 'res' and self.on_resource:
            self.on_resource(node[1])
        elif node[0] == 'file' and self.on_file:
            self.on_file(node[1], self.res_by_file.get(node[1], []))

    def select_resource(self, resource):
        for iid, node in self.nodes.items():
            if node[0] == 'res' and node[1] is resource:
                self.tree.see(iid)
                self.tree.selection_set(iid)
                return


class InfoView(Card):
    def __init__(self, parent, title='Informações do gráfico'):
        super().__init__(parent, title)
        self.text = tk.Text(self, height=9, bg=theme.PANEL, fg=theme.TEXT, relief='flat',
                            font=theme.FONT_MONO, wrap='word', padx=2, pady=2, borderwidth=0)
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('key', foreground=theme.MUTED)
        self.text.configure(state='disabled')

    def set_values(self, pairs):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for key, value in pairs:
            self.text.insert('end', f'{key:<14}', 'key')
            self.text.insert('end', f' {value}\n')
        self.text.configure(state='disabled')


SETTINGS = Path(os.environ.get('APPDATA') or Path.home() / '.config') / 'NitroGFX' / 'settings.json'


def load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    data = load_settings()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')


def open_in_editor(path, program=None):
    """Open a file in `program` (the editor the user picked: Paint,
    Photoshop, Aseprite...) or, without one, in the system's default EDITOR
    for its type (Windows 'edit' verb - Paint for PNG)."""
    import subprocess
    import sys
    if program:
        if sys.platform == 'darwin' and program.endswith('.app'):
            subprocess.Popen(['open', '-a', program, str(path)])
        else:
            subprocess.Popen([program, str(path)])
    elif os.name == 'nt':
        try:
            os.startfile(str(path), 'edit')    # noqa: S606 - user-requested action
        except OSError:
            os.startfile(str(path))            # noqa: S606
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])

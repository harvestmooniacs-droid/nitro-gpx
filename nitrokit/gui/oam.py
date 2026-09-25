"""OAM workbench: inspect a sprite's OAM pieces and switch the export
between the flat cell sheet and the lossless per-OAM sheet.

Why this tab exists: a cell is drawn from several OAM pieces that may
OVERLAP. A flat PNG can only hold one value per pixel, so whatever the top
piece covers is lost for editing - the 'chopped sprite' look. The OAM sheet
gives every piece its own rectangle, so nothing hides anything else and
every pixel round-trips (measured on Valkyrie Profile: 18% more source
pixels become reachable). The choice is stored on the resource, so a later
insert rebuilds exactly the same sheet.
"""
import tkinter as tk
from tkinter import ttk

import numpy as np

from ..formats import g2d
from ..pipeline import resource as resources
from ..render import compose as C
from .views import ZoomView
from .widgets import Card


COLUMNS = (('cell', 'Célula', 52), ('oam', 'OAM', 44), ('pos', 'X, Y', 78),
           ('size', 'Tam.', 68), ('tile', 'Tile', 58), ('pal', 'Sub-pal', 62),
           ('flip', 'Flip', 52))


class OamPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.resource = None
        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True)

        left = Card(pane, 'Tratamento de OAM')
        pane.add(left, weight=1)
        ttk.Label(left, wraplength=320, style='Muted.TLabel', justify='left',
                  text='Um sprite é montado por peças (OAM). Quando duas peças se sobrepõem, a '
                       'folha normal perde o que fica por baixo — é isso que deixa o gráfico '
                       '"picotado" na edição. A folha de OAM dá um retângulo para cada peça: nada '
                       'cobre nada, e todo pixel volta igual na reinserção.').pack(fill='x', pady=(0, 10))

        self.state = tk.StringVar(value='Nenhum sprite selecionado.')
        ttk.Label(left, textvariable=self.state, style='Panel.TLabel',
                  wraplength=320, justify='left').pack(fill='x', pady=(0, 10))

        self.shared = tk.StringVar(value='')
        ttk.Label(left, textvariable=self.shared, style='Muted.TLabel',
                  wraplength=320, justify='left').pack(fill='x', pady=(0, 8))

        self.isolated = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text='Exportar como folha de OAM (peças isoladas)',
                        variable=self.isolated, command=self.apply).pack(anchor='w')

        grid = ttk.Frame(left, style='Panel.TFrame')
        grid.pack(fill='x', pady=(10, 0))
        self.sheet_w, self.gap = tk.IntVar(value=C.SHEET_W), tk.IntVar(value=C.GAP)
        for label, var, lo, hi in (('Largura da folha (px)', self.sheet_w, 64, 2048),
                                   ('Espaço entre peças (px)', self.gap, 0, 32)):
            row = ttk.Frame(grid, style='Panel.TFrame')
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, style='Muted.TLabel', width=24).pack(side='left')
            ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=7,
                        command=self.apply).pack(side='left')

        ttk.Separator(left).pack(fill='x', pady=12)
        ttk.Button(left, text='Aplicar ao sprite selecionado', style='Accent.TButton',
                   command=self.apply).pack(fill='x')
        ttk.Button(left, text='Aplicar a todos os sprites com sobreposição',
                   command=self.apply_all_overlapping).pack(fill='x', pady=(6, 0))
        ttk.Button(left, text='Voltar todos ao modo normal',
                   command=self.reset_all).pack(fill='x', pady=(6, 0))
        self.batch = tk.StringVar(value='')
        ttk.Label(left, textvariable=self.batch, style='Muted.TLabel',
                  wraplength=320, justify='left').pack(fill='x', pady=(8, 0))

        center = ttk.Frame(pane)
        self.preview = ZoomView(center, show_oam_toggle=False)
        self.preview.title.configure(text='Preview do sprite')
        self.preview.pack(fill='both', expand=True)
        pane.add(center, weight=3)

        right = Card(pane, 'Peças OAM deste sprite')
        pane.add(right, weight=2)
        self.tree = ttk.Treeview(right, columns=[c[0] for c in COLUMNS], show='headings', height=20)
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor='center')
        scroll = ttk.Scrollbar(right, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)

    # --------------------------------------------------------------- state
    def set_resource(self, resource):
        """Called whenever the app's selected resource changes."""
        self.resource = resource if resource and resource['kind'] == 'cell' else None
        self.tree.delete(*self.tree.get_children())
        if self.resource is None:
            self.state.set('Selecione um sprite (tipo "cell") em Explorar e extrair.')
            self.preview.set_image(None)
            return
        info = self.resource.setdefault('cells', {})
        self.isolated.set(bool(info.get('isolated')))
        self.sheet_w.set(int(info.get('sheet_w') or C.SHEET_W))
        self.gap.set(int(info.get('gap', C.GAP)))
        try:
            ncer = g2d.NCER(self.app.session.get(self.resource['map']))
        except Exception as exc:
            self.state.set(f'Não foi possível ler o NCER: {exc}')
            return
        n_oam = sum(len(c['oams']) for c in ncer.cells)
        overlap = C.oams_overlap(ncer)
        info['overlap'] = bool(overlap)
        self.state.set(f"{self.resource['name']}\n{len(ncer.cells)} célula(s) • {n_oam} peça(s) OAM • "
                       + ('PEÇAS SE SOBREPÕEM: a folha normal perde pixels aqui.'
                          if overlap else 'sem sobreposição: a folha normal já é fiel.'))
        for ci, cell in enumerate(ncer.cells):
            for oi, o in enumerate(cell['oams']):
                flip = ('H' if o['hflip'] else '') + ('V' if o['vflip'] else '') or '—'
                self.tree.insert('', 'end', values=(ci, oi, f"{o['x']}, {o['y']}",
                                                    f"{o['w']}×{o['h']}", o['tile'], o['pal'], flip))
        self.refresh_preview()

    def refresh_preview(self):
        if not self.resource:
            return
        try:
            layout = resources.layout(self.resource, self.app.session)
            image, _info = resources.to_png(self.resource, self.app.session)
        except Exception as exc:
            self.preview.set_image(None)
            self.state.set(f'{self.state.get()}\nErro ao montar: {exc}')
            return
        self.preview.set_image(image)
        # pixels that appear more than once in this sheet come from the same
        # bytes: editing one copy and not the others is a real contradiction,
        # and the insert step refuses it - better to say so up front
        src = layout['src']
        mapped = src[src >= 0]
        if mapped.size:
            _ids, counts = np.unique(mapped, return_counts=True)
            shared = int(counts[counts > 1].sum())
            total = int(mapped.size)
            self.shared.set(
                f'{total} pixel(s) na folha • {shared} aparecem em mais de um lugar '
                f'(mesma origem: edite TODAS as cópias igualmente, senão a reinserção recusa).'
                if shared else f'{total} pixel(s) na folha, nenhum repetido: edição livre.')
        else:
            self.shared.set('')

    # -------------------------------------------------------------- actions
    def apply(self):
        if not self.resource:
            return
        self.resource.setdefault('cells', {}).update(
            isolated=bool(self.isolated.get()), sheet_w=int(self.sheet_w.get()),
            gap=int(self.gap.get()))
        self.refresh_preview()
        self.app.select_resource(self.resource, from_oam=True)

    def apply_all_overlapping(self):
        """Turn the OAM sheet on for every cell whose pieces really overlap -
        the ones a flat sheet cannot represent - and leave the rest alone."""
        if not self.app.resources:
            return
        changed = checked = 0
        for r in self.app.resources:
            if r['kind'] != 'cell' or not r.get('map'):
                continue
            info = r.setdefault('cells', {})
            if 'overlap' not in info:
                try:
                    info['overlap'] = bool(C.oams_overlap(g2d.NCER(self.app.session.get(r['map']))))
                except Exception:
                    info['overlap'] = False
            checked += 1
            if info['overlap'] and not info.get('isolated'):
                info['isolated'] = True
                changed += 1
        self.batch.set(f'{changed} sprite(s) passaram para folha de OAM '
                       f'(de {checked} verificados). Os demais não têm sobreposição.')
        if self.resource:
            self.set_resource(self.resource)

    def reset_all(self):
        count = 0
        for r in self.app.resources:
            info = r.get('cells') or {}
            if info.get('isolated'):
                info['isolated'] = False
                count += 1
        self.batch.set(f'{count} sprite(s) voltaram para a folha normal.')
        if self.resource:
            self.set_resource(self.resource)

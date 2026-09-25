"""Coverage panel: every file of the source that produced no graphics.

Answers 'is the tool ignoring folders?' with data instead of a guess - the
scan walks every file, and this lists the ones nothing was recognised in,
with size and reason, so an undeciphered container is visible rather than
silent.
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import compression
from ..pipeline import coverage
from ..render import heuristics
from .widgets import Card

COLUMNS = (('size', 'Tamanho', 96), ('reason', 'Motivo', 300), ('path', 'Arquivo', 420))


def _human(size):
    for unit in ('B', 'KB', 'MB'):
        if size < 1024 or unit == 'MB':
            return f'{size:,.0f} {unit}'.replace(',', '.')
        size /= 1024


class CoveragePanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.rows = []
        card = Card(self, 'Cobertura da varredura')
        card.pack(fill='both', expand=True)
        self.summary = tk.StringVar(value='Abra uma origem para ver a cobertura.')
        ttk.Label(card, textvariable=self.summary, style='Panel.TLabel',
                  wraplength=900, justify='left').pack(fill='x', pady=(0, 8))
        ttk.Label(card, style='Muted.TLabel', wraplength=900, justify='left',
                  text='A varredura passa por TODOS os arquivos da origem. A lista abaixo é o que '
                       'sobrou: arquivos onde nenhum gráfico foi reconhecido. Áudio, vídeo e código '
                       'aparecem marcados como fora do escopo; o que estiver como "contêiner próprio" '
                       'é formato ainda não decifrado — é ali que falta pesquisa, não na varredura.'
                  ).pack(fill='x', pady=(0, 10))
        tools = ttk.Frame(card, style='Panel.TFrame')
        tools.pack(fill='x', pady=(0, 6))
        self.only_unknown = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text='Mostrar só formatos não decifrados', variable=self.only_unknown,
                        command=self.refresh).pack(side='left')
        ttk.Button(tools, text='Exportar lista (.txt)', command=self.export).pack(side='right')
        self.tree = ttk.Treeview(card, columns=[c[0] for c in COLUMNS], show='headings')
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor='e' if key == 'size' else 'w')
        scroll = ttk.Scrollbar(card, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<Double-1>', self._show_diag)

    def set_source(self, source, resources, unresolved=None):
        if source is None or not hasattr(source, 'all_files'):
            self.rows = []
            self.summary.set('Cobertura disponível para ROMs .nds.')
            self.refresh()
            return
        try:
            data = coverage.report(source, resources, unresolved)
        except Exception as exc:
            self.rows = []
            self.summary.set(f'Não foi possível medir a cobertura: {exc}')
            self.refresh()
            return
        self.rows = data['rows']
        unknown = [r for r in self.rows if r['candidate']]
        diagnosed = [r for r in self.rows if r['diag']]
        extra = (f" ({len(diagnosed)} com diagnóstico detalhado — dê duplo-clique na linha)"
                 if diagnosed else '')
        self.summary.set(
            f"{data['with_graphics']} de {data['files']} arquivos geraram gráficos • "
            f"{len(self.rows)} sem gráfico reconhecido, dos quais {len(unknown)} "
            f"({_human(data['unknown_bytes'])}) são possíveis formatos próprios ainda não decifrados"
            f"{extra}.")
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows):
            if self.only_unknown.get() and not row['candidate']:
                continue
            iid = str(i)
            self.tree.insert('', 'end', iid=iid, values=(_human(row['size']), row['reason'], row['path']))

    def _show_diag(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.rows[int(sel[0])]
        if row['diag']:
            self._show_container_diag(row)
        elif row['candidate']:
            self._show_raw_guess(row)

    def _show_container_diag(self, row):
        diag = row['diag']
        lines = [f"Arquivo: {row['path']}", f"Contêiner: {diag['container']} ({diag['entries']} itens)", '']
        for s in diag['samples']:
            lines.append(f"  item {s['index']}: {s['raw_size']} bytes brutos, compressão {s['codec']}, "
                         f"payload {s['payload_size']} bytes")
            lines.append(f"    primeiros bytes: {s['head_hex']}")
        lines.append('')
        lines.append('O contêiner e a compressão foram reconhecidos e descomprimidos corretamente; '
                     'o problema é que os bytes acima não correspondem a nenhum formato gráfico Nitro '
                     'nem a um padrão bruto conhecido — provavelmente uma estrutura de célula/sprite '
                     'própria deste jogo, ainda não decifrada.')
        messagebox.showinfo(f"Diagnóstico — {row['path']}", '\n'.join(lines))

    def _show_raw_guess(self, row):
        """No container/compression was recognised at all for this file (a
        game's own .bin/.dat/... with zero header) - try the same row-
        smoothness heuristic already used for ambiguous atlas widths to see
        if the raw bytes LOOK like a tile/bitmap dump, and offer to open it
        pre-filled in the Laboratório raw instead of asking the user to
        guess bpp/width from scratch. This is only a suggestion (score-
        based), never an auto-added resource - it can be wrong."""
        try:
            data = self.app.source.read(row['path'])
        except Exception as exc:
            messagebox.showerror('Diagnóstico', f"Não foi possível ler \"{row['path']}\": {exc}")
            return
        payload, codec = compression.unwrap(data)
        guess = heuristics.guess_raw_tile(payload)
        lines = [f"Arquivo: {row['path']}", f"Tamanho: {_human(row['size'])}",
                f"Compressão: {codec or 'nenhuma (dados crus)'}", '']
        if guess:
            lines.append(f"Palpite de tileset cru (4bpp): {guess['width']}×{guess['height']}px "
                         f"— confiança {guess['confidence']:.2f}"
                         + (' (baixa: pode estar errado)' if guess['confidence'] < 0.3 else '.'))
            lines.append('')
            lines.append('Nenhum cabeçalho/magic foi reconhecido, então isto NÃO foi adicionado como '
                         'recurso automaticamente — é só um palpite de largura testando qual delas deixa '
                         'a imagem mais "lisa" linha a linha, a mesma técnica usada para atlas ambíguos.')
        else:
            lines.append('Não achei um palpite razoável de largura para estes bytes como tileset 4bpp '
                         '— pode não ser gráfico, ou usar outro bpp/formato. Tente ajustar manualmente '
                         'no Laboratório raw.')
        messagebox.showinfo(f"Diagnóstico — {row['path']}", '\n'.join(lines))
        if guess and messagebox.askyesno('Diagnóstico', 'Abrir este arquivo no Laboratório raw com '
                                         'esse palpite já preenchido?'):
            self.app.tabs.select(self.app.advanced)
            self.app.advanced.sub.select(self.app.advanced.raw)
            raw = self.app.advanced.raw
            raw.data = payload
            raw.file.set(row['path'])
            raw.codec.set('Sem compressão')
            raw.bpp.set(guess['bpp'])
            raw.width.set(guess['width'])
            raw.height.set(guess['height'])
            raw.order.set('Linear')
            raw.offset.set('0')
            raw.preview_manual(silent=True)

    def export(self):
        if not self.rows:
            return
        path = filedialog.asksaveasfilename(defaultextension='.txt',
                                            filetypes=[('Texto', '*.txt')])
        if not path:
            return
        lines = [f"{row['size']:>12}  {row['reason']:<45} {row['path']}" for row in self.rows]
        with open(path, 'w', encoding='utf-8') as out:
            out.write(self.summary.get() + '\n\n' + '\n'.join(lines) + '\n')

"""Nitro GFX - desktop studio for dumping and inserting NDS graphics.

Layout:
  header   logo · NITRO GFX · banner icon + name of the loaded game · music
  Dump     left: ROM base (Tinke-like tree + ROM info) + Buscar/Analisar
           center: zoom preview (wheel at cursor, OAM mode, grids) with the
                   graphic's info and the live palette chooser under it
           right: graphics health, supported-games log, Dumpar Root /
                  Dumpar GpxOAM
  Insert   toolbar: Abrir ROM · Pasta orig · Importar PNGs Mod · Importar
           OAM (matched BY PNG NAME) · Montar ROM; tree + Analisar; the
           preview IS the pixel editor (gui/editor.py) + Carregar imagem /
           Editor externo / Salvar imagem
  Avançado ROM tree + Laboratório raw · OAM/sprites · Cobertura

Every heavy step runs in a worker thread; the Tk thread only polls.
"""
import collections
import json
import os
import sys
import tempfile
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .. import __version__
from ..io import open_source
from ..rom.ndsrom import NDSRom
from ..pipeline import coverage, resource as resources, workflow
from ..pipeline import scan as scan_module
from ..pipeline.scan import scan
from ..pipeline.source import EditError, Session
from ..formats import banner as bannerfmt
from ..formats import g2d
from . import theme, views
from .advanced import AUTO, CODECS, render as render_raw
from .coverage_view import CoveragePanel
from .editor import PixelEditor
from .oam import OamPanel
from .views import (Card, GameLog, HealthView, InfoView, PaletteChooser, RomTree, ZoomView,
                    load_settings, open_in_editor, resource_file, save_setting)
from .widgets import MetadataView, Tooltip, help_icon

ASSETS = Path(__file__).resolve().parent / 'assets'
GUIA = Path(__file__).resolve().parents[2] / 'GUIA.md'
PREVIEW_OK = ('tex', 'banner', 'font', 'photo', 'bitmap', 'sir', 'sir_sprite', 'obp', 'bgp')

GUIDE_TEXT = {
    'dump': ('Dump', """Como extrair os gráficos de uma ROM:

1. CARREGAR ROM BASE (canto superior esquerdo)
   Abra o arquivo .nds (ou uma pasta/arquivo isolado). Isso só lê a
   estrutura de pastas da ROM - ainda não procura gráfico nenhum, por
   isso é instantâneo mesmo em ROMs enormes.

2. BUSCAR / ANALISAR GRÁFICOS
   Só aqui a tool de fato descomprime e identifica tudo que se encaixa
   nos formatos Nitro que ela reconhece, e pareia cada gráfico com sua
   paleta/tileset/mapa. Em ROMs grandes pode levar de alguns segundos a
   alguns minutos. Depois disso os arquivos com gráfico aparecem em verde
   na árvore.

3. NAVEGAR E CONFERIR
   Clique num arquivo verde na árvore pra expandir e ver os gráficos
   dentro dele; clique num gráfico pra ver no preview central.
   • Roda do mouse no preview = zoom (some no ponto onde o cursor está)
   • Arrastar = mover a imagem
   • Duplo clique = ajustar o zoom à janela
   • "Modo OAM" = mostra o sprite com as peças separadas, útil quando
     partes se sobrepõem e ficam "picotadas" na folha normal
   • O seletor de Paleta (abaixo do preview) troca a cor em tempo real -
     útil quando a paleta identificada automaticamente está errada

4. SAÚDE DOS GRÁFICOS e LOG DE JOGOS (direita)
   Mostram, respectivamente, um resumo de quanto da ROM foi decodificado
   e se esse jogo já foi testado antes (e com que resultado).

5. DUMPAR ROOT
   Exporta todos os gráficos da ROM como PNG, numa pasta que espelha a
   estrutura de pastas do próprio jogo. Cada pasta de dump ganha um
   arquivo manifest.json - NÃO APAGUE nem edite esse arquivo: é ele que
   diz à reinserção qual PNG volta pra qual lugar da ROM.

6. DUMPAR GPXOAM
   Dump separado, só dos sprites que têm peças sobrepostas (ou que você
   ativou o Modo OAM manualmente) - a versão "sem perda" desses sprites.
   Use quando notar sprite picotado na pasta Root."""),

    'insert': ('Insert', """Como reinserir PNGs editados numa ROM nova:

1. ABRIR ROM
   A MESMA ROM original que você usou pra fazer o dump.

2. PASTA ORIG
   Aponte para a pasta do dump (a que tem o manifest.json) - normalmente
   a pasta que "Dumpar Root" criou. Pode carregar mais de uma pasta (por
   exemplo Root + GpxOAM) repetindo esse passo.

3. ANALISAR
   Carrega a estrutura da ROM na árvore, já com os PNGs do(s) dump(s)
   marcados.

4. IMPORTAR PNGs MOD. / IMPORTAR OAM
   Escolha os arquivos PNG que você editou. Não importa em qual pasta
   eles estão agora (podem estar copiados pra fora da pasta de dump) -
   a tool casa cada PNG com o gráfico certo PELO NOME DO ARQUIVO, que já
   sai único de cada dump. "Importar OAM" é só um atalho pra quando os
   PNGs vêm da pasta GpxOAM.

5. CONFERIR
   Clique num item da lista à direita ("PNGs para reinserir") pra ver
   Original vs. Modificado lado a lado no preview. "Abrir no editor" abre
   o PNG no editor de imagem padrão do sistema; "Carregar imagem" troca
   por um arquivo já editado; "Desfazer este" tira o item da lista sem
   apagar o PNG.

6. MONTAR ROM
   Gera a ROM nova com todas as edições. A tool valida o lote inteiro
   antes de gravar qualquer coisa - se algo não bater, nada é escrito
   (nenhuma ROM corrompida é gerada; você recebe o erro exato)."""),

    'advanced': ('Modo avançado', """Ferramentas para casos que a análise automática não resolveu sozinha:

LABORATÓRIO RAW
   Renderização manual de um arquivo binário: escolha compressão, bpp,
   largura/altura, offset e paleta na mão. Útil pra investigar um
   arquivo que a varredura normal não reconheceu. Clicar num arquivo na
   árvore à esquerda já carrega os bytes dele aqui direto.
   Com um gráfico já identificado selecionado, os botões "Paleta…" /
   "Tileset…" / "Largura" corrigem manualmente o pareamento que a tool
   errou (paleta trocada, atlas com largura errada, tela sem tileset).

OAM / SPRITES
   Mostra as peças (OAM) de um sprite selecionado e avisa quando elas se
   sobrepõem - o motivo mais comum de sprite "picotado". Dá pra ativar o
   modo OAM (peças isoladas, sem perda) por sprite, ou em lote pra todos
   os que precisam.

COBERTURA
   Depois de Buscar/Analisar, lista todo arquivo da ROM onde NENHUM
   gráfico foi reconhecido, com tamanho e motivo (áudio, texto, código,
   ou "formato próprio ainda não decifrado"). É a forma de saber se a
   tool está mesmo ignorando alguma coisa, ou se aquele arquivo
   simplesmente não é gráfico."""),
}


def locator_text(loc):
    if not loc:
        return '—'
    return ' › '.join(f"{s[0]}:{s[1]}" if len(s) > 1 else s[0] for s in loc)


def codecs_of(loc):
    out = []
    for s in loc or []:
        if s[0] == 'codec':
            out.append(s[1])
        elif s[0] == 'lz' and len(s) > 2:
            out.append(s[2])
        elif s[0] == 'crilayla':
            out.append('crilayla')
    return ', '.join(out) or 'nenhuma'


def container_of(loc):
    steps = [s[0] for s in loc or [] if s[0] not in ('file', 'codec', 'slice', 'lz')]
    return ' › '.join(steps) or '—'


def rom_files(source):
    rows = []
    for fid, path in source.all_files():
        try:
            s, e = source.fat[fid]
            rows.append((path, max(0, e - s)))
        except Exception:
            try:
                rows.append((path, len(source.read(path))))
            except Exception:
                rows.append((path, 0))
    return rows


KIND_LABEL = {
    'bg': 'bg (fundo)', 'cell': 'cell (sprite montado)', 'atlas': 'atlas (grade de tiles)',
    'tex': 'tex (textura 3D)', 'banner': 'banner (ícone do cartucho)', 'font': 'font',
    'raw_bg': 'raw_bg', 'raw_atlas': 'raw_atlas', 'raw_screen': 'raw_screen (sem tileset)',
    'raw_tex': 'raw_tex', 'photo': 'photo', 'bitmap': 'bitmap', 'sir': 'sir', 'sir_sprite': 'sprite (sir)',
    'obp': 'obp', 'bgp': 'bgp',
}


def health_values(source, res, stats, unresolved=None):
    """% integrity / identified / 2D / 3D (see HealthView), plus a per-kind
    breakdown line. `res` excluding the banner is the denominator for 2D/3D/
    integrity - the banner is a fixed, always-present, unrelated code path,
    so a ROM whose ONLY resource is the banner must show 0%/'-', not the
    100% a naive '(len(res)-tex)/len(res)' produces."""
    unresolved = unresolved if unresolved is not None else scan_module.UNRESOLVED
    if not res:
        return {}, 'Nenhum gráfico encontrado.'
    gfx = [r for r in res if r['kind'] != 'banner']
    values = {}
    note_parts = [f'{len(res)} gráficos ({len(gfx)} sem contar o banner)']
    if gfx:
        tex = sum(1 for r in gfx if r['kind'] == 'tex')
        bad = sum(v for k, v in stats.items() if k.endswith('does_not_fit'))
        # 'ok' here is still a proxy (has a usable palette/format, no
        # does_not_fit flag) - not a byte-exact round-trip check, which
        # would need re-rendering + re-encoding every resource on every
        # scan. Good enough to flag "this resource is clearly broken",
        # not strong enough to claim verified integrity.
        ok = sum(1 for r in gfx if r['kind'] != 'raw_screen' and (r.get('pal') or r['kind'] in PREVIEW_OK))
        values['integrity'] = max(0, ok - bad) * 100 / len(gfx)
        values['g2d'] = (len(gfx) - tex) * 100 / len(gfx)
        values['g3d'] = tex * 100 / len(gfx)
    try:
        cov = coverage.report(source, res, unresolved)
        candidates = max(1, cov['candidates'])
        values['identified'] = min(100, cov['with_graphics'] * 100 / candidates)
        note_parts.append(f"{cov['with_graphics']} de {candidates} arquivos que podem conter gráfico "
                          f"identificados (áudio/texto/código não contam)")
    except Exception:
        pass
    kinds = collections.Counter(r['kind'] for r in res)
    breakdown = [f"{n} {KIND_LABEL.get(k, k)}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])]
    if unresolved:
        breakdown.append(f"{len(unresolved)} arquivo(s) não identificados (compressão/formato próprio)")
    note = ' • '.join(note_parts) + ('\n' + ' | '.join(breakdown) if breakdown else '')
    return values, note


def banner_icon(source, size=56):
    """(PhotoImage-ready RGBA image, best title) from the cartridge banner,
    or (None, None). Cheap: one fixed-size block of the header area."""
    data = getattr(source, 'data', b'')
    if not data or not hasattr(source, 'banner_offset'):
        return None, None
    off = source.banner_offset()
    if not bannerfmt.is_banner(data, off):
        return None, None
    b = bannerfmt.Banner(data[off:off + bannerfmt.total_size(data, off)])
    px = b.icon_pixels()
    flat_px = [int(v) for row in px for v in row] if not hasattr(px, 'reshape') else [int(v) for v in px.reshape(-1)]
    im = Image.new('P', (32, 32))
    im.putdata(flat_px)
    flat = [c for rgb in b.palette() for c in rgb]
    im.putpalette((flat + [0] * 768)[:768])
    im.info['transparency'] = 0
    im = im.convert('RGBA').resize((size, size), Image.Resampling.NEAREST)
    titles = b.titles()
    title = (titles.get('en') or titles.get('ja') or '').replace('\n', ' · ').strip()
    return im, title or None


class Header(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style='Header.TFrame', padding=(14, 8))
        self.app = app
        self.logo = self.icon_photo = None
        try:
            im = Image.open(ASSETS / 'logo.png').convert('RGBA')
            im.thumbnail((52, 52), Image.Resampling.LANCZOS)
            self.logo = ImageTk.PhotoImage(im)
            ttk.Label(self, image=self.logo, style='Panel.TLabel').pack(side='left', padx=(0, 10))
        except Exception:
            pass
        ttk.Label(self, text='NITRO GFX', style='Title.TLabel').pack(side='left')
        ttk.Label(self, text='  NDS Graphics Studio', style='Muted.TLabel').pack(side='left', pady=(8, 0))
        ttk.Separator(self, orient='vertical').pack(side='left', fill='y', padx=16)
        self.icon_label = ttk.Label(self, style='Panel.TLabel')
        self.icon_label.pack(side='left', padx=(0, 8))
        info = ttk.Frame(self, style='Header.TFrame')
        info.pack(side='left', fill='x', expand=True)
        self.game = ttk.Label(info, text='Nenhuma ROM carregada', style='Game.TLabel')
        self.game.pack(anchor='w')
        self.sub = ttk.Label(info, text='Carregue uma ROM base para começar.', style='Muted.TLabel')
        self.sub.pack(anchor='w')
        ttk.Button(self, text='Guia', style='Tool.TButton', command=app.show_help).pack(side='right')
        self.music_btn = ttk.Button(self, text='♪ Música', style='Tool.TButton', command=app.toggle_music)
        self.music_btn.pack(side='right', padx=6)
        ttk.Checkbutton(self, text='Varrer overlays/ARM', variable=app.include_code).pack(side='right', padx=10)

    def set_game(self, source, path):
        title = Path(path).stem
        self.icon_photo = None
        self.icon_label.configure(image='')
        try:
            icon, banner_title = banner_icon(source)
            if icon is not None:
                self.icon_photo = ImageTk.PhotoImage(icon)
                self.icon_label.configure(image=self.icon_photo)
            if banner_title:
                title = banner_title
        except Exception:
            pass                            # banner is decoration: never block loading
        data = getattr(source, 'data', b'')
        sub = str(path)
        if data:
            sub = (f'{getattr(source, "code", "")} • {len(data) / 1048576:.0f} MB • '
                   f'{getattr(source, "n_files", "?")} arquivos')
        self.game.configure(text=title)
        self.sub.configure(text=sub)


# ======================================================================= DUMP
class DumpTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=8, pady=8)

        # ---- left: ROM base
        left = ttk.Frame(pane)
        load = Card(left, 'ROM base')
        load.pack(fill='x')
        row = ttk.Frame(load, style='Panel.TFrame')
        row.pack(fill='x')
        ttk.Button(row, text='Carregar ROM base', style='Accent.TButton',
                   command=app.load_rom).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Pasta', command=lambda: app.load_rom(kind='folder')).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Arquivo', command=lambda: app.load_rom(kind='file')).pack(side='left', padx=(6, 0))
        self.rom_info = InfoView(left, 'Informações da ROM')
        self.rom_info.text.configure(height=6)
        self.rom_info.pack(fill='x', pady=(8, 0))
        self.tree = RomTree(left, on_resource=app.select_resource, on_file=self._file_selected)
        self.tree.pack(fill='both', expand=True, pady=(8, 0))
        analyze = Card(left)
        analyze.pack(fill='x', pady=(8, 0))
        self.analyze_btn = ttk.Button(analyze, text='🔎  Buscar / Analisar gráficos', style='Success.TButton',
                                      command=self._analyze)
        self.analyze_btn.pack(fill='x')
        self.analyze_bar = ttk.Progressbar(analyze, maximum=100)
        views.wrap_label(analyze, 'Descomprime e identifica tudo que se encaixa nos modelos Nitro '
                                  'que a tool reconhece, e pareia paleta/tileset/mapa.'
                         ).pack(fill='x', pady=(6, 0))
        pane.add(left, weight=2)

        # ---- center: preview + info + palette
        center = ttk.Frame(pane)
        self.preview = ZoomView(center, on_toggle_oam=app.toggle_oam)
        self.preview.pack(fill='both', expand=True)
        lower = ttk.Frame(center)
        lower.pack(fill='x', pady=(8, 0))
        self.info = InfoView(lower)
        self.info.pack(side='left', fill='both', expand=True)
        self.palette = PaletteChooser(lower, app.change_palette)
        self.palette.pack(side='left', fill='y', padx=(8, 0))
        pane.add(center, weight=5)

        # ---- right: health, games, dump. The Dump card is anchored to the
        # BOTTOM first (before the health/games cavity is divided) so a long
        # health note (more lines as more graphics get identified) never
        # pushes "Dumpar Root"/"Dumpar GpxOAM" out of view - it used to be
        # packed last on top, so it could end up clipped below the window.
        right = ttk.Frame(pane)
        dump = Card(right, 'Dump')
        dump.pack(side='bottom', fill='x', pady=(8, 0))
        ttk.Button(dump, text='Dumpar Root', style='Accent.TButton', command=app.dump_root).pack(fill='x')
        views.wrap_label(dump, 'todas as pastas do jogo com os gráficos descomprimidos'
                         ).pack(fill='x', pady=(2, 8))
        ttk.Button(dump, text='Dumpar GpxOAM', command=app.dump_oam).pack(fill='x')
        views.wrap_label(dump, 'outra pasta, só sprites, com o modo OAM aplicado').pack(fill='x', pady=(2, 8))
        ttk.Button(dump, text='Exportar PNG do selecionado', style='Tool.TButton',
                   command=app.export_selected).pack(fill='x')
        views.wrap_label(dump, '⚠ Não apague nem edite o manifest.json da pasta de dump: '
                               'é ele que diz à reinserção onde cada PNG volta.',
                         style='Warn.TLabel').pack(fill='x', pady=(10, 0))
        self.health = HealthView(right)
        self.health.pack(fill='x')
        self.games = GameLog(right, GUIA)
        self.games.pack(fill='both', expand=True, pady=(8, 0))
        pane.add(right, weight=2)

    def _analyze(self):
        self.analyze_btn.configure(text='🔎  Analisando…', state='disabled')
        self.analyze_bar.pack(fill='x', pady=(6, 0))
        self.analyze_bar['value'] = 0
        self.app.analyze(then=self._analyze_done)
        self._poll_analyze()           # a refused start (no ROM) resets the button at once

    def _poll_analyze(self):
        job = self.app.job
        if not job or job['done']:
            self._analyze_done()       # also after a FAILED scan, not only on success
            return
        total = job.get('total', 0)
        self.analyze_bar['value'] = job.get('n', 0) * 100 / total if total else 0
        self.after(120, self._poll_analyze)

    def _analyze_done(self):
        self.analyze_bar.pack_forget()
        self.analyze_btn.configure(text='🔎  Buscar / Analisar gráficos', state='normal')

    def _file_selected(self, path, found):
        size = next((s for p, s in self.tree.files if p == path), 0)
        self.info.set_values([('Arquivo', path), ('Tamanho', f'{size:,} bytes'.replace(',', '.')),
                              ('Gráficos', f'{len(found)} encontrados' if found else
                               ('nenhum reconhecido' if self.app.analyzed else 'rode Buscar/Analisar')),
                              ('Dica', 'expanda o arquivo na árvore para ver os gráficos')])


# ===================================================================== INSERT
class InsertTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.manifests = []          # [(folder, manifest)]
        self.records = []            # every record of every loaded manifest
        self.staged = {}             # record key -> (record, png path, origin)
        self.pending = {}            # record key -> (record, PIL image): edited, not saved yet
        self.current = None
        self._workdir = None         # temp folder for PNGs saved by the built-in editor
        self._watch = None           # (png path, mtime) opened in the external editor
        self._rom_info0 = {}         # ROM info as loaded, to tell what the user changed
        bar = ttk.Frame(self, style='Panel.TFrame', padding=8)
        bar.pack(fill='x', padx=8, pady=(8, 0))
        b = ttk.Button(bar, text='Abrir root/arquivo', command=self.open_loose)
        for text, cmd in (('Abrir ROM', app.load_rom), ('Pasta orig', self.pick_orig),
                          ('Importar PNGs Mod.', self.import_mod), ('Importar OAM', self.import_oam)):
            ttk.Button(bar, text=text, command=cmd).pack(side='left', padx=(0, 6))
            if text == 'Abrir ROM':
                b.pack(side='left', padx=(0, 6))
                Tooltip(b, 'Para quem tem a pasta "root" extraída (ndstool, DSLazy, Tinke) em vez do '
                           '.nds: abre a pasta ou um arquivo de gráfico solto (.narc, .bin, .NCGR, .arc...). '
                           'Montar então gera uma cópia nova da pasta/arquivo com os gráficos inseridos.')
        ttk.Button(bar, text='Montar ROM', style='Success.TButton', command=self.build).pack(side='right')
        b = ttk.Button(bar, text='Montar Arquivos', command=self.export_files)
        b.pack(side='right', padx=(0, 6))
        Tooltip(b, 'Em vez da ROM inteira, grava numa pasta nova SÓ os arquivos do jogo que mudaram '
                   '(.narc, .bin, .NCGR, .arc...), com o mesmo caminho interno da ROM (ex.: data/menu.narc). '
                   'É só copiar por cima da sua pasta root extraída e remontar com a sua ferramenta. '
                   'Ícone vira banner.bin; overlays mudam também y9.bin (nomes do ndstool).')
        b = ttk.Button(bar, text='Salvar todas as edições', command=self.save_all)
        b.pack(side='right', padx=(0, 6))
        Tooltip(b, 'Aplica de uma vez todos os gráficos editados e ainda não salvos (em laranja) '
                   'e põe todos na lista de reinserção.')
        self.status = ttk.Label(bar, text='1) Abrir ROM original  2) Pasta orig = pasta do dump  '
                                          '3) Analisar  4) importar PNGs editados  5) Montar ROM',
                                style='Muted.TLabel')
        self.status.pack(side='left', padx=12)

        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=8, pady=8)
        left = ttk.Frame(pane)
        self.tree = RomTree(left, on_resource=self.select, title='Estrutura da ROM + PNGs do dump')
        self.tree.res_tag = self._res_tag
        self.tree.pack(fill='both', expand=True)
        box = Card(left)
        box.pack(fill='x', pady=(8, 0))
        ttk.Button(box, text='🔎  Analisar', style='Success.TButton', command=self.analyze).pack(fill='x')
        self._build_rom_info(left)
        pane.add(left, weight=2)

        center = ttk.Frame(pane)
        tools = ttk.Frame(center, style='Panel.TFrame', padding=(8, 6))
        tools.pack(fill='x')
        self.show = tk.StringVar(value='mod')
        self._shown = 'mod'
        ttk.Radiobutton(tools, text='Original', value='orig', variable=self.show,
                        command=self._switch_view).pack(side='left')
        ttk.Radiobutton(tools, text='Modificado', value='mod', variable=self.show,
                        command=self._switch_view).pack(side='left', padx=(8, 16))
        for text, cmd, tip in (
                ('Carregar imagem', self.load_image, 'Escolhe um PNG já editado para este gráfico.'),
                ('Abrir no editor', self.open_editor,
                 'Abre o PNG no editor escolhido em "⚙" (ou, sem escolha, no editor padrão do '
                 'sistema - no Windows normalmente o Paint). Salve lá e volte: a prévia recarrega sozinha.'),
                ('Salvar como…', self.save_image, 'Grava uma cópia do que está na tela onde você escolher.')):
            b = ttk.Button(tools, text=text, command=cmd)
            b.pack(side='left', padx=(0, 6))
            Tooltip(b, tip)
            if cmd == self.open_editor:
                b.pack_configure(padx=(0, 1))
                self._editor_btn = ttk.Button(tools, text='⚙', width=3, style='Tool.TButton',
                                              command=self.choose_editor)
                self._editor_btn.pack(side='left', padx=(0, 6))
                self._editor_tip = Tooltip(self._editor_btn, '')
                self._update_editor_tip()
        b = ttk.Button(tools, text='Remover', style='Tool.TButton', command=self.unstage)
        b.pack(side='right')
        Tooltip(b, 'Tira este gráfico da lista de reinserção e descarta edições não salvas dele.')
        self.preview = PixelEditor(center, on_apply=self._apply_edit, on_save=self._save_png,
                                   on_discard=self._discarded, on_dirty=lambda _d: self._mark())
        self.preview.pack(fill='both', expand=True, pady=(8, 0))
        self.info = InfoView(center)
        self.info.pack(fill='x', pady=(8, 0))
        pane.add(center, weight=5)

        right = Card(pane, 'PNGs para reinserir')
        self.list = ttk.Treeview(right, columns=('src',), show='tree headings', height=20)
        self.list.heading('#0', text='PNG')
        self.list.heading('src', text='Origem')
        self.list.column('#0', width=220)
        self.list.column('src', width=70, anchor='center')
        self.list.pack(fill='both', expand=True)
        self.list.bind('<<TreeviewSelect>>', self._list_select)
        self.list.tag_configure('pending', foreground=theme.WARNING)
        self.list.tag_configure('saved', foreground=theme.SUCCESS)
        self.count = ttk.Label(right, text='nenhum', style='Muted.TLabel')
        self.count.pack(fill='x', pady=(6, 0))
        pane.add(right, weight=2)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def key(r):
        return f"{r.get('_dump', '')}|{r.get('png', '')}"

    def _load_manifest(self, folder):
        folder = Path(folder)
        path = folder / 'manifest.json'
        if not path.exists():
            raise ValueError(f'{folder} não tem manifest.json (não é uma pasta de dump da tool).')
        if any(Path(f).resolve() == folder.resolve() for f, _m in self.manifests):
            return
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if self.app.source is not None:
            workflow.validate_identity(self.app.source, manifest)
        self.manifests.append((folder, manifest))
        for r in manifest.get('resources', []):
            if 'png' in r:
                r['_dump'] = str(folder)
                self.records.append(r)

    def pick_orig(self):
        folder = filedialog.askdirectory(title='Pasta orig: a pasta do dump (tem manifest.json)')
        if not folder:
            return
        try:
            self._load_manifest(folder)
        except Exception as exc:
            messagebox.showerror('Pasta orig', str(exc))
            return
        self.status.configure(text=f'Pasta orig: {folder}  •  agora clique em Analisar')

    def analyze(self):
        if self.app.source is None:
            messagebox.showwarning('Analisar', 'Abra a ROM original primeiro.')
            return
        if not self.manifests:
            messagebox.showwarning('Analisar', 'Escolha a Pasta orig (a pasta do dump).')
            return
        self.tree.load(rom_files(self.app.source), self.records)
        self.status.configure(text=f'{len(self.records)} PNGs do dump carregados na árvore.')

    def _stage(self, pairs, origin):
        for r, png in pairs:
            self.staged[self.key(r)] = (r, Path(png), origin)
        self._refresh_list()

    def _import(self, origin, title):
        if self.app.source is None or not self.manifests:
            messagebox.showwarning(title, 'Abra a ROM e a Pasta orig (e clique em Analisar) antes.')
            return
        paths = filedialog.askopenfilenames(title=title, filetypes=[('PNG', '*.png')])
        if not paths:
            return
        # a PNG inside another dump folder (e.g. GpxOAM) brings that dump's manifest along
        for p in paths:
            m = workflow.find_manifest(p)
            if m is not None:
                try:
                    self._load_manifest(m.parent)
                except Exception as exc:
                    messagebox.showerror(title, f'{m.parent}: {exc}')
                    return
        manifests = [(f, {'resources': [r for r in self.records if r['_dump'] == str(f)]})
                     for f, _m in self.manifests]
        pairs, unmatched = workflow.match_pngs(paths, manifests)
        self._stage(pairs, origin)
        self.tree.load(rom_files(self.app.source), self.records)
        msg = f'{len(pairs)} PNG(s) casados pelo nome.'
        if unmatched:
            msg += (f'\n\n{len(unmatched)} sem correspondência (nome diferente do dump):\n'
                    + '\n'.join(Path(u).name for u in unmatched[:12]))
        messagebox.showinfo(title, msg)

    def import_mod(self):
        self._import('mod', 'Importar PNGs Mod. (substituição por nome do PNG)')

    def import_oam(self):
        if not any((r.get('cells') or {}).get('isolated') for r in self.records):
            folder = filedialog.askdirectory(title='Pasta do dump GpxOAM (tem manifest.json)')
            if folder:
                try:
                    self._load_manifest(folder)
                except Exception as exc:
                    messagebox.showerror('Importar OAM', str(exc))
                    return
        self._import('oam', 'Importar OAM (PNGs da pasta GpxOAM, por nome)')

    def _refresh_list(self):
        self.list.delete(*self.list.get_children())
        rows = {key: (png.name, 'OAM' if origin == 'oam' else 'Mod', 'saved')
                for key, (r, png, origin) in self.staged.items()}
        for key, (r, _im) in self._unsaved().items():
            rows[key] = (Path(r['png']).name, 'pendente', 'pending')
        for key, (name, origin, tag) in sorted(rows.items(), key=lambda kv: kv[1][0]):
            self.list.insert('', 'end', iid=key, text=name, values=(origin,), tags=(tag,))
        n_pending = sum(1 for v in rows.values() if v[2] == 'pending')
        text = f'{len(self.staged)} PNG(s) prontos para reinserir (verde)'
        if n_pending:
            text += f'\n{n_pending} edição(ões) NÃO salva(s) (laranja)'
        self.count.configure(text=text)

    def _list_select(self, _e=None):
        sel = self.list.selection()
        if not sel:
            return
        rec = (self.staged.get(sel[0]) or self.pending.get(sel[0]) or (None,))[0]
        if rec is not None and rec is not self.current:
            self.select(rec)

    # ------------------------------------------------------------ edit state
    def _unsaved(self):
        """key -> (record, image) of every edit not saved anywhere yet,
        including the one open in the editor right now."""
        out = dict(self.pending)
        if self.current is not None and self.preview.dirty:
            out[self.key(self.current)] = (self.current, None)       # image taken on save
        return out

    def has_unsaved(self):
        return bool(self._unsaved())

    def _res_tag(self, r):
        key = self.key(r)
        if key in self.pending or (r is self.current and self.preview.dirty):
            return 'pending'
        return 'saved' if key in self.staged else None

    def _mark(self):
        self.tree.retag()
        self._refresh_list()

    def _stash(self):
        """Keep the open graphic's unsaved edits in memory before showing
        another one - nothing is lost and nothing is forced to be saved."""
        if self.current is not None and self.preview.dirty:
            self.pending[self.key(self.current)] = (self.current, self.preview.get_image())

    def select(self, record):
        if record is not self.current:
            self._stash()
        self.current = record
        self.refresh()
        self._mark()

    def _switch_view(self):
        if self.show.get() != self._shown:
            self._stash()
        self._shown = self.show.get()
        self.refresh()

    def _working_copy(self, r, image):
        if self._workdir is None:
            self._workdir = Path(tempfile.mkdtemp(prefix='nitrogfx_edit_'))
        target = self._workdir / f'{abs(hash(self.key(r))) % 10 ** 8:08d}_{Path(r["png"]).name}'
        image.save(target)
        self._stage([(r, target)], 'mod')
        self.pending.pop(self.key(r), None)
        return target

    def _apply_edit(self, image):
        """'Aplicar edição': a working copy of ours (never the dump, never
        a PNG the user imported), staged for reinsertion."""
        r = self.current
        if r is None:
            return False
        self._working_copy(r, image)
        self.status.configure(text=f'Edição aplicada: {Path(r["png"]).name} está na lista de reinserção.')
        self.after_idle(self._after_save)

    def _save_png(self, image):
        """'Salvar no PNG': overwrite the PNG that is loaded - the one the
        user imported, or the dump's own PNG if there is no other."""
        r = self.current
        if r is None:
            return False
        orig, mod = self._images(r)
        ours = mod is not None and self._workdir is not None and Path(mod).parent == self._workdir
        target = mod if mod is not None and not ours else orig
        warn = ('\n\nEste é o PNG do DUMP: o original some e passa a ser a versão editada.'
                if target == orig else '')
        if not messagebox.askyesno('Salvar no PNG', f'Substituir este arquivo?\n\n{target}{warn}'):
            return False
        image.save(target)
        self._stage([(r, target)], 'mod')
        self.pending.pop(self.key(r), None)
        self.status.configure(text=f'Salvo em {target}')
        self.after_idle(self._after_save)

    def _after_save(self):
        self.refresh()
        self._mark()

    def _discarded(self):
        """Editor 'Descartar': forget pending edits and reload the file."""
        if self.current is not None:
            self.pending.pop(self.key(self.current), None)
        self.refresh()
        self._mark()

    def save_all(self):
        self._stash()
        if not self.pending:
            messagebox.showinfo('Salvar todas', 'Não há edições pendentes.')
            return
        n = len(self.pending)
        for r, image in list(self.pending.values()):
            self._working_copy(r, image)
        self.refresh()
        self._mark()
        self.status.configure(text=f'{n} edição(ões) aplicada(s) e na lista de reinserção.')

    def discard_all(self):
        self.pending.clear()
        if self.preview.dirty:
            self.preview.discard()
        self.refresh()
        self._mark()

    def _images(self, r):
        orig = Path(r['_dump']) / r['png']
        mod = self.staged.get(self.key(r))
        return orig, (mod[1] if mod else None)

    def refresh(self):
        r = self.current
        if not r:
            return
        orig, mod = self._images(r)
        path = mod if (self.show.get() == 'mod' and mod) else orig
        pending = self.pending.get(self.key(r)) if self.show.get() == 'mod' else None
        try:
            if pending:
                image = pending[1].copy()
            else:
                with Image.open(path) as im:
                    image = im.copy()
        except Exception as exc:
            self.preview.set_image(None)
            self.info.set_values([('Erro', str(exc))])
            return
        same = self.preview.image is not None and self.preview.image.size == image.size
        info = r.get('info') or {}
        self.preview.set_image(image, keep_view=same,
                               editable=self.show.get() == 'mod' and info.get('editable', True) is not False,
                               bpp=info.get('bpp'), dirty=bool(pending))
        shown = ('EDITADO — não salvo' if pending else 'MODIFICADO' if path == mod else
                 'original — base da edição' if self.show.get() == 'mod' else 'original')
        self.info.set_values([('Gráfico', r['name']), ('Tipo', r['kind']), ('Arquivo', resource_file(r)),
                              ('PNG do dump', r['png']), ('Mostrando', shown),
                              ('Modificado', str(mod) if mod else '— (Carregar imagem ou Importar)'),
                              ('Editável', 'sim' if r.get('info', {}).get('editable') else 'só prévia'),
                              ('Modo OAM', 'sim' if (r.get('cells') or {}).get('isolated') else 'não')])

    def load_image(self):
        if not self.current:
            return
        path = filedialog.askopenfilename(title='PNG editado deste gráfico', filetypes=[('PNG', '*.png')])
        if path:
            self._stage([(self.current, path)], 'mod')
            self.show.set('mod')
            self.refresh()

    def open_editor(self):
        """Open the graphic's PNG in the system's default image editor. Edits
        made here first are saved to it, so the other program sees them;
        the dump itself is never handed out (a working copy is)."""
        r = self.current
        if not r:
            return
        if self.preview.dirty:
            self._working_copy(r, self.preview.get_image())
        orig, mod = self._images(r)
        target = mod or self._working_copy(r, Image.open(orig).copy())
        try:
            open_in_editor(target, load_settings().get('editor'))
        except OSError as exc:
            messagebox.showerror('Abrir no editor', f'{target}\n\nO sistema não conseguiu abrir: {exc}')
            return
        self._watch = (Path(target), Path(target).stat().st_mtime)
        self.refresh()
        self._mark()
        self.status.configure(text=f'Aberto no editor: {Path(target).name} — salve lá e volte; '
                                   'a prévia recarrega sozinha.')

    def check_external(self):
        """Window got focus back: reload the PNG if the external editor
        saved it (only when there are no unsaved edits here to lose)."""
        if not self._watch or self.preview.dirty:
            return
        path, mtime = self._watch
        try:
            now = path.stat().st_mtime
        except OSError:
            return
        if now != mtime:
            self._watch = (path, now)
            self.refresh()
            self.status.configure(text=f'Prévia atualizada com o que foi salvo no editor externo: {path.name}')

    def save_image(self):
        if self.preview.image is None or not self.current:
            return
        path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG', '*.png')])
        if path and self.preview.dirty:
            self.preview.get_image().save(path)          # what is on screen, edits included
        elif path:
            orig, mod = self._images(self.current)
            src = mod if (self.show.get() == 'mod' and mod) else orig
            Path(path).write_bytes(Path(src).read_bytes())    # byte copy: keeps the indexed palette

    def unstage(self):
        if not self.current:
            return
        self.pending.pop(self.key(self.current), None)
        if self.preview.dirty:
            self.preview.discard()
        self.staged.pop(self.key(self.current), None)
        self.refresh()
        self._mark()

    # ------------------------------------------------------------ ROM information
    def _build_rom_info(self, parent):
        card = Card(parent, 'Informações da ROM (aplicadas ao Montar ROM)')
        card.pack(fill='x', pady=(8, 0))
        self.rom_title, self.rom_code = tk.StringVar(), tk.StringVar()
        self._rom_entries = []
        for label, var, tip in (
                ('Título interno', self.rom_title, 'Nome no cabeçalho do cartucho (até 12 caracteres ASCII, '
                                                   'sem acento). Aparece em emuladores e flashcarts.'),
                ('Código do jogo (ID)', self.rom_code, '4 letras/números (ex.: AYWE). Atenção: emuladores, '
                                                       'cheats e alguns flashcarts usam o ID para achar o '
                                                       'save/tipo de save - mude só se souber o que faz.')):
            head = ttk.Frame(card, style='Panel.TFrame')
            head.pack(fill='x', pady=(4, 2))
            ttk.Label(head, text=label, style='Muted.TLabel').pack(side='left')
            help_icon(head, tip).pack(side='left')
            entry = ttk.Entry(card, textvariable=var)
            entry.pack(fill='x')
            self._rom_entries.append(entry)
        head = ttk.Frame(card, style='Panel.TFrame')
        head.pack(fill='x', pady=(6, 2))
        ttk.Label(head, text='Título do banner (menu do DS)', style='Muted.TLabel').pack(side='left')
        help_icon(head, 'O texto que aparece no menu do DS ao lado do ícone: até 3 linhas. Vai para '
                        'todos os idiomas do banner. O ícone em si se edita como qualquer gráfico '
                        '(item "banner do cartucho" na árvore).').pack(side='left')
        self.rom_banner = tk.Text(card, height=3, bg=theme.FIELD, fg=theme.TEXT, insertbackground=theme.TEXT,
                                  relief='flat', font=theme.FONT, wrap='none')
        self.rom_banner.pack(fill='x')
        ttk.Button(card, text='Restaurar originais', style='Tool.TButton',
                   command=lambda: self.set_rom_info(self.app.source)).pack(anchor='e', pady=(6, 0))

    def set_rom_info(self, source):
        title = getattr(source, 'title', '') or ''
        code = getattr(source, 'code', '') or ''
        banner = ''
        data = getattr(source, 'data', None)
        if data and hasattr(source, 'banner_offset') and bannerfmt.is_banner(data, source.banner_offset()):
            titles = bannerfmt.Banner(data[source.banner_offset():]).titles()
            banner = titles.get('en') or titles.get('ja') or ''
        self._rom_info0 = {'title': title, 'code': code, 'banner_title': banner}
        self.rom_title.set(title)
        self.rom_code.set(code)
        self.rom_banner.configure(state='normal')
        self.rom_banner.delete('1.0', 'end')
        self.rom_banner.insert('1.0', banner)
        # a folder/loose file has no cartridge header or banner to change
        self._rom_editable = isinstance(source, NDSRom)
        state = 'normal' if self._rom_editable else 'disabled'
        for entry in self._rom_entries:
            entry.configure(state=state)
        self.rom_banner.configure(state=state)

    def rom_info_changes(self):
        if not getattr(self, '_rom_editable', False):
            return {}
        now = {'title': self.rom_title.get(), 'code': self.rom_code.get(),
               'banner_title': self.rom_banner.get('1.0', 'end-1c')}
        return {k: v for k, v in now.items() if v != self._rom_info0.get(k)}

    # ------------------------------------------------------------ build
    def _update_editor_tip(self):
        prog = load_settings().get('editor')
        self._editor_tip.text = ('Escolher o programa do "Abrir no editor" (Paint, Photoshop, Aseprite...). '
                                 'Vale para todos os gráficos e fica salvo.\n\nAtual: '
                                 + (Path(prog).name if prog else 'padrão do sistema'))

    def choose_editor(self):
        prog = load_settings().get('editor')
        answer = messagebox.askyesnocancel(
            'Editor de imagens', 'Editor atual: ' + (prog or 'padrão do sistema') +
            '\n\nSim = escolher outro programa\nNão = voltar ao padrão do sistema\nCancelar = manter')
        if answer is None:
            return
        if answer:
            types = [('Programas', '*.exe'), ('Todos', '*.*')] if os.name == 'nt' else [('Todos', '*')]
            path = filedialog.askopenfilename(title='Programa para editar os PNGs', filetypes=types)
            if not path:
                return
            save_setting('editor', path)
        else:
            save_setting('editor', None)
        self._update_editor_tip()

    def open_loose(self):
        answer = messagebox.askyesnocancel('Abrir root/arquivo', 'Sim = abrir uma PASTA (root extraído)\n'
                                           'Não = abrir UM arquivo de gráfico\nCancelar = voltar')
        if answer is not None:
            self.app.load_rom(kind='folder' if answer else 'file')

    def export_files(self):
        if self.app.source is None:
            messagebox.showwarning('Montar Arquivos', 'Abra a ROM (ou a pasta root) original primeiro.')
            return
        if self.has_unsaved():
            answer = messagebox.askyesnocancel(
                'Montar Arquivos', f'{len(self._unsaved())} gráfico(s) com edição NÃO salva (em laranja).\n\n'
                                     'Sim = salvar todas e incluir\nNão = exportar sem elas\nCancelar = voltar')
            if answer is None:
                return
            if answer:
                self.save_all()
        if not self.staged:
            messagebox.showwarning('Montar Arquivos', 'Nenhum gráfico editado para exportar.')
            return
        if self.rom_info_changes():
            messagebox.showinfo('Montar Arquivos', 'As informações da ROM (título, código, banner) só '
                                                     'entram no Montar ROM; aqui vão só os gráficos.')
        parent = filedialog.askdirectory(title='Onde criar a pasta com os arquivos modificados')
        if not parent:
            return
        dest = Path(parent) / 'arquivos_modificados'
        n = 2
        while dest.exists() and any(dest.iterdir()):
            dest = Path(parent) / f'arquivos_modificados_{n}'
            n += 1
        pairs = [(r, png) for r, png, _o in self.staged.values()]
        tables = {}
        for _f, m in self.manifests:
            tables.update(m.get('tables', {}))
        source = self.app.source

        def job(progress):
            return workflow.export_files(source, pairs, dest, tables, progress)

        def done(report):
            listing = '\n'.join(report['files'][:15]) + ('\n…' if len(report['files']) > 15 else '')
            messagebox.showinfo('Arquivos montados', f"{report['changed']} gráfico(s) inserido(s) em "
                                                       f"{len(report['files'])} arquivo(s):\n\n{listing}\n\n"
                                                       f"{report['output']}")
        self.app.run_job('Montando arquivos', job, done)

    def build(self):
        if self.app.source is None:
            messagebox.showwarning('Montar ROM', 'Abra a ROM original primeiro.')
            return
        if self.has_unsaved():
            answer = messagebox.askyesnocancel(
                'Montar ROM', f'{len(self._unsaved())} gráfico(s) com edição NÃO salva (em laranja).\n\n'
                              'Sim = salvar todas e incluir na ROM\nNão = montar sem elas\nCancelar = voltar')
            if answer is None:
                return
            if answer:
                self.save_all()
        try:
            info = workflow.validate_rom_info(self.rom_info_changes())
        except EditError as exc:
            messagebox.showerror('Informações da ROM', str(exc))
            return
        if not self.staged and not info:
            messagebox.showwarning('Montar ROM', 'Nada para montar: nenhum gráfico editado e nenhuma '
                                                 'informação da ROM alterada.')
            return
        kind = getattr(self.app.source, 'kind', 'rom')
        if kind == 'folder':
            parent = filedialog.askdirectory(title='Onde criar a CÓPIA da pasta com os gráficos inseridos')
            dest = None
            if parent:
                base = Path(parent) / (Path(self.app.source.path).name + '_mod')
                dest, n = base, 2
                while dest.exists():
                    dest, n = base.with_name(f'{base.name}_{n}'), n + 1
                dest = str(dest)
        elif kind == 'file':
            ext = Path(self.app.source.path).suffix
            dest = filedialog.asksaveasfilename(title='Novo arquivo', defaultextension=ext,
                                                filetypes=[('Mesmo tipo', '*' + ext), ('Todos', '*.*')])
        else:
            dest = filedialog.asksaveasfilename(title='Nova ROM', defaultextension='.nds',
                                                filetypes=[('ROM NDS', '*.nds')])
        if not dest:
            return
        pairs = [(r, png) for r, png, _o in self.staged.values()]
        tables = {}
        for _f, m in self.manifests:
            tables.update(m.get('tables', {}))
        source = self.app.source

        def job(progress):
            return workflow.insert_staged(source, pairs, dest, tables, progress, rom_info=info)

        def done(report):
            names = {'title': 'título interno', 'code': 'código', 'banner_title': 'título do banner'}
            extra = ('\nInformações alteradas: ' + ', '.join(names[k] for k in info)) if info else ''
            messagebox.showinfo('ROM montada', f"{report['changed']} gráfico(s) reinserido(s), "
                                               f"{report['files']} arquivo(s) da ROM reescrito(s).{extra}\n\n"
                                               f"{report['output']}")
        self.app.run_job('Montando ROM', job, done)


# =================================================================== ADVANCED
class RawLabTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.data = None
        self._preview_after = None
        self._previewed = False
        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True)
        controls = Card(pane, 'Laboratório raw')
        pane.add(controls, weight=1)
        self.file, self.palette, self.codec = tk.StringVar(), tk.StringVar(), tk.StringVar(value=AUTO)
        self.bpp, self.width, self.height, self.tile = (tk.IntVar(value=4), tk.IntVar(value=128),
                                                        tk.IntVar(value=128), tk.IntVar(value=8))
        self.order, self.offset = tk.StringVar(value='Tiles'), tk.StringVar(value='0')
        for var in (self.palette, self.codec, self.bpp, self.width, self.height, self.offset,
                    self.order, self.tile):
            var.trace_add('write', self._schedule_preview)
        self._entry(controls, 'Arquivo (da árvore ou do disco)', self.file, self._pick_file,
                    help='Arquivo bruto a decodificar manualmente. Pode vir da árvore da ROM (clique '
                         'num arquivo à esquerda) ou de um arquivo solto no disco. Isso não depende do '
                         'scan automático — o Laboratório raw tenta montar uma imagem a partir dos '
                         'bytes exatamente como você configurar abaixo, útil quando o formato não foi '
                         'reconhecido sozinho.')
        self._combo(controls, 'Compressão', self.codec, CODECS,
                   help='Qual descompressor aplicar antes de interpretar os pixels. "Auto detectar" '
                        'tenta reconhecer o cabeçalho de compressão sozinho (LZ10/LZ11/RLE/Huffman/'
                        'AKLZ etc). Escolha manualmente se souber o algoritmo, ou "Sem compressão" se '
                        'o arquivo já estiver com os dados de pixel crus.')
        self._combo(controls, 'BPP', self.bpp, (1, 2, 4, 8),
                   help='Bits por pixel: quantos bits cada índice de cor ocupa. 4bpp = paleta de 16 '
                        'cores (o mais comum em jogos DS), 8bpp = 256 cores. Se a imagem sair com '
                        'listras/ruído, o BPP errado é a causa mais provável.')
        self._entry(controls, 'Largura (px)', self.width,
                   help='Largura da imagem final em pixels. Precisa ser um valor que o jogo realmente '
                        'usa — se estiver errada, a imagem sai "torta" (cada linha desalinhada com a '
                        'seguinte). Tente múltiplos de 8 primeiro.')
        self._entry(controls, 'Altura (px)', self.height,
                   help='Altura da imagem final em pixels. Sem um cabeçalho descrevendo isso, é '
                        'tentativa e erro — ajuste até a imagem fazer sentido visualmente.')
        warn_row = ttk.Frame(controls, style='Panel.TFrame')
        warn_row.pack(fill='x')
        self.tile_warn = ttk.Label(warn_row, text='⚠ largura/altura não são múltiplas do tile',
                                   style='Warn.TLabel', cursor='question_arrow')
        self._tile_tip = Tooltip(self.tile_warn, '')
        self.tile_warn.pack_forget()
        self._entry(controls, 'Offset', self.offset,
                   help='Quantos bytes pular no início do arquivo (já descomprimido) antes dos dados '
                        'de pixel começarem — útil para pular um cabeçalho que a tool não reconhece. '
                        'Aceita hexadecimal (ex.: 0x20).')
        self._combo(controls, 'Ordem dos pixels', self.order, ('Tiles', 'Linear'),
                   help='Como os pixels estão organizados na memória. "Tiles" = blocos quadrados '
                        'pequenos em sequência (padrão Nintendo DS clássico — modo NCGR "tile"). '
                        '"Linear" = linha por linha, como um bitmap comum (modo NCGR "bitmap"). Se a '
                        'imagem sair em faixas horizontais cortadas, troque este modo.')
        self._combo(controls, 'Tile', self.tile, (1, 8, 16, 32),
                   help='Tamanho de cada bloco (em pixels) quando "Ordem dos pixels" = Tiles. 8 é o '
                        'padrão do hardware DS; valores maiores só se o jogo empacotar tiles maiores '
                        'já agrupados.')
        self._entry(controls, 'Paleta BGR555 (arquivo)', self.palette, self._pick_palette,
                   help='Arquivo com as cores da paleta, no formato nativo do DS (BGR555, 2 bytes por '
                        'cor). Sem isso, a prévia usa uma escala de cinza artificial só para conferir '
                        'a forma dos pixels — as cores reais só aparecem com a paleta certa vinculada.')
        pal_row = ttk.Frame(controls, style='Panel.TFrame')
        pal_row.pack(fill='x', pady=(4, 0))
        pb1 = ttk.Button(pal_row, text='Paleta do jogo…', command=self.pick_game_palette)
        pb1.pack(side='left', expand=True, fill='x')
        Tooltip(pb1, 'Usa uma paleta que o scan automático já reconheceu em algum lugar da ROM/pasta - '
                    'não precisa ser a paleta "dona" deste arquivo, é só um jeito rápido de ver as cores '
                    'prováveis quando você não tem o arquivo de paleta em mãos.')
        pb2 = ttk.Button(pal_row, text='Nenhuma', command=self.clear_palette)
        pb2.pack(side='left', expand=True, fill='x', padx=(4, 0))
        Tooltip(pb2, 'Volta para a escala de cinza artificial (padrão), sem paleta nenhuma.')
        self._palette_override, self._palette_label = None, None
        self.palette_status = ttk.Label(controls, text='Paleta: nenhuma (cinza)', style='Muted.TLabel')
        self.palette_status.pack(fill='x', pady=(2, 0))
        ttk.Button(controls, text='Gerar preview manual', style='Accent.TButton',
                   command=lambda: self.preview_manual(silent=False)).pack(fill='x', pady=(12, 0))
        views.wrap_label(controls, 'A prévia também atualiza sozinha (~1/4s depois de parar de digitar) '
                                   'conforme você muda os campos acima; use o botão para forçar um '
                                   'recálculo imediato ou ver o erro completo de novo.'
                         ).pack(fill='x', pady=(4, 0))
        exp_row = ttk.Frame(controls, style='Panel.TFrame')
        exp_row.pack(fill='x', pady=(6, 0))
        eb1 = ttk.Button(exp_row, text='Exportar PNG…', command=self.export_preview)
        eb1.pack(side='left', expand=True, fill='x')
        Tooltip(eb1, 'Salva a prévia atual (com as configurações e a paleta de cima) como um PNG novo.')
        eb2 = ttk.Button(exp_row, text='Abrir no editor', command=self.open_preview_in_editor)
        eb2.pack(side='left', expand=True, fill='x', padx=(4, 0))
        Tooltip(eb2, 'Salva a prévia atual num arquivo temporário e abre no editor de imagens '
                    'escolhido no Insert (⚙), ou no padrão do sistema. É só para olhar/desenhar por '
                    'cima; o Laboratório raw não reinsere de volta na ROM.')
        ttk.Separator(controls).pack(fill='x', pady=12)
        head = ttk.Frame(controls, style='Panel.TFrame')
        head.pack(fill='x')
        ttk.Label(head, text='Recurso selecionado', style='Section.TLabel').pack(side='left')
        help_icon(head, 'Ações que corrigem um recurso já reconhecido pelo scan automático, mas com '
                        'algum dado incompleto ou ambíguo — diferente do Laboratório raw acima, que '
                        'decodifica um arquivo do zero manualmente.').pack(side='left')
        row = ttk.Frame(controls, style='Panel.TFrame')
        row.pack(fill='x', pady=(8, 0))
        b1 = ttk.Button(row, text='Paleta…', command=self.link_palette)
        b1.pack(side='left', expand=True, fill='x')
        Tooltip(b1, 'Vincula manualmente uma paleta de cores a este recurso, para quando o jogo não '
                    'guarda a relação tileset↔paleta de um jeito que o scan consiga inferir sozinho.')
        b2 = ttk.Button(row, text='Tileset…', command=self.link_tileset)
        b2.pack(side='left', expand=True, fill='x', padx=4)
        Tooltip(b2, 'Vincula um tileset (conjunto de pixels) a um mapa de tela (raw_screen) órfão, '
                    'quando o scan achou o mapa mas não conseguiu achar sozinho de onde vêm os tiles.')
        b3 = ttk.Button(row, text='Largura', command=self.apply_width)
        b3.pack(side='left', expand=True, fill='x')
        Tooltip(b3, 'Força a largura (em tiles/px) de um atlas ou textura crua cuja largura real é '
                    'ambígua — o formato não guarda essa informação, então o scan só "chuta" com base '
                    'em heurística. Use o campo "Largura (px)" acima e clique aqui para aplicar.')
        self.preview = ZoomView(pane, show_oam_toggle=False)
        self.preview.title.configure(text='Preview avançado')
        pane.add(self.preview, weight=3)
        self.info = MetadataView(pane)
        pane.add(self.info, weight=1)

    def _entry(self, parent, label, var, command=None, help=None):
        head = ttk.Frame(parent, style='Panel.TFrame')
        head.pack(fill='x', pady=(7, 2))
        ttk.Label(head, text=label, style='Muted.TLabel').pack(side='left')
        if help:
            help_icon(head, help).pack(side='left')
        row = ttk.Frame(parent, style='Panel.TFrame')
        row.pack(fill='x')
        ttk.Entry(row, textvariable=var).pack(side='left', fill='x', expand=True)
        if command:
            ttk.Button(row, text='…', width=3, command=command).pack(side='left', padx=(4, 0))

    def _combo(self, parent, label, var, values, help=None):
        head = ttk.Frame(parent, style='Panel.TFrame')
        head.pack(fill='x', pady=(7, 2))
        ttk.Label(head, text=label, style='Muted.TLabel').pack(side='left')
        if help:
            help_icon(head, help).pack(side='left')
        ttk.Combobox(parent, textvariable=var, values=values, state='readonly').pack(fill='x')

    def _pick_file(self):
        path = filedialog.askopenfilename(filetypes=[('Binários', '*.bin *.dat *.raw *.img *.ncgr *.nbfc *.ntft'),
                                                     ('Todos', '*.*')])
        if path:
            self.file.set(path)
            self.data = None

    def _pick_palette(self):
        path = filedialog.askopenfilename(filetypes=[('Paletas', '*.nclr *.nbfp *.ntfp *.pal *.plt *.bin'),
                                                     ('Todos', '*.*')])
        if path:
            self.palette.set(path)

    def load_rom_file(self, path):
        """A file picked in the advanced tree: render its bytes directly."""
        if path in ('(banner do cartucho)',) or path.startswith('arm'):
            messagebox.showinfo('Arquivo', f'"{path}" não é um arquivo real da ROM (FAT) — é um recurso '
                                f'que a tool lê de um jeito especial, então não dá para abrir aqui no '
                                f'Laboratório raw. ' + (
                                'Veja o banner na aba Dump: ele carrega e mostra sozinho, sem precisar de '
                                'configuração manual.' if path.startswith('(banner')
                                else 'Ele aparece no scan normal (marque "Varrer overlays/ARM" no topo).'))
            return
        try:
            self.data = self.app.source.read(path)
        except Exception as exc:
            messagebox.showerror('Arquivo', f'Não foi possível ler "{path}" da ROM: {exc}')
            return
        self.file.set(path)
        # each file has its own compression/offset - carrying over what was
        # picked for the PREVIOUS file is exactly what was making every
        # next file fail with 'not type 0x10': it kept forcing whatever
        # codec was last selected onto files that don't use it.
        self.codec.set(AUTO)
        self.offset.set('0')
        self.preview_manual(silent=True)

    def _schedule_preview(self, *_args):
        """Live preview: re-render a short delay after any field changes,
        instead of requiring a button click - the delay avoids re-rendering
        on every single keystroke while a width/height is being typed."""
        if self._preview_after:
            self.after_cancel(self._preview_after)
        self._preview_after = self.after(250, lambda: self.preview_manual(silent=True))

    def _update_tile_warning(self):
        try:
            w, h, tile, tiled = int(self.width.get()), int(self.height.get()), int(self.tile.get()), \
                self.order.get() == 'Tiles'
        except (ValueError, tk.TclError):
            self.tile_warn.pack_forget()
            return
        if tiled and tile > 1 and (w % tile or h % tile):
            self._tile_tip.text = (f'Largura ({w}) e/ou altura ({h}) não são múltiplas do tile ({tile}). '
                                   'A prévia vai falhar ou sair cortada/deslocada - ajuste um dos três '
                                   'valores, ou troque "Ordem dos pixels" para Linear se o arquivo não '
                                   'usar tiles.')
            self.tile_warn.pack(side='left', padx=(6, 0))
        else:
            self.tile_warn.pack_forget()

    def preview_manual(self, silent=False):
        self._update_tile_warning()
        if not self.file.get():
            if not silent:
                messagebox.showwarning('Arquivo ausente', 'Escolha um arquivo na árvore ou no disco.')
            return
        override = self._palette_override if not self.palette.get() else None
        if self.palette.get():
            self.palette_status.configure(text=f'Paleta: arquivo ({Path(self.palette.get()).name})')
        try:
            image, info = render_raw(self.file.get(), self.codec.get(), int(self.bpp.get()),
                                     int(self.width.get()), int(self.height.get()), int(self.offset.get(), 0),
                                     int(self.tile.get()) if self.order.get() == 'Tiles' else 1,
                                     self.palette.get() or None, data=self.data, palette_colors=override)
        except Exception as exc:
            name = Path(self.file.get()).name
            msg = f'Arquivo: {name}\n\n{exc}'
            if not silent:
                messagebox.showerror('Preview manual', msg)
            self.info.set_values([('Arquivo', name), ('Erro', str(exc))])
            return
        self.preview.set_image(image, keep_view=self._previewed)
        self._previewed = True
        values = [(k, v if v is not None else 'ausente') for k, v in info.items()]
        values += self._diag_values()
        self.info.set_values(values)

    def _diag_values(self):
        """If this exact file was flagged by the last scan as 'container ok,
        payload not decoded' (nitrokit.pipeline.scan.UNRESOLVED), surface
        that here too - not just in the Cobertura panel - since this is
        where a user manually poking at that same file will actually be
        looking."""
        path = self.file.get()
        diag = next((d for d in scan_module.UNRESOLVED if d['path'] == path), None)
        if not diag:
            return []
        codecs = sorted({s['codec'] for s in diag['samples']} - {'none (dados crus)'})
        return [('— diagnóstico —', ''),
                ('Contêiner', f"{diag['container']} ({diag['entries']} itens)"),
                ('Compressão', ' + '.join(codecs) if codecs else 'nenhuma (dados crus)'),
                ('Estrutura interna', 'NÃO reconhecida - formato próprio deste jogo, ainda não decifrado')]

    def _choose(self, title, candidates):
        if not candidates:
            messagebox.showwarning(title, 'Nenhuma dependência compatível foi encontrada.')
            return None
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry('650x480')
        win.configure(bg=theme.BG)
        chosen = {'value': None}
        listing = tk.Listbox(win, bg=theme.FIELD, fg=theme.TEXT, selectbackground=theme.ACCENT,
                             font=theme.FONT_MONO, borderwidth=0)
        listing.pack(fill='both', expand=True, padx=8, pady=8)
        for _loc, name in candidates:
            listing.insert('end', name)

        def use():
            if listing.curselection():
                chosen['value'] = candidates[listing.curselection()[0]][0]
                win.destroy()
        ttk.Button(win, text='Usar dependência', style='Accent.TButton', command=use).pack(pady=(0, 8))
        win.transient(self.winfo_toplevel())
        win.grab_set()
        self.wait_window(win)
        return chosen['value']

    def link_palette(self):
        if not self.app.current_resource:
            return
        chosen = self._choose('Selecionar paleta', [(loc, label) for label, loc in self.app.palette_options()])
        if chosen:
            self.app.change_palette(chosen)

    def link_tileset(self):
        r = self.app.current_resource
        if not r or r['kind'] != 'raw_screen':
            messagebox.showwarning('Tileset', 'Selecione um recurso raw_screen sem tileset.')
            return
        seen = {}
        for item in self.app.resources:
            if item.get('char') and item['kind'] != 'raw_screen':
                seen.setdefault(json.dumps(item['char']), (item['char'], item['name']))
        chosen = self._choose('Selecionar tileset', list(seen.values()))
        if chosen:
            pal = next((i.get('pal') for i in self.app.resources if i.get('char') == chosen), None)
            resources.link_tileset(r, chosen, pal)
            self.app.select_resource(r)

    def pick_game_palette(self):
        opts = self.app.palette_options()
        chosen = self._choose('Paleta do jogo', [(loc, label) for label, loc in opts])
        if not chosen:
            return
        try:
            data = self.app.session.get(chosen)
            try:
                colors = g2d.NCLR(data).palette256()
            except Exception:
                colors = g2d.decode_colors(data[:len(data) // 2 * 2])
        except Exception as exc:
            messagebox.showerror('Paleta do jogo', str(exc))
            return
        label = next((label for label, loc in opts if loc == chosen), '?')
        self._palette_override, self._palette_label = colors, label
        self.palette.set('')
        self.palette_status.configure(text=f'Paleta: do jogo ({label})')
        self.preview_manual(silent=True)

    def clear_palette(self):
        self._palette_override, self._palette_label = None, None
        self.palette.set('')
        self.palette_status.configure(text='Paleta: nenhuma (cinza)')
        self.preview_manual(silent=True)

    def export_preview(self):
        if self.preview.image is None:
            messagebox.showwarning('Exportar PNG', 'Gere uma prévia primeiro.')
            return
        path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG', '*.png')])
        if path:
            self.preview.image.save(path, optimize=False)

    def open_preview_in_editor(self):
        if self.preview.image is None:
            messagebox.showwarning('Abrir no editor', 'Gere uma prévia primeiro.')
            return
        name = (Path(self.file.get()).stem if self.file.get() else 'preview') + '.png'
        path = Path(tempfile.mkdtemp(prefix='.nitrogfx-lab-')) / name
        self.preview.image.save(path, optimize=False)
        try:
            views.open_in_editor(path, views.load_settings().get('editor'))
        except OSError as exc:
            messagebox.showerror('Abrir no editor', f'{path}\n\nO sistema não conseguiu abrir: {exc}')

    def reset(self):
        """Called when a new ROM/file is loaded: a game's chosen bpp/width/
        tile/offset are meaningless (and misleading, giving a wrong 'não
        múltiplo do tile' error) for a different game's file."""
        self.data = None
        self.file.set('')
        self.codec.set(AUTO)
        self.bpp.set(4)
        self.width.set(128)
        self.height.set(128)
        self.offset.set('0')
        self.order.set('Tiles')
        self.tile.set(8)
        self.palette.set('')
        self._palette_override, self._palette_label = None, None
        self.palette_status.configure(text='Paleta: nenhuma (cinza)')
        self._previewed = False
        self.preview.set_image(None)
        self.info.set_values([])

    def apply_width(self):
        r = self.app.current_resource
        if not r or r['kind'] not in ('atlas', 'raw_atlas', 'raw_tex'):
            messagebox.showwarning('Largura', 'Selecione um atlas ou textura crua.')
            return
        try:
            resources.to_png(r, self.app.session)
            info = r.get('atlas') or {}
            key = 'width_px' if 'width_px' in info else 'width_tiles'
            r['atlas'] = {**info, key: int(self.width.get()), 'auto': False, 'confidence': 1.0}
            self.app.select_resource(r)
        except Exception as exc:
            messagebox.showerror('Largura', str(exc))


class AdvancedTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=8, pady=8)
        self.tree = RomTree(pane, on_resource=app.select_resource, on_file=self._file)
        pane.add(self.tree, weight=1)
        self.sub = ttk.Notebook(pane, style='Sub.TNotebook')
        self.raw = RawLabTab(self.sub, app)
        self.oam = OamPanel(self.sub, app)
        self.coverage = CoveragePanel(self.sub, app)
        self.sub.add(self.raw, text='Laboratório raw')
        self.sub.add(self.oam, text='OAM / sprites')
        self.sub.add(self.coverage, text='Cobertura')
        pane.add(self.sub, weight=4)
        # the app writes into these when a resource is selected
        raw = self.raw
        self.preview, self.info = raw.preview, raw.info
        self.file, self.palette, self.codec = raw.file, raw.palette, raw.codec
        self.bpp, self.width, self.height, self.tile = raw.bpp, raw.width, raw.height, raw.tile
        self.order, self.offset = raw.order, raw.offset

    def _file(self, path, _found):
        self.sub.select(self.raw)
        self.raw.load_rom_file(path)


# ======================================================================== APP
class NitroApp(tk.Tk):
    def __init__(self, music=True):
        super().__init__()
        theme.apply(self)
        self.title(f'Nitro GFX {__version__} • NDS Graphics Studio')
        self.geometry('1600x940')
        self.minsize(1180, 720)
        try:
            self.iconbitmap(str(ASSETS / 'icon.ico'))
        except Exception:
            pass
        self.source = self.session = self.source_path = None
        self.resources, self.stats = [], {}
        self.analyzed = False
        self._palettes = None
        self.include_code = tk.BooleanVar(value=False)
        self.current_resource = self.current_image = self.current_info = self.job = None
        self.report_callback_exception = self._tk_error
        self.music = None
        self._build()
        if music:
            self.after(400, self._start_music)
        self.protocol('WM_DELETE_WINDOW', self._close)
        # coming back from the external image editor: pick up what it saved
        self.bind('<FocusIn>', lambda e: e.widget is self and self.insert_tab.check_external())

    def _build(self):
        self.header = Header(self, self)
        self.header.pack(fill='x')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, pady=(4, 0))
        self.dump = DumpTab(self.tabs, self)
        self.insert_tab = InsertTab(self.tabs, self)
        self.advanced = AdvancedTab(self.tabs, self)
        self.tabs.add(self.dump, text='  Dump  ')
        self.tabs.add(self.insert_tab, text='  Insert  ')
        self.tabs.add(self.advanced, text='  Modo avançado  ')
        self.tabs.bind('<<NotebookTabChanged>>', self._on_tab_changed)
        bottom = ttk.Frame(self, style='Panel.TFrame', padding=(12, 6))
        bottom.pack(fill='x')
        self.progress = ttk.Progressbar(bottom, maximum=100, length=220)
        self.progress.pack(side='right')
        self.status = ttk.Label(bottom, text='Carregue uma ROM base.', style='Panel.TLabel')
        self.status.pack(side='left', fill='x', expand=True)
        self.browse = self.dump          # older panels refer to app.browse

    # ------------------------------------------------------------ music
    def _start_music(self):
        try:
            from .audio import Bgm
            self.music = Bgm(ASSETS / 'bgm.mp3')
            if self.music.ok:
                self.music.play()
                self.header.music_btn.configure(text='♪ Música: ON')
            else:
                self.header.music_btn.configure(text='♪ (sem áudio)', state='disabled')
        except Exception:
            self.header.music_btn.configure(text='♪ (sem áudio)', state='disabled')

    def toggle_music(self):
        if self.music and self.music.ok:
            on = self.music.toggle()
            self.header.music_btn.configure(text='♪ Música: ON' if on else '♪ Música: OFF')

    def _ask_unsaved(self, action):
        """Insert tab has unsaved edits: save all / discard / cancel.
        Returns False when the user chose to stay."""
        ins = self.insert_tab
        if not ins.has_unsaved():
            return True
        n = len(ins._unsaved())
        answer = messagebox.askyesnocancel(
            'Edições não salvas', f'{n} gráfico(s) editado(s) no Insert ainda não foram salvos (em laranja).\n\n'
                                  f'Sim = salvar todas e {action}\nNão = descartar e {action}\n'
                                  'Cancelar = voltar ao Insert')
        if answer is None:
            return False
        if answer:
            ins.save_all()
        else:
            ins.discard_all()
        return True

    def _close(self):
        if not self._ask_unsaved('fechar'):
            self.tabs.select(self.insert_tab)
            return
        if self.music:
            self.music.close()
        self.destroy()

    # ------------------------------------------------------------ jobs
    def set_status(self, text):
        self.status.configure(text=text)

    def run_job(self, label, function, done):
        if self.job and not self.job['done']:
            messagebox.showinfo('Processando', 'Aguarde a operação atual terminar.')
            return
        state = {'label': label, 'n': 0, 'total': 0, 'done': False, 'result': None, 'error': None}
        self.job = state
        self._lock_tabs(True)

        def progress(n, total, message=''):
            state.update(n=n, total=total, message=message)

        def worker():
            try:
                state['result'] = function(progress)
            except Exception:
                state['error'] = traceback.format_exc()
            state['done'] = True
        threading.Thread(target=worker, daemon=True).start()
        self._poll(done)

    def _lock_tabs(self, locked):
        """While a job runs (load / analyse / build), the other tabs stay
        disabled: switching tabs mid-scan made Tk redraw heavy trees while
        the worker competed for the interpreter, and the UI stuttered."""
        current = self.tabs.select()
        for tab in self.tabs.tabs():
            if tab != current:
                self.tabs.tab(tab, state='disabled' if locked else 'normal')

    def _poll(self, done):
        state = self.job
        total = state.get('total', 0)
        self.progress['value'] = state.get('n', 0) * 100 / total if total else 0
        self.set_status(f"{state['label']}…  {state.get('n', 0)}/{total}  {state.get('message', '')}"
                        if total else f"{state['label']}…")
        if not state['done']:
            self.after(120, lambda: self._poll(done))
            return
        self._lock_tabs(False)
        if state['error']:
            self.progress['value'] = 0
            self.set_status(f"{state['label']}: falhou")
            messagebox.showerror('Erro', state['error'][-2400:])
        else:
            self.progress['value'] = 100
            done(state['result'])
            self.after(900, lambda: self.progress.configure(value=0))

    # ------------------------------------------------------------ load / analyze
    def load_rom(self, kind='rom', path=None):
        if path is None:
            if kind == 'folder':
                path = filedialog.askdirectory()
            elif kind == 'file':
                path = filedialog.askopenfilename(filetypes=[('Gráficos/binários', '*.*')])
            else:
                path = filedialog.askopenfilename(filetypes=[('ROM Nintendo DS', '*.nds'), ('Todos', '*.*')])
        if not path:
            return

        def job(_progress):
            source = open_source(path, kind)
            return source, rom_files(source)
        self.run_job('Carregando ROM', job, lambda result: self._loaded(result, path))

    def _loaded(self, result, path):
        source, files = result
        self.source, self.source_path = source, path
        self.session = Session(source)
        self.resources, self.stats, self.analyzed, self._palettes = [], {}, False, None
        self.current_resource = None
        self.header.set_game(source, path)
        for tree in (self.dump.tree, self.advanced.tree):
            tree.load(files)
        self.dump.rom_info.set_values([
            ('Título', getattr(source, 'title', Path(path).stem)), ('Código', getattr(source, 'code', '—')),
            ('Tamanho', f'{len(getattr(source, "data", b"")) / 1048576:.1f} MB'),
            ('Arquivos', len(files)), ('Pastas', len({str(Path(p).parent) for p, _s in files})),
            ('Caminho', path)])
        self.dump.health.set_values({}, 'Estrutura carregada. Clique em Buscar/Analisar gráficos.')
        self.dump.health.set_game(None, None)
        self.dump.preview.set_image(None)
        self.dump.games.highlight(self.header.game.cget('text'), getattr(source, 'title', ''),
                                  Path(path).parent.name, Path(path).stem)
        self.advanced.oam.set_resource(None)
        self.insert_tab.set_rom_info(source)
        self.advanced.raw.reset()
        self.advanced.coverage.set_source(None, None)
        self.set_status(f'ROM carregada: {len(files)} arquivos. Próximo passo: Buscar/Analisar gráficos.')

    def analyze(self, then=None):
        if self.source is None:
            messagebox.showwarning('Analisar', 'Carregue uma ROM base primeiro.')
            return
        source, include_code = self.source, bool(self.include_code.get())

        def job(progress):
            session = Session(source)
            result, stats = scan(source, session, include_code=include_code,
                                 progress=lambda n, total, name: progress(n, total, name))
            return session, result, stats

        def done(result):
            self._analyzed(result)
            if then:
                then()
        self.run_job('Analisando gráficos', job, done)

    def _analyzed(self, result):
        self.session, self.resources, self.stats = result
        self.analyzed, self._palettes = True, None
        files = rom_files(self.source)
        for tree in (self.dump.tree, self.advanced.tree):
            tree.load(files, self.resources)
        values, note = health_values(self.source, self.resources, self.stats, scan_module.UNRESOLVED)
        self.dump.health.set_values(values, note)
        icon, banner_title = None, None
        try:
            icon, banner_title = banner_icon(self.source, size=40)
        except Exception:
            pass
        self.dump.health.set_game(icon, banner_title or self.header.game.cget('text'))
        self.advanced.coverage.set_source(self.source, self.resources, scan_module.UNRESOLVED)
        self.set_status(f'Análise concluída: {len(self.resources)} gráficos. '
                        'Expanda os arquivos na árvore (verde = tem gráfico).')

    # ------------------------------------------------------------ selection
    def palette_options(self):
        if self._palettes is None:
            seen, opts = set(), []
            for r in self.resources:
                loc = r.get('pal')
                if not loc:
                    continue
                key = json.dumps(loc)
                if key in seen:
                    continue
                seen.add(key)
                label = r['name'] if len(r['name']) < 60 else '…' + r['name'][-58:]
                opts.append((label, loc))
            self._palettes = opts
        return self._palettes

    def select_resource(self, resource, from_oam=False, keep_view=False):
        try:
            image, info = resources.to_png(resource, self.session)
        except Exception as exc:
            messagebox.showerror('Preview', f'{resource["name"]}\n\n{exc}')
            return
        self.current_resource, self.current_image, self.current_info = resource, image, info
        boxes = []
        if resource['kind'] == 'cell':
            try:
                boxes = resources.cell_boxes(resource, self.session)
            except Exception:
                boxes = []
        pv = self.dump.preview
        pv.oam_var.set(bool((resource.get('cells') or {}).get('isolated')))
        pv.set_image(image, boxes, keep_view=keep_view)
        pv.title.configure(text=Path(resource['name'].replace('@', '/')).name or resource['name'])
        self._show_info(resource, image, info)
        current = resource.get('pal')
        own = [('★ identificada: ' + self._pal_name(current), current)] if current else []
        self.dump.palette.set_options(own + self.palette_options(), current)
        self.dump.palette.show(image, info.get('grey', False))
        # the advanced tab mirrors the selection
        adv = self.advanced
        adv.preview.set_image(image)
        adv.info.set_values([('Origem', resource['name']), ('Tipo', resource['kind']),
                             ('BPP', info.get('bpp', '—')), ('Largura', image.width),
                             ('Altura', image.height), ('Editável', info.get('editable', True))])
        if info.get('bpp') in (1, 2, 4, 8):
            adv.bpp.set(info['bpp'])
        adv.width.set(image.width)
        adv.height.set(image.height)
        codecs = [s[1] if s[0] == 'codec' else s[2] for s in resource.get('char', [])
                  if (s[0] == 'codec' and len(s) > 1) or (s[0] == 'lz' and len(s) > 2)]
        adv.codec.set(codecs[-1] if codecs and codecs[-1] in CODECS else AUTO)
        if not from_oam:
            adv.oam.set_resource(resource)

    def _pal_name(self, loc):
        key = json.dumps(loc)
        for label, other in self.palette_options():
            if json.dumps(other) == key:
                return label
        return locator_text(loc)

    def _show_info(self, r, image, info):
        colors = '—'
        if image.mode == 'P':
            colors = f"{2 ** info['bpp'] if info.get('bpp') in (1, 2, 4, 8) else 256} cores"
            colors += ' (sem paleta: cinza)' if info.get('grey') else ''
        elif info.get('rgb') or image.mode in ('RGB', 'RGBA'):
            colors = 'cor direta'
        heuristic = r.get('atlas') or r.get('screen') or {}
        conf = heuristic.get('confidence')
        cells = r.get('cells') or {}
        oam = '—'
        if r['kind'] == 'cell':
            oam = ('peças se sobrepõem' if cells.get('overlap') else 'sem sobreposição') + \
                  (' • modo OAM ativo' if cells.get('isolated') else '')
        self.dump.info.set_values([
            ('Gráfico', r['name']), ('Arquivo', resource_file(r)),
            ('Modelo', r['kind'] + (f" / {r['format']}" if r.get('format') else '')
             + f"  •  layout {r.get('layout', '—')}"),
            ('Compressão', codecs_of(r.get('char'))), ('Contêiner', container_of(r.get('char'))),
            ('Tamanho', f'{image.width} × {image.height} px'),
            ('BPP / cores', f"{info.get('bpp', '—')} bpp • {colors}"),
            ('Paleta', locator_text(r.get('pal')) if r.get('pal') else 'nenhuma no jogo'),
            ('Mapa / célula', locator_text(r.get('map'))),
            ('Montagem', ('automática' if heuristic.get('auto', True) else 'manual')
             + (f' • confiança {conf:.2f}' if conf is not None else '')),
            ('OAM', oam), ('Editável', 'sim' if info.get('editable', True) else 'somente prévia'),
            ('Jogo', self.header.game.cget('text'))])

    def change_palette(self, loc):
        r = self.current_resource
        if r is None or not loc:
            return
        r['pal'] = loc
        self.select_resource(r, keep_view=True)
        self.set_status('Paleta aplicada em tempo real — vale para a prévia, o dump e a reinserção.')

    def toggle_oam(self, on):
        r = self.current_resource
        if r is None or r['kind'] != 'cell':
            self.dump.preview.oam_var.set(False)
            if on:
                messagebox.showinfo('Modo OAM', 'O modo OAM vale para sprites (tipo cell).')
            return
        r.setdefault('cells', {})['isolated'] = bool(on)
        self.select_resource(r)

    # ------------------------------------------------------------ dump
    def _dump_target(self, suffix, parent=None):
        if parent is None:
            parent = filedialog.askdirectory(title=f'Pasta de saída ({suffix})')
            if not parent:
                return None
        parent = Path(parent)
        if parent.exists() and any(parent.iterdir()):
            code = getattr(self.source, 'code', 'ROM') or 'ROM'
            base = parent / f'{code}_{suffix}'
            parent, n = base, 2
            while parent.exists():
                parent = base.with_name(f'{base.name}_{n}')
                n += 1
        return parent

    def dump_root(self, parent=None):
        if not self.analyzed:
            messagebox.showwarning('Dump', 'Rode Buscar/Analisar gráficos primeiro.')
            return
        folder = self._dump_target('Root', parent)
        if not folder:
            return
        source, session, res = self.source, self.session, list(self.resources)

        def job(progress):
            return workflow.dump_resources(source, session, res, folder, workflow.root_png_name, progress)
        self.run_job('Dumpando Root', job, lambda rep: self._dump_done(rep, folder))

    def oam_records(self):
        """Every sprite with the OAM sheet applied: those the user switched
        on, plus every one whose pieces overlap (the flat sheet loses
        pixels there)."""
        from ..formats import g2d
        from ..render import compose as C
        picked = []
        for r in self.resources:
            if r['kind'] != 'cell' or not r.get('map'):
                continue
            info = r.setdefault('cells', {})
            if 'overlap' not in info:
                try:
                    info['overlap'] = bool(C.oams_overlap(g2d.NCER(self.session.get(r['map']))))
                except Exception:
                    info['overlap'] = False
            if info.get('isolated') or info['overlap']:
                rr = json.loads(json.dumps(r))
                rr.setdefault('cells', {})['isolated'] = True
                picked.append(rr)
        return picked

    def dump_oam(self, parent=None):
        if not self.analyzed:
            messagebox.showwarning('Dump', 'Rode Buscar/Analisar gráficos primeiro.')
            return
        picked = self.oam_records()
        if not picked:
            messagebox.showinfo('Dumpar GpxOAM', 'Nenhum sprite com peças sobrepostas nesta ROM: '
                                                 'o Dump Root já é fiel.')
            return
        folder = self._dump_target('GpxOAM', parent)
        if not folder:
            return
        source, session = self.source, self.session

        def job(progress):
            return workflow.dump_resources(source, session, picked, folder, workflow.root_png_name, progress)
        self.run_job('Dumpando GpxOAM', job, lambda rep: self._dump_done(rep, folder))

    def _dump_done(self, report, folder):
        self.last_dump = folder
        self.insert_tab.status.configure(text=f'Último dump: {folder}')
        msg = f"{report['dumped']} PNG(s) em\n{folder}\n\nNão apague o manifest.json dessa pasta."
        if report['errors']:
            msg += f"\n\n{len(report['errors'])} erro(s):\n" + '\n'.join(report['errors'][:8])
            messagebox.showwarning('Dump concluído com avisos', msg)
        else:
            messagebox.showinfo('Dump concluído', msg)
        self.set_status(f"Dump: {report['dumped']} PNGs em {folder}")

    def export_selected(self):
        if not self.current_image:
            return
        path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG', '*.png')])
        if path:
            self.current_image.save(path, optimize=False)
            self.set_status(f'PNG exportado: {path}')

    # ------------------------------------------------------------ misc
    def current_tab_key(self):
        try:
            widget = self.nametowidget(self.tabs.select())
        except Exception:
            return 'dump'
        return {self.dump: 'dump', self.insert_tab: 'insert', self.advanced: 'advanced'}.get(widget, 'dump')

    def _on_tab_changed(self, _event=None):
        current = self.tabs.select()
        previous, self._tab_prev = getattr(self, '_tab_prev', None), current
        if previous == str(self.insert_tab) and current != previous and not self._ask_unsaved('sair'):
            self._tab_prev = previous
            self.tabs.select(self.insert_tab)
            return
        if getattr(self, '_guide_win', None) and self._guide_win.winfo_exists():
            self._fill_guide(self.current_tab_key())

    def _fill_guide(self, key):
        title, body = GUIDE_TEXT[key]
        self._guide_win.title(f'Guia — {title}')
        self._guide_text.configure(state='normal')
        self._guide_text.delete('1.0', 'end')
        self._guide_text.insert('1.0', body)
        self._guide_text.configure(state='disabled')

    def show_help(self):
        key = self.current_tab_key()
        if getattr(self, '_guide_win', None) and self._guide_win.winfo_exists():
            self._fill_guide(key)
            self._guide_win.lift()
            self._guide_win.focus_set()
            return
        win = tk.Toplevel(self)
        win.geometry('640x620')
        win.configure(bg=theme.BG)
        self._guide_win = win
        text = tk.Text(win, bg=theme.PANEL, fg=theme.TEXT, wrap='word', padx=16, pady=16,
                       font=theme.FONT, borderwidth=0)
        text.pack(fill='both', expand=True)
        self._guide_text = text
        ttk.Button(win, text='Guia completo (GUIA.md)', style='Tool.TButton',
                   command=self.show_full_guide).pack(fill='x', padx=8, pady=(0, 8))
        self._fill_guide(key)

    def show_full_guide(self):
        win = tk.Toplevel(self)
        win.title('Guia completo do Nitro GFX')
        win.geometry('920x720')
        win.configure(bg=theme.BG)
        text = tk.Text(win, bg=theme.PANEL, fg=theme.TEXT, wrap='word', padx=16, pady=16,
                       font=theme.FONT, borderwidth=0)
        text.pack(fill='both', expand=True)
        text.insert('1.0', GUIA.read_text(encoding='utf-8') if GUIA.exists() else 'Guia não encontrado.')
        text.configure(state='disabled')

    def _tk_error(self, *args):
        messagebox.showerror('Erro da interface', ''.join(traceback.format_exception(*args))[-2400:])


def selftest():
    """`NITROGFX_SELFTEST=1`: build the whole UI, touch what a packaged
    build can lose (assets, GUIA.md, numpy, the editor's font) and exit 0.
    build_exe.py runs the freshly built executable this way."""
    from .editor import render_text
    problems = [str(p) for p in (ASSETS / 'logo.png', ASSETS / 'icon.ico', GUIA) if not p.exists()]
    app = NitroApp(music=False)
    app.update()
    codes, _mask = render_text('Nitro GFX', 12, True)
    if not (codes == 1).any():
        problems.append('editor font renders nothing')
    app.destroy()
    if problems:
        print('SELFTEST FAILED:', ', '.join(problems))
        sys.exit(1)
    print('SELFTEST OK')
    sys.exit(0)


def main():
    if os.name == 'nt':
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if os.environ.get('NITROGFX_SELFTEST'):
        selftest()
    NitroApp().mainloop()


if __name__ == '__main__':
    main()

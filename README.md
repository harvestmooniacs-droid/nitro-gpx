# Nitro GFX — gráficos de Nintendo DS: dump, edição e reinserção

Ferramenta **genérica** para ver, extrair, editar e reinserir gráficos de
jogos de Nintendo DS. Ela não tem código por jogo: descobre os gráficos pela
**estrutura dos arquivos** (magics, tabelas, compressão). Foi construída e
validada contra um acervo de **62 ROMs** (tabela na seção 4).

- Abre um `.nds` direto, uma **pasta root extraída** (ndstool, DSLazy,
  Tinke) ou **um arquivo solto** (`.narc`, `.bin`, `.NCGR`, `.arc`...).
- Exporta cada gráfico como **PNG indexado** e reinsere o PNG editado.
- Tem editor de pixel embutido: lápis, borracha, seleção, texto e colar
  imagem.
- É **fail-closed**: o lote inteiro é validado antes de gravar. Se algo não
  couber ou não puder ser representado, nada é gravado.

---------------------------------------------------------------------------
## 1. Instalação e uso

Requisitos: Python 3.10+, `numpy`, `Pillow`, `miniaudio` (opcional: só a
música da interface). Tkinter já vem com o Python no Windows e no macOS.

```bash
pip install -r requirements.txt
```

Para abrir a interface, rode o comando abaixo ou dê duplo clique em
`NitroGFX.exe`:

---------------------------------------------------------------------------
## 2. Como a tool funciona

### 2.1 Fluxo
1. **Dump**: abrir a ROM (ou a pasta/arquivo), **Analisar** e exportar os
   PNGs. O dump grava também o `manifest.json`, que registra de onde veio
   cada pixel.
2. **Editar** os PNGs no editor embutido ou em qualquer editor que mantenha
   o modo indexado. O editor externo é escolhido no botão ⚙ ao lado de
   "Abrir no editor", fica salvo para todos os gráficos e pode ser trocado a
   qualquer momento.
3. **Insert**, com três saídas:
   - **Montar ROM**: gera um `.nds` novo, ou uma cópia da pasta/arquivo
     quando a entrada era root/solto. Também aplica título interno, código
     do jogo e título do banner.
   - **Montar Arquivos**: grava só os arquivos do jogo que mudaram
     (`data/menu.narc`, `.bin`, `.NCGR`...), com o caminho interno da ROM.
     É para copiar por cima da pasta root e remontar com a sua ferramenta.
     Ícone e overlays saem com os nomes do ndstool (`banner.bin`, `y9.bin`).
   - **Salvar no PNG**: grava a edição por cima do PNG carregado.

A ROM original nunca é sobrescrita. A saída é sempre um caminho novo.

### 2.2 Como os gráficos são encontrados (sem conhecer o jogo)
1. Remove a compressão do arquivo (detecção estrita, seção 3.2).
2. Abre contêineres de forma recursiva: NARC, CPK, NitroFS, tabelas de
   offsets, ROM aninhada (`.srl`)...
3. Um arquivo Nitro inteiro vira item **solto**, pareado pelo nome.
4. Extensões cruas (`.nbfc/.nbfp/.nbfs/.ntft/.ntfp`, `.imb/.plb/.scb`...).
5. Se nada disso reconhecer o arquivo, entra o **carver**: procura
   cabeçalhos Nitro válidos em qualquer offset, e streams LZ cujo começo
   descomprimido é um cabeçalho Nitro.

**Pareamento**:
- Soltos: mesma pasta e mesmo nome-base. Senão, a paleta de nome mais
  parecido. Senão, a única paleta da pasta.
- Dentro de contêineres, vale a **ordem**: cada NCGR fica com o trecho de
  blocos até o próximo NCGR. A paleta preferida é a NCLR mais próxima antes
  dele.

### 2.3 Localizador (camadas de contêiner)
Cada recurso guarda o caminho até os seus bytes, de fora para dentro:
```
file:data/2d/logo.NCGR_ > codec:lz10
file:book/01/PB101.narc > narc:3 > codec:lz11
file:mcd.dat > lz:0x104 (lz11, 8199 B)
file:data/data.srl > srl:data/ef/ed/x.esp          (ROM NDS aninhada)
```
Na reinserção, cada camada é reconstruída de dentro para fora e várias
edições no mesmo contêiner são juntadas:
- **codec**: recomprime com o **mesmo** codec.
- **narc**: reempacota mantendo nomes e padding.
- **stream embutido**: se o contêiner tem tabela de offsets, o item pode
  crescer. Sem tabela, precisa caber no espaço original; se não couber, sai
  um erro claro dizendo onde.

### 2.4 Tipos de gráfico (o que o PNG é)
| tipo | origem | o PNG é |
|---|---|---|
| `bg` | NCGR + NSCR (+NCLR) | tela montada pelo mapa (flip, sub-paleta) |
| `cell` | NCGR + NCER (+NCLR) | sprite: todas as células (poses) numa folha |
| `atlas` | NCGR sem mapa | grade de tiles 8x8 ou bitmap linear. A largura é detectada por heurística, com índice de confiança |
| `tex` | TEX0 (NSBTX/NSBMD) | textura 3D. Editável em 2/4/8bpp; 4x4 e direct são só prévia |
| `raw_bg` / `raw_atlas` / `raw_tex` | tiles/paleta/mapa crus sem cabeçalho | igual a `bg` / `atlas`, com dimensões inferidas |
| `raw_screen` | mapa cru sem tileset achado | prévia em cinza; vincular com **Tileset…** |
| `banner` | ícone do cartucho ou banner solto | ícone 32x32 4bpp (CRCs recalculados) |
| `font` | NFTR | todos os glifos numa folha (1/2/4bpp) |
| `photo` | JPEG/PNG/GIF/TGA/codec próprio | **só prévia** |
| codecs de jogo | `obp`/`bgp` (Monolith), `sir`/`sir_sprite` (999), `bitmap`/`cbp` (Cing), `layton_ani` (Level-5) | formatos próprios decifrados (seção 3.4) |

**Por que o PNG é indexado**: o gráfico do DS guarda índices de paleta, não
cores. Só os **índices** são reinseridos; a paleta do PNG serve para
visualizar. Um gráfico sem paleta em arquivo nenhum sai em cinza e mesmo
assim é editável.

### 2.5 Modo avançado
- **Laboratório raw**: renderiza um binário cru à mão, escolhendo codec,
  bpp, largura, altura, offset, ordem tile/linear e paleta externa. Serve
  para investigar formatos desconhecidos. Os botões **Paleta…**,
  **Tileset…** e **Largura** corrigem o pareamento de um recurso, e a
  correção fica gravada no `manifest.json`. A paleta da prévia manual
  pode vir de um arquivo BGR555, de uma paleta do jogo já reconhecida em
  qualquer lugar da ROM (atalho para ver cores prováveis sem achar o
  arquivo certo) ou de nenhuma (cinza). **Exportar PNG…** e **Abrir no
  editor** salvam/abrem a prévia atual, sem reinserção.
- **OAM / sprites**: veja 2.6.
- **Cobertura**: lista todo arquivo da ROM em que nenhum gráfico foi
  reconhecido, com tamanho e motivo. Um duplo clique dá um palpite de
  tileset cru.

### 2.6 OAM: sprites com peças sobrepostas
Um sprite Nitro é montado por peças de hardware (OAM) que podem se
**sobrepor**. Uma PNG plana guarda um valor por pixel, então o que fica
embaixo de outra peça não existe no arquivo exportado. Isso faz o sprite
parecer "picotado" ao editar.

A proporção de sprites com sobreposição medida no acervo:

| Jogo | Sprites com sobreposição |
|------------------|-----|
| Radiant Historia | 83% |
| Valkyrie Profile | 53% |
| Kimi no Yuusha   | 5%  |

A tool monta o sprite de duas formas, e a escolha é por recurso:
- **folha normal**: as peças montadas como no jogo. Boa para ver o sprite.
- **folha de OAM**: cada peça no próprio retângulo, sem nada cobrindo nada.
  É a que permite editar sem perder pixel: no Valkyrie Profile deixa 18% a
  mais de pixels alcançáveis.

A escolha fica no `manifest.json`, e a reinserção remonta a mesma folha. Um
tile reusado por duas peças e pintado diferente em cada uma é contradição,
e a reinserção recusa esse caso.

---------------------------------------------------------------------------
## 3. Formatos decifrados e módulos

### 3.1 Bibliotecas Nitro (blocos com magic invertido no disco)
`RLCN`=NCLR paleta · `RGCN`=NCGR pixels · `RCSN`=NSCR mapa de tela ·
`RECN`=NCER células/OAM · `RNAN`=NANR animação · `BTX0`/`BMD0`=texturas e
modelos · `RTFN`=NFTR fonte.

Correções automáticas, todas vistas em jogos reais:
- **NCLR**: o tamanho declarado não é confiável; a tool usa o tamanho do
  bloco.
- **NCGR**: o bit 0 das flags indica **bitmap linear**. Dimensão `0xFFFF`
  é deduzida.
- **NCER**: o *mapping mode* pode ser lixo de memória. A tool escolhe o modo
  em que os OAMs cabem **sem se sobrepor parcialmente**. Também suporta a
  tabela de VRAM e o endereçamento 2D de tile.
- **TEX0**: largura = `8 << bits 4-6`, altura = `8 << bits 7-9`, formato =
  bits 10-12.
- **Índice 0** (8bpp) e `índice % 16 == 0` (4bpp) são transparentes por
  regra do hardware. Viram tRNS no PNG, e o valor real é mantido.

### 3.2 Compressão (dump **e** insert em todos)
|         assinatura           | codec                                                                                |
|------------------------------|--------------------------------------------------------------------------------------|
| 0x10 / 0x11                  | LZ10 / LZ11 (encoder de parsing ótimo) |
| 0x30                         | RLE |
| 0x24 / 0x28                  | Huffman 4/8-bit (encoder próprio) |
| 0x81 / 0x82                  | Diff8 / Diff16 |
| 0x40 / 0x60                  | LZ40 / LZ60 |
| `LZ77`, `SOLCOMP` + 0x10     | LZ10 com prefixo |
| BLZ (fim do arquivo)         | LZ reverso dos overlays (encoder derivado do decoder; atualiza a tabela de overlays) |
| `Le`                         | LZE (Luminous Arc 3) |
| tipo 1/2 + u32               | bitlz (Kaijuu Busters, rastreado no ARM9) |
| bit31 do 1º u32              | AKLZ (Akagi DS, decifrado por **emulação** com unicorn) |
| `CRILAYLA` (ou marca zerada) | CRILAYLA (CRI, dentro de CPK) |
| `AT6P`                       | codec de diferença (999) |

A catalogação completa, com layout de bits e validação, está em
`docs/COMPRESSION_FORMATS.md`.

### 3.3 Contêineres
| módulo | formato |
|---|---|
| `containers/narc.py` | NARC com nomes, repack byte-idêntico |
| `containers/cpk.py` | CRI CPK (`@UTF`, TOC e ITOC) |
| `containers/nitrofs.py` | mini NitroFS (`dwc/utility.bin`, 16 jogos); membros podem crescer |
| `containers/offset_table.py` | detecção genérica de tabela (offset, tamanho), para crescer itens |
| `containers/offset_archive.py` / `member_table.py` | tabela com contagem explícita / implícita |
| `containers/pair_table.py` | `u32 n` + n × (offset, tamanho), sem magic |
| `containers/lzchain.py` | streams LZ10/LZ11 grudados |
| `containers/ntrp.py` | NTRP (Ragnarok DS) |
| `containers/ccb.py` | CCB (CyberConnect2, Solatorobo) |
| `containers/cingpack.py` | `.pack` zlib (Cing, Last Window) |
| `containers/indexpack.py` | packs com índice compartilhado (Theresia) |
| `containers/name_tree.py` | árvore de nomes (Radiant Historia `Data.ndx`) |
| `containers/zipfs.py` | ZIP, com recuperação de nome por CRC32 |
| `rom/ndsrom.py` | ROM NDS e ROM aninhada: FNT/FAT, overlays, banner, CRCs do cabeçalho |

### 3.4 Formatos de jogo decifrados
| módulo | formato | jogo(s) |
|---|---|---|
| `formats/layton_arc.py` | `.arc`/`.arj`: u32 codec + fundo 8bpp ou animação em peças | Professor Layton |
| `formats/sir0.py` | SIR0: fundos, ícones e personagens com boca animada | 999 |
| `formats/monolith.py` | OBP1 / BGP1 | Soma Bringer |
| `formats/bitmaps.py` | BPG1, EBP, CBP1; BMP do Windows | Last Window; Viewtiful Joe |
| `formats/raw.py` | tiles/paleta/mapa crus, Nitro "enxuto" (`NCG\0`...), trio SBMP | vários |
| `formats/nftr.py` | fonte NFTR | 16+ jogos |
| `formats/banner.py` | banner v1/v2/v3/DSi (todos os CRCs) | todos |
| `formats/photo.py` | detecção de formatos fotográficos (só prévia) | Myst |

### 3.5 Kits externos (varredura Tinke, Kuriimu2, dsdecmp, BlocksDS)
Só entrou o que é **reaproveitável entre jogos** e que o censo mostrou
destravar arquivos de verdade:
- `nitrofs` (Tinke), `blz` (dsdecmp), `pair_table` e `lzchain` (Tinke).
- A regra "paleta antes dos tiles".
- Classificação de arquivos que não são imagem: animação 3D, texto, áudio.

Resultado medido no mesmo acervo: **+12.499 recursos em 26 jogos**, e os
dados não identificados caíram de 1551 para 1458 MB. Os ~30 codecs do
Kuriimu2 foram testados nos arquivos não identificados e **nenhum**
apareceu, por isso não foram portados.

---------------------------------------------------------------------------
## 4. Jogos testados

**Legenda**:
- ✅ dump e insert validados ponta a ponta (E2E: edita → reinsere →
  re-lê → compara).
- 🟢 dump correto visualmente, mesmo mecanismo de jogos validados, sem E2E.
- 🟡 parcial.
- 🟠 bem parcial.
- ❌ nada extraído.

A coluna **integridade** é uma estimativa de quanto do conteúdo gráfico
conhecido virou recurso editável. Não é uma métrica pixel a pixel. Um E2E
prova que reinserir não corrompe nada, mas **não** prova que a imagem foi
montada certa.

| Jogo | Como os gráficos estão guardados | Recursos | Integridade | Status |
|---|---|---|---|---|
| Kimi no Yuusha | soltos NCLR/NCBR/NCER/NSCR + NSBTX | 2378 | 100% | ✅ 12/12 |
| Okamiden | soltos + `.fpp` + texturas | 9240 | 100% | ✅ |
| Witch's Wish | soltos, paleta por prefixo | 1665 | ~95% | 🟢 |
| Londonian Gothics | soltos + texturas | 981 | ~95% | 🟢 |
| Elite Beat Agents | soltos LZ10 por arquivo, NCER com mapping lixo | 7135 | 100% | ✅ 9/9 |
| Drawn to Life | soltos + crus `.nbfc/.nbfp/.ntft` | 2018 | 100%\* | ✅ 8/8 · \*~1900 sprites sem paleta em arquivo (cinza é o correto) |
| Etrian Odyssey | crus `.nbfc/.nbfp/.ntft/.ntfp` + texturas | 446 | 100% | ✅ 9/9 |
| The World Ends with You | crus + texturas | 3286 | ~40% | ✅ 3/3 · ❌ `pack` principal |
| Chocobo to Mahou no Ehon | NARC com LZ11, `.z`, `D2KP`, overlays BLZ | 6256 | 100% | ✅ 6/6 |
| Harvest Moon DS: Island of Happiness | soltos + `.xbb` | 6214 | 100% | ✅ 6/6 |
| Luminous Arc | `.iear` (MAIN/JTBL) | 2410 | 100% | ✅ 6/6 |
| Ni no Kuni (T-En) | `NPCK` `.n2d/.n3d` | 39550 | 100% | ✅ 10/10 |
| Nostalgia | `SSAM` .dat com LZ | 1301 | 100% | ✅ 10/10 |
| Lufia: Curse of the Sinistrals | `mcd.dat` com LZ11 + RLE | 3868 | 100% | ✅ 16/16 |
| Suikoden Tierkreis | `.bin` com LZ10 + tabela | 3728 | 100% | ✅ 3/3 |
| Rune Factory 3 | `rf3Archive.arc` + `dwc/utility.bin` | 4553 | ~99% | ✅ 19/19 |
| Wizard of Oz | `.pac` LZ10 + bundle | 2081 | 100% | ✅ 3/3 |
| Soma Bringer | ROM aninhada `data.srl`, OBP1/BGP1 | 532 | ~99% | ✅ 11/11 · ❌ `dangerico.obp` |
| Medarot DS | texturas NSBMD | 3202 | ~60% | 🟢 · ❌ NTEX `.tex/.pal` |
| Solatorobo | `.ccb` | 771 cell + fontes + tex | 100% | ✅ 10/10 |
| Radiant Historia | `Data.bin` + nomes `Data.ndx` | 395 cell + bg + atlas + tex | 100% | ✅ 13/13 |
| Sigma Harmonics | CPK ITOC + CRILAYLA sem marca | 1373 | 100% | ✅ 16/16 |
| Valkyrie Profile: CotP | CPK + CRILAYLA sem marca | 1656 | 100% | ✅ 13/13 |
| Luminous Arc 3 (T-En) | LZE `.imb/.scb/.plb/.LZE` | 219 | ~70% | ✅ 3/3 · 🟡 sprites de batalha |
| 999 | `AT6P` + `SIR0` | 5456 | 100% | ✅ 7/7 |
| Magical Starsign | `.dat` com tabela de offsets | 2753 | ~95% | ✅ 7/7 · 🟡 mapas sem tileset |
| Knights in the Nightmare | `DATA.SFS` monolítico | 0 | 0% | ❌ |
| Summon Night: Twin Age | `dat/*.pac` próprio | 1 | ~5% | 🟠 |
| Deltora Quest | soltos + texturas | 1770 | 100% | ✅ |
| Avalon Code | `SSAM` via carver | 2926 | ~95% | ✅ |
| Emily the Strange | soltos LZ11 + banners soltos | 2504 | 100% | ✅ |
| Ivy the Kiwi | soltos LZ11 + ROM aninhada | 738 | ~95% | ✅ |
| Nora to Toki no Koubou | NARC + `SOLCOMP` | 2849 | 100% | ✅ |
| Shepherd's Crossing 2 | NARC + `SOLCOMP` + SBMP | 1049 | 100% | ✅ |
| Twilight Syndrome | soltos | 2096 | 100% | ✅ |
| Kaijuu Busters | Nitro "enxuto" + `bitlz` | 6502 | ~90% | ✅ · 🟡 bpp de alguns atlas |
| Code Lyoko | ZIP com nomes em hash | 1918 | ~70% | 🟡 pareamento sem nomes |
| Theresia | packs com índice compartilhado | 2555 | ~85% | ✅ · 🟡 39 mapas de 2 camadas |
| Last Window | `.pack` zlib + BPG1/EBP/CBP1 | 3194 | ~90% | ✅ · 🟡 `.bra/.iba` |
| Kaiji (2712) | pares `*_img.bin` + `*_pal.bin` | 457 | ~85% | 🟢 · 🟡 bpp incerto |
| Monster Lab | contêiner próprio | 447 (só tex) | ~40% | 🟠 `spritedata.bin` |
| Teenage Zombies | soltos + `.ntfi` | 259 | ~70% | 🟡 níveis em ruído |
| Touch Detective | `data.bin` próprio | 183 | ~15% | 🟠 |
| Touhai Densetsu Akagi DS | `member_table` + `aklz` | 1 | ~5% | 🟠 compressão ok, conteúdo não |
| Heroes of Mana | `.dat` com tabela implícita + soltos | 584 | ~60% | 🟡 telas cruas sem tileset |
| Final Fantasy: 4 Heroes of Light | NARC + carve + NSBMD/NSBTX | 2047 | ~90% | ✅ |
| Ragnarok DS | soltos + carve + `.ntrp` | 11512 | ~90% | ✅ (sem E2E) |
| Viewtiful Joe: Double Trouble | soltos + `.bmp` do Windows | 4491 | ~90% | ✅ (sem E2E) |
| Myst | `.pu` fotográfico próprio | 0 | 0% | ❌ só prévia `photo` |
| Professor Layton and the Curious Village | `.arc/.arj` Level-5 | 4175 | ~99% | ✅ 19/19 + 1874 arquivos editados |
| Assassin's Creed II: Discovery | `Glob.bin`, só NSBMD/NSBTX | 116 | 100% do que existe | 🟢 menus são quads 3D |
| Harvest Moon DS | `.bin` crus | 2 | ~5% | 🟠 |
| Castlevania: Dawn of Sorrow | `.dat` crus 128×128 | 13 | ~5% | 🟠 |
| Contra 4 | NSBMD + `.TS8/.LYR/.SCN` crus | 317 | ~30% | 🟡 |
| Yoshi's Island DS | `.imbz/.mpdz/.arcz` (LZ10 + chunks) | 1 | ~0% | ❌ |
| Rayman DS | `.nbfc/.nbfs` crus | 28 | ~50% | 🟡 largura manual |
| Inazuma Eleven 2: Blizzard | `.pkb` + `.pac` + NSBMD/NSBTX | 29348 | ~85% | 🟢 · 🟡 `.pkb` com índice no `.pkh` |
| Dragon Quest IX | varredura genérica (ganhou `dwc/utility.bin`) | 30374 | — | 🟢 |
| Pokémon HeartGold | varredura genérica (ganhou `dwc/utility.bin`) | 32013 | — | 🟢 |
| Animal Crossing: Wild World | varredura genérica (ganhou `dwc/utility.bin`) | 12155 | — | 🟢 |
| Digimon World: Dawn | varredura genérica (ganhou `dwc/utility.bin`) | 6305 | — | 🟢 |
| Chrono Trigger | varredura genérica (ganhou `dwc/utility.bin`) | 3051 | — | 🟢 |
| Ultimate Mortal Kombat | varredura genérica (ganhou `dwc/utility.bin`) | 469 | — | 🟢 |

**Última regressão E2E** (2026-09-24): 15 jogos passaram com 100% das
edições reproduzidas e os recursos de controle intactos. Os jogos cuja ROM
não estava na pasta no momento não rodaram; isso é arquivo ausente, não
falha. O round-trip sem edição em 19.535 recursos de 10 jogos deu 0
divergências.

---------------------------------------------------------------------------
## 5. Legendas, integridade e o que a tool NÃO suporta

**O que a tool não suporta**:
- Mudar **tamanho** de imagem, número de tiles, mapa de tela, disposição das
  células ou número de cores da paleta. Só os pixels (índices) mudam.
- **Editar a paleta**: mudar a cor de um índice no PNG não altera o NCLR.
- **Texturas 4x4 e direct color**, formatos fotográficos (`photo`) e
  `.bmp` 4x4: só prévia.
- **Animação** (NANR, NMCR/NMAR, animações 3D): detectadas, não
  renderizadas.
- Sprites **afins** (rotate/scale): renderizados sem a rotação.
- **Texto, script, áudio e vídeo**: fora do escopo.
- Gráficos dentro do **ARM9/ARM7**: só leitura. Nenhum foi achado no acervo.

---------------------------------------------------------------------------
## 6. Lacunas conhecidas

### 6.1 Limites estruturais (não são bugs, são a regra do jogo)
- **Invariante de inserção**: só o desenho muda (seção 5).
- **Contêiner sem tabela de offsets**: uma edição que aumenta o stream
  comprimido precisa caber no espaço original. O encoder ótimo costuma dar
  folga; se não couber, a tool diz exatamente onde.
- **Tiles compartilhados**: o mesmo tile em vários lugares é um dado só.
  Editar muda todos os lugares; se as cópias forem pintadas diferente, vale
  a primeira ocorrência, e o insert conta esses conflitos.
- **4bpp com sub-paletas**: cada tile só usa as 16 cores da própria linha
  da paleta.

---------------------------------------------------------------------------
## 7. Versionamento

### Histórico
- **1.0.0** (2026-09-24), primeira versão pública:
  - dump e insert genéricos, com 62 ROMs testadas;
  - editor de pixel embutido;
  - modo avançado: Laboratório raw, OAM e Cobertura;
  - edição das informações da ROM;
  - exportação só dos arquivos modificados, para quem trabalha com a pasta
    root;
  - editor externo configurável;
  - executável para Windows (PyInstaller).

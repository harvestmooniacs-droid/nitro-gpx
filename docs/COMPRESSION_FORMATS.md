# Formatos de compressão e contêineres — catálogo de referência

Este documento cataloga **todo formato de compressão e contêiner que o
`nitrokit` sabe ler/escrever hoje**, de onde ele veio, e com que grau de
confiança foi validado. É o "o que a tool já sabe fazer" — o oposto do
`SKILL_ADDITIONS.md` (que é o diário de pesquisa, cronológico). Aqui a
ordem é por categoria, não por data de descoberta.

Convenção de confiança usada nas tabelas:

- ✅ **Validado em ROM real** — decodifica E recodifica dados reais de jogo,
  com round-trip binário verificado (edição → reinserção → nova leitura
  idêntica).
- ⚠️ **Validado só sinteticamente** — encoder/decoder corretos em testes
  aleatórios, mas nenhum jogo do corpus atual usa esse formato (ex.: LZ40).
- 🔍 **Detectado, não editável** — a tool reconhece e mostra, mas não
  recompacta (normalmente por ser formato de 3D/preview).

---

## 1. Compressão (`nitrokit/compression/`)

| Codec | Tag/assinatura | Onde apareceu | Status | Módulo |
|---|---|---|---|---|
| LZ10 (BIOS LZ77) | `0x10` + u24 tamanho | quase todo jogo | ✅ | `lz10.py` |
| LZ11 | `0x11` + u24 tamanho | quase todo jogo | ✅ | `lz11.py` |
| RLE | `0x30` + u24 tamanho | vários | ✅ | `others.py` |
| Huffman 4-bit/8-bit | `0x24`/`0x28` + u24 tamanho | Theresia (via Diff8), outros | ✅ | `others.py` |
| LZ10 atrás de `'LZ77'` | ASCII `LZ77` + LZ10 | alguns jogos | ✅ | `__init__.py` (`lz10_hdr`) |
| LZ10 atrás de `SOLCOMP\0` | `SOLCOMP\0` + LZ10 | Shepherd's Crossing 2, Nora to Toki no Koubou | ✅ | `__init__.py` (`lz10_solcomp`) |
| Diff8 / Diff16 (filtro delta do SDK) | `0x81`/`0x82` + u24 tamanho | Theresia (encadeado depois de Huffman) | ✅ | `others.py` |
| AT6P (proprietário, jogo não identificado no nome do módulo) | `'AT6P'`-like | ver módulo | ✅ | `at6p.py` |
| LZE (`'Le'`, jogo: Luminous Arc 3) | `'Le'` + u24 tamanho | Luminous Arc 3 | ✅ | `lze.py` |
| bitlz (LZ com larguras de campo unárias, Kaijuu Busters) | tipo 1/2 + u32 tamanho | Kaijuu Busters `*.lz` | ✅ (rastreado por disassembly ARM9) | `bitlz.py` |
| LZ40 / LZ60 | `0x40`/`0x60` + u24 tamanho | nenhum jogo do corpus | ⚠️ | `lz40.py` |
| AKLZ (proprietário, Akagi DS) | flag bit31 do primeiro u32 do membro | Touhai Densetsu Akagi DS `bg.b`/`chr.b`/`obj.b`/`adt.b`/`bmp.b`/`mes.b` | ✅ (rastreado por **emulação** ARM9 com Unicorn) | `aklz.py` |
| CRILAYLA (CRI Middleware) | `'CRILAYLA'` | Ni no Kuni, Valkyrie Profile CotP (dentro de CPK) | ✅ | `crilayla.py` |
| BLZ (LZ de trás pra frente, overlays) | rodapé de 8 bytes | 1192 overlays comprimidos do acervo | ✅ decode E encode (107 overlays reais recompactados; reinserção atualiza também a tabela de overlays) | `blz.py` |
| Envelope Level-5 (Layton) | `u32` = codec (1 RLE, 2 LZ10, 3/4 Huffman) + stream | Professor Layton `.arc`/`.arj` | ✅ (1874 arquivos editados e relidos) | `formats/layton_arc.py` |

Encoder de referência ótimo (menor saída possível para o mesmo
casador-de-padrões) para LZ10/LZ11: `optimal.py` — usado sempre que o
payload cabe em memória; para arquivos muito grandes cai para o encoder
guloso (`lz10.py`/`lz11.py`).

### Descartados (mesmo formato já coberto, achado ao pesquisar dois
repositórios externos a pedido do usuário)

Comparado contra **PeterLemon/Nintendo_DS_Compressors** e
**Venomalia/AuroraLib.Compression**: LZSS/LZ10, LZ11 ("LZX" no primeiro
repo), RLE, Huffman e a "LZE" de magic `0x654C` já eram exatamente os
formatos acima (confirmado byte a byte). O único achado realmente novo
foi LZ40/LZ60.

---

## 2. Contêineres (`nitrokit/containers/`)

| Contêiner | Assinatura | Jogo(s) | Cresce? | Módulo |
|---|---|---|---|---|
| NARC | `'NARC'` | padrão Nintendo, maioria dos jogos | sim (reempacota) | `narc.py` |
| CCB (CyberConnect2) | `'CCB '` | Solatorobo | sim | `ccb.py` |
| CPK (CRI Middleware) | `'CPK '` | Ni no Kuni, Valkyrie Profile CotP | não (só extrai; CRILAYLA cresce via slack de alinhamento) | `cpk.py` |
| Offset archive (tabela com contagem explícita) | `u32 count` + `count+1` offsets | Magical Starsign `hiraishi/*.dat` | sim | `offset_archive.py` |
| Offset table genérica (achada por correlação, não por assinatura) | nenhuma — encontrada comparando posições já carveadas contra candidatos no cabeçalho | Suikoden Tierkreis, Ni no Kuni, Luminous Arc, Harvest Moon, Nostalgia (SSAM) | sim | `offset_table.py` |
| Name tree (árvore de nomes de arquivo) | nenhuma — heurística estrutural | Radiant Historia `Data.bin`+`Data.ndx` | não precisa (só nomeia) | `name_tree.py` |
| Cing `.pack` | `u32 0` + `u32 BE count` | Last Window | sim | `cingpack.py` |
| ZIP comum (PKZIP) | `'PK\x03\x04'` | Code Lyoko `vfsflat.zip` | sim (recompacta deflate) | `zipfs.py` |
| Index pack (tabela `flags,offset,size` compartilhando um espaço de índice) | `u32 0, u32 0` + tabela | Theresia `*pack.dat` | sim | `indexpack.py` |
| Member table (contagem implícita = `offsets[0]//4`) | nenhuma — heurística estrutural | Touhai Densetsu Akagi DS `bg.b` etc. | sim | `member_table.py` |
| NTRP | `'NTRP'` + tabela (offset, tamanho) | Ragnarok DS `2d_u/**/*.ntrp` | sim | `ntrp.py` |
| Mini NitroFS (`dwc/utility.bin`) | cabeçalho `fnt_off, fnt_size, fat_off, fat_size` + FNT/FAT iguais aos da ROM | **16 jogos** (todo jogo com Wi-Fi da Nintendo) | sim (FAT reescrito; rebuild sem edição idêntico nos 16) | `nitrofs.py` |
| Tabela de pares | `u32 n` + n × (offset, tamanho), sem magic | Inazuma Eleven 2 `.pac` / membros de `.pkb` | sim | `pair_table.py` |
| Cadeia de streams LZ | arquivo = streams LZ10/LZ11 grudados (padding ≤ 16) | Inazuma Eleven 2 `.pkb`, Advance Wars | não (membro editado tem que caber no espaço original; o índice fica no `.pkh` irmão) | `lzchain.py` |

---

## 3. Formatos de imagem/bitmap "crus" (sem contêiner Nitro)

Ficam em `nitrokit/formats/raw.py` (extensões `.nbfc`/`.nbfp`/`.nbfs`,
`.ntft`/`.ntfp`, `.imb`/`.plb`/`.scb`, `.char`/`.plt`, `.sbmph`/`.sbmpic`/
`.sbmpp`) e `nitrokit/formats/bitmaps.py` (formatos com magic próprio):

| Formato | Magic | Jogo | Editável? |
|---|---|---|---|
| BPG1 | `'BPG1'` | Last Window | ✅ sim |
| EBP (4 variantes de paleta) | sem magic, por tamanho | Last Window | ✅ sim |
| EBP 0x4444 (compressão 4x4 do hardware) | sem magic | Last Window | 🔍 preview |
| EBP 0x18 (cor direta 16bpp) | sem magic | Last Window | 🔍 preview |
| CBP1 | `'CBP1'` | Last Window | ✅ sim |
| Monolith OBP1/BGP1 | `'OBP1'`/`'BGP1'` | Soma Bringer | ✅ sim |
| SIR0 (imagem/sprite) | `'SIR0'` | vários | ✅ sim |
| TEX0 (textura 3D, paletada) | dentro de BMD0/BTX0 | vários | ✅ sim (formatos paletados); 🔍 4x4/direct-color |
| Layton BG (`.arc`) | envelope Level-5 | Professor Layton (1–3 pelo Tinke; testado no 1) | ✅ sim |
| Layton animação (`.arc`/`.arj`) | envelope Level-5 | Professor Layton | ✅ sim (um recurso por quadro) |
| `.ncg`/`.ncl`/`.nsc` crus (+ `.l` = LZ) | sem magic, dentro do `utility.bin` | 16 jogos (telas do Wi-Fi) | ✅ sim; mapas sem tileset deduzível viram `raw_screen` |

---

## 4. O que NÃO foi decifrado (lacunas conhecidas)

Cada item abaixo já foi investigado de verdade (não é só "não olhei ainda")
e está documentado com o achado exato em `SKILL_ADDITIONS.md`. Listados
aqui só como resumo executivo:

| Jogo | Arquivo(s) | O que se sabe | O que falta |
|---|---|---|---|
| Knights in the Nightmare | `DATA.SFS` (86 MB) | é um blob monolítico; achei a referência da string no ARM9 (offset `0x1951C`) | rastrear a rotina de abertura via emulação (mesmo método usado em AKLZ) |
| Summon Night: Twin Age | `dat/*.pac` | tabela externa (offset,tamanho encadeados) decifrada; só ~7% do arquivo é coberto por ela | camada de dados por trás da tabela (nested table ou compressão desconhecida) |
| Last Window | `.bra` (retrato animado) | paleta BGR555 real encontrada por volta do byte 0x1C; claramente multi-frame | formato de frame/animação em si |
| Last Window | `.iba` | parecido com CBP1 mas os campos de tamanho não fecham | não decifrado |
| Touhai Densetsu Akagi DS | conteúdo dentro de `chr.b`/`bmp.b` (pós-AKLZ) | compressão 100% resolvida (ver acima); cabeçalhos internos parecem listas de células/OAM | layout de célula/sprite em si |
| Teenage Zombies | `Levels/*.ntfi` | confirmado bit a bit como screen NSCR real (tile+flip+subpaleta) | reconstrução de pixel dá ruído; provavelmente precisa dos arquivos `.til`/`.idx` (chunks de sala) |
| Magical Starsign | `aikyo/Archive.sar.dat` | magic `'sar '` da Brownie Brown | não examinado |
| vários (TWEWY) | Square Enix/Jupiter `pack` (2D) | não é NARC nem offset-table | não decifrado |
| Chunsoft (via Ni no Kuni?) | AT5P `.b3d` | 3D apenas, fora do escopo do tool | não aplicável (não é 2D) |
| Medarot DS | `NTEX` `.tex`/`.pal` | magic reconhecido | payload não decifrado |
| NMCR/NMAR (formato Nitro público) | `RCMN`/`RAMN` | estrutura do bloco MCBK totalmente conhecida (fonte: NitroPaint) | precisa de um parser NANR (sequência de animação) que o projeto não tem |
| Yoshi's Island DS | `.imbz/.mpdz/.arcz` | LZ10 ok; chunks próprios `SET`, `SCEN`, `INFO`, `PLTB`, `OBAR`/`OBJB` | estrutura dos chunks |
| Inazuma Eleven 2 | índice `.pkh` dos `.pkb` | formato no plugin do Tinke (PKH1/PKH2) | escrever o índice para o `.pkb` poder crescer |
| Professor Layton | `data/anisoft/*.arc` (2) | envelope Level-5 ok | animação "software", outro formato |
| NCER com `rotateScale` (afim) | bit conhecido, offset da tabela não | qualquer jogo com sprites rotate/scale | tabela de parâmetros afins não localizada; hoje renderiza sem rotação (silenciosamente errado) |

Ver `SKILL_ADDITIONS.md` para o relato completo de cada investigação
(inclusive as tentativas que falharam e por quê — ex.: por que a
reconstrução de pixel do Teenage Zombies foi revertida em vez de
publicada errada).

# Proposed additions to the `nds-nitro-romhack` skill

Findings from building `nitro_work/nitrokit` (generic graphics
scan/dump/insert) against 44 commercial DS ROMs. Everything below was
verified on real data, with the game named. Written to be merged into
SKILL.md sections 2, 5b and 6.

**This file is the chronological research diary** - it stays in the order
things were discovered, including dead ends, so nobody re-investigates
something already ruled out. Two companion documents organize the same
knowledge for different purposes:
- `COMPRESSION_FORMATS.md` - reference catalog of every codec/container
  the tool knows today, by category, not by discovery date.
- `GUIA.md` (Portuguese) - the executive summary: how to use the tool,
  the full per-game status table, and a dedicated gaps section.

---

## 2.x A generic resource discovery cascade (works without per-game RE)

For every FAT file, in this order (cheap checks first, all cascade):

1. **Compression wrapper**: 0x10 LZ10, 0x11 LZ11, 0x30 RLE, 0x24/0x28
   Huffman, `LZ77`+0x10 prefix. A whole-file stream may be followed by
   padding: accept it when the trailing bytes are only 0x00/0xFF (Elite
   Beat Agents `*.NCGR_` files had >16 bytes of padding and were
   otherwise misread as "embedded" streams that could not grow).
2. **NARC** -> members, each run through the cascade again.
3. **Nested NDS ROM**: a file with a valid NDS header + FNT/FAT (Soma
   Bringer `data/data.srl`, 126 MB, is a complete ROM with 547 files;
   download-play `.srl` files too). Parse it with the same FNT/FAT code and
   rebuild it with the same banner-safe rebuilder.
4. **Whole-file Nitro** (header magic at offset 0).
5. **Headerless NITRO-System binaries** by extension: `.nbfc` (char),
   `.nbfp` (palette), `.nbfs` (screen), `.ntft` (texels), `.ntfp` (texture
   palette) - Etrian Odyssey, Drawn to Life, The World Ends with You. No
   dims/bpp are stored: `.nbfs` is 32 tiles wide (256x192 = 1024 entries),
   bpp is inferred (palette >= 512 bytes and no sub-palette nibbles in the
   screen -> 8bpp) and the guess is saved in the manifest so insert uses the
   same layout.
6. **Carve** everything else: scan for Nitro headers anywhere (magic +
   BOM FFFE + header size 16 + sane block count + first block fits), AND for
   compressed streams whose *partial* decompression (first 16 bytes only -
   cheap) yields a valid Nitro header whose size field equals the stream's
   declared size (allow +4 for alignment). This alone opened, with zero
   reverse engineering: Lufia CotS `mcd.dat` (47 MB), Radiant Historia
   `Data.bin`, Rune Factory 3 `rf3Archive.arc` (103 MB), Sigma Harmonics
   `data.cpk`, Suikoden Tierkreis `*.bin`, Luminous Arc `.iear`, Ni no Kuni
   `.n2d/.n3d` (NPCK), Harvest Moon IoH `.xbb`, Okamiden `.fpp`, Nostalgia
   `SSAM`, Wizard of Oz `.pac`, Chocobo `.pbg/.pak.z`.

**Carving pitfalls (both seen for real):**
- A compressor copies short runs verbatim, so a Nitro header can appear
  *inside the literals of a compressed stream*. Lufia `mcd.dat`: a "raw"
  NCLR at odd offset 0x2129 was really the literal copy inside an RLE
  stream starting 5 bytes earlier -> wrong palette on every portrait.
  Carve compressed streams first and drop raw hits that fall inside one.
- Carve RLE (0x30) too, not only LZ: the same `$FAB` container stores
  NCGR/NCER as LZ11 and NCLR as RLE, choosing the codec per entry.

## 2.x Pairing inside ordered containers: detect the group opener

The Atelier rule "palette = nearest NCLR *before* the tileset" breaks when
a publisher writes the palette *after* the tileset. Lufia `mcd.dat` is
`NCER NCGR NCLR | NCER NCGR NCLR | ...` - the before-rule gives every group
the previous character's palette (shapes right, colours garbage).
Rule that handles Atelier (NCLR-first, NCER-before-NCGR) AND Lufia:
- the kind of the FIRST 2D item opens every group (if the only NCLR in the
  container is first, treat it as a global palette and open groups on NCGR);
- inside a group maps belong to the nearest preceding NCGR (maps before the
  first NCGR go to the first one);
- a group without its own NCLR carries the previous group's palette forward,
  else borrows the next one.

For loose files with real names: same directory, same stem; palette falls
back to the longest-prefix NCLR (Witch's Wish `c10s1a0.NCGR` ->
`c10s1.NCLR`), then to the directory's only NCLR. Normalise extensions:
`.NCBR` = NCGR (Harvest Moon, Kimi no Yuusha, Drawn to Life), trailing `_`
(Elite Beat Agents `.NCGR_` = LZ10-wrapped).

## 2.x NCER mapping-mode field can be garbage

Elite Beat Agents stores uninitialised memory in the KBEC mapping field
(`0x0012FDE4`), like the NCLR size bug. "Pick the first mode where every OAM
fits in the NCGR" is NOT enough: mode 0 always fits when mode 1 does, and
the result is sprites cut into strips. Score each fitting mode by how many
OAM pixel ranges *partially* overlap (real runs are either shared exactly or
disjoint) and take the lowest.

## 2.x TEX0 (NSBTX/NSBMD textures) - verified layout

G3D files have a u32 block-offset table after the 16-byte header (not inline
blocks). TEX0 dictionary: `u8 0, u8 count, u16 size`, then an "unknown"
block whose **u16 size at dict+6 is measured from the dict start**, then
`u16 unit, u16 size`, entries, 16-byte names.
Texture entry: `u16 offset<<3`, `u16 params`:
`w = 8 << (params>>4 & 7)`, `h = 8 << (params>>7 & 7)`,
format = `params>>10 & 7`, color0-transparent = bit 13. The following u8
"width" byte is NOT reliable. Format ids solved from the data-size fields of
1855 files (Okamiden, Witch's Wish, Londonian Gothics; 4x4 matched 1855/1855,
paletted 1182/1186): **2 = 2bpp, 3 = 4bpp, 1/4/6 = 8bpp, 5 = 4x4 compressed,
7 = direct 16-bit**. 4x4 texel palette index = `u16 & 0x3FFF` (x2 colours),
mode in the top 2 bits. Paletted textures reinsert by patching texels in
place; 4x4/direct are preview-only (no encoder).
Palette pairing: name `<tex>_pl`, else same index.

## 2.x Growing an item inside an unknown container

Re-inserting an LZ stream into a container with no known index is bounded by
the original compressed length. Two measures, both needed:

1. **Optimal-parse LZ10/LZ11 encoder** (backwards DP over literal / every
   match length, costs 9 / 17 / 25 / 33 bits). It is *smaller than
   Nintendo's own encoder* on every stream tested (Lufia 8116 vs 8199 B,
   Suikoden 1257 vs 1259 B) and runs in ~0.1 s per 12 KB in Python. A
   greedy hash-chain encoder is 0.2-1% LARGER than the original and
   overflows on the first real edit.
2. **Detect the container's offset table instead of reverse-engineering
   it.** The carver knows every item's start and length; look in the header
   region (before the first item) for u32 values equal to `start - base`
   with a u32 equal to the length next to it (+4, +8 or -4). Try bases from
   0 up to the end of the header region. Found automatically in: Suikoden
   `(offset, packed, raw)`, Ni no Kuni NPCK `(offset, size)`, Luminous Arc
   `JTBL` (base 0x10), Harvest Moon XBB `(offset, size, ?, hash)`, Nostalgia
   `SSAM` (offsets relative to the end of the name table). With it an item
   can grow: insert `delta = align4(new - old)` bytes after it (keeps later
   items' alignment even when items themselves are not aligned), add delta
   to every table offset pointing past it, rewrite its size field, update a
   header field equal to the container length if present.

## 2.x CRI CPK containers (`@UTF` tables) - partial support added

Sigma Harmonics and Valkyrie Profile CotP store their whole 2D asset set
inside one `data.cpk`/`*.cpk` file. This is CRI Middleware's own container,
publicly documented across many modding tools (CriPakTools, vgmtoolbox) as
a big-endian `@UTF` table format - reimplemented from the documented byte
layout (not copied from any tool's source), since the ROM's own scan
previously produced pure static/noise here (byte-scanning the raw CPK blob
found "valid" Nitro headers purely by chance, with garbage bodies).

- `'CPK '` + a 16-byte wrapper (magic, `FF000000`, u32 size, 8 reserved)
  precedes every top-level `@UTF` packet - not just the outer one. `TOC `,
  `ITOC`, `ETOC`, `GTOC` all use the SAME 16-byte wrapper before their own
  `@UTF`; skip it generically rather than assuming `@UTF` starts at the
  pointer.
- **Column storage flag is a nibble, and 0x00 is NOT "not stored"**: the
  real "value omitted for every row" flag is **0x10** (`STORAGE_ZERO`); only
  `0x30`/`0x70` are the "one shared constant value" flags, and `0x50` is
  "stored per row". Treating raw `0x00` as the only "not stored" case
  desyncs every row after the first column that uses `0x10` (silent, no
  parse error until a string offset lands out of bounds much later).
- Two TOC layouts exist: a **named TOC** (`TocOffset`, real `FileName`/
  `DirName` per row, explicit `FileOffset`) and an **ITOC** (`ItocOffset`,
  no `TocOffset` at all - Sigma Harmonics uses this one exclusively, 8652
  files, only numeric IDs in two sub-`@UTF` tables `DataL`/`DataH` split by
  ID range). ITOC gives no per-file offset: files are laid out **in ID
  order**, each aligned up to `Align` bytes, starting after `ContentOffset`
  **plus a fixed CRI signature/padding block** (`(c)CRI` text + 0xFF
  padding) whose size was NOT found in any header field - it had to be
  measured empirically (0x800 bytes past `ContentOffset` in Sigma
  Harmonics) by bisecting until cumulative offsets produced actual valid
  Nitro headers (confirmed: 65/3934 uncompressed entries carry a valid
  Nitro header at the computed offset - real signal, not chance).
- **Most files in both CPKs are compressed, and NEITHER carries a
  `CRILAYLA` magic anywhere in the archive** (checked with a full-file byte
  search) - meaning this is not CRI's own standard "CRILAYLA" codec but an
  unidentified per-title variant. Files where `FileSize == ExtractSize`
  (stored uncompressed - about 45% of entries in Sigma Harmonics) are fully
  supported end-to-end (scan, dump, edit, insert, E2E-validated); the
  compressed ~55% are skipped (silently absent from the scan, not garbage)
  until that codec is identified. This turned both games from "outputs
  static" to "outputs real, editable graphics for the uncompressed subset."

## 2.x Auto-detecting the width of a headerless/mapless atlas (the CrystalTile2 problem, automated)

A resource with NO map at all - `atlas` (NCGR alone), `raw_atlas`
(`.nbfc` alone), `raw_tex` (`.ntft` alone) - has no stored "display width"
in many games: the NCGR's own `w_tiles` field is written by the authoring
tool and is frequently either a placeholder or simply wrong for how the
tile stream is meant to be viewed, since the file format never actually
requires it to be meaningful (only NSCR/NCER-backed resources have a real,
consumed layout). This is the exact ambiguity manual tools (CrystalTile2,
Tinke) hand to a human via a "tiles per row" slider, nudged until the
picture stops looking sheared/chopped. It IS automatable, and the fix
generalises to any raw/atlas resource in any game, not just the one that
exposed it.

**Technique**: the classic "guess a raw framebuffer's scanline width"
trick from generic hex/image viewers and steganography tools - for every
plausible width `W` (a divisor of the pixel/tile count), reconstruct the
image at that width and score it by the mean absolute difference between
vertically adjacent rows of PALETTE INDICES (works even with no palette at
all, since it never looks at colour). A wrong width produces a
diagonal-shear/interleave artifact that is measurably rougher row-to-row
than the real image; the correct width is a clean, often well-separated
minimum. For a tile-mode atlas (each unit is an indivisible 8x8 block, not
a raw pixel run) the same scoring is applied to the actually-composed
index image per candidate tile-column count, not to a raw reshape.

**Validated end-to-end on a real broken export**: Radiant Historia
`Data/Data.bin@44d8`, a bitmap-mode NCGR that DECLARES `w_tiles=32`
(256 px) and renders as pure static at that width (row-continuity score
45.0). Sweeping every divisor of its 16,384-pixel buffer found width=64px
scores 20.1 - a clean, isolated global minimum among all valid divisors
(the next candidates, 32px and 128px, scored 96.1 and 32.3) - and visually
reassembles a correct, sharp character portrait. Rolled out across the
whole `Radiant Historia` atlas set (285 resources): dozens of previously
static-looking exports became recognisable art with zero manual input.
Re-ran the full 28-ROM regression scan and E2E suite afterward: zero
regressions, all previously-correct atlases (which the heuristic mostly
agrees with the declared value on, or picks something visually equivalent
for) stayed visually identical.

**Persistence pitfall (a real bug caught by testing, not hypothetical)**:
the chosen width MUST be cached into the resource's own record (this
project stores it in `resource['atlas']['width_px'|'width_tiles']`) the
first time it's computed, and a later render (dump, insert, or a
verification re-scan) must read that cached value back rather than
re-running the heuristic on (possibly slightly edited) pixel data. Two
different widths can score within a hair of each other for a small/sparse
atlas (confidence near 0, defined as `1 - best_score/second_best_score`);
a routine few-pixel edit is enough to flip which one wins on a *fresh*
re-computation, silently changing the image's reported dimensions between
dump and a later independent re-scan even though nothing about the
resource's real content changed. Caught exactly this way: an end-to-end
test's *verification* step (which re-scanned the rebuilt ROM from scratch
instead of reusing the original manifest) intermittently reported a
shape-mismatch "failure" on a single 32x32, 20-tile Rune Factory 3 atlas
whose top two candidate widths scored 0.03 apart - not a pipeline bug,
a test-methodology bug (a real dump->edit->insert round trip never
re-derives the width; it reads it back from `manifest.json`, exactly like
an auto-detected palette bpp guess or a raw-format bpp guess already did
in this codebase - same pattern, just applied to width).

**Surface the ambiguity, don't hide it**: every auto-detected width also
records a confidence score; anything under 0.15 is flagged (CLI `analyze`
command, GUI `Analyze...` batch button and a `Set width...` per-resource
override with live preview - deliberately modeled on CrystalTile2's own
slider, just pre-seeded with the heuristic's ranked guesses instead of
starting blind) so a human only ever needs to look at the genuinely
ambiguous cases, not sweep every atlas in the ROM by hand.

## 2.x A missing palette never blocks editing pixel DATA - only the preview colour

A resource with no NCLR/palette file anywhere (Drawn to Life: ~1,920 of
2,018 2D resources - colour comes from code or the player's own drawing,
not from a file) still has real, indexed pixel data (NCGR/NCBR bytes) that
decodes and re-encodes exactly like any palette-backed resource. The
palette only feeds `render/compose.make_png`'s *display* colours (falls
back to a flat greyscale ramp, `render.compose.grey_palette`); nothing in
the decode/edit/encode path (`unpack_indices` -> PNG indices ->
`decompose` -> `pack_indices`) ever reads a colour value, so editing a
"grey" PNG's *indices* (shape/silhouette work - what index goes where) and
reinserting behaves identically to a normal resource, byte for byte.
Verified directly: edited one pixel of Drawn to Life's
`Challenge/door_arrow.NCER` (no palette anywhere), reinserted, rebuilt the
ROM, re-scanned it - the new index round-tripped exactly at every position
that legitimately shares that source pixel (tile reuse), and nothing else
in the resource changed. The only real limitation is cosmetic: you're
guessing which index means which colour in-game. The GUI's **Set
palette...** picker (any resource can borrow ANY palette the scan already
knows, purely for preview) is the practical workaround when a close
approximation exists elsewhere in the ROM; when none does, editing "blind"
by index/contrast still fully works for reinsertion, it is just harder to
preview.

## 2.x CRI CPK - the "unknown codec" was CRILAYLA with its magic zeroed (a correction)

An earlier pass concluded that Sigma Harmonics and Valkyrie Profile CotP use
a bespoke codec, because no entry carries the `CRILAYLA` text and a
"headerless CRILAYLA" attempt failed. **That conclusion was wrong.** Every
compressed entry has the exact CRILAYLA layout with the 8 magic bytes set
to zero:
```
0x00 8 zero bytes (instead of 'CRILAYLA')
0x08 u32 uncompressed_size (without the 0x100 plain bytes)
0x0C u32 compressed_size
0x10 bitstream, then 0x100 plain bytes = the first 0x100 of the output
```
Tell-tale that cracked it: for every entry `ExtractSize == usize + 0x100`
and `FileSize == 0x10 + csize + 0x100`, exactly. The earlier attempt failed
because this repo's own CRILAYLA decoder was wrong (it read the second field
as a prefix size at the file tail and read bits LSB-first) and had only been
checked against its own encoder - a self-consistent pair proves nothing
about the real format. Rewritten from the documented format
(`compression/crilayla.py`: bits MSB-first, bytes consumed from the end of
the bitstream, output filled from its end, copy = 13-bit distance+3 and a
2/3/5/8-bit then 8-bit length chain). Result: VP 2063/2063 and Sigma
Harmonics 4718/4718 compressed entries decode to their exact size.
**Lesson: never test a codec only against your own encoder; when a known
format "does not match", suspect your implementation of it first.**

Two things were needed to also INSERT:
- An optimal-parse encoder (bit-cost DP). The greedy one was 0.3% larger
  than CRI's and overflowed real slots; the DP one is ~1.3% smaller than
  CRI's on average. Match search compares slices with doubling + binary
  search: a byte-by-byte loop is quadratic on long identical runs and hung
  on image data.
- Growth into alignment slack: an ITOC CPK aligns every file to `Align`
  (2048), so an entry can grow up to its aligned end without moving any
  other file; the per-row `FileSize` value is patched in place in the @UTF
  row (`cpk.grow_entry`, TOC layouts too - the parser now records where
  each row value sits).

Result: Valkyrie Profile 4 -> 1657 resources (664 cells, 67 bg, 344 atlas,
581 fonts), Sigma Harmonics 6 -> 1381 (1188 bg, 153 cells), E2E 13/13 and
16/16 through recompress + in-place growth.

**Make it visible, not silent**: `nitrokit.pipeline.scan.CPK_STATS` counts
`crilayla` (decoded fine), `unknown_codec` (no magic, skipped), and
`broken` (had the magic but failed to decode/verify) per scan; `scan()`
folds these into its returned `stats` Counter as `cpk_files_crilayla` /
`cpk_files_unknown_codec` / `cpk_files_broken`, so `nitrogfx.py scan`'s
existing `containers:` printout surfaces the count automatically (e.g.
`{'cpk': 1, 'cpk_files_unknown_codec': 4718}`) - a future CPK-based ROM
with an unidentified codec is immediately visible in the scan output
instead of just having fewer resources than expected with no explanation.

## 2.x The cartridge banner icon - a graphic that lives OUTSIDE the filesystem

Present in literally every commercial DS ROM (28/28 in this survey) yet
easy to miss entirely, because it is not a FAT file at all: the header
(offset 0x68) points directly at a fixed-format block sitting in the
untracked gap between the FAT and the first FAT-tracked file (the same
gap section 1's banner-safe rebuild rule already has to preserve
verbatim during any rebuild). Layout (public, GBATEK): u16 version (1 in
27/28 ROMs surveyed, 0x103/DSi-enhanced in the remaining one) + u16
CRC16/MODBUS over bytes 0x20..0x840 + 512 bytes 4bpp 32x32 icon (16 tiles,
4x4 grid) + 32 bytes 16-colour BGR555 palette + 6x256 bytes UTF-16LE
titles. Editing the icon and leaving everything else (palette, titles,
any DSi-only trailing animation data for version 0x103) byte-for-byte
untouched, then recomputing the CRC, is enough - the console/emulator
rejects the banner if the CRC is stale. Needs its own locator step kind
(not ['file', path], since there is no path) that resolves directly
against the header's own offset field and is applied via the ROM
rebuilder's separate `banner_patch` parameter (already built for exactly
this in section 1) rather than through the normal per-file replacement
dict.

## 2.x NFTR - the DS bitmap font (glyph pixel data reverse-engineered from scratch)

A second, entirely separate 2D graphic format hiding in plain sight:
NFTR (magic RTFN, reversed like every other Nitro container) is the
standard DS bitmap font, found in at least 16 of 28 ROMs surveyed here
(loose .NFTR files, and - once the carver was pointed at compressed/
packed blobs - also embedded inside dwc/utility.bin, rf3Archive.arc,
mcd.dat and others). Nobody had implemented it for this toolkit before;
getting from "unknown format" to "100% byte-identical round-trip on 50+
real fonts across 14 games, 1/2/4bpp, full kanji sets included" took a
genuine reverse-engineering pass, worth recording in full since the
technique (not just the result) generalises:

**Container structure** (FINF/CGLP/CWDH/CMAP sub-blocks, stored reversed
as FNIF/PLGC/HDWC/PAMC - these are the well-known public names from the
DS font-hacking community, not something invented here):
- FINF body (20 bytes): a few metric bytes, then three u32 pointers to
  each other block's DATA start (block_offset+8) - useful only as a
  sanity cross-check, not needed for pixel access.
- **CGLP** (the pixel data) 10-byte header: `u8 cell_w, u8 cell_h,
  u16 tile_size, u8 render_w, u8 render_h, u8 bpp, u8 flags, u16 reserved`,
  then glyphs packed back-to-back, `tile_size` bytes each, glyph count =
  floor((block_size - 18) / tile_size) (any remainder is alignment
  padding, not a partial glyph).
- **CWDH** (advance metrics, not pixel data): `u16 first, u16 last,
  u32 next`, then 3 bytes/glyph (left offset, ink width, total advance).
- **CMAP** (character code -> glyph index, chainable via `next`): u16
  first, u16 last, u32 type, u32 next, then type 0 = direct range
  (glyph=code-first), type 1 = a u16-per-code lookup table, type 2 = a
  u16 count + (u16 code, u16 glyph) pair list.

**How the header size and tile_size's meaning were actually nailed
down** (SKILL's own "diff/derive from real data" method, applied twice):
1. First pass, guessing tile_size = "bytes per glyph, ceil(w*h*bpp/8) with
   PER-ROW byte alignment" (the same convention NCGR tiles use) seemed to
   fit one font by sheer coincidence (a 7x7 1bpp glyph: 1 row-byte x 7
   rows = 7, same total as the continuous-bitstream reading). It broke
   immediately on a second, non-square font (12x11 1bpp: row-aligned
   needs ceil(12/8)*11=22 bytes/glyph, but the file's own stored
   tile_size field said 17). **A single non-square sample was what
   exposed the wrong hypothesis** - always validate a size/layout guess
   against at least one asymmetric example, not just the first (often
   square, hence ambiguous) file found.
2. ceil(cell_w*cell_h*bpp/8) as ONE CONTINUOUS bitstream (no per-row
   padding) matched that same 17-byte field exactly, and also matched a
   completely different game/font's declared size. Cross-checked the
   *header size* itself (10, not 12 or 16 as first guessed - some fonts'
   first few "header" bytes after the true 10-byte header are all zero,
   which looks like more reserved header fields but is actually just a
   blank first glyph, e.g. a space character) by requiring the division
   (block_size - 8 - header_size) / tile_size to be an exact integer -
   true on 6 different fonts once trailing 4-byte alignment PADDING
   (up to tile_size - 1 leftover bytes) was accounted for rather than
   expected to divide out exactly.
3. **The genuinely hard part was pixel bit order**, and this is the
   reusable lesson: a small, unusual "icon" font (Rune Factory 3's 7x7
   digit-and-symbol counter font) never produced a clean-looking digit
   under ANY of the 16 combinations of {MSB/LSB-first bit order} x
   {row-major/column-major fill} x {h-flip} x {v-flip} tried, including
   scoring every combination objectively against that font's own CWDH
   ink-width metrics (a narrow '1' should measure visibly thinner than a
   '0' - none of the 16 combos showed a clean, consistent size ordering
   either). **The font itself was the wrong validation target** - it is a
   tiny, stylised numeral/icon set, not real letterforms, so "does this
   look like a recognisable character" is a weak signal for it even
   decoded correctly. Switching to ANY font with a full Latin alphabet
   (Witch's Wish font/LC10.NFTR) resolved it immediately and
   unambiguously: MSB-first, row-major, continuous bitstream decoded a
   pixel-perfect capital 'A' on the very first try. **When a format's bit-
   level packing won't confirm on the first real sample, switch to a
   sample whose correct decoding is unambiguous to the human eye** (full
   alphabet text beats digits, beats a 14-symbol icon set) rather than
   permuting encodings against a hard-to-verify target indefinitely.
4. Zero-edit round-trip was NOT byte-identical on the first working
   decoder: tile_size*8 is usually a few bits roomier than
   cell_w*cell_h*bpp (the RF3 font: 56 stored bits vs 49 needed), and
   those leftover PADDING bits routinely hold non-zero leftover authoring
   garbage in the original file. Fix (same principle as NCGR's
   header_extra passthrough elsewhere in this skill): rebuild() starts
   from a copy of the ORIGINAL glyph bytes and only explicitly sets/clears
   the bits that are real pixels, leaving any trailing padding bits
   exactly as they were - never zero-initialise a buffer that has to
   round-trip bit-for-bit.
5. Pixel VALUES are palette indices same as any other Nitro pixel format
   (0 = background), but a font normally has no companion NCLR at all -
   render with a plain grey ramp sized to 2**bpp entries, exactly the
   raw_atlas/no-palette handling already established elsewhere in this
   project, and it renders 2bpp/4bpp anti-aliased kanji glyphs correctly
   (confirmed visually: Wizard of Oz's Lc10.NFTR, a 2bpp kanji/hiragana/
   katakana font, decodes as crisp, properly-anti-aliased text).

Editable exactly like an atlas: pack every glyph into one grid sheet
(deterministic, same principle as layout_atlas/layout_cells - no side
manifest needed), edit, and reinsert; only the glyph bitmap bytes are ever
rewritten, so glyph count/size/advance metrics/character map never move.
A font embedded inside a compressed container follows the exact same
"must fit in slot, or grow via a detected offset table" rule as any other
compressed resource (section on growing an item) - and because fonts
compress far better than typical tile graphics (long runs of background),
a broad, dense edit relative to a TINY font's own compressed size can hit
that ceiling sooner than the same edit would on an ordinary graphic; the
tool reports it as a normal slot-overflow, not a font-specific failure.

## 2.x Monolith Soft OBP1/BGP1 (Soma Bringer) - solved, and a nested ROM has its own banner

Soma Bringer's real game lives in `data/data.srl`, a complete NDS image
inside the cartridge. Two generic lessons:
- **A nested ROM carries its own banner**, in its own header gap, which a
  scanner that only looks at the outer cartridge never shows. Expose it as
  `[... , ['banner']]` under the nested locator; on commit, apply nested
  file edits first (rebuild the inner ROM - its FAT may move data), THEN
  splice the banner at the offset the REBUILT inner header points to (0x68),
  then propagate outward as a normal file edit.
- Everything 2D inside is Monolith's own format, not Nitro. Both formats
  were solved from the files alone:

**OBP1** (`.obp/.ntp/.obps`; `.img` = several records back to back, each
padded to 512 bytes - split on the magic, a record's slice runs to the next
magic so padding stays inside it):
```
0x00 'OBP1'  0x04 u16 a, u16 b  (block = 8<<(a&3) x 8<<(b&3); high bits flags)
0x08 u16 w, u16 h   0x0C u16 flags (bit0 = 8bpp else 4bpp, bit9 = extended)
0x0E u16 palette bytes   0x10 BGR555 palette, then pixels
```
Pixels are stored **block by block**, each block its own small raster, blocks
row-major (a block bigger than the image is clipped to it). Reading them as
one w-wide raster gives sheared garbage - the row-continuity width heuristic
is what exposed it (a 128-wide portrait scored best at width 32 = the block
size). Plain files: `16 + pal + w*h*bpp/8 == file size` exactly.
Extended (flag 0x200): at 0x10, u32 `cell_off, n_cells, block_off, n_blocks,
pal_off, pal_size, px_off, px_size`; block records are `u32 flags, u16
data_offset/32, u8 x, u8 y`. Accept only when the table is the plain grid
and agrees with the 0x04 block-size field (116/117; the one that disagrees
renders wrong both ways and is refused). A trap on the way: the portraits
also set bit 12, which looked like the "extended" marker until the stage
maps showed bit 9 alone is.

**BGP1** (`.bgp`): NCLR+NCGR+NSCR in one file -
`0x04 u32 size, u16 w, u16 h, u8 tile(8), u8 bpp, u8, u8`, then at 0x10
u32 `pal_off, pal_size, tile_off, tile_size, map_off, map_size`; map entries
are NSCR-style u16. `map_off + map_size == size` on all 23 files, so it
reuses the ordinary bg compose/decompose unchanged.

Result: 441 OBP1 + 24 BGP1 + both banners, zero-edit round trip on all,
E2E 11/11 edits reproduced and 200/200 untouched resources unchanged
through inner-ROM + outer-ROM rebuild.

## 2.x TWEWY's `pack` container - fully solved; the pixel codec inside is not

Square Enix/Jupiter's DS engine (TWEWY) puts almost all 2D graphics inside
`.bin` files whose magic is literally `pack` (lowercase, no relation to
Nitro NARC). This container is now **fully reverse engineered and byte-
verified** (entry sizes sum to the declared total, exactly, at every level
tested):
```
"pack" + u32 n_table_dwords + u32 total_size + 8 zero bytes (reserved)
       + a table of n_table_dwords/2 (offset, size) u32 pairs, offsets
         absolute from the START OF THIS PACK BLOCK
```
Any entry's span can itself be another `pack` block - sometimes directly
(`entry_bytes[:4] == b'pack'`), sometimes behind one extra fixed 32-byte
record (see the trap below) that precedes the nested magic. Recursing
this two-shape check finds every leaf blob in a `.bin` with zero guessing;
confirmed on this game's ARM9 disassembly symbol table (a public
splat-decomp project, github.com/Yotona/twewy) which independently names
the same mechanism `PacMgr_LoadPack` / `PacMgr_LoadPackEntryData` /
`PacMgr_GetPackEntryDataPtr` - i.e. this is not a guess, it's confirmed
against the real loader's own naming.

**A trap that cost a lot of time and is worth flagging generically**: each
resource *group* (a background, a sprite) starts with its own small
32-byte record shaped `(u32 type_tag, u32 value, 24 zero bytes)`. The
`value` field is suspicious because it exactly equals the CONTAINING
pack's own declared `total_size` - which looks exactly like "declared
decompressed size of the sibling entry", especially since the sibling
pixel-data entry's stored length is reliably *smaller* than that value by
a plausible-looking ~15-20%. That apparent match is a coincidence, not a
decompression hint: `value` is just a redundant copy of the parent pack's
own `total` field (probably a loader sanity-check), unrelated to any
individual sibling's real, uncompressed size. **Lesson: when a header
field equals another field you already know independently, treat that as
the null hypothesis (redundant validation copy) before building a theory
that requires it to mean something new** - here it cost a full detour
trying to fit LZ10/LZ11/RLE/BIOS-Huffman body decoders (with and without
a discovered header, at every plausible bit order/flag polarity/length
encoding, ~40+ parameter combinations tried) against a "target size" that
was never real.

Once past that trap: a group's 4 real entries are, in table order,
(1) the type-tag record above, (2) the pixel-index blob, (3) a
placement/cell-like table (repeating records separated by a `0x0000FFFF`
u32 sentinel - not decoded), (4) a **raw BGR555 palette, no header at
all** - confirmed unambiguously on multiple samples (`Apl_Kit/Grp_FldBg.bin`,
`Apl_Sug/Grp_baycmnbg.bin`): index 0 decodes to exactly RGB(248,0,248),
the same "impossible magenta = transparent" authoring convention already
documented for NCLR, followed by coherent, non-random-looking colour
ramps. Palette size varies per group (64 or 256 colours seen); on one
64-colour sample, masking each pixel-index byte with `0x3F` collapses the
values to exactly the palette's range with a clean top-2-bits split
(0/1, rarely 2) - strong evidence of a 6-bit index + 2 flag bits packing
on THAT sample, not yet confirmed as universal.

**What's still unsolved: the pixel-index blob's own layout/encoding.**
Every avenue tried came back negative rather than inconclusive, which is
worth recording precisely so nobody repeats them:
- Row-continuity width-guessing (the same heuristic that nails
  `atlas`/`raw_atlas` width elsewhere in this codebase, see the dedicated
  section) found NO width with a clear score minimum - scores were flat
  across every divisor of the blob's byte length, the signature of data
  that is NOT a linear raster at any width, not just an ambiguous one.
- The blob's byte length does not consistently share a common width
  factor across ~80 samples from the same file (GCD of lengths = 1, and
  some lengths are odd) - rules out "same background width, different
  height" for this file.
- Byte-value entropy is ~7.4 bits/byte (near-random) on a full 0-255
  spread, unlike a padded/blank-heavy sprite - consistent with either a
  compressed stream or a dithered photographic-style background, not
  distinguishing between them on its own.
- No BIOS-standard codec matches: `nitrokit.compression.try_decompress`
  (LZ10/LZ11/RLE/Huffman, the real, verified decoders already in this
  repo) fails at every small byte-shift; ~40+ hand-parameterised LZSS/RLE
  variants (flag bit polarity, MSB/LSB flag order, 2 vs 3-byte tokens,
  disp/length nibble order, big vs little-endian) all failed too.
- The ARM9 disassembly's symbol table (github.com/Yotona/twewy) only
  exposes the stock Nintendo SDK decompressors (`LZ77UnComp*`,
  `RLUnComp*`, `HuffUnCompReadByCallback`, `BitUnPack`) - no game-specific
  decompressor symbol exists near `PacMgr_*`. That doesn't rule out a
  custom bit-packing scheme (SDK symbols don't prove the SDK function is
  actually *called* for this data), but it means there is no shortcut
  symbol name to go find and read.
- **Next real step, if this is revisited**: static guessing has been
  exhausted for one session; the reliable way to close this is dynamic
  analysis - run the ROM in an emulator with debugging (melonDS/desmume
  with a Lua/GDB hook, or no$gba's debugger) and set a breakpoint on
  `PacMgr_LoadPackEntryData` (and whatever function reads its result into
  VRAM/a texture), then diff the bytes before/after to observe the actual
  transform in action, the same way the CRILAYLA and CPK formats in this
  project were originally cracked by diffing known-good references rather
  than guessing cold.

## 2.x Chunsoft AT6P + SIR0 (999) - a byte-delta codec and a relocatable container

Starting point was matheuscardoso96's 9H9P9DTools (Lib999: `ATP6.cs`,
`SirBg.cs`; its AT6P decoder in turn comes from Tinke's 999 plugin).
Reimplemented as reusable modules, then extended past what that tool does:

- **AT6P** (`compression/at6p.py`, registered as codec `at6p` so any ROM
  gets it): NOT an LZ. Header `'AT6P'`, `u32@4 = file_len << 8 | tag`,
  `u32@0x10` = decoded length; the stream at 0x14 is LSB-first bits: first
  byte literal (+8 pad bits), then per symbol `k` zeros, a one, a `k`-bit
  value; `n = 2^k - 1 + v`. `n=0` repeat the current byte, `n=1` emit the
  byte before it and swap, `n>=2` add `n/2` (even) or subtract (odd).
  Encoder written here; all 5034 files decode, re-encode then decode is
  identical and the re-encoded size equals the original. Pure Python decodes
  the whole ROM in ~70 s (a fast path for runs of 1-bit 'repeat' symbols
  helped only ~20%: the images are dithered).
- **SIR0** (`formats/sir0.py`): `'SIR0'`, content offset, pointer-table
  offset. The pointer table is big-endian 7-bit varints of DELTAS between
  file offsets that hold pointers, 0-terminated. It is the generic way to
  find records: three adjacent pointer slots `(palette, pixels, pieces)`
  identify a sprite with no per-game knowledge.
- **Backgrounds** (AT6P -> SIR0, 5034 files): the original tool takes
  width/height/bpp from a hand-written list. They are in the file: content
  record `u32 x0, y0, x1, y1` (inclusive rectangle in 8px tiles), `u32 ?,?`,
  `u32 pixels, palette, palette_end`. `(x1-x0+1)*8 x (y1-y0+1)*8` matches
  the pixel byte count on all 5034; the hand list agrees on 934/950 and the
  16 others are wrong in the list (their byte counts cannot hold the listed
  size; one "256x192" is really a 256x384 scrolling scene). Lesson: when a
  tool needs dimensions by hand, look for a rectangle in the header before
  building a table.
- **Character sprites** (`cha/*.dat`, plain SIR0, 274): palette, pixels,
  and 10-byte pieces `u16 w, h, x, y, byte_offset` until a zeroed record.
  Pixel bytes past the last piece are animated-part frames (mouths: 5 x
  8x8), exposed as an extra editable strip under the body.
- **Item icons** (`item/*.dat`, SIR0, 148): record is just `u32 pixels,
  palette`, palette runs to the record; 32x32 4bpp **stored as 8x8 tiles**
  (a raster read gives stripes; tile order gives the icon).

## 2.x CyberConnect2 CCB (Solatorobo) - a simple named archive, not a codec

It was listed as "proprietary, no Nitro visible after carving". Carving saw
nothing because every body is LZ11/RLE with its 4-byte header moved into the
table. Layout (all 1881 files: contiguous bodies ending exactly at EOF, every
entry decompresses to its declared size): `'CCB ' u8 0, u8 1, u16 count`;
32-byte entries `char[24] name, u32 flags | csize << 8, u32 codec | dsize << 8`
(codec = Nitro tag 0x10/0x11/0x30, 0 = stored); bodies back to back. Names
are real (`if0011nor00.ncbr/.nclr/.ncer`), so pairing is by name inside each
archive. Sizes live in the table, so an entry may grow: the archive is just
rewritten. Result: 771 sprites (all with palette), 3 fonts, textures;
E2E 10/10 through recompress + archive rewrite. **Lesson: before declaring
a container proprietary, look at the bytes right after its table - a
compression flag byte followed by a literal magic (`00 'RTFN'`) is an LZ
stream with its header stored elsewhere.**

## 2.x Radiant Historia Data.bin - name the carved files instead of cracking the index

`Data.bin` is every file of a tree back to back (4-byte aligned, no headers);
`Data.ndx` holds the names (`u16 count`; entries `u16 len, name, u32 child`
with 0 = leaf); depth-first leaf order = data order. `Data.idx` (the offset
index) was never needed: the carver already knows every Nitro file's range,
so the names are lined up with the carved items by an order-preserving
alignment of KINDS (extension vs magic; `.bin`/no-extension leaves are
wildcards for LZ-wrapped `*_LZ.bin` tilesets). 2D kinds line up exactly
(NCLR 620=620, NCER 395=395, NANR 395=395, NSCR 11=11; the only mismatches
are 3D models inside packs). Generic hook: a carved file `X.bin` with a
same-stem sibling that parses as this tree gets names
(`containers/name_tree.py`). (Data.idx itself: 8-byte header, then mostly
6-byte `u24, u24` records whose first field is sorted - a lookup table, not
a per-file offset list; an 11-byte period appears later. Not decoded, not
needed.)

Then pairing/composition rules, all generic:
- **Parallel category folders**: `x/Cell/a.NCER`, `x/Character/a.NCBR`,
  `x/ColorPalette/a.NCLR`, `x/Screen/a.NSCR` pair as one directory.
- **A map without a same-name tileset** borrows the most similarly named
  one (the palette-similarity rule): `Screen/bg_03.NSCR` ->
  `Character/bg_01.NCGR`; only if nothing is similar, fall back to the
  ordered-container rule.
- **Bitmap NCGR + NCER has two index conventions**: linear (Atelier: a
  w-wide run from `tile << mapping`) and a **2D grid** (Radiant Historia
  field sprites: `tile` is an 8x8-tile position in the bitmap, the OAM copies
  the w x h rectangle there). When both fit geometrically, compose both and
  keep the smoother (row-continuity score); cached in the manifest.
- **OAM transparency when composing**: a lower OAM must show through index-0
  pixels of the OAM above it (hardware never draws index 0). Without it,
  overlapping body parts came out cut.

Result: 395/395 NCER assembled (was 10 - the rest were raw tile atlases),
11/11 NSCR, zero round-trip failures, E2E 13/13.

## 2.x Imageepoch LZE ('Le', Luminous Arc 3) - read from the game's code, not guessed

Three rounds of structured brute force (2-bit codes, literal/copy mixes,
bit splits, bases) found nothing, although hand-reading a screen file had
already shown code 3 = 3 literals and code 2 = 1 literal. What worked:
find the loader instead of the magic. The game opens files by name, so
`.imb/.plb/.scb` path strings sit in ARM9 with literal-pool pointers to
them; the function using them calls a "copy to VRAM" helper, which reads a
u24 size at +2, allocates, and calls the decompressor - in **ITCM**
(0x01FF831C), i.e. inside the ARM9 autoload blocks, not at its RAM address in
the ARM9 file. Autoload table: module params before the `0x2106C0DE`
nitrocode word = `autoload_list_start, autoload_list_end, autoload_start,
bss_start, bss_end, compressed_static_end, sdk_version`; each list entry
`u32 ram_address, u32 size, u32 bss`, blocks concatenated from
`autoload_start`. The routine checks `'L','e'` and then (flags = 4 two-bit
codes, low bits first):
```
0 long copy  u16 v: length (v>>12)+3, distance (v&0xFFF)+5
1 short copy u8 b:  length (b>>2)+2,  distance (b&3)+1
2 one literal       3 three literals
```
The brute force had missed it only on a 6-bit length field and a distance
base of 5. `compression/lze.py` (codec `lze`): all 347 files decode,
encoder round-trips (avg 1% smaller). **Lesson: when a format resists
search after the first structural facts are known, trace the loader from a
filename string - one level of calls gets to the codec.**

Contents are GBA-style raw graphics, now in `formats/raw.py`: `.imb` tiles,
`.plb` / `pal*.bin` palettes, `.scb` screen with a header `u32 cols, rows,
header_size` (detected when the three fields describe the file exactly),
`obj*.LZE` object tile sheets. Raw pairing falls back to the most similar
palette name (`objA0008.LZE` -> `palA0008.bin`). 71 backgrounds, 148 object
sheets. Not done: battle unit sprites (`styleF1/*/bu*_pack.bin` = 132-byte
frames of `u32 n` + 16 x `u16 flags, u8 tile, u8 shape, s16 x, s16 y`;
pixels in `usr1/chrbtl/chr/NNN/bu_c_NNN.bin`, 4bpp in 32px-wide pieces;
palettes in `usr1/btlplt/BTLPLTCH.bin`) - the piece addressing is not
decoded; the object sheets' on-screen layout is in code, so they stay tile
atlases.

## 2.x Magical Starsign - a magic-less offset archive, paired by suffix or by neighbour

`hiraishi/*.dat`: `u32 n`, then `n+1` absolute u32 offsets (last = file
length, empty slot = 0; an entry runs to the next non-zero offset), entries
mostly LZ10 streams of raw data (`containers/offset_archive.py`, locator
step `oarc`, rewrite recomputes offsets so entries may grow). No magic, so
detection is strict (increasing non-zero offsets after the table, last ==
length). What an entry is:
- archives named with a Nitro suffix (`map_ncg`, `map_ncl`, `fldeff_ncg`...)
  - every entry is that kind and entry i pairs with entry i of the sibling;
- mixed archives (`mesFace`, `menu`, `demo`...) - a palette-sized entry
  (32..512 B, every word < 0x8000) right after a tile-sized one is its
  palette; accepted only if such pairs cover >= 20% of the archive's live
  entries (data archives like `map_shp` reach 10% by chance).
Also generic: a raw screen pairs with a tileset only if its tile indices fit
(index-matched archives of different lengths otherwise pair garbage), and a
256-colour palette with no screen is tested as 16 sub-palettes of 4bpp
tiles (smoother reading wins). Result: 256 dialogue portraits, opening
cut-ins, icons, menus, map tilesets (998 sheets); E2E 7/7. Not done: map
screens (`map_amp.dat`, 2 layers per map) store no dimensions.

## 2.x Headerless screens with no width/height anywhere (generic, not one game)

The exact same ambiguity as an atlas with no declared width (section above)
also happens to NSCR-style tile MAPS: Magical Starsign's `map_amp.dat`/
`map_shp.dat` are genuine screen arrays (u16 entries, tile index + flip +
sub-palette) with no width/height field anywhere in the file or its
container. `render.heuristics.best_screen_dims` is the same row-continuity
technique as `best_bitmap_width`, scored on the TILE INDEX (masked to the
low 10 bits - the flip/sub-palette bits are per-tile noise) instead of a
pixel value, over every divisor PAIR of the entry count (a screen is
exactly cols*rows, never padded, unlike a bitmap which can have trailing
partial rows). Cached in the manifest as `res['screen']` exactly like
`res['atlas']`, with the same confidence score and a GUI override ('Set
screen size...', mirroring 'Set width...').

**A second, separate problem for the same file**: `map_amp.dat` never has
a tileset in its OWN archive at all - the tiles are a full tile+palette
pair in a DIFFERENT offset-table archive (`map_ncg.dat`+`map_ncl.dat`) with
no shared index space (`map_amp` has 2400 slots, `map_ncg` 1000). Detecting
that an archive is screen-only (so it can be exposed at all instead of
silently producing zero resources) needs a strict signature - a loose one
(any u16 array with varying top bits) false-positived on `map_id.dat`/
`map_evt.dat`/`obj_anm1.dat`/etc, which are event/animation/object structs
that coincidentally look u16-ish. What worked, `scan._screen_only_items`:
- a live entry counts as a screen only if it uses 2+ different sub-palette
  nibbles OR sets a flip bit (either alone let unrelated archives through -
  needed both signals available, checked with OR, but see below);
- **and** the archive's entries reuse a small set of sizes (map/screen data
  clusters into a handful of common map dimensions: `map_amp.dat` has 20
  distinct lengths across 1142 live entries, 1.75%; a real event/struct
  archive has a near-unique length per entry, `obj_anm1.dat` 154/290 =
  53%) - `distinct_lengths <= 10% of live_entries` is what actually
  separated real screens from every false positive once added; the
  per-entry signature alone was not enough.
Once found, PAIRING it to the RIGHT tileset archive is generic too: the
only thing tying `map_amp`/`map_ncg`/`map_ncl` together at all is that one
archive's stem is a prefix of the others' (`map`) - true of this naming
convention in general, not special-cased to this game. Every candidate
match is then validated by tile-fit (`_screen_fits`: every tile index used
must be `< tile_count` of the candidate) before it is ever used, so a
coincidental prefix match can only fail closed (falls back to the manual
picker below), never mis-render - confirmed on real output (a genuine top-
down room layout came out of the very first validated match tried).
Entries that don't find a validated match stay visible as a new resource
kind, `raw_screen` (a grey tile-index preview, not yet editable), with a
GUI action `pick_tileset` ('Set tileset...') that lets a human assign any
tileset this scan already knows about - same list-and-apply pattern as
'Set palette...' - which promotes it to a normal editable `raw_bg` and
re-resolves its dimensions against the now-known tile count. Result: 855
`map_amp` + 933 `map_shp` screens found (previously 0, invisible), 442
auto-linked validated, 1379 exposed for the manual picker,
`stats['raw_screen_linked']`/`['raw_screen_unlinked']` making the split
visible instead of silent.

## 2.x LZ40/LZ60 (cross-checked against two independent open-source tools, zero corpus usage found)

Checked two more third-party compression collections the user pointed at -
PeterLemon/Nintendo_DS_Compressors and Venomalia/AuroraLib.Compression -
against every codec this project has, looking for gaps. Everything in both
(LZSS/LZ10 0x10, LZ11/"LZX" 0x11, RLE 0x30, Huffman 0x24/0x28, BLZ, and the
"LZE" 0x654C-magic codec - confirmed to be byte-identical to this project's
own `lze.py`, magic `'Le'` read as a little-endian u16 0x654C) was already
in nitrokit. The one real gap: **LZ40/LZ60** (tags 0x40/0x60), an LZ11-
shaped codec neither tool's name suggested was already covered. Added as
`compression/lz40.py`, codecs `lz40`/`lz60` in the normal cascade.

Byte layout (agrees exactly between both third-party implementations, so
trusted without a real sample to test against): same flag-byte-per-8-tokens
idea as LZ10/LZ11, MSB first, but with two differences - (1) the flag byte
stored on disk is the ARITHMETIC negation of the logical byte (`(-b) & 0xFF`,
not a bitwise complement - two's-complement negation mod 256 is its own
inverse, so one function serves encode and decode), and (2) copy tokens
store the RAW distance (window 0x1000, no `-1` bias unlike LZ10/LZ11) with
the low 4 bits of the first u16 selecting the length field width (2-15
inline, or 16-271/272-65807 via one/two extra bytes) - see the module
docstring for the exact bit diagram. LZ60 is identical except for the tag
byte and an optional 32-bit length field for files over 16 MB (irrelevant
at single-resource size).

**Zero hits across the full corpus** (all 28 original ROMs + the 16 games in
`Passagem_NovosGames`) - nobody here uses it. Detection is deliberately
strict for this reason (0x40/0x60 are ordinary bytes any file could start
with): a candidate must decompress to EXACTLY the declared size AND consume
the whole input to within padding, the same bar LZ10/LZ11 already use.
Verified only via synthetic round-trip (encode -> decode -> identical, incl.
edge sizes 0/1/2/3 and uniform-random data) - flagged here, like DIFF8/16
before it, rather than silently assumed correct against real game data that
doesn't exist in this corpus. Ships ready for whatever future ROM does use
it.

## 2.x AKLZ (Touhai Densetsu Akagi DS's in-house LZSS variant) - traced via CPU emulation, not disassembly guesswork

Akagi's `bg.b`/`chr.b`/`obj.b`/`adt.b`/`bmp.b`/`mes.b` hold almost every
graphics/data asset (everything else in the ROM is 1 overlay + 2 SDATs),
each behind an in-house compression format with no public name or spec.
Manually transcribing the ARM9 routine from disassembly (as worked for
LZE and bitlz in earlier passes) produced a decoder that was subtly wrong
in exactly the two constants that matter (shift amount and copy length for
the "long" token) - two ARM instructions computing a rounding correction
that is always zero for this function's actual input range make hand
tracing genuinely misleading here. Installed `unicorn` (a CPU emulator,
`pip install unicorn` - works standalone, no ROM/BIOS needed) and ran the
REAL decompression routine (ARM9 `0x02003918`) directly against real
compressed members with a Python harness (`Uc(UC_ARCH_ARM, UC_MODE_ARM)`,
map the decompressed ARM9 image + scratch src/dst/stack buffers, set
r0=dst, r1=src, lr=a trap address, `emu_start`) - this gives a
guaranteed-correct reference with no transcription risk at all, and is
worth reaching for immediately (not just as a last resort) whenever a
hand-disassembled routine's bit-twiddling looks ambiguous, since it turns
"is my reading of this shift right" into a one-line experiment. Matched a
hand-written Python decoder against that emulated ground truth on every
real compressed member across 3 files (321/321, zero mismatches) before
shipping.

Format (`compression/aklz.py`, codec `aklz`): each archive member starts
with `u32 flagged_size` (bit 31 set = this member is aklz-compressed,
bits 0-30 redundantly repeat the codec's own size field below - checked)
or, bit 31 clear, the member is stored raw and the whole word is its byte
length. The aklz stream itself: `u32 LE decompressed size`, then flag
bytes (MSB-first, bit=1 -> back-reference) covering up to 8 tokens each;
a back-reference token byte T with `T & 0x80 == 0` is a SHORT copy -
always exactly 2 bytes, copied one at a time (so distance 1 correctly
doubles the byte just written) from a 7-bit distance (1-128); `T & 0x80`
set means a second byte U follows and it's a LONG copy, length `((T &
0x7F) >> 4) + 1` (1-8), distance `(T & 0xF) << 8 | U` (1-4096), also
copied one byte at a time (so an overlapping run repeats correctly).
Detection is strict (the redundant size-field double-match, cross-checked
in `is_aklz`) since the leading bit is otherwise unremarkable.

The archive format wrapping these members (`containers/member_table.py`,
`bg.b` etc.) has no count field either - unlike Magical Starsign's offset
archive (explicit count + count+1 absolute offsets ending in the file
length), here the table is `count` raw absolute u32 offsets with `count`
inferred from the first offset itself (`table[0] // 4`, since the table
is exactly that many bytes and the first entry starts right after it).
Confirmed on all 6 files that fit this shape (`bg.b` 290, `chr.b` 130,
`obj.b` 33, `adt.b` 4, `bmp.b` 23, `mes.b` 8 entries) - `ann.b`/`mvp.b`
(58 MB / 25 MB - almost certainly video/audio, not 2D graphics) and the
4 tiny `fd*/fs*` files do NOT fit this shape and were left alone rather
than forced.

**Not shipped**: what's actually INSIDE the decompressed members. A second
pass (static ARM9 disassembly with `capstone`, no emulation this time) went
substantially further than the paragraph above used to say - real, verified
findings, not another guess - but did not finish the job. Kept here in
detail so nobody re-walks the same ARM9 addresses from zero.

*The archive family is bigger than previously catalogued.* A 10-entry
filename table sits at ARM9 `0x205cd40` (found by searching for the `chr.b`
string and reading backwards/forwards through the surrounding data): `adt.b,
bg.b, obj.b, mes.b, fst.b, fdt.b, ann.b, mvp.b, bmp.b, chr.b` (indices 0-9).
Only `bg.b/chr.b/obj.b/adt.b/bmp.b/mes.b` were previously known - **`fst.b`
and `fdt.b` are new**:
- `fst.b` (3138 bytes): every 2-byte unit is a valid Shift-JIS code point in
  the `0x81xx` (JIS symbols/kana) range - this is a text/string table, not
  graphics, and can be deprioritized for the sprite work.
- `fdt.b` (50208 bytes): starts with a long zero run, then small u16 values
  that are all exact multiples of 8 in plausible tile-dimension range (the
  first nonzero record, at byte 55: `96, 48, 24, 8, 0, 0, 0, 0, ...`). This
  is an **unconfirmed but concrete lead** for where `chr.b`'s missing width/
  height might live (see below) - not cross-checked against real `chr.b`
  entries yet.
- `ann.b`/`mvp.b` (58/25 MB): confirmed still not 2D graphics (huge, and
  their content doesn't fit any container shape this project knows) -
  no new information, still presumed video/audio.

*`chr.b`'s own per-entry header, refined and one hypothesis disproven.*
Every entry (130 in `chr.b`, checked via `containers.member_table`) opens
with 5 LE `u32`s: `payload_size, 4 (constant across every entry checked),
count, f3, f4`, immediately followed by `count`-ish small records and then
a long high-entropy tail (presumably pixel/index data). `count` takes only
a handful of values across the corpus (7, 12, 16, 25, 72, 128 - looks like
a real per-sprite item count, e.g. OAM-equivalent pieces, not a type tag).
**Ruled out**: `f3`/`f4` as byte offsets INTO this same payload (the
natural first guess, matching how `chr.b`'s own doc used to describe them)
- on several real entries `f3` is smaller than the 20-byte header itself,
which no valid internal offset could ever be, so whatever `f3`/`f4` encode,
it isn't "where in this buffer to find X".

*ARM9 addresses traced for the loading pipeline (not the payload parser
yet).* `aklz` decompression itself lives at `0x02003918` (already known).
Found 4 near-identical wrapper functions (`0x2003608, 0x20036dc, 0x20037c8,
0x2003894`) that each: read a member's `flagged_size` word, mask off the
top nibble for the real size, allocate a buffer, call `aklz` if the
compressed bit is set (else a plain memcpy at `0x204c094`), and stash the
result as a `(size, ptr)` pair into one of 4 consecutive global slots
(`0x2060x59c`-`0x20605b8`). A separate, generic function at `0x2001574`
takes a file-table INDEX (not a hardcoded name) and stores it into a
"current selection" global, referencing the same 10-entry string table -
meaning `chr.b` (index 9) is very likely loaded through this generic,
index-parameterized path rather than one of the 4 direct wrappers found
(those 4 are plausibly `bg.b/obj.b/adt.b/mes.b`, the earliest, most simply
loaded files - not confirmed by name, since none of the 4 directly embeds
one of the filename-string addresses).

**What's actually needed to finish this**: everything above was recovered
by reading code without ever running it. The remaining piece - what
`count`/`f3`/`f4` and the following small records actually MEAN in terms of
tile index / palette / x,y placement - lives in whatever function
eventually dereferences the decompressed `chr.b` buffer to draw a sprite,
several indirect call layers past everything traced here. Chasing that by
hand (reading disassembly, guessing which callee does what) does not have
a natural stopping point in a reasonable amount of time. The right tool is
the same one that solved the AKLZ compression itself: `unicorn`, but used
differently - not to re-run one already-identified function, but to run
the game (or a rendering routine) for real with a memory **read hook**
installed over one live `chr.b` buffer's address range, so every access
the game's own code makes into that buffer is logged with its address and
size. That turns "guess what `f3` means" into "watch the game read it and
see what it's used for" - the same category of shortcut that made AKLZ's
two ambiguous constants a one-line experiment instead of a transcription
argument. This is a materially bigger undertaking than the compression
trace was (one self-contained function vs an asset pipeline spread across
indirect dispatch and, eventually, real rendering code) and needs its own
dedicated pass, not a follow-up guess.

## 2.x Loose banner-shaped files anywhere in the ROM (generic, benefits 5 games at once)

`formats.banner.Banner` already reads any of the 4 known versions (1/2/3/
0x103 DSi-animated) of the cartridge icon, but `scan()` only ever looked
for it at the ONE fixed header-pointed slot. Several games ship EXTRA
banner-shaped files as ordinary FAT files - Emily the Strange has 3
(`Emily.TWL.bnr`, `Emily_ce.TWL.bnr`, `Emily_dtp.TWL.bnr` - regional/
eShop-listing icon variants), Kaijuu Busters has 3 (`data/banner/
ipl_00.bnr`, `data/banner/banner_download/ipl_01.bnr`, plus a nested-ROM
icon already found via the existing `.srl` path), Avalon Code, Kaiji and
Ivy the Kiwi one each - all previously invisible to the tool (no kind
maps `.bnr`/arbitrary extensions to a banner check). Added a generic
check in `_explore`: any file whose first 2 bytes are a valid banner
version AND whose length exactly equals that version's known size AND
whose stored CRC16/MODBUS matches the real one (recomputed over the same
0x20-0x840 span the cartridge banner uses) is exposed as an editable
`banner` resource - the version+size match alone isn't enough (1/2/3 are
too common as an arbitrary leading `u16`), the CRC match is what makes
this safe to apply to every loose file in the ROM without a name/
extension allowlist. Verified: renders a real, distinguishable icon in
every case checked by eye (Avalon Code's `BANNER/mg04.bnr` is a book/
scroll icon, Kaijuu Busters' `ipl_00.bnr` a monster face) and a full
28+16-ROM survey found zero false positives and zero new scan errors.

## 2.x Teenage Zombies `Levels/*.ntfi`/`.ntft` - a real screen map confirmed, but the pixel reconstruction still isn't right

`Levels/<name>-tiled.ntfi` decodes bit-for-bit as a real NSCR-style screen
(confirmed, not guessed): every one of its ~218k u16 entries decomposes
into the standard tile(10 bits)/hflip/vflip/sub-palette(4 bits) layout
with no exceptions - sub-palette only ever reads 12-14, comfortably inside
the 0-15 range, and the tile field never exceeds 1023, comfortably inside
the sibling `-tiled.ntft`'s much larger tile pool (27232 tiles at 4bpp).
That statistical cleanliness is what made this worth pursuing - and also
what made the failure quiet: composing it (bg-style, 4bpp, tile-order
pixel pool, palette read straight from the sibling `.ntfp`) produces an
image with no error, no crash, and a plausible-looking width guess, but
zoomed in it is unstructured static, not tile art - confirmed at both
4bpp and a same-effort 8bpp attempt. Reverted rather than shipped (this
project's rule: a technically-valid-looking guess that produces the wrong
picture is worse than an honest "not paired"). Two things this data rules
out as an explanation on their own: the bit-field layout (verified
correct above) and a bad canvas-width guess (tried several candidate
widths from the true divisors of the entry count; all equally noisy).
What's left unexplained: each level ships 3 more sibling files this pass
did not touch - `.til` (a small binary table, likely per-chunk metadata),
`.idx`, and per-level `.chapN` text files with numeric fields that look
like they could be room/segment dimensions - a chunked/roomed level
layout (rather than one flat scrolling screen) would explain both the
noise (each on-screen row currently splices together unrelated rooms) and
why no single global width scores meaningfully better than any other.
Needs the `.til`/`.idx` format decoded (or ARM9 tracing of the level
loader) before another attempt, not another guessed width.

## 2.x NitroPaint (github.com/Garhoogin/NitroPaint) - codecs and formats cross-checked, one shipped, two documented for later

Went through NitroPaint's own format/compression modules (a permissively-
licensed, actively maintained NDS graphics tool - a legitimate reference
alongside GBATEK for public Nitro format details) looking for codecs and
formats this project didn't have yet.

**Shipped**: the SDK's own DIFF8/DIFF16 delta filter (`compression/
others.py`, codecs `diff8`/`diff16`, wired into the normal `unwrap`/`rewrap`
cascade so any ROM using it is now handled with no per-game work). Tag
0x80/0x81 + u24 size, then each byte/halfword is stored as the difference
from the previous one (running sum decodes it) - NOT real compression,
output is exactly the input size rounded up to a multiple of 4. Cross-
checked against `CxFilterDiff8`/`CxFilterDiff16`/`CxUnfilterDiff8`/
`CxUnfilterDiff16` in NitroPaint's `compression.c`. Zero hits across this
project's 28-ROM corpus (nobody here uses it), so nothing to validate it
against beyond synthetic round-trip - flagged here rather than silently
assumed correct.

**Investigated, NOT shipped** (both would need real new work, not a quick
port - listed so nobody re-investigates from zero):
- **NMCR/NMAR (multi-cell)** - NitroPaint's `NitroMultiCell.c`/`.h` gives
  the exact structure this project had only guessed at before: block
  `MCBK`, `u16 nMultiCell` then `u32 multiCellsOffset, u32 hierarchyOffset`
  (both relative to MCBK's own start); each multi-cell is
  `u16 nNodes, u16 nCellAnim` + an offset into the hierarchy area; each
  hierarchy node is `u16 sequenceNumber, s16 x, s16 y, u16 nodeAttr` (8
  bytes). The blocker: `sequenceNumber` indexes a NANR **animation
  sequence**, not an NCER cell directly (confirmed in NitroPaint's
  `nmcrviewer.c`: `AnmRenderSequenceFrame(..., ncer, ..., seqId, frame,
  x, y, ...)`) - correct rendering needs a NANR frame-sequence parser this
  project does not have (NANR is currently only carved/copied whole for
  round-trip, never actually read). Guessing `sequenceNumber` as a direct
  cell index would be exactly the kind of blind guess this project avoids;
  parsing NANR properly is the real prerequisite, and only 2 files in the
  current 28-ROM corpus (Kimi no Yuusha) would benefit, so it's left as
  documented future work with the format now fully specified instead of
  unknown.
- **NCER rotate/scale (affine) OAMs** - a real, silent rendering gap in
  every game already supported: `g2d.NCER.glyph`-equivalent OAM parsing
  reads the `rotateScale` bit and a 5-bit `matrix` index
  (`attr0 bit8, attr1 bits9-13`, confirmed against NitroPaint's
  `CellReadObj` in `NitroCell.c`) but this project's current compose code
  only ever reads `hflip`/`vflip` and zeroes them when `rotateScale` is
  set - it never applies the actual affine transform, so a rotated/scaled
  OAM currently renders as if it were a plain, unrotated sprite instead of
  being skipped or warned about. The 4 affine parameters (A/B/C/D,
  fixed-point) that `matrix` selects live in a per-NCER table this pass did
  not finish tracing the exact offset of (NitroPaint reads it through an
  abstraction that did not resolve to a fixed byte offset in the time
  available). Fixing this needs (a) locating that table, (b) an affine
  pixel-resampling composer (real image warping, not just an index
  remap) - a bigger addition than anything else in this section. Left
  documented rather than half-fixed; look for OAMs with `rotateScale` set
  before trusting a cell sheet's rotated poses on any game.

## 2.x Not Nitro / not solvable generically (so nobody retries blindly)

- Chunsoft **AT5P** (999 `.b3d`, 3D only), Medarot **NTEX** `.tex/.pal`,
  Brownie Brown `sar ` (Magical Starsign `aikyo/Archive.sar.dat`, not
  examined), Square Enix/Jupiter **`pack`** (TWEWY 2D): proprietary
  compression/encoding - no Nitro headers are visible even after LZ/RLE
  carving.
- **Knights in the Nightmare (Sting) `DATA.SFS`** - a single 86 MB
  monolithic container holding effectively every graphics/data asset in
  the game (everything else in the ROM is just overlays + 2 SDATs). The
  first 24 bytes look like a tiny header (`0, 2, 12, 20, 23` as u32s) but
  every word past that is high-entropy - this is NOT a flat offset table
  like Magical Starsign's; the "counts" are far too small to index 86 MB
  of content, so the real structure is nested and/or the payload itself is
  compressed with an algorithm this project hasn't seen elsewhere in the
  corpus. Traced the ARM9 string `"DATA.SFS"` to its one cross-reference
  (file offset `0x1951C`, RAM `0x201951C`) and found the nearest PC-relative
  `ldr` at RAM `0x20194DC`, which is almost certainly inside Sting's own
  SFS-open routine - **not disassembled further this pass**. Solving this
  properly needs the same treatment as LZE (section above): trace that
  routine's actual instructions to get the real header/table layout,
  rather than guess from the compressed-looking bytes. Left as a single
  monolithic blob (only the banner is exposed) until that tracing is done.
- **Summon Night: Twin Age `dat/*.pac`** (`battle.pac`, `event.pac`,
  `script.pac`, `system.pac`, 1.7-16 MB each) - the outer 16-byte header
  is `u16 count, u16 1, u32 4, u32 a, u32 b` followed by `count` **chained**
  `(offset u32, size u32)` pairs (offsets relative to the END of this
  table, `entry[i+1].offset == entry[i].offset + entry[i].size` - same
  "next starts where the last ended" idea as the Magical Starsign
  offset-table archive, just paired instead of `count+1` absolute
  offsets). Confirmed on all 4 files. Unused trailing slots repeat the
  final real offset with `size=0` (same empty-slot convention as Magical
  Starsign) - except the very last slot in `battle.pac`, which holds an
  unrelated small value pair, likely a footer/checksum rather than a real
  entry; don't treat the last slot as data without checking it fits the
  chain. **Where this breaks down**: the chain only accounts for a small
  head of each file (`battle.pac`: real entries end at byte 257,748 out of
  4,117,952 - under 7% of the file) and the individual entries are
  themselves either another nested table (entry 0 of `battle.pac` looks
  like 4 more (offset,size)-shaped u32s) or high-entropy blobs with no
  recognizable header (entry 6, the single 140,180-byte entry, starts
  `f7 ad 5e 7a ...` - not ASCII, not a Nitro magic, not printable-looking
  at all, so probably compressed/packed with something this project
  hasn't identified). Same conclusion as Knights in the Nightmare: the
  outer container is now understood, but the actual pixel data is behind
  one more layer that needs ARM9 tracing (find where `.pac` gets opened
  and where each entry's bytes get consumed) rather than guessing a
  compression algorithm from entropy alone. `dat/icon.char`+`dat/icon.plt`
  (a tiny, separate loose raw char+palette pair, same idea as `.imb`/`.plb`
  from Luminous Arc 3) **is** shipped - added `.char`/`.plt` to
  `raw.RAW_EXT`, no new pairing code needed since `_pair_raw` already
  groups loose `RAW_CHAR`/`RAW_PAL` by shared stem.
- **Last Window (Cing) `.cbp`** (calendar/map screens inside `.pack`,
  79 files, all `data/*.pack`) - now fully shipped as `bitmaps.CBP1`
  (kind `cbp`), unlike `.bra`/`.iba` below. Layout: `'CBP1'` + `u16 pal_n,
  u8 bpp, u8 0, u16 w, u16 h, u16 bw, u16 bh` (20 bytes) + `pal_n` BGR555
  colours **immediately** after the header + a `(w/bw)*(h/bh)`-entry table
  of `u16` ABSOLUTE byte offsets (from file start, not from the table) into
  a shared tile pool, one per `bw x bh` block, each block stored as
  `(bh/8 x bw/8)` 8x8 tiles in row-major tile order (NCGR tile-mode
  convention) - all 79 files use `bpp=8, bw=bh=32`. **The one real trap**:
  a long run of black/zero palette entries at the front of most files
  looks exactly like padding before a "real" palette further in - it is
  not; treating it as padding (an early pass here did) shifts the actual
  palette read by however many black entries happen to lead, and every
  dumped image comes out with a plausible-looking but entirely wrong
  colour cast (this is easy to miss because SOME wrong offset still reads
  valid-looking BGR555 bytes - only comparing against the game's own
  screenshots caught it). Composed with the existing generic tile-reuse
  machinery (`render.compose.layout_tiles`, a new tiny sibling of
  `layout_bg` with no 10-bit tile-index cap - CBP1 pools routinely exceed
  the 1024 tiles an NSCR entry's field could address) rather than a
  BPG1-style flat permutation, because blocks legitimately repeat (a
  shared "blank" tile). Table entries below the table's own end (0, 1, or
  even a value landing inside the table itself in bigger files - not one
  fixed sentinel, just "smaller than where real blocks can start") mean
  "nothing here"; the smallest real value is 0, which is also a genuine
  file position (the header) - writing there would corrupt the magic, so
  those cells are marked unmapped (`-1`, the same convention `layout_atlas`
  already uses for "no backing pixel") rather than ever resolved to a real
  tile, which doubles as the fix for both problems (never renders the
  header as fake pixels, never writes to it either). Some files have a few
  bytes past the last referenced tile that no block ever points at;
  `rebuild()` only touches the exact span it read pixels from, so that
  tail survives untouched. Verified on all 79 files: parses, round-trips
  pixel-identical with no edits, and a real edit survives a full ROM
  rebuild.
- **Last Window `.bra`/`.iba`** (character-portrait animation and a second
  image format, respectively) - investigated, **not shipped**. Both are
  headerless (no magic) like `.cbp` was, but neither is a simple bitmap:
  `.bra` (`Amazed.bra` etc., one per named emotion) opens with small
  integers (frame/segment counts, not width/height in any combination that
  produces a sane image) followed by what looks like a smoothly-graded
  BGR555 palette starting around byte 0x1C - consistent with a genuine
  multi-frame animation (bone motion or palette-cycling portrait), not a
  static picture, so it needs real structural work (frame table format,
  what the leading integers count) that a byte-equation guess would not
  safely produce. `.iba` looked at first like another CBP1 (same idea:
  bpp code byte, w, h, then two u32 fields) but the two length-like u32
  fields don't add up the way CBP1's table+pool does for anything but the
  one sample checked by hand - e.g. `BG_CP_L.iba` (256x256, bpp code 4):
  header(20) + a 256-colour palette(512) accounts for byte 532, leaving a
  360-byte gap and a 49152-byte tail that don't factor into blocks the way
  CBP1's did (49152 isn't `(w/bw)*(h/bh)*bw*bh` for any block size that
  also makes the 360-byte gap a whole number of table entries). Left
  undecoded rather than shipping a table-offset scheme that only matches
  one sample; both are real, remaining Last Window gaps.
- Drawn to Life: most NCER/NCBR have **no palette file at all** (colours
  come from code / the player's drawing) - dump is greyscale by design.
- **NMCR/NMAR** (multi-cell / multi-cell animation, magics RCMN/RAMN -
  groups several NCER cells together with their own placement/transform
  data, e.g. a multi-part enemy formation drawn as one animated unit):
  found in this survey (Kimi no Yuusha, 1 file each). Genuinely a real,
  public Nitro sub-format, but rare enough here (2 files total across 28
  ROMs) that it was not worth the implementation cost yet - currently
  silently skipped by pairing (the underlying NCER/NCGR/NCLR it references
  are still dumped normally as ordinary `cell` resources, just not
  assembled into the higher-level multi-cell composite).
- **Medarot DS's `NTEX`** (a Nitro-style magic, `.tex`/`.pal` files, 1014
  files - by far the largest genuinely-Nitro-shaped gap by file count, but
  confined to a single game): a proprietary Nitro-ADJACENT texture format,
  not TEX0. Partially characterised: `.pal` files are a clean, tiny format
  (32-byte header + 16 BGR555 colours, matches exactly for every `.pal`
  sampled); `.tex` files have a larger (~32+ byte) header whose fields
  weren't fully pinned down, followed by data that decodes as smooth
  BGR555 gradients when read as plain 16-bit direct color - consistent
  with an uncompressed direct-colour texture format similar in spirit to
  TEX0's format-7 textures, but with Medarot's own header layout. Stopped
  short of shipping a decoder without confirming the width/height/format
  fields against multiple samples (the same standard this project holds
  every other format to) - a good candidate for a future, single-game-
  focused pass if that title's textures matter enough to justify it.

## 5b.x Validation numbers to expect from a correct generic tool

- Zero-edit pixel round-trip (compose -> decompose == original pixels) over
  100% of 2D resources: 19,535 resources in 10 games, 0 mismatches.
- End-to-end edit test (paint, insert, rebuild ROM, re-scan, compare): edit
  expectations must be computed AFTER tile sharing - comparing to the painted
  PNG reports false failures whenever a tile/OAM is reused (differences are
  exact multiples of 64 px). Compare against the session's own re-render of
  the queued edit; that isolates container/compression/rebuild bugs.
- A grown container shifts every later locator: match resources between the
  original and rebuilt ROM by deterministic scan order, not by offset.

## 5.x Tooling note

Python heredocs driven from a POSIX shell on Windows turned `b'\0'` in a
replacement string into a literal NUL byte inside the .py file
("source code string cannot contain null bytes"). Write patch scripts to a
file with an editor tool, or use `bytes(n)` instead of `b'\0' * n`.

## 2.x Overlapping OAMs: why a correctly decoded sprite still exports "chopped"

A Nitro cell is drawn from several hardware OBJ pieces (OAMs) and they are
allowed to **overlap**. Composing them onto one flat PNG is lossy by
construction: a pixel can hold one value, so whatever the top piece covers
is simply absent from the exported file - and is destroyed on reinsert if
the user paints over that area. This is not a decoding bug, and no amount
of pairing/mapping work fixes it; it is the flat sheet itself.

How common it actually is (measured over the whole corpus, counting cells
where at least two OAMs of the same cell intersect):
Radiant Historia 327/395 (83%), Valkyrie Profile 355/664 (53%),
Kimi no Yuusha 71/1513 (5%), Deltora Quest 24/654 (4%),
Lufia 17/654 (3%). So for two of the games the majority of sprites cannot
be edited losslessly as a flat sheet - which matches the user's
"picotado" reports much better than any pairing theory did.

Fix shipped: `render.compose.layout_cells(isolated=True)` lays out ONE BOX
PER OAM instead of one per cell (same deterministic packing, so insert
rebuilds the identical sheet), plus `compose.oams_overlap(ncer)` to detect
the condition cheaply. The mode is opt-in per resource, stored in
`res['cells']['isolated']` so it survives into `manifest.json`, and the
sheet width/gap are stored beside it. Same idea as the OAM tab of the
Lina no Atelier tool, but wired through this project's own layout/compose
so the round-trip guarantees hold.

Proof, not vibes:
- compose -> decompose -> identical pixels: 800/800 in BOTH modes across
  Lufia, Valkyrie Profile and Kimi no Yuusha (400 cells x 2 modes each);
- how much it actually recovers, counted as distinct source pixels the
  sheet can address: Valkyrie Profile 8,862,156 -> 10,506,688 (+18.6%),
  Lufia and Kimi no Yuusha +0.2% (they have few overlapping cells);
- full dump -> paint -> insert -> rebuild -> re-read -> compare through the
  real fail-closed workflow, in OAM mode: Lufia 2/2, Radiant Historia 2/2,
  Valkyrie Profile 1/1.

Two honest limits the panel now states out loud:
- when the same source pixel appears twice in one sheet (pieces reusing
  the same tiles), editing one copy and not the other is a contradiction
  and the insert step refuses the batch by design - the panel counts those
  pixels up front;
- in 4bpp a pixel must stay inside its own OAM's 16-colour row (writing a
  global index 1 into a piece whose sub-palette is 2 is not representable).
  Worth remembering when writing tests: an early version of the OAM e2e
  test "failed" for exactly this reason and the tool was right.

## 2.x Implicit-count offset archives can hold HEADERLESS graphics too

`containers.member_table` (the count-less offset table found in Akagi)
opened the container, looked for Nitro blocks inside each member and, when
it found none, dropped everything silently. Heroes of Mana keeps its whole
2D set exactly that way: `data/monstershap.dat` (220 members),
`faceshap.dat` (227), `titleload.dat` (92), `eventchar.dat` (199),
`story.dat` (27), `ending.dat` (19) are implicit-count tables whose members
are raw, headerless graphics - the same situation as Magical Starsign's
explicit-count `.dat` archives, which this project already handled.

The raw-member heuristics (suffix pairing, palette-sized neighbour
pairing, screen-only detection) were container-specific by accident, not by
design: they were written inline in `_offset_archive_items` and hardcoded
`offset_archive.parse` + the `oarc` locator. Split out as
`_archive_raw_items(payload, loc, path, entries, tag)` and now used for
both shapes (`oarc` and `mtab`); `_raw_member` takes the entry list and
locator tag. Heroes of Mana went from **23 to 316 resources** with no
new per-game code. Both locators already supported write-back, so what
appears is editable, not preview-only.

## 2.x A palette whose used rows are blank cannot be the right palette

In 4bpp each tile/OAM picks a 16-colour row of the NCLR. A row holding a
single colour is either unwritten or past the end of the palette, and a
real drawing never uses one: the export comes out as a flat silhouette
(the 'everything is black' report on Lufia, whose 47 MB `mcd.dat` carve
container makes the positional "nearest NCLR before" rule guess wrong for
a handful of cells). `scan._repair_palettes` replaces such a palette with
the nearest one in the same container whose used rows have real colours.

Deliberately conservative: it only fires when EVERY row the map/cell
actually references is blank, so a legitimately dark layer (distinct
colours, all dark) and a genuinely empty resource are never touched. It
fixed the clear-cut Lufia cases; 8 of 654 cells there keep a
plausible-but-wrong palette (the rows do have colours, just not the art's)
- distinguishing those needs rendering and judging the image, which is
exactly the kind of guess this project refuses to automate, so they are
left to the GUI's manual **Paleta…** button.

## 2.x Looked for graphics inside overlays/ARM9 - there are none (in this corpus)

Worth recording because it looks like an obvious gap: `scan()` skipped
`overlay9/`/`overlay7/` outright, and never touched the ARM9/ARM7 images
at all, so "the tool ignores code" was a fair suspicion. Now
`scan(..., include_code=True)` covers both (ARM binaries via a new
read-only `['arm', 9]` locator; `Session.put` refuses them with a clear
message, since the code region is not a FAT file and would need its own
same-size patch path like the banner).

Result across the corpus: **zero graphics**. The magic strings a raw byte
search finds (`RTFN` in Lufia's and Kimi no Yuusha's ARM9, and in a Heroes
of Mana overlay) are the loader's own comparison constants - the bytes read
`RTFNFNIF...`, i.e. "NFTR" followed by "FINF", sitting in code, not a font
file: `nitro.header()` rejects every one of them. Overlays are also
compressed with the SDK's backwards/BLZ scheme in the games that flag them
compressed (Twin Age 20/20, Lufia 20/20), which this project has no decoder
for - so if some other game does hide graphics there, that codec is the
prerequisite. Default is off (costs time, finds nothing here), exposed as a
checkbox so a new game can be checked in one click.

## 2.x Coverage report: turn "the scan ignored my folder" into a list

The scan walks every file, but a file with no recognisable graphics just
disappears from the result, which is indistinguishable from being skipped.
`pipeline/coverage.py` + the GUI's Cobertura tab list every file no
resource points into, with size and a reason (audio/video, text, code, too
small, or "no graphics recognised - proprietary container?"), biggest
first, exportable to a text file. Heroes of Mana: 7 of 156 files hold the
2D set, and the honest remainder is `data/skana.dat` (74 MB, magic "ANAG"
plus a small offset list, an SDAT inside - a mega-archive still to be
decoded). Valkyrie Profile: 10 of 30 files hold graphics, 5 files / 13 MB
are undeciphered. That is the difference between "the tool is broken" and
"these exact N megabytes are formats nobody has decoded yet".

## 2.x NTRP (Ragnarok DS): an (offset,size) table over complete Nitro files, two families

`2d_u/**/*.ntrp` (311 files, always LZ10): `'NTRP' + u8 version[4] (04 04
04 00 in every sample) + u32 0`, then `(u32 offset, u32 size)` pairs until
an entry is 0/out of range/overlapping. Reverse-engineered by dumping the
u32 header words and recognising the pattern directly (no emulation
needed - a static offset table, same shape as `offset_archive`/
`member_table` already in the project).

Members are COMPLETE graphics files, either:
- standard Nitro, reversed magic (RGCN/RLCN/RECN/RNAN) - 306 files
  (icon/object bundles), already found by the generic carver before this
  change, so nothing regressed for them;
- the Kaijuu-Busters-style "lite" Nitro (NCG\0/NCL\0/NSC\0, forward magic,
  no reversed form for the carver's regex to match) - 5 files, the
  full-screen backgrounds (credits x4, title x1). `formats.raw.nitro_lite`
  already reads this shape; the carver never saw it because NTRP is its
  own container, opened at `_explore` depth 0 by its own magic, not by
  falling through to the generic carve path.

The container groups members BY KIND (every screen, then every tileset,
then every palette - not interleaved per logical picture), confirmed
across every sample. `_explore`'s NTRP branch renumbers each member's
synthetic path PER KIND after building it (`creditbg00/0000.screen`,
`.../0000.char`, `.../0000.pal`, ...) so the i-th screen shares a stem
with the i-th tileset and palette - the normal raw-pairing-by-stem logic
(`_pair_raw`) then matches them with no NTRP-specific pairing code at
all. Standard-Nitro members go through the ordinary `_pair_ordered` path
in parallel (`mode == 'ntrp'` splits items by G2D_KINDS vs `RAW_` prefix
in `scan()`).

Result: 23 full 256x192 8bpp screens recovered (credits x4x4frames + a
title screen x8), confirmed BY EYE (sepia character portraits,
gravestones, a title composition - not just "renders without error").
Write path (`containers/ntrp.pack`): same-size replacements (the only
kind this project ever produces) patch the member's bytes in place,
byte-identical elsewhere. A real size change falls back to a full
relayout from the first member's original start offset. Round-trip:
311/311 byte-identical on zero-edit AND on same-size single-member
replacement.

## 2.x Windows BMP found for real inside a ROM (Viewtiful Joe: Double Trouble)

`obj/*.bmp` (49 files): genuine BM-signed Windows bitmaps (BITMAPV3,
16bpp RGB565 bitfields in the sample checked, `obj/cock_00a.bmp` 256x64),
openable directly by Pillow with zero massaging - almost certainly
leftover from the authoring pipeline, never converted to a Nitro format
for the final ROM. Nothing to reverse: this is the public format. Added
`formats.bitmaps.WinBMP` (reader/writer) rather than a new module, since
it's the same "self-describing paletted/direct-colour bitmap" family as
BPG1/EBP already there.

Handles 4/8bpp (indexed - own BGRA palette table, converted from BMP's
byte order) and 16/24/32bpp (direct colour - exposed as an RGB PNG with
no palette at all, `res['kind'] == 'bitmap'`, `res['format'] == 'WinBMP'`,
`info['rgb'] = True`; `pipeline.resource.to_png`/`from_png` branch on
that flag before the indexed-only `layout()` path). Two round-trip traps
found and fixed by testing against the real files rather than assuming
the inverse formula was exact:
- narrowing 8-bit RGB back to RGB565 must ROUND, not truncate - truncation
  is a strictly one-directional lossy map that never round-trips even on
  values that came FROM a 565 source;
- 32bpp's alpha channel is not part of the edited PNG (the tool exports
  RGB, not RGBA, since alpha here is per-pixel game data rather than a
  transparency mask meant for editing) - `rebuild()` keeps each pixel's
  ORIGINAL alpha byte instead of synthesising 255, or a byte-exact
  zero-edit round-trip would silently rewrite it.
Verified: 49/49 files byte-identical on zero-edit round-trip.

## 2.x Photographic image formats: a general detector, censused across the corpus

Added `formats/photo.py`: signature detection for PUBLIC photo formats
(JPEG/PNG/GIF, decoded via Pillow, exposed as a preview-only `photo`
resource - re-encoding JPEG is lossy and PNG/GIF change size, so this is
NOT the same pixel-only edit contract as everything else here) plus a
small table of PROPRIETARY photo-codec signatures seen in this corpus
(currently one entry: Myst's `.pu`, magic `pu\x01\x01` at offset 4),
counted into `stats['photo_codec: <label>']` so a scan/coverage report
can NAME the codec instead of the file just vanishing into "no graphics
recognised" like before.

Ran a full-corpus census (49 ROMs, every file checked both raw and after
the normal decompression cascade) asking "does any OTHER game also use a
photographic format": only Myst does (1493 `.pu` files - confirmed
earlier by low zero-byte fraction, ~2%, consistent with compressed
photographic data rather than paletted tile/palette data). Kaijuu Busters
and Viewtiful Joe each have exactly the WinBMP files already covered
above; nothing else in the 49-ROM corpus trips either check. This answers
the standing question "could another DS game use a photo codec" with data
rather than a guess, and the detector stays live for any ROM added later -
no per-game wiring needed for a NEW game using JPEG/PNG/GIF/BMP.

## 2.x Scan performance on a huge ROM (Ragnarok DS, 21512 files): two real bottlenecks found and fixed

Ragnarok DS's `analyze()` took 1225s (profiled) / ~20 min wall clock - bad
enough to be a real usability problem for the new GUI's "Buscar/Analisar"
button, not a paper cut. `cProfile` pointed at two functions, both genuine
bugs (redundant work per call, not proportional cost):

1. `_sibling_names` (finds a same-stem sibling name-tree file, e.g.
   Radiant Historia's Data.bin+Data.ndx): for EVERY file explored in
   'carve' mode (1905 calls here), it re-walked the ROM's ENTIRE file
   list (21512 entries) constructing a fresh PurePosixPath for each one
   to compare parent+stem. 900 of the 1225s. Fixed by building a
   `(dir, stem) -> [paths]` index ONCE per ROM (`_sibling_index`, cached
   by `id(rom)`) instead of rescanning per caller.
2. `_repair_fits`'s `char_info` (parses a candidate NCGR to check whether
   a map fits it): legitimately cached by `json.dumps(loc)`, so THIS part
   was not a caching bug - the ~100s there is the real cost of parsing
   the ~4283 distinct candidate tilesets a 21k-file, 173-background ROM's
   repair pass actually needs to look at. Left as is.

Net: 1225s -> 94s wall-clock (no profiler), same resource count (4768)
before and after - confirms the fix only removed redundant work, changed
no logic. Separately, the new GUI's ROM tree was ALSO summing every
resource's owning file over the full file list once per folder shown -
fine on small ROMs, 16s here. Fixed the same way: a `path -> graphic
count` index built once in `RomTree.load` (`gfx_per_dir`), turning
per-folder lookup into a dict read. 16s -> 0.18s for the same 21k-file,
~4600-resource tree.

## 2.x Level-5 Layton `.arc`/`.arj`: a codec-typed envelope, and a guessed grid that was wrong

Professor Layton (Curious Village, A5FE) stores all 2D art in `.arc`/`.arj`
files that are NOT NARC. Each file is `u32 codec` + a DS BIOS stream; the
u32 is not a constant tag but selects the codec, checked against the
stream's own first byte: 1 = RLE (0x30), 2 = LZ10 (0x10), 3 = Huffman-4
(0x24), 4 = Huffman-8 (0x28). Census over 1877 files: 1770 LZ10, 58 RLE,
47 Huffman-8, 2 Huffman-4. Treating the u32 as a fixed magic `2` silently
dropped the other 107 files.

Decompressed background: `u32 n_colors` + BGR555, `u32 n_tiles` + 8bpp
tiles, then `u16 w_tiles, u16 h_tiles` + NSCR-style entries. Animation:
`u16 n_images, u16 depth(3=4bpp,4=8bpp)` [`.arj`: `u32 n_colors`], per image
`u16 w,h,n_parts,pad`, per part [`.arj`: `u16 glbX,glbY`] `u16 x,y,log2(w/8),
log2(h/8)` + linear pixels; palette; then animation scripts (kept verbatim).
Source: Tinke `Plugins/LAYTON` (Bg.cs, Ani.cs), confirmed by rendering.

Lesson: the first pass, done by hand before reading Tinke, found 1540 map
bytes and "confirmed" a 35x22 grid (770 entries) because two renders looked
plausible. The real layout is a 4-byte `(32, 24)` header + 768 entries;
the two dimension words were being read as tile entries, which produced a
few garbage tiles at the edges that looked like content noise. A render
that "looks mostly right" is not validation - a grid guess must be checked
against a size field or an exact byte-count equation (here
`map_off + 4 + 2*w*h == len(payload)`, now enforced as the detector).

Insert: pixels edited in place (same tile/part counts), payload re-wrapped
with the file's OWN codec (`compression.rewrap`). Validated by E2E (19/19
edits reproduced, 200/200 controls unchanged) and by editing one pixel in
each of the 1874 recognised files across all 4 codecs and both extensions:
the edit reads back exactly and no other byte of the payload changes.
Detector has zero false positives over 2079 `.arc/.arj` files of four
other games. Our own LZ10 encoder pads to 4 bytes, so the "stream must
consume the whole file" check allows up to 3 trailing zero bytes -
otherwise the tool would refuse to re-open its own output.

## 2.x Porting from other tools: drive it with a census, not with the plugin list

Sweeping Tinke/Kuriimu2/dsdecmp/BlocksDS for "everything portable" would
mean ~30 codecs and dozens of per-game formats. Instead: census every file
the scanner still leaves unidentified across the whole ROM library (62
ROMs, ~89k files, 1.5 GB), fingerprint them AFTER the tool's own
decompression, and test each kit's signatures against that. Only formats
that recur across games or unlock real files were ported:
- WFC `dwc/utility.bin` = mini NitroFS (u32 fnt_off, fnt_size, fat_off,
  fat_size + ROM-style FNT/FAT) in 16 games; zero-edit repack is byte-exact
  in all 16, and a FAT rewrite lets members grow (fixed a font that never fit).
- SDK overlay BLZ: validate a decoder against a free oracle - the overlay
  table's RAM size. 1192/1192 exact.
- "u32 n + n x (offset, size)" tables and "file = back-to-back LZ streams"
  chains (a plain unwrap silently decodes only the FIRST stream).
- Kuriimu2's Yaz0/Yay0/MIO0/Level-5-header/zlib signatures matched nothing
  real in this library: not ported.
Also: most "unidentified" bytes were not images at all (3D animation
BCA0/BTA0/BMA0/BTP0, NANR, textureless BMD0, text, audio) - classifying by
content makes the coverage numbers mean "real gap".
Result: +12,499 resources over 26 games; E2E regression 16/16.

## 2.x BLZ encoder: derive the byte layout from the decoder, don't guess it

dsdecmp only ships a BLZ *decoder*. To write one back, reverse-engineer the
encoder's byte layout directly from the decoder's pointer arithmetic
(`pak`/`out` moving backward) instead of assuming "reverse the whole
buffer" - that naive approach corrupts 2-byte match tokens (their internal
low/high byte order must NOT flip). Worked example, done by hand-tracing
a 3-byte and a 10-byte literal-only decode: the correct rule is "process
data in reverse (standard LZ77 on the reversed buffer), group units into
8-unit flag groups in that processing order, then write the FILE bytes as
the REVERSE of the full group sequence (flag-byte-then-units, per group),
but reverse only the ORDER of units within a group, not the bytes inside
a multi-byte token, and put the flag byte at the END of its group's file
span instead of the start." Confirmed by predicting exact file byte
offsets for a synthetic 10-byte case before writing any code, then
validating: 600 synthetic round-trips (all shapes/sizes) + 107 real
overlays from the whole ROM library, editing 2 bytes of each, recompressing
with the new encoder, and decompressing back to an exact match.

Wiring it into the pipeline also needed the overlay TABLE patched (a
separate header region caching each overlay's compressed size, distinct
from the FAT) - `NDSRom.rebuild()` gained an `overlay_comp_sizes` param
for this, mirroring how `banner_patch` already handles the other
non-FAT-tracked region (the cartridge banner).

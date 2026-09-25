"""Discover every graphic resource of a ROM and pair palette/char/map.

Output: list of resource dicts (JSON-serialisable):
  {'id', 'kind': 'bg'|'cell'|'atlas'|'tex'|'raw_bg'|'raw_atlas',
   'name', 'pal': loc|None, 'char': loc, 'map': loc|None, 'tex': i,
   'layout': how the container was found ('loose','narc','carve','bundle'),
   'raw': {...inferred params for headerless files}}

Discovery per FAT file (cheap cascade, SKILL 'container layers'):
  1. unwrap compression (lz10/lz11/rle/huff/'LZ77' prefix)
  2. NARC            -> members (each unwrapped again, nested NARC ok)
  3. whole-file Nitro (NCLR/NCGR/... or BTX0/BMD0) -> loose item
  4. headerless ext   (.nbfc/.nbfp/.nbfs/.ntft/.ntfp) -> raw item
  5. otherwise carve  -> every embedded Nitro file, raw + LZ-wrapped
     (publisher bundles / big archives; order is kept)

Pairing:
  * loose files (real names): same directory, by stem; palette falls back
    to the longest-prefix NCLR, then the directory's only NCLR.
  * ordered units (NARC members, carved blobs): span rule from Atelier -
    each NCGR owns items up to the next NCGR; palette = nearest NCLR
    BEFORE it, else first in its span; the first NCGR also claims maps
    that precede it.
"""
import collections
import re
import struct
from pathlib import PurePosixPath

from .. import compression
from ..compression import blz
from ..containers import (narc, cpk, ccb, offset_archive, cingpack, zipfs, indexpack, member_table, ntrp,
                          nitrofs, pair_table, lzchain)
from ..render import compose as C
from ..formats import (nitro, raw as rawfmt, bitmaps, g2d as nitro_g2d, banner as bannerfmt,
                       photo as photofmt, layton_arc)

G2D_KINDS = ('NCLR', 'NCGR', 'NSCR', 'NCER', 'NANR')
HASH_NAME = re.compile(r'^[0-9a-f]{8}$')
MAX_CARVE = 256 << 20

# populated as a side effect of scan() for CPK containers - not per-ROM
# state, just a simple visible counter so an unknown per-title codec shows
# up in the scan/health output instead of silently disappearing.
CPK_STATS = collections.Counter()

# populated as a side effect of scan(): top-level files whose CONTAINER
# (and, if present, COMPRESSION) were recognised and unwrapped correctly,
# but whose decompressed payload matched no known Nitro/raw-graphic format
# - e.g. a game's own hand-rolled cell/sprite layout. Game-agnostic (any
# title using member_table/etc hits this the same way): the point is to
# turn a silent "0 resources" into an actionable diagnostic in the GUI's
# Modo Avancado instead of looking indistinguishable from an unsupported
# ROM entirely. Cleared and refilled on every scan() call.
UNRESOLVED = []


def _member_sample(payload, ents, n=4):
    """First few members of a container, decompressed if possible, for the
    UNRESOLVED diagnostic - lets a human (or a future codec writer) see
    exactly what's failing to be recognised without re-running the scan."""
    out = []
    for i, (off, size) in enumerate(ents):
        if len(out) >= n:
            break
        if not size:
            continue
        member = payload[off:off + size]
        try:
            unwrapped, codec = compression.unwrap(member)
        except Exception:
            unwrapped, codec = member, None
        out.append({
            'index': i,
            'raw_size': size,
            'codec': codec or 'none (dados crus)',
            'payload_size': len(unwrapped),
            'head_hex': unwrapped[:16].hex(),
        })
    return out


def _norm_ext(path):
    pp = PurePosixPath(path)
    ext = pp.suffix.lower().rstrip('_')
    if ext == '.l':                       # 'x.ncg.l': '.l' only marks LZ (WFC utility.bin)
        ext = PurePosixPath(pp.stem).suffix.lower()
    return {'.ncbr': '.ncgr'}.get(ext, ext)


def _stem(path):
    name = PurePosixPath(path).name
    name = re.sub(r'(\.(lz|cmp|z|lz77|l))$', '', name, flags=re.I)
    name = name.rsplit('.', 1)[0] if '.' in name else name
    return re.sub(r'_(lz|cmp)$', '', name, flags=re.I)


# NitroCharacter-style trees keep one resource in parallel folders
# (x/Cell/a.NCER, x/Character/a.NCBR, x/ColorPalette/a.NCLR): pair across them
CATEGORY_DIRS = {'cell', 'character', 'colorpalette', 'palette', 'screen', 'anim', 'animation',
                 'ncer', 'ncgr', 'ncbr', 'nclr', 'nscr', 'nanr'}


def _pair_dir(path):
    parent = PurePosixPath(path).parent
    return str(parent.parent) if parent.name.lower() in CATEGORY_DIRS else str(parent)


def _sibling_index(rom, cache={}):
    """(dir, stem) -> [other paths sharing it], built once per ROM instead of
    re-walking every file (a PurePosixPath per entry) for each caller - on a
    21k-file ROM (Ragnarok DS) that walk, repeated per 'carve' file, was the
    single largest cost of a scan (~900 of ~1200s)."""
    key = id(rom)
    if key not in cache:
        idx = collections.defaultdict(list)
        for other in rom.files:
            op = PurePosixPath(other)
            idx[(str(op.parent), op.stem)].append(other)
        cache[key] = idx
    return cache[key]


def _sibling_names(rom, path, cache={}):
    """Leaf names for a headerless archive from a same-stem sibling name
    tree (Radiant Historia Data.bin + Data.ndx). None if there is none."""
    from ..containers import name_tree
    key = (id(rom), path)
    if key not in cache:
        cache[key] = None
        pp = PurePosixPath(path)
        for other in _sibling_index(rom).get((str(pp.parent), pp.stem), ()):
            if other == path:
                continue
            leaves = name_tree.parse_tree(rom.read(other))
            if leaves:
                cache[key] = leaves
                break
    return cache[key]


class Item:
    __slots__ = ('loc', 'kind', 'path', 'index')

    def __init__(self, loc, kind, path, index=0):
        self.loc, self.kind, self.path, self.index = loc, kind, path, index


def _explore(data, loc, path, depth=0, allow_carve=True):
    """Return (mode, [Item]) for one blob."""
    if depth < 2 and data[:1] in (b'\x10', b'\x11') and len(data) > 64:
        chain = lzchain.parse(data)
        if chain:
            items = []
            stem = PurePosixPath(path).stem
            try:
                members = [compression.decompress_as(data[off:off + used], c) for off, c, used in chain]
            except (ValueError, IndexError):
                members = None
            for i, (off, c, used) in enumerate(chain if members else ()):
                _, sub = _explore_decompressed(members[i], loc + [['lz', off, c, used]],
                                               f'{stem}/{i:04d}.bin', depth + 1)
                items.extend(sub)
            if items:
                return 'cpack', items
    if depth == 0 and path.startswith(('overlay9/', 'overlay7/')) and blz.footer(data):
        # SDK-compressed overlay (backward LZ, read from the end): decoded
        # dump, preview AND insert - compression/blz.py has a real encoder
        try:
            return _explore_decompressed(blz.decompress(data), loc, path, depth, allow_carve, 'blz')
        except ValueError:
            pass
    payload, codec = compression.unwrap(data)
    return _explore_decompressed(payload, loc, path, depth, allow_carve, codec)


def _explore_decompressed(payload, loc, path, depth=0, allow_carve=False, codec=None):
    """_explore for an already-decompressed blob; `codec` is the outer codec
    that produced it (its step is appended to `loc` here)."""
    if codec:
        loc = loc + [['codec', codec]]
        # the SDK chains its delta filter under a real codec (Theresia:
        # Huffman -> Diff8 -> NCGR); only that pairing is followed
        if payload[:1] in (b'\x81', b'\x82'):
            inner, filt = compression.unwrap(payload)
            if filt in ('diff8', 'diff16'):
                payload, loc = inner, loc + [['codec', filt]]
    if depth == 0 and not codec and PurePosixPath(path).suffix.lower() in ('.arc', '.arj'):
        found = layton_arc.open_any(payload, path)
        if found:
            return 'loose', [Item(loc, 'LAYTON_' + found[0].upper(), path)]
    if narc.is_narc(payload) and depth < 3:
        try:
            info = narc.parse(payload)
        except Exception:
            info = None
        if info:
            items = []
            for i, f in enumerate(info['files']):
                nm = info['names'][i] or f'{i:04d}'
                sub_mode, sub = _explore(f, loc + [['narc', i]], f'{path}/{nm}', depth + 1, allow_carve=False)
                items.extend(sub)
            return 'narc', items
    if ccb.is_ccb(payload) and depth < 3:
        try:
            entries = ccb.parse(payload)
        except (ValueError, struct.error):
            entries = None
        if entries is not None:
            items = []
            for i, e in enumerate(entries):
                try:
                    member = ccb.read(payload, e)
                except (ValueError, IndexError):
                    continue
                _, sub = _explore(member, loc + [['ccb', i]], f'{path}/{e["name"]}', depth + 1,
                                  allow_carve=False)
                items.extend(sub)
            return 'ccb', items
    if depth < 3:
        ents = nitrofs.parse(payload)
        if ents:
            items = []
            for i, (name, off, size) in enumerate(ents):
                _, sub = _explore(payload[off:off + size], loc + [['nfs', i]], f'{path}/{name}',
                                  depth + 1, allow_carve=False)
                items.extend(sub)
            return 'cpack', items
    if depth < 3 and zipfs.is_zip(payload):
        infos = zipfs.entries(payload)
        if infos:
            import io
            import zipfile
            zf = zipfile.ZipFile(io.BytesIO(payload))
            items = []
            for i, info in enumerate(infos):
                _, sub = _explore(zf.read(info), loc + [['zip', i]], f'{path}/{info.filename}',
                                  depth + 1, allow_carve=False)
                items.extend(sub)
            return 'cpack', items
    if depth == 0 and not codec:
        ents = member_table.parse(payload)
        if ents and len(ents) >= 2:
            items = []
            stem = PurePosixPath(path).stem
            for i, (off, size) in enumerate(ents):
                if not size:
                    continue
                _, sub = _explore(payload[off:off + size], loc + [['mtab', i]],
                                  f'{stem}/{i:04d}.bin', depth + 1, allow_carve=False)
                items.extend(sub)
            if items:
                return 'cpack', items
            # no Nitro block inside: the members may still be HEADERLESS
            # graphics, exactly like the explicit-count archives of Magical
            # Starsign. Heroes of Mana keeps its whole 2D set this way
            # (data/*shap.dat, titleload.dat, story.dat...), and without this
            # the container was opened and then silently dropped.
            raw_items = _archive_raw_items(payload, loc, path, ents, 'mtab')
            if raw_items:
                return 'raw', raw_items
            # container AND (per-member) compression both recognised fine -
            # the members themselves just aren't a format this tool knows
            # how to read (a game-proprietary cell/sprite layout, most
            # often). Record it instead of silently contributing nothing,
            # so 'ele so achou o banner' has a reason on screen.
            UNRESOLVED.append({
                'path': path, 'container': 'member_table', 'entries': len(ents),
                'samples': _member_sample(payload, ents),
            })
    if depth == 0 and payload[:8] == bytes(8) and indexpack.TYPE_TOKEN.search(PurePosixPath(path).stem):
        ents = indexpack.parse(payload)
        if ents:
            group = indexpack.group_name(path)
            items = []
            for i, (_, off, size) in enumerate(ents):
                if size:
                    _, sub = _explore(payload[off:off + size], loc + [['ipack', i]],
                                      f'{group}/{i:04d}.bin', depth + 1, allow_carve=False)
                    items.extend(sub)
            return 'cpack', items
    if depth < 3 and payload[:4] == ntrp.MAGIC:
        # Ragnarok DS `2d_u/**/*.ntrp`: an (offset, size) table over complete
        # graphics files - standard Nitro in the icon/object bundles, the
        # Kaijuu-style "lite" Nitro (NCG/NCL/NSC) in the full-screen
        # backgrounds, which carry no reversed magic for the carver to find
        ents = ntrp.parse(payload)
        if ents:
            items = []
            stem = PurePosixPath(path).stem
            for i, (off, size) in enumerate(ents):
                _, sub = _explore(payload[off:off + size], loc + [['ntrp', i]],
                                  f'{stem}/{i:04d}.bin', depth + 1, allow_carve=False)
                items.extend(sub)
            # the bundle groups by kind (all screens, then all tilesets, then
            # all palettes), so member index N means different things per
            # kind; renumber PER KIND so the i-th screen, i-th tileset and
            # i-th palette share a stem and the normal raw pairing matches
            # them (verified: 4 screens + 4 tilesets + 4 palettes per
            # background bundle, and 3 + 3 + 3 in the shorter one)
            ranks = collections.Counter()
            for it in items:
                ext = it.path.rsplit('.', 1)[-1]
                it.path = f'{stem}/{ranks[it.kind]:04d}.{ext}'
                ranks[it.kind] += 1
            if items:
                return 'ntrp', items
    if depth < 3 and payload[:4] == b'\0\0\0\0':
        entries = cingpack.parse(payload)
        if entries:
            items = []
            for i, e in enumerate(entries):
                _, sub = _explore(cingpack.read(payload, e), loc + [['cpack', i]], f'{path}/{e["name"]}',
                                  depth + 1, allow_carve=False)
                items.extend(sub)
            return 'cpack', items
    if depth == 0 and not codec and not nitro.kind(payload):
        items = _offset_archive_items(payload, loc, path)
        if items:
            return 'raw', items
    if depth < 3 and not nitro.kind(payload):
        ents = pair_table.parse(payload)
        if ents:
            items = _archive_raw_items(payload, loc, path, ents, 'ptab')
            if items:
                return 'raw', items
    if depth == 0 and cpk.is_cpk(payload) and len(payload) < MAX_CARVE:
        try:
            entries = cpk.list_files(payload)
        except Exception:
            entries = []
        if entries:
            items = []
            for name, off, size, esize in entries:
                if off < 0 or off + size > len(payload):
                    continue
                if size != esize:
                    # compressed entry: only CRI's own tagged CRILAYLA can be
                    # decoded here (see nitrokit.containers.cpk). Anything
                    # else is a per-title codec this tool does not know -
                    # counted and surfaced (CPK_STATS), never silently lost.
                    blob = payload[off:off + size]
                    if cpk.is_crilayla(blob):
                        try:
                            dec = cpk.crilayla_decompress(blob)
                        except (ValueError, IndexError):
                            CPK_STATS['broken'] += 1
                            continue
                        if len(dec) != esize:
                            CPK_STATS['broken'] += 1
                            continue
                        _, sub = _explore(dec, loc + [['crilayla', off, size, esize]],
                                          f'{path}/{name}', depth + 1)
                        items.extend(sub)
                        CPK_STATS['crilayla'] += 1
                    else:
                        CPK_STATS['unknown_codec'] += 1
                    continue
                _, sub = _explore(payload[off:off + size], loc + [['slice', off, size]],
                                  f'{path}/{name}', depth + 1)
                items.extend(sub)
            return 'cpk', items
    from ..rom.ndsrom import NDSRom, looks_like_rom
    if depth == 0 and looks_like_rom(payload):
        try:
            sub = NDSRom(payload)
        except Exception:
            sub = None
        if sub:
            groups = []
            if bannerfmt.is_banner(sub.data, sub.banner_offset()):
                groups.append(('banner', [Item(loc + [['banner']], 'BANNER', f'{path} (nested ROM icon)')]))
            for _, p in sub.all_files():
                if p.startswith(('overlay9/', 'overlay7/')):
                    continue
                m, its = _explore(sub.read(p), loc + [['srl', p]], f'{path}/{p}', depth + 1)
                if its:
                    groups.append((m, its))
            return 'srl', groups
    from ..formats import monolith, sir0
    if sir0.is_sir0(payload):
        for cls, kind in ((sir0.open_image, 'SIRIMG'), (sir0.SirSprite, 'SIRSPR')):
            try:
                cls(payload)
                return 'loose', [Item(loc, kind, path)]
            except (sir0.Unsupported, struct.error):
                pass
    if payload[:4] in monolith.MAGICS:
        recs = monolith.records(payload)
        if len(recs) == 1:
            return 'loose', [Item(loc, recs[0][2], path)]
        return 'monolith', [Item(loc + [['slice', off, size]], m, f'{path}@{off:x}')
                            for off, size, m in recs]
    if payload[:4] == b'CBP1':
        return 'loose', [Item(loc, 'CBP1', path)]
    if len(payload) >= 4:
        # a loose banner-shaped file elsewhere in the ROM (Emily the
        # Strange ships 3 DSi-animated .bnr icons outside the cartridge
        # banner slot) - gated on an exact size AND CRC match, since the
        # version field alone (1/2/3/0x103) is too common a leading value
        # to trust on its own
        v = struct.unpack_from('<H', payload, 0)[0]
        size = bannerfmt.SIZE_BY_VERSION.get(v)
        if size and len(payload) == size:
            crc = struct.unpack_from('<H', payload, 2)[0]
            if crc == bannerfmt.crc16_modbus(payload[bannerfmt.CRC_SPAN[0]:bannerfmt.CRC_SPAN[1]]):
                return 'loose', [Item(loc, 'BANNER', path)]
    k = nitro.kind(payload)
    if k and k != 'NARC':
        return 'loose', [Item(loc, k, path)]
    fmt = bitmaps.detect(payload, path)
    if fmt:
        return 'loose', [Item(loc, 'BITMAP_' + fmt, path)]
    ph = photofmt.detect(payload, path)
    if ph:
        family, label = ph
        return 'loose', [Item(loc, ('PHOTO_' if family == 'public' else 'PHOTOCODEC_') + label, path)]
    ext = _norm_ext(path)
    # the magic-bearing "lite" exports win over any extension guess below
    lite = rawfmt.nitro_lite(payload)
    if lite:
        kind, hdr, _ = lite
        pp = PurePosixPath(path)
        base = re.sub(r'_(ncg|ncl|nsc)$', '', pp.stem, flags=re.I)
        name = str(pp.with_name(f'{base}.{kind}'))
        if kind == 'screen':
            return 'raw', [Item(loc, 'RAW_SCREEN', name)]
        return 'raw', [Item(loc + [['slice', hdr, len(payload) - hdr]], 'RAW_' + kind.upper(), name)]
    if ext in rawfmt.RAW_EXT and rawfmt.RAW_EXT[ext]:
        return 'raw', [Item(loc, 'RAW_' + rawfmt.RAW_EXT[ext].upper(), path)]
    if ext == '.bin' and rawfmt.is_palette_named(path, len(payload)):
        return 'raw', [Item(loc, 'RAW_PAL', path)]
    if ext == '.bin' and rawfmt.is_tiles_named(path, len(payload)):
        return 'raw', [Item(loc, 'RAW_CHAR', path)]
    if not allow_carve and depth > 0:
        # NARC members that are small bundles are still worth carving
        pass
    if len(payload) < 32 or len(payload) > MAX_CARVE:
        return 'none', []
    items, lz_spans = [], []
    if not codec:
        from .source import lz_consumed
        for off, lzc, size, kind in nitro.carve_lz(payload):
            try:
                used = lz_consumed(payload, off, lzc)
            except IndexError:
                continue
            lz_spans.append((off, off + used))
            items.append(Item(loc + [['lz', off, lzc, used]], kind, f'{path}@{off:x}'))
    for off, size, kind in nitro.carve(payload):
        # a compressor copies short headers verbatim as literals: a "raw" hit
        # inside an LZ stream is a false positive (Lufia mcd.dat)
        if any(a <= off < b for a, b in lz_spans):
            continue
        if kind in ('NARC', 'SDAT'):
            if kind == 'NARC':
                _, sub = _explore(payload[off:off + size], loc + [['slice', off, size]], f'{path}@{off:x}', depth + 1)
                items.extend(sub)
            continue
        items.append(Item(loc + [['slice', off, size]], kind, f'{path}@{off:x}'))
    items.sort(key=lambda it: next((s[1] for s in reversed(it.loc) if s[0] in ('slice', 'lz')), 0))
    if len(items) == 1 and items[0].loc[-1][0] == 'lz' and items[0].loc[-1][1] == 0 and depth == 0:
        # a whole compressed file with junk padding after the stream: it is a
        # loose named file, not a bundle (Elite Beat Agents *.NCGR_)
        return 'loose', [Item(items[0].loc, items[0].kind, path)]
    return ('carve' if items else 'none'), items


RAW_SUFFIX_KIND = {'_ncg': 'RAW_CHAR', '_ncl': 'RAW_PAL', '_nsc': 'RAW_SCREEN'}


def _offset_archive_items(payload, loc, path):
    """Raw graphics inside a magic-less offset-table archive (Magical
    Starsign hiraishi/*.dat). Two ways to tell what an entry is:
      * archive named with a Nitro suffix (map_ncg.dat / map_ncl.dat): every
        entry has that kind, and entry i of x_ncg pairs with entry i of x_ncl;
      * mixed archive (mesFace.dat): a palette-sized entry (32..512 bytes)
        right after a tile-sized one is that tileset's palette.
    Entries become RAW_CHAR / RAW_PAL items with a shared stem so the raw
    pairing matches them; anything else in the archive is left alone."""
    entries = offset_archive.parse(payload)
    if not entries:
        return None
    return _archive_raw_items(payload, loc, path, entries, 'oarc')


def _archive_raw_items(payload, loc, path, entries, tag):
    """The container-independent half of _offset_archive_items: given the
    entry list of ANY magic-less archive, decide which entries are raw
    graphics. Used for both `oarc` and `mtab` archives."""
    stem = PurePosixPath(path).with_suffix('')
    name_lower = stem.name.lower()
    suffix = digit = None
    for k in RAW_SUFFIX_KIND:
        m = re.match(rf'^(.*){k}(\d*)$', name_lower)
        if m:
            suffix, digit = k, m.group(2)
            break
    items = []
    if suffix:
        # a numbered variant (obj_ncg1/obj_ncl1/obj_ncl2) keeps its own
        # number in the prefix so entries from ncl1 and ncl2 do not collide
        cut = len(suffix) + len(digit)
        prefix = str(stem)[:-cut] + digit
        ext = {'RAW_CHAR': 'chr', 'RAW_PAL': 'pal', 'RAW_SCREEN': 'scr'}[RAW_SUFFIX_KIND[suffix]]
        for i, (o, size) in enumerate(entries):
            if size:
                items.append(_raw_member(payload, loc, i, RAW_SUFFIX_KIND[suffix],
                                         f'{prefix}/{i:04d}.{ext}', entries, tag))
        return [it for it in items if it]
    members = [compression.unwrap(payload[o:o + size])[0] if size else b'' for o, size in entries]
    triples = _pal_first_groups(members)
    live = sum(1 for m in members if m)
    if triples and 3 * len(triples) >= 0.5 * live:
        for pal, scr, char in triples:
            base = f'{stem}/{pal:04d}'
            items.append(_raw_member(payload, loc, char, 'RAW_CHAR', f'{base}.chr', entries, tag))
            items.append(_raw_member(payload, loc, pal, 'RAW_PAL', f'{base}.pal', entries, tag))
            if scr is not None:
                items.append(_raw_member(payload, loc, scr, 'RAW_SCREEN', f'{base}.scr', entries, tag))
        return items
    pairs = []
    for i in range(1, len(members)):
        pal, chars = members[i], members[i - 1]
        if (32 <= len(pal) <= 512 and len(pal) % 32 == 0 and len(chars) >= 512 and len(chars) % 32 == 0
                and len(chars) > len(pal)
                and all(w < 0x8000 for w in struct.unpack_from(f'<{len(pal) // 2}H', pal))):
            pairs.append(i)
    live = sum(1 for m in members if m)
    # a data archive can hold a few tile/palette-sized neighbours by chance
    # (Magical Starsign map_shp: 10% of entries); graphics archives pair
    # a quarter or more of their entries (mesFace: all of them)
    if pairs and 2 * len(pairs) >= 0.2 * live:
        for i in pairs:
            items.append(_raw_member(payload, loc, i - 1, 'RAW_CHAR',
                                     f'{stem}/{i - 1:04d}.chr', entries, tag))
            items.append(_raw_member(payload, loc, i, 'RAW_PAL',
                                     f'{stem}/{i - 1:04d}.pal', entries, tag))
        return items
    return _screen_only_items(payload, loc, path, stem, entries, members, tag)


def _is_palette(m):
    return (32 <= len(m) <= 512 and len(m) % 32 == 0
            and all(w < 0x8000 for w in struct.unpack_from(f'<{len(m) // 2}H', m)))


def _pal_first_groups(members):
    """(pal, screen|None, chars) index triples for archives that store a
    palette FIRST, then an optional tile map, then the tiles (Inazuma
    Eleven pic2d: 32 | 2*T | 32*T bytes). The map must only point at tiles
    that exist, which random data practically never does."""
    out, i = [], 0
    while i + 1 < len(members):
        pal = members[i]
        if not pal or not _is_palette(pal):
            i += 1
            continue
        tile = 32 if len(pal) == 32 else 64
        nxt = members[i + 1]
        if (i + 2 < len(members) and nxt and len(nxt) % 2 == 0 and members[i + 2]
                and len(members[i + 2]) % tile == 0):
            n_tiles = len(members[i + 2]) // tile
            ents = struct.unpack_from(f'<{len(nxt) // 2}H', nxt)
            if max(e & 0x3FF for e in ents) < n_tiles:
                out.append((i, i + 1, i + 2))
                i += 3
                continue
        if nxt and len(nxt) % tile == 0 and len(nxt) >= 4 * tile:
            out.append((i, None, i + 1))
            i += 2
            continue
        i += 1
    return out


def _screen_only_items(payload, loc, path, stem, entries, members, tag='oarc'):
    """A screen (NSCR-style tile map) with no tileset anywhere in its own
    archive (Magical Starsign `map_amp.dat`/`map_shp.dat`: the tiles live in
    a DIFFERENT archive, `map_ncg.dat`, and nothing ties one index to the
    other - unlike the suffix-paired case above where entry i always means
    entry i on both sides). Detected by real screen-data signatures, not by
    size alone (`map_id.dat`/`map_evt.dat`/`map_anm.dat` are also u16/small-
    struct arrays but never vary sub-palette or set flip bits): a live entry
    counts as a screen when it decodes to an even-length u16 array of at
    least 16 entries that uses 2+ different sub-palette nibbles OR sets a
    flip bit anywhere. Exposed as a `raw_screen` resource (kind
    'RAW_SCREEN_ONLY') the user can link to any tileset via 'Set tileset...'
    in the GUI - see pipeline.resource.link_tileset / gui.app.RawLabTab.link_tileset."""
    def is_screen(m):
        # require BOTH signatures (not just one) plus real tile variety and
        # a size in the range a real screen (not an event/animation struct)
        # would be - a single loose signal false-positived on this archive's
        # own event/object/animation tables (evt_scp, map_obj, cmake...),
        # which also happen to be u16-ish and vary their top bits by chance
        if len(m) < 128 or len(m) % 2:
            return False
        arr = struct.unpack_from(f'<{len(m) // 2}H', m)
        tiles = [e & 0x3FF for e in arr]
        if max(tiles) >= 2048 or len({t for t in tiles if t}) < 8:
            return False
        return len({e >> 12 for e in arr}) >= 2 or any(e & 0xC00 for e in arr)
    hits = [i for i, m in enumerate(members) if m and is_screen(m)]
    live = sum(1 for m in members if m)
    # a real screen archive is homogeneous (every live entry is a screen);
    # a data archive that stumbles into a few false positives will not clear
    # a strict majority
    if not hits or 2 * len(hits) < live:
        return None
    # event/object/animation tables (evt_scp, map_obj, obj_anm1...) are
    # structs of many different sizes; a real screen archive reuses a
    # handful of map sizes over and over (map_amp: 20 distinct lengths
    # across 1142 entries) - this alone throws out every false positive
    # the per-entry signature check let through in practice
    lens = {len(m) for m in members if m}
    if len(lens) > 0.1 * live:
        return None
    return [_raw_member(payload, loc, i, 'RAW_SCREEN_ONLY', f'{stem}/{i:04d}.scr', entries, tag)
            for i in hits]


def _raw_member(payload, loc, i, kind, name, entries=None, tag='oarc'):
    """`entries`/`tag` let the same raw-member heuristics serve both
    magic-less archive shapes: `oarc` (explicit count, Magical Starsign) and
    `mtab` (implicit count, Heroes of Mana / Akagi) - both are readable AND
    writable locators (see pipeline.source)."""
    o, size = (entries or offset_archive.parse(payload))[i]
    _, codec = compression.unwrap(payload[o:o + size])
    step = loc + [[tag, i]] + ([['codec', codec]] if codec else [])
    return Item(step, kind, name)


def _font_resource(item, out):
    out.append({'kind': 'font', 'name': item.path, 'char': item.loc, 'pal': None,
               'map': None, 'layout': 'font', 'owner': item.path})


def _special_resource(item, session, out, stats):
    """Items that are not paired: fonts, nested-ROM banners, Monolith
    OBP1/BGP1. Returns True if the item was consumed."""
    if item.kind == 'NFTR':
        _font_resource(item, out)
    elif item.kind == 'BANNER':
        out.append({'kind': 'banner', 'name': item.path, 'char': item.loc, 'pal': None,
                    'map': None, 'layout': 'banner', 'owner': item.path})
    elif item.kind == 'LAYTON_BG':
        out.append({'kind': 'layton_bg', 'name': item.path, 'char': item.loc, 'pal': None,
                    'map': None, 'layout': 'layton_arc', 'owner': item.path})
    elif item.kind == 'LAYTON_ANI':
        ani = layton_arc.LaytonAni(session.get(item.loc), arj=item.path.lower().endswith('.arj'))
        # one resource per image (frame); 'tex' is the generic sub-index field
        for i in range(len(ani.images)):
            out.append({'kind': 'layton_ani', 'name': f'{item.path}#{i}', 'char': item.loc, 'pal': None,
                        'map': None, 'tex': i, 'layout': 'layton_arc', 'owner': item.path})
    elif item.kind in ('SIRIMG', 'SIRSPR'):
        out.append({'kind': 'sir' if item.kind == 'SIRIMG' else 'sir_sprite', 'name': item.path, 'char': item.loc, 'pal': None,
                    'map': None, 'layout': 'sir0', 'owner': item.path})
    elif item.kind.startswith('PHOTO_'):
        out.append({'kind': 'photo', 'format': item.kind[6:], 'name': item.path, 'char': item.loc,
                    'pal': None, 'map': None, 'layout': 'photo', 'owner': item.path})
        stats['photo'] += 1
    elif item.kind.startswith('PHOTOCODEC_'):
        # recognised, not decodable: counted so the scan/coverage can name it
        stats['photo_codec: ' + item.kind[11:]] += 1
    elif item.kind.startswith('BITMAP_'):
        out.append({'kind': 'bitmap', 'format': item.kind[7:], 'name': item.path, 'char': item.loc,
                    'pal': None, 'map': None, 'layout': 'bitmap', 'owner': item.path})
    elif item.kind in ('OBP1', 'BGP1'):
        from ..formats import monolith
        cls = monolith.OBP1 if item.kind == 'OBP1' else monolith.BGP1
        try:
            cls(session.get(item.loc))
        except (monolith.Unsupported, struct.error, ValueError):
            stats['monolith_unsupported'] += 1
            return True
        out.append({'kind': item.kind[:3].lower(), 'name': item.path, 'char': item.loc, 'pal': None,
                    'map': None, 'layout': 'monolith', 'owner': item.path})
    elif item.kind == 'CBP1':
        try:
            bitmaps.CBP1(session.get(item.loc))
        except (ValueError, struct.error):
            stats['cbp_unsupported'] += 1
            return True
        out.append({'kind': 'cbp', 'name': item.path, 'char': item.loc, 'pal': None,
                    'map': None, 'layout': 'cbp', 'owner': item.path})
    else:
        return False
    return True


def _texture_resources(item, session, out):
    from ..formats.tex0 import TEX0
    try:
        t = TEX0(session.get(item.loc))
    except Exception:
        return
    for i, tex in enumerate(t.textures):
        out.append({'kind': 'tex', 'name': f'{item.path}/{tex["name"] or i}', 'char': item.loc, 'tex': i,
                    'format': tex['format'], 'w': tex['w'], 'h': tex['h']})


def _pair_ordered(items, layout, out, owner):
    """Ordered container pairing.

    Groups are delimited by the kind of the FIRST 2D item: that kind opens
    every group (NCLR NCGR NSCR | NCLR ... in Atelier/NARCs, NCER NCGR NCLR |
    NCER ... in Lufia's mcd.dat, NCGR NCLR NCER | ... elsewhere). Inside a
    group every map goes to the nearest preceding NCGR (maps before the first
    NCGR go to the first one - Atelier's 'NCER NCGR NCLR NANR' case). A group
    without its own palette carries the previous group's palette forward
    (one palette feeding several tilesets); if nothing precedes, the next
    group's palette is borrowed."""
    g2d = [it for it in items if it.kind in G2D_KINDS and it.kind != 'NANR']
    if not g2d:
        return
    opener = g2d[0].kind
    if opener == 'NCLR' and sum(1 for it in g2d if it.kind == 'NCLR') == 1:
        opener = 'NCGR'                  # a single global palette up front
    groups, cur = [], []
    for it in g2d:
        if it.kind == opener and any(x.kind == opener for x in cur):
            groups.append(cur)
            cur = []
        cur.append(it)
    groups.append(cur)
    pals = [next((x for x in g if x.kind == 'NCLR'), None) for g in groups]
    last_pal = None
    for gi, g in enumerate(groups):
        pal = pals[gi] or last_pal or next((p for p in pals[gi + 1:] if p), None)
        if pals[gi]:
            last_pal = pals[gi]
        chars = [x for x in g if x.kind == 'NCGR']
        if not chars:
            continue
        owned = {id(c): [] for c in chars}
        cur_char = chars[0]
        for x in g:
            if x.kind == 'NCGR':
                cur_char = x
            elif x.kind in ('NSCR', 'NCER'):
                owned[id(cur_char)].append(x)
        for ch in chars:
            base = {'char': ch.loc, 'pal': pal.loc if pal else None, 'layout': layout, 'owner': owner}
            if not owned[id(ch)]:
                out.append({**base, 'kind': 'atlas', 'name': ch.path, 'map': None})
            for m in owned[id(ch)]:
                out.append({**base, 'kind': 'bg' if m.kind == 'NSCR' else 'cell', 'name': m.path, 'map': m.loc})


def _similar_palette(stem, pals):
    """Best NCLR for a tileset without a same-stem palette: longest common
    prefix + common suffix ('bg0_s' -> 'bg_s', 'c10s1a0' -> 'c10s1'); the
    directory's only palette if nothing is similar."""
    import os
    if HASH_NAME.match(stem):
        # '0026b1d5' (Code Lyoko vfsflat.zip: CRC32 of a lost path): two hash
        # names sharing digits is chance, not a relation - never pair on it
        return None
    scored = []
    for p, it in pals.items():
        lcp = len(os.path.commonprefix([stem, p]))
        lcs = len(os.path.commonprefix([stem[::-1], p[::-1]]))
        scored.append((lcp + min(lcs, len(p) - lcp), lcp, it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[0][:2] != scored[1][:2]):
        return scored[0][2]
    return next(iter(pals.values())) if len(pals) == 1 else None


def _pair_loose(items, out):
    """items: Items with real file paths (one per FAT file)."""
    by_dir = collections.defaultdict(list)
    for it in items:
        by_dir[_pair_dir(it.path)].append(it)
    for d, its in by_dir.items():
        stems = collections.defaultdict(dict)
        pals = {}
        for it in its:
            s = _stem(it.path).lower()
            stems[s].setdefault(it.kind, it)
            if it.kind == 'NCLR':
                pals[s] = it
        chars = {s: g['NCGR'] for s, g in stems.items() if 'NCGR' in g}
        for s, group in stems.items():
            ch = group.get('NCGR')
            if not ch:
                # maps without a same-name tileset borrow the most similarly
                # named one (x/Screen/bg_03.NSCR -> x/Character/bg_01.NCGR)
                if not chars or not any(k in group for k in ('NSCR', 'NCER')):
                    continue
                ch = _similar_palette(s, chars)
                if ch is None:
                    continue
                if 'NCLR' not in group:
                    group = {**group, 'NCLR': stems[_stem(ch.path).lower()].get('NCLR')}
                    if group['NCLR'] is None:
                        del group['NCLR']
            pal = group.get('NCLR')
            if pal is None and pals:
                pal = _similar_palette(s, pals)
            base = {'char': ch.loc, 'pal': pal.loc if pal else None, 'layout': 'loose', 'owner': ch.path}
            maps = [group[k] for k in ('NSCR', 'NCER') if k in group]
            if not maps:
                out.append({**base, 'kind': 'atlas', 'name': ch.path, 'map': None})
            for m in maps:
                out.append({**base, 'kind': 'bg' if m.kind == 'NSCR' else 'cell', 'name': m.path, 'map': m.loc})


def _screen_fits(session, scr, char, declared_ok=False):
    """A raw screen only pairs with a raw tileset whose tile count covers
    every index it uses (index-matched archives of different lengths, e.g.
    Magical Starsign fldvfx_ncg x200 vs fldvfx_nsc x800, would otherwise
    pair unrelated entries)."""
    try:
        scr_data = session.get(scr.loc)
        entries, _, _ = rawfmt.screen_layout(scr_data)
        char_data = session.get(char.loc)
        tiles = len(char_data) // 32
        if char.loc[-1][0] == 'slice':
            lite = rawfmt.nitro_lite(session.get(char.loc[:-1]))
            if lite and lite[2] == 8:
                tiles = len(char_data) // 64
    except Exception:
        return False
    if not entries:
        return False
    if max(e & 0x3FF for e in entries) < tiles:
        return True
    # a screen whose OWN header declares its size (nitro-lite NSC) names its
    # tileset by stem - strong evidence. Exporters trim trailing empty tiles
    # and overlay layers also index a second tileset loaded above the first
    # (Kaijuu Busters title_bg_B_01: 0-262 own, 768+ the other), so demand a
    # clear majority instead of every index; out-of-range tiles just stay
    # transparent in the composer.
    if declared_ok and rawfmt.nitro_lite(scr_data):
        return 2 * sum((e & 0x3FF) < tiles for e in entries) > len(entries)
    return False


def _cross_archive_tileset(dir_, index, own_prefix, tilesets):
    """A RAW_SCREEN_ONLY item ('hiraishi/map_amp/0004.scr') has no tileset in
    its own archive - the tileset is a SEPARATE offset-archive in the same
    directory (Magical Starsign map_ncg.dat/map_ncl.dat, exposed here as
    'hiraishi/map/0004.chr'), addressed by the SAME numeric index. The only
    generic thing tying the two archives together is that one's stem is a
    prefix of the other's ('map' in 'map_amp'/'map_ncg'/'map_ncl') - true of
    every game following this convention, not just this one. Every match is
    still validated by tile-fit before use (see the caller), so a
    coincidental prefix match can only ever fail closed, never mis-render."""
    return [(prefix, cp) for (d, prefix, idx), cp in tilesets.items()
           if d == dir_ and idx == index and own_prefix.startswith(prefix)]


def _pair_raw(items, out, session=None, stats=None):
    by_stem = collections.defaultdict(dict)
    pals_by_dir = collections.defaultdict(dict)
    tilesets = {}                    # (dir, archive_prefix, index) -> (char_loc, pal_loc)
    for it in items:
        by_stem[str(PurePosixPath(it.path).with_suffix(''))][it.kind] = it
        if it.kind == 'RAW_PAL':
            pals_by_dir[str(PurePosixPath(it.path).parent)][PurePosixPath(it.path).stem.lower()] = it
    for it in items:
        if it.kind != 'RAW_CHAR':
            continue
        p = PurePosixPath(it.path)
        stem = str(p.with_suffix(''))
        pal = by_stem[stem].get('RAW_PAL')
        tilesets[(str(p.parent.parent), p.parent.name, p.stem)] = (it.loc, pal.loc if pal else None)
    claimed = set()                  # chars already shown through a screen
    chars_by_dir = collections.defaultdict(dict)
    for it in items:
        if it.kind == 'RAW_CHAR':
            chars_by_dir[str(PurePosixPath(it.path).parent)][PurePosixPath(it.path).stem] = it
    for stem, g in list(by_stem.items()):
        if 'RAW_SCREEN' in g and 'RAW_CHAR' not in g and session is not None:
            # menu_BG_a.screen / menu_BG_b.screen share menu_BG.char: the
            # longest same-directory tileset stem that prefixes the screen's
            p = PurePosixPath(stem)
            dir_chars = chars_by_dir[str(p.parent)]
            for cstem in sorted(dir_chars, key=len, reverse=True):
                char = dir_chars[cstem]
                if p.name.startswith(cstem) and _screen_fits(session, g['RAW_SCREEN'], char):
                    cg = by_stem[str(PurePosixPath(char.path).with_suffix(''))]
                    g['RAW_CHAR'] = char
                    claimed.add(id(char))
                    if 'RAW_PAL' not in g and 'RAW_PAL' in cg:
                        g['RAW_PAL'] = cg['RAW_PAL']
                    break
            else:
                # end.screen drawn with end_00.char .. end_18.char: one
                # screen shared by every tileset whose stem extends it
                for cstem in sorted(dir_chars):
                    char = dir_chars[cstem]
                    if cstem.startswith(p.name + '_') and                             _screen_fits(session, g['RAW_SCREEN'], char, declared_ok=True):
                        cg = by_stem[str(PurePosixPath(char.path).with_suffix(''))]
                        if 'RAW_SCREEN' in cg:
                            continue
                        cg['RAW_SCREEN'] = g['RAW_SCREEN']
                        cg['_shared_screen'] = True
                        g['_given'] = True
    for stem, g in by_stem.items():
        if 'RAW_CHAR' in g:
            scr = g.get('RAW_SCREEN')
            if scr is not None and session is not None and not _screen_fits(
                    session, scr, g['RAW_CHAR'], declared_ok=True):
                scr = None
            if scr is None and id(g['RAW_CHAR']) in claimed:
                continue
            pal = g.get('RAW_PAL')
            if pal is None:
                # objA0008.LZE -> palA0008.bin: most similarly named palette
                pals = pals_by_dir[str(PurePosixPath(stem).parent)]
                pal = _similar_palette(PurePosixPath(stem).name.lower(), pals) if pals else None
            shown = g['RAW_CHAR'].path if g.get('_shared_screen') else (scr or g['RAW_CHAR']).path
            res = {'kind': 'raw_bg' if scr else 'raw_atlas', 'name': shown,
                   'char': g['RAW_CHAR'].loc, 'pal': pal.loc if pal else None,
                   'map': scr.loc if scr else None, 'layout': 'raw', 'owner': stem}
            if session is not None and g['RAW_CHAR'].loc[-1][0] == 'slice':
                lite = rawfmt.nitro_lite(session.get(g['RAW_CHAR'].loc[:-1]))
                if lite and lite[2]:
                    res['raw'] = {'bpp': lite[2]}
            out.append(res)
        if 'RAW_TEXEL' in g:
            pal = g.get('RAW_TEXPAL')
            res = {'kind': 'raw_tex', 'name': g['RAW_TEXEL'].path, 'char': g['RAW_TEXEL'].loc,
                   'pal': pal.loc if pal else None, 'map': None, 'layout': 'raw', 'owner': stem}
            hdr = g.get('RAW_BMPHDR')
            if hdr is not None and session is not None:
                dims = rawfmt.sbmp_header(session.get(hdr.loc))
                if dims and dims[1] * dims[2] * dims[0] // 8 == len(session.get(g['RAW_TEXEL'].loc)):
                    res['raw'] = {'bpp': dims[0]}
                    res['atlas'] = {'width_px': dims[1], 'confidence': 1.0, 'declared': dims[1], 'auto': False}
            out.append(res)
        if 'RAW_SCREEN' in g and 'RAW_CHAR' not in g and not g.get('_given'):
            # a named map whose tileset can't be told from the names (WFC
            # 'eb2Ap.nsc' is drawn with 'ebBgStep2.ncg'): list it for a manual
            # 'Set tileset...' instead of dropping it silently
            it = g['RAW_SCREEN']
            out.append({'kind': 'raw_screen', 'name': it.path, 'char': None, 'pal': None,
                        'map': it.loc, 'layout': 'raw', 'owner': stem})
        if 'RAW_SCREEN_ONLY' in g and 'RAW_CHAR' not in g:
            it = g['RAW_SCREEN_ONLY']
            p = PurePosixPath(it.path)
            linked = None
            for _prefix, (char_loc, pal_loc) in _cross_archive_tileset(
                    str(p.parent.parent), p.stem, p.parent.name, tilesets):
                if session is not None and _screen_fits(session, it, type('I', (), {'loc': char_loc})()):
                    linked = (char_loc, pal_loc)
                    break
            if linked:
                out.append({'kind': 'raw_bg', 'name': it.path, 'char': linked[0], 'pal': linked[1],
                            'map': it.loc, 'layout': 'raw-cross', 'owner': stem})
                if stats is not None:
                    stats['raw_screen_linked'] += 1
            else:
                out.append({'kind': 'raw_screen', 'name': it.path, 'char': None, 'pal': None,
                            'map': it.loc, 'layout': 'raw', 'owner': stem})
                if stats is not None:
                    stats['raw_screen_unlinked'] += 1


def _register_tables(items, session):
    from ..containers import offset_table
    import json
    by_parent = collections.defaultdict(list)
    for it in items:
        st = it.loc[-1]
        if st[0] == 'slice':
            by_parent[json.dumps(it.loc[:-1])].append((st[1], st[2]))
        elif st[0] == 'lz':
            by_parent[json.dumps(it.loc[:-1])].append((st[1], st[3]))
    for key, spans in by_parent.items():
        spans.sort()
        blob = session.get(json.loads(key))
        t = offset_table.detect(blob, spans)
        if t:
            session.tables[key] = t


def _link_index_tables(rom, session, loose, resources, stats, path_filter=None):
    """Index packs (containers.indexpack) share one index space, but a screen
    or cell may still use a tileset from ANOTHER index. Theresia keeps those
    links in small sibling tables of u16 rows (bgdata.dat: row j = screen j
    -> tileset, palette; spdata.dat: tileset, palette, cell, animation).
    No names are trusted: every small `len % 8 == 0` file next to the packs
    is tried as 4 x u16 rows, and a column reading is accepted only when most
    rows it applies to really fit (every tile / OAM inside the tileset).
    Accepted links replace the same-index guess for that screen or cell."""
    import json
    groups = collections.defaultdict(lambda: collections.defaultdict(dict))
    for it in loose:
        if any(st[0] == 'ipack' for st in it.loc):
            p = PurePosixPath(it.path)
            groups[str(p.parent.parent)][it.kind][int(p.stem)] = it
    if not groups:
        return
    by_map = {json.dumps(r['map']): r for r in resources if r.get('map')}

    def screen_fits(scr, ch):
        try:
            s = nitro_g2d.NSCR(session.get(scr.loc))
            c = nitro_g2d.NCGR(session.get(ch.loc))
        except Exception:
            return False
        return bool(s.entries) and max(e & 0x3FF for e in s.entries) < c.n_tiles

    def cell_fits(cel, ch):
        try:
            ncer = nitro_g2d.NCER(session.get(cel.loc))
            c = nitro_g2d.NCGR(session.get(ch.loc))
        except Exception:
            return False
        need = 0
        for cell in ncer.cells:
            for o in cell['oams']:
                need = max(need, (o['tile'] << ncer.mapping) * 32 + o['w'] * o['h'] * c.bpp // 8)
        return need and need <= len(c.pixels) * c.bpp // 8

    for d, kinds in groups.items():
        chars, pals = kinds.get('NCGR', {}), kinds.get('NCLR', {})
        tables = [p for p in rom.files if str(PurePosixPath(p).parent) == d
                  and (not path_filter or path_filter(p))]
        for tp in tables:
            data = rom.read(tp)
            if not 64 <= len(data) <= 1 << 18 or len(data) % 8 or indexpack.parse(data):
                continue
            rows = [struct.unpack_from('<4H', data, i) for i in range(0, len(data), 8)]
            for kind, col, fits, label in (('NSCR', None, screen_fits, 'bg'), ('NCER', 2, cell_fits, 'cell')):
                maps = kinds.get(kind, {})
                links = []
                tested = 0
                for j, row in enumerate(rows):
                    target = maps.get(j if col is None else row[col])
                    if target is None or row[0] not in chars:
                        continue
                    tested += 1
                    if fits(target, chars[row[0]]):
                        links.append((target, chars[row[0]], pals.get(row[1])))
                if tested < 8 or 2 * len(links) < tested:
                    continue
                stats[f'linked_{label}'] += len(links)
                for target, ch, pal in links:
                    key = json.dumps(target.loc)
                    res = by_map.get(key)
                    new = {'kind': label, 'name': target.path, 'char': ch.loc,
                           'pal': pal.loc if pal else None, 'map': target.loc,
                           'layout': 'link-table', 'owner': ch.path}
                    if res is not None:
                        res.update(new)
                    else:
                        resources.append(new)
                        by_map[key] = new


def _map_need(session, kind, loc, cache):
    """Bytes of NCGR pixel data (8bpp-equivalent tile units for NSCR) a map
    needs: an NCER needs every OAM inside the tileset, an NSCR every tile."""
    key = str(loc)
    if key not in cache:
        try:
            d = session.get(loc)
            if kind == 'cell':
                ncer = nitro_g2d.NCER(d)
                m = ncer.mapping if ncer.mapping < 5 else 0
                vram = ncer.vram_src
                # (tile << mapping) units of 32 bytes + this cell's VRAM
                # transfer offset (bytes), then the OAM's own area
                need = [((o['tile'] << m) * 32 + (vram[ci] if vram and ci < len(vram) else 0),
                         o['w'] * o['h'])
                        for ci, c in enumerate(ncer.cells) for o in c['oams']] or None
            else:
                s = nitro_g2d.NSCR(d)
                need = (max(e & 0x3FF for e in s.entries) + 1) if s.entries else None
        except Exception:
            need = None
        cache[key] = need
    return cache[key]


def _repair_fits(resources, session, stats):
    """A map paired with a tileset too small for it renders chopped (pieces
    past the end of the pixel data come out empty). This happens whenever a
    container does not follow the span rule - Luminous Arc's .iear keeps 17
    NCGRs up front and then 17 NANR/NCER/NCLR triples, so every cell fell on
    the last NCGR. For each map that does NOT fit, look among the tilesets
    of the same container (same file / same directory): the one with the
    same rank (i-th map <-> i-th tileset) if it fits, else the smallest one
    that fits (nearest on ties). Maps that already fit are never touched,
    and nothing that does not fit is ever chosen."""
    import json
    ncgr = {}

    def char_info(loc):
        k = json.dumps(loc)
        if k not in ncgr:
            try:
                c = nitro_g2d.NCGR(session.get(loc))
                ncgr[k] = (len(c.pixels) * c.bpp // 8, c.n_tiles, c.bpp, len(c.pixels), c.w_tiles * 8)
            except Exception:
                ncgr[k] = None
        return ncgr[k]

    NUM = re.compile(r'(\d+)(?=\.bin\b)')

    def group_of(r, loc):
        # cpk: each numbered member is its own top-level file (own crilayla
        # offset in loc), so grouping by loc would put every member alone -
        # group by the archive instead (Valkyrie Profile: NCER/NCGR spread
        # across ~2000 separate 00NNNNNN.bin members of one .cpk)
        if r.get('layout') == 'cpk':
            m = NUM.search(r['name'])
            return r.get('owner', 'cpk'), int(m.group(1)) if m else 0
        if loc and loc[-1][0] == 'slice':
            return json.dumps(loc[:-1]), loc[-1][1]
        return str(PurePosixPath(r['name']).parent), r['name']

    groups = collections.defaultdict(lambda: {'chars': {}, 'maps': []})
    pal_of_char = {}
    for r in resources:
        if r.get('kind') not in ('cell', 'bg', 'atlas') or not r.get('char') or r.get('layout') == 'banner':
            continue
        g, pos = group_of(r, r['char'])
        groups[g]['chars'].setdefault(json.dumps(r['char']), (pos, r['char']))
        if r.get('pal'):
            pal_of_char.setdefault(json.dumps(r['char']), r['pal'])
        if r['kind'] != 'atlas' and r.get('map'):
            groups[g]['maps'].append(r)
    need_cache = {}

    def fits(r, cloc):
        info = char_info(cloc)
        need = _map_need(session, r['kind'], r['map'], need_cache)
        if info is None or need is None:
            return None
        nbytes, ntiles, bpp, npx, width = info
        if r['kind'] == 'bg':
            return need <= ntiles, ntiles
        if max(off + area * bpp // 8 for off, area in need) <= nbytes:
            return True, nbytes
        # 2D tile-position addressing (see resource._cell_grid_width)
        try:
            ncer = nitro_g2d.NCER(session.get(r['map']))
            return bool(width) and npx % width == 0 and C.grid_fits(ncer, npx, width), nbytes
        except Exception:
            return False, nbytes

    moved = set()
    for g, grp in groups.items():
        if len(grp['chars']) < 2:
            continue
        cands = sorted(grp['chars'].values(), key=lambda x: (str(type(x[0])), x[0]))
        for kind in ('cell', 'bg'):
            maps = sorted((r for r in grp['maps'] if r['kind'] == kind),
                          key=lambda r: str(group_of(r, r['map'])[1]).zfill(12))
            for rank, r in enumerate(maps):
                ok = fits(r, r['char'])
                if ok is None or ok[0]:
                    continue
                choice = None
                if len(maps) == len(cands):
                    f = fits(r, cands[rank][1])
                    if f and f[0]:
                        choice = cands[rank][1]
                if choice is None:
                    good = [(f[1], i, c[1]) for i, c in enumerate(cands)
                            for f in [fits(r, c[1])] if f and f[0]]
                    if good:
                        good.sort(key=lambda x: (x[0], abs(x[1] - rank)))
                        choice = good[0][2]
                if choice is None:
                    stats[f'{kind}_does_not_fit'] += 1
                    continue
                r['char'] = choice
                # the palette came from the OLD pairing's group, so prefer the
                # one another resource already uses with this tileset (a
                # tileset and its palette are authored together)
                owner_pal = pal_of_char.get(json.dumps(choice))
                if owner_pal is not None:
                    r['pal'] = owner_pal
                moved.add(json.dumps(choice))
                stats[f'{kind}_refit'] += 1
    if moved:
        used = {json.dumps(r['char']) for r in resources if r.get('kind') in ('cell', 'bg')}
        resources[:] = [r for r in resources
                        if not (r.get('kind') == 'atlas' and json.dumps(r['char']) in moved
                                and json.dumps(r['char']) in used)]


def _repair_palettes(resources, session, stats):
    """A palette whose USED rows are blank cannot be the right one.

    In 4bpp every tile/OAM picks a 16-colour row of the NCLR, and a real
    drawing never uses a row that holds a single colour: that row is either
    an unwritten part of the palette or past its end, and the export comes
    out as one flat silhouette (the 'everything is black' report on Lufia's
    mcd.dat, where a 47 MB carve container makes the positional
    nearest-palette-before rule guess wrong for a handful of cells).

    Only fires when EVERY row the map/cell actually references is blank -
    a resource with one good row and some missing ones is left alone, and so
    is a legitimately dark layer, since that still has distinct colours.
    The replacement must itself be non-blank on those rows; nearest palette
    in the container wins, so a right-but-distant palette never displaces a
    right-and-adjacent one. Purely additive: a resource that renders nothing
    with every candidate keeps what it had."""
    import json
    pal_cache, used_cache = {}, {}

    def colours(loc):
        """Distinct colour count per 16-entry row, or None if unreadable."""
        key = json.dumps(loc)
        if key not in pal_cache:
            try:
                full = nitro_g2d.NCLR(session.get(loc)).palette256()
            except Exception:
                pal_cache[key] = None
            else:
                rows = []
                for row in range(16):
                    # index 0 of a row is hardware-transparent: never drawn,
                    # so it does not make a row 'have a colour'
                    entries = full[row * 16 + 1:row * 16 + 16]
                    rows.append(len(set(entries)) if entries else 0)
                pal_cache[key] = rows
        return pal_cache[key]

    def used_rows(r):
        key = json.dumps(r.get('map')) + r['kind']
        if key not in used_cache:
            try:
                data = session.get(r['map'])
                if r['kind'] == 'bg':
                    rows = {e >> 12 for e in nitro_g2d.NSCR(data).entries}
                else:
                    rows = {o['pal'] for c in nitro_g2d.NCER(data).cells for o in c['oams']}
            except Exception:
                rows = set()
            used_cache[key] = {row for row in rows if 0 <= row < 16}
        return used_cache[key]

    by_owner = collections.defaultdict(list)
    for r in resources:
        if r.get('pal') and r.get('owner'):
            by_owner[r['owner']].append(r['pal'])

    def offset_of(loc):
        for step in reversed(loc or []):
            if step[0] in ('slice', 'lz') and len(step) > 1:
                return step[1]
        return 0

    for r in resources:
        if r.get('kind') not in ('bg', 'cell') or not r.get('pal') or not r.get('map'):
            continue
        rows = used_rows(r)
        current = colours(r['pal'])
        if not rows or current is None:
            continue
        if any(current[row] > 1 for row in rows):
            continue                       # at least one used row has real colours
        here = offset_of(r['pal'])
        best = None
        for cand in by_owner.get(r['owner'], ()):
            if json.dumps(cand) == json.dumps(r['pal']):
                continue
            cols = colours(cand)
            if cols is None or not all(cols[row] > 1 for row in rows):
                continue
            distance = abs(offset_of(cand) - here)
            if best is None or distance < best[0]:
                best = (distance, cand)
        if best is not None:
            r['pal'] = best[1]
            stats['palette_repaired'] += 1


DETECTED_KINDS = ('NANR', 'NMCR', 'NMAR', 'NSBTX', 'NSBMD')


def _count_detected(items, mode, stats):
    """Visibility counters for structures the scan RECOGNISES but does not
    fully render yet (animation blocks, 3D model/texture containers), so the
    GUI can say so instead of the user wondering where they went.

    `_explore` returns [Item] for every mode except 'srl' (a nested ROM),
    where it returns [(inner_mode, [Item])] - one entry per inner file. A
    flat `for item in items: item.kind` therefore crashes on every ROM that
    carries a nested ROM (Soma Bringer, Ivy the Kiwi, The World Ends with
    You), which is why this walks the nested shape explicitly."""
    if mode == 'srl':
        for inner_mode, inner_items in items:
            _count_detected(inner_items, inner_mode, stats)
        return
    for item in items:
        if item.kind in DETECTED_KINDS:
            stats[f'detected_{item.kind.lower()}'] += 1


def scan(rom, session, include_3d=True, progress=None, path_filter=None, include_code=False):
    resources, loose, raws = [], [], []
    stats = collections.Counter()
    CPK_STATS.clear()
    UNRESOLVED.clear()
    from ..formats import banner as bannerfmt
    if (not path_filter or path_filter('__banner__')) and bannerfmt.is_banner(rom.data, rom.banner_offset()):
        resources.append({'kind': 'banner', 'name': '__banner__ (cartridge icon)',
                          'char': [['banner']], 'pal': None, 'map': None, 'layout': 'banner',
                          'owner': '__banner__'})
        stats['banner'] += 1
    files = [(path, [['file', path]]) for _fid, path in rom.all_files()
             if include_code or not path.startswith(('overlay9/', 'overlay7/'))]
    if include_code and hasattr(rom, 'arm_binary'):
        # the executables are NOT FAT files but do carry graphics (a font in
        # Lufia's and Kimi no Yuusha's ARM9, for instance) - read-only, see
        # pipeline.source's 'arm' step
        for which in (9, 7):
            try:
                if rom.arm_binary(which):
                    files.append((f'arm{which}.bin', [['arm', which]]))
            except Exception:
                pass
    for n, (path, file_loc) in enumerate(files):
        if path_filter and not path_filter(path):
            continue
        if progress and n % 200 == 0:
            progress(n, len(files), path)
        data = session.get(file_loc)
        try:
            mode, items = _explore(data, file_loc, path)
        except Exception:                # a broken file must not stop the scan
            stats['error'] += 1
            continue
        if not items:
            continue
        stats[mode] += 1
        _count_detected(items, mode, stats)
        if mode == 'srl':          # nested ROM: each inner file behaves like a top-level one
            for m, its in items:
                if m == 'carve':
                    _register_tables(its, session)
                if m in ('loose', 'ccb', 'cpack'):
                    loose.extend(it for it in its if it.kind in G2D_KINDS)
                    raws.extend(it for it in its if it.kind.startswith('RAW_'))
                elif m == 'raw':
                    raws.extend(its)
                else:
                    _pair_ordered(its, m, resources, its[0].path.split('@')[0])
                for it in its:
                    if _special_resource(it, session, resources, stats):
                        pass
                    elif include_3d and it.kind in ('NSBTX', 'NSBMD'):
                        _texture_resources(it, session, resources)
            continue
        if mode == 'carve':
            _register_tables(items, session)
            names = _sibling_names(rom, path)
            if names:
                from ..containers import name_tree
                found = name_tree.assign([it.kind for it in items], names)
                for i, name in found.items():
                    items[i].path = f'{path}/{name}'
                named = [items[i] for i in found if items[i].kind in G2D_KINDS]
                stats['named_from_tree'] += len(found)
                # a map with no same-name tileset (x/Screen/bg_01.NSCR next to
                # x/Character/area_*.NCGR) keeps the ordered-container pairing
                by_dir = collections.defaultdict(dict)
                for it in named:
                    if it.kind == 'NCGR':
                        by_dir[_pair_dir(it.path)][_stem(it.path).lower()] = it
                orphans = {id(it) for it in named if it.kind in ('NSCR', 'NCER')
                           and _stem(it.path).lower() not in by_dir[_pair_dir(it.path)]
                           and _similar_palette(_stem(it.path).lower(), by_dir[_pair_dir(it.path)]) is None}
                loose.extend(it for it in named if id(it) not in orphans)
                taken = {id(it) for it in named if id(it) not in orphans}
                ordered = []
                _pair_ordered([it for it in items if id(it) not in taken or it.kind in ('NCGR', 'NCLR')],
                              mode, ordered, path)
                orphan_locs = {str(it.loc) for it in items if id(it) in orphans}
                orphan_locs |= {str(it.loc) for it in items if id(it) not in taken and it.kind in ('NSCR', 'NCER')}
                resources.extend(r for r in ordered if r['map'] is not None and str(r['map']) in orphan_locs)
                for it in items:
                    if _special_resource(it, session, resources, stats):
                        pass
                    elif include_3d and it.kind in ('NSBTX', 'NSBMD'):
                        _texture_resources(it, session, resources)
                continue
        if mode in ('loose', 'ccb', 'cpack'):
            loose.extend(it for it in items if it.kind in G2D_KINDS)
            # a named archive (ZIP, WFC utility.bin...) can hold headerless members too
            raws.extend(it for it in items if it.kind.startswith('RAW_'))
        elif mode == 'raw':
            raws.extend(items)
        elif mode == 'ntrp':
            # one bundle can hold both families: standard Nitro members pair
            # by the ordered-container rule, headerless "lite" ones by the
            # raw rule (see containers.ntrp)
            _pair_ordered([it for it in items if it.kind in G2D_KINDS], mode, resources, path)
            raws.extend(it for it in items if it.kind.startswith('RAW_'))
        else:
            _pair_ordered(items, mode, resources, path)
        for it in items:
            if _special_resource(it, session, resources, stats):
                pass
            elif include_3d and it.kind in ('NSBTX', 'NSBMD'):
                _texture_resources(it, session, resources)
    _pair_loose(loose, resources)
    _link_index_tables(rom, session, loose, resources, stats, path_filter)
    _pair_raw(raws, resources, session, stats)
    _repair_fits(resources, session, stats)
    _repair_palettes(resources, session, stats)
    for i, r in enumerate(resources):
        r['id'] = i
    if CPK_STATS:
        stats['cpk_files_crilayla'] = CPK_STATS['crilayla']
        stats['cpk_files_unknown_codec'] = CPK_STATS['unknown_codec']
        if CPK_STATS['broken']:
            stats['cpk_files_broken'] = CPK_STATS['broken']
    return resources, stats

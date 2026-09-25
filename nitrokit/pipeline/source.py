"""Layered locators and a batched edit session.

A locator is a tuple of steps, outermost first, JSON-serialisable:
  ['file', 'data/2d/logo.NCGR_']        FAT file of the ROM
  ['codec', 'lz10']                     payload after decompression
  ['narc', 3]                           member #3 of a NARC
  ['srl', 'data/x.bin']                 file inside a nested NDS ROM image
  ['crilayla', off, packed_size, extract_size]   a CRI CRILAYLA-compressed
                                         CPK entry (only the real, tagged
                                         codec - see nitrokit.containers.cpk)
  ['arm', 9] / ['arm', 7]                the ARM9/ARM7 executable image -
                                         also NOT a FAT file (header-pointed
                                         region); READ-ONLY for now, so
                                         graphics found in code are visible
                                         and dumpable but not insertable
  ['banner']                             the cartridge icon/title block -
                                         NOT a FAT file, lives in the
                                         untracked gap the header points at
                                         (0x68) - special-cased on commit
  ['oarc', i]                            entry #i of a magic-less offset-table archive
                                         (containers.offset_archive; may grow)
  ['ccb', i]                             entry #i of a CyberConnect2 CCB archive
  ['cpack', i]                           entry #i of a Cing .pack (zlib or raw)
  ['zip', i]                             file #i of a PKZIP archive
  ['ipack', i]                           slot #i of an index-table pack
  ['ntrp', i]                            member #i of a Ragnarok DS NTRP bundle
  ['nfs', i]                             file #i of a mini NitroFS (dwc/utility.bin; may grow)
  ['ptab', i]                            entry #i of a count + (offset, size) table (may grow)
  ['mtab', i]                            entry #i of an implicit-count offset table
                                         (decompressed; may grow on insert)
  ['slice', off, size]                  raw bytes carved out of a blob
  ['lz', off, codec, consumed]          LZ10/LZ11/RLE stream carved out of a blob
  ['tex', i]  (resources only)          texture i of a TEX0 - not a step

`Session.get(loc)` resolves (cached). `Session.put(loc, data)` records an
edit; `Session.commit()` propagates every edit upward, deepest first,
merging edits that share a parent, and returns {rom_path: bytes}.

Write-back rules per step (the only lossy/limited parts of the pipeline):
  codec : recompress with the SAME codec (AT6P keeps the original header tag);
          'blz' (compressed overlays) recompresses with a real BLZ
          encoder; the overlay TABLE's cached size is patched too, via
          Session.overlay_comp_sizes -> NDSRom.rebuild(overlay_comp_sizes=...)
  narc  : repack, keeping BTNF and padding byte
  slice : same size required (containers with an unknown index can't grow)
  lz    : recompressed stream must fit in the ORIGINAL consumed length;
          the remainder is padded with 0 (the index still points at the old
          start and the decompressor stops at the declared size)
"""
import struct
from .. import compression
from ..compression import lz10
from ..containers import (narc, ntrp, cpk, ccb, offset_archive, cingpack, zipfs, indexpack, member_table,
                          nitrofs, pair_table)


def lz_consumed(data, off, codec):
    """Compressed bytes used by the stream starting at `off`."""
    if codec == 'lz10':
        return lz10.compressed_length(data[off:])
    if codec == 'rle':
        size = data[off + 1] | data[off + 2] << 8 | data[off + 3] << 16
        i, produced = off + 4, 0
        while produced < size:
            f = data[i]
            if f & 0x80:
                produced += (f & 0x7F) + 3
                i += 2
            else:
                produced += (f & 0x7F) + 1
                i += (f & 0x7F) + 2
        return i - off
    size = data[off + 1] | data[off + 2] << 8 | data[off + 3] << 16
    i, produced = off + 4, 0
    while produced < size:
        flags = data[i]
        i += 1
        for _ in range(8):
            if produced >= size:
                break
            if flags & 0x80:
                hi = data[i] >> 4
                if hi == 0:
                    produced += ((data[i] << 4) | (data[i + 1] >> 4)) + 0x11
                    i += 3
                elif hi == 1:
                    produced += (((data[i] & 0xF) << 12) | (data[i + 1] << 4) | (data[i + 2] >> 4)) + 0x111
                    i += 4
                else:
                    produced += hi + 1
                    i += 2
            else:
                produced += 1
                i += 1
            flags = flags << 1 & 0xFF
    return i - off


class EditError(Exception):
    pass


class Session:
    def __init__(self, rom):
        self.rom = rom
        self._cache = {}
        self._edits = {}
        self._narc = {}
        self.tables = {}        # json(parent loc) -> offset table (containers.offset_table)
        self.banner_patch = None  # set by commit() if a ['banner'] edit was queued
        self.overlay_comp_sizes = {}  # set by commit(): {file_id: new BLZ size} for NDSRom.rebuild()

    # ------------------------------------------------------------ resolve
    def get(self, loc):
        loc = tuple(tuple(s) for s in loc)
        if loc in self._edits:
            return self._edits[loc]
        if loc in self._cache:
            return self._cache[loc]
        *parent, step = loc
        kind = step[0]
        if kind == 'file':
            data = self.rom.read(step[1]) if isinstance(step[1], str) else self.rom.read_fid(step[1])
        elif kind == 'arm' and not parent:
            data = self.rom.arm_binary(step[1])
        elif kind == 'banner' and not parent:
            from ..formats import banner as bannerfmt
            off = self.rom.banner_offset()
            size = bannerfmt.total_size(self.rom.data, off)
            data = self.rom.data[off:off + size]
        elif kind == 'banner':
            from ..formats import banner as bannerfmt
            p = self.get(parent)
            off = struct.unpack_from('<I', p, 0x68)[0]
            data = p[off:off + bannerfmt.total_size(p, off)]
        else:
            p = self.get(parent)
            if kind == 'codec' and step[1] == 'blz':
                from ..compression import blz
                data = blz.decompress(p)
            elif kind == 'codec':
                data, codec = compression.try_decompress(p)
                if codec != step[1]:
                    raise EditError(f'codec mismatch at {loc}')
            elif kind == 'narc':
                data = self._narc_files(tuple(parent), p)[step[1]]
            elif kind == 'slice':
                data = p[step[1]:step[1] + step[2]]
            elif kind == 'oarc':
                entries = offset_archive.parse(p)
                if entries is None or step[1] >= len(entries):
                    raise EditError(f'not an offset archive at {loc}')
                o, size = entries[step[1]]
                data = p[o:o + size]
            elif kind == 'zip':
                data = zipfs.read(p, step[1])
            elif kind == 'ipack':
                data = indexpack.read(p, step[1])
            elif kind == 'ntrp':
                try:
                    data = ntrp.read(p, step[1])
                except ValueError as e:
                    raise EditError(f'{loc}: {e}')
            elif kind == 'nfs':
                entries = nitrofs.parse(p)
                if entries is None or step[1] >= len(entries):
                    raise EditError(f'not a mini NitroFS at {loc}')
                _, o, sz = entries[step[1]]
                data = p[o:o + sz]
            elif kind == 'ptab':
                entries = pair_table.parse(p)
                if entries is None or step[1] >= len(entries):
                    raise EditError(f'not a pair-table archive at {loc}')
                o, sz = entries[step[1]]
                data = p[o:o + sz]
            elif kind == 'mtab':
                entries = member_table.parse(p)
                if entries is None or step[1] >= len(entries):
                    raise EditError(f'not a member_table archive at {loc}')
                o, sz = entries[step[1]]
                data = p[o:o + sz]
            elif kind == 'cpack':
                entries = cingpack.parse(p)
                if entries is None or step[1] >= len(entries):
                    raise EditError(f'not a Cing pack at {loc}')
                data = cingpack.read(p, entries[step[1]])
            elif kind == 'ccb':
                try:
                    data = ccb.read(p, ccb.parse(p)[step[1]])
                except (ValueError, IndexError) as e:
                    raise EditError(f'cannot read CCB entry at {loc}: {e}')
            elif kind == 'srl':
                data = self._nested_rom(tuple(parent), p).read(step[1])
            elif kind == 'crilayla':
                blob = p[step[1]:step[1] + step[2]]
                try:
                    data = cpk.crilayla_decompress(blob)
                except (ValueError, IndexError) as e:
                    raise EditError(f'cannot decompress CRILAYLA at {loc}: {e}')
                if len(data) != step[3]:
                    raise EditError(f'{loc}: CRILAYLA decompressed to {len(data)} B, '
                                    f'expected {step[3]} B (extract size from the CPK table)')
            elif kind == 'lz':
                # the scan already validated this stream: decode by codec name
                # (try_decompress's ratio sanity check rejects near-empty maps)
                blob = p[step[1]:step[1] + step[3]]
                try:
                    data = compression.decompress_as(blob, step[2])
                except (ValueError, IndexError) as e:
                    raise EditError(f'cannot decompress {loc}: {e}')
            else:
                raise EditError(f'unknown step {step}')
        if len(self._cache) > 4096:
            self._cache.clear()
            self._narc.clear()
        self._cache[loc] = data
        return data

    def _nested_rom(self, key, data):
        from ..rom.ndsrom import NDSRom
        k = ('srl',) + key
        if k not in self._narc:
            self._narc[k] = NDSRom(data)
        return self._narc[k]

    def _narc_files(self, key, data):
        if key not in self._narc:
            self._narc[key] = narc.unpack(data)
        return self._narc[key]

    # ------------------------------------------------------------ edit
    def put(self, loc, data):
        loc = tuple(tuple(s) for s in loc)
        if any(step[0] == 'arm' for step in loc):
            raise EditError('graphics inside the ARM9/ARM7 executable are preview/dump only: '
                            'writing to the code region is not implemented yet (it is not a FAT '
                            'file, so it needs its own same-size patch path like the banner)')
        self._edits[loc] = bytes(data)

    def commit(self):
        """Propagate edits to FAT files. Returns {rom_path: bytes}.
        A ['banner'] edit is NOT a FAT file - it is stashed on
        `self.banner_patch` as (offset, blob) instead, for the caller to
        pass through to NDSRom.rebuild(files, banner_patch=...)."""
        edits = dict(self._edits)
        self.banner_patch = None
        for k in list(edits):
            if k[-1][0] == 'banner' and len(k) == 1:
                off = self.rom.banner_offset()
                self.banner_patch = (off, edits.pop(k))
        out = {}
        while edits:
            depth = max(len(k) for k in edits)
            level = {k: v for k, v in edits.items() if len(k) == depth}
            for k in level:
                del edits[k]
            if depth == 1:
                for k, v in level.items():
                    out[k[0][1]] = v
                continue
            groups = {}
            for k, v in level.items():
                groups.setdefault(k[:-1], []).append((k[-1], v))
            for parent, changes in groups.items():
                base = edits.get(parent)
                if base is None:
                    base = self._original(parent)
                edits[parent] = self._apply(parent, base, changes)
        self._edits.clear()
        return out

    def _original(self, loc):
        saved = self._edits
        self._edits = {}
        try:
            return self.get(loc)
        finally:
            self._edits = saved

    def _apply(self, parent, base, changes):
        kinds = {c[0][0] for c in changes}
        if kinds == {'codec'} and changes[0][0][1] == 'blz':
            (step, v), = changes
            from ..compression import blz
            try:
                comp = blz.compress(v)
            except ValueError as exc:
                raise EditError(f'{parent}: {exc}') from exc
            if len(parent) == 1 and parent[0][0] == 'file':
                fid = parent[0][1]
                fid = fid if isinstance(fid, int) else self.rom.fid_of(fid)
                self.overlay_comp_sizes[fid] = len(comp)
            return comp
        if kinds == {'codec'}:
            (step, v), = changes
            return compression.rewrap(v, step[1], original=base)
        if kinds <= {'banner', 'srl'}:
            from ..rom.ndsrom import NDSRom
            files = {step[1]: v for step, v in changes if step[0] == 'srl'}
            banner = next((v for step, v in changes if step[0] == 'banner'), None)
            new = NDSRom(base).rebuild(files) if files else bytes(base)
            if banner is not None:
                off = struct.unpack_from('<I', new, 0x68)[0]
                new = new[:off] + banner + new[off + len(banner):]
            return new
        if kinds == {'oarc'}:
            return offset_archive.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'ipack'}:
            return indexpack.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'ntrp'}:
            return ntrp.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'nfs'}:
            return nitrofs.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'ptab'}:
            return pair_table.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'mtab'}:
            return member_table.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'zip'}:
            return zipfs.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'cpack'}:
            return cingpack.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'ccb'}:
            return ccb.pack(base, {step[1]: v for step, v in changes})
        if kinds == {'narc'}:
            files = list(narc.unpack(base))
            for step, v in changes:
                files[step[1]] = v
            return narc.pack(files, original=base)
        import json
        from ..containers import offset_table
        table = self.tables.get(json.dumps([list(x) for x in parent]))
        new = bytes(base)
        # highest offset first: growing an item never moves the ones before it
        for step, v in sorted(changes, key=lambda c: -c[0][1]):
            if step[0] == 'slice':
                if len(v) != step[2]:
                    raise EditError(f'{parent}: carved file size changed ({step[2]} -> {len(v)}); '
                                    'unknown container index cannot be updated')
                new = new[:step[1]] + v + new[step[1] + step[2]:]
            elif step[0] == 'lz':
                comp = compression.rewrap(v, step[2], pad=False)
                tail = new[step[1] + step[3]:]
                if len(parent) == 1 and step[1] == 0 and not tail.strip(bytes([0, 255])):
                    # the stream IS the whole FAT file (plus padding): it may grow
                    return comp + bytes((-len(comp)) % 4)
                if len(comp) <= step[3]:
                    new = new[:step[1]] + comp + bytes(step[3] - len(comp)) + new[step[1] + step[3]:]
                elif table and str(step[1]) in table['size_fields']:
                    new, delta = offset_table.grow(new, table, step[1], step[3], comp)
                else:
                    raise EditError(f'{parent}@{step[1]:#x}: recompressed {len(comp)} B > original '
                                    f'{step[3]} B slot and no offset table was detected for this '
                                    'container. Simplify the edit or add a container writer.')
            elif step[0] == 'crilayla':
                orig_blob = new[step[1]:step[1] + step[2]]
                comp = cpk.crilayla_compress(v, magic=orig_blob[:8])
                if len(comp) <= step[2]:
                    comp = cpk.crilayla_compress(v, magic=orig_blob[:8], target_len=step[2])
                    new = new[:step[1]] + comp + new[step[1] + step[2]:]
                else:
                    # use the alignment slack after the file (the next file does
                    # not move) and patch its FileSize in the table row
                    grown = cpk.grow_entry(new, step[1], len(comp)) if cpk.is_cpk(new) else None
                    if grown is None:
                        raise EditError(f'{parent}@{step[1]:#x}: CRILAYLA {len(comp)} B does not fit the '
                                        f'{step[2]} B entry nor the gap before the next file; '
                                        'simplify the edit.')
                    new = grown[:step[1]] + comp + grown[step[1] + len(comp):]
            else:
                raise EditError(f'cannot mix step kinds {kinds}')
        return new


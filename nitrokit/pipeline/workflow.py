"""Shared, fail-closed dump/insert workflow for the CLI and GUI.

No output ROM is published until the entire batch and FAT payloads validate.
This is a structural check, not a substitute for testing the game itself.
"""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import shutil
import struct

import numpy as np
from PIL import Image

from . import resource as R
from .source import Session, EditError
from ..formats import banner as bannerfmt
from ..rom.ndsrom import NDSRom, crc16_modbus, HEADER_FIELDS


def png_name(res):
    base = re.sub(r'[<>:"\\|?*]', '_', res['name'])
    base = base.replace('@', '_at_')
    extra = f"_t{res['tex']}" if res['kind'] == 'tex' else ''
    return f"{res['kind']}/{base}{extra}_{res['id']:05d}.png"


def root_png_name(res):
    """Dump path that mirrors the ROM's own folder tree: every graphic lands
    under the folder of the ROM file it came from (Dumpar Root)."""
    loc = res.get('char') or []
    base = loc[0][1] if loc and loc[0][0] == 'file' and isinstance(loc[0][1], str) else ''
    name = res['name'].replace(chr(92), '/')     # backslash -> slash
    rel = name if not base or name.startswith(base) else f'{base}/{name}'
    rel = re.sub(r'[<>:"|?*]', '_', rel).replace('@', '_at_').strip('/')
    if rel.startswith('__banner__'):
        rel = '_banner/icone'
    extra = f"_t{res['tex']}" if res['kind'] == 'tex' else ''
    return f"{rel}{extra}__{res['kind']}_{res['id']:05d}.png"


def pixel_hash(im):
    arr = np.array(im)
    return hashlib.md5(arr.tobytes() + str(arr.shape).encode()).hexdigest()


def image_metadata(im):
    """Canonical palette/alpha table, independent of PNG chunk encoding."""
    table = None
    if im.mode == 'P':
        ramp = Image.fromarray(np.arange(256, dtype=np.uint8).reshape(16, 16))
        ramp.putpalette(im.getpalette())
        if 'transparency' in im.info:
            ramp.info['transparency'] = im.info['transparency']
        table = hashlib.sha256(ramp.convert('RGBA').tobytes()).hexdigest()
    return {'mode': im.mode, 'size': list(im.size), 'palette_rgba_sha256': table}


def source_identity(source):
    if hasattr(source, 'identity'):
        return source.identity()
    return {'kind': 'rom', 'path': str(source.path.resolve()) if source.path else None,
            'sha256': hashlib.sha256(source.data).hexdigest(), 'size': len(source.data),
            'code': source.code}


def make_manifest(rom, session, resources):
    identity = source_identity(rom)
    return {'schema_version': 3, 'source': identity,
            # Kept for readable tooling and migration diagnostics.
            'rom': identity.get('path') if identity['kind'] == 'rom' else None,
            'rom_sha256': identity.get('sha256') if identity['kind'] == 'rom' else None,
            'rom_size': identity.get('size') if identity['kind'] == 'rom' else None,
            'code': identity.get('code', getattr(rom, 'code', None)),
            'tables': session.tables, 'resources': resources}


def validate_identity(rom, manifest):
    if manifest.get('schema_version') not in (2, 3):
        raise EditError('Legacy/unsupported manifest: create a fresh dump from the original ROM; '
                        'keep your edited PNGs separately.')
    if manifest.get('schema_version') == 2:
        if not manifest.get('rom_sha256') or not isinstance(rom, NDSRom):
            raise EditError('Manifest source type does not match the selected input.')
        expected = {'kind': 'rom', 'sha256': manifest['rom_sha256'],
                    'size': manifest.get('rom_size'), 'code': manifest.get('code')}
    else:
        expected = manifest.get('source') or {}
    actual = source_identity(rom)
    fields = ('kind', 'sha256') + (('size', 'code') if actual['kind'] == 'rom' else ())
    if any(expected.get(field) != actual.get(field) for field in fields):
        raise EditError('Input does not match the dump (source type/SHA-256/size/game code).')


def dump_resources(rom, session, resources, folder, name_for, progress=None):
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise EditError('Dump folder must be empty; existing edits will not be overwritten.')
    folder.mkdir(parents=True, exist_ok=True)
    records = copy.deepcopy(resources)
    errors = []
    for n, r in enumerate(records):
        if progress:
            progress(n, len(records))
        for key in ('error', 'png', 'hash', 'image', 'info'):
            r.pop(key, None)
        try:
            im, info = R.to_png(r, session)
            p = resource_path(folder, name_for(r))
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p, optimize=False)
            r.update(png=p.relative_to(folder.resolve()).as_posix(), hash=pixel_hash(im),
                     image=image_metadata(im), info={k: v for k, v in info.items() if k != 'size'})
        except Exception as exc:
            r['error'] = f'{type(exc).__name__}: {exc}'
            errors.append(f"{r['name']}: {r['error']}")
    manifest = make_manifest(rom, session, records)
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding='utf-8')
    return {'dumped': sum('png' in r for r in records), 'errors': errors}


def resource_path(folder, relative):
    root = Path(folder).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise EditError(f'PNG path escapes dump folder: {relative}')
    return path


def publish_new(path, data):
    """Publish a complete file without replacing an existing destination.

    The temporary file and destination share a filesystem. Hard-link creation
    is atomic and fails if another process creates the destination first.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.nitrogfx-', delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def publish_loose(source, destination, replacements):
    """Publish a loose file or folder as a new output without touching input."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise EditError('Output already exists; choose a new destination.')
    if source.kind == 'file':
        name = next(iter(source.entries))
        publish_new(destination, replacements.get(name, source.entries[name]))
        return
    stage = Path(tempfile.mkdtemp(prefix='.nitrogfx-', dir=destination.parent))
    try:
        for name, data in source.iter_entries():
            out = resource_path(stage, name)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(replacements.get(name, data))
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_candidate(rom, data, replacements, banner_patch, overlay_comp_sizes=None, header_patch=None):
    rebuilt = NDSRom(data)
    if len(data) != len(rom.data):
        raise EditError('ROM capacity changed; expansion beyond the original size is not validated.')
    if rebuilt.n_files != rom.n_files or rebuilt.files != rom.files:
        raise EditError('Filesystem identity changed during rebuild.')
    by_id = {k if isinstance(k, int) else rom.fid_of(k): v for k, v in replacements.items()}
    for fid, (start, end) in enumerate(rebuilt.fat):
        if not 0 <= start <= end <= len(data):
            raise EditError(f'Invalid FAT bounds for file {fid}')
        if rebuilt.read_fid(fid) != by_id.get(fid, rom.read_fid(fid)):
            raise EditError(f'Rebuilt FAT payload differs for file {fid}')
    # The prefix must differ only in the FAT, used-size/header CRC, banner,
    # the size field of re-compressed overlays and the requested header fields.
    fat_end = rom.fat_off + rom.fat_size
    prefix_end = min((s for s, e in rom.fat if s >= fat_end and e > s), default=len(rom.data))
    expected = bytearray(rom.data[:prefix_end])
    allowed = [(rom.fat_off, fat_end), (0x80, 0x84), (0x15E, 0x160)]
    for key, value in (header_patch or {}).items():
        if value is not None:
            off, size = HEADER_FIELDS[key]
            allowed.append((off, off + size))
    for table_off, ovs in ((rom.ov9_off, rom.overlays9), (rom.ov7_off, rom.overlays7)):
        for i, ov in enumerate(ovs):
            if ov['file_id'] in (overlay_comp_sizes or {}):
                allowed.append((table_off + i * 32 + 28, table_off + i * 32 + 32))
    for start, end in allowed:
        expected[start:end] = data[start:end]
    if banner_patch:
        off, blob = banner_patch
        expected[off:off + len(blob)] = blob
    if bytes(expected) != data[:prefix_end]:
        raise EditError('Unexpected change outside FAT payloads/header fields/banner.')
    if int.from_bytes(data[0x15E:0x160], 'little') != crc16_modbus(data[:0x15E]):
        raise EditError('Invalid rebuilt header CRC.')


def find_manifest(png_path):
    """Nearest manifest.json at or above a PNG's folder, or None."""
    for folder in Path(png_path).resolve().parents:
        candidate = folder / 'manifest.json'
        if candidate.exists():
            return candidate
    return None


def match_pngs(png_paths, manifests):
    """Match edited PNGs to dump records BY FILE NAME (not by folder).

    `manifests`: [(dump_folder, manifest_dict)]. A PNG inside a dump folder
    is matched against that dump first (exact relative path); a PNG copied
    elsewhere is matched by its file name, which is unique by construction
    (every dumped name ends in the resource id). -> (pairs, unmatched)"""
    by_name = {}
    for folder, manifest in manifests:
        for r in manifest.get('resources', []):
            if 'png' in r:
                by_name.setdefault(Path(r['png']).name.lower(), []).append((folder, r))
    pairs, unmatched = [], []
    for png in png_paths:
        png = Path(png)
        hits = by_name.get(png.name.lower(), [])
        if len(hits) > 1:            # same name in two dumps: prefer the one it lives in
            inside = [h for h in hits if Path(h[0]).resolve() in png.resolve().parents]
            hits = inside or hits[:1]
        if hits:
            pairs.append((hits[0][1], png))
        else:
            unmatched.append(str(png))
    return pairs, unmatched


def validate_rom_info(info):
    """{'title', 'code', 'banner_title'} as typed by the user -> the same
    dict with only real changes kept; raises EditError on invalid values."""
    out = {}
    title, code, banner = info.get('title'), info.get('code'), info.get('banner_title')
    if title is not None:
        if len(title) > 12 or not title.isascii() or not title.isprintable():
            raise EditError('Título interno: até 12 caracteres ASCII (sem acentos).')
        out['title'] = title
    if code is not None:
        if len(code) != 4 or not code.isascii() or not code.isalnum():
            raise EditError('Código do jogo: exatamente 4 letras/números ASCII (ex.: AYWE).')
        out['code'] = code.upper()
    if banner is not None:
        if len(banner) > 127 or banner.count('\n') > 2:
            raise EditError('Título do banner: até 3 linhas e 127 caracteres.')
        out['banner_title'] = banner
    return out


def insert_staged(rom, pairs, destination, tables=None, progress=None, rom_info=None):
    """Insert only the given (record, png_path) pairs - records may come
    from different dumps (normal + GpxOAM), each carrying its own layout.
    `rom_info`: optional {'title', 'code', 'banner_title'} changes (see
    validate_rom_info). Same fail-closed checks as insert_dump."""
    destination = Path(destination).resolve()
    if rom.path and destination == Path(rom.path).resolve():
        raise EditError('Output must differ from the original ROM.')
    if destination.exists():
        raise EditError('Output already exists; choose a new destination.')
    rom_info = validate_rom_info(rom_info or {})
    if not pairs and not rom_info:
        raise EditError('No edited PNG or ROM information change to insert.')
    session = Session(rom)
    session.tables = copy.deepcopy(tables or {})
    changed = _apply_pairs(rom, session, [(copy.deepcopy(r), Path(p)) for r, p in pairs], progress)
    return _publish(rom, session, destination, changed, rom_info)


def _apply_pairs(rom, session, pairs, progress=None):
    changed = []
    for n, (r, path) in enumerate(pairs):
        if progress:
            progress(n, len(pairs))
        if r.get('error'):
            raise EditError(f"Dump contains an extraction error: {r['name']}: {r['error']}")
        with Image.open(path) as opened:
            im = opened.copy()
        if image_metadata(im) != r.get('image'):
            raise EditError(f"{Path(path).name}: mode, dimensions, palette or transparency changed.")
        if pixel_hash(im) == r['hash']:
            continue
        if not r.get('info', {}).get('editable', False):
            raise EditError(f"{Path(path).name}: preview-only resource cannot be inserted.")
        baseline, _ = R.to_png(r, Session(rom))
        if pixel_hash(baseline) != r['hash'] or image_metadata(baseline) != r['image']:
            raise EditError(f"{Path(path).name}: manifest layout no longer matches the original dump.")
        conflicts = R.from_png(r, session, im)
        if conflicts:
            raise EditError(f"{Path(path).name}: {conflicts} conflicting shared pixels; batch aborted.")
        changed.append((r, im, Path(path).name))
    for r, requested, name in changed:
        actual, _ = R.to_png(r, session)
        if pixel_hash(actual) != pixel_hash(requested):
            raise EditError(f"{name}: requested pixels cannot be represented by this layout/shared data.")
    return changed


def _publish(rom, session, destination, changed, rom_info=None):
    files = session.commit()
    rom_info = rom_info or {}
    if isinstance(rom, NDSRom):
        banner_patch = session.banner_patch
        if rom_info.get('banner_title') is not None:
            off = rom.banner_offset()
            base = banner_patch[1] if banner_patch else rom.data[off:off + bannerfmt.total_size(rom.data, off)]
            banner_patch = (off, bannerfmt.Banner(base).with_title(rom_info['banner_title']))
        header = {k: rom_info.get(k) for k in HEADER_FIELDS}
        data = rom.rebuild(files, banner_patch=banner_patch,
                           overlay_comp_sizes=session.overlay_comp_sizes, header_patch=header)
        verify_candidate(rom, data, files, banner_patch, session.overlay_comp_sizes, header)
        publish_new(destination, data)
        output_hash = hashlib.sha256(data).hexdigest()
    else:
        if session.banner_patch or rom_info:
            raise EditError('Loose inputs have no cartridge header/banner to change.')
        publish_loose(rom, destination, files)
        output_hash = source_identity(type(rom).open(destination))['sha256']
    return {'changed': len(changed), 'files': len(files),
            'rom_sha256': output_hash, 'output': str(destination)}


def export_files(rom, pairs, destination, tables=None, progress=None):
    """Insert the edited PNGs, but publish ONLY the rewritten game files
    (e.g. data/menu.narc, a .bin, an .NCGR) into a new folder, keeping the
    ROM's internal paths - for people working on an extracted root
    (ndstool/DSLazy/Tinke) instead of a whole .nds. Out-of-FAT edits use
    ndstool's names: banner.bin, y9.bin/y7.bin (overlay tables)."""
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise EditError('A pasta de saída precisa ser nova ou vazia.')
    if not pairs:
        raise EditError('Nenhum PNG editado para exportar.')
    session = Session(rom)
    session.tables = copy.deepcopy(tables or {})
    changed = _apply_pairs(rom, session, [(copy.deepcopy(r), Path(p)) for r, p in pairs], progress)
    files = session.commit()
    out = {}
    for key, data in files.items():
        name = rom.names[key] if isinstance(key, int) else str(key)
        out[name] = bytes(data)
    if session.banner_patch:
        out['banner.bin'] = bytes(session.banner_patch[1])
    if session.overlay_comp_sizes and isinstance(rom, NDSRom):
        for fname, table_off, size, ovs in (('y9.bin', rom.ov9_off, rom.ov9_size, rom.overlays9),
                                            ('y7.bin', rom.ov7_off, rom.ov7_size, rom.overlays7)):
            table = bytearray(rom.data[table_off:table_off + size])
            hit = False
            for i, ov in enumerate(ovs):
                new_size = session.overlay_comp_sizes.get(ov['file_id'])
                if new_size is not None:
                    struct.pack_into('<I', table, i * 32 + 28, new_size | 1 << 24)
                    hit = True
            if hit:
                out[fname] = bytes(table)
    if not out:
        raise EditError('As edições não mudaram nenhum arquivo.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.nitrogfx-', dir=destination.parent))
    try:
        for name, data in out.items():
            path = resource_path(stage, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if destination.exists():
            destination.rmdir()
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'changed': len(changed), 'files': sorted(out), 'output': str(destination)}


def insert_dump(rom, folder, destination, progress=None):
    """Insert a whole dump folder (every PNG listed in its manifest)."""
    folder, destination = Path(folder), Path(destination).resolve()
    if rom.path and destination == Path(rom.path).resolve():
        raise EditError('Output must differ from the original ROM.')
    if destination.exists():
        raise EditError('Output already exists; choose a new destination.')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    validate_identity(rom, manifest)
    session = Session(rom)
    session.tables = copy.deepcopy(manifest.get('tables', {}))
    items = manifest['resources']
    if not items:
        raise EditError('Manifest contains no resources.')
    pairs, seen = [], set()
    for r in items:
        if 'png' not in r:
            raise EditError(f"Resource has no PNG: {r.get('name')}")
        path = resource_path(folder, r['png'])
        if path in seen:
            raise EditError(f'Duplicate PNG path: {path}')
        seen.add(path)
        pairs.append((r, path))
    changed = _apply_pairs(rom, session, pairs, progress)
    return _publish(rom, session, destination, changed)

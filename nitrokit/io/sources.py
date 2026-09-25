"""NDS-like adapters for folders and isolated binary files.

The scanner only requires ``data``, ``banner_offset()``, ``all_files()`` and
``read()``. Keeping that small protocol lets the same discovery pipeline work
without pretending a loose collection is a cartridge image.
"""
from hashlib import sha256
from pathlib import Path

from ..rom.ndsrom import NDSRom


class LooseSource:
    kind = 'folder'
    code = 'LOOSE'
    data = b''

    def __init__(self, path, entries):
        self.path = Path(path).resolve()
        self.entries = dict(entries)
        self.title = self.path.name
        self.files = {name: i for i, name in enumerate(self.entries)}
        self.names = {i: name for name, i in self.files.items()}

    def banner_offset(self):
        return 0

    def all_files(self):
        yield from enumerate(self.entries)

    def read(self, name):
        return self.entries[str(name)]

    def read_fid(self, fid):
        return self.entries[self.names[fid]]

    def iter_entries(self):
        yield from self.entries.items()

    def arm9(self):
        for name in self.entries:
            if Path(name).name.lower() == 'arm9.bin':
                return self.read(name)
        return b''

    def identity(self):
        digest = sha256()
        files = []
        for name in sorted(self.entries):
            data = self.entries[name]
            item_hash = sha256(data).hexdigest()
            digest.update(name.encode('utf-8'))
            digest.update(b'\0')
            digest.update(len(data).to_bytes(8, 'little'))
            digest.update(bytes.fromhex(item_hash))
            files.append({'path': name, 'size': len(data), 'sha256': item_hash})
        return {'kind': self.kind, 'path': str(self.path), 'sha256': digest.hexdigest(),
                'files': files}


class FolderSource(LooseSource):
    kind = 'folder'

    @classmethod
    def open(cls, path):
        root = Path(path).resolve()
        paths = {file.relative_to(root).as_posix(): file
                 for file in sorted(p for p in root.rglob('*') if p.is_file())}
        source = cls(root, {name: None for name in paths})
        source._paths = paths
        return source

    def read(self, name):
        return self._paths[str(name)].read_bytes()

    def read_fid(self, fid):
        return self.read(self.names[fid])

    def iter_entries(self):
        for name in self.entries:
            yield name, self.read(name)

    def identity(self):
        digest = sha256()
        files = []
        for name, data in self.iter_entries():
            item_hash = sha256(data).hexdigest()
            digest.update(name.encode('utf-8'))
            digest.update(b'\0')
            digest.update(len(data).to_bytes(8, 'little'))
            digest.update(bytes.fromhex(item_hash))
            files.append({'path': name, 'size': len(data), 'sha256': item_hash})
        return {'kind': self.kind, 'path': str(self.path), 'sha256': digest.hexdigest(), 'files': files}


class SingleFileSource(LooseSource):
    kind = 'file'

    @classmethod
    def open(cls, path):
        path = Path(path).resolve()
        return cls(path, {path.name: path.read_bytes()})


def open_source(path, kind=None):
    path = Path(path)
    if kind == 'rom' or (kind is None and path.is_file() and path.suffix.lower() == '.nds'):
        return NDSRom.open(path)
    if kind == 'folder' or path.is_dir():
        return FolderSource.open(path)
    return SingleFileSource.open(path)

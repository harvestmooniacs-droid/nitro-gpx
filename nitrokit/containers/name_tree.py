"""Names for a headerless archive from a sibling name tree.

Some games store every file of a directory tree back to back in one blob
(`Data.bin`) with the names in a separate tree file (`Data.ndx`), in the
same order as the data. Radiant Historia's tree format:
  node  = u16 count, then count entries
  entry = u16 name_length, name bytes, u32 child_node_offset (0 = leaf)
Depth-first leaf order = order of the files in the blob.

The companion offset index (`Data.idx`) is not needed: the carver already
finds every Nitro file and its byte range, so the names only have to be
lined up with the carved items. That is done on the sequence of KINDS
(file extension vs. carved magic): an exact, order-preserving alignment,
so a file the carver cannot see (text, script) or finds several of (3D
models inside a pack) never shifts the names of the others.
"""
import difflib
import struct
from pathlib import PurePosixPath

EXT_KIND = {'nclr': 'NCLR', 'ncgr': 'NCGR', 'ncbr': 'NCGR', 'nscr': 'NSCR', 'ncer': 'NCER',
            'nanr': 'NANR', 'nsbmd': 'NSBMD', 'nsbtx': 'NSBTX', 'nftr': 'NFTR'}
WILDCARD_EXT = {'bin', ''}


def parse_tree(data, max_depth=32):
    """Depth-first leaf paths, or None if `data` is not this tree format."""
    leaves = []

    def walk(off, prefix, depth):
        if depth > max_depth or off + 2 > len(data):
            raise ValueError('bad node')
        count = struct.unpack_from('<H', data, off)[0]
        p = off + 2
        for _ in range(count):
            ln = struct.unpack_from('<H', data, p)[0]
            name = data[p + 2:p + 2 + ln]
            if not ln or p + 6 + ln > len(data) or not all(32 <= c < 127 for c in name):
                raise ValueError('bad entry')
            child = struct.unpack_from('<I', data, p + 2 + ln)[0]
            path = f'{prefix}/{name.decode()}' if prefix else name.decode()
            p += 6 + ln
            if child:
                if child <= off or child >= len(data):
                    raise ValueError('bad child offset')
                walk(child, path, depth + 1)
            else:
                leaves.append(path)

    try:
        walk(0, '', 0)
    except (ValueError, struct.error):
        return None
    return leaves or None


def _kind_of(path):
    name = PurePosixPath(path).name
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext in EXT_KIND:
        return EXT_KIND[ext]
    return '*' if ext in WILDCARD_EXT else None


def assign(kinds, leaf_paths):
    """kinds: carved item kinds in blob order. Returns {item_index: path}.
    Exact kind matches are aligned first; inside each gap, leaves with a
    wildcard extension (.bin, none) take unmatched items one to one."""
    named = [(p, _kind_of(p)) for p in leaf_paths]
    named = [(p, k) for p, k in named if k]
    a = [k for _, k in named]
    out = {}
    sm = difflib.SequenceMatcher(None, a, list(kinds), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for d in range(i2 - i1):
                out[j1 + d] = named[i1 + d][0]
        elif tag == 'replace':
            wild = [named[i][0] for i in range(i1, i2) if named[i][1] == '*']
            if len(wild) == j2 - j1:
                for d, path in enumerate(wild):
                    out[j1 + d] = path
    return out

"""The other BIOS codecs: Huffman (0x24/0x28), RLE (0x30), the SDK's DIFF8/
DIFF16 delta filter (0x81/0x82 - low nibble = sample width in bytes, as in
GBATEK; confirmed on Theresia's 260 Huffman->Diff8->NCGR chains), plus BLZ
(bottom-up LZ used by ARM9/overlays) decompression. A delta filter, not
real compression (output is exactly the input size, rounded up to a
multiple of 4 for the header): tag byte, u24 size, then
each byte/halfword is the difference from the previous one (running sum to
decode). Rare on its own; mostly seen chained after LZ on GBA-originated
assets (the DS SDK inherited it from AGB).
"""
import struct


# ------------------------------------------------------------------ RLE 0x30
def rle_decompress(data):
    size = data[1] | data[2] << 8 | data[3] << 16
    out, i = bytearray(), 4
    while len(out) < size:
        f = data[i]
        i += 1
        if f & 0x80:
            out += bytes([data[i]]) * ((f & 0x7F) + 3)
            i += 1
        else:
            n = (f & 0x7F) + 1
            out += data[i:i + n]
            i += n
    return bytes(out[:size])


def rle_compress(data, pad=True):
    n = len(data)
    out = bytearray([0x30, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    i, lit = 0, bytearray()

    def flush():
        while lit:
            chunk = lit[:128]
            out.append(len(chunk) - 1)
            out.extend(chunk)
            del lit[:128]
    while i < n:
        run = 1
        while i + run < n and run < 130 and data[i + run] == data[i]:
            run += 1
        if run >= 3:
            flush()
            out += bytes([0x80 | (run - 3), data[i]])
            i += run
        else:
            lit.append(data[i])
            i += 1
    flush()
    out += b'\0' * ((-len(out)) % 4)
    return bytes(out)


# -------------------------------------------------------------- Huffman 0x2x
def huff_decompress(data):
    bits = data[0] & 0xF
    size = data[1] | data[2] << 8 | data[3] << 16
    tree_size = (data[4] + 1) * 2
    tree = data[4:4 + tree_size]
    pos = 4 + tree_size
    out = bytearray()
    nibbles = []
    node = 1  # offset of root node in tree (tree[0] = size byte)
    need = size * (2 if bits == 4 else 1)
    while len(nibbles) + len(out) < need and pos + 4 <= len(data):
        word = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        for b in range(31, -1, -1):
            nv = tree[node]
            off = ((node & ~1) + ((nv & 0x3F) + 1) * 2)
            right = word >> b & 1
            child = off + right
            is_leaf = nv & (0x40 if right else 0x80)
            if is_leaf:
                sym = tree[child]
                if bits == 8:
                    out.append(sym)
                else:
                    nibbles.append(sym)
                node = 1
                if len(out) + len(nibbles) >= need:
                    break
            else:
                node = child
    if bits == 4:
        for k in range(0, len(nibbles) - 1, 2):
            out.append(nibbles[k] | nibbles[k + 1] << 4)
    return bytes(out[:size])


def huff_compress(data, bits=8):
    """Nintendo Huffman (0x24 4-bit / 0x28 8-bit), the exact inverse of
    huff_decompress. Tree table: byte (entries/2 - 1), root at [1]; a node
    byte is offset | 0x80 (left child is a leaf) | 0x40 (right is a leaf),
    children at (pos & ~1) + (offset + 1) * 2, with offset <= 63.
    Child pairs are laid out earliest-deadline-first (optimal for unit slots
    with deadlines). A near-balanced tree (flat 8-bit data) can still be too
    wide for 6-bit offsets; then the symbols are split into frequency-sorted
    groups of <= 32 (each group's subtree has <= 31 internal nodes) hung off
    a spine, which always fits, at some cost in ratio."""
    import collections
    syms = []
    if bits == 4:
        for b in data:
            syms += (b & 0xF, b >> 4)
    else:
        syms = list(data)
    freq = collections.Counter(syms)
    if len(freq) == 1:
        freq[(next(iter(freq)) + 1) % (1 << bits)] = 0
    try:
        root = _huff_tree(freq.items())
        tree = _huff_layout(root, _edf_order)
    except ValueError:
        root = _huff_grouped(freq)
        tree = _huff_layout(root, _group_order)
    codes = {}

    def walk(n, code, ln):
        if n[0] == 'leaf':
            codes[n[1]] = (code, ln)
        else:
            walk(n[1], code << 1, ln + 1)
            walk(n[2], code << 1 | 1, ln + 1)
    walk(root, 0, 0)
    n = len(data)
    out = bytearray([0x20 | bits, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF]) + tree
    acc = nacc = 0
    for s in syms:
        code, ln = codes[s]
        acc = acc << ln | code
        nacc += ln
        if nacc >= 32:
            nacc -= 32
            out += ((acc >> nacc) & 0xFFFFFFFF).to_bytes(4, 'little')
            acc &= (1 << nacc) - 1
    if nacc:
        out += ((acc << (32 - nacc)) & 0xFFFFFFFF).to_bytes(4, 'little')
    return bytes(out)


def _huff_tree(items):
    import heapq
    heap = [(f, i, ('leaf', s)) for i, (s, f) in enumerate(sorted(items))]
    heapq.heapify(heap)
    uid = len(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        heapq.heappush(heap, (f1 + f2, uid, ('node', a, b)))
        uid += 1
    return heap[0][2]


def _huff_grouped(freq):
    ranked = sorted(freq.items(), key=lambda kv: -kv[1])
    groups = [ranked[i:i + 32] for i in range(0, len(ranked), 32)]
    subs = [_huff_tree(g) if len(g) > 1 else ('node', ('leaf', g[0][0]), ('leaf', g[0][0])) for g in groups]
    node = subs[-1]
    for sub in reversed(subs[:-1]):
        node = ('node', sub, node, 'spine')
    return node


def _edf_order(root):
    """Internal nodes in the order their child pairs are written."""
    order, pending, pair, tie = [], [(63, 0, root)], 1, 1
    while pending:
        pending.sort(key=lambda t: t[:2])
        deadline, _, node = pending.pop(0)
        if pair > deadline:
            raise ValueError('huffman tree too wide for 6-bit node offsets')
        order.append(node)
        for child in node[1:3]:
            if child[0] != 'leaf':
                pending.append((pair + 63, tie, child))
                tie += 1
        pair += 1
    return order


def _bfs_internal(node):
    out, queue = [], [node] if node[0] != 'leaf' else []
    while queue:
        n = queue.pop(0)
        out.append(n)
        queue += [c for c in n[1:3] if c[0] != 'leaf']
    return out


def _group_order(root):
    order, node = [], root
    while len(node) == 4:                  # spine node: its group, then the next spine node
        order.append(node)
        order += _bfs_internal(node[1])
        node = node[2]
    return order + _bfs_internal(node)


def _huff_layout(root, order_fn):
    order = order_fn(root)
    pair_of = {id(n): k + 1 for k, n in enumerate(order)}      # children pair index
    slot_of = {id(root): 1}
    for n in order:
        base = pair_of[id(n)] * 2
        for side, child in enumerate(n[1:3]):
            slot_of[id(child)] = base + side
    pairs = len(order) + 1
    if pairs % 2:
        pairs += 1                 # keeps the bitstream on a 4-byte boundary
    tree = bytearray(pairs * 2)
    tree[0] = pairs - 1
    for n in order:
        pos = slot_of[id(n)]
        off = pair_of[id(n)] - (pos >> 1) - 1
        if not 0 <= off <= 63:
            raise ValueError('huffman tree too wide for 6-bit node offsets')
        flag = 0
        for side, child in enumerate(n[1:3]):
            if child[0] == 'leaf':
                tree[pair_of[id(n)] * 2 + side] = child[1]
                flag |= 0x80 >> side
        tree[pos] = off | flag
    return tree


# --------------------------------------------------------------- DIFF8/16
def diff8_decompress(data):
    size = data[1] | data[2] << 8 | data[3] << 16
    out = bytearray(size)
    last = 0
    for i in range(size):
        last = (last + data[4 + i]) & 0xFF
        out[i] = last
    return bytes(out)


def diff8_compress(data):
    n = len(data)
    out = bytearray([0x81, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    last = 0
    for b in data:
        out.append((b - last) & 0xFF)
        last = b
    out += bytes((-len(out)) % 4)
    return bytes(out)


def diff16_decompress(data):
    size = data[1] | data[2] << 8 | data[3] << 16
    out = bytearray(size)
    last = 0
    for i in range(0, size, 2):
        last = (last + struct.unpack_from('<H', data, 4 + i)[0]) & 0xFFFF
        chunk = struct.pack('<H', last)
        out[i:i + 2] = chunk[:min(2, size - i)]
    return bytes(out)


def diff16_compress(data):
    n = len(data)
    padded = data + bytes((-n) % 2)
    out = bytearray([0x82, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    last = 0
    for i in range(0, len(padded), 2):
        hw = struct.unpack_from('<H', padded, i)[0]
        out += struct.pack('<H', (hw - last) & 0xFFFF)
        last = hw
    return bytes(out)

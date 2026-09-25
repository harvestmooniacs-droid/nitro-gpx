"""Optimal-parse LZ10/LZ11 encoder (minimum output size by dynamic programming).

Why: re-inserting into a container that has no index we can rewrite (a
bundle, a big archive) means the new stream must fit the ORIGINAL slot.
Nintendo's encoder beats a greedy hash-chain one by 0.5-2%, so a greedy
recompression of a lightly edited file overflows. Choosing, for every
position, the cheapest of "literal" or "match of length 3..L" from the end
backwards gives the smallest possible stream for the match finder used, and
in practice lands below the original size.

Costs in bits (flag bit included): literal 9; LZ10 match 17; LZ11 match
17 (len<=16), 25 (<=272), 33 (longer).
"""
MAX_CHAIN = 96


def longest_matches(data, max_match, max_dist):
    n = len(data)
    L = [0] * n
    D = [0] * n
    heads = {}
    prev = [-1] * n
    for i in range(n - 2):
        key = data[i] | data[i + 1] << 8 | data[i + 2] << 16
        j = heads.get(key, -1)
        best, bd = 0, 0
        cap = min(max_match, n - i)
        chain = 0
        lim = i - max_dist
        while j >= 0 and j >= lim and chain < MAX_CHAIN:
            if best < cap and data[j + best] == data[i + best]:
                ln = 3
                while ln < cap and data[j + ln] == data[i + ln]:
                    ln += 1
                if ln > best:
                    best, bd = ln, i - j
                    if ln == cap:
                        break
            j = prev[j]
            chain += 1
        if best >= 3:
            L[i], D[i] = best, bd
        prev[i] = heads.get(key, -1)
        heads[key] = i
    return L, D


def _encode(data, lz11, pad):
    n = len(data)
    max_match = 0x10110 if lz11 else 18
    L, D = longest_matches(data, max_match, 4096)
    cost = [0] * (n + 1)
    choice = [0] * n        # 0 = literal, else match length
    for i in range(n - 1, -1, -1):
        best = cost[i + 1] + 9
        ch = 0
        li = L[i]
        if li >= 3:
            if lz11:
                # candidate lengths: every boundary where the token size
                # changes, plus a sweep of short lengths
                cands = set(range(3, min(li, 16) + 1))
                cands.update(x for x in (16, 17, 272, 273, li) if 3 <= x <= li)
                if li > 17:
                    cands.update(range(max(17, li - 20), li + 1))
                for ln in cands:
                    c = (17 if ln <= 16 else 25 if ln <= 272 else 33) + cost[i + ln]
                    if c < best:
                        best, ch = c, ln
            else:
                for ln in range(3, li + 1):
                    c = 17 + cost[i + ln]
                    if c < best:
                        best, ch = c, ln
        cost[i] = best
        choice[i] = ch
    out = bytearray([0x11 if lz11 else 0x10, n & 0xFF, n >> 8 & 0xFF, n >> 16 & 0xFF])
    i, bit, flag_pos = 0, -1, 0
    while i < n:
        if bit < 0:
            flag_pos = len(out)
            out.append(0)
            bit = 7
        ln = choice[i]
        if ln:
            d = D[i] - 1
            out[flag_pos] |= 1 << bit
            if not lz11:
                out += bytes([(ln - 3) << 4 | d >> 8, d & 0xFF])
            elif ln <= 16:
                out += bytes([(ln - 1) << 4 | d >> 8, d & 0xFF])
            elif ln <= 272:
                v = ln - 0x11
                out += bytes([v >> 4, (v & 0xF) << 4 | d >> 8, d & 0xFF])
            else:
                v = ln - 0x111
                out += bytes([0x10 | v >> 12, v >> 4 & 0xFF, (v & 0xF) << 4 | d >> 8, d & 0xFF])
            i += ln
        else:
            out.append(data[i])
            i += 1
        bit -= 1
    if pad:
        out += b'\0' * ((-len(out)) % 4)
    return bytes(out)


def compress_lz10(data, pad=True):
    return _encode(data, False, pad)


def compress_lz11(data, pad=True):
    return _encode(data, True, pad)

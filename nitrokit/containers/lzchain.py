"""A file that is nothing but BIOS LZ10/LZ11 streams back to back, with at
most a few padding bytes between them (Inazuma Eleven .pkb, whose index
lives in a separate .pkh; Advance Wars resource banks). Decompressing
only the first stream - what a plain `unwrap` does - hides every other
member, so the whole chain is recognised here instead.

Members are addressed with the ordinary ['lz', off, codec, consumed] step,
so write-back follows the carver's rule: an edited member is recompressed
into its original slot (no index to update without the sibling file).
"""
MAX_PAD = 16
CODEC = {0x10: 'lz10', 0x11: 'lz11'}


def parse(data, min_streams=2):
    """[(offset, codec, consumed)] or None."""
    from ..pipeline.source import lz_consumed
    if data[:1] not in (b'\x10', b'\x11'):
        return None
    pos, out, n = 0, [], len(data)
    while pos < n:
        skip = 0
        while pos < n and data[pos] in (0, 0xFF) and skip < MAX_PAD:
            pos += 1
            skip += 1
        if pos >= n:
            break
        if data[pos] in (0, 0xFF):                      # long run: only trailing padding is fine
            if data[pos:].strip(b'\0\xff'):
                return None
            break
        codec = CODEC.get(data[pos])
        if codec is None:
            return None
        try:
            used = lz_consumed(data, pos, codec)       # walks the token stream: validates structure
        except (ValueError, IndexError):
            return None
        if used <= 4 or pos + used > n:
            return None
        out.append((pos, codec, used))
        pos += used
    return out if len(out) >= min_streams else None

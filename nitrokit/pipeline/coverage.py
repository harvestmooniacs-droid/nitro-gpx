"""Which files of the source produced graphics, and which produced nothing.

The scan walks every file, but a file that holds no recognisable graphics
just disappears from the result - which looks exactly like 'the tool is
ignoring folders'. This turns that silence into an auditable list: every
file no resource points into, with its size and a reason, biggest first.
Audio/video and code are called out separately so the real unknowns (the
proprietary containers still to be reverse engineered) stand out.
"""
from pathlib import PurePosixPath

# extensions whose content is genuinely not 2D/3D graphics
AUDIO_VIDEO = {'.sdat', '.adx', '.mods', '.sad', '.strm', '.swav', '.swar', '.sseq',
               '.sbnk', '.wav', '.mp3', '.mod', '.vx', '.mov', '.moflex', '.mobiclip'}
TEXT_DATA = {'.txt', '.csv', '.xml', '.json', '.htx', '.msg', '.ini'}
PROBE_MAX = 8 << 20          # content probe (decompress + magic) only below this size


def _diag_reason(diag):
    """Turn one scan.UNRESOLVED entry into a human-readable line: what WAS
    recognised (container, compression) vs. what wasn't (the payload
    itself) - generic across games, since it just reports whatever the
    scanner's own container/compression detectors already found."""
    codecs = sorted({s['codec'] for s in diag['samples']} - {'none (dados crus)'})
    comp = ' + '.join(codecs) if codecs else 'nenhuma (dados crus)'
    return (f"contêiner reconhecido ({diag['container']}, {diag['entries']} itens) · "
            f"compressão: {comp} · estrutura interna não decifrada (formato próprio do jogo)")


# what a file IS, by its content (after decompression), when that content
# is a known format that simply isn't a picture - so "unidentified" only
# counts real gaps. Magic -> reason.
NOT_IMAGE = {
    b'BCA0': 'animação 3D de esqueleto (NSBCA) — não é imagem',
    b'BTA0': 'animação 3D de textura (NSBTA) — não é imagem',
    b'BMA0': 'animação 3D de material (NSBMA) — não é imagem',
    b'BTP0': 'animação 3D de troca de textura (NSBTP) — não é imagem',
    b'BVA0': 'animação 3D de visibilidade (NSBVA) — não é imagem',
    b'RNAN': 'animação 2D (NANR: sequência de quadros) — não é imagem',
    b'RNMA': 'animação 2D multicélula (NMAR) — não é imagem',
    b'MESG': 'texto (BMG)', b'SDAT': 'áudio (SDAT)', b'SWAR': 'áudio', b'SSEQ': 'áudio',
}


def _probe(data):
    """-> reason for a known non-image content, or None."""
    from .. import compression
    try:
        payload, _ = compression.unwrap(data)
    except Exception:
        payload = data
    magic = bytes(payload[:4])
    if magic in NOT_IMAGE:
        return NOT_IMAGE[magic]
    if magic == b'BMD0':
        return 'modelo 3D sem textura embutida (NSBMD) — não há imagem'
    if magic == b'NARC':
        from ..containers import narc
        try:
            files = narc.unpack(payload)
        except Exception:
            return None
        kinds = {_probe(f) for f in files if f}
        if files and None not in kinds:
            return 'NARC só com dados que não são imagem (modelos/animações 3D/2D)'
    return None


def _reason(path, size, diag=None):
    """-> (text, candidate). `candidate` is True for a file that COULD have
    held a graphic (so its absence from the resource list is a real miss,
    not scope) - callers should use this flag, not string-match the text,
    to decide what counts as 'identified out of what could be'."""
    if diag is not None:
        return _diag_reason(diag), True
    ext = PurePosixPath(path).suffix.lower().rstrip('_')
    if path.startswith(('overlay9/', 'overlay7/')) or path.startswith('arm'):
        return 'código (overlay/ARM) — varredura opcional', False
    if ext in AUDIO_VIDEO:
        return 'áudio/vídeo — fora do escopo', False
    if ext in TEXT_DATA:
        return 'texto/dados — fora do escopo', False
    if size < 64:
        return 'muito pequeno para um gráfico', False
    return 'nenhum gráfico reconhecido (contêiner próprio?)', True


def report(rom, resources, unresolved=None):
    """-> {'files': n, 'with_graphics': n, 'rows': [{path,size,reason,diag}, ...]}

    `rows` is only the files nothing points into, sorted biggest first.
    `unresolved` is scan.UNRESOLVED from the last scan() call (a list of
    per-file container/compression diagnostics for files whose CONTAINER
    was recognised but whose payload wasn't) - when given, those files get
    a detailed reason instead of the generic 'contêiner próprio?' guess.
    """
    covered = set()
    for r in resources:
        for key in ('char', 'map', 'pal'):
            loc = r.get(key)
            if loc and loc[0] and loc[0][0] == 'file':
                covered.add(loc[0][1])
    by_path = {d['path']: d for d in (unresolved or ())}
    rows, total = [], 0
    for fid, path in rom.all_files():
        total += 1
        if path in covered:
            continue
        try:
            start, end = rom.fat[fid]
            size = max(0, end - start)
        except Exception:
            size = 0
        diag = by_path.get(path)
        reason, candidate = _reason(path, size, diag)
        if candidate and diag is None and size <= PROBE_MAX:
            try:
                known = _probe(rom.read_fid(fid))
            except Exception:
                known = None
            if known:
                reason, candidate = known, False
        rows.append({'path': path, 'size': size, 'reason': reason, 'diag': diag, 'candidate': candidate})
    rows.sort(key=lambda row: -row['size'])
    return {'files': total, 'with_graphics': len(covered), 'rows': rows,
            'candidates': len(covered) + sum(1 for r in rows if r['candidate']),
            'unknown_bytes': sum(r['size'] for r in rows if r['candidate'])}

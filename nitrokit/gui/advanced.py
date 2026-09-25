"""Manual raw-graphics laboratory used by the advanced GUI tab."""
from pathlib import Path

import numpy as np
from PIL import Image

from .. import compression


AUTO = 'Auto detectar'
CODECS = (AUTO, 'Sem compressão') + tuple(compression.CODECS)


def decode_palette(path, colors):
    if not path:
        return [(i * 255 // max(1, colors - 1),) * 3 for i in range(colors)]
    data = Path(path).read_bytes()
    if len(data) < colors * 2:
        raise ValueError(f'Paleta precisa de {colors * 2} bytes; arquivo tem {len(data)}.')
    result = []
    for i in range(colors):
        value = int.from_bytes(data[i * 2:i * 2 + 2], 'little')
        r, g, b = value & 31, value >> 5 & 31, value >> 10 & 31
        result.append((r * 255 // 31, g * 255 // 31, b * 255 // 31))
    return result


def unpack_indices(data, bpp):
    raw = np.frombuffer(data, dtype=np.uint8)
    if bpp == 8:
        return raw
    if bpp == 4:
        return np.stack((raw & 15, raw >> 4), axis=1).reshape(-1)
    if bpp == 2:
        return np.stack((raw & 3, raw >> 2 & 3, raw >> 4 & 3, raw >> 6), axis=1).reshape(-1)
    if bpp == 1:
        return np.stack(tuple((raw >> bit) & 1 for bit in range(8)), axis=1).reshape(-1)
    raise ValueError('BPP manual suportado: 1, 2, 4 ou 8.')


def untile(indices, width, height, tile):
    if tile <= 1:
        return indices[:width * height].reshape(height, width)
    if width % tile or height % tile:
        raise ValueError('Largura e altura precisam ser múltiplas do tile.')
    needed = width * height
    source = indices[:needed]
    if len(source) < needed:
        raise ValueError(f'Dados insuficientes: {len(source)} pixels para {needed}.')
    tiles = source.reshape(-1, tile, tile)
    rows, cols = height // tile, width // tile
    return tiles.reshape(rows, cols, tile, tile).transpose(0, 2, 1, 3).reshape(height, width)


class LabError(ValueError):
    """Carries which STEP of the manual pipeline failed, so the GUI can
    show 'descompressão' / 'montagem dos pixels' / 'paleta' instead of a
    bare library exception with no idea which of the 5 stages broke."""

    def __init__(self, step, detail):
        super().__init__(f'{step}: {detail}')
        self.step, self.detail = step, detail


def render(path, codec=AUTO, bpp=4, width=128, height=128, offset=0,
           tile=8, palette_path=None, data=None, palette_colors=None):
    """`data`: bytes straight from the ROM (a file picked in the tree) -
    then `path` is only a label. `palette_colors`: RGB tuples already
    decoded (e.g. from a palette the scan already recognised, possibly
    belonging to a different graphic) - takes priority over `palette_path`
    when given."""
    data = Path(path).read_bytes() if data is None else bytes(data)
    detected = None
    try:
        if codec == AUTO:
            payload, detected = compression.unwrap(data)
        elif codec == 'Sem compressão':
            payload = data
        else:
            payload = compression.decompress_as(data, codec)
            detected = codec
    except Exception as exc:
        if codec not in (AUTO, 'Sem compressão'):
            raise LabError('Descompressão', f'os bytes deste arquivo não começam com o cabeçalho da '
                           f'compressão "{codec}", que você escolheu manualmente ({exc}). Tente "Auto '
                           f'detectar" ou "Sem compressão".') from exc
        raise LabError('Descompressão', f'o arquivo parece comprimido mas os dados estão corrompidos ou '
                       f'truncados para esse algoritmo ({exc}).') from exc
    if offset < 0 or offset >= len(payload):
        raise LabError('Offset', f'{offset} está fora do payload descomprimido, que só tem '
                       f'{len(payload)} bytes. Reduza o Offset.')
    try:
        indices = unpack_indices(payload[offset:], bpp)
        array = untile(indices, width, height, tile)
    except (ValueError, IndexError) as exc:
        raise LabError('Montagem dos pixels', f'largura {width} × altura {height} (em {bpp}bpp, a partir '
                       f'do offset {offset}) precisa de mais bytes do que os {len(payload) - offset} que '
                       f'sobraram no arquivo depois da descompressão/offset ({exc}). Reduza largura, '
                       f'altura ou offset.') from exc
    image = Image.fromarray(array.astype(np.uint8), 'P')
    if palette_colors is not None:
        n = 1 << bpp
        palette = (list(palette_colors) + [(0, 0, 0)] * n)[:n]
    else:
        try:
            palette = decode_palette(palette_path, 1 << bpp)
        except (ValueError, OSError) as exc:
            raise LabError('Paleta', f'não foi possível ler "{palette_path}" como paleta BGR555 de '
                           f'{1 << bpp} cores ({exc}).') from exc
    flat = [channel for rgb in palette for channel in rgb]
    image.putpalette((flat + [0] * 768)[:768])
    return image, {'codec': detected or 'none', 'compressed_size': len(data),
                   'payload_size': len(payload), 'bpp': bpp, 'offset': offset,
                   'width': width, 'height': height, 'tile': tile,
                   'palette': ('paleta do jogo' if palette_colors is not None
                              else str(palette_path) if palette_path else None)}

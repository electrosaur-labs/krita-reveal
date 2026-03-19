"""
pipeline.py — Krita pixel I/O + pyreveal integration.

Krita LABA U16: 4 channels × 2 bytes = 8 bytes/pixel (L, a, b, alpha).
pyreveal engine16: L/a/b each 0-32768 (neutral a/b = 16384).
Conversion: pyreveal_val = krita_val >> 1
"""

from __future__ import annotations

import struct


def krita_pixels_to_pyreveal(raw: bytes, pixel_count: int) -> list:
    """Decode Krita LABA U16 raw bytes → flat pyreveal 16-bit Lab list."""
    result = [0] * (pixel_count * 3)
    for i in range(pixel_count):
        off = i * 8
        L, a, b = struct.unpack_from('<HHH', raw, off)
        j = i * 3
        result[j]     = L >> 1
        result[j + 1] = a >> 1
        result[j + 2] = b >> 1
    return result


def read_document_pixels(doc, node=None):
    """Read Lab16 pixels from a Krita document node.

    Returns (pixels, width, height) — pixels in pyreveal encoding.
    """
    width  = doc.width()
    height = doc.height()
    if node is None:
        raw = bytes(doc.rootNode().projectionPixelData(0, 0, width, height))
    else:
        raw = bytes(node.pixelData(0, 0, width, height))
    return krita_pixels_to_pyreveal(raw, width * height), width, height


def downsample_pixels(pixels: list, width: int, height: int, max_dim: int = 800):
    """Nearest-neighbour downsample to fit within max_dim × max_dim."""
    scale = min(1.0, max_dim / max(width, height))
    if scale >= 1.0:
        return pixels, width, height
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    result = []
    for y in range(new_h):
        src_y = int(y / scale)
        for x in range(new_w):
            src_x = int(x / scale)
            off = (src_y * width + src_x) * 3
            result.extend([pixels[off], pixels[off + 1], pixels[off + 2]])
    return result, new_w, new_h


def make_original_rgb(lab_pixels, width: int, height: int) -> bytes:
    """Convert pyreveal engine16 Lab pixels → RGB24 bytes for display.

    Uses the engine16→8bit→rgb path for reliable cross-platform results.
    """
    from pyreveal.color.encoding import convert_engine16bit_to_8bit_lab, lab8bit_to_rgb
    n = width * height
    lab8 = convert_engine16bit_to_8bit_lab(lab_pixels, n)
    return bytes(lab8bit_to_rgb(lab8, n))


def make_posterized_rgb(assignments, palette: list, width: int, height: int) -> bytes:
    """Map each pixel's assignment index to its palette RGB colour."""
    lut = [(c['r'], c['g'], c['b']) for c in palette]
    n = width * height
    data = bytearray(n * 3)
    for i in range(n):
        idx = assignments[i]
        r, g, b = lut[idx] if idx < len(lut) else (0, 0, 0)
        j = i * 3
        data[j], data[j + 1], data[j + 2] = r, g, b
    return bytes(data)


def make_solo_rgb(assignments, palette: list, color_index: int,
                  width: int, height: int) -> bytes:
    """RGB preview showing one colour at full brightness, others as dark grey."""
    lut = [(c['r'], c['g'], c['b']) for c in palette]
    n   = width * height
    data = bytearray(n * 3)
    for i in range(n):
        j = i * 3
        if assignments[i] == color_index:
            data[j], data[j + 1], data[j + 2] = lut[assignments[i]]
        else:
            data[j] = data[j + 1] = data[j + 2] = 35
    return bytes(data)


def _get_archetype_scores(config: dict) -> list:
    """Extract ranked archetype list from a generate_configuration() result."""
    from pyreveal.analysis.archetype_loader import ArchetypeLoader
    ranking = (config.get('meta') or {}).get('match_ranking') or []
    if not ranking:
        params = (config.get('parameters') or {})
        return [{'id': config.get('id', ''), 'name': config.get('name', ''),
                 'group': '', 'score': 1.0,
                 'min_colors': params.get('minColors', 4),
                 'max_colors': params.get('maxColors', 8)}]
    arch_by_id = {a['id']: a for a in ArchetypeLoader.load_archetypes()}
    return [
        {
            'id':         m['id'],
            'name':       arch_by_id.get(m['id'], {}).get('name', m['id']),
            'group':      arch_by_id.get(m['id'], {}).get('group', ''),
            'score':      m['score'],
            'min_colors': arch_by_id.get(m['id'], {}).get('parameters', {}).get('minColors', 4),
            'max_colors': arch_by_id.get(m['id'], {}).get('parameters', {}).get('maxColors', 8),
        }
        for m in ranking
    ]


def run_separation(pixels: list, width: int, height: int,
                   target_colors: int = 6, options: dict = None) -> dict:
    """Run the full pyreveal pipeline and return posterize_image() result.

    options keys consumed internally (not passed to engine):
      _archetype_id           — force a specific archetype (or '__auto__' for auto-match)
      _preprocessing_intensity — 'off'|'auto'|'light'|'medium'|'heavy'
    """
    import pyreveal
    from pyreveal.analysis.parameter_generator import ParameterGenerator

    options = dict(options) if options else {}

    archetype_id            = options.pop('_archetype_id', None)
    preprocessing_intensity = options.pop('_preprocessing_intensity', 'off')

    dna = pyreveal.analyze_image(pixels, width, height, {'bit_depth': 16})

    # ── Archetype-driven mode ─────────────────────────────────────────────
    if archetype_id is not None:
        manual_id = archetype_id if archetype_id != '__auto__' else None
        config    = pyreveal.generate_configuration(
            dna, {'manual_archetype_id': manual_id} if manual_id else None,
        )
        # Archetype owns all algorithm params; mechanical knobs override
        mechanical = {
            'density_floor':  options.get('density_floor', 0),
            'speckle_rescue': options.get('speckle_rescue', 0),
            'shadow_clamp':   options.get('shadow_clamp', 0),
        }
        params = ParameterGenerator.to_engine_options(config, mechanical)
        params['target_colors'] = target_colors   # user Colors knob wins
        # Map unported engine types to best available equivalent
        _ENGINE_MAP = {'classic': 'balanced'}
        params['engine_type'] = _ENGINE_MAP.get(params.get('engine_type', ''), params.get('engine_type', 'reveal'))
    # ── Manual mode (explicit UI knobs) ──────────────────────────────────
    else:
        params = {'substrate_mode': 'none'}
        params.update(options)
        config = pyreveal.generate_configuration(dna)

    # ── Pre-smoothing ─────────────────────────────────────────────────────
    if preprocessing_intensity != 'off':
        from pyreveal.preprocessing.bilateral_filter import create_preprocessing_config
        pre_config = create_preprocessing_config(
            dna, pixels, width, height,
            intensity_override=preprocessing_intensity,
        )
        if pre_config.get('enabled'):
            pyreveal.preprocess_image(pixels, width, height, pre_config)
    elif config.get('preprocessing', {}).get('enabled'):
        pyreveal.preprocess_image(pixels, width, height, config['preprocessing'])

    result = pyreveal.posterize_image(pixels, width, height, target_colors, params)

    # Attach archetype metadata for the UI
    result['_matched_archetype'] = {
        'id':   config.get('id', ''),
        'name': config.get('name', ''),
    }
    result['_archetype_scores'] = _get_archetype_scores(config)

    return result

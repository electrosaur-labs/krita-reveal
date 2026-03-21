"""
pipeline.py — Krita pixel I/O + pyreveal integration.

Krita LABA U16: 4 channels × 2 bytes = 8 bytes/pixel (L, a, b, alpha).
pyreveal engine16: L/a/b each 0-32768 (neutral a/b = 16384).
Conversion: pyreveal_val = krita_val >> 1
"""

from __future__ import annotations

import struct

# Pseudo-archetypes: code-only, no JSON file.
# Chameleon = adaptive interpolation from DNA (nearest-archetype blend).
# Distilled = over-quantize then furthest-point sample; no palette reduction.
# Salamander = SALIENCY centroid, DNA color count, no palette reduction, no preprocessing.
_PSEUDO_ARCHETYPES = {
    'dynamic_interpolator': {'name': 'Chameleon',  'min_colors': 4,  'max_colors': 10},
    'distilled':            {'name': 'Distilled',  'min_colors': 6,  'max_colors': 14},
    'salamander':           {'name': 'Salamander', 'min_colors': 4,  'max_colors': 10},
}


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
    """Extract ranked archetype list from a generate_configuration() result.

    Always appends the three pseudo-archetypes (Chameleon, Distilled, Salamander)
    at a fixed score just below the best real match, mirroring ScoringManager.js.
    """
    from pyreveal.analysis.archetype_loader import ArchetypeLoader
    ranking = (config.get('meta') or {}).get('match_ranking') or []
    if not ranking:
        # Forced single-archetype rerun — no full ranking available.
        # Return [] so the processor keeps its cached full ranking from the
        # initial auto-match run (processor.py: `if fresh: self._archetype_scores = fresh`).
        return []
    else:
        arch_by_id = {a['id']: a for a in ArchetypeLoader.load_archetypes()}
        scores = [
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

    # Inject pseudo-archetypes just below the best real score (mirrors ScoringManager.js)
    pseudo_score = min(70.0, scores[0]['score'] if scores else 70.0)
    for pid, pdef in _PSEUDO_ARCHETYPES.items():
        scores.append({
            'id':         pid,
            'name':       pdef['name'],
            'group':      'adaptive',
            'score':      pseudo_score,
            'min_colors': pdef['min_colors'],
            'max_colors': pdef['max_colors'],
        })
        pseudo_score = max(0.0, pseudo_score - 1.0)

    return scores


# camelCase interpolator output → snake_case config keys expected by ParameterGenerator
_CAMEL_TO_SNAKE = {
    'lWeight':                     'l_weight',
    'cWeight':                     'c_weight',
    'bWeight':                     'b_weight',
    'blackBias':                   'black_bias',
    'vibrancyBoost':               'vibrancy_boost',
    'vibrancyMode':                'vibrancy_mode',
    'highlightThreshold':          'highlight_threshold',
    'highlightBoost':              'highlight_boost',
    'paletteReduction':            'palette_reduction',
    'enablePaletteReduction':      'enable_palette_reduction',
    'substrateTolerance':          'substrate_tolerance',
    'substrateMode':               'substrate_mode',
    'hueLockAngle':                'hue_lock_angle',
    'enableHueGapAnalysis':        'enable_hue_gap_analysis',
    'shadowPoint':                 'shadow_point',
    'colorMode':                   'color_mode',
    'preserveWhite':               'preserve_white',
    'preserveBlack':               'preserve_black',
    'ignoreTransparent':           'ignore_transparent',
    'centroidStrategy':            'centroid_strategy',
    'splitMode':                   'split_mode',
    'quantizer':                   'quantizer',
    'refinementPasses':            'refinement_passes',
    'neutralSovereigntyThreshold': 'neutral_sovereignty_threshold',
    'chromaGate':                  'chroma_gate',
    'detailRescue':                'detail_rescue',
    'speckleRescue':               'speckle_rescue',
    'medianPass':                  'median_pass',
    'minVolume':                   'min_volume',
    'shadowClamp':                 'shadow_clamp',
    'shadowChromaGateL':           'shadow_chroma_gate_l',
    'distanceMetric':              'distance_metric',
    'ditherType':                  'dither_type',
    'maxColors':                   'target_colors',   # interpolator uses maxColors
    'preprocessingIntensity':      'preprocessing_intensity',
}


def _interpolator_to_config(interp_params: dict, dna: dict) -> dict:
    """Convert InterpolatorEngine camelCase output to the snake_case config dict
    expected by ParameterGenerator.to_engine_options()."""
    config: dict = {}
    for camel, snake in _CAMEL_TO_SNAKE.items():
        val = interp_params.get(camel)
        if val is not None:
            config[snake] = val
    # Chameleon always uses distilled engine (set by generateConfigurationMk2)
    config['engine_type'] = 'distilled'
    # Fixed safety floor (matches ParameterGenerator.generate)
    config['neutral_centroid_clamp_threshold'] = 0.5
    config['range_clamp'] = [dna.get('min_l', 0), dna.get('max_l', 100)]
    config.setdefault('preprocessing', {'enabled': False})
    return config


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

    mechanical  = {}    # populated in archetype-driven branch; used for _matched_archetype
    blend_info  = None  # set for Chameleon/Salamander (interpolator); None otherwise

    # ── Archetype-driven mode ─────────────────────────────────────────────
    if archetype_id is not None:
        manual_id = archetype_id if archetype_id != '__auto__' else None
        is_pseudo = manual_id in _PSEUDO_ARCHETYPES

        # Build config: interpolator for Chameleon/Salamander, auto-match for others.
        if manual_id in ('dynamic_interpolator', 'salamander'):
            from pyreveal.analysis.interpolator_engine import get_engine
            flat_dna   = dna.get('global', dna)
            interp     = get_engine().interpolate(flat_dna)
            config     = _interpolator_to_config(interp['parameters'], dna)
            blend_info = interp['blendInfo']
        elif is_pseudo:
            # distilled — no JSON archetype, use auto-match config as base
            config     = pyreveal.generate_configuration(dna)
            blend_info = None
        else:
            config     = pyreveal.generate_configuration(
                dna, {'manual_archetype_id': manual_id} if manual_id else None,
            )
            blend_info = None

        # Archetype owns all algorithm params; mechanical knobs override only
        # when explicitly provided — otherwise fall through to archetype's values.
        mechanical = {
            'density_floor':  options.get('density_floor',
                                          config.get('min_volume', 0) / 100.0),
            'speckle_rescue': options.get('speckle_rescue',
                                          config.get('speckle_rescue', 0)),
            'shadow_clamp':   options.get('shadow_clamp',
                                          config.get('shadow_clamp', 0)),
        }
        # Colors: 0 is a sentinel meaning "let archetype decide" — use the
        # adaptive count the archetype computed from DNA.
        if target_colors == 0:
            target_colors = config.get('target_colors', 6)

        params = ParameterGenerator.to_engine_options(config, mechanical)

        # Apply any user-explicit algorithm overrides from the Advanced panel
        _ADVANCED_KEYS = [
            'vibrancy_boost', 'vibrancy_mode',
            'l_weight', 'c_weight', 'black_bias',
            'shadow_point',
            'palette_reduction', 'enable_palette_reduction',
            'enable_hue_gap_analysis', 'hue_lock_angle',
            'substrate_mode', 'preserve_white', 'preserve_black',
            'engine_type', 'color_mode', 'dither_type',
            'distance_metric', 'centroid_strategy', 'split_mode', 'quantizer',
            'neutral_sovereignty_threshold', 'chroma_gate',
            'highlight_threshold', 'highlight_boost',
            'median_pass', 'detail_rescue',
            'substrate_tolerance', 'ignore_transparent',
        ]
        for key in _ADVANCED_KEYS:
            if key in options:
                params[key] = options[key]

        params['target_colors'] = target_colors
        # Map unported engine types to best available equivalent
        _ENGINE_MAP = {'classic': 'balanced'}
        params['engine_type'] = _ENGINE_MAP.get(params.get('engine_type', ''), params.get('engine_type', 'reveal'))

        # ── Pseudo-archetype behaviour overrides ──────────────────────────
        if manual_id == 'dynamic_interpolator':
            # Chameleon: interpolator already set engine_type='distilled'; just ensure it.
            # Disable mk1.5-specific extras (peak injection, hue gap) — the JS distilled
            # engine uses over-quantize + FPS instead; these would inflate color count.
            params['engine_type']              = 'distilled'
            params['enable_hue_gap_analysis']  = False
            params['peak_finder_max_peaks']    = 0
        elif manual_id == 'distilled':
            params['engine_type']             = 'distilled'
            params['enable_palette_reduction'] = False
        elif manual_id == 'salamander':
            # Salamander = Chameleon + distilled engine + no pruning + no preprocessing
            params['engine_type']             = 'distilled'
            params['centroid_strategy']        = 'SALIENCY'
            params['enable_palette_reduction'] = False
            params['enable_hue_gap_analysis']  = False
            params['peak_finder_max_peaks']    = 0
            mechanical['density_floor']        = options.get('density_floor', 0)
            mechanical['speckle_rescue']       = options.get('speckle_rescue', 0)
            config = dict(config)
            config['preprocessing'] = {'enabled': False}

        # Patch config identity so UI displays the pseudo-archetype name
        if is_pseudo:
            config = dict(config)
            config['id']   = manual_id
            config['name'] = _PSEUDO_ARCHETYPES[manual_id]['name']
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

    # Attach archetype metadata for the UI — all control values round-trip
    result['_matched_archetype'] = {
        'id':      config.get('id', ''),
        'name':    config.get('name', ''),
        'colors':  target_colors,
        'density': round(mechanical.get('density_floor', 0) * 100.0, 1),
        'speckle': mechanical.get('speckle_rescue', 0),
        'clamp':   mechanical.get('shadow_clamp', 0),
        'vibrancy_boost':           params.get('vibrancy_boost', 1.4),
        'vibrancy_mode':            params.get('vibrancy_mode', 'moderate'),
        'l_weight':                 params.get('l_weight', 1.2),
        'c_weight':                 params.get('c_weight', 2.0),
        'black_bias':               params.get('black_bias', 3.0),
        'shadow_point':             params.get('shadow_point', 15),
        'palette_reduction':        params.get('palette_reduction', 6.0),
        'enable_palette_reduction': params.get('enable_palette_reduction', True),
        'enable_hue_gap_analysis':  params.get('enable_hue_gap_analysis', True),
        'hue_lock_angle':           params.get('hue_lock_angle', 20),
        'substrate_mode':           params.get('substrate_mode', 'none'),
        'preserve_white':           params.get('preserve_white', True),
        'preserve_black':           params.get('preserve_black', True),
        'preprocessing':            options.get('_preprocessing_intensity', 'off'),
        'engine_type':                      params.get('engine_type', 'reveal-mk1.5'),
        'color_mode':                       params.get('color_mode', 'color'),
        'dither_type':                      params.get('dither_type', 'none'),
        'distance_metric':                  params.get('distance_metric', 'cie76'),
        'centroid_strategy':                params.get('centroid_strategy', 'ROBUST_SALIENCY'),
        'split_mode':                       params.get('split_mode', 'median'),
        'quantizer':                        params.get('quantizer', 'wu'),
        'neutral_sovereignty_threshold':    params.get('neutral_sovereignty_threshold', 0),
        'chroma_gate':                      params.get('chroma_gate', 1.0),
        'highlight_threshold':              params.get('highlight_threshold', 90),
        'highlight_boost':                  params.get('highlight_boost', 1.5),
        'median_pass':                      params.get('median_pass', False),
        'detail_rescue':                    params.get('detail_rescue', 0),
        'substrate_tolerance':              params.get('substrate_tolerance', 2.0),
        'ignore_transparent':               params.get('ignore_transparent', True),
    }
    result['_archetype_scores'] = _get_archetype_scores(config)

    return result

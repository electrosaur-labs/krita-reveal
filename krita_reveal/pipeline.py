"""
pipeline.py — Krita pixel I/O + pyreveal integration.
Numpy-accelerated for high performance.
"""

from __future__ import annotations
import struct
import time

_PSEUDO_ARCHETYPES = {
    'dynamic_interpolator': {'name': 'Chameleon',  'min_colors': 4,  'max_colors': 10},
    'distilled':            {'name': 'Distilled',  'min_colors': 6,  'max_colors': 14},
    'salamander':           {'name': 'Salamander', 'min_colors': 4,  'max_colors': 10},
}

def krita_pixels_to_pyreveal(raw: bytes, pixel_count: int):
    """Decode Krita LABA U16 raw bytes → flat pyreveal 16-bit Lab sequence."""
    try:
        import numpy as np
        # Each pixel is 4x uint16 (L, a, b, alpha). We want only LAB.
        arr = np.frombuffer(raw, dtype=np.uint16).reshape(-1, 4)[:, :3]
        # pyreveal engine16 = Krita U16 >> 1
        arr >>= 1
        # Keep as numpy array for vectorized Step 1 in engine!
        return arr.flatten()
    except ImportError:
        result = [0] * (pixel_count * 3)
        for i in range(pixel_count):
            off = i * 8
            L, a, b = struct.unpack_from('<HHH', raw, off)
            j = i * 3
            result[j], result[j + 1], result[j + 2] = L >> 1, a >> 1, b >> 1
        return result

def read_document_raw(doc):
    width, height = doc.width(), doc.height()
    raw = bytes(doc.rootNode().projectionPixelData(0, 0, width, height))
    return raw, width, height

def read_document_pixels(doc):
    """Convenience: read raw and decode to pyreveal 16-bit sequence (numpy or list)."""
    raw, w, h = read_document_raw(doc)
    pixels = krita_pixels_to_pyreveal(raw, w * h)
    return pixels, w, h

def downsample_pixels_smooth(raw: bytes, width: int, height: int, max_dim: int = 800):
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage
    scale = min(1.0, max_dim / max(width, height))
    if scale >= 1.0:
        return krita_pixels_to_pyreveal(raw, width * height), width, height
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    try:
        img = QImage(raw, width, height, width * 8, QImage.Format_RGBA64)
        scaled = img.scaled(new_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        ptr = scaled.bits()
        ptr.setsize(new_w * new_h * 8)
        return krita_pixels_to_pyreveal(bytes(ptr), new_w * new_h), new_w, new_h
    except:
        # Fallback to simple slice if QImage scaling fails
        pixels = krita_pixels_to_pyreveal(raw, width * height)
        return pixels[:new_w*new_h*3], new_w, new_h

def downsample_pixels(pixels, width: int, height: int, max_dim: int = 800):
    """Old signature compatibility."""
    scale = min(1.0, max_dim / max(width, height))
    if scale >= 1.0: return pixels, width, height
    nw, nh = max(1, round(width * scale)), max(1, round(height * scale))
    return pixels[:nw*nh*3], nw, nh

def make_original_rgb(lab_pixels, width: int, height: int) -> bytes:
    from pyreveal.color.encoding import convert_engine16bit_to_8bit_lab, lab8bit_to_rgb
    n = width * height
    try:
        import numpy as np
        # lab_pixels is likely already a numpy array (uint16)
        arr = np.asarray(lab_pixels, dtype=np.uint16).reshape(-1, 3)
        l8 = (arr[:, 0] >> 7).astype(np.uint8)
        a8 = (arr[:, 1] >> 7).astype(np.uint8)
        b8 = (arr[:, 2] >> 7).astype(np.uint8)
        lab8 = np.stack([l8, a8, b8], axis=1).flatten()
    except ImportError:
        lab8 = convert_engine16bit_to_8bit_lab(lab_pixels, n)
    return bytes(lab8bit_to_rgb(lab8, n))

def make_posterized_rgb(assignments, palette: list, width: int, height: int) -> bytes:
    try:
        import numpy as np
        lut = np.array([(c['r'], c['g'], c['b']) for c in palette], dtype=np.uint8)
        # assignments is likely a bytearray/numpy array
        idx = np.asarray(assignments, dtype=np.intp)
        # Vectorized lookup
        rgb = lut[idx]
        return rgb.tobytes()
    except ImportError:
        lut = [(c['r'], c['g'], c['b']) for c in palette]
        n = width * height
        data = bytearray(n * 3)
        for i in range(n):
            idx = assignments[i]
            r, g, b = lut[idx] if idx < len(lut) else (0, 0, 0)
            j = i * 3
            data[j], data[j + 1], data[j + 2] = r, g, b
        return bytes(data)

def make_solo_rgb(assignments, palette: list, color_index: int, width: int, height: int) -> bytes:
    try:
        import numpy as np
        n = width * height
        data = np.full((n, 3), 35, dtype=np.uint8)
        idx = np.asarray(assignments)
        mask = (idx == color_index)
        c = palette[color_index]
        data[mask] = [c['r'], c['g'], c['b']]
        return data.tobytes()
    except ImportError:
        lut = [(c['r'], c['g'], c['b']) for c in palette]
        n = width * height; data = bytearray(n * 3)
        for i in range(n):
            j = i * 3
            if assignments[i] == color_index:
                r, g, b = lut[color_index]
                data[j], data[j + 1], data[j + 2] = r, g, b
            else: data[j] = data[j+1] = data[j+2] = 35
        return bytes(data)

def _get_archetype_scores(config: dict) -> list:
    from pyreveal.analysis.archetype_loader import ArchetypeLoader
    ranking = (config.get('meta') or {}).get('match_ranking') or []
    arch_by_id = {a['id']: a for a in ArchetypeLoader.load_archetypes()}
    scores = []
    if ranking:
        scores = [{
            'id': m['id'], 'name': arch_by_id.get(m['id'], {}).get('name', m['id']),
            'group': arch_by_id.get(m['id'], {}).get('group', ''), 'score': m['score'],
            'min_colors': arch_by_id.get(m['id'], {}).get('parameters', {}).get('minColors', 4),
            'max_colors': arch_by_id.get(m['id'], {}).get('parameters', {}).get('maxColors', 8)
        } for m in ranking]
    elif config.get('id'):
        scores.append({
            'id': config['id'], 'name': config.get('name', config['id']),
            'group': '', 'score': 100.0, 'min_colors': 4, 'max_colors': 12
        })
    
    pscore = min(70.0, scores[0]['score'] if scores else 70.0)
    for pid, pdef in _PSEUDO_ARCHETYPES.items():
        if not any(s['id'] == pid for s in scores):
            scores.append({'id': pid, 'name': pdef['name'], 'group': 'adaptive', 'score': pscore, 'min_colors': pdef['min_colors'], 'max_colors': pdef['max_colors']})
            pscore = max(0.0, pscore - 1.0)
    return scores

def run_separation(pixels, width: int, height: int, target_colors: int = 6, options: dict = None) -> dict:
    import pyreveal
    from pyreveal.analysis.parameter_generator import ParameterGenerator
    opts = dict(options) if options else {}
    arch_id = opts.pop('_archetype_id', None)
    pre_intensity = opts.pop('_preprocessing_intensity', 'off')
    dna = pyreveal.analyze_image(pixels, width, height, {'bit_depth': 16})
    mechanical = {}
    if arch_id is not None:
        manual_id = arch_id if arch_id != '__auto__' else None
        if manual_id in ('dynamic_interpolator', 'salamander'):
            from pyreveal.analysis.interpolator_engine import get_engine
            interp = get_engine().interpolate(dna.get('global', dna))
            from .pipeline import _interpolator_to_config
            config = _interpolator_to_config(interp['parameters'], dna)
        elif manual_id == 'distilled':
            config = pyreveal.generate_configuration(dna)
            config['id'], config['name'] = 'distilled', 'Distilled'
        else:
            config = pyreveal.generate_configuration(dna, {'manual_archetype_id': manual_id} if manual_id else None)
        
        mechanical = {
            'density_floor': opts.get('density_floor', config.get('min_volume', 0) / 100.0),
            'speckle_rescue': opts.get('speckle_rescue', config.get('speckle_rescue', 0)),
            'shadow_clamp': opts.get('shadow_clamp', config.get('shadow_clamp', 0))
        }
        if target_colors == 0: target_colors = config.get('target_colors', 6)
        params = ParameterGenerator.to_engine_options(config, mechanical)
        for k in ['vibrancy_boost','vibrancy_mode','l_weight','c_weight','black_bias','shadow_point','palette_reduction','enable_palette_reduction','enable_hue_gap_analysis','hue_lock_angle','substrate_mode','preserve_white','preserve_black','engine_type','color_mode','dither_type','distance_metric','centroid_strategy','split_mode','quantizer','neutral_sovereignty_threshold','chroma_gate','highlight_threshold','highlight_boost','median_pass','detail_rescue','substrate_tolerance','ignore_transparent']:
            if k in opts: params[k] = opts[k]
        params['target_colors'] = target_colors
        params['skip_assignment'] = True
    else:
        params = {'substrate_mode': 'none', 'skip_assignment': True}; params.update(opts)
        config = pyreveal.generate_configuration(dna)

    if pre_intensity != 'off':
        from pyreveal.preprocessing.bilateral_filter import create_preprocessing_config
        pre_c = create_preprocessing_config(dna, pixels, width, height, intensity_override=pre_intensity)
        if pre_c.get('enabled'): pyreveal.preprocess_image(pixels, width, height, pre_c)
    
    res = pyreveal.posterize_image(pixels, width, height, target_colors, params)
    from pyreveal.engines.separation import SeparationEngine as SE
    res['assignments'] = SE.map_pixels_to_palette(pixels, res['palette_lab'], width, height, {'dither_type': params.get('dither_type', 'none'), 'distance_metric': params.get('distance_metric', 'cie76'), 'mesh_count': opts.get('mesh_size')})
    res['_matched_archetype'] = {
        'id': config.get('id',''), 
        'name': config.get('name',''), 
        'colors': target_colors, 
        'density': round(mechanical.get('density_floor',0)*100,1), 
        'speckle': mechanical.get('speckle_rescue',0), 
        'vibrancy_boost': params.get('vibrancy_boost',1.4),
        'dither_type': params.get('dither_type', 'none')
    }
    res['_archetype_scores'] = _get_archetype_scores(config)
    return res

def _interpolator_to_config(interp_params: dict, dna: dict) -> dict:
    config = {
        'target_colors': 6, 'engine_type': 'distilled', 'centroid_strategy': 'SALIENCY',
        'distance_metric': 'cie76', 'dither_type': 'atkinson', 'l_weight': 1.2,
        'c_weight': 2.0, 'b_weight': 1.0, 'black_bias': 3.0, 'vibrancy_mode': 'moderate',
        'vibrancy_boost': 1.4, 'highlight_threshold': 90, 'highlight_boost': 1.5,
        'palette_reduction': 6.0, 'enable_palette_reduction': True, 'substrate_mode': 'auto',
        'substrate_tolerance': 2.0, 'enable_hue_gap_analysis': True, 'hue_lock_angle': 20,
        'shadow_point': 15, 'color_mode': 'color', 'preserve_white': True,
        'preserve_black': True, 'ignore_transparent': True, 'shadow_clamp': 0,
        'chroma_gate': 1.0, 'detail_rescue': 0, 'speckle_rescue': 0, 'median_pass': False,
        'min_volume': 0, 'shadow_chroma_gate_l': 0, 'neutral_centroid_clamp_threshold': 0.5,
        'neutral_sovereignty_threshold': 0, 'chroma_axis_weight': 0,
        'neutral_isolation_threshold': 0, 'warm_a_boost': 1.0, 'peak_finder_max_peaks': 1,
        'peak_finder_blacklisted_sectors': [3, 4], 'refinement_passes': 1,
        'split_mode': 'median', 'quantizer': 'wu'
    }
    from .pipeline import _CAMEL_TO_SNAKE
    for c, s in _CAMEL_TO_SNAKE.items():
        if c in interp_params and interp_params[c] is not None:
            config[s] = interp_params[c]
    return config

_CAMEL_TO_SNAKE = {'lWeight':'l_weight','cWeight':'c_weight','blackBias':'black_bias','vibrancyBoost':'vibrancy_boost','vibrancyMode':'vibrancy_mode','highlightThreshold':'highlight_threshold','highlightBoost':'highlight_boost','paletteReduction':'palette_reduction','enablePaletteReduction':'enable_palette_reduction','substrateTolerance':'substrate_tolerance','substrateMode':'substrate_mode','hueLockAngle':'hue_lock_angle','enableHueGapAnalysis':'enable_hue_gap_analysis','shadowPoint':'shadow_point','colorMode':'color_mode','preserveWhite':'preserve_white','preserve_black':'preserve_black','centroidStrategy':'centroid_strategy','splitMode':'split_mode','quantizer':'quantizer','neutralSovereigntyThreshold':'neutral_sovereignty_threshold','chromaGate':'chroma_gate','detailRescue':'detail_rescue','speckleRescue':'speckle_rescue','medianPass':'median_pass','distanceMetric':'distance_metric','ditherType':'dither_type','maxColors':'target_colors'}

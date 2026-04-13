"""
PosterizationEngine — colour quantization for screen printing.

Reduces images to limited colour palettes (3–9 colours) using Lab-space
median cut.  Optimised for screen-printing workflow.

Engine types:
  'reveal'       Lab median cut + hue gap analysis (default, highest quality)
  'balanced'     Lab median cut without hue gap analysis
  'stencil'      Luminance-only quantization (monochrome)
  'classic'      RGB median cut (not yet ported — raises NotImplementedError)
  'reveal-mk1.5' Legacy engine (RevealMk15Engine port)
  'distilled'    Over-quantize + furthest-point sampling (PaletteDistiller port)

Input:  list/array of 16-bit Lab engine values (3 per pixel),
        or RGBA uint8 array when format='rgb'.
Output: dict {palette, palette_lab, assignments, lab_pixels,
              substrate_lab, substrate_index, metadata}.
"""

from __future__ import annotations

import math
import time

from ..color.encoding import (
    LAB16_L_MAX,
    LAB16_AB_NEUTRAL,
    L_SCALE,
    AB_SCALE,
    lab_to_rgb_d50 as lab_to_rgb,
    rgb_to_lab,
)
from .hue_gap_recovery import (
    _analyze_image_hue_sectors,
    _analyze_palette_hue_coverage,
    _identify_hue_gaps,
    _find_true_missing_hues,
)
from .lab_median_cut import median_cut_in_lab_space, _analyze_color_space
from .palette_ops import (
    apply_perceptual_snap,
    _prune_palette,
    _apply_density_floor,
    _refine_k_means,
    _get_adaptive_snap_threshold,
    _lab_distance,
)
from .centroid_strategies import CENTROID_STRATEGIES
from .reveal_mk15_engine import posterize_mk15
from .palette_distiller import over_quantize_count, distill as distill_palette


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MIN_PRESERVED_COVERAGE = 0.001       # 0.1% — minimum pixels for a preserved slot
MIN_HUE_COVERAGE = 0.01             # 1.0% — minimum sector coverage to inject gap colour
PRESERVED_UNIFY_THRESHOLD = 12.0    # ΔE below which a preserved colour unifies with an existing one

TUNING = {
    'split': {
        'highlightBoost': 2.2,
        'vibrancyBoost': 1.6,
        'minVariance': 10,
        'chromaAxisWeight': 0,
        'neutralIsolationThreshold': 0,
    },
    'prune': {
        'threshold': 9.0,
        'hueLockAngle': 18,
        'whitePoint': 85,
        'shadowPoint': 15,
    },
    'centroid': {
        'lWeight': 1.1,
        'cWeight': 2.0,
        'blackBias': 5.0,
    },
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _normalize_bit_depth(input_val) -> int:
    """Normalise bitDepth to an integer (default 8)."""
    if isinstance(input_val, int):
        return input_val
    if isinstance(input_val, str):
        digits = ''.join(c for c in input_val if c.isdigit())
        if digits:
            return int(digits)
    return 8


def _build_tuning_from_config(options: dict) -> dict:
    """Map flat options fields to the nested tuning structure.

    Uses explicit None-checks so that a key present with value None falls
    back to the TUNING default rather than propagating None into the engine.
    """
    def _o(key, default):
        v = options.get(key)
        return v if v is not None else default

    return {
        'split': {
            'highlightBoost':           _o('highlight_boost',          TUNING['split']['highlightBoost']),
            'vibrancyBoost':            _o('vibrancy_boost',           TUNING['split']['vibrancyBoost']),
            'minVariance':              TUNING['split']['minVariance'],
            'chromaAxisWeight':         _o('chroma_axis_weight',       TUNING['split']['chromaAxisWeight']),
            'neutralIsolationThreshold': _o('neutral_isolation_threshold', TUNING['split']['neutralIsolationThreshold']),
            'warmABoost':               _o('warm_a_boost',             1.0),
            'splitMode':                _o('split_mode',               'median'),
            'quantizer':                _o('quantizer',                'median-cut'),
        },
        'prune': {
            'threshold':         _o('palette_reduction',   TUNING['prune']['threshold']),
            'hueLockAngle':      _o('hue_lock_angle',      TUNING['prune']['hueLockAngle']),
            'whitePoint':        _o('highlight_threshold', TUNING['prune']['whitePoint']),
            'shadowPoint':       _o('shadow_point',        TUNING['prune']['shadowPoint']),
            'isolationThreshold': _o('isolation_threshold', 0.0),
        },
        'centroid': {
            'lWeight':      _o('l_weight',     TUNING['centroid']['lWeight']),
            'cWeight':      _o('c_weight',     TUNING['centroid']['cWeight']),
            'bWeight':      _o('b_weight',     1.0),
            'blackBias':    _o('black_bias',   TUNING['centroid']['blackBias']),
            'bitDepth':     _normalize_bit_depth(_o('bit_depth', 8)),
            'vibrancyMode': _o('vibrancy_mode', 'aggressive'),
            'vibrancyBoost': _o('vibrancy_boost', 2.2),
        },
    }


def _lab_dict_to_rgb_dict(lab: dict) -> dict:
    """Convert {L, a, b} perceptual dict to {r, g, b} uint8 dict."""
    r, g, b = lab_to_rgb(lab['L'], lab['a'], lab['b'])
    return {'r': r, 'g': g, 'b': b}


# ---------------------------------------------------------------------------
# Substrate detection
# ---------------------------------------------------------------------------

def auto_detect_substrate(lab_bytes, width: int, height: int, bit_depth: int = 16) -> dict:
    """Sample corners of a 16-bit Lab image to detect the substrate colour.

    lab_bytes: flat sequence of 16-bit Lab engine values (3 per pixel).
    Returns {L, a, b} in perceptual space.
    """
    SAMPLE_SIZE = 10
    sum_l = sum_a = sum_b = count = 0

    def _sample(x, y):
        nonlocal sum_l, sum_a, sum_b, count
        i = (y * width + x) * 3
        if i + 2 >= len(lab_bytes):
            return
        sum_l += lab_bytes[i] / L_SCALE
        sum_a += (lab_bytes[i + 1] - LAB16_AB_NEUTRAL) / AB_SCALE
        sum_b += (lab_bytes[i + 2] - LAB16_AB_NEUTRAL) / AB_SCALE
        count += 1

    for y in range(min(SAMPLE_SIZE, height)):
        for x in range(min(SAMPLE_SIZE, width)):
            _sample(x, y)
            _sample(width - 1 - x, y)
            _sample(x, height - 1 - y)
            _sample(width - 1 - x, height - 1 - y)

    if count == 0:
        return {'L': 100, 'a': 0, 'b': 0}
    return {'L': sum_l / count, 'a': sum_a / count, 'b': sum_b / count}


# ---------------------------------------------------------------------------
# Palette utilities
# ---------------------------------------------------------------------------

def palette_to_hex(palette: list) -> list:
    """Convert list of {r, g, b} dicts to uppercase hex strings."""
    result = []
    for color in palette:
        result.append(f"#{color['r']:02X}{color['g']:02X}{color['b']:02X}")
    return result


# ---------------------------------------------------------------------------
# Reveal Mk 1.0 engine (the core algorithm)
# ---------------------------------------------------------------------------

def _posterize_reveal_mk1_0(pixels, width: int, height: int, target_colors: int, options: dict) -> dict:
    """Lab-space median cut with optional hue gap analysis.

    pixels: flat sequence of 16-bit Lab engine values (format='lab')
            or RGBA uint8 values (format='rgb').
    """
    distance_metric = options.get('distance_metric', 'cie76')
    is_legacy_v1 = distance_metric == 'cie76'

    snap_threshold = options.get('snap_threshold', 8.0)
    enable_palette_reduction = options.get('enable_palette_reduction', True)
    palette_reduction = options.get('palette_reduction', 8.0)
    preserved_unify_threshold = options.get('preserved_unify_threshold', PRESERVED_UNIFY_THRESHOLD)
    density_floor = options.get('density_floor', 0.005)

    if is_legacy_v1:
        snap_threshold = 0.0
        enable_palette_reduction = False
        preserved_unify_threshold = 0.5
        density_floor = 0.0

    enable_hue_gap = options.get('enable_hue_gap_analysis', False)
    grayscale_only = options.get('grayscale_only', False)
    preserve_white = options.get('preserve_white', False)
    preserve_black = options.get('preserve_black', False)
    vibrancy_mode = options.get('vibrancy_mode', 'aggressive')
    vibrancy_boost = options.get('vibrancy_boost', 2.0)
    highlight_threshold = options.get('highlight_threshold', 92)
    highlight_boost = options.get('highlight_boost', 3.0)
    strategy = options.get('strategy')
    tuning = options.get('tuning')

    start_time = time.perf_counter()

    is_lab_input = options.get('format', 'lab') == 'lab'
    source_bit_depth = options.get('bit_depth', 16)
    is_8bit_source = source_bit_depth <= 8

    # ── Step 1: Decode to perceptual Lab ─────────────────────────────────
    lab_pixels = []
    transparent_pixels = set()

    shadow_threshold = 7.5 if is_8bit_source else 6.0
    hi_threshold = 97.5 if is_8bit_source else 98.0

    if is_lab_input:
        for i in range(0, len(pixels), 3):
            L = pixels[i] / L_SCALE
            a = (pixels[i + 1] - LAB16_AB_NEUTRAL) / AB_SCALE
            b = (pixels[i + 2] - LAB16_AB_NEUTRAL) / AB_SCALE

            if L < shadow_threshold:
                L, a, b = 0.0, 0.0, 0.0
            elif L > hi_threshold:
                L, a, b = 100.0, 0.0, 0.0

            lab_pixels.extend([L, a, b])
    else:
        # RGBA uint8 input
        ALPHA_THRESHOLD = 10
        j = 0
        for i in range(0, len(pixels), 4):
            alpha = pixels[i + 3]
            if alpha < ALPHA_THRESHOLD:
                transparent_pixels.add(j)
            L, a, b = rgb_to_lab(pixels[i], pixels[i + 1], pixels[i + 2])
            if L < shadow_threshold:
                L, a, b = 0.0, 0.0, 0.0
            elif L > hi_threshold:
                L, a, b = 100.0, 0.0, 0.0
            lab_pixels.extend([L, a, b])
            j += 1

    # ── Step 1.5: Separate preserved colours ─────────────────────────────
    total_pixels = len(lab_pixels) // 3
    preserved_pixel_map = {}   # 'white'/'black' → set of pixel indices
    non_preserved_indices = []
    actual_target_colors = target_colors

    AB_THRESHOLD = 5.0 if is_8bit_source else 0.01
    WHITE_L_MIN = 95
    BLACK_L_MAX = 10

    if preserve_white or preserve_black or transparent_pixels:
        for i in range(0, len(lab_pixels), 3):
            idx = i // 3
            if idx in transparent_pixels:
                continue
            L = lab_pixels[i]
            a = lab_pixels[i + 1]
            b = lab_pixels[i + 2]
            preserved = False

            if preserve_white and L > WHITE_L_MIN and abs(a) < AB_THRESHOLD and abs(b) < AB_THRESHOLD:
                preserved_pixel_map.setdefault('white', set()).add(idx)
                preserved = True
            elif preserve_black and L < BLACK_L_MAX and abs(a) < AB_THRESHOLD and abs(b) < AB_THRESHOLD:
                preserved_pixel_map.setdefault('black', set()).add(idx)
                preserved = True

            if not preserved:
                non_preserved_indices.append(idx)

        num_preserved = (1 if preserve_white else 0) + (1 if preserve_black else 0)
        if num_preserved > 0:
            actual_target_colors = target_colors - num_preserved
    else:
        for i in range(total_pixels):
            if i not in transparent_pixels:
                non_preserved_indices.append(i)

    # Extract non-preserved pixels into their own flat list
    if len(non_preserved_indices) < total_pixels:
        np_pixels = []
        for idx in non_preserved_indices:
            off = idx * 3
            np_pixels.extend([lab_pixels[off], lab_pixels[off + 1], lab_pixels[off + 2]])
    else:
        np_pixels = lab_pixels

    # ── Step 1.5: Substrate detection ────────────────────────────────────
    substrate_lab = None
    substrate_mode = options.get('substrate_mode', 'auto')

    if is_lab_input and substrate_mode != 'none':
        if substrate_mode in ('auto', None, ''):
            substrate_lab = auto_detect_substrate(pixels, width, height, source_bit_depth)
        elif substrate_mode == 'white':
            substrate_lab = {'L': 100, 'a': 0, 'b': 0}
        elif substrate_mode == 'black':
            substrate_lab = {'L': 0, 'a': 0, 'b': 0}
        elif options.get('substrate_lab'):
            substrate_lab = options['substrate_lab']

    median_cut_target = actual_target_colors + (1 if substrate_lab else 0)

    # ── Step 2: Median cut ───────────────────────────────────────────────
    mc_result = median_cut_in_lab_space(
        np_pixels,
        max(1, median_cut_target),
        grayscale_only,
        width,
        height,
        substrate_lab,
        options.get('substrate_tolerance', 3.5),
        vibrancy_mode,
        vibrancy_boost,
        highlight_threshold,
        highlight_boost,
        strategy,
        tuning,
    )
    initial_palette_lab = mc_result['palette']
    all_colors = mc_result['all_colors']

    # ── Step 2.5: K-means refinement ─────────────────────────────────────
    split_mode = (tuning or {}).get('split', {}).get('splitMode', 'median')
    default_passes = 3 if split_mode == 'variance' else 1
    refinement_passes = options.get('refinement_passes', default_passes)

    if not grayscale_only and len(initial_palette_lab) > 1 and refinement_passes > 0:
        for _ in range(refinement_passes):
            initial_palette_lab = _refine_k_means(np_pixels, initial_palette_lab, tuning)

    # ── Step 3: Adaptive perceptual snap ─────────────────────────────────
    color_space = _analyze_color_space(lab_pixels)
    is_grayscale = grayscale_only or color_space['chroma_range'] < 10

    l_range = 0.0
    color_space_extent = None

    if is_grayscale:
        min_l = min(lab_pixels[i] for i in range(0, len(lab_pixels), 3))
        max_l = max(lab_pixels[i] for i in range(0, len(lab_pixels), 3))
        l_range = max_l - min_l
    else:
        min_l = min_a = min_b = float('inf')
        max_l = max_a = max_b = float('-inf')
        for i in range(0, len(lab_pixels), 3):
            L = lab_pixels[i]; a = lab_pixels[i + 1]; b = lab_pixels[i + 2]
            if L < min_l: min_l = L
            if L > max_l: max_l = L
            if a < min_a: min_a = a
            if a > max_a: max_a = a
            if b < min_b: min_b = b
            if b > max_b: max_b = b
        color_space_extent = {
            'lRange': max_l - min_l,
            'aRange': max_a - min_a,
            'bRange': max_b - min_b,
        }

    adaptive_threshold = _get_adaptive_snap_threshold(
        snap_threshold, target_colors, is_grayscale, l_range, color_space_extent
    )
    curated = apply_perceptual_snap(
        initial_palette_lab, adaptive_threshold, is_grayscale,
        vibrancy_boost, strategy, tuning,
    )

    # ── Palette reduction ────────────────────────────────────────────────
    if enable_palette_reduction and len(curated) > target_colors:
        pruned = _prune_palette(curated, palette_reduction, highlight_threshold, target_colors, tuning, distance_metric)
        if len(pruned) < len(curated):
            curated = pruned

    if enable_palette_reduction:
        dedup_threshold = max(palette_reduction, 2.0)
        dedup = _prune_palette(curated, dedup_threshold, highlight_threshold, 0, tuning, distance_metric)
        if len(dedup) < len(curated):
            curated = dedup

    # ── Hue gap analysis (after snap and pruning) ────────────────────────
    if enable_hue_gap and not grayscale_only and all_colors:
        hue_chroma_threshold = 1.0 if vibrancy_mode == 'exponential' else 5.0
        image_hues = _analyze_image_hue_sectors(lab_pixels, hue_chroma_threshold)
        coverage_result = _analyze_palette_hue_coverage(curated, hue_chroma_threshold)
        covered = coverage_result['covered_sectors']
        counts_by_sector = coverage_result['color_counts_by_sector']
        gaps = _identify_hue_gaps(image_hues, covered, counts_by_sector)
        gaps.sort(key=lambda s: image_hues[s], reverse=True)

        if gaps:
            num_preserved_slots = (1 if preserve_white else 0) + (1 if preserve_black else 0)
            available_slots = actual_target_colors - len(curated) - num_preserved_slots
            gaps_to_fill = gaps[:max(available_slots, 3)] if available_slots <= 0 else gaps[:available_slots]

            candidates = _find_true_missing_hues(lab_pixels, curated, gaps_to_fill)

            MIN_GAP_DISTANCE = 15.0
            forced = [
                c for c in candidates
                if all(_lab_distance(c, p) >= MIN_GAP_DISTANCE for p in curated)
            ]

            if forced:
                for c in forced:
                    c['_min_volume_exempt'] = True
                curated = curated + forced

    # ── Final safety-net dedup ───────────────────────────────────────────
    final_dedup_threshold = max(palette_reduction, 2.0) if enable_palette_reduction else 2.0
    dedup_final = _prune_palette(curated, final_dedup_threshold, highlight_threshold, 0, tuning, distance_metric)
    if len(dedup_final) < len(curated):
        curated = dedup_final

    # ── Step 3.5: Add preserved colours ─────────────────────────────────
    preserved_colors = []
    actually_preserved_white = actually_preserved_black = False

    if preserve_white:
        white_count = len(preserved_pixel_map.get('white', set()))
        if white_count / total_pixels >= MIN_PRESERVED_COVERAGE:
            absolute_white = {'L': 100, 'a': 0, 'b': 0}
            existing = next(
                (c for c in curated if _lab_distance(c, absolute_white) < preserved_unify_threshold),
                None,
            )
            if not existing:
                preserved_colors.append(absolute_white)
                actually_preserved_white = True

    if preserve_black:
        black_count = len(preserved_pixel_map.get('black', set()))
        if black_count / total_pixels >= MIN_PRESERVED_COVERAGE:
            absolute_black = {'L': 0, 'a': 0, 'b': 0}
            existing = next(
                (c for c in curated if _lab_distance(c, absolute_black) < preserved_unify_threshold),
                None,
            )
            if not existing:
                preserved_colors.append(absolute_black)
                actually_preserved_black = True

    # ── Step 3.6: Add substrate colour ───────────────────────────────────
    substrate_colors = []
    if substrate_lab and 6.0 <= substrate_lab['L'] <= 98.0:
        DUPE_THRESHOLD = 3.0

        # Duplicate check must respect grayscale_only
        is_dup = False
        for p in preserved_colors:
            dL = substrate_lab['L'] - p['L']
            dA = 0 if grayscale_only else (substrate_lab['a'] - p['a'])
            dB = 0 if grayscale_only else (substrate_lab['b'] - p['b'])
            deltaE = math.sqrt(dL*dL + dA*dA + dB*dB)
            if deltaE < DUPE_THRESHOLD:
                is_dup = True
                break

        if not is_dup:
            final_substrate = {
                'L': substrate_lab['L'],
                'a': 0.0 if grayscale_only else substrate_lab['a'],
                'b': 0.0 if grayscale_only else substrate_lab['b']
            }
            substrate_colors.append(final_substrate)


    # ── Final palette assembly ────────────────────────────────────────────
    final_palette_lab = curated + preserved_colors + substrate_colors
    palette_rgb = [_lab_dict_to_rgb_dict(c) for c in final_palette_lab]

    # ── Step 5: Pixel assignment ─────────────────────────────────────────
    palette_size = len(final_palette_lab)
    preserved_color_idx = len(curated)
    white_index = preserved_color_idx if actually_preserved_white else -1
    if actually_preserved_white:
        preserved_color_idx += 1
    black_index = preserved_color_idx if actually_preserved_black else -1

    is_preview = options.get('is_preview', False)
    use_stride = is_preview and options.get('optimize_preview', True)
    ASSIGNMENT_STRIDE = options.get('preview_stride', 4) if use_stride else 1

    white_set = preserved_pixel_map.get('white', set())
    black_set = preserved_pixel_map.get('black', set())

    # Pre-convert palette to 16-bit integer space for fast Lab distance
    palette16 = [
        {
            'L': (p['L'] / 100) * LAB16_L_MAX,
            'a': p['a'] * AB_SCALE + LAB16_AB_NEUTRAL,
            'b': p['b'] * AB_SCALE + LAB16_AB_NEUTRAL,
        }
        for p in final_palette_lab
    ]

    assignments = bytearray(total_pixels)

    for y in range(0, height, ASSIGNMENT_STRIDE):
        row_offset = y * width
        for x in range(0, width, ASSIGNMENT_STRIDE):
            anchor_i = row_offset + x

            if anchor_i in transparent_pixels:
                anchor_assign = 255
            else:
                assigned_preserved = False

                if actually_preserved_white and anchor_i in white_set:
                    anchor_assign = white_index
                    assigned_preserved = True
                elif actually_preserved_black and anchor_i in black_set:
                    anchor_assign = black_index
                    assigned_preserved = True

                if not assigned_preserved:
                    min_dist = float('inf')
                    anchor_assign = 0
                    idx = anchor_i * 3

                    if is_lab_input:
                        # 16-bit integer fast path
                        raw_l = pixels[idx]
                        raw_a = pixels[idx + 1]
                        raw_b = pixels[idx + 2]

                        for j in range(palette_size):
                            t = palette16[j]
                            dL = raw_l - t['L']
                            dA = raw_a - t['a']
                            dB = raw_b - t['b']
                            dist = (1.0 * dL * dL) if grayscale_only else (1.5 * dL * dL + dA * dA + dB * dB)
                            if dist < min_dist:
                                min_dist = dist
                                anchor_assign = j
                    else:
                        # Perceptual Lab float path
                        pL = lab_pixels[idx]
                        pA = lab_pixels[idx + 1]
                        pB = lab_pixels[idx + 2]

                        for j, tgt in enumerate(final_palette_lab):
                            dL = pL - tgt['L']
                            dA = pA - tgt['a']
                            dB = pB - tgt['b']
                            dist = (dL * dL) if grayscale_only else (1.5 * dL * dL + dA * dA + dB * dB)
                            if dist < min_dist:
                                min_dist = dist
                                anchor_assign = j

            # Fill the stride block
            for bY in range(ASSIGNMENT_STRIDE):
                if y + bY >= height:
                    break
                fill_row = (y + bY) * width
                for bX in range(ASSIGNMENT_STRIDE):
                    if x + bX >= width:
                        break
                    assignments[fill_row + (x + bX)] = anchor_assign

    duration = time.perf_counter() - start_time

    # ── Density floor ────────────────────────────────────────────────────
    density_floor_threshold = options.get('density_floor', 0.005 if not is_legacy_v1 else 0.0)

    if density_floor_threshold > 0:
        protected = set()
        if actually_preserved_white:
            protected.add(white_index)
        if actually_preserved_black:
            protected.add(black_index)
        if substrate_colors:
            protected.add(len(final_palette_lab) - 1)

        density_result = _apply_density_floor(assignments, final_palette_lab, density_floor_threshold, protected)

        if density_result['actual_count'] < len(final_palette_lab):
            final_palette_lab = density_result['palette']
            assignments = density_result['assignments']
            palette_rgb = [_lab_dict_to_rgb_dict(c) for c in final_palette_lab]

    substrate_index = (
        len(curated) + len(preserved_colors)
        if substrate_colors else None
    )

    return {
        'palette': palette_rgb,
        'palette_lab': final_palette_lab,
        'assignments': assignments,
        'lab_pixels': lab_pixels,
        'substrate_lab': substrate_lab,
        'substrate_index': substrate_index,
        'metadata': {
            'target_colors': target_colors,
            'final_colors': len(final_palette_lab),
            'snap_threshold': snap_threshold,
            'duration': round(duration, 3),
        },
    }


# ---------------------------------------------------------------------------
# Engine wrappers
# ---------------------------------------------------------------------------

def _posterize_balanced(pixels, width: int, height: int, target_colors: int, options: dict) -> dict:
    """Balanced: Reveal Mk 1.0 without hue gap analysis."""
    return _posterize_reveal_mk1_0(pixels, width, height, target_colors, {
        **options,
        'enable_hue_gap_analysis': False,
    })


def _posterize_stencil(pixels, width: int, height: int, target_colors: int, options: dict) -> dict:
    """Stencil: luminance-only (grayscale) quantization."""
    return _posterize_reveal_mk1_0(pixels, width, height, target_colors, {
        **options,
        'grayscale_only': True,
        'enable_hue_gap_analysis': False,
    })


# ---------------------------------------------------------------------------
# Distilled engine (over-quantize + furthest-point sampling)
# ---------------------------------------------------------------------------

def _posterize_distilled(pixels, width: int, height: int, target_colors: int, options: dict) -> dict:
    """Over-quantize then distill to target_colors via furthest-point sampling.

    Uses the mk1.5 engine for over-quantization, matching the JS reference
    (distilledPosterize calls reveal-mk1.5 with snapThreshold=0, densityFloor=0,
    enablePaletteReduction=false). The k-means refinement pass in mk1.5 produces
    more coherent clusters, resulting in smoother final assignments.
    """
    over_k = over_quantize_count(target_colors)

    # Over-quantize with mk1.5: disable all merging so we get full hue resolution.
    # snap_threshold=0 and density_floor=0 prevent premature color collapse.
    over_options = {
        **options,
        'enable_hue_gap_analysis':  False,
        'enable_palette_reduction': False,
        'snap_threshold':           0,
        'density_floor':            0,
    }
    over_result = posterize_mk15(pixels, width, height, over_k, over_options)

    over_palette = over_result['palette_lab']
    assignments  = over_result['assignments']
    pixel_count  = width * height

    distilled = distill_palette(over_palette, assignments, pixel_count, target_colors)

    reduced_palette_lab = distilled['palette']
    remap               = distilled['remap']

    # Remap assignments (255 = transparent — leave unchanged)
    new_assignments = bytearray(len(assignments))
    for i, idx in enumerate(assignments):
        new_assignments[i] = remap[idx] if idx < len(remap) else idx

    reduced_palette_rgb = [_lab_dict_to_rgb_dict(c) for c in reduced_palette_lab]

    return {
        'palette':       reduced_palette_rgb,
        'palette_lab':   reduced_palette_lab,
        'assignments':   new_assignments,
        'lab_pixels':    over_result['lab_pixels'],
        'substrate_lab': over_result.get('substrate_lab'),
        'substrate_index': None,
        'metadata': {
            **over_result.get('metadata', {}),
            'target_colors': target_colors,
            'final_colors':  len(reduced_palette_lab),
            'over_k':        over_k,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def posterize(pixels, width: int, height: int, target_colors: int, options: dict | None = None) -> dict:
    """Posterize an image using the selected engine.

    pixels:        flat sequence of 16-bit Lab engine values (3 per pixel)
                   when options['format']='lab' (default), or RGBA uint8
                   values when format='rgb'.
    width/height:  image dimensions.
    target_colors: desired palette size (1-20).
    options:       see module docstring for full option list.

    Returns dict:
      palette         list of {r, g, b} dicts
      palette_lab     list of {L, a, b} dicts
      assignments     bytearray of palette indices (255 = transparent)
      lab_pixels      flat [L, a, b, ...] perceptual float list
      substrate_lab   {L, a, b} or None
      substrate_index int or None
      metadata        {target_colors, final_colors, snap_threshold, duration}
    """
    if options is None:
        options = {}

    if not pixels:
        raise ValueError('posterize: pixels must not be empty')
    if not (isinstance(width, int) and isinstance(height, int) and width >= 1 and height >= 1):
        raise ValueError(f'posterize: width and height must be positive integers (got {width}x{height})')
    if not (isinstance(target_colors, int) and 1 <= target_colors <= 20):
        raise ValueError(f'posterize: target_colors must be 1-20 (got {target_colors})')

    engine_type = options.get('engine_type', 'reveal')
    snap_threshold = options.get('snap_threshold', 8.0)
    enable_hue_gap = options.get('enable_hue_gap_analysis', False)

    # Strategy selection
    strategy_name = options.get('centroid_strategy')
    if not strategy_name:
        strategy_name = 'SALIENCY' if engine_type in ('reveal', 'reveal-mk1.5') else 'VOLUMETRIC'
    strategy = CENTROID_STRATEGIES.get(strategy_name, CENTROID_STRATEGIES['SALIENCY'])

    # Build tuning
    tuning = options.get('tuning') or _build_tuning_from_config(options)

    merged = {
        **options,
        'snap_threshold': snap_threshold,
        'enable_hue_gap_analysis': enable_hue_gap,
        'strategy': strategy,
        'strategy_name': strategy_name,
        'tuning': tuning,
    }

    if engine_type == 'reveal':
        return _posterize_reveal_mk1_0(pixels, width, height, target_colors, merged)

    if engine_type == 'balanced':
        return _posterize_balanced(pixels, width, height, target_colors, {**merged, 'enable_hue_gap_analysis': False})

    if engine_type == 'stencil':
        return _posterize_stencil(pixels, width, height, target_colors, merged)

    if engine_type == 'classic':
        raise NotImplementedError("engine_type='classic' (RGB median cut) is not yet ported to pyreveal")

    if engine_type in ('reveal-mk1.5', 'reveal-mk2'):
        return posterize_mk15(pixels, width, height, target_colors, merged)

    if engine_type == 'distilled':
        return _posterize_distilled(pixels, width, height, target_colors, merged)

    # Unknown engine — fall back to reveal
    return _posterize_reveal_mk1_0(pixels, width, height, target_colors, merged)

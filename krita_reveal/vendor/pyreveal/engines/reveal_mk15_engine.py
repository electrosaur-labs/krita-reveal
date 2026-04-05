"""
RevealMk15Engine — Reveal Mk 1.5 posterization engine.

Port of RevealMk15Engine.js.

Key differences from Mk 1.0 (reveal engine):
  - No substrate detection or culling
  - PeakFinder for identity peak detection → forced centroids injected AFTER
    median cut with ΔE < 3.0 duplicate checking (no wasted slot if MC found it)
  - Neutral sovereignty: extract near-neutral pixels into a fixed 1-slot
    allocation so they don't consume the chromatic split budget
  - K-means refinement passes after median cut
  - Highlight rescue: detect bright warm highlights missed by MC, replace
    lowest-coverage slot
  - Preserved white/black with minimum coverage check
  - Density floor applied last with protected index set
"""

from __future__ import annotations

import math
import time

from ..color.encoding import (
    LAB16_AB_NEUTRAL,
    L_SCALE,
    AB_SCALE,
    lab_to_rgb_d50 as lab_to_rgb,
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
from .hue_gap_recovery import (
    _analyze_image_hue_sectors,
    _analyze_palette_hue_coverage,
    _identify_hue_gaps,
    _find_true_missing_hues,
)
from .peak_finder import PeakFinder


MIN_PRESERVED_COVERAGE = 0.001   # 0.1% minimum pixels for a preserved slot


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def posterize_mk15(pixels, width: int, height: int,
                   target_colors: int, options: dict) -> dict:
    """Reveal Mk 1.5 engine.

    pixels:  flat list of 16-bit Lab engine values (3 per pixel),
             L: 0-32768, a/b: 0-32768 (neutral = 16384).
    Returns the same dict schema as the Mk 1.0 engine.
    """
    distance_metric          = options.get('distance_metric', 'cie76')

    snap_threshold           = options.get('snap_threshold', 8.0)
    enable_palette_reduction = options.get('enable_palette_reduction', True)
    palette_reduction        = options.get('palette_reduction', 8.0)

    # density_floor: explicit key wins; fall back to min_volume (pct→fraction)
    _min_vol      = options.get('min_volume')
    density_floor = options.get('density_floor',
                                (_min_vol / 100.0) if _min_vol is not None else 0.005)

    # CIE76 mode: disable perceptual snap (JS uses no snap for cie76 archetypes)
    if distance_metric == 'cie76':
        snap_threshold = 0.0

    grayscale_only      = options.get('grayscale_only', False)
    preserve_white      = options.get('preserve_white', False)
    preserve_black      = options.get('preserve_black', False)
    vibrancy_mode       = options.get('vibrancy_mode', 'aggressive')
    vibrancy_boost      = options.get('vibrancy_boost', 2.0)
    highlight_threshold = options.get('highlight_threshold', 92)
    highlight_boost     = options.get('highlight_boost', 3.0)
    strategy            = options.get('strategy')
    tuning              = options.get('tuning')

    source_bit_depth = options.get('bit_depth', 16)
    is_8bit_source   = source_bit_depth <= 8

    start_time = time.perf_counter()

    # ── Step 1: Decode 16-bit Lab → perceptual Lab ────────────────────────
    shadow_threshold = 7.5 if is_8bit_source else 6.0
    hi_threshold     = 97.5 if is_8bit_source else 98.0

    lab_pixels = []
    for i in range(0, len(pixels), 3):
        L = pixels[i] / L_SCALE
        a = (pixels[i + 1] - LAB16_AB_NEUTRAL) / AB_SCALE
        b = (pixels[i + 2] - LAB16_AB_NEUTRAL) / AB_SCALE
        if L < shadow_threshold:
            L, a, b = 0.0, 0.0, 0.0
        elif L > hi_threshold:
            L, a, b = 100.0, 0.0, 0.0
        lab_pixels.extend([L, a, b])

    # Optional: hard chroma gate — zeroes a/b for pixels below the threshold.
    # Uses 'chroma_gate_threshold' key (JS: options.chromaGateThreshold).
    # NOTE: 'chroma_gate' is NOT used here — it is the JS chromaGate cWeight
    # multiplier, already applied by ParameterGenerator before reaching this
    # engine.  chromaGateThreshold is never set by any archetype (defaults to 0).
    chroma_gate_threshold = options.get('chroma_gate_threshold', 0)
    if chroma_gate_threshold > 0:
        for i in range(0, len(lab_pixels), 3):
            a = lab_pixels[i + 1]
            b = lab_pixels[i + 2]
            if math.sqrt(a * a + b * b) < chroma_gate_threshold:
                lab_pixels[i + 1] = 0.0
                lab_pixels[i + 2] = 0.0

    # Optional: shadow chroma gate
    shadow_chroma_gate_l = options.get('shadow_chroma_gate_l', 0)
    if shadow_chroma_gate_l > 0:
        for i in range(0, len(lab_pixels), 3):
            if lab_pixels[i] < shadow_chroma_gate_l:
                a = lab_pixels[i + 1]
                b = lab_pixels[i + 2]
                if math.sqrt(a * a + b * b) < 20.0:
                    lab_pixels[i + 1] = 0.0
                    lab_pixels[i + 2] = 0.0

    total_pixels = len(lab_pixels) // 3

    # ── Identity peak detection (PeakFinder) ─────────────────────────────
    _pfmp                          = options.get('peak_finder_max_peaks')
    peak_finder_max_peaks          = _pfmp if _pfmp is not None else 1
    peak_finder_preferred_sectors  = options.get('peak_finder_preferred_sectors')
    _pfbs                          = options.get('peak_finder_blacklisted_sectors')
    peak_finder_blacklisted        = _pfbs if _pfbs is not None else [3, 4]
    forced_centroids_input         = options.get('forced_centroids') or options.get('forcedCentroids')

    forced_centroids       = []
    used_predefined        = False
    detected_peaks         = []

    if forced_centroids_input and isinstance(forced_centroids_input, list):
        try:
            forced_centroids = [
                {
                    'L': float(fc.get('L', fc.get('l', 0))),
                    'a': float(fc.get('a', 0)),
                    'b': float(fc.get('b', 0)),
                }
                for fc in forced_centroids_input
            ]
            used_predefined = True
        except Exception:
            pass

    if not used_predefined:
        pf = PeakFinder({
            'chromaThreshold':    30,
            'volumeThreshold':    0.05,
            'maxPeaks':           peak_finder_max_peaks,
            'preferredSectors':   peak_finder_preferred_sectors,
            'blacklistedSectors': peak_finder_blacklisted,
        })
        detected_peaks = pf.find_identity_peaks(lab_pixels, {'bitDepth': source_bit_depth})
        forced_centroids = [{'L': p['L'], 'a': p['a'], 'b': p['b']} for p in detected_peaks]

    # ── Preserved colour separation ───────────────────────────────────────
    preserved_pixel_map = {}   # 'white' / 'black' → set of pixel indices
    non_preserved_indices = []

    AB_THRESHOLD = 5.0 if is_8bit_source else 0.01
    WHITE_L_MIN  = 95
    BLACK_L_MAX  = 10

    if preserve_white or preserve_black:
        for i in range(0, len(lab_pixels), 3):
            idx = i // 3
            L = lab_pixels[i]
            a = lab_pixels[i + 1]
            b = lab_pixels[i + 2]
            preserved = False

            if (preserve_white and L > WHITE_L_MIN
                    and abs(a) < AB_THRESHOLD and abs(b) < AB_THRESHOLD):
                preserved_pixel_map.setdefault('white', set()).add(idx)
                preserved = True
            elif (preserve_black and L < BLACK_L_MAX
                    and abs(a) < AB_THRESHOLD and abs(b) < AB_THRESHOLD):
                preserved_pixel_map.setdefault('black', set()).add(idx)
                preserved = True

            if not preserved:
                non_preserved_indices.append(idx)
    else:
        non_preserved_indices = list(range(total_pixels))

    # Slot budget: forced centroids are NOT deducted — injected after MC
    num_preserved      = (1 if preserve_white else 0) + (1 if preserve_black else 0)
    median_cut_target  = max(1, target_colors - num_preserved)

    # Extract non-preserved pixels
    if len(non_preserved_indices) < total_pixels:
        np_pixels = []
        for idx in non_preserved_indices:
            off = idx * 3
            np_pixels.extend([lab_pixels[off], lab_pixels[off + 1], lab_pixels[off + 2]])
    else:
        np_pixels = lab_pixels

    # ── Neutral sovereignty ───────────────────────────────────────────────
    neutral_sovereignty_threshold = options.get('neutral_sovereignty_threshold', 0)
    sovereign_neutral_centroid    = None
    median_cut_pixels             = np_pixels
    adjusted_mc_target            = median_cut_target

    if neutral_sovereignty_threshold > 0 and not grayscale_only:
        neutral_sum_l = neutral_sum_a = neutral_sum_b = 0.0
        neutral_count = 0
        chromatic_pixels = []

        for i in range(0, len(np_pixels), 3):
            a = np_pixels[i + 1]
            b = np_pixels[i + 2]
            chroma = math.sqrt(a * a + b * b)
            if chroma < neutral_sovereignty_threshold:
                neutral_sum_l += np_pixels[i]
                neutral_sum_a += a
                neutral_sum_b += b
                neutral_count += 1
            else:
                chromatic_pixels.extend([np_pixels[i], a, b])

        chromatic_count = len(chromatic_pixels) // 3
        if neutral_count > 0 and chromatic_count > 0:
            neutral_fraction = neutral_count / (neutral_count + chromatic_count)
            if neutral_fraction > 0.20:
                sovereign_neutral_centroid = {
                    'L': neutral_sum_l / neutral_count,
                    'a': neutral_sum_a / neutral_count,
                    'b': neutral_sum_b / neutral_count,
                }
                median_cut_pixels  = chromatic_pixels
                adjusted_mc_target = max(1, median_cut_target - 1)

    # ── Step 2: Median cut ────────────────────────────────────────────────
    # NOTE: No substrate for Mk1.5 (substrate_lab=None)
    mc_result = median_cut_in_lab_space(
        median_cut_pixels,
        adjusted_mc_target,
        grayscale_only,
        width,
        height,
        None,            # no substrate
        3.5,             # substrate_tolerance (unused when None)
        vibrancy_mode,
        vibrancy_boost,
        highlight_threshold,
        highlight_boost,
        strategy,
        tuning,
    )
    initial_palette_lab = mc_result['palette']

    # ── K-means refinement ────────────────────────────────────────────────
    split_mode = (tuning or {}).get('split', {}).get('splitMode', 'median')
    default_passes    = 3 if split_mode == 'variance' else 1
    refinement_passes = options.get('refinement_passes', default_passes)

    # K-means runs on chromatic pixels (or all non-preserved if no sovereignty)
    kmeans_pixels = median_cut_pixels if sovereign_neutral_centroid else np_pixels
    if not grayscale_only and len(initial_palette_lab) > 1 and refinement_passes > 0:
        for _ in range(refinement_passes):
            initial_palette_lab = _refine_k_means(kmeans_pixels, initial_palette_lab, tuning)

    # Inject sovereign neutral AFTER k-means (it's frozen, not refined)
    if sovereign_neutral_centroid:
        initial_palette_lab.append(sovereign_neutral_centroid)

    # ── Highlight rescue ──────────────────────────────────────────────────
    # Only active when neutral sovereignty is enabled (matches JS logic)
    highlight_rescue_threshold = options.get(
        'highlight_rescue_threshold',
        85 if neutral_sovereignty_threshold > 0 else 0
    )
    if highlight_rescue_threshold > 0 and not grayscale_only and len(initial_palette_lab) > 2:
        _rescue_highlights(initial_palette_lab, median_cut_pixels, highlight_rescue_threshold)

    # ── Step 3: Perceptual snap ───────────────────────────────────────────
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
    snapped_palette_lab = apply_perceptual_snap(
        initial_palette_lab, adaptive_threshold, is_grayscale,
        vibrancy_boost, strategy, tuning,
    )

    # ── Step 4: Palette reduction ─────────────────────────────────────────
    if enable_palette_reduction and len(snapped_palette_lab) > median_cut_target:
        pruned = _prune_palette(
            snapped_palette_lab, palette_reduction, highlight_threshold,
            median_cut_target, tuning, distance_metric
        )
        if len(pruned) < len(snapped_palette_lab):
            snapped_palette_lab = pruned

    # Unconditional similarity prune
    if enable_palette_reduction:
        dedup_threshold = max(palette_reduction, 2.0)
        dedup = _prune_palette(
            snapped_palette_lab, dedup_threshold, highlight_threshold,
            0, tuning, distance_metric
        )
        if len(dedup) < len(snapped_palette_lab):
            snapped_palette_lab = dedup

    # ── Step 4.5: Hue gap analysis ────────────────────────────────────────
    enable_hue_gap = options.get('enable_hue_gap_analysis', False)
    if enable_hue_gap and not grayscale_only:
        hue_chroma_threshold = 1.0 if vibrancy_mode == 'exponential' else 5.0
        image_hues = _analyze_image_hue_sectors(median_cut_pixels, hue_chroma_threshold)
        coverage_result = _analyze_palette_hue_coverage(snapped_palette_lab, hue_chroma_threshold)
        covered      = coverage_result['covered_sectors']
        counts_by_s  = coverage_result['color_counts_by_sector']
        gaps = _identify_hue_gaps(image_hues, covered, counts_by_s)
        gaps.sort(key=lambda s: image_hues[s], reverse=True)

        if gaps:
            gaps_to_fill = gaps[:3]
            candidates = _find_true_missing_hues(lab_pixels, snapped_palette_lab, gaps_to_fill)

            MIN_GAP_DE = 15.0
            forced_gap = [
                c for c in candidates
                if all(_lab_distance(c, p) >= MIN_GAP_DE for p in snapped_palette_lab)
            ]
            if forced_gap:
                for c in forced_gap:
                    c['_min_volume_exempt'] = True
                snapped_palette_lab = snapped_palette_lab + forced_gap

    # ── Anchor injection (PeakFinder forced centroids) ────────────────────
    merged_palette = list(snapped_palette_lab)
    added_count = skipped_count = 0
    ANCHOR_DUP_THRESHOLD = 3.0

    for forced in forced_centroids:
        dists = [(_lab_distance(c, forced), c) for c in merged_palette]
        is_dup = any(d < ANCHOR_DUP_THRESHOLD for d, _ in dists)
        if is_dup:
            skipped_count += 1
        else:
            fc = dict(forced)
            fc['_min_volume_exempt'] = True
            merged_palette.append(fc)
            added_count += 1

    # ── Step 5: Preserved colours ─────────────────────────────────────────
    # Build preserved_colors and track their positions before final dedup
    preserved_colors        = []
    white_in_preserved      = -1
    black_in_preserved      = -1
    actually_preserved_white = False
    actually_preserved_black = False

    if preserve_white:
        white_pixels = preserved_pixel_map.get('white', set())
        if len(white_pixels) >= total_pixels * MIN_PRESERVED_COVERAGE:
            preserved_colors.append({'L': 100.0, 'a': 0.0, 'b': 0.0})
            white_in_preserved      = len(preserved_colors) - 1
            actually_preserved_white = True

    if preserve_black:
        black_pixels = preserved_pixel_map.get('black', set())
        if len(black_pixels) >= total_pixels * MIN_PRESERVED_COVERAGE:
            preserved_colors.append({'L': 0.0, 'a': 0.0, 'b': 0.0})
            black_in_preserved      = len(preserved_colors) - 1
            actually_preserved_black = True

    # ── Final safety-net dedup (on merged_palette only) ───────────────────
    final_dedup_threshold = max(palette_reduction, 2.0) if enable_palette_reduction else 2.0
    dedup_final = _prune_palette(
        merged_palette, final_dedup_threshold, highlight_threshold,
        0, tuning, distance_metric
    )
    if len(dedup_final) < len(merged_palette):
        merged_palette = dedup_final

    # Compute white/black indices against the final merged_palette length
    white_index = len(merged_palette) + white_in_preserved if actually_preserved_white else -1
    black_index = len(merged_palette) + black_in_preserved if actually_preserved_black else -1

    final_palette_lab = merged_palette + preserved_colors
    palette_size      = len(final_palette_lab)

    # ── Step 6: Pixel assignment ──────────────────────────────────────────
    white_set = preserved_pixel_map.get('white', set())
    black_set = preserved_pixel_map.get('black', set())

    # Assignment distance metric (mirrors JS: options.distanceMetric || 'squared')
    assign_metric = options.get('distance_metric', 'cie76')
    l_weight = options.get('l_weight', 1.0)
    c_weight = options.get('c_weight', 1.0)

    assignments = bytearray(total_pixels)

    for i in range(total_pixels):
        if actually_preserved_white and i in white_set:
            assignments[i] = white_index
            continue
        if actually_preserved_black and i in black_set:
            assignments[i] = black_index
            continue

        off = i * 3
        p_L = lab_pixels[off]
        p_a = lab_pixels[off + 1]
        p_b = lab_pixels[off + 2]

        min_dist = float('inf')
        best_j   = 0

        for j in range(palette_size):
            t  = final_palette_lab[j]
            dL = p_L - t['L']
            da = p_a - t['a']
            db = p_b - t['b']

            if grayscale_only:
                dist = dL * dL
            elif assign_metric == 'cie76':
                # Plain CIE76 squared
                dist = dL * dL + da * da + db * db
            else:
                # 'squared': lWeight*dL^2 + cWeight*dC^2
                dC   = math.sqrt(da * da + db * db)
                dist = l_weight * dL * dL + c_weight * dC * dC

            if dist < min_dist:
                min_dist = dist
                best_j   = j

        assignments[i] = best_j

    duration = time.perf_counter() - start_time

    # ── Density floor ─────────────────────────────────────────────────────
    if density_floor > 0:
        protected = set()
        if actually_preserved_white: protected.add(white_index)
        if actually_preserved_black: protected.add(black_index)
        # _min_volume_exempt colors (PeakFinder peaks, hue gap injections) use
        # a reduced threshold matching JS MechanicalKnobs exempt treatment.
        for _i, _c in enumerate(final_palette_lab):
            if _c.get('_min_volume_exempt'):
                protected.add(_i)


        density_result = _apply_density_floor(
            assignments, final_palette_lab, density_floor, protected
        )
        if density_result['actual_count'] < len(final_palette_lab):
            final_palette_lab = density_result['palette']
            assignments       = density_result['assignments']

    palette_rgb = [_lab_to_rgb_dict(c) for c in final_palette_lab]

    return {
        'palette':        palette_rgb,
        'palette_lab':    final_palette_lab,
        'assignments':    assignments,
        'lab_pixels':     lab_pixels,
        'substrate_lab':  None,
        'substrate_index': None,
        'metadata': {
            'target_colors':   target_colors,
            'final_colors':    len(final_palette_lab),
            'auto_anchors':    added_count,
            'skipped_anchors': skipped_count,
            'detected_peaks': [
                {'L': round(p['L'], 1), 'a': round(p['a'], 1), 'b': round(p['b'], 1)}
                for p in detected_peaks
            ],
            'snap_threshold': snap_threshold,
            'duration':       round(duration, 3),
            'engine_type':    'reveal-mk1.5',
        },
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _rescue_highlights(palette_lab: list, pixel_source: list,
                        threshold: float) -> None:
    """Detect bright warm highlights missed by median cut.

    If found and sufficiently distinct (ΔE > 20), replace the palette slot
    with lowest pixel coverage.  Modifies palette_lab in-place.
    """
    hl_pixels = []
    pixel_total = len(pixel_source) // 3

    for i in range(0, len(pixel_source), 3):
        L = pixel_source[i]
        a = pixel_source[i + 1]
        b = pixel_source[i + 2]
        if L > threshold and b > 40 and 0 <= a < 20:
            hl_pixels.append({'L': L, 'a': a, 'b': b})

    hl_count    = len(hl_pixels)
    hl_fraction = hl_count / pixel_total if pixel_total > 0 else 0
    if hl_count == 0 or hl_fraction <= 0.005:
        return

    sorted_b = sorted(p['b'] for p in hl_pixels)
    p90_idx  = min(len(sorted_b) - 1, int(len(sorted_b) * 0.90))
    b_target = sorted_b[p90_idx]

    hl_sum_l = sum(p['L'] for p in hl_pixels)
    hl_sum_a = sum(p['a'] for p in hl_pixels)
    hl_centroid = {
        'L': hl_sum_l / hl_count,
        'a': hl_sum_a / hl_count,
        'b': b_target,
    }

    # Skip if palette already covers this highlight region
    nearest_de = float('inf')
    for p in palette_lab:
        dL = hl_centroid['L'] - p['L']
        da = hl_centroid['a'] - p['a']
        db = hl_centroid['b'] - p['b']
        de = math.sqrt(dL * dL + da * da + db * db)
        if de < nearest_de:
            nearest_de = de

    if nearest_de <= 20.0:
        return

    # Find lowest-coverage slot to replace
    pal_len    = len(palette_lab)
    slot_counts = [0] * pal_len

    for pi in range(0, len(pixel_source), 3):
        pL = pixel_source[pi]; pa = pixel_source[pi + 1]; pb = pixel_source[pi + 2]
        best_d = float('inf'); best_j = 0
        for j in range(pal_len):
            c = palette_lab[j]
            d = ((pL - c['L']) ** 2 + (pa - c['a']) ** 2 + (pb - c['b']) ** 2)
            if d < best_d:
                best_d = d; best_j = j
        slot_counts[best_j] += 1

    min_count = float('inf'); min_idx = -1
    for j in range(pal_len):
        if slot_counts[j] < min_count:
            min_count = slot_counts[j]; min_idx = j

    if min_idx >= 0:
        palette_lab[min_idx] = hl_centroid


def _lab_to_rgb_dict(lab: dict) -> dict:
    r, g, b = lab_to_rgb(lab['L'], lab['a'], lab['b'])
    return {'r': r, 'g': g, 'b': b}

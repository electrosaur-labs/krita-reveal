"""
PaletteOps — palette management operations.

Contains colour merging, pruning, snapping, and distance methods.
All methods are static (module-level functions).

DEFAULT_TUNING:
  prune.threshold:     9.0
  prune.hueLockAngle:  18 degrees
  prune.whitePoint:    85 (L)
  prune.shadowPoint:   15 (L)
  centroid.lWeight:    1.1
  centroid.cWeight:    2.0
  centroid.blackBias:  5.0
"""

from __future__ import annotations

import math

from .centroid_strategies import volumetric, CENTROID_STRATEGIES

_DEFAULT_TUNING = {
    'prune': {'threshold': 9.0, 'hueLockAngle': 18, 'whitePoint': 85, 'shadowPoint': 15},
    'centroid': {'lWeight': 1.1, 'cWeight': 2.0, 'blackBias': 5.0},
}


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def calculate_cielab_distance(lab1: dict, lab2: dict, is_grayscale: bool = False) -> float:
    """Return SQUARED perceptual distance (L-weighted CIE76).

    L_WEIGHT = 3.0 for grayscale, 1.5 for colour.
    Returns squared distance — sufficient for comparisons (no sqrt needed).
    """
    dL = lab1['L'] - lab2['L']
    da = lab1['a'] - lab2['a']
    db = lab1['b'] - lab2['b']
    L_WEIGHT = 3.0 if is_grayscale else 1.5
    return L_WEIGHT * dL * dL + da * da + db * db


def _lab_distance(lab1: dict, lab2: dict) -> float:
    """Standard Euclidean CIE76 distance (sqrt)."""
    dL = lab1['L'] - lab2['L']
    da = lab1['a'] - lab2['a']
    db = lab1['b'] - lab2['b']
    return math.sqrt(dL * dL + da * da + db * db)


def _weighted_lab_distance(lab1: dict, lab2: dict) -> float:
    """L-weighted CIE76: doubles L weight when avgL < 40 to preserve shadows."""
    dL = lab1['L'] - lab2['L']
    da = lab1['a'] - lab2['a']
    db = lab1['b'] - lab2['b']
    avg_l = (lab1['L'] + lab2['L']) / 2
    l_weight = 2.0 if avg_l < 40 else 1.0
    return math.sqrt((dL * l_weight) ** 2 + da * da + db * db)


# ---------------------------------------------------------------------------
# Centroid helpers
# ---------------------------------------------------------------------------

def _calculate_lab_centroid(
    colors: list,
    grayscale_only: bool = False,
    strategy=None,
    tuning: dict | None = None,
) -> dict:
    """Calculate representative colour for a bucket using a centroid strategy.

    Falls back to VOLUMETRIC if no strategy supplied.
    Grayscale mode: forces a=b=0.
    """
    if not colors:
        return {'L': 50, 'a': 0, 'b': 0}

    centroid_fn = strategy if callable(strategy) else volumetric
    default_weights = {'l_weight': 1.1, 'c_weight': 2.0, 'black_bias': 5.0}
    weights = tuning['centroid'] if tuning and 'centroid' in tuning else default_weights

    result = centroid_fn(colors, weights)

    if grayscale_only:
        return {'L': result['L'], 'a': 0, 'b': 0}
    return result


def _merge_lab_colors(c1: dict, c2: dict) -> dict:
    """Return the colour with higher saliency (1.5×L + 2.5×C)."""
    chroma1 = math.sqrt(c1['a'] ** 2 + c1['b'] ** 2)
    chroma2 = math.sqrt(c2['a'] ** 2 + c2['b'] ** 2)
    s1 = c1['L'] * 1.5 + chroma1 * 2.5
    s2 = c2['L'] * 1.5 + chroma2 * 2.5
    return c1 if s1 > s2 else c2


def _merge_by_saliency(c1: dict, c2: dict) -> dict:
    """Alias for _merge_lab_colors."""
    return _merge_lab_colors(c1, c2)


def _get_saliency_winner(c1: dict, c2: dict) -> dict:
    """Return colour with higher saliency using (1.2×L + 2.0×C) formula."""
    s1 = c1['L'] * 1.2 + math.sqrt(c1['a'] ** 2 + c1['b'] ** 2) * 2.0
    s2 = c2['L'] * 1.2 + math.sqrt(c2['a'] ** 2 + c2['b'] ** 2) * 2.0
    return c1 if s1 > s2 else c2


def _snap_to_source(target_lab: dict, bucket: list) -> dict:
    """Snap mathematical Lab average to nearest actual source pixel.

    Prevents muddy averaged colours. Uses L-weighting for dark pixels.
    """
    if not bucket:
        return target_lab

    min_dist_sq = float('inf')
    best = target_lab

    for pixel in bucket:
        dL = target_lab['L'] - pixel['L']
        da = target_lab['a'] - pixel['a']
        db = target_lab['b'] - pixel['b']
        avg_l = (target_lab['L'] + pixel['L']) / 2
        l_weight = 2.0 if avg_l < 40 else 1.0
        dist_sq = (dL * l_weight) ** 2 + da * da + db * db
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best = {'L': pixel['L'], 'a': pixel['a'], 'b': pixel['b']}

    return best


def _find_nearest_in_palette(target_lab: dict, sub_palette: list) -> int:
    """Return index of nearest colour in sub_palette (Euclidean Lab)."""
    if not target_lab or not sub_palette:
        return 0

    min_dist = float('inf')
    closest_idx = 0

    for i, p in enumerate(sub_palette):
        if p is None:
            continue
        d = math.sqrt(
            (target_lab['L'] - p['L']) ** 2 +
            (target_lab['a'] - p['a']) ** 2 +
            (target_lab['b'] - p['b']) ** 2
        )
        if d < min_dist:
            min_dist = d
            closest_idx = i

    return closest_idx


# ---------------------------------------------------------------------------
# Snap / prune / floor / refine
# ---------------------------------------------------------------------------

def apply_perceptual_snap(
    palette: list,
    threshold: float = 8.0,
    is_grayscale: bool = False,
    vibrancy_multiplier: float = 2.0,
    strategy=None,
    tuning: dict | None = None,
) -> list:
    """Collapse similar colours into their centroid representative.

    threshold: ΔE merge distance (compared against squared distance internally).
    strategy:  centroid strategy function; defaults to VOLUMETRIC.
    """
    if len(palette) <= 1:
        return palette

    threshold_sq = threshold * threshold
    snapped = []
    merged = set()

    for i in range(len(palette)):
        if i in merged:
            continue

        feature_group = [palette[i]]

        for j in range(i + 1, len(palette)):
            if j in merged:
                continue
            dist_sq = calculate_cielab_distance(palette[i], palette[j], is_grayscale)
            if dist_sq < threshold_sq:
                feature_group.append(palette[j])
                merged.add(j)

        representative = _calculate_lab_centroid(feature_group, is_grayscale, strategy, tuning)
        snapped.append(representative)

    return snapped


def _prune_palette(
    palette_lab: list,
    threshold: float | None = None,
    highlight_threshold: float | None = None,
    target_count: int = 0,
    tuning: dict | None = None,
    distance_metric: str = 'cie76',
) -> list:
    """Merge near-duplicate colours with hue-lock and highlight protection.

    Keeps the saliency winner (1.5×L + 2.5×C) when merging a pair.
    distance_metric: 'cie76' (default), 'cie94', 'cie2000'.
    (cie94/cie2000 fall back to weighted CIE76 until LabDistance is ported.)
    """
    config = tuning if tuning is not None else _DEFAULT_TUNING
    prune_threshold = threshold if threshold is not None else config['prune']['threshold']
    highlight_protect = highlight_threshold if highlight_threshold is not None else config['prune']['whitePoint']
    shadow_protect = config['prune']['shadowPoint']
    hue_lock = config['prune']['hueLockAngle']

    # Distance function selection
    # cie94/cie2000 not yet ported — fall back to weighted CIE76
    if distance_metric in ('cie94', 'cie2000'):
        def dist_fn(p1, p2):
            avg_l = (p1['L'] + p2['L']) / 2
            return _weighted_lab_distance(p1, p2) if avg_l < 40 else _lab_distance(p1, p2)
    else:
        dist_fn = None  # Use inline logic below (CIE76)

    pruned = list(palette_lab)
    i = 0

    while i < len(pruned):
        j = i + 1
        while j < len(pruned):
            if target_count > 0 and len(pruned) <= target_count:
                return pruned

            p1 = pruned[i]
            p2 = pruned[j]

            if dist_fn is not None:
                dist = dist_fn(p1, p2)
            else:
                avg_l = (p1['L'] + p2['L']) / 2
                dist = _weighted_lab_distance(p1, p2) if avg_l < 40 else _lab_distance(p1, p2)

            if dist < prune_threshold:
                chroma1 = math.sqrt(p1['a'] ** 2 + p1['b'] ** 2)
                chroma2 = math.sqrt(p2['a'] ** 2 + p2['b'] ** 2)

                # Hue lock: don't merge chromatic colours with large hue difference
                if chroma1 > 5 and chroma2 > 5:
                    h1 = math.atan2(p1['b'], p1['a']) * (180 / math.pi)
                    h2 = math.atan2(p2['b'], p2['a']) * (180 / math.pi)
                    hue_diff = abs(h1 - h2)
                    if hue_diff > 180:
                        hue_diff = 360 - hue_diff
                    if hue_diff > hue_lock:
                        j += 1
                        continue

                # Highlight protection: don't merge across white-point boundary
                if (p1['L'] > highlight_protect) != (p2['L'] > highlight_protect):
                    j += 1
                    continue

                # Keep saliency winner
                s1 = p1['L'] * 1.5 + chroma1 * 2.5
                s2 = p2['L'] * 1.5 + chroma2 * 2.5
                pruned[i] = p1 if s1 > s2 else p2
                del pruned[j]
                # j stays the same (next element shifted in)
            else:
                j += 1

        i += 1

    return pruned


def _apply_density_floor(
    assignments,
    palette: list,
    threshold: float = 0.005,
    protected_indices: set | None = None,
) -> dict:
    """Remove palette colours below coverage threshold and reassign pixels.

    assignments: list/bytearray of palette indices (255 = transparent).
    protected_indices: set of indices that survive if they have any pixels.
    Returns {'palette': list, 'assignments': bytearray, 'actual_count': int}.
    """
    if not assignments or not palette:
        return {'palette': palette, 'assignments': assignments, 'actual_count': len(palette)}

    if protected_indices is None:
        protected_indices = set()

    total_pixels = len(assignments)
    counts = [0] * len(palette)

    for idx in assignments:
        if idx == 255:
            continue
        if 0 <= idx < len(palette):
            counts[idx] += 1

    viable_indices = []
    for i, count in enumerate(counts):
        coverage = count / total_pixels
        if i in protected_indices:
            if count > 0:
                viable_indices.append(i)
        elif coverage >= threshold:
            viable_indices.append(i)

    if len(viable_indices) == len(palette):
        return {'palette': palette, 'assignments': assignments, 'actual_count': len(palette)}

    if not viable_indices:
        return {'palette': palette, 'assignments': assignments, 'actual_count': len(palette)}

    clean_palette = [palette[idx] for idx in viable_indices]
    remapped = bytearray(total_pixels)

    for i, old_idx in enumerate(assignments):
        if old_idx == 255:
            remapped[i] = 255
            continue
        if not (0 <= old_idx < len(palette)):
            remapped[i] = 0
            continue
        try:
            new_idx = viable_indices.index(old_idx)
            remapped[i] = new_idx
        except ValueError:
            target_color = palette[old_idx]
            if target_color and clean_palette:
                remapped[i] = _find_nearest_in_palette(target_color, clean_palette)
            else:
                remapped[i] = 0

    return {'palette': clean_palette, 'assignments': remapped, 'actual_count': len(clean_palette)}


def _refine_k_means(lab_pixels, palette: list, tuning: dict | None = None) -> list:
    """1-pass k-means centroid refinement after median cut.

    lab_pixels: flat list or sequence [L, a, b, ...] in perceptual space.
    GRID_STRIDE=4 — samples every 4th pixel for performance.
    warmABoost: amplifies a-axis weight to preserve yellow/green separation.
    Returns new palette list; caller is responsible for preserving metadata.
    """
    GRID_STRIDE = 4
    pixel_count = len(lab_pixels) // 3

    if not palette or len(palette) <= 1 or pixel_count == 0:
        return palette

    num_colors = len(palette)
    current_palette = [{'L': c['L'], 'a': c['a'], 'b': c['b']} for c in palette]

    warm_a_boost = 1.0
    if tuning and 'split' in tuning:
        warm_a_boost = tuning['split'].get('warmABoost', 1.0)
    a_weight = math.sqrt(warm_a_boost) if warm_a_boost > 1.0 else 1.0

    # Step 1: accumulate per-cluster weighted sums
    sum_l = [0.0] * num_colors
    sum_a = [0.0] * num_colors
    sum_b = [0.0] * num_colors
    counts = [0] * num_colors

    for i in range(0, pixel_count, GRID_STRIDE):
        idx = i * 3
        L = lab_pixels[idx]
        a = lab_pixels[idx + 1]
        b = lab_pixels[idx + 2]

        best_idx = 0
        best_dist = float('inf')

        for c in range(num_colors):
            p = current_palette[c]
            dL = L - p['L']
            da = (a - p['a']) * a_weight
            db = b - p['b']
            dist = dL * dL + da * da + db * db
            if dist < best_dist:
                best_dist = dist
                best_idx = c

        sum_l[best_idx] += L
        sum_a[best_idx] += a
        sum_b[best_idx] += b
        counts[best_idx] += 1

    # Step 2: recompute centroids
    for c in range(num_colors):
        if counts[c] == 0:
            continue
        current_palette[c] = {
            'L': sum_l[c] / counts[c],
            'a': sum_a[c] / counts[c],
            'b': sum_b[c] / counts[c],
        }

    return current_palette


def _get_adaptive_snap_threshold(
    base_threshold: float,
    target_colors: int,
    is_grayscale: bool,
    l_range: float = 0,
    color_space_extent: dict | None = None,
) -> float:
    """Return adaptive snap threshold based on target colour count and space.

    Grayscale: 0.4 × (lRange / (targetColors-1)) × sqrt(3)
    Colour:    min(base, 0.4 × labDiagonal / (targetColors-1))
    """
    if is_grayscale and l_range > 0:
        target_spacing = l_range / max(1, target_colors - 1)
        return 0.4 * target_spacing * math.sqrt(3.0)

    if is_grayscale:
        return 2.0

    if color_space_extent:
        lab_diagonal = math.sqrt(
            color_space_extent.get('lRange', 0) ** 2 * 1.5 +
            color_space_extent.get('aRange', 0) ** 2 +
            color_space_extent.get('bRange', 0) ** 2
        )
        target_spacing = lab_diagonal / max(1, target_colors - 1)
        return min(base_threshold, 0.4 * target_spacing)

    # Fallback
    if target_colors >= 9:
        return min(base_threshold, 4.0)
    elif target_colors >= 6:
        return min(base_threshold, 6.0)
    else:
        return base_threshold


def consolidate_near_duplicates(
    palette: list,
    edited_indices: set,
    threshold: float = 3.0,
) -> dict:
    """Merge user-edited palette slots that are near-duplicates of others.

    Returns {edited_index: target_index} merge map.
    Only edited slots are candidates for merging.
    """
    if not palette or len(palette) <= 1 or not edited_indices:
        return {}

    merge_map = {}
    dead = set()
    th_sq = threshold * threshold

    for ed in edited_indices:
        if ed in dead or ed >= len(palette):
            continue
        for j in range(len(palette)):
            if j == ed or j in dead:
                continue
            dL = palette[ed]['L'] - palette[j]['L']
            da = palette[ed]['a'] - palette[j]['a']
            db = palette[ed]['b'] - palette[j]['b']
            if dL * dL + da * da + db * db < th_sq:
                merge_map[ed] = j
                dead.add(ed)
                break

    return merge_map

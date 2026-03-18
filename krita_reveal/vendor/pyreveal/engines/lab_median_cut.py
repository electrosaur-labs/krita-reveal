"""
LabMedianCut — Lab-space median cut quantization.

Core Lab-space quantization with substrate culling, green rescue, and
hue-aware split priority.

Supports two quantizers:
  'median-cut' (default): recursive variance-weighted Lab box splitting
  'wu':                   Xiaolin Wu 3D histogram histogram quantization

Returns a dict:
  {
    'palette':    list of {L, a, b} dicts,
    'all_colors': deduplicated {L, a, b, count} list (for hue gap analysis),
    'lab_pixels': flat [L, a, b, ...] list passed in,
  }
"""

from __future__ import annotations

import math

from .hue_gap_recovery import _get_hue_sector, _analyze_image_hue_sectors
from .palette_ops import _calculate_lab_centroid

_DEFAULT_TUNING = {
    'split': {'highlightBoost': 2.2, 'vibrancyBoost': 1.6, 'minVariance': 10},
    'prune': {'threshold': 9.0, 'hueLockAngle': 18, 'whitePoint': 85, 'shadowPoint': 15},
    'centroid': {'lWeight': 1.1, 'cWeight': 2.0, 'blackBias': 5.0},
}


# ---------------------------------------------------------------------------
# Box metadata
# ---------------------------------------------------------------------------

def _calculate_box_metadata(box: dict, grayscale_only: bool = False, tuning: dict | None = None) -> dict:
    """Return {meanL, meanA, meanB, sector, variance} for a colour box.

    Applies vibrancy boost (avgChroma>10) and highlight boost (meanL>whitePoint).
    Uses Math.max to let either feature win independently.
    """
    colors = box.get('colors', [])
    if not colors:
        return {'meanL': 0, 'meanA': 0, 'meanB': 0, 'sector': -1, 'variance': 0}

    config = tuning if tuning is not None else _DEFAULT_TUNING

    n = len(colors)
    meanL = sum(c['L'] for c in colors) / n
    meanA = sum(c['a'] for c in colors) / n
    meanB = sum(c['b'] for c in colors) / n

    varL = varA = varB = chroma_sum = 0.0
    for c in colors:
        varL += (c['L'] - meanL) ** 2
        if not grayscale_only:
            varA += (c['a'] - meanA) ** 2
            varB += (c['b'] - meanB) ** 2
            chroma_sum += math.sqrt(c['a'] ** 2 + c['b'] ** 2)

    avg_chroma = 0 if grayscale_only else chroma_sum / n
    vibrancy_boost = config['split']['vibrancyBoost'] if avg_chroma > 10 else 1.0
    highlight_boost = (
        config['split']['highlightBoost']
        if meanL > config['prune']['whitePoint']
        else 1.0
    )
    final_boost = max(vibrancy_boost, highlight_boost)

    base_variance = varL if grayscale_only else (varL + varA + varB)
    variance = base_variance * final_boost

    sector = -1 if grayscale_only else _get_hue_sector(meanA, meanB)

    return {'meanL': meanL, 'meanA': meanA, 'meanB': meanB, 'sector': sector, 'variance': variance}


# ---------------------------------------------------------------------------
# Box contains hue sector
# ---------------------------------------------------------------------------

def _box_contains_hue_sector(colors: list, target_sectors: list, chroma_threshold: float = 2.0) -> bool:
    """Return True if any sampled colour in the box falls in a target hue sector.

    Samples up to 100 colours (stride = len/100).
    Also returns True for a<-3, b>0, C>3 (greenish regardless of sector).
    """
    sample_size = min(len(colors), 100)
    step = max(1, int(len(colors) / sample_size))

    for i in range(0, len(colors), step):
        c = colors[i]
        chroma = math.sqrt(c['a'] ** 2 + c['b'] ** 2)
        if chroma < chroma_threshold:
            continue

        hue = math.atan2(c['b'], c['a']) * 180 / math.pi
        norm_hue = hue if hue >= 0 else hue + 360
        sector = int(norm_hue / 30) % 12

        if sector in target_sectors:
            return True

        if c['a'] < -3 and c['b'] > 0 and chroma > 3:
            return True

    return False


# ---------------------------------------------------------------------------
# Split priority
# ---------------------------------------------------------------------------

def _calculate_split_priority(
    box: dict,
    sector_energy: list | None,
    covered_sectors: set,
    grayscale_only: bool,
    hue_multiplier: float = 5.0,
    vibrancy_mode: str = 'aggressive',
    vibrancy_boost: float = 2.0,
    highlight_threshold: float = 92,
    highlight_boost: float = 3.0,
    tuning: dict | None = None,
) -> float:
    """Return priority score for splitting a box (variance × hue-hunger multiplier).

    Neutral boxes (meanChroma<10) get 0.25× penalty.
    Red sector (0): RED_RESCUE_THRESHOLD=2%, RED_RESCUE_MULTIPLIER=10×.
    Green sectors (3,4) in archive/16-bit mode: GREEN_RESCUE_THRESHOLD=0.5-1.5%.
    Green peek: 8× if box contains green signal but none covered yet.
    """
    metadata = _calculate_box_metadata(box, grayscale_only, tuning)
    base_priority = metadata['variance']

    if grayscale_only or sector_energy is None:
        return base_priority

    mean_chroma = math.sqrt(metadata['meanA'] ** 2 + metadata['meanB'] ** 2)
    if mean_chroma < 10.0:
        base_priority *= 0.25

    is_16bit = tuning and tuning.get('centroid', {}).get('bitDepth') == 16
    is_archive_mode = vibrancy_mode == 'exponential' or is_16bit
    GREEN_PEEK_THRESHOLD = 0.5 if is_16bit else 2.0
    GREEN_PEEK_MULTIPLIER = 8.0

    green_sector3_covered = 3 in covered_sectors
    green_sector4_covered = 4 in covered_sectors
    green_energy = max(
        sector_energy[3] if len(sector_energy) > 3 else 0,
        sector_energy[4] if len(sector_energy) > 4 else 0,
    )

    if is_archive_mode and not green_sector3_covered and not green_sector4_covered:
        has_green = _box_contains_hue_sector(box.get('colors', []), [3, 4], GREEN_PEEK_THRESHOLD)
        if has_green and green_energy > 0.1:
            return base_priority * GREEN_PEEK_MULTIPLIER

    multiplier = 1.0
    box_sector = metadata['sector']

    if box_sector >= 0:
        source_energy = sector_energy[box_sector] if box_sector < len(sector_energy) else 0

        is_red_sector = box_sector == 0
        RED_RESCUE_THRESHOLD = 2.0
        RED_RESCUE_MULTIPLIER = 10.0

        is_green_sector = box_sector in (3, 4)
        GREEN_RESCUE_THRESHOLD = 0.5 if is_16bit else 1.5
        GREEN_RESCUE_MULTIPLIER = 10.0

        significance_threshold = 5.0
        sector_multiplier = hue_multiplier

        if is_red_sector:
            significance_threshold = RED_RESCUE_THRESHOLD
            sector_multiplier = max(RED_RESCUE_MULTIPLIER, hue_multiplier)
        elif is_archive_mode and is_green_sector:
            significance_threshold = GREEN_RESCUE_THRESHOLD
            sector_multiplier = max(GREEN_RESCUE_MULTIPLIER, hue_multiplier)

        if source_energy > significance_threshold and box_sector not in covered_sectors:
            multiplier = sector_multiplier

    return base_priority * multiplier


# ---------------------------------------------------------------------------
# Box SSE (for variance split mode)
# ---------------------------------------------------------------------------

def _calculate_box_sse(box: dict, tuning: dict | None = None) -> float:
    """Return total count-weighted SSE across L, a, b channels."""
    colors = box.get('colors', [])
    if not colors:
        return 0.0

    l_weight = tuning['centroid']['lWeight'] if tuning and 'centroid' in tuning else 1.0
    c_weight = tuning['centroid']['cWeight'] if tuning and 'centroid' in tuning else 1.0

    total_count = sum_l = sum_a = sum_b = 0.0
    for c in colors:
        w = c.get('count', 1)
        sum_l += c['L'] * w
        sum_a += c['a'] * w
        sum_b += c['b'] * w
        total_count += w

    mean_l = sum_l / total_count
    mean_a = sum_a / total_count
    mean_b = sum_b / total_count

    sse = 0.0
    for c in colors:
        w = c.get('count', 1)
        dL = c['L'] - mean_l
        dA = c['a'] - mean_a
        dB = c['b'] - mean_b
        sse += w * (l_weight * dL * dL + c_weight * (dA * dA + dB * dB))

    return sse


# ---------------------------------------------------------------------------
# Box splitting
# ---------------------------------------------------------------------------

def _split_box_lab(box: dict, grayscale_only: bool = False, tuning: dict | None = None):
    """Split a box along the highest-variance Lab channel.

    split_mode='median' (default): split at the median index.
    split_mode='variance': SSE-minimizing split using prefix sums.
    Returns (box1, box2) or (None, None) if unsplittable.
    """
    colors = box['colors']
    if len(colors) < 2:
        return None, None

    l_weight = tuning['centroid']['lWeight'] if tuning and 'centroid' in tuning else 1.0
    c_weight = tuning['centroid']['cWeight'] if tuning and 'centroid' in tuning else 1.0
    b_weight = tuning['centroid'].get('bWeight', 1.0) if tuning and 'centroid' in tuning else 1.0
    chroma_axis_weight = tuning['split'].get('chromaAxisWeight', 0) if tuning and 'split' in tuning else 0
    warm_a_boost = tuning['split'].get('warmABoost', 1.0) if tuning and 'split' in tuning else 1.0
    split_mode = tuning['split'].get('splitMode', 'median') if tuning and 'split' in tuning else 'median'

    depth = box.get('depth', 0) + 1

    if grayscale_only:
        avg_l = sum(c['L'] for c in colors) / len(colors)
        var_l = sum((c['L'] - avg_l) ** 2 for c in colors)
        if var_l == 0:
            return None, None
        colors.sort(key=lambda c: c['L'])
        mid = len(colors) // 2
        return (
            {'colors': colors[:mid], 'depth': depth, 'grayscale_only': True},
            {'colors': colors[mid:], 'depth': depth, 'grayscale_only': True},
        )

    n = len(colors)
    avg_l = sum(c['L'] for c in colors) / n
    avg_a = sum(c['a'] for c in colors) / n
    avg_b = sum(c['b'] for c in colors) / n

    # Warm a-axis boost for warm hue boxes (hue 20-75°, chroma > 15)
    a_axis_multiplier = 1.0
    if warm_a_boost > 1.0:
        mean_chroma = math.sqrt(avg_a ** 2 + avg_b ** 2)
        mean_hue = ((math.atan2(avg_b, avg_a) * 180 / math.pi) + 360) % 360
        if mean_chroma > 15 and 20 <= mean_hue <= 75:
            a_axis_multiplier = warm_a_boost

    var_l_w = sum((c['L'] - avg_l) ** 2 for c in colors) * l_weight
    var_a_w = sum((c['a'] - avg_a) ** 2 for c in colors) * c_weight * a_axis_multiplier
    var_b_w = sum((c['b'] - avg_b) ** 2 for c in colors) * c_weight * b_weight

    # Optional C* axis
    var_c_w = 0.0
    if chroma_axis_weight > 0:
        chromas = [math.sqrt(c['a'] ** 2 + c['b'] ** 2) for c in colors]
        avg_c = sum(chromas) / n
        var_c_w = sum((ch - avg_c) ** 2 for ch in chromas) * chroma_axis_weight

    max_var = var_l_w
    split_channel = 'L'
    if var_a_w > max_var:
        max_var = var_a_w
        split_channel = 'a'
    if var_b_w > max_var:
        max_var = var_b_w
        split_channel = 'b'
    if var_c_w > max_var:
        max_var = var_c_w
        split_channel = 'C'

    if max_var == 0:
        return None, None

    if split_channel == 'C':
        colors.sort(key=lambda c: math.sqrt(c['a'] ** 2 + c['b'] ** 2))
    else:
        colors.sort(key=lambda c: c[split_channel])

    # Choose split point
    if split_mode == 'variance' and n > 2:
        # Build prefix sums for SSE-minimizing split
        pref_sum_l = [0.0] * (n + 1)
        pref_sum_a = [0.0] * (n + 1)
        pref_sum_b = [0.0] * (n + 1)
        pref_sq_l = [0.0] * (n + 1)
        pref_sq_a = [0.0] * (n + 1)
        pref_sq_b = [0.0] * (n + 1)
        pref_cnt = [0.0] * (n + 1)

        for i, c in enumerate(colors):
            w = c.get('count', 1)
            pref_sum_l[i + 1] = pref_sum_l[i] + c['L'] * w
            pref_sum_a[i + 1] = pref_sum_a[i] + c['a'] * w
            pref_sum_b[i + 1] = pref_sum_b[i] + c['b'] * w
            pref_sq_l[i + 1] = pref_sq_l[i] + c['L'] ** 2 * w
            pref_sq_a[i + 1] = pref_sq_a[i] + c['a'] ** 2 * w
            pref_sq_b[i + 1] = pref_sq_b[i] + c['b'] ** 2 * w
            pref_cnt[i + 1] = pref_cnt[i] + w

        total_n = pref_cnt[n]
        best_sse = float('inf')
        split_idx = n // 2

        for k in range(1, n):
            left_n = pref_cnt[k]
            right_n = total_n - left_n
            if left_n == 0 or right_n == 0:
                continue

            sse_left_l = pref_sq_l[k] - pref_sum_l[k] ** 2 / left_n
            sse_left_a = pref_sq_a[k] - pref_sum_a[k] ** 2 / left_n
            sse_left_b = pref_sq_b[k] - pref_sum_b[k] ** 2 / left_n

            r_sum_l = pref_sum_l[n] - pref_sum_l[k]
            r_sum_a = pref_sum_a[n] - pref_sum_a[k]
            r_sum_b = pref_sum_b[n] - pref_sum_b[k]
            r_sq_l = pref_sq_l[n] - pref_sq_l[k]
            r_sq_a = pref_sq_a[n] - pref_sq_a[k]
            r_sq_b = pref_sq_b[n] - pref_sq_b[k]

            sse_right_l = r_sq_l - r_sum_l ** 2 / right_n
            sse_right_a = r_sq_a - r_sum_a ** 2 / right_n
            sse_right_b = r_sq_b - r_sum_b ** 2 / right_n

            total_sse = (l_weight * (sse_left_l + sse_right_l) +
                         c_weight * (sse_left_a + sse_right_a + sse_left_b + sse_right_b))

            if total_sse < best_sse:
                best_sse = total_sse
                split_idx = k
    else:
        split_idx = n // 2

    return (
        {'colors': colors[:split_idx], 'depth': depth},
        {'colors': colors[split_idx:], 'depth': depth},
    )


# ---------------------------------------------------------------------------
# Wu histogram quantizer
# ---------------------------------------------------------------------------

def _wu_cumulative_moments(wt, mL, mA, mB, m2, BINS):
    """Compute 3D cumulative moment prefix sums in-place (Wu 1991)."""
    area_wt = [0.0] * BINS
    area_l = [0.0] * BINS
    area_a = [0.0] * BINS
    area_b = [0.0] * BINS
    area_2 = [0.0] * BINS

    for r in range(1, BINS):
        for j in range(BINS):
            area_wt[j] = area_l[j] = area_a[j] = area_b[j] = area_2[j] = 0.0

        for g in range(1, BINS):
            line_wt = line_l = line_a = line_b = line_2 = 0.0

            for b in range(1, BINS):
                idx = r * BINS * BINS + g * BINS + b
                prev_r = (r - 1) * BINS * BINS + g * BINS + b

                line_wt += wt[idx]; line_l += mL[idx]; line_a += mA[idx]
                line_b += mB[idx]; line_2 += m2[idx]

                area_wt[b] += line_wt; area_l[b] += line_l; area_a[b] += line_a
                area_b[b] += line_b; area_2[b] += line_2

                wt[idx] = wt[prev_r] + area_wt[b]
                mL[idx] = mL[prev_r] + area_l[b]
                mA[idx] = mA[prev_r] + area_a[b]
                mB[idx] = mB[prev_r] + area_b[b]
                m2[idx] = m2[prev_r] + area_2[b]


def _wu_vol(box: dict, mmt: list, BINS: int) -> float:
    """3D inclusion-exclusion volume query over moment array."""
    r0, r1 = box['r0'], box['r1']
    g0, g1 = box['g0'], box['g1']
    b0, b1 = box['b0'], box['b1']
    S = BINS * BINS
    return (
        mmt[r1 * S + g1 * BINS + b1]
      - mmt[r1 * S + g1 * BINS + b0]
      - mmt[r1 * S + g0 * BINS + b1]
      + mmt[r1 * S + g0 * BINS + b0]
      - mmt[r0 * S + g1 * BINS + b1]
      + mmt[r0 * S + g1 * BINS + b0]
      + mmt[r0 * S + g0 * BINS + b1]
      - mmt[r0 * S + g0 * BINS + b0]
    )


def _wu_variance(box: dict, wt, mL, mA, mB, m2, BINS: int) -> float:
    """Variance = m2 - (mL² + mA² + mB²) / wt."""
    vol = _wu_vol(box, wt, BINS)
    if vol <= 0:
        return 0.0
    sL = _wu_vol(box, mL, BINS)
    sA = _wu_vol(box, mA, BINS)
    sB = _wu_vol(box, mB, BINS)
    s2 = _wu_vol(box, m2, BINS)
    return s2 - (sL * sL + sA * sA + sB * sB) / vol


def _wu_box_centroid(box: dict, wt, mL, mA, mB, BINS: int) -> dict:
    """Return vol-weighted centroid {L, a, b} for a Wu box."""
    vol = _wu_vol(box, wt, BINS)
    if vol <= 0:
        return {'L': 50, 'a': 0, 'b': 0}
    return {
        'L': _wu_vol(box, mL, BINS) / vol,
        'a': _wu_vol(box, mA, BINS) / vol,
        'b': _wu_vol(box, mB, BINS) / vol,
    }


def _wu_maximize(box: dict, axis: int, wt, mL, mA, mB, m2, BINS: int):
    """Find best split position along one axis. Returns {'pos', 'metric'} or None."""
    r0, r1 = box['r0'], box['r1']
    g0, g1 = box['g0'], box['g1']
    b0, b1 = box['b0'], box['b1']

    whole_wt = _wu_vol(box, wt, BINS)
    whole_l = _wu_vol(box, mL, BINS)
    whole_a = _wu_vol(box, mA, BINS)
    whole_b = _wu_vol(box, mB, BINS)

    if whole_wt <= 0:
        return None

    if axis == 0:
        lo, hi = r0 + 1, r1
    elif axis == 1:
        lo, hi = g0 + 1, g1
    else:
        lo, hi = b0 + 1, b1

    best_metric = float('-inf')
    best_pos = -1

    for pos in range(lo, hi):
        half = {'r0': r0, 'r1': r1, 'g0': g0, 'g1': g1, 'b0': b0, 'b1': b1}
        if axis == 0:
            half['r1'] = pos
        elif axis == 1:
            half['g1'] = pos
        else:
            half['b1'] = pos

        half_wt = _wu_vol(half, wt, BINS)
        if half_wt <= 0:
            continue

        half_l = _wu_vol(half, mL, BINS)
        half_a = _wu_vol(half, mA, BINS)
        half_b = _wu_vol(half, mB, BINS)

        top_wt = whole_wt - half_wt
        if top_wt <= 0:
            continue

        top_l = whole_l - half_l
        top_a = whole_a - half_a
        top_b = whole_b - half_b

        metric = (
            (half_l * half_l + half_a * half_a + half_b * half_b) / half_wt
          + (top_l * top_l + top_a * top_a + top_b * top_b) / top_wt
        )

        if metric > best_metric:
            best_metric = metric
            best_pos = pos

    return {'pos': best_pos, 'metric': best_metric} if best_pos >= 0 else None


def _wu_cut(box: dict, wt, mL, mA, mB, m2, BINS: int):
    """Find best split across all 3 axes. Returns [box1, box2] or None."""
    r0, r1 = box['r0'], box['r1']
    g0, g1 = box['g0'], box['g1']
    b0, b1 = box['b0'], box['b1']

    best_axis = -1
    best_pos = -1
    best_metric = float('-inf')

    if r1 > r0 + 1:
        res = _wu_maximize(box, 0, wt, mL, mA, mB, m2, BINS)
        if res and res['metric'] > best_metric:
            best_metric = res['metric']; best_axis = 0; best_pos = res['pos']

    if g1 > g0 + 1:
        res = _wu_maximize(box, 1, wt, mL, mA, mB, m2, BINS)
        if res and res['metric'] > best_metric:
            best_metric = res['metric']; best_axis = 1; best_pos = res['pos']

    if b1 > b0 + 1:
        res = _wu_maximize(box, 2, wt, mL, mA, mB, m2, BINS)
        if res and res['metric'] > best_metric:
            best_metric = res['metric']; best_axis = 2; best_pos = res['pos']

    if best_axis < 0:
        return None

    box1 = {'r0': r0, 'r1': r1, 'g0': g0, 'g1': g1, 'b0': b0, 'b1': b1}
    box2 = {'r0': r0, 'r1': r1, 'g0': g0, 'g1': g1, 'b0': b0, 'b1': b1}

    if best_axis == 0:
        box1['r1'] = best_pos; box2['r0'] = best_pos
    elif best_axis == 1:
        box1['g1'] = best_pos; box2['g0'] = best_pos
    else:
        box1['b1'] = best_pos; box2['b0'] = best_pos

    return [box1, box2]


def _split_loop_wu(colors: list, target_colors: int, tuning, sector_energy, covered_sectors, initial_boxes: list) -> list:
    """Wu histogram-based splitting loop. Returns list of {colors} box dicts."""
    BINS = 33
    BIN_COUNT = 32
    size = BINS * BINS * BINS

    wt = [0.0] * size
    mL = [0.0] * size
    mA = [0.0] * size
    mB = [0.0] * size
    m2 = [0.0] * size

    # Find Lab ranges for binning
    min_l = min_a = min_b = float('inf')
    max_l = max_a = max_b = float('-inf')
    for c in colors:
        if c['L'] < min_l: min_l = c['L']
        if c['L'] > max_l: max_l = c['L']
        if c['a'] < min_a: min_a = c['a']
        if c['a'] > max_a: max_a = c['a']
        if c['b'] < min_b: min_b = c['b']
        if c['b'] > max_b: max_b = c['b']

    range_l = max_l - min_l or 1
    range_a = max_a - min_a or 1
    range_b = max_b - min_b or 1

    # Map each colour to bin indices (1-32)
    color_bins = []  # list of (bl, ba, bb) per colour
    for c in colors:
        bl = min(BIN_COUNT, max(1, 1 + int((c['L'] - min_l) / range_l * (BIN_COUNT - 1))))
        ba = min(BIN_COUNT, max(1, 1 + int((c['a'] - min_a) / range_a * (BIN_COUNT - 1))))
        bb = min(BIN_COUNT, max(1, 1 + int((c['b'] - min_b) / range_b * (BIN_COUNT - 1))))
        color_bins.append((bl, ba, bb))

        idx = bl * BINS * BINS + ba * BINS + bb
        w = c.get('count', 1)
        wt[idx] += w
        mL[idx] += c['L'] * w
        mA[idx] += c['a'] * w
        mB[idx] += c['b'] * w
        m2[idx] += w * (c['L'] ** 2 + c['a'] ** 2 + c['b'] ** 2)

    _wu_cumulative_moments(wt, mL, mA, mB, m2, BINS)

    # Initialise Wu boxes
    if len(initial_boxes) > 1:
        wu_boxes = []
        for ibox in initial_boxes:
            ibox_set = {(ic['L'], ic['a'], ic['b']) for ic in ibox['colors']}
            r0 = g0 = b0 = BIN_COUNT
            r1 = g1 = b1 = 0
            for i, c in enumerate(colors):
                if (c['L'], c['a'], c['b']) in ibox_set:
                    bl, ba, bb = color_bins[i]
                    if bl < r0: r0 = bl
                    if bl > r1: r1 = bl
                    if ba < g0: g0 = ba
                    if ba > g1: g1 = ba
                    if bb < b0: b0 = bb
                    if bb > b1: b1 = bb
            wu_boxes.append({'r0': r0 - 1, 'r1': r1, 'g0': g0 - 1, 'g1': g1, 'b0': b0 - 1, 'b1': b1})
    else:
        wu_boxes = [{'r0': 0, 'r1': BIN_COUNT, 'g0': 0, 'g1': BIN_COUNT, 'b0': 0, 'b1': BIN_COUNT}]

    variances = [_wu_variance(b, wt, mL, mA, mB, m2, BINS) for b in wu_boxes]

    while len(wu_boxes) < target_colors:
        best_idx = -1
        best_var = 0.0

        for i, box in enumerate(wu_boxes):
            priority = variances[i]
            if sector_energy and priority > 0:
                centroid = _wu_box_centroid(box, wt, mL, mA, mB, BINS)
                chroma = math.sqrt(centroid['a'] ** 2 + centroid['b'] ** 2)
                if chroma >= 10:
                    hue = ((math.atan2(centroid['b'], centroid['a']) * 180 / math.pi) + 360) % 360
                    sector = int(hue / 30) % 12
                    if sector not in covered_sectors and sector_energy[sector] > 0:
                        priority *= 5.0
            if priority > best_var:
                best_var = priority
                best_idx = i

        if best_idx < 0 or best_var <= 0:
            break

        cut = _wu_cut(wu_boxes[best_idx], wt, mL, mA, mB, m2, BINS)
        if cut is None:
            variances[best_idx] = 0
            continue

        box1, box2 = cut
        wu_boxes[best_idx] = box1
        variances[best_idx] = _wu_variance(box1, wt, mL, mA, mB, m2, BINS)
        wu_boxes.append(box2)
        variances.append(_wu_variance(box2, wt, mL, mA, mB, m2, BINS))

        if sector_energy:
            for b in (box1, box2):
                cent = _wu_box_centroid(b, wt, mL, mA, mB, BINS)
                c = math.sqrt(cent['a'] ** 2 + cent['b'] ** 2)
                if c >= 10:
                    h = ((math.atan2(cent['b'], cent['a']) * 180 / math.pi) + 360) % 360
                    covered_sectors.add(int(h / 30) % 12)

    # Map Wu boxes back to colour arrays
    box_colors = [[] for _ in wu_boxes]
    for i, c in enumerate(colors):
        bl, ba, bb = color_bins[i]
        assigned = False
        for j, box in enumerate(wu_boxes):
            if (bl > box['r0'] and bl <= box['r1'] and
                ba > box['g0'] and ba <= box['g1'] and
                bb > box['b0'] and bb <= box['b1']):
                box_colors[j].append(c)
                assigned = True
                break
        if not assigned:
            # Boundary crack — assign to nearest centroid
            best_j = 0
            best_dist = float('inf')
            for j, box in enumerate(wu_boxes):
                cent = _wu_box_centroid(box, wt, mL, mA, mB, BINS)
                dist = (c['L'] - cent['L']) ** 2 + (c['a'] - cent['a']) ** 2 + (c['b'] - cent['b']) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_j = j
            box_colors[best_j].append(c)

    return [
        {'colors': bc, 'depth': 0, 'grayscale_only': False}
        for bc in box_colors if bc
    ]


# ---------------------------------------------------------------------------
# Color space analysis
# ---------------------------------------------------------------------------

def _analyze_color_space(lab_pixels) -> dict:
    """Return {chroma_range, range_a, range_b} for the pixel set."""
    min_a = min_b = float('inf')
    max_a = max_b = float('-inf')

    for i in range(0, len(lab_pixels), 3):
        a = lab_pixels[i + 1]
        b = lab_pixels[i + 2]
        if a < min_a: min_a = a
        if a > max_a: max_a = a
        if b < min_b: min_b = b
        if b > max_b: max_b = b

    range_a = max_a - min_a
    range_b = max_b - min_b
    return {'chroma_range': max(range_a, range_b), 'range_a': range_a, 'range_b': range_b}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def median_cut_in_lab_space(
    lab_pixels,
    target_colors: int,
    grayscale_only: bool = False,
    width=None,
    height=None,
    substrate_lab: dict | None = None,
    substrate_tolerance: float = 3.5,
    vibrancy_mode: str = 'aggressive',
    vibrancy_boost: float = 2.0,
    highlight_threshold: float = 92,
    highlight_boost: float = 3.0,
    strategy=None,
    tuning: dict | None = None,
) -> dict:
    """Core Lab-space quantization.

    lab_pixels: flat list [L, a, b, ...] in perceptual space.
    Returns {'palette', 'all_colors', 'lab_pixels'}.
    palette: list of {L, a, b} dicts (representative colour per box).
    all_colors: deduplicated {L, a, b, count} list (for hue gap analysis).
    """
    GRID_STRIDE = 4
    total_pixels = len(lab_pixels) // 3

    config = tuning if tuning is not None else _DEFAULT_TUNING
    is_16bit = config.get('centroid', {}).get('bitDepth') == 16

    # Deduplicate and grid-sample
    if grayscale_only:
        l_map = {}
        for i in range(0, len(lab_pixels), 3 * GRID_STRIDE):
            L = lab_pixels[i]
            key = f'{L:.2f}'
            if key in l_map:
                l_map[key]['count'] += 1
            else:
                l_map[key] = {'L': L, 'a': 0, 'b': 0, 'count': 1}
        colors = sorted(l_map.values(), key=lambda c: c['L'])
    else:
        lab_map = {}
        for i in range(0, len(lab_pixels), 3 * GRID_STRIDE):
            L = lab_pixels[i]
            a = lab_pixels[i + 1]
            b = lab_pixels[i + 2]

            if substrate_lab:
                dL = L - substrate_lab['L']
                da = a - substrate_lab['a']
                db = b - substrate_lab['b']
                if dL * dL + da * da + db * db < substrate_tolerance ** 2:
                    continue

            key = f'{L:.2f},{a:.2f},{b:.2f}'
            if key in lab_map:
                lab_map[key]['count'] += 1
            else:
                lab_map[key] = {'L': L, 'a': a, 'b': b, 'count': 1}

        colors = sorted(lab_map.values(), key=lambda c: (c['L'], c['a'], c['b']))

    hue_chroma_threshold = 1.0 if (vibrancy_mode == 'exponential' or is_16bit) else 5.0
    sector_energy = (
        None if grayscale_only
        else _analyze_image_hue_sectors(lab_pixels, hue_chroma_threshold)
    )
    covered_sectors: set = set()

    # Neutral isolation (optional pre-split)
    neutral_isolation_threshold = (
        config.get('split', {}).get('neutralIsolationThreshold', 0)
        if tuning else 0
    )
    if not grayscale_only and neutral_isolation_threshold > 0:
        neutral_colors = [c for c in colors if math.sqrt(c['a'] ** 2 + c['b'] ** 2) < neutral_isolation_threshold]
        chromatic_colors = [c for c in colors if math.sqrt(c['a'] ** 2 + c['b'] ** 2) >= neutral_isolation_threshold]
        if neutral_colors and chromatic_colors:
            boxes = [
                {'colors': neutral_colors, 'depth': 0, 'grayscale_only': grayscale_only},
                {'colors': chromatic_colors, 'depth': 0, 'grayscale_only': grayscale_only},
            ]
        else:
            boxes = [{'colors': colors, 'depth': 0, 'grayscale_only': grayscale_only}]
    else:
        boxes = [{'colors': colors, 'depth': 0, 'grayscale_only': grayscale_only}]

    split_mode = config.get('split', {}).get('splitMode', 'median') if tuning else 'median'
    quantizer = config.get('split', {}).get('quantizer', 'median-cut') if tuning else 'median-cut'

    if quantizer == 'wu' and not grayscale_only:
        boxes = _split_loop_wu(colors, target_colors, tuning, sector_energy, covered_sectors, boxes)
    else:
        while len(boxes) < target_colors:
            if split_mode == 'variance':
                boxes.sort(key=lambda b: _calculate_box_sse(b, tuning), reverse=True)
            else:
                boxes.sort(
                    key=lambda b: _calculate_split_priority(
                        b, sector_energy, covered_sectors, grayscale_only,
                        5.0, vibrancy_mode, vibrancy_boost, highlight_threshold, highlight_boost, tuning,
                    ),
                    reverse=True,
                )

            if boxes[0]['colors'] and len(boxes[0]['colors']) == 1:
                break

            box = boxes.pop(0)
            box1, box2 = _split_box_lab(box, grayscale_only, tuning)

            if box1 and box2:
                boxes.append(box1)
                boxes.append(box2)

                if not grayscale_only and sector_energy:
                    COVERAGE_CHROMA_MIN = 10.0
                    for new_box in (box1, box2):
                        meta = _calculate_box_metadata(new_box, grayscale_only, tuning)
                        if meta['sector'] >= 0:
                            c_val = math.sqrt(meta['meanA'] ** 2 + meta['meanB'] ** 2)
                            if c_val >= COVERAGE_CHROMA_MIN:
                                covered_sectors.add(meta['sector'])
            else:
                boxes.append(box)
                break

    # Green rescue (16-bit mode)
    green_energy = (sector_energy[3] + sector_energy[4]) if sector_energy else 0
    GREEN_RESCUE_THRESHOLD = 1.5
    should_rescue_green = not grayscale_only and green_energy > GREEN_RESCUE_THRESHOLD and is_16bit

    best_green_box_idx = -1
    best_green_count = 0

    if should_rescue_green:
        for idx, box in enumerate(boxes):
            green_count = 0
            for c in box['colors']:
                chroma = math.sqrt(c['a'] ** 2 + c['b'] ** 2)
                if chroma < 0.5:
                    continue
                hue = math.atan2(c['b'], c['a']) * (180 / math.pi)
                norm_hue = hue if hue >= 0 else hue + 360
                sector = int(norm_hue / 30)
                if sector in (3, 4):
                    green_count += 1
            if green_count > best_green_count:
                best_green_count = green_count
                best_green_box_idx = idx

    # Isolation threshold filter
    isolation_threshold = config.get('prune', {}).get('isolationThreshold', 0.0) if tuning else 0.0
    if isolation_threshold > 0:
        min_pixels = total_pixels * (isolation_threshold / 2500)
        filtered = [b for b in boxes if len(b['colors']) >= min_pixels]
        if len(filtered) >= target_colors:
            boxes = filtered

    # Build palette — centroid per box
    palette = []
    for idx, box in enumerate(boxes):
        if should_rescue_green and idx == best_green_box_idx and best_green_count > 5:
            green_colors = []
            for c in box['colors']:
                chroma = math.sqrt(c['a'] ** 2 + c['b'] ** 2)
                if chroma < 0.5:
                    continue
                hue = math.atan2(c['b'], c['a']) * (180 / math.pi)
                norm_hue = hue if hue >= 0 else hue + 360
                sector = int(norm_hue / 30)
                if sector in (3, 4):
                    green_colors.append(c)
            if green_colors:
                palette.append(_calculate_lab_centroid(green_colors, grayscale_only, strategy, tuning))
                continue

        palette.append(_calculate_lab_centroid(box['colors'], grayscale_only, strategy, tuning))

    return {
        'palette': palette,
        'all_colors': colors,
        'lab_pixels': lab_pixels,
    }

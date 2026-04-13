"""
CentroidStrategies — centroid selection methods for Lab median cut.

Each strategy determines how a representative colour is chosen from a
bucket of Lab pixel samples.  Input data is always in perceptual Lab space
(L: 0-100, a/b: -128..+127) regardless of source bit depth.

weights dict keys (camelCase — matches JS CentroidStrategies and _build_tuning_from_config):
  lWeight:      Lightness priority (default 1.0)
  cWeight:      Chroma priority (default 1.0)
  blackBias:    Black-pixel score boost (default 5.0)
  bitDepth:     8 or 16 (16-bit uses tighter thresholds)
  vibrancyMode: 'subtle'|'moderate'|'aggressive'|'exponential'
  vibrancyBoost: chroma power/scale factor (default 2.2)
  isVibrant:    bool — uses 2% slice instead of 5%
"""

from __future__ import annotations

import math


def saliency(bucket: list, weights: dict | None = None) -> dict:
    """THE 'HANDMADE' STRATEGY — averages top 5% pixels by saliency score.

    Prevents stray outliers while maintaining vibrancy.
    BLACK PROTECTION: massive boost for very dark pixels (L < 10).
    AGGRESSIVE MODE: boosts a* values in the pink zone to rescue reds.
    """
    if not bucket:
        return {'L': 50, 'a': 0, 'b': 0}
    if weights is None:
        weights = {}

    # Support both camelCase (JS heritage) and snake_case (Pythonic)
    black_bias     = weights.get('blackBias', weights.get('black_bias', 5.0))
    is_16bit       = weights.get('bitDepth', weights.get('bit_depth', 8)) == 16
    vibrancy_mode  = weights.get('vibrancyMode', weights.get('vibrancy_mode', 'moderate'))
    vibrancy_boost = weights.get('vibrancyBoost', weights.get('vibrancy_boost', 2.2))
    l_weight       = weights.get('lWeight', weights.get('l_weight', 1.0))
    c_weight       = weights.get('cWeight', weights.get('c_weight', 1.0))
    is_vibrant     = weights.get('isVibrant', weights.get('is_vibrant', False))

    # Step 1: Normalize precision to 4dp — eliminates JS Float32Array vs Python float64
    # divergence so every downstream sort and comparison is deterministic across platforms.
    def _norm(p):
        return {
            'L': round(p['L'], 4),
            'a': round(p['a'], 4),
            'b': round(p['b'], 4),
            'count': p.get('count', 1),
        }
    normalized = [_norm(p) for p in bucket]

    # Achromatic exclusion wall (only when c_weight >= 2.5)
    achromatic_floor = 15.0 if c_weight >= 2.5 else 0.0
    
    # Calculate chroma once for all pixels
    with_chroma = []
    for p in normalized:
        with_chroma.append((p, math.sqrt(p['a']**2 + p['b']**2)))

    # Primary path: only consider pixels above the achromatic floor
    # If achromatic_floor is 0, everyone is eligible
    eligible = [pair for pair in with_chroma if pair[1] >= achromatic_floor]

    # Fallback to volumetric average of ALL pixels if no chromatic pixels are eligible
    if not eligible:
        total_px = sum_l = sum_a = sum_b = 0.0
        for p in normalized:
            cnt = p.get('count', 1)
            sum_l += p['L'] * cnt; sum_a += p['a'] * cnt; sum_b += p['b'] * cnt
            total_px += cnt
        if total_px == 0: return {'L': 50, 'a': 0, 'b': 0}
        return {'L': sum_l / total_px, 'a': sum_a / total_px, 'b': sum_b / total_px}

    def _hue_sector(a: float, b: float) -> int:
        return int(((math.atan2(b, a) * 180 / math.pi) + 360) % 360 / 30)

    scored = []
    total_eligible_px = 0
    for p, chroma in eligible:
        cnt = p.get('count', 1)
        total_eligible_px += cnt
        sector = _hue_sector(p['a'], p['b'])

        chroma_value = (math.pow(chroma, 1 / vibrancy_boost)
                        if vibrancy_mode == 'exponential' and chroma > 0
                        else chroma)

        # Brown dampener: penalise low-chroma warm pixels (sectors 0/1) on 8-bit sources
        chroma_w = (c_weight * 0.5
                    if not is_16bit and chroma < 8 and sector in (0, 1)
                    else c_weight)

        black_boost = (10 - p['L']) * black_bias if p['L'] < 10 else 0
        score = p['L'] * l_weight + chroma_value * chroma_w + black_boost
        scored.append((score, p))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Take top 5% (or 2% if vibrant) of POPULATION
    slice_pct = 0.02 if is_vibrant else 0.05
    target_px = max(1.0, total_eligible_px * slice_pct)

    is_aggressive = vibrancy_mode == 'aggressive'
    accum_px = 0.0
    sum_l = sum_a = sum_b = 0.0
    
    for _, p in scored:
        cnt = float(p.get('count', 1))
        # How many pixels from this bucket can we fit in our top-X% sample?
        take = min(cnt, target_px - accum_px)
        if take <= 0:
            break
            
        sum_l += p['L'] * take
        raw_a = p['a']
        # Boost pink-zone a* (0 < a < 50) toward red
        sum_a += (raw_a * 1.6 if (is_aggressive and 0 < raw_a < 50) else raw_a) * take
        sum_b += p['b'] * take
        accum_px += take

    if accum_px < 0.0001:
        return {'L': 50, 'a': 0, 'b': 0}
        
    result = {'L': sum_l / accum_px, 'a': sum_a / accum_px, 'b': sum_b / accum_px}

    # Neutrality gate: snap very low chroma to perfect neutral
    neutrality_thr = 0.0 if is_16bit else 5.0
    if math.sqrt(result['a'] ** 2 + result['b'] ** 2) < neutrality_thr:
        result['a'] = 0
        result['b'] = 0

    return result


def robust_saliency(bucket: list, weights: dict | None = None) -> dict:
    """THE 'ROBUST' STRATEGY — population-weighted with P90 chroma winsorisation.

    Designed for warm archetypes where SALIENCY's top-5% slice inflates centroids.
    Matches Photoshop Indexed Color behaviour for warm tones.
    """
    if not bucket:
        return {'L': 50, 'a': 0, 'b': 0}
    if weights is None:
        weights = {}

    black_bias     = weights.get('blackBias', 5.0)
    is_16bit       = weights.get('bitDepth', 8) == 16
    c_weight       = weights.get('cWeight', 1.0)
    vibrancy_boost = weights.get('vibrancyBoost', 1.0)

    # P90 chroma winsorisation: cap extreme chroma while preserving hue angle
    chromas = [math.sqrt(p['a'] ** 2 + p['b'] ** 2) for p in bucket]
    sorted_c = sorted(chromas)
    p90_idx = min(len(sorted_c) - 1, int(len(sorted_c) * 0.90))
    chroma_cap = sorted_c[p90_idx]

    working = []
    for p, c in zip(bucket, chromas):
        if c <= chroma_cap or chroma_cap == 0:
            working.append(p)
        else:
            scale = chroma_cap / c
            working.append({'L': p['L'], 'a': p['a'] * scale, 'b': p['b'] * scale,
                            'count': p.get('count', 1)})

    # Achromatic exclusion wall
    achromatic_floor = 15.0 if c_weight >= 2.5 else 0.0
    eligible = ([p for p in working if math.sqrt(p['a'] ** 2 + p['b'] ** 2) >= achromatic_floor]
                if achromatic_floor > 0 else working)

    if not eligible:
        total_w = sum_l = sum_a = sum_b = 0
        for p in working:
            w = p.get('count', 1)
            sum_l += p['L'] * w; sum_a += p['a'] * w; sum_b += p['b'] * w
            total_w += w
        return {'L': sum_l / total_w, 'a': sum_a / total_w, 'b': sum_b / total_w}

    # Green exclusion: filter out negative-a* in mixed warm/cool buckets (c_weight >= 2.5)
    if c_weight >= 2.5:
        has_warm = any(p['a'] > 0 and p['b'] > 30 for p in eligible)
        has_cool = any(p['a'] < -5 and p['b'] > 20 for p in eligible)
        if has_warm and has_cool:
            warm_only = [p for p in eligible if p['a'] >= 0]
            if warm_only:
                eligible = warm_only

    # Population-weighted average with black protection
    sum_l = sum_a = sum_b = total_w = 0.0
    for p in eligible:
        w = p.get('count', 1)
        if p['L'] < 10:
            w *= 1 + (10 - p['L']) * black_bias
        sum_l += p['L'] * w
        sum_a += p['a'] * w
        sum_b += p['b'] * w
        total_w += w

    result = {'L': sum_l / total_w, 'a': sum_a / total_w, 'b': sum_b / total_w}

    # Vibrancy control: scale chroma around population mean
    if vibrancy_boost != 1.0:
        centroid_c = math.sqrt(result['a'] ** 2 + result['b'] ** 2)
        if centroid_c > 5.0:
            if vibrancy_boost > 1.0:
                target_c = min(centroid_c + (chroma_cap - centroid_c) * (vibrancy_boost - 1.0),
                               chroma_cap)
            else:
                target_c = centroid_c * vibrancy_boost
            if target_c != centroid_c:
                scale = target_c / centroid_c
                result['a'] *= scale
                result['b'] *= scale

    # Neutrality gate
    neutrality_thr = 0.0 if is_16bit else 5.0
    if math.sqrt(result['a'] ** 2 + result['b'] ** 2) < neutrality_thr:
        result['a'] = 0
        result['b'] = 0

    return result


def volumetric(bucket: list, weights: dict | None = None) -> dict:
    """THE 'BALANCED' STRATEGY — simple pixel-count-weighted average."""
    if not bucket:
        return {'L': 50, 'a': 0, 'b': 0}

    total_w = sum_l = sum_a = sum_b = 0.0
    for p in bucket:
        w = p.get('count', 1)
        sum_l += p['L'] * w
        sum_a += p['a'] * w
        sum_b += p['b'] * w
        total_w += w

    return {'L': sum_l / total_w, 'a': sum_a / total_w, 'b': sum_b / total_w}


# Strategy dispatch table
CENTROID_STRATEGIES = {
    'SALIENCY':        saliency,
    'ROBUST_SALIENCY': robust_saliency,
    'VOLUMETRIC':      volumetric,
}

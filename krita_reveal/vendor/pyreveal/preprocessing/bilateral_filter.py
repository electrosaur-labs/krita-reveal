"""
BilateralFilter — Edge-preserving image preprocessing.

Bilateral filter for noise reduction while preserving edges.
Part of the 3-Level Perceptual Rescue System:
  Level 1: DNA (Archetype Detection)
  Level 2: Entropy (Bilateral Filter) — this module
  Level 3: Complexity (CIE2000 Override)

Primary API (16-bit Lab engine space):
  calculate_entropy_score_lab()  — entropy from 16-bit Lab channels
  apply_bilateral_filter_lab()   — filter 16-bit Lab data in-place

Deprecated (8-bit RGBA, external tools only):
  calculate_entropy_score()
  apply_bilateral_filter()
"""

from __future__ import annotations

import math


# ── Preprocessing intensity constants ────────────────────────────────────────

class PreprocessingIntensity:
    OFF = 'off'
    AUTO = 'auto'
    LIGHT = 'light'
    MEDIUM = 'medium'
    HEAVY = 'heavy'


# ── 16-bit Lab primary API ────────────────────────────────────────────────────

def calculate_entropy_score_lab(
    lab_data,
    width: int,
    height: int,
    sample_rate: int = 4,
) -> float:
    """Calculate entropy score from 16-bit Lab data.

    Measures local variance across all three Lab channels to detect noise.
    Returns the maximum entropy found in any channel.

    lab_data: flat sequence of 16-bit Lab values (L,a,b per pixel, 0-32768).
    Returns entropy score 0-100 (higher = noisier).
    """
    if not lab_data or width <= 0 or height <= 0:
        return 0.0

    expected = width * height * 3
    if len(lab_data) < expected:
        return 0.0

    l_scale = 255 / 32768    # L:  0-32768 → 0-255
    ab_scale = 255 / 32768   # a/b same range

    max_entropy = 0.0

    for channel in range(3):
        scale = l_scale if channel == 0 else ab_scale
        total_variance = 0.0
        sample_count = 0

        for y in range(1, height - 1, sample_rate):
            for x in range(1, width - 1, sample_rate):
                idx = (y * width + x) * 3 + channel

                s = ss = 0.0
                n = 0
                for dy in range(-1, 2):
                    for dx in range(-1, 2):
                        nidx = ((y + dy) * width + (x + dx)) * 3 + channel
                        val = lab_data[nidx] * scale
                        s += val
                        ss += val * val
                        n += 1

                mean = s / n
                variance = (ss / n) - (mean * mean)
                if variance > 0:
                    total_variance += math.sqrt(variance)
                sample_count += 1

        avg_variance = total_variance / max(1, sample_count)
        channel_entropy = min(100.0, avg_variance * 2)
        if channel_entropy > max_entropy:
            max_entropy = channel_entropy

    return max_entropy


def apply_bilateral_filter_lab(
    lab_data: list,
    width: int,
    height: int,
    radius: int = 4,
    sigma_r: float = 3000,
) -> None:
    """Apply bilateral filter to 16-bit Lab data in-place.

    Edge-preserving smoothing using spatial and range Gaussian weights.
    sigma_r is in 16-bit units (0-32768 range) — no internal scaling.
      - 16-bit source: sigma_r ≈ 5000 (~15 % of L range)
      - 8-bit source:  sigma_r ≈ 3000 (more conservative)

    Modifies lab_data in place.
    """
    sigma_r2x2 = 2.0 * sigma_r * sigma_r

    # Pre-compute LUT: map quantized 3D Lab distances → Gaussian weight
    lut_scale = 256.0 / 32768.0
    exp_lut = [math.exp(-((i / lut_scale) ** 2) / sigma_r2x2) for i in range(256)]

    # Clone original for read-only reference
    original = list(lab_data)

    step = 2 if radius > 3 else 1

    for y in range(height):
        for x in range(width):
            idx = (y * width + x) * 3
            cL = original[idx]
            cA = original[idx + 1]
            cB = original[idx + 2]

            sum_l = sum_a = sum_b = weight_sum = 0.0

            for dy in range(-radius, radius + 1, step):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in range(-radius, radius + 1, step):
                    nx = x + dx
                    if nx < 0 or nx >= width:
                        continue

                    nidx = (ny * width + nx) * 3
                    nL = original[nidx]
                    nA = original[nidx + 1]
                    nB = original[nidx + 2]

                    dL = cL - nL
                    dA = cA - nA
                    dB = cB - nB
                    color_dist = math.sqrt(dL * dL + dA * dA + dB * dB)

                    lut_idx = min(255, int(color_dist * lut_scale))
                    weight = exp_lut[lut_idx]

                    sum_l += nL * weight
                    sum_a += nA * weight
                    sum_b += nB * weight
                    weight_sum += weight

            if weight_sum > 0:
                lab_data[idx] = round(sum_l / weight_sum)
                lab_data[idx + 1] = round(sum_a / weight_sum)
                lab_data[idx + 2] = round(sum_b / weight_sum)


# ── 8-bit RGBA API (deprecated, external tools only) ─────────────────────────

def calculate_entropy_score(
    image_data,
    width: int,
    height: int,
    sample_rate: int = 4,
) -> float:
    """Calculate entropy score from 8-bit RGBA image data.

    image_data: flat sequence of RGBA bytes (4 bytes per pixel).
    Returns entropy score 0-100 (higher = noisier).

    Deprecated: prefer calculate_entropy_score_lab for engine pipelines.
    """
    total_variance = 0.0
    sample_count = 0

    for y in range(1, height - 1, sample_rate):
        for x in range(1, width - 1, sample_rate):
            s = ss = 0.0
            n = 0
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    nidx = ((y + dy) * width + (x + dx)) * 4
                    val = image_data[nidx]
                    s += val
                    ss += val * val
                    n += 1
            mean = s / n
            variance = (ss / n) - (mean * mean)
            total_variance += math.sqrt(max(0.0, variance))
            sample_count += 1

    avg_variance = total_variance / max(1, sample_count)
    return min(100.0, avg_variance * 2)


def apply_bilateral_filter(
    image_data: list,
    width: int,
    height: int,
    radius: int = 4,
    sigma_r: float = 30,
) -> None:
    """Apply bilateral filter to 8-bit RGBA image data in-place.

    Deprecated: prefer apply_bilateral_filter_lab for engine pipelines.
    """
    sigma_r2x2 = 2.0 * sigma_r * sigma_r
    exp_lut = [math.exp(-(d * d) / sigma_r2x2) for d in range(256)]

    original = list(image_data)
    step = 2 if radius > 3 else 1

    for y in range(height):
        for x in range(width):
            idx = (y * width + x) * 4
            cR = original[idx]
            cG = original[idx + 1]
            cB = original[idx + 2]

            sum_r = sum_g = sum_b = weight_sum = 0.0

            for dy in range(-radius, radius + 1, step):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in range(-radius, radius + 1, step):
                    nx = x + dx
                    if nx < 0 or nx >= width:
                        continue

                    nidx = (ny * width + nx) * 4
                    color_dist = (abs(cR - original[nidx]) +
                                  abs(cG - original[nidx + 1]) +
                                  abs(cB - original[nidx + 2]))
                    range_weight = exp_lut[min(255, int(color_dist / 3))]
                    weight = range_weight

                    sum_r += original[nidx] * weight
                    sum_g += original[nidx + 1] * weight
                    sum_b += original[nidx + 2] * weight
                    weight_sum += weight

            if weight_sum > 0:
                image_data[idx] = round(sum_r / weight_sum)
                image_data[idx + 1] = round(sum_g / weight_sum)
                image_data[idx + 2] = round(sum_b / weight_sum)


# ── Decision logic ────────────────────────────────────────────────────────────

def get_filter_params(
    entropy_score: float,
    peak_chroma: float = 0,
    is_16bit: bool = False,
) -> dict:
    """Return filter radius and sigma_r for given entropy score.

    sigma_r is in 16-bit L units (0-32768 range).
    """
    base_sigma_r = 5000 if is_16bit else 3000
    if entropy_score <= 40:
        return {'radius': 3, 'sigmaR': base_sigma_r}
    return {'radius': 5, 'sigmaR': base_sigma_r}


def should_preprocess(dna: dict, entropy_score: float, is_16bit: bool = False) -> dict:
    """Determine whether preprocessing should be applied.

    Returns {'shouldProcess': bool, 'reason': str, ...filter params if True}.
    """
    archetype = (dna.get('archetype') or '').lower()
    peak_chroma = dna.get('maxC') or 0

    very_low_threshold = 2 if is_16bit else 15
    bit_label = '16-bit' if is_16bit else '8-bit'

    detail_rescue = dna.get('detailRescue') or 0
    if detail_rescue > 0:
        very_low_threshold = max(0, very_low_threshold - detail_rescue)

    if 'vector' in archetype or 'flat' in archetype:
        return {'shouldProcess': False, 'reason': 'Vector/Flat - preserving sharp edges'}

    if entropy_score < very_low_threshold:
        return {
            'shouldProcess': False,
            'reason': f'Very low entropy ({entropy_score:.1f}, {bit_label}) - already clean',
        }

    params = get_filter_params(entropy_score, peak_chroma, is_16bit)
    return {
        'shouldProcess': True,
        'reason': f'{bit_label} noise reduction (entropy {entropy_score:.1f})',
        **params,
    }


def create_preprocessing_config(
    dna: dict,
    image_data=None,
    width: int = 0,
    height: int = 0,
    intensity_override: str = 'auto',
) -> dict:
    """Create preprocessing configuration based on DNA analysis.

    image_data: optional pixel data for entropy calculation.
      - 16-bit Lab: flat sequence of 16-bit values, 3 per pixel
      - 8-bit RGBA: flat sequence of bytes, 4 per pixel
    intensity_override: 'off' | 'auto' | 'light' | 'medium' | 'heavy'
    Returns preprocessing config dict with 'enabled', 'radius', 'sigmaR', etc.
    """
    if intensity_override == 'off':
        return {'enabled': False, 'intensity': 'off', 'reason': 'Disabled by user'}

    if intensity_override == 'light':
        return {'enabled': True, 'intensity': 'light', 'radius': 3, 'sigmaR': 30,
                'reason': 'Light filter (user override)'}

    if intensity_override == 'medium':
        return {'enabled': True, 'intensity': 'medium', 'radius': 4, 'sigmaR': 37,
                'reason': 'Medium filter (user override)'}

    if intensity_override == 'heavy':
        return {'enabled': True, 'intensity': 'heavy', 'radius': 5, 'sigmaR': 45,
                'reason': 'Heavy filter (user override)'}

    # Auto mode
    entropy_score = 0.0
    is_16bit = False

    if image_data is not None and width > 0 and height > 0:
        pixel_count = width * height
        n = len(image_data)
        if n >= pixel_count * 3:
            if n == pixel_count * 3:
                entropy_score = calculate_entropy_score_lab(image_data, width, height)
                is_16bit = True
            elif n == pixel_count * 4:
                entropy_score = calculate_entropy_score(image_data, width, height)
                is_16bit = False

    decision = should_preprocess(dna, entropy_score, is_16bit)

    if not decision['shouldProcess']:
        return {
            'enabled': False,
            'intensity': 'off',
            'entropyScore': entropy_score,
            'reason': decision['reason'],
        }

    intensity = 'heavy' if decision.get('radius', 3) >= 5 else 'light'
    return {
        'enabled': True,
        'intensity': intensity,
        'radius': decision['radius'],
        'sigmaR': decision['sigmaR'],
        'entropyScore': entropy_score,
        'reason': decision['reason'],
    }

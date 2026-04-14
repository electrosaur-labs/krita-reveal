"""
Lab color distance calculations.

Provides configurable distance metrics for CIELAB color space:
  CIE76:   Standard Euclidean distance (fast)
  CIE94:   Improved perceptual weighting (better for saturated colors)
  CIE2000: State-of-the-art perceptual metric (museum-grade)

Each metric is available in both object-based and inline (scalar) forms.
Native 16-bit integer variants operate directly on engine 16-bit values
without converting to perceptual space.
"""

from __future__ import annotations

import math
from array import array
from .encoding import LAB16_L_MAX, LAB16_AB_NEUTRAL, L_SCALE, AB_SCALE

# ============================================================================
# Constants
# ============================================================================

class DistanceMetric:
    CIE76  = 'cie76'
    CIE94  = 'cie94'
    CIE2000 = 'cie2000'


DEFAULT_CIE94_PARAMS = {'kL': 1, 'k1': 0.045, 'k2': 0.015}

# CIE94 coefficients pre-scaled for 16-bit chroma space:
#   k1_16 = 0.045 / 128 = 0.000352
#   k2_16 = 0.015 / 128 = 0.000117
DEFAULT_CIE94_PARAMS_16 = {'k1': 0.000352, 'k2': 0.000117}

# Perceptual ΔE = 2.0 in 16-bit² units (conservative threshold)
SNAP_THRESHOLD_SQ_16 = 180000

# ============================================================================
# CIE76
# ============================================================================

def cie76(lab1: tuple, lab2: tuple, squared: bool = False) -> float:
    """CIE76 (ΔE*ab) — Euclidean distance in CIELAB space.

    lab1, lab2: (L, a, b) tuples in perceptual space.
    """
    dL = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    dist_sq = dL * dL + da * da + db * db
    return dist_sq if squared else math.sqrt(dist_sq)


def cie76_weighted(lab1: tuple, lab2: tuple, squared: bool = False,
                   shadow_threshold: float = 40.0, shadow_weight: float = 2.0) -> float:
    """CIE76 with L-weighting for shadow preservation.

    Applies increased L weight when average L is below shadow_threshold.
    """
    dL = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    avg_L = (lab1[0] + lab2[0]) / 2
    l_weight = shadow_weight if avg_L < shadow_threshold else 1.0
    dist_sq = (dL * l_weight) ** 2 + da * da + db * db
    return dist_sq if squared else math.sqrt(dist_sq)


def cie76_squared_inline(L1, a1, b1, L2, a2, b2) -> float:
    """CIE76 squared distance — scalar inline form for hot loops."""
    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2
    return dL * dL + da * da + db * db


def cie76_weighted_squared_inline(L1, a1, b1, L2, a2, b2, l_weight: float) -> float:
    """CIE76 weighted squared distance — scalar inline form."""
    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2
    return (dL * l_weight) ** 2 + da * da + db * db

# ============================================================================
# CIE94
# ============================================================================

def cie94(lab1: tuple, lab2: tuple, squared: bool = False,
          params: dict | None = None) -> float:
    """CIE94 (ΔE*94) — improved perceptual distance.

    Addresses CIE76's non-uniformity in high-chroma regions by applying
    chroma-dependent weighting.
    """
    if params is None:
        params = DEFAULT_CIE94_PARAMS
    kL = params.get('kL', 1)
    k1 = params.get('k1', 0.045)
    k2 = params.get('k2', 0.015)

    dL = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]

    C1 = math.sqrt(lab1[1] * lab1[1] + lab1[2] * lab1[2])
    C2 = math.sqrt(lab2[1] * lab2[1] + lab2[2] * lab2[2])
    dC = C1 - C2
    dH_sq = max(0.0, da * da + db * db - dC * dC)

    SL = 1.0
    SC = 1 + k1 * C1
    SH = 1 + k2 * C1

    dist_sq = (dL / (kL * SL)) ** 2 + (dC / SC) ** 2 + dH_sq / (SH * SH)
    return dist_sq if squared else math.sqrt(dist_sq)


def cie94_squared_inline(L1, a1, b1, L2, a2, b2,
                          C1: float = 0.0, k1: float = 0.045, k2: float = 0.015) -> float:
    """CIE94 squared distance — scalar inline form.

    C1 is the pre-computed chroma of the first color. Pass 0 to compute inline.
    """
    if C1 == 0.0:
        C1 = math.sqrt(a1 * a1 + b1 * b1)

    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2

    C2 = math.sqrt(a2 * a2 + b2 * b2)
    dC = C1 - C2
    dH_sq = max(0.0, da * da + db * db - dC * dC)

    SC = 1 + k1 * C1
    SH = 1 + k2 * C1

    return dL * dL + (dC / SC) ** 2 + dH_sq / (SH * SH)

# ============================================================================
# CIE2000
# ============================================================================

def cie2000(lab1: tuple, lab2: tuple, squared: bool = False) -> float:
    """CIE2000 (ΔE*00) — state-of-the-art perceptual distance.

    Best for complex 16-bit files, subtle gradients, museum-grade reproduction,
    and blue/violet tones that confuse CIE94. ~3-4x slower than CIE94.
    """
    dist = cie2000_inline(lab1[0], lab1[1], lab1[2], lab2[0], lab2[1], lab2[2])
    return dist * dist if squared else dist


def cie2000_inline(L1, a1, b1, L2, a2, b2) -> float:
    """Full CIEDE2000 implementation — scalar inline form.

    Uses kL=kC=kH=1 (reference conditions).
    """
    RAD2DEG = 180 / math.pi
    DEG2RAD = math.pi / 180

    # Step 1: C'i and h'i
    C1 = math.sqrt(a1 * a1 + b1 * b1)
    C2 = math.sqrt(a2 * a2 + b2 * b2)
    avg_C = (C1 + C2) / 2

    avg_C7 = avg_C ** 7
    G = 0.5 * (1 - math.sqrt(avg_C7 / (avg_C7 + 6103515625)))  # 25^7

    a1p = a1 * (1 + G)
    a2p = a2 * (1 + G)

    C1p = math.sqrt(a1p * a1p + b1 * b1)
    C2p = math.sqrt(a2p * a2p + b2 * b2)
    avg_Cp = (C1p + C2p) / 2

    h1p = math.atan2(b1, a1p) * RAD2DEG
    if h1p < 0:
        h1p += 360
    h2p = math.atan2(b2, a2p) * RAD2DEG
    if h2p < 0:
        h2p += 360

    # Step 2: ΔL', ΔC', Δh', ΔH'
    dLp = L2 - L1
    dCp = C2p - C1p

    hp_diff = h2p - h1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(hp_diff) <= 180:
        dhp = hp_diff
    elif hp_diff > 180:
        dhp = hp_diff - 360
    else:
        dhp = hp_diff + 360

    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(dhp / 2 * DEG2RAD)

    # Step 3: average hue h̄'
    if C1p * C2p == 0:
        avg_Hp = h1p + h2p
    elif abs(hp_diff) <= 180:
        avg_Hp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        avg_Hp = (h1p + h2p + 360) / 2
    else:
        avg_Hp = (h1p + h2p - 360) / 2

    # Step 4: weighting functions
    T = (1
         - 0.17 * math.cos((avg_Hp - 30) * DEG2RAD)
         + 0.24 * math.cos(2 * avg_Hp * DEG2RAD)
         + 0.32 * math.cos((3 * avg_Hp + 6) * DEG2RAD)
         - 0.20 * math.cos((4 * avg_Hp - 63) * DEG2RAD))

    avg_L = (L1 + L2) / 2
    avg_L50 = avg_L - 50
    SL = 1 + (0.015 * avg_L50 * avg_L50) / math.sqrt(20 + avg_L50 * avg_L50)
    SC = 1 + 0.045 * avg_Cp
    SH = 1 + 0.015 * avg_Cp * T

    # Step 5: rotation term RT
    avg_Cp7 = avg_Cp ** 7
    RC = 2 * math.sqrt(avg_Cp7 / (avg_Cp7 + 6103515625))
    d_theta = 30 * math.exp(-((avg_Hp - 275) / 25) ** 2)
    RT = -RC * math.sin(2 * d_theta * DEG2RAD)

    # Step 6: total difference
    dLpSL = dLp / SL
    dCpSC = dCp / SC
    dHpSH = dHp / SH

    return math.sqrt(dLpSL * dLpSL + dCpSC * dCpSC + dHpSH * dHpSH + RT * dCpSC * dHpSH)


def cie2000_squared_inline(L1, a1, b1, L2, a2, b2) -> float:
    """CIE2000 squared distance — approximation for nearest-neighbor comparisons."""
    dist = cie2000_inline(L1, a1, b1, L2, a2, b2)
    return dist * dist

# ============================================================================
# Native 16-bit Integer Distance Functions
# ============================================================================

def cie76_squared_inline16(L1, a1, b1, L2, a2, b2) -> float:
    """CIE76 squared distance in native engine 16-bit space.

    Relative ordering preserved vs perceptual space — valid for nearest-neighbor.
    """
    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2
    return dL * dL + da * da + db * db


def cie76_weighted_squared_inline16(L1, a1, b1, L2, a2, b2,
                                     shadow_threshold16: int = 13107,
                                     shadow_weight: float = 2.0) -> float:
    """CIE76 weighted squared distance in 16-bit space.

    Default shadow_threshold16=13107 corresponds to L=40% in 16-bit units.
    """
    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2
    avg_L = (L1 + L2) >> 1
    l_weight = shadow_weight if avg_L < shadow_threshold16 else 1.0
    wdL = dL * l_weight
    return wdL * wdL + da * da + db * db


def cie94_squared_inline16(L1, a1, b1, L2, a2, b2,
                            C_ref: float = 0.0,
                            k1_16: float = 0.000352,
                            k2_16: float = 0.000117) -> float:
    """CIE94 squared distance in native 16-bit space.

    C_ref: pre-computed chroma of the reference/palette color (L2,a2,b2).
           Computed inline if 0.
    k1_16, k2_16: CIE94 k1/k2 pre-scaled for 16-bit chroma range (÷128).
    """
    dL = L1 - L2
    da = a1 - a2
    db = b1 - b2

    a1off = a1 - LAB16_AB_NEUTRAL
    b1off = b1 - LAB16_AB_NEUTRAL
    C_test = math.sqrt(a1off * a1off + b1off * b1off)

    if C_ref == 0.0:
        a2off = a2 - LAB16_AB_NEUTRAL
        b2off = b2 - LAB16_AB_NEUTRAL
        C_ref = math.sqrt(a2off * a2off + b2off * b2off)

    dC = C_ref - C_test
    dH_sq = max(0.0, da * da + db * db - dC * dC)

    SC = 1 + k1_16 * C_ref
    SH = 1 + k2_16 * C_ref

    return dL * dL + (dC / SC) ** 2 + dH_sq / (SH * SH)


def prepare_palette_chroma16(palette16: list) -> array:
    """Pre-compute 16-bit chroma values for a palette.

    palette16: list of (L, a, b) tuples in engine 16-bit space.
    Returns float32 array of chroma values (offset from neutral).
    """
    chroma = array('f', [0.0] * len(palette16))
    for i, (_, a, b) in enumerate(palette16):
        a_off = a - LAB16_AB_NEUTRAL
        b_off = b - LAB16_AB_NEUTRAL
        chroma[i] = math.sqrt(a_off * a_off + b_off * b_off)
    return chroma

# ============================================================================
# Factory & Helpers
# ============================================================================

def prepare_palette_chroma(palette: list) -> array:
    """Pre-compute chroma values for a perceptual Lab palette.

    palette: list of (L, a, b) tuples in perceptual space.
    Returns float32 array of chroma values.
    """
    chroma = array('f', [0.0] * len(palette))
    for i, (_, a, b) in enumerate(palette):
        chroma[i] = math.sqrt(a * a + b * b)
    return chroma


def create_distance_calculator(config: dict | None = None):
    """Create a configured distance function (lab1, lab2) → float.

    config keys:
      metric: 'cie76' | 'cie94' | 'cie2000'  (default: 'cie76')
      squared: bool  (default: False)
      weighted: bool  (CIE76 only, default: False)
      shadow_threshold: float  (default: 40.0)
      shadow_weight: float  (default: 2.0)
      cie94_params: dict  (default: DEFAULT_CIE94_PARAMS)
    """
    if config is None:
        config = {}
    metric            = config.get('metric', DistanceMetric.CIE76)
    squared           = config.get('squared', False)
    weighted          = config.get('weighted', False)
    shadow_threshold  = config.get('shadow_threshold', 40.0)
    shadow_weight     = config.get('shadow_weight', 2.0)
    cie94_params      = config.get('cie94_params', DEFAULT_CIE94_PARAMS)

    if metric == DistanceMetric.CIE2000:
        return lambda lab1, lab2: cie2000(lab1, lab2, squared)

    if metric == DistanceMetric.CIE94:
        return lambda lab1, lab2: cie94(lab1, lab2, squared, cie94_params)

    # CIE76 (default)
    if weighted:
        return lambda lab1, lab2: cie76_weighted(lab1, lab2, squared, shadow_threshold, shadow_weight)
    return lambda lab1, lab2: cie76(lab1, lab2, squared)


def normalize_distance_config(options: dict | None = None) -> dict:
    """Normalize raw options into a consistent distance configuration dict."""
    if options is None:
        options = {}
    metric = options.get('distance_metric', DistanceMetric.CIE76)
    color_mode = options.get('color_mode', 'color')
    cie94_params = {**DEFAULT_CIE94_PARAMS, **options.get('cie94_params', {})}
    return {
        'metric':     metric,
        'cie94_params': cie94_params,
        'is_cie76':   metric == DistanceMetric.CIE76,
        'is_cie94':   metric == DistanceMetric.CIE94,
        'is_cie2000': metric == DistanceMetric.CIE2000,
        'is_grayscale': color_mode in ('bw', 'grayscale'),
    }

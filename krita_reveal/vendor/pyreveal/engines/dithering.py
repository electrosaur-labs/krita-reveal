"""
DitheringStrategies — dithering algorithms for Lab color separation.

All algorithms operate in CIELAB perceptual space.
Input: sequence of 16-bit engine-encoded Lab values (3 per pixel,
       L: 0-32768, a/b: 0-32768 neutral=16384).
Output: bytearray of palette indices (one byte per pixel).

Algorithms:
  Error diffusion: floyd_steinberg, atkinson, stucki
  Ordered:         bayer (LPI-aware 8×8 Bayer matrix)
"""

from __future__ import annotations

from array import array

from ..color.encoding import LAB16_L_MAX, LAB16_AB_NEUTRAL, AB_SCALE
from ..color.distance import cie76_squared_inline

# ---------------------------------------------------------------------------
# 8×8 Bayer ordered-dither matrix (values 0-63)
# ---------------------------------------------------------------------------

BAYER_MATRIX = [
    [ 0, 32,  8, 40,  2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44,  4, 36, 14, 46,  6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [ 3, 35, 11, 43,  1, 33,  9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47,  7, 39, 13, 45,  5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]

# ---------------------------------------------------------------------------
# Palette search helpers
# ---------------------------------------------------------------------------

def get_nearest(L: float, a: float, b: float, lab_palette: list) -> int:
    """Return index of nearest palette colour using squared CIE76."""
    min_dist_sq = float('inf')
    best = 0
    for j, p in enumerate(lab_palette):
        d = cie76_squared_inline(L, a, b, p['L'], p['a'], p['b'])
        if d < min_dist_sq:
            min_dist_sq = d
            best = j
    return best


def get_two_nearest(L: float, a: float, b: float, lab_palette: list) -> dict:
    """Return two nearest palette colours (i1/i2, d1/d2) using squared CIE76."""
    d1 = float('inf')
    d2 = float('inf')
    i1 = 0
    i2 = 0
    for j, p in enumerate(lab_palette):
        d = cie76_squared_inline(L, a, b, p['L'], p['a'], p['b'])
        if d < d1:
            d2, i2 = d1, i1
            d1, i1 = d, j
        elif d < d2:
            d2, i2 = d, j
    return {'i1': i1, 'i2': i2, 'd1': d1, 'd2': d2}

# ---------------------------------------------------------------------------
# Error distribution helpers
# ---------------------------------------------------------------------------

def _distribute_floyd_steinberg_error(
    buf: array, x: int, y: int, w: int, h: int,
    eL: float, eA: float, eB: float,
) -> None:
    """Floyd-Steinberg: distribute error to 4 neighbours (7/16 3/16 5/16 1/16)."""
    def add(nx: int, ny: int, weight: float) -> None:
        if 0 <= nx < w and 0 <= ny < h:
            idx = (ny * w + nx) * 3
            buf[idx]     += eL * weight
            buf[idx + 1] += eA * weight
            buf[idx + 2] += eB * weight

    add(x + 1, y,      7 / 16)
    add(x - 1, y + 1,  3 / 16)
    add(x,     y + 1,  5 / 16)
    add(x + 1, y + 1,  1 / 16)


def _distribute_atkinson_error(
    buf: array, x: int, y: int, w: int, h: int,
    eL: float, eA: float, eB: float, weight: float,
) -> None:
    """Atkinson: distribute error to 6 neighbours (1/8 each, 75% total)."""
    def add(nx: int, ny: int) -> None:
        if 0 <= nx < w and 0 <= ny < h:
            idx = (ny * w + nx) * 3
            buf[idx]     += eL * weight
            buf[idx + 1] += eA * weight
            buf[idx + 2] += eB * weight

    add(x + 1, y)
    add(x + 2, y)
    add(x - 1, y + 1)
    add(x,     y + 1)
    add(x + 1, y + 1)
    add(x,     y + 2)


def _distribute_stucki_error(
    buf: array, x: int, y: int, w: int, h: int,
    eL: float, eA: float, eB: float,
) -> None:
    """Stucki: distribute error to 12 neighbours (/42 denominator, 100%)."""
    def add(nx: int, ny: int, weight: int) -> None:
        if 0 <= nx < w and 0 <= ny < h:
            idx = (ny * w + nx) * 3
            f = weight / 42
            buf[idx]     += eL * f
            buf[idx + 1] += eA * f
            buf[idx + 2] += eB * f

    add(x + 1, y, 8);  add(x + 2, y, 4)
    add(x - 2, y + 1, 2); add(x - 1, y + 1, 4); add(x, y + 1, 8)
    add(x + 1, y + 1, 4); add(x + 2, y + 1, 2)
    add(x - 2, y + 2, 1); add(x - 1, y + 2, 2); add(x, y + 2, 4)
    add(x + 1, y + 2, 2); add(x + 2, y + 2, 1)

# ---------------------------------------------------------------------------
# Dithering algorithms
# ---------------------------------------------------------------------------

def floyd_steinberg(raw_bytes, lab_palette: list, width: int, height: int) -> bytearray:
    """Floyd-Steinberg error diffusion in CIELAB space.

    raw_bytes: indexable sequence of 16-bit engine-encoded Lab values (3 per pixel).
    lab_palette: list of {'L': float, 'a': float, 'b': float} in perceptual ranges.
    Returns bytearray of palette indices.
    """
    pixel_count = len(raw_bytes) // 3
    color_indices = bytearray(pixel_count)
    if not lab_palette:
        return color_indices

    error_buf = array('f', [0.0] * (pixel_count * 3))

    for i in range(pixel_count):
        px = i * 3
        y = i // width
        x = i % width

        L = (raw_bytes[px]     / LAB16_L_MAX) * 100   + error_buf[px]
        a = (raw_bytes[px + 1] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 1]
        b = (raw_bytes[px + 2] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 2]

        L = max(0.0, min(100.0, L))
        a = max(-128.0, min(127.0, a))
        b = max(-128.0, min(127.0, b))

        best = 0
        min_d = float('inf')
        for j, pal in enumerate(lab_palette):
            dL = L - pal['L']; da = a - pal['a']; db = b - pal['b']
            d = dL * dL + da * da + db * db
            if d < min_d:
                min_d = d
                best = j

        color_indices[i] = best
        ch = lab_palette[best]
        _distribute_floyd_steinberg_error(
            error_buf, x, y, width, height,
            L - ch['L'], a - ch['a'], b - ch['b'],
        )

    return color_indices


def atkinson(raw_bytes, lab_palette: list, width: int, height: int) -> bytearray:
    """Atkinson error diffusion in CIELAB space (75% error distributed)."""
    pixel_count = len(raw_bytes) // 3
    color_indices = bytearray(pixel_count)
    if not lab_palette:
        return color_indices

    error_buf = array('f', [0.0] * (pixel_count * 3))

    for i in range(pixel_count):
        px = i * 3
        y = i // width
        x = i % width

        L = (raw_bytes[px]     / LAB16_L_MAX) * 100   + error_buf[px]
        a = (raw_bytes[px + 1] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 1]
        b = (raw_bytes[px + 2] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 2]

        L = max(0.0, min(100.0, L))
        a = max(-128.0, min(127.0, a))
        b = max(-128.0, min(127.0, b))

        best = get_nearest(L, a, b, lab_palette)
        color_indices[i] = best
        ch = lab_palette[best]
        _distribute_atkinson_error(
            error_buf, x, y, width, height,
            L - ch['L'], a - ch['a'], b - ch['b'], 1 / 8,
        )

    return color_indices


def stucki(raw_bytes, lab_palette: list, width: int, height: int) -> bytearray:
    """Stucki error diffusion in CIELAB space (100% error over 12 neighbours)."""
    pixel_count = len(raw_bytes) // 3
    color_indices = bytearray(pixel_count)
    if not lab_palette:
        return color_indices

    error_buf = array('f', [0.0] * (pixel_count * 3))

    for i in range(pixel_count):
        px = i * 3
        y = i // width
        x = i % width

        L = (raw_bytes[px]     / LAB16_L_MAX) * 100   + error_buf[px]
        a = (raw_bytes[px + 1] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 1]
        b = (raw_bytes[px + 2] - LAB16_AB_NEUTRAL) / AB_SCALE + error_buf[px + 2]

        L = max(0.0, min(100.0, L))
        a = max(-128.0, min(127.0, a))
        b = max(-128.0, min(127.0, b))

        best = get_nearest(L, a, b, lab_palette)
        color_indices[i] = best
        ch = lab_palette[best]
        _distribute_stucki_error(
            error_buf, x, y, width, height,
            L - ch['L'], a - ch['a'], b - ch['b'],
        )

    return color_indices


def bayer(raw_bytes, lab_palette: list, width: int, height: int, scale: int = 1) -> bytearray:
    """Bayer 8×8 ordered dithering (LPI-aware).

    scale: macro-cell size in pixels (1 = per-pixel, >1 = clustered for screen mesh).
    LPI-aware scale formula (Rule of 7): maxLPI = meshCount / 7,
                                          scale = round(dpi / maxLPI)
    """
    pixel_count = len(raw_bytes) // 3
    color_indices = bytearray(pixel_count)
    if not lab_palette or len(lab_palette) <= 1:
        return color_indices

    for i in range(pixel_count):
        px = i * 3
        x = i % width
        y = i // width

        L = (raw_bytes[px]     / LAB16_L_MAX) * 100
        a = (raw_bytes[px + 1] - LAB16_AB_NEUTRAL) / AB_SCALE
        b = (raw_bytes[px + 2] - LAB16_AB_NEUTRAL) / AB_SCALE

        two = get_two_nearest(L, a, b, lab_palette)
        i1, i2, d1, d2 = two['i1'], two['i2'], two['d1'], two['d2']

        total = d1 + d2
        ratio = 0.0 if total == 0 else d1 / total

        cell_x = x // scale
        cell_y = y // scale
        threshold = (BAYER_MATRIX[cell_y % 8][cell_x % 8] + 0.5) / 64

        color_indices[i] = i2 if ratio > threshold else i1

    return color_indices


# ---------------------------------------------------------------------------
# Strategy dispatch table
# ---------------------------------------------------------------------------

DITHERING_STRATEGIES: dict = {
    'floyd-steinberg': floyd_steinberg,
    'atkinson':        atkinson,
    'stucki':          stucki,
    'bayer':           bayer,
}

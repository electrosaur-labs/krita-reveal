"""
DNAGenerator v2.0 — image DNA for archetype matching.

Generates a 7D global vector + 12-sector hue breakdown from Lab pixel data.
"""

from __future__ import annotations

import math


# 12 hue sectors at 30° intervals. 'red' wraps around 0°.
SECTORS = [
    {'name': 'red',         'start': 345, 'end': 15},
    {'name': 'orange',      'start': 15,  'end': 45},
    {'name': 'yellow',      'start': 45,  'end': 75},
    {'name': 'chartreuse',  'start': 75,  'end': 105},
    {'name': 'green',       'start': 105, 'end': 135},
    {'name': 'cyan',        'start': 135, 'end': 165},
    {'name': 'azure',       'start': 165, 'end': 195},
    {'name': 'blue',        'start': 195, 'end': 225},
    {'name': 'purple',      'start': 225, 'end': 255},
    {'name': 'magenta',     'start': 255, 'end': 285},
    {'name': 'pink',        'start': 285, 'end': 315},
    {'name': 'rose',        'start': 315, 'end': 345},
]


class DNAGenerator:
    """Generates comprehensive image DNA including 12-sector hue analysis."""

    def __init__(self):
        self.sectors = SECTORS

    def generate(self, lab_pixels, width: int, height: int, options: dict | None = None) -> dict:
        """Generate DNA v2.0 from Lab pixel data.

        lab_pixels: flat sequence of (L, a, b, L, a, b, ...) values.
        Bit depth is set via options['bit_depth'] (8, 16, or 'perceptual').
        """
        if options is None:
            options = {}
        bit_depth = options.get('bit_depth', 8)
        total_pixels = width * height

        # Initialize sector accumulators
        sector_data = {
            s['name']: {'pixels': [], 'weight': 0.0, 'l_mean': 0.0, 'c_mean': 0.0, 'c_max': 0.0}
            for s in self.sectors
        }

        l_sum = l_sq_sum = c_sum = c_max = 0.0
        k_max = 0.0
        k_min = 100.0
        warm_pixels = cool_pixels = 0

        for i in range(0, len(lab_pixels), 3):
            L = self._normalize_lab(lab_pixels[i],     'L', bit_depth)
            a = self._normalize_lab(lab_pixels[i + 1], 'a', bit_depth)
            b = self._normalize_lab(lab_pixels[i + 2], 'b', bit_depth)

            C = math.sqrt(a * a + b * b)
            h = self._lab_to_hue(a, b)

            l_sum    += L
            l_sq_sum += L * L
            c_sum    += C
            if C > c_max:
                c_max = C
            if L > k_max:
                k_max = L
            if L < k_min:
                k_min = L

            if abs(b) > 5:
                if b > 0:
                    warm_pixels += 1
                else:
                    cool_pixels += 1

            if C > 5:
                sector = self._get_sector_for_hue(h)
                if sector:
                    sd = sector_data[sector['name']]
                    sd['pixels'].append((L, C))
                    if C > sd['c_max']:
                        sd['c_max'] = C

        # Global metrics
        l_mean   = l_sum / total_pixels
        l_var    = (l_sq_sum / total_pixels) - (l_mean * l_mean)
        l_std_dev = math.sqrt(max(0.0, l_var))
        c_mean   = c_sum / total_pixels
        k        = k_max - k_min

        # Sector statistics
        dominant_sector = None
        max_weight = 0.0

        for s in self.sectors:
            sd = sector_data[s['name']]
            pixels = sd['pixels']
            count  = len(pixels)
            sd['weight'] = count / total_pixels

            if count > 0:
                sd['l_mean'] = sum(p[0] for p in pixels) / count
                sd['c_mean'] = sum(p[1] for p in pixels) / count

            if sd['weight'] > max_weight:
                max_weight = sd['weight']
                dominant_sector = s['name']

            del sd['pixels']

        # Shannon hue entropy
        hue_entropy = self._calculate_entropy(
            [sector_data[s['name']]['weight'] for s in self.sectors]
        )

        # Temperature bias: -1=cool, +1=warm
        total_temp = warm_pixels + cool_pixels
        temperature_bias = (warm_pixels - cool_pixels) / total_temp if total_temp > 0 else 0.0

        return {
            'version': '2.0',
            'global': {
                'l':                    round(l_mean, 1),
                'c':                    round(c_mean, 1),
                'k':                    round(k, 1),
                'l_std_dev':            round(l_std_dev, 1),
                'hue_entropy':          round(hue_entropy, 3),
                'temperature_bias':     round(temperature_bias, 2),
                'primary_sector_weight': round(max_weight, 3),
            },
            'dominant_sector': dominant_sector,
            'sectors': sector_data,
            'metadata': {
                'width':       width,
                'height':      height,
                'total_pixels': total_pixels,
                'bit_depth':   bit_depth,
            },
        }

    @classmethod
    def from_pixels(cls, lab_pixels, width: int, height: int, options: dict | None = None) -> dict:
        """Generate DNA v2.0 from a raw Lab pixel buffer.

        Bit depth is auto-detected from the array type if not specified:
          array('H') / uint16 → engine 16-bit
          bytes / bytearray   → 8-bit
        """
        from array import array as _array
        if options is None:
            options = {}
        if 'bit_depth' not in options:
            options = dict(options)
            if isinstance(lab_pixels, _array) and lab_pixels.typecode == 'H':
                options['bit_depth'] = 16
            else:
                options['bit_depth'] = 8
        gen = cls()
        return gen.generate(lab_pixels, width, height, options)

    @classmethod
    def from_indices(cls, color_indices, lab_palette: list, width: int, height: int) -> dict:
        """Generate DNA from posterization output (palette + per-pixel color indices).

        lab_palette: list of (L, a, b) tuples in perceptual space.
        """
        pixel_count = width * height
        lab_pixels = [0.0] * (pixel_count * 3)
        for i in range(pixel_count):
            ci = color_indices[i]
            L, a, b = lab_palette[ci]
            off = i * 3
            lab_pixels[off]     = L
            lab_pixels[off + 1] = a
            lab_pixels[off + 2] = b
        gen = cls()
        return gen.generate(lab_pixels, width, height, {'bit_depth': 'perceptual'})

    # -------------------------------------------------------------------------

    def _normalize_lab(self, value: float, component: str, bit_depth) -> float:
        if bit_depth == 'perceptual':
            return value
        if bit_depth == 16:
            if component == 'L':
                return (value / 32768) * 100
            return (value - 16384) / 128
        # 8-bit
        if component == 'L':
            return (value / 255) * 100
        return value - 128

    def _lab_to_hue(self, a: float, b: float) -> float:
        h = math.atan2(b, a) * (180 / math.pi)
        if h < 0:
            h += 360
        return h

    def _get_sector_for_hue(self, hue: float) -> dict | None:
        for sector in self.sectors:
            if sector['start'] > sector['end']:
                # Wraps around 0° (red: 345-15)
                if hue >= sector['start'] or hue < sector['end']:
                    return sector
            else:
                if sector['start'] <= hue < sector['end']:
                    return sector
        return None

    def _calculate_entropy(self, weights: list) -> float:
        """Shannon entropy of sector weights, normalized to 0-1."""
        max_entropy = math.log2(len(self.sectors))
        entropy = 0.0
        for w in weights:
            if w > 0:
                entropy -= w * math.log2(w)
        return entropy / max_entropy

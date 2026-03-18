"""
RevelationError — Chroma-weighted CIE76 fidelity metric (E_rev).

Measures colour accuracy between original and posterized images,
weighting chromatic regions 11× more than achromatic (printing
economics: ink colour errors are far more visible than grey errors).

Two calling conventions:
  from_buffers()   — 8-bit Lab buffers (batch + adobe backward compat)
  from_indices()   — 16-bit Lab + palette indices (Navigator native path)

Algorithm (two-pass):
  Pass 1: Find cMax (peak chroma across sampled original pixels)
  Pass 2: Weighted mean ΔE where w = 1 + 10 * (C_i / cMax)
"""

from __future__ import annotations

import math

from ..color.encoding import LAB16_L_MAX, LAB16_AB_NEUTRAL


_L_SCALE_16 = 100 / 32768     # 16-bit PS L  → perceptual (0-100)
_AB_SCALE_16 = 128 / 16384    # 16-bit PS a/b → perceptual (−128..+127)


class RevelationError:

    @staticmethod
    def from_buffers(
        original_lab,
        posterized_lab,
        width: int,
        height: int,
        options: dict | None = None,
    ) -> dict:
        """Compute E_rev from 8-bit Lab buffers.

        original_lab / posterized_lab: flat sequences of 8-bit Lab values
            (3 bytes/pixel, L:0-255, a/b:0-255 centre=128).
        Returns {'eRev': float, 'chromaStats': {'cMax', 'avgChroma', 'chromaPixelRatio'}}.
        """
        stride = (options or {}).get('stride', 1)

        # Pass 1: find cMax
        c_max = 0.0
        for y in range(0, height, stride):
            for x in range(0, width, stride):
                idx = (y * width + x) * 3
                a = original_lab[idx + 1] - 128
                b = original_lab[idx + 2] - 128
                c = math.sqrt(a * a + b * b)
                if c > c_max:
                    c_max = c
        if c_max < 1:
            c_max = 1.0

        # Pass 2: weighted error
        sum_we = sum_w = sum_chroma = 0.0
        chroma_count = sample_count = 0

        for y in range(0, height, stride):
            for x in range(0, width, stride):
                idx = (y * width + x) * 3

                L1 = (original_lab[idx] / 255) * 100
                a1 = original_lab[idx + 1] - 128
                b1 = original_lab[idx + 2] - 128

                L2 = (posterized_lab[idx] / 255) * 100
                a2 = posterized_lab[idx + 1] - 128
                b2 = posterized_lab[idx + 2] - 128

                C_i = math.sqrt(a1 * a1 + b1 * b1)
                dL = L1 - L2
                da = a1 - a2
                db = b1 - b2
                dE = math.sqrt(dL * dL + da * da + db * db)
                w = 1 + 10 * (C_i / c_max)

                sum_we += w * dE
                sum_w += w
                sum_chroma += C_i
                sample_count += 1
                if C_i > 5:
                    chroma_count += 1

        e_rev = sum_we / sum_w if sum_w > 0 else 0.0

        return {
            'eRev': round(e_rev, 3),
            'chromaStats': {
                'cMax': round(c_max, 1),
                'avgChroma': round(sum_chroma / max(sample_count, 1), 1),
                'chromaPixelRatio': round(chroma_count / max(sample_count, 1), 3),
            },
        }

    @staticmethod
    def from_indices(
        lab_pixels,
        color_indices,
        lab_palette: list,
        pixel_count: int,
        options: dict | None = None,
    ) -> dict:
        """Compute E_rev from 16-bit Lab pixels + palette indices.

        lab_pixels:    flat sequence of 16-bit Lab engine values (L,a,b per pixel,
                       PS encoding: L 0-32768, a/b 0-32768 centre=16384).
        color_indices: per-pixel palette index sequence.
        lab_palette:   list of {L, a, b} dicts in perceptual Lab.
        pixel_count:   number of pixels.
        Returns {'eRev': float, 'chromaStats': {'cMax', 'avgChroma', 'chromaPixelRatio'}}.
        """
        stride = (options or {}).get('stride', 1)
        palette_size = len(lab_palette)

        # Pass 1: find cMax (perceptual a/b units)
        c_max = 0.0
        for p in range(0, pixel_count, stride):
            off = p * 3
            a = (lab_pixels[off + 1] - LAB16_AB_NEUTRAL) * _AB_SCALE_16
            b = (lab_pixels[off + 2] - LAB16_AB_NEUTRAL) * _AB_SCALE_16
            c = math.sqrt(a * a + b * b)
            if c > c_max:
                c_max = c
        if c_max < 1:
            c_max = 1.0

        # Pass 2: weighted error
        sum_we = sum_w = sum_chroma = 0.0
        chroma_count = sample_count = 0

        for p in range(0, pixel_count, stride):
            off = p * 3
            ci = color_indices[p]
            if ci >= palette_size:
                continue

            L1 = lab_pixels[off] * _L_SCALE_16
            a1 = (lab_pixels[off + 1] - LAB16_AB_NEUTRAL) * _AB_SCALE_16
            b1 = (lab_pixels[off + 2] - LAB16_AB_NEUTRAL) * _AB_SCALE_16

            pal = lab_palette[ci]
            L2 = pal['L']
            a2 = pal['a']
            b2 = pal['b']

            C_i = math.sqrt(a1 * a1 + b1 * b1)
            dL = L1 - L2
            da = a1 - a2
            db = b1 - b2
            dE = math.sqrt(dL * dL + da * da + db * db)
            w = 1 + 10 * (C_i / c_max)

            sum_we += w * dE
            sum_w += w
            sum_chroma += C_i
            sample_count += 1
            if C_i > 5:
                chroma_count += 1

        e_rev = sum_we / sum_w if sum_w > 0 else 0.0

        return {
            'eRev': round(e_rev, 3),
            'chromaStats': {
                'cMax': round(c_max, 1),
                'avgChroma': round(sum_chroma / max(sample_count, 1), 1),
                'chromaPixelRatio': round(chroma_count / max(sample_count, 1), 3),
            },
        }

    @staticmethod
    def mean_delta_e16(
        lab_pixels,
        color_indices,
        lab_palette: list,
        pixel_count: int,
    ) -> float:
        """Compute unweighted mean CIE76 ΔE from 16-bit Lab pixels + palette indices.

        No chroma weighting — simple arithmetic mean.
        Used by ProxyEngine (archetype quality ranking) and session accuracy monitor.

        Returns mean ΔE (0 = perfect, higher = more deviation).
        """
        palette_size = len(lab_palette)

        # Pre-extract palette into flat lists for hot loop
        pal_L = [lab_palette[i]['L'] for i in range(palette_size)]
        pal_a = [lab_palette[i]['a'] for i in range(palette_size)]
        pal_b = [lab_palette[i]['b'] for i in range(palette_size)]

        sum_de = 0.0
        for i in range(pixel_count):
            off = i * 3
            L = lab_pixels[off] * _L_SCALE_16
            a = (lab_pixels[off + 1] - LAB16_AB_NEUTRAL) * _AB_SCALE_16
            b = (lab_pixels[off + 2] - LAB16_AB_NEUTRAL) * _AB_SCALE_16

            ci = color_indices[i]
            if ci >= palette_size:
                continue

            dL = L - pal_L[ci]
            da = a - pal_a[ci]
            db = b - pal_b[ci]
            sum_de += math.sqrt(dL * dL + da * da + db * db)

        return sum_de / pixel_count if pixel_count > 0 else 0.0

    @staticmethod
    def edge_survival16(
        lab_pixels,
        color_indices,
        width: int,
        height: int,
        options: dict | None = None,
    ) -> dict:
        """Compute edge survival ratio from 16-bit Lab pixels + palette indices.

        Measures structural fidelity: fraction of significant colour boundaries
        in the original that are preserved in the posterized version.

        A pair of adjacent pixels is a "significant edge" if original Lab ΔE ≥ threshold.
        An edge "survives" if the two pixels have different palette assignments.

        Returns {'edgeSurvival': float, 'significantEdges': int, 'survivedEdges': int}.
        """
        threshold = (options or {}).get('edgeThreshold', 15)
        threshold_sq = threshold * threshold

        significant = 0
        survived = 0

        # Horizontal edges
        for y in range(height):
            row_off = y * width
            for x in range(width - 1):
                p1 = row_off + x
                p2 = p1 + 1
                off1 = p1 * 3
                off2 = p2 * 3

                dL = (lab_pixels[off1] - lab_pixels[off2]) * _L_SCALE_16
                da = (lab_pixels[off1 + 1] - lab_pixels[off2 + 1]) * _AB_SCALE_16
                db = (lab_pixels[off1 + 2] - lab_pixels[off2 + 2]) * _AB_SCALE_16
                de_sq = dL * dL + da * da + db * db

                if de_sq >= threshold_sq:
                    significant += 1
                    if color_indices[p1] != color_indices[p2]:
                        survived += 1

        # Vertical edges
        for y in range(height - 1):
            row1_off = y * width
            row2_off = (y + 1) * width
            for x in range(width):
                p1 = row1_off + x
                p2 = row2_off + x
                off1 = p1 * 3
                off2 = p2 * 3

                dL = (lab_pixels[off1] - lab_pixels[off2]) * _L_SCALE_16
                da = (lab_pixels[off1 + 1] - lab_pixels[off2 + 1]) * _AB_SCALE_16
                db = (lab_pixels[off1 + 2] - lab_pixels[off2 + 2]) * _AB_SCALE_16
                de_sq = dL * dL + da * da + db * db

                if de_sq >= threshold_sq:
                    significant += 1
                    if color_indices[p1] != color_indices[p2]:
                        survived += 1

        edge_survival = survived / significant if significant > 0 else 1.0

        return {
            'edgeSurvival': round(edge_survival, 4),
            'significantEdges': significant,
            'survivedEdges': survived,
        }

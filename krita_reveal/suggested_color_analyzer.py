"""
suggested_color_analyzer.py — Suggest colors present in the image but absent from the palette.

Port of the JS SuggestedColorAnalyzer.  Runs K-Means on a sample of image
pixels, then filters clusters that are already represented by the current
palette.  Survivors are scored and returned as suggestions.

Input pixels are in pyreveal 16-bit engine encoding:
  flat list [L, a, b, L, a, b, ...] with L in 0-32768, a/b in 0-32768
  where 16384 is neutral for a and b.
"""

from __future__ import annotations

import math

# ── Constants ────────────────────────────────────────────────────────────────

MAX_SUGGESTIONS = 6
PALETTE_EXCLUSION_DE = 10
DEDUP_THRESHOLD = 12
K_CLUSTERS = 16
K_ITERATIONS = 10
SAMPLE_COUNT = 2000
MIN_CHROMA = 15
CAPPED_DIST = 20
SUBSTRATE_L_HIGH = 92
SUBSTRATE_L_LOW = 8
SUBSTRATE_THRESHOLD = 0.30

# 16-bit encoding constants (same as pyreveal engine)
L_SCALE = 327.68        # L: 0-32768 -> 0-100
AB_NEUTRAL = 16384      # a/b neutral in 16-bit
AB_SCALE = 128          # a/b: (value-16384)/128 -> perceptual


class SuggestedColorAnalyzer:
    """Compute suggested colors for an image that are absent from its palette."""

    @classmethod
    def analyze(cls, pixels, width, height, palette_lab, substrate_mode='auto'):
        """
        Analyze image pixels and return suggested colors.

        Parameters
        ----------
        pixels : list[int]
            Flat 16-bit Lab pixel data [L,a,b,L,a,b,...].
        width, height : int
            Image dimensions.
        palette_lab : list[dict]
            Current palette as [{L, a, b}, ...] in perceptual Lab (0-100 / -128..+127).
        substrate_mode : str
            One of 'auto', 'white', 'black', 'none'.

        Returns
        -------
        list[dict]
            [{L, a, b, score, reason}, ...] sorted descending by score.
        """
        total_pixels = width * height
        if total_pixels == 0 or len(pixels) < 3:
            return []

        # Convert all pixels to perceptual Lab
        lab_pixels = cls._decode_pixels(pixels, total_pixels)

        # Detect substrate
        substrate = cls._detect_substrate(lab_pixels, total_pixels, substrate_mode)

        # K-Means clustering on a sample
        clusters = cls._kMeansLab(lab_pixels, total_pixels)

        # Filter and score
        suggestions = cls._filter_and_score(
            clusters, palette_lab, substrate, total_pixels,
        )

        return suggestions

    @classmethod
    def _decode_pixels(cls, pixels, total_pixels):
        """Decode 16-bit encoded pixels to perceptual Lab tuples."""
        lab_pixels = []
        for i in range(total_pixels):
            base = i * 3
            if base + 2 >= len(pixels):
                break
            L = pixels[base] / L_SCALE
            a = (pixels[base + 1] - AB_NEUTRAL) / AB_SCALE
            b = (pixels[base + 2] - AB_NEUTRAL) / AB_SCALE
            lab_pixels.append((L, a, b))
        return lab_pixels

    @classmethod
    def _detect_substrate(cls, lab_pixels, total_pixels, substrate_mode):
        """Determine substrate type (white/black/none)."""
        if substrate_mode == 'none':
            return 'none'
        if substrate_mode == 'white':
            return 'white'
        if substrate_mode == 'black':
            return 'black'

        # Auto-detect: count pixels near white and near black
        white_count = 0
        black_count = 0
        for (L, a, b) in lab_pixels:
            if L > SUBSTRATE_L_HIGH:
                white_count += 1
            elif L < SUBSTRATE_L_LOW:
                black_count += 1

        white_ratio = white_count / total_pixels if total_pixels > 0 else 0
        black_ratio = black_count / total_pixels if total_pixels > 0 else 0

        if white_ratio >= SUBSTRATE_THRESHOLD:
            return 'white'
        if black_ratio >= SUBSTRATE_THRESHOLD:
            return 'black'
        return 'none'

    @classmethod
    def _kMeansLab(cls, lab_pixels, total_pixels):
        """
        Run K-Means on a random sample of pixels.

        Uses a seeded LCG PRNG for reproducible sampling.
        Returns list of {L, a, b, count} cluster centroids.
        """
        # Sample pixels using LCG PRNG
        sample = []
        seed = 42
        n = min(SAMPLE_COUNT, total_pixels)
        for _ in range(n):
            seed = (seed * 1664525 + 1013904223) & 0x7FFFFFFF
            rand = seed / 0x7FFFFFFF
            idx = int(rand * total_pixels)
            if idx >= total_pixels:
                idx = total_pixels - 1
            sample.append(lab_pixels[idx])

        if len(sample) == 0:
            return []

        # Initialize centroids: pick K evenly spaced from sample
        k = min(K_CLUSTERS, len(sample))
        centroids = []
        step = len(sample) / k
        for i in range(k):
            centroids.append(list(sample[int(i * step)]))

        # Iterate
        for _ in range(K_ITERATIONS):
            # Assign each sample point to nearest centroid
            assignments = [0] * len(sample)
            for si, (sL, sa, sb) in enumerate(sample):
                best_d = float('inf')
                best_c = 0
                for ci in range(k):
                    cL, ca, cb = centroids[ci]
                    dL = sL - cL
                    da = sa - ca
                    db = sb - cb
                    d = dL * dL + da * da + db * db
                    if d < best_d:
                        best_d = d
                        best_c = ci
                assignments[si] = best_c

            # Recompute centroids
            sums = [[0.0, 0.0, 0.0] for _ in range(k)]
            counts = [0] * k
            for si, (sL, sa, sb) in enumerate(sample):
                ci = assignments[si]
                sums[ci][0] += sL
                sums[ci][1] += sa
                sums[ci][2] += sb
                counts[ci] += 1

            for ci in range(k):
                if counts[ci] > 0:
                    centroids[ci] = [
                        sums[ci][0] / counts[ci],
                        sums[ci][1] / counts[ci],
                        sums[ci][2] / counts[ci],
                    ]

        # Build result with counts
        clusters = []
        for ci in range(k):
            if counts[ci] > 0:
                clusters.append({
                    'L': centroids[ci][0],
                    'a': centroids[ci][1],
                    'b': centroids[ci][2],
                    'count': counts[ci],
                })
        return clusters

    @classmethod
    def _filter_and_score(cls, clusters, palette_lab, substrate, total_pixels):
        """Filter clusters against palette and substrate, score survivors."""
        suggestions = []

        for cluster in clusters:
            cL = cluster['L']
            ca = cluster['a']
            cb = cluster['b']
            count = cluster['count']

            # Skip low-chroma clusters
            chroma = math.sqrt(ca * ca + cb * cb)
            if chroma < MIN_CHROMA:
                continue

            # Skip substrate-colored clusters
            if substrate == 'white' and cL > SUBSTRATE_L_HIGH:
                continue
            if substrate == 'black' and cL < SUBSTRATE_L_LOW:
                continue

            # Skip clusters too close to any palette color
            too_close = False
            min_palette_dist = float('inf')
            for p in palette_lab:
                dL = cL - p['L']
                da = ca - p['a']
                db = cb - p['b']
                dist = math.sqrt(dL * dL + da * da + db * db)
                if dist < min_palette_dist:
                    min_palette_dist = dist
                if dist < PALETTE_EXCLUSION_DE:
                    too_close = True
                    break
            if too_close:
                continue

            # Score: combination of distance from palette and pixel coverage
            capped_dist = min(min_palette_dist, CAPPED_DIST)
            coverage = count / SAMPLE_COUNT if SAMPLE_COUNT > 0 else 0
            score = capped_dist * (1 + coverage * 5)

            reason = f"dE={min_palette_dist:.0f} cov={coverage * 100:.0f}%"

            suggestions.append({
                'L': cL,
                'a': ca,
                'b': cb,
                'score': score,
                'reason': reason,
            })

        # Dedup: remove suggestions too close to each other (keep higher score)
        suggestions.sort(key=lambda s: s['score'], reverse=True)
        deduped = []
        for s in suggestions:
            is_dup = False
            for d in deduped:
                dL = s['L'] - d['L']
                da = s['a'] - d['a']
                db = s['b'] - d['b']
                dist = math.sqrt(dL * dL + da * da + db * db)
                if dist < DEDUP_THRESHOLD:
                    is_dup = True
                    break
            if not is_dup:
                deduped.append(s)

        return deduped[:MAX_SUGGESTIONS]

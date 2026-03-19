"""
PeakFinder — identity peak detection for Reveal Mk 1.5.

Port of PeakFinder.js.

Identifies high-chroma, low-volume clusters that represent important
details (e.g. Monroe's blue eyes) that would otherwise be lost in
probabilistic median cut algorithms.

ALGORITHM:
  1. Perceptual bucketing — group similar pixels in Lab space (5-unit grid)
  2a. Separate candidates (low volume) from dominants (high volume)
  2b. Perceptual isolation — filter peaks too close to dominants
  2c. Sector sanitization — remove blacklisted sectors (green noise traps)
  2d. Sector preference — enforce allowed sectors if specified
  3. Sector-weighted saliency — 2× boost for blue spectrum (sectors 8-9)
  4. Sort by boosted score; return top N

CRITERIA (all must pass):
  - Chroma > 30 (saturated rarity)
  - ΔE > adaptive threshold from nearest dominant (8.0 for 16-bit, 15.0 for 8-bit)
  - Not in a blacklisted sector
  - In a preferred sector (if specified)
"""

from __future__ import annotations

import math


class PeakFinder:
    def __init__(self, options: dict | None = None):
        if options is None:
            options = {}
        self.chroma_threshold    = options.get('chromaThreshold', 30)
        self.volume_threshold    = options.get('volumeThreshold', 0.05)
        self.min_delta_e         = options.get('minDeltaE', 15)
        self.grid_size           = options.get('gridSize', 5)
        self.max_peaks           = options.get('maxPeaks', 1)
        self.blacklisted_sectors = options.get('blacklistedSectors', [3, 4])
        self.preferred_sectors   = options.get('preferredSectors', None)

    def find_identity_peaks(self, lab_pixels: list, options: dict | None = None) -> list:
        """Find identity peaks in perceptual Lab pixel data.

        lab_pixels: flat [L, a, b, L, a, b, ...] list in perceptual space
        options:    optional per-call overrides
        Returns list of {L, a, b, chroma, volume, sector, score} dicts.
        """
        if options is None:
            options = {}

        chroma_threshold = options.get('chromaThreshold', self.chroma_threshold)
        volume_threshold = options.get('volumeThreshold', self.volume_threshold)
        max_peaks        = options.get('maxPeaks', self.max_peaks)
        bit_depth        = options.get('bitDepth', 16)

        # 16-bit data is signal → tighter isolation threshold (8.0)
        # 8-bit data has quantization noise → standard threshold (15.0)
        adaptive_min_de = 8.0 if bit_depth == 16 else self.min_delta_e

        buckets = {}
        total_pixels = len(lab_pixels) // 3

        # Stage 1: Perceptual bucketing
        for i in range(0, len(lab_pixels), 3):
            L = lab_pixels[i]
            a = lab_pixels[i + 1]
            b = lab_pixels[i + 2]
            chroma = math.sqrt(a * a + b * b)

            if chroma > chroma_threshold:
                key = self._bucket_key(L, a, b)
                if key not in buckets:
                    buckets[key] = {'L': 0.0, 'a': 0.0, 'b': 0.0, 'count': 0, 'max_c': 0.0}
                bkt = buckets[key]
                bkt['L'] += L
                bkt['a'] += a
                bkt['b'] += b
                bkt['count'] += 1
                if chroma > bkt['max_c']:
                    bkt['max_c'] = chroma

        # Stage 2a: Separate candidates from dominants
        candidates = []
        dominant_colors = []

        for data in buckets.values():
            n = data['count']
            volume = n / total_pixels if total_pixels > 0 else 0.0
            centroid = {
                'L': data['L'] / n,
                'a': data['a'] / n,
                'b': data['b'] / n,
                'chroma': data['max_c'],
                'volume': volume,
            }
            if volume < volume_threshold:
                centroid['sector'] = self._hue_sector(centroid['a'], centroid['b'])
                candidates.append(centroid)
            else:
                dominant_colors.append(centroid)

        # Stage 2b: Perceptual isolation
        isolated = self._filter_isolation(candidates, dominant_colors, adaptive_min_de)

        # Stage 2c: Remove blacklisted sectors
        sanitized = self._filter_blacklist(isolated)

        # Stage 2d: Preferred sectors
        final = self._filter_preference(sanitized)

        # Stage 3: Sector-weighted saliency + sort
        scored = []
        for peak in final:
            is_blue = peak['sector'] in (8, 9)
            score = peak['chroma'] * (2.0 if is_blue else 1.0)
            scored.append({**peak, 'score': score})

        scored.sort(key=lambda p: -p['score'])
        return scored[:max_peaks]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _bucket_key(self, L: float, a: float, b: float) -> tuple:
        g = self.grid_size
        return (int(L / g), int(a / g), int(b / g))

    def _hue_sector(self, a: float, b: float) -> int:
        hue = math.atan2(b, a) * 180.0 / math.pi
        return int(((hue + 360.0) % 360.0) / 30.0) % 12

    def _filter_isolation(self, candidates, dominants, min_de):
        if not dominants:
            return candidates
        result = []
        for peak in candidates:
            min_dist = min(
                math.sqrt(
                    (peak['L'] - d['L']) ** 2 +
                    (peak['a'] - d['a']) ** 2 +
                    (peak['b'] - d['b']) ** 2
                )
                for d in dominants
            )
            if min_dist > min_de:
                result.append(peak)
        return result

    def _filter_blacklist(self, candidates):
        if not self.blacklisted_sectors:
            return candidates
        return [p for p in candidates if p['sector'] not in self.blacklisted_sectors]

    def _filter_preference(self, candidates):
        if not self.preferred_sectors:
            return candidates
        return [p for p in candidates if p['sector'] in self.preferred_sectors]

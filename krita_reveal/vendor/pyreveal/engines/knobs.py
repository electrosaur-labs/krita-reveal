"""
MechanicalKnobs — shared post-separation mask processing.

Three screen-printing knobs:
  apply_min_volume:    Ghost plate removal — merge weak colours into nearest strong.
  apply_speckle_rescue: Halftone solidity — despeckle + BFS heal.
  apply_shadow_clamp:  Ink body / edge erosion — tonal-aware.

Also exports:
  heal_orphaned_pixels — BFS fill of zero-masked pixels from neighbours.
  rebuild_masks        — Reconstruct masks from colour index array.

All functions mutate masks/indices in place and are pure (no I/O).
"""

from __future__ import annotations

import math

from .separation import SeparationEngine

_HUE_SECTORS = 12


class MechanicalKnobs:

    # -------------------------------------------------------------------------
    # minVolume
    # -------------------------------------------------------------------------

    @staticmethod
    def apply_min_volume(
        color_indices,
        palette: list,
        pixel_count: int,
        min_volume_percent: float,
        options: dict | None = None,
    ) -> dict:
        """Remap weak-colour pixels to nearest strong neighbour.

        Colours with coverage below min_volume_percent are merged into their
        nearest CIE76 neighbour. Palette array stays the same length (indices
        remain stable for palette overrides).

        Sector-aware rescue: if pruning a weak colour would eliminate the last
        chromatic representative of its 30° hue sector, it is promoted to strong.

        options:
          max_colors: int — hard screen cap (0 = no cap). Lowest-coverage strong
                            colours are demoted to weak until count ≤ max_colors.

        Returns {'remapped_count': int}.
        """
        if options is None:
            options = {}

        max_colors = options.get('max_colors', 0)
        if min_volume_percent <= 0 and max_colors <= 0:
            return {'remapped_count': 0}

        min_pixels = round(pixel_count * min_volume_percent / 100)

        # Count pixels per colour
        color_counts = [0] * len(palette)
        for ci in color_indices:
            color_counts[ci] += 1

        # Classify each colour into a 30° hue sector (-1 for achromatic C < 5)
        color_sectors = []
        for p in palette:
            C = math.sqrt(p['a'] * p['a'] + p['b'] * p['b'])
            if C < 5:
                color_sectors.append(-1)
            else:
                hue = (math.atan2(p['b'], p['a']) * 180 / math.pi + 360) % 360
                color_sectors.append(int(hue / 30) % _HUE_SECTORS)

        # _minVolumeExempt threshold: 0.1% of image or 50 px, whichever is larger
        exempt_min = max(50, round(pixel_count * 0.001))

        weak_indices   = []
        strong_indices = []
        for i in range(len(palette)):
            if color_counts[i] == 0:
                continue
            if color_counts[i] >= min_pixels:
                strong_indices.append(i)
            elif palette[i].get('_user_added'):
                strong_indices.append(i)  # user-added: unconditionally strong
            elif palette[i].get('_min_volume_exempt') and color_counts[i] >= exempt_min:
                strong_indices.append(i)
            else:
                weak_indices.append(i)

        # Sector-aware rescue: promote last representative of each hue sector
        if weak_indices and strong_indices:
            strong_sectors = set(color_sectors[i] for i in strong_indices if color_sectors[i] >= 0)
            for w in range(len(weak_indices) - 1, -1, -1):
                wi = weak_indices[w]
                sec = color_sectors[wi]
                if sec >= 0 and sec not in strong_sectors:
                    strong_indices.append(wi)
                    strong_sectors.add(sec)
                    weak_indices.pop(w)

        # Screen cap: demote lowest-coverage strong colours
        if max_colors > 0 and len(strong_indices) > max_colors:
            ranked = sorted(strong_indices, key=lambda i: color_counts[i])
            for i in range(len(strong_indices) - max_colors):
                demoted = ranked[i]
                weak_indices.append(demoted)
                strong_indices.remove(demoted)

        if not weak_indices or not strong_indices:
            return {'remapped_count': 0}

        # Build remap table: each weak colour → nearest strong (CIE76)
        remap_table = list(range(len(palette)))
        for wi in weak_indices:
            wc = palette[wi]
            best_si  = strong_indices[0]
            best_dsq = float('inf')
            for si in strong_indices:
                sc  = palette[si]
                dL  = wc['L'] - sc['L']
                da  = wc['a'] - sc['a']
                db  = wc['b'] - sc['b']
                dsq = dL * dL + da * da + db * db
                if dsq < best_dsq:
                    best_dsq = dsq
                    best_si  = si
            remap_table[wi] = best_si

        # Remap indices in place
        for i in range(pixel_count):
            color_indices[i] = remap_table[color_indices[i]]

        return {'remapped_count': len(weak_indices)}

    # -------------------------------------------------------------------------
    # speckleRescue
    # -------------------------------------------------------------------------

    @staticmethod
    def apply_speckle_rescue(
        masks: list,
        color_indices,
        width: int,
        height: int,
        threshold_pixels: float,
        original_width: int | None = None,
    ) -> None:
        """Morphological despeckle + BFS healing.

        threshold_pixels: user-facing speckle size (0-10 px).
        original_width: full document width for proxy scaling. Area scales as
                        linearScale², so the threshold is scaled by sqrt(linearScale).
        """
        if threshold_pixels <= 0:
            return

        threshold = round(threshold_pixels)
        if original_width and original_width > width:
            linear_scale = original_width / width
            threshold = round(threshold * math.sqrt(linear_scale))

        for mask in masks:
            SeparationEngine.despeckle_mask(mask, width, height, threshold)

        MechanicalKnobs.heal_orphaned_pixels(masks, color_indices, width, height)

    # -------------------------------------------------------------------------
    # shadowClamp
    # -------------------------------------------------------------------------

    @staticmethod
    def apply_shadow_clamp(
        masks: list,
        color_indices,
        palette: list,
        width: int,
        height: int,
        clamp_percent: float,
    ) -> None:
        """Tonal-aware edge erosion.

        For each mask pixel, compute the fraction of 8-connected neighbours
        that share the same mask. If below a per-ink threshold, zero the pixel.
        Light inks (high L) erode more aggressively than dark inks.

        clamp_percent range: 0-40%.
        """
        if clamp_percent <= 0:
            return

        # Map 0-40% → 0-1.2 base neighbour fraction
        base_threshold = (clamp_percent / 100) * 3

        for c, mask in enumerate(masks):
            ink_L = palette[c]['L'] if c < len(palette) and 'L' in palette[c] else 50.0
            lightness_boost = ink_L / 100
            threshold = base_threshold * (0.5 + lightness_boost)

            to_remove = []
            for y in range(height):
                for x in range(width):
                    i = y * width + x
                    if mask[i] == 0:
                        continue
                    same = 0
                    total = 0
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            nx = x + dx
                            ny = y + dy
                            if 0 <= nx < width and 0 <= ny < height:
                                total += 1
                                if mask[ny * width + nx] > 0:
                                    same += 1
                    if total > 0 and same / total < threshold:
                        to_remove.append(i)

            for idx in to_remove:
                mask[idx] = 0

        MechanicalKnobs.heal_orphaned_pixels(masks, color_indices, width, height)

    # -------------------------------------------------------------------------
    # BFS healing
    # -------------------------------------------------------------------------

    @staticmethod
    def heal_orphaned_pixels(masks: list, color_indices, width: int, height: int) -> None:
        """BFS-fill orphaned pixels from surrounding non-orphan neighbours.

        An orphaned pixel is one whose assigned colour's mask was zeroed by
        despeckle or erosion but colorIndices still points to that colour.
        Floods orphans with the nearest non-orphan neighbour's colour.

        O(pixel_count) — each pixel visited at most twice.
        """
        pixel_count = width * height
        num_colors  = len(masks)

        # Identify orphans
        is_orphan = bytearray(pixel_count)
        orphan_count = 0
        for i in range(pixel_count):
            ci = color_indices[i]
            if ci >= num_colors or masks[ci][i] == 0:
                is_orphan[i] = 1
                orphan_count += 1

        if orphan_count == 0:
            return

        # Seed BFS with non-orphan pixels adjacent to at least one orphan
        queue = [0] * pixel_count
        head = 0
        tail = 0

        for i in range(pixel_count):
            if is_orphan[i]:
                continue
            x = i % width
            y = i  // width
            adjacent = False
            for dy in (-1, 0, 1):
                if adjacent:
                    break
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    ny = y + dy
                    if 0 <= nx < width and 0 <= ny < height and is_orphan[ny * width + nx]:
                        adjacent = True
                        break
            if adjacent:
                queue[tail] = i
                tail += 1

        # BFS: spread non-orphan colours into orphan gaps
        while head < tail:
            i  = queue[head]
            head += 1
            ci = color_indices[i]
            x  = i % width
            y  = i  // width

            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or nx >= width or ny < 0 or ny >= height:
                        continue
                    ni = ny * width + nx
                    if is_orphan[ni]:
                        color_indices[ni] = ci
                        masks[ci][ni] = 255
                        is_orphan[ni] = 0
                        queue[tail] = ni
                        tail += 1

    # -------------------------------------------------------------------------
    # Mask rebuild
    # -------------------------------------------------------------------------

    @staticmethod
    def rebuild_masks(color_indices, palette_size: int, pixel_count: int) -> list:
        """Reconstruct binary masks from colour index array.

        Returns a list of palette_size bytearrays (255 where that colour is assigned).
        """
        masks = [bytearray(pixel_count) for _ in range(palette_size)]
        for i in range(pixel_count):
            ci = color_indices[i]
            if ci < palette_size:
                masks[ci][i] = 255
        return masks

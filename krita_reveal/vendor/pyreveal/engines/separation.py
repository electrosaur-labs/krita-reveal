"""
SeparationEngine — pixel-to-palette mapping, mask generation, and despeckle.

Operates in CIELAB space. Native 16-bit nearest-neighbour mapping avoids
per-pixel float conversion ("normalisation leak").

Public API:
  map_pixels_to_palette(raw_bytes, lab_palette, width, height, options) → bytearray
  generate_layer_mask(color_indices, target_index, width, height) → bytearray
  despeckle_mask(mask, width, height, threshold) → dict
  prune_weak_colors(lab_palette, color_indices, width, height, min_volume, options) → dict
  separate_image(raw_bytes, width, height, hex_colors, lab_palette, options) → list
"""

from __future__ import annotations

import math

from ..color.encoding import (
    LAB16_L_MAX, LAB16_AB_NEUTRAL, AB_SCALE,
    perceptual_to_engine16,
)
from ..color.distance import (
    DistanceMetric,
    cie76_weighted_squared_inline16,
    cie94_squared_inline16,
    cie2000_squared_inline,
    prepare_palette_chroma16,
    normalize_distance_config,
    SNAP_THRESHOLD_SQ_16,
    DEFAULT_CIE94_PARAMS_16,
)
from .dithering import DITHERING_STRATEGIES

# Snap threshold for CIE2000 (perceptual ΔE² ≈ 1.0)
_SNAP_CIE2000 = 1.0
# Shadow L threshold in 16-bit units (L = 40% → 0.4 × 32768 = 13107)
_SHADOW_THRESHOLD_16 = 13107


class SeparationEngine:
    """Pure-function class: pixel mapping, mask generation, palette pruning."""

    # -------------------------------------------------------------------------
    # Pixel-to-palette mapping
    # -------------------------------------------------------------------------

    @staticmethod
    def map_pixels_to_palette(
        raw_bytes,
        lab_palette: list,
        width: int | None = None,
        height: int | None = None,
        options: dict | None = None,
    ) -> bytearray:
        """Map 16-bit Lab pixels to palette indices.

        raw_bytes: indexable sequence of 16-bit engine-encoded Lab values (3 per pixel).
        lab_palette: list of {'L': float, 'a': float, 'b': float} dicts.
        options keys:
          dither_type:     'none'|'floyd-steinberg'|'atkinson'|'stucki'|'bayer'
          distance_metric: 'cie76'|'cie94'|'cie2000'
          mesh_count:      screen mesh TPI — enables LPI-aware Bayer scale
          dpi:             image DPI (default 300)
          cie94_params:    {'kL', 'k1', 'k2'} override
        """
        if options is None:
            options = {}

        dither_type = options.get('dither_type', 'none')
        mesh_count  = options.get('mesh_count', None)
        dpi         = options.get('dpi', 300)
        dist_cfg    = normalize_distance_config(options)

        # LPI-aware Bayer scale (Rule of 7)
        scale = 1
        if mesh_count:
            max_lpi = mesh_count / 7
            scale = max(1, round(dpi / max_lpi))

        if not width or not height or dither_type == 'none':
            return SeparationEngine._map_pixels_nearest_neighbor(raw_bytes, lab_palette, dist_cfg)

        strategy = DITHERING_STRATEGIES.get(dither_type)
        if strategy:
            if dither_type == 'bayer':
                return strategy(raw_bytes, lab_palette, width, height, scale)
            return strategy(raw_bytes, lab_palette, width, height)

        return SeparationEngine._map_pixels_nearest_neighbor(raw_bytes, lab_palette, dist_cfg)

    @staticmethod
    def _map_pixels_nearest_neighbor(
        raw_bytes,
        lab_palette: list,
        dist_cfg: dict | None = None,
    ) -> bytearray:
        """Nearest-neighbour mapping in native 16-bit space.

        Uses numpy-vectorised distance computation for CIE76 and CIE94 when
        numpy is available.  Falls back to a pure-Python spatial-locality loop
        for CIE2000 or when numpy is not present.
        """
        if dist_cfg is None:
            dist_cfg = normalize_distance_config({})

        if not dist_cfg['is_cie2000']:
            try:
                return SeparationEngine._map_pixels_nearest_neighbor_numpy(
                    raw_bytes, lab_palette, dist_cfg
                )
            except Exception:
                pass  # fall through to Python path

        return SeparationEngine._map_pixels_nearest_neighbor_python(
            raw_bytes, lab_palette, dist_cfg
        )

    @staticmethod
    def _map_pixels_nearest_neighbor_numpy(
        raw_bytes,
        lab_palette: list,
        dist_cfg: dict,
    ) -> bytearray:
        """Numpy-vectorised nearest-neighbour mapping (CIE76 and CIE94).

        Processes pixels in chunks of ~400 K to keep peak memory under ~100 MB.
        """
        import numpy as np

        K            = len(lab_palette)
        pixel_count  = len(raw_bytes) // 3
        is_cie94     = dist_cfg['is_cie94']

        # Palette in engine 16-bit as float32 (K, 3)
        pal16 = np.array(
            [list(perceptual_to_engine16(p['L'], p['a'], p['b'])) for p in lab_palette],
            dtype=np.float32,
        )
        pal_L = pal16[:, 0]  # (K,)
        pal_a = pal16[:, 1]  # (K,)
        pal_b = pal16[:, 2]  # (K,)

        # Pre-compute palette chroma for CIE94
        if is_cie94:
            a_off      = pal_a - LAB16_AB_NEUTRAL
            b_off      = pal_b - LAB16_AB_NEUTRAL
            pal_chroma = np.sqrt(a_off * a_off + b_off * b_off)  # (K,)
            k1         = DEFAULT_CIE94_PARAMS_16['k1']
            k2         = DEFAULT_CIE94_PARAMS_16['k2']

        # Convert pixel buffer to float32 (N, 3) — handles array.array, bytearray, list
        pixels = np.asarray(raw_bytes, dtype=np.float32).reshape(pixel_count, 3)

        result = np.empty(pixel_count, dtype=np.uint8)

        CHUNK = 400_000
        for start in range(0, pixel_count, CHUNK):
            end   = min(start + CHUNK, pixel_count)
            chunk = pixels[start:end]   # (C, 3)

            pL = chunk[:, 0:1]  # (C, 1) — broadcasts against (K,) to (C, K)
            pa = chunk[:, 1:2]
            pb = chunk[:, 2:3]

            dL = pL - pal_L  # (C, K)
            da = pa - pal_a  # (C, K)
            db = pb - pal_b  # (C, K)

            if dist_cfg.get('is_grayscale'):
                # Strict 1D luminance distance
                dist_sq = dL * dL
            elif is_cie94:
                a_off_p = pa - LAB16_AB_NEUTRAL         # (C, 1)
                b_off_p = pb - LAB16_AB_NEUTRAL         # (C, 1)
                C_test  = np.sqrt(a_off_p * a_off_p + b_off_p * b_off_p)  # (C, 1)

                dC    = pal_chroma - C_test             # (C, K): C_ref − C_test
                dH_sq = np.maximum(0.0, da * da + db * db - dC * dC)   # (C, K)
                SC    = 1.0 + k1 * pal_chroma          # (K,) → broadcast (C, K)
                SH    = 1.0 + k2 * pal_chroma
                dist_sq = (dL * dL
                           + (dC / SC) * (dC / SC)
                           + dH_sq / (SH * SH))        # (C, K)
            else:
                # CIE76 with shadow-L weighting
                avg_L   = (pL + pal_L) * 0.5           # (C, K)
                l_w     = np.where(avg_L < _SHADOW_THRESHOLD_16, 2.0, 1.0)  # (C, K)
                dL_w    = dL * l_w
                dist_sq = dL_w * dL_w + da * da + db * db  # (C, K)

            result[start:end] = dist_sq.argmin(axis=1)

        return bytearray(result)

    @staticmethod
    def _map_pixels_nearest_neighbor_python(
        raw_bytes,
        lab_palette: list,
        dist_cfg: dict,
    ) -> bytearray:
        """Pure-Python nearest-neighbour mapping with spatial locality + snap threshold.

        Used for CIE2000 (which is hard to vectorise) and as numpy fallback.
        """
        pixel_count  = len(raw_bytes) // 3
        color_indices = bytearray(pixel_count)
        palette_size  = len(lab_palette)

        is_cie94   = dist_cfg['is_cie94']
        is_cie2000 = dist_cfg['is_cie2000']
        snap_thr   = _SNAP_CIE2000 if is_cie2000 else SNAP_THRESHOLD_SQ_16

        # Pre-convert palette to 16-bit integer arrays (eliminates per-pixel conversion)
        pal_L16 = [0] * palette_size
        pal_a16 = [0] * palette_size
        pal_b16 = [0] * palette_size
        # Perceptual copies for CIE2000
        pal_Lp  = [0.0] * palette_size
        pal_ap  = [0.0] * palette_size
        pal_bp  = [0.0] * palette_size

        for j, p in enumerate(lab_palette):
            L16, a16, b16 = perceptual_to_engine16(p['L'], p['a'], p['b'])
            pal_L16[j] = L16
            pal_a16[j] = a16
            pal_b16[j] = b16
            pal_Lp[j]  = p['L']
            pal_ap[j]  = p['a']
            pal_bp[j]  = p['b']

        # Pre-compute 16-bit chroma for CIE94
        pal_chroma16 = None
        k1_16 = DEFAULT_CIE94_PARAMS_16['k1']
        k2_16 = DEFAULT_CIE94_PARAMS_16['k2']
        if is_cie94:
            pal_chroma16 = prepare_palette_chroma16(
                [(pal_L16[j], pal_a16[j], pal_b16[j]) for j in range(palette_size)]
            )

        last_best = 0

        for p in range(pixel_count):
            pidx = p * 3
            pL = raw_bytes[pidx]
            pA = raw_bytes[pidx + 1]
            pB = raw_bytes[pidx + 2]

            # Spatial locality: check previous winner first
            if dist_cfg.get('is_grayscale'):
                min_d = (pL - pal_L16[last_best]) ** 2
                snap_thr = 0  # ensure bit-perfect tonal mapping
            elif is_cie2000:
                Lp = (pL / LAB16_L_MAX) * 100
                ap = (pA - LAB16_AB_NEUTRAL) / AB_SCALE
                bp = (pB - LAB16_AB_NEUTRAL) / AB_SCALE
                min_d = cie2000_squared_inline(Lp, ap, bp, pal_Lp[last_best], pal_ap[last_best], pal_bp[last_best])
            elif is_cie94:
                min_d = cie94_squared_inline16(pL, pA, pB, pal_L16[last_best], pal_a16[last_best], pal_b16[last_best], pal_chroma16[last_best], k1_16, k2_16)
            else:
                min_d = cie76_weighted_squared_inline16(pL, pA, pB, pal_L16[last_best], pal_a16[last_best], pal_b16[last_best], _SHADOW_THRESHOLD_16, 2.0)

            if min_d > snap_thr:
                nearest = last_best
                for c in range(palette_size):
                    if dist_cfg.get('is_grayscale'):
                        d = (pL - pal_L16[c]) ** 2
                    elif is_cie2000:
                        d = cie2000_squared_inline(Lp, ap, bp, pal_Lp[c], pal_ap[c], pal_bp[c])
                    elif is_cie94:
                        d = cie94_squared_inline16(pL, pA, pB, pal_L16[c], pal_a16[c], pal_b16[c], pal_chroma16[c], k1_16, k2_16)
                    else:
                        d = cie76_weighted_squared_inline16(pL, pA, pB, pal_L16[c], pal_a16[c], pal_b16[c], _SHADOW_THRESHOLD_16, 2.0)

                    if d < min_d:
                        min_d = d
                        nearest = c
                        if d <= snap_thr:
                            break
                last_best = nearest

            color_indices[p] = last_best

        return color_indices

    # -------------------------------------------------------------------------
    # Mask generation
    # -------------------------------------------------------------------------

    @staticmethod
    def generate_layer_mask(color_indices, target_index: int, width: int, height: int) -> bytearray:
        """Generate a binary mask (255/0) for one palette colour index."""
        try:
            import numpy as np
            arr = np.asarray(color_indices, dtype=np.uint8)
            return bytearray(np.where(arr == target_index, np.uint8(255), np.uint8(0)))
        except Exception:
            mask = bytearray(width * height)
            for i in range(len(color_indices)):
                if color_indices[i] == target_index:
                    mask[i] = 255
            return mask

    # -------------------------------------------------------------------------
    # Despeckle
    # -------------------------------------------------------------------------

    @staticmethod
    def despeckle_mask(mask: bytearray, width: int, height: int, threshold: int) -> dict:
        """Remove isolated pixel clusters smaller than threshold using 8-connected DFS.

        Modifies mask in place. Returns {'clusters_removed': int, 'pixels_removed': int}.
        """
        pixel_count = width * height
        visited = bytearray(pixel_count)
        stack = [0] * pixel_count
        clusters_removed = 0
        pixels_removed   = 0
        clusters_to_remove = []

        for i in range(pixel_count):
            if mask[i] == 0 or visited[i]:
                continue

            cluster = []
            sp = 0
            stack[sp] = i
            sp += 1
            visited[i] = 1

            while sp > 0:
                sp -= 1
                idx = stack[sp]
                cluster.append(idx)

                x = idx % width
                y = idx  // width

                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx = x + dx
                        ny = y + dy
                        if nx < 0 or nx >= width or ny < 0 or ny >= height:
                            continue
                        ni = ny * width + nx
                        if mask[ni] > 0 and not visited[ni]:
                            stack[sp] = ni
                            sp += 1
                            visited[ni] = 1

            if len(cluster) < threshold:
                clusters_to_remove.append(cluster)
                clusters_removed += 1
                pixels_removed   += len(cluster)

        for cluster in clusters_to_remove:
            for idx in cluster:
                mask[idx] = 0

        return {'clusters_removed': clusters_removed, 'pixels_removed': pixels_removed}

    # Keep old name as alias (used by MechanicalKnobs)
    _despeckle_mask = despeckle_mask

    # -------------------------------------------------------------------------
    # Palette pruning
    # -------------------------------------------------------------------------

    @staticmethod
    def prune_weak_colors(
        lab_palette: list,
        color_indices,
        width: int,
        height: int,
        min_volume: float,
        options: dict | None = None,
    ) -> dict:
        """Merge colours below min_volume threshold into their nearest strong neighbour.

        min_volume: minimum coverage percentage (e.g. 1.5 = 1.5%).
        Returns {'pruned_palette', 'remapped_indices', 'merged_count', 'details'}.
        """
        if options is None:
            options = {}

        pixel_count = width * height
        min_pixels  = math.ceil((min_volume / 100) * pixel_count)
        max_colors  = options.get('max_colors', 0)

        # Count pixels per colour
        color_counts = [0] * len(lab_palette)
        for idx in color_indices:
            color_counts[idx] += 1

        volumes = [c / pixel_count * 100 for c in color_counts]

        # Classify weak vs strong
        weak_indices   = []
        strong_indices = []
        for i in range(len(lab_palette)):
            if color_counts[i] < min_pixels:
                weak_indices.append(i)
            else:
                strong_indices.append(i)

        # Screen cap: demote lowest-coverage strong colours if over maxColors
        if max_colors > 0 and len(strong_indices) > max_colors:
            ranked = sorted(strong_indices, key=lambda i: color_counts[i])
            for i in range(len(strong_indices) - max_colors):
                demoted = ranked[i]
                weak_indices.append(demoted)
                strong_indices.remove(demoted)

        if not weak_indices:
            return {
                'pruned_palette': lab_palette,
                'remapped_indices': color_indices,
                'merged_count': 0,
                'details': [],
            }
        if not strong_indices:
            return {
                'pruned_palette': lab_palette,
                'remapped_indices': color_indices,
                'merged_count': 0,
                'details': [],
            }

        # Safety: never prune below 4 colours
        MIN_COLORS = 4
        if len(strong_indices) < MIN_COLORS:
            needed  = MIN_COLORS - len(strong_indices)
            by_vol  = sorted(weak_indices, key=lambda i: color_counts[i], reverse=True)
            for i in range(min(needed, len(by_vol))):
                promoted = by_vol[i]
                strong_indices.append(promoted)
                weak_indices.remove(promoted)

        # Build remap table: weak → nearest strong (CIE76)
        remap_table  = list(range(len(lab_palette)))
        merge_details = []

        for wi in weak_indices:
            wc = lab_palette[wi]
            best_si  = strong_indices[0]
            best_dsq = float('inf')
            for si in strong_indices:
                sc  = lab_palette[si]
                dL  = wc['L'] - sc['L']
                da  = wc['a'] - sc['a']
                db  = wc['b'] - sc['b']
                dsq = dL * dL + da * da + db * db
                if dsq < best_dsq:
                    best_dsq = dsq
                    best_si  = si
            remap_table[wi] = best_si
            merge_details.append({
                'weak_index':   wi,
                'strong_index': best_si,
                'weak_color':   wc,
                'strong_color': lab_palette[best_si],
                'volume':       volumes[wi],
                'pixel_count':  color_counts[wi],
                'delta_e':      math.sqrt(best_dsq),
            })

        # Remap indices (old → old strong index)
        remapped = bytearray(len(color_indices))
        for i, old in enumerate(color_indices):
            remapped[i] = remap_table[old]

        # Build compact palette and mapping
        pruned_palette = []
        compact = {}
        for i, si in enumerate(strong_indices):
            pruned_palette.append(lab_palette[si])
            compact[si] = i

        for i in range(len(remapped)):
            remapped[i] = compact[remapped[i]]

        return {
            'pruned_palette':   pruned_palette,
            'remapped_indices': remapped,
            'merged_count':     len(weak_indices),
            'details':          merge_details,
        }

    # -------------------------------------------------------------------------
    # Full separation pipeline
    # -------------------------------------------------------------------------

    @staticmethod
    def separate_image(
        raw_bytes,
        width: int,
        height: int,
        hex_colors: list,
        lab_palette: list,
        options: dict | None = None,
    ) -> list:
        """Run the full separation pipeline.

        Returns list of layer dicts:
          {'name', 'lab_color', 'hex', 'mask', 'width', 'height'}
        Empty layers (<0.1% coverage) are omitted.
        """
        if options is None:
            options = {}
        if not lab_palette:
            raise ValueError('separate_image requires a non-empty lab_palette')

        color_indices = SeparationEngine.map_pixels_to_palette(
            raw_bytes, lab_palette, width, height, options
        )

        layers = []
        for index, lab_color in enumerate(lab_palette):
            hex_color = hex_colors[index] if index < len(hex_colors) else '#000000'
            mask = SeparationEngine.generate_layer_mask(color_indices, index, width, height)

            # Optional shadow clamp (kept for API compatibility — MechanicalKnobs is preferred)
            shadow_clamp = options.get('shadow_clamp', 0)
            if shadow_clamp > 0:
                clamp_thr = round(shadow_clamp * 255 / 100)
                for i in range(len(mask)):
                    if 0 < mask[i] < clamp_thr:
                        mask[i] = clamp_thr

            # Optional speckle rescue
            speckle_rescue = options.get('speckle_rescue', 0)
            if speckle_rescue > 0:
                SeparationEngine.despeckle_mask(mask, width, height, round(speckle_rescue))

            opaque = sum(1 for v in mask if v == 255)
            coverage = opaque / (width * height) * 100
            if opaque == 0 or coverage < 0.1:
                continue

            layers.append({
                'name':      f'Feature {index + 1} ({hex_color})',
                'lab_color': lab_color,
                'hex':       hex_color,
                'mask':      mask,
                'width':     width,
                'height':    height,
            })

        return layers

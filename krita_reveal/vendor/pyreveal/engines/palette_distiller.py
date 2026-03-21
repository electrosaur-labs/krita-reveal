"""
PaletteDistiller — Over-quantize then reduce via furthest-point sampling.

Direct port of reveal-core/lib/engines/PaletteDistiller.js.
"""

from __future__ import annotations

import math

OVER_FACTOR  = 3      # multiplier for over-quantize count
OVER_MAX     = 20     # hard cap on over-quantize count
MIN_COVERAGE = 0.001  # 0.1% — exclude ghost colors from selection


def over_quantize_count(target_k: int) -> int:
    """Return the over-quantize target for a desired final color count."""
    return min(max(target_k, 1) * OVER_FACTOR, OVER_MAX)


def _update_min_dist(min_dist_sq: list[float], palette: list[dict], new_idx: int) -> None:
    """Update min_dist_sq after adding new_idx to the selected set."""
    nc = palette[new_idx]
    nL, na, nb = nc['L'], nc['a'], nc['b']
    for i, c in enumerate(palette):
        dL = c['L'] - nL
        da = c['a'] - na
        db = c['b'] - nb
        d = dL * dL + da * da + db * db
        if d < min_dist_sq[i]:
            min_dist_sq[i] = d
    min_dist_sq[new_idx] = 0.0  # mark selected — excluded from future picks


def distill(
    palette: list[dict],
    assignments: bytearray,
    pixel_count: int,
    target_k: int,
    ghost_floor: float | None = None,
) -> dict:
    """Distill a large palette to K colors using coverage-seeded furthest-point sampling.

    palette:     list of {L, a, b} dicts (N colors, N >= K)
    assignments: bytearray of pixel→palette-index (length = pixel_count); 255 = transparent
    pixel_count: number of pixels
    target_k:    desired output color count
    ghost_floor: min coverage fraction (default MIN_COVERAGE = 0.1%)

    Returns dict with:
      palette:   reduced K-color palette (list of {L, a, b})
      remap:     bytearray mapping old index (0…N-1) to new index (0…K-1)
      selected:  list of original indices kept
    """
    N = len(palette)
    K = min(target_k, N)

    # ── 1. Count coverage per color ──────────────────────────────────────
    counts = [0.0] * N
    for idx in assignments:
        if idx < N:
            counts[idx] += 1

    # Nothing to reduce
    if N <= K:
        remap = bytearray(range(N))
        return {
            'palette':  [dict(c) for c in palette],
            'remap':    remap,
            'selected': list(range(N)),
        }

    # ── 2. Seed: highest-coverage color ──────────────────────────────────
    seed_idx = max(range(N), key=lambda i: counts[i])

    # ── 3. Greedy furthest-point selection ───────────────────────────────
    min_dist_sq = [math.inf] * N
    selected = [seed_idx]
    _update_min_dist(min_dist_sq, palette, seed_idx)

    threshold = (pixel_count or 1) * (MIN_COVERAGE if ghost_floor is None else ghost_floor)

    while len(selected) < K:
        best_score = -1.0
        best_idx   = -1

        for i in range(N):
            if min_dist_sq[i] == 0.0:
                continue          # already selected
            if counts[i] < threshold:
                continue          # ghost color
            score = math.sqrt(min_dist_sq[i]) * (1.0 + counts[i] / (pixel_count or 1))
            if score > best_score:
                best_score = score
                best_idx   = i

        if best_idx == -1:
            break
        selected.append(best_idx)
        _update_min_dist(min_dist_sq, palette, best_idx)

    # ── 4. Build reduced palette ─────────────────────────────────────────
    reduced_palette = [dict(palette[i]) for i in selected]

    # ── 5. Build remap: old index → nearest selected index (ΔE²) ─────────
    remap = bytearray(N)
    for i in range(N):
        c = palette[i]
        best_dist = math.inf
        best_slot = 0
        for j, orig_idx in enumerate(selected):
            s  = palette[orig_idx]
            dL = c['L'] - s['L']
            da = c['a'] - s['a']
            db = c['b'] - s['b']
            d  = dL * dL + da * da + db * db
            if d < best_dist:
                best_dist = d
                best_slot = j
        remap[i] = best_slot

    return {
        'palette':  reduced_palette,
        'remap':    remap,
        'selected': selected,
    }

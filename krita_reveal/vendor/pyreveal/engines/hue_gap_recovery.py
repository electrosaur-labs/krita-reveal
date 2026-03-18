"""
HueGapRecovery — hue sector analysis and gap recovery.

Divides the colour wheel into 12 sectors of 30° each:
  0: Red (0-30)        6: Blue (180-210)
  1: Orange (30-60)    7: B-Purple (210-240)
  2: Yellow (60-90)    8: Purple (240-270)
  3: Y-Green (90-120)  9: Magenta (270-300)
  4: Green (120-150)  10: Pink (300-330)
  5: Cyan (150-180)   11: R-Pink (330-360)

Returns -1 for achromatic pixels (chroma <= threshold).
"""

from __future__ import annotations

import math


def _get_hue_sector(a: float, b: float) -> int:
    """Map Lab a/b to a hue sector 0-11, or -1 if achromatic (chroma <= 5)."""
    CHROMA_THRESHOLD = 5
    chroma = math.sqrt(a * a + b * b)
    if chroma <= CHROMA_THRESHOLD:
        return -1
    angle = math.atan2(b, a) * (180 / math.pi)
    if angle < 0:
        angle += 360
    return min(int(angle / 30), 11)


def _analyze_image_hue_sectors(lab_pixels: list, chroma_threshold: float = 5) -> list:
    """Return 12-element list of percentage of chromatic pixels per sector.

    lab_pixels: flat list [L, a, b, L, a, b, ...] in perceptual space.
    Normalises by chromatic pixel count (not total) so substrate/achromatic
    pixels don't suppress minority hue sectors.
    """
    if not lab_pixels:
        return [0.0] * 12

    hue_counts = [0] * 12
    chroma_count = 0

    for i in range(0, len(lab_pixels), 3):
        a = lab_pixels[i + 1]
        b = lab_pixels[i + 2]
        chroma = math.sqrt(a * a + b * b)

        if chroma > chroma_threshold:
            chroma_count += 1
            hue = math.atan2(b, a) * 180 / math.pi
            hue_norm = hue if hue >= 0 else hue + 360
            sector_idx = min(int(hue_norm / 30), 11)
            hue_counts[sector_idx] += 1

    denominator = chroma_count if chroma_count > 0 else 1
    return [(count / denominator) * 100 for count in hue_counts]


def _analyze_palette_hue_coverage(palette: list, chroma_threshold: float = 5) -> dict:
    """Return {covered_sectors: set, color_counts_by_sector: list[12]}.

    palette: list of {'L', 'a', 'b'} dicts.
    """
    if not palette:
        return {'covered_sectors': set(), 'color_counts_by_sector': [0] * 12}

    covered_sectors = set()
    color_counts_by_sector = [0] * 12

    for color in palette:
        a = color['a']
        b = color['b']
        chroma = math.sqrt(a * a + b * b)
        if chroma > chroma_threshold:
            hue = math.atan2(b, a) * 180 / math.pi
            hue_norm = hue if hue >= 0 else hue + 360
            sector_idx = min(int(hue_norm / 30), 11)
            covered_sectors.add(sector_idx)
            color_counts_by_sector[sector_idx] += 1

    return {'covered_sectors': covered_sectors, 'color_counts_by_sector': color_counts_by_sector}


def _identify_hue_gaps(
    image_hues: list,
    palette_coverage,
    palette_color_counts_by_sector: list | None = None,
) -> list:
    """Return list of gap sector indices.

    image_hues:           12-element percentage list from _analyze_image_hue_sectors.
    palette_coverage:     set of covered sector indices (from _analyze_palette_hue_coverage).
    palette_color_counts_by_sector: optional per-sector color count list.

    GAP_THRESHOLD = 1.0% — sector must have >1% of chromatic pixels to matter.
    HEAVY_SECTOR_THRESHOLD = 40.0% — sector with >40% needs ≥2 palette colours.
    """
    GAP_THRESHOLD = 1.0
    HEAVY_SECTOR_THRESHOLD = 40.0
    gaps = []

    for i in range(len(image_hues)):
        if image_hues[i] > GAP_THRESHOLD and i not in palette_coverage:
            gaps.append(i)
        elif image_hues[i] > HEAVY_SECTOR_THRESHOLD and i in palette_coverage:
            colors_in_sector = (
                palette_color_counts_by_sector[i]
                if palette_color_counts_by_sector is not None
                else 1
            )
            if colors_in_sector < 2:
                gaps.append(i)

    return gaps


def _find_true_missing_hues(
    lab_pixels: list,
    current_palette: list,
    gaps: list,
    options: dict | None = None,
) -> list:
    """Scan image for high-chroma, distinct colours in missing hue sectors.

    lab_pixels:      flat list [L, a, b, ...] in perceptual space.
    current_palette: list of {'L', 'a', 'b'} dicts.
    gaps:            list of sector indices from _identify_hue_gaps.
    options:         optional dict with chroma_threshold, distinctness_threshold,
                     min_hue_coverage overrides.

    Returns list of {'L', 'a', 'b'} dicts sorted by chroma descending.
    Viability: sector must cover >= min_hue_coverage of chromatic pixels.
    """
    if options is None:
        options = {}

    CHROMA_THRESHOLD = options.get('chroma_threshold', 12)
    DISTINCTNESS_THRESHOLD = options.get('distinctness_threshold', 15)
    MIN_HUE_COVERAGE = options.get('min_hue_coverage', 0.01)

    if not lab_pixels or not gaps:
        return []

    total_pixels = len(lab_pixels) // 3
    gaps_set = set(gaps)

    # Count chromatic pixels for viability denominator
    chromatic_pixel_count = 0
    for i in range(0, len(lab_pixels), 3):
        a = lab_pixels[i + 1]
        b = lab_pixels[i + 2]
        if math.sqrt(a * a + b * b) > CHROMA_THRESHOLD:
            chromatic_pixel_count += 1
    viability_denominator = chromatic_pixel_count if chromatic_pixel_count > 0 else total_pixels

    # Per-sector: best sample (most saturated, distinct) and total scanned count
    bin_samples = [None] * 12
    sector_scanned = [0] * 12

    for i in range(0, len(lab_pixels), 3):
        L = lab_pixels[i]
        a = lab_pixels[i + 1]
        b = lab_pixels[i + 2]

        hue = (math.atan2(b, a) * 180 / math.pi + 360) % 360
        bin_idx = min(int(hue / 30), 11)

        if bin_idx not in gaps_set:
            continue

        sector_scanned[bin_idx] += 1

        chroma = math.sqrt(a * a + b * b)
        if chroma < CHROMA_THRESHOLD:
            continue

        # Only replace if more saturated
        if bin_samples[bin_idx] is not None and bin_samples[bin_idx]['chroma'] >= chroma:
            continue

        # Check distinctness from current palette
        min_dist = float('inf')
        for p in current_palette:
            dL = L - p['L']
            da = a - p['a']
            db = b - p['b']
            dist = math.sqrt(dL * dL + da * da + db * db)
            if dist < min_dist:
                min_dist = dist

        if min_dist > DISTINCTNESS_THRESHOLD:
            bin_samples[bin_idx] = {'L': L, 'a': a, 'b': b, 'chroma': chroma}

    # Collect viable results
    forced_colors = []
    for gap_idx in gaps:
        if bin_samples[gap_idx] is None:
            continue
        coverage = sector_scanned[gap_idx] / viability_denominator
        if coverage < MIN_HUE_COVERAGE:
            continue
        s = bin_samples[gap_idx]
        forced_colors.append({'L': s['L'], 'a': s['a'], 'b': s['b']})

    # Sort by chroma descending
    forced_colors.sort(
        key=lambda c: math.sqrt(c['a'] ** 2 + c['b'] ** 2),
        reverse=True,
    )
    return forced_colors


def _force_include_hue_gaps(colors: list, gaps: list, image_hues: list | None = None) -> list:
    """DEPRECATED: old hue gap filling. Use _find_true_missing_hues instead.

    Picks best sector-center-aligned high-chroma colour per gap sector.
    For heavy sectors (>20%), picks one light + one dark colour.
    """
    CHROMA_THRESHOLD = 5
    HEAVY_SECTOR_THRESHOLD = 20.0
    forced_colors = []

    for sector_idx in gaps:
        sector_colors = []
        for color in colors:
            a = color['a']
            b = color['b']
            chroma = math.sqrt(a * a + b * b)
            if chroma <= CHROMA_THRESHOLD:
                continue
            hue = math.atan2(b, a) * 180 / math.pi
            hue_norm = hue if hue >= 0 else hue + 360
            color_sector = min(int(hue_norm / 30), 11)
            if color_sector == sector_idx:
                sector_colors.append(color)

        if not sector_colors:
            continue

        is_heavy = image_hues is not None and image_hues[sector_idx] > HEAVY_SECTOR_THRESHOLD

        if is_heavy and len(sector_colors) > 1:
            sector_colors_sorted = sorted(sector_colors, key=lambda c: c['L'], reverse=True)

            light_slice = sector_colors_sorted[: max(1, int(len(sector_colors_sorted) * 0.3))]
            best_light = max(light_slice, key=lambda c: math.sqrt(c['a'] ** 2 + c['b'] ** 2))

            dark_slice = sector_colors_sorted[int(len(sector_colors_sorted) * 0.7):]
            if not dark_slice:
                dark_slice = sector_colors_sorted[-1:]
            best_dark = max(dark_slice, key=lambda c: math.sqrt(c['a'] ** 2 + c['b'] ** 2))

            forced_colors.append({'L': best_light['L'], 'a': best_light['a'], 'b': best_light['b']})
            forced_colors.append({'L': best_dark['L'], 'a': best_dark['a'], 'b': best_dark['b']})
        else:
            sector_center_angle = sector_idx * 30 + 15
            best_score = -1.0
            best = sector_colors[0]

            for color in sector_colors:
                chroma = math.sqrt(color['a'] ** 2 + color['b'] ** 2)
                hue = math.atan2(color['b'], color['a']) * 180 / math.pi
                hue_norm = hue if hue >= 0 else hue + 360
                angle_dist = abs(hue_norm - sector_center_angle)
                if angle_dist > 180:
                    angle_dist = 360 - angle_dist
                center_bonus = 1.0 - (angle_dist / 15.0)
                score = chroma * center_bonus
                if score > best_score:
                    best_score = score
                    best = color

            forced_colors.append({'L': best['L'], 'a': best['a'], 'b': best['b']})

    return forced_colors

"""
ArchetypeMapper v2.2 — 40/45/15 weighted scoring.

  40%: Structural DNA (L, C, K, L-StdDev) — weighted Euclidean + exponential decay
  45%: Sector Affinity (12-sector hue weights / chroma / tonal bonuses)
  15%: Pattern / Signature (entropy + temperature + sector weight)
"""

from __future__ import annotations

import math


class ArchetypeMapper:
    """Maps DNA to archetypes using three-component weighted scoring."""

    def __init__(self, archetypes: list, options: dict | None = None):
        if options is None:
            options = {}
        self.archetypes    = archetypes
        self.decay_constant = 0.05
        self.w_structural  = options.get('w_structural', 0.40)
        self.w_sector      = options.get('w_sector',     0.45)
        self.w_pattern     = options.get('w_pattern',    0.15)

    def get_best_match(self, dna: dict) -> dict:
        """Return the highest-scoring archetype match for this DNA."""
        results = self._score_all(dna)
        results.sort(key=lambda r: (-r['score'], r['id']))
        return results[0]

    def get_top_matches(self, dna: dict, n: int = 3) -> list:
        """Return top N archetype matches sorted by score descending."""
        results = self._score_all(dna)
        results.sort(key=lambda r: (-r['score'], r['id']))
        return results[:n]

    def _score_all(self, dna: dict) -> list:
        results = []
        for archetype in self.archetypes:
            s_struct = self.calculate_structural_score(dna, archetype)
            s_sector = self.calculate_sector_affinity(dna, archetype)
            s_pattern = self.calculate_pattern_score(dna, archetype)
            total = (s_struct * self.w_structural +
                     s_sector * self.w_sector +
                     s_pattern * self.w_pattern)
            results.append({
                'id':    archetype['id'],
                'score': round(total, 2),
                'breakdown': {
                    'structural':    round(s_struct, 1),
                    'sector_affinity': round(s_sector, 1),
                    'pattern':       round(s_pattern, 1),
                },
            })
        return results

    # -------------------------------------------------------------------------
    # 40%: Structural score

    def calculate_structural_score(self, dna: dict, archetype: dict) -> float:
        dims = ['l', 'c', 'k', 'l_std_dev']
        dist_sq = 0.0
        centroid = archetype['centroid']
        weights  = archetype.get('weights', {})
        g = dna['global']

        for dim in dims:
            w     = weights.get(dim, 1.0)
            delta = g[dim] - centroid[dim]
            dist_sq += w * delta * delta

        distance = math.sqrt(dist_sq)
        return 100 * math.exp(-self.decay_constant * distance)

    # -------------------------------------------------------------------------
    # 45%: Sector affinity

    def calculate_sector_affinity(self, dna: dict, archetype: dict) -> float:
        profile = archetype.get('scoring') or self._derive_profile(archetype)
        preferred = archetype.get('preferred_sectors') or []

        total_weight = 0.0
        weighted_affinity = 0.0

        for sector_name, sector in dna['sectors'].items():
            s_affinity = 50.0

            if sector_name in preferred:
                s_affinity += 30

            is_preferred = (not preferred) or (sector_name in preferred)
            if is_preferred:
                cp = profile.get('chroma_profile', '')
                c_max = sector['c_max']
                if   cp == 'extreme'    and c_max > 70:            s_affinity += 25
                elif cp == 'moderate'   and 20 <= c_max <= 60:     s_affinity += 15
                elif cp == 'low'        and c_max < 30:            s_affinity += 15
                elif cp == 'very_low'   and c_max < 20:            s_affinity += 20
                elif cp == 'achromatic' and c_max < 5:             s_affinity += 30

                tr = profile.get('tonal_range', '')
                l_mean = sector['l_mean']
                if   tr == 'dark'       and l_mean < 50:           s_affinity += 10
                elif tr == 'mid'        and 40 <= l_mean <= 65:    s_affinity += 10
                elif tr == 'mid-bright' and 50 <= l_mean <= 70:    s_affinity += 10
                elif tr == 'bright'     and l_mean > 55:           s_affinity += 10

            weighted_affinity += s_affinity * sector['weight']
            total_weight       += sector['weight']

        affinity = weighted_affinity / total_weight if total_weight > 0 else 50.0

        # Pattern-specific bonuses
        if profile.get('expects_outlier'):
            has_outlier = any(
                sector_name in preferred
                and dna['sectors'][sector_name]['weight'] < 0.15
                and dna['sectors'][sector_name]['c_max'] > 40
                for sector_name in dna['sectors']
            )
            if has_outlier:
                affinity += 20

        if profile.get('expects_dominance'):
            if (dna['global']['primary_sector_weight'] > 0.4 and
                    dna.get('dominant_sector') in preferred):
                affinity += 15

        if profile.get('max_chroma_gate') is not None:
            if dna['global']['c'] > profile['max_chroma_gate']:
                affinity -= 30
            else:
                affinity += 20

        if profile.get('max_l_std_dev_gate') is not None:
            if dna['global']['l_std_dev'] > profile['max_l_std_dev_gate']:
                affinity -= 30

        if profile.get('rewards_high_texture'):
            if dna['global']['l_std_dev'] > 18.0:
                affinity += 20

        if profile.get('max_sector_gate') is not None:
            if dna['global']['primary_sector_weight'] > profile['max_sector_gate']:
                affinity -= 30
            else:
                affinity += 20

        if profile.get('expects_high_entropy'):
            if dna['global']['hue_entropy'] > 0.85:
                affinity += 25
            elif dna['global']['hue_entropy'] < 0.75:
                affinity -= 20

        return max(0.0, min(100.0, affinity))

    # -------------------------------------------------------------------------
    # 15%: Pattern / signature score

    def calculate_pattern_score(self, dna: dict, archetype: dict) -> float:
        profile  = archetype.get('scoring') or self._derive_profile(archetype)
        centroid = archetype['centroid']
        weights  = archetype.get('weights', {})
        g = dna['global']

        entropy_delta = abs(g['hue_entropy'] - centroid.get('hue_entropy', 0.5))
        entropy_w     = weights.get('hue_entropy', 2.0)
        entropy_score = 100 * math.exp(-0.5 * entropy_w * entropy_delta)

        temp_delta = abs(g['temperature_bias'] - centroid.get('temperature_bias', 0.0))
        temp_w     = weights.get('temperature_bias', 1.5)
        temp_score = 100 * math.exp(-0.5 * temp_w * temp_delta)

        sw_delta = abs(g['primary_sector_weight'] - centroid.get('primary_sector_weight', 0.2))
        sw_w     = weights.get('primary_sector_weight', 2.5)
        sw_score = 100 * math.exp(-0.5 * sw_w * sw_delta)

        score = entropy_score * 0.4 + temp_score * 0.3 + sw_score * 0.3

        if profile.get('expects_monochrome') and g['hue_entropy'] < 0.3:
            score += 20
        if profile.get('expects_diversity') and g['hue_entropy'] > 0.7:
            score += 15
        if profile.get('expects_warm') and g['temperature_bias'] > 0.4:
            score += 10
        if profile.get('expects_cool') and g['temperature_bias'] < -0.3:
            score += 10

        return max(0.0, min(100.0, score))

    # -------------------------------------------------------------------------

    def _derive_profile(self, archetype: dict) -> dict:
        """Auto-derive scoring profile flags from archetype centroid values."""
        c = archetype['centroid']
        profile = {}

        cv = c.get('c', 25)
        if   cv < 5:  profile['chroma_profile'] = 'achromatic'
        elif cv < 15: profile['chroma_profile'] = 'very_low'
        elif cv < 30: profile['chroma_profile'] = 'low'
        elif cv < 60: profile['chroma_profile'] = 'moderate'
        else:         profile['chroma_profile'] = 'extreme'

        lv = c.get('l', 50)
        if   lv < 45: profile['tonal_range'] = 'dark'
        elif lv < 55: profile['tonal_range'] = 'mid'
        elif lv < 70: profile['tonal_range'] = 'mid-bright'
        else:         profile['tonal_range'] = 'bright'

        if c.get('temperature_bias', 0) > 0.3:   profile['expects_warm'] = True
        if c.get('temperature_bias', 0) < -0.3:  profile['expects_cool'] = True
        if c.get('hue_entropy', 0.5) < 0.3:      profile['expects_monochrome'] = True
        if c.get('hue_entropy', 0.5) > 0.7:      profile['expects_diversity'] = True
        if c.get('primary_sector_weight', 0) > 0.4: profile['expects_dominance'] = True
        if c.get('l_std_dev', 20) > 20:           profile['rewards_high_texture'] = True

        if c.get('l_std_dev', 20) < 12:
            profile['max_l_std_dev_gate'] = c['l_std_dev'] + 5

        return profile

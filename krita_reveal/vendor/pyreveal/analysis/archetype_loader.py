"""
ArchetypeLoader — loads and matches archetype JSON files from the package.

Supports DNA v1.0 (4D: L/C/K/σL) and DNA v2.0 (7D + 12-sector hue analysis).
"""

from __future__ import annotations

import json
import math
import pathlib

from .archetype_mapper import ArchetypeMapper

_ARCHETYPES_DIR = pathlib.Path(__file__).parent.parent / 'archetypes'

_DEFAULT_WEIGHTS = {'l': 0.5, 'c': 1.5, 'k': 1.0, 'l_std_dev': 2.0}

_FALLBACK_ARCHETYPE = {
    'id': 'everyday_photo',
    'name': 'Standard Balanced',
    'description': 'Fallback archetype',
    'centroid': {
        'l': 50, 'c': 25, 'k': 50, 'l_std_dev': 25,
        'hue_entropy': 0.5, 'temperature_bias': 0.0, 'primary_sector_weight': 0.2,
    },
    'weights': {'l': 0.5, 'c': 1.5, 'k': 1.0, 'l_std_dev': 2.0},
    'parameters': {
        'min_colors': 4, 'max_colors': 10,
        'dither_type': 'atkinson', 'distance_metric': 'cie76',
        'l_weight': 1.2, 'c_weight': 2.0, 'black_bias': 3.0,
        'vibrancy_mode': 'moderate', 'vibrancy_boost': 1.4,
        'highlight_threshold': 90, 'highlight_boost': 1.5,
        'enable_palette_reduction': True, 'palette_reduction': 6.0,
        'substrate_mode': 'auto', 'substrate_tolerance': 2.0,
        'shadow_point': 15, 'enable_hue_gap_analysis': True,
        'hue_lock_angle': 20, 'color_mode': 'color',
        'preserve_white': True, 'preserve_black': True,
        'ignore_transparent': True, 'preprocessing_intensity': 'auto',
    },
}


class ArchetypeLoader:
    """Loads archetype JSON files and matches DNA to the best archetype."""

    _archetypes: list | None = None

    @classmethod
    def _apply_defaults(cls, archetype: dict) -> dict:
        if 'weights' not in archetype:
            archetype['weights'] = dict(_DEFAULT_WEIGHTS)
        return archetype

    @classmethod
    def load_archetypes(cls) -> list:
        """Load all archetype JSON files. Results are cached."""
        if cls._archetypes is not None:
            return cls._archetypes

        if not _ARCHETYPES_DIR.is_dir():
            return [dict(_FALLBACK_ARCHETYPE)]

        archetypes = []
        for path in sorted(_ARCHETYPES_DIR.glob('*.json')):
            if path.name == 'schema.json':
                continue
            with path.open(encoding='utf-8') as f:
                archetypes.append(cls._apply_defaults(json.load(f)))

        if not archetypes:
            return [dict(_FALLBACK_ARCHETYPE)]

        archetypes.sort(key=lambda a: a['id'])
        cls._archetypes = archetypes
        return cls._archetypes

    @classmethod
    def match_archetype(cls, dna: dict, manual_archetype_id: str | None = None) -> dict:
        """Match DNA to nearest archetype.

        Supports DNA v1.0 (4D) and v2.0 (7D + sectors).
        manual_archetype_id bypasses DNA matching if provided.
        """
        archetypes = cls.load_archetypes()

        if manual_archetype_id:
            match = next((a for a in archetypes if a['id'] == manual_archetype_id), None)
            if match:
                return match

        is_dna_v2 = (dna.get('version') == '2.0' and
                     'global' in dna and 'sectors' in dna)

        if is_dna_v2:
            return cls._match_dna_v2(dna, archetypes)
        return cls._match_dna_v1(dna, archetypes)

    @classmethod
    def _match_dna_v2(cls, dna: dict, archetypes: list) -> dict:
        mapper = ArchetypeMapper(archetypes)
        all_matches = mapper.get_top_matches(dna, len(archetypes))
        result = all_matches[0]

        archetype = next(a for a in archetypes if a['id'] == result['id'])
        archetype['match_score']     = result['score']
        archetype['match_breakdown'] = result['breakdown']
        archetype['match_version']   = '2.0'
        archetype['match_ranking']   = all_matches
        return archetype

    @classmethod
    def _match_dna_v1(cls, dna: dict, archetypes: list) -> dict:
        l        = dna.get('l', 50)
        c        = dna.get('c', 20)
        k        = dna.get('k', 50)
        l_std_dev = dna.get('l_std_dev', 25)

        best    = None
        min_dist = math.inf

        for archetype in archetypes:
            centroid = archetype['centroid']
            weights  = archetype.get('weights', _DEFAULT_WEIGHTS)

            d_sq = (weights.get('l', 0.5)       * (l        - centroid['l']) ** 2 +
                    weights.get('c', 1.5)        * (c        - centroid['c']) ** 2 +
                    weights.get('k', 1.0)        * (k        - centroid['k']) ** 2 +
                    weights.get('l_std_dev', 2.0) * (l_std_dev - centroid['l_std_dev']) ** 2)
            dist = math.sqrt(d_sq)

            if dist < min_dist:
                min_dist = dist
                best = archetype

        best['match_distance'] = min_dist
        best['match_version']  = '1.0'
        return best

    @classmethod
    def get_fallback_archetype(cls) -> dict:
        return dict(_FALLBACK_ARCHETYPE)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached archetypes (for testing)."""
        cls._archetypes = None

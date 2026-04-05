"""
interpolator_engine.py — Python port of InterpolatorEngine.js

KNN cluster-then-interpolate parameter generation.
Given a DNA vector, finds the K nearest cluster centroids and blends
their parameter sets weighted by inverse distance.

Identical algorithm to the JS implementation; shares the same
interpolator-model.json trained model file.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

# ---------------------------------------------------------------------------
# Parameter classification (mirrors InterpolatorEngine.js)
# ---------------------------------------------------------------------------

CONTINUOUS_PARAMS = [
    'lWeight', 'cWeight', 'bWeight', 'blackBias',
    'vibrancyBoost', 'saturationBoost',
    'highlightThreshold', 'highlightBoost',
    'paletteReduction', 'substrateTolerance', 'hueLockAngle', 'shadowPoint',
    'shadowClamp', 'minVolume', 'speckleRescue', 'detailRescue',
    'neutralSovereigntyThreshold',
    'chromaGate', 'refinementPasses',
    'minColors', 'maxColors',
    'shadowChromaGateL',
]

ORDERED_ENUMS: dict[str, list[str]] = {
    'vibrancyMode':           ['subtle', 'moderate', 'aggressive', 'exponential'],
    'preprocessingIntensity': ['off', 'none', 'light', 'medium', 'heavy'],
}

PREP_ALIASES = {'none': 'off'}

CATEGORICAL_PARAMS = [
    'ditherType', 'distanceMetric', 'centroidStrategy',
    'substrateMode', 'colorMode',
    'preserveWhite', 'preserveBlack', 'ignoreTransparent',
    'enablePaletteReduction', 'enableHueGapAnalysis', 'medianPass',
]

DIM_KEYS = [
    'l', 'c', 'k', 'l_std_dev', 'hue_entropy',
    'temperature_bias', 'primary_sector_weight',
]

_MODEL_PATH = os.path.join(os.path.dirname(__file__), 'interpolator-model.json')


# ---------------------------------------------------------------------------
# InterpolatorEngine
# ---------------------------------------------------------------------------

class InterpolatorEngine:
    """Cluster-then-interpolate parameter generation (port of InterpolatorEngine.js)."""

    def __init__(self, model: dict):
        self._norm      = model['normalization']   # {mean: [...], std: [...]}
        self._neighbors = model.get('blendNeighbors', 3)
        self._clusters  = model['clusters']

    # ------------------------------------------------------------------
    # Public

    def interpolate(self, dna: dict) -> dict:
        """Blend parameters for a DNA vector.

        dna must be a flat dict with keys matching DIM_KEYS
        (l, c, k, l_std_dev, hue_entropy, temperature_bias, primary_sector_weight).

        Returns {'parameters': {...}, 'blendInfo': {...}}.
        """
        mean = self._norm['mean']
        std  = self._norm['std']
        vec  = [(dna.get(k, 0) - mean[i]) / std[i] for i, k in enumerate(DIM_KEYS)]

        # Distance to every cluster centroid
        dists = [
            (i, _euclidean(vec, c['centroid']), c)
            for i, c in enumerate(self._clusters)
        ]
        dists.sort(key=lambda x: x[1])
        nearest = dists[:self._neighbors]

        # Inverse-distance weights
        FLOOR = 1e-6
        raw_w  = [1.0 / max(d, FLOOR) for _, d, _ in nearest]
        w_sum  = sum(raw_w)
        weights = [w / w_sum for w in raw_w]

        blended = self._blend_parameters(nearest, weights)

        blend_info = {
            'neighbors': [
                {
                    'clusterId':       c.get('id'),
                    'sourceArchetype': c.get('sourceArchetype'),
                    'distance':        round(d, 4),
                    'weight':          round(weights[i], 4),
                }
                for i, (_, d, c) in enumerate(nearest)
            ]
        }

        return {'parameters': blended, 'blendInfo': blend_info}

    # ------------------------------------------------------------------
    # Private

    def _blend_parameters(self, nearest: list, weights: list) -> dict:
        params_list = [c['parameters'] for _, _, c in nearest]
        result: dict[str, Any] = {}

        # 1. Continuous: weighted average
        for key in CONTINUOUS_PARAMS:
            vals = [p.get(key) for p in params_list]
            if all(v is None for v in vals):
                continue
            blended = sum((v or 0) * weights[i] for i, v in enumerate(vals))
            result[key] = round(blended, 4)

        # Round integer params
        for int_key in ('minColors', 'maxColors', 'refinementPasses'):
            if int_key in result:
                result[int_key] = round(result[int_key])

        # 2. Ordered enums: ordinal weighted average, snap to nearest
        for key, scale in ORDERED_ENUMS.items():
            vals = [p.get(key) for p in params_list]
            if all(v is None for v in vals):
                continue
            indices = []
            for v in vals:
                if key == 'preprocessingIntensity' and v in PREP_ALIASES:
                    v = PREP_ALIASES[v]
                idx = scale.index(v) if v in scale else 0
                indices.append(idx)
            blended_idx = sum(idx * weights[i] for i, idx in enumerate(indices))
            snapped     = min(round(blended_idx), len(scale) - 1)
            result[key] = scale[snapped]

        # 3. Categorical/boolean: nearest cluster wins
        for key in CATEGORICAL_PARAMS:
            val = params_list[0].get(key)
            if val is not None:
                result[key] = val

        # 4. Safe defaults for params not stored in archetype JSONs
        result.setdefault('centroidStrategy', 'SALIENCY')
        result.setdefault('ditherType',       'atkinson')
        result.setdefault('medianPass',       False)
        result.setdefault('bWeight',          1.0)
        result.setdefault('saturationBoost',  result.get('vibrancyBoost', 1.4))
        result.setdefault('detailRescue',     0)
        result.setdefault('chromaGate',       1.0)
        result.setdefault('shadowChromaGateL', 0)

        return result


# ---------------------------------------------------------------------------
# camelCase → snake_case config conversion
# (used by pipeline.py and parity tests to feed interpolator output into
#  ParameterGenerator.to_engine_options)
# ---------------------------------------------------------------------------

_CAMEL_TO_SNAKE: dict[str, str] = {
    'lWeight':                     'l_weight',
    'cWeight':                     'c_weight',
    'bWeight':                     'b_weight',
    'blackBias':                   'black_bias',
    'vibrancyBoost':               'vibrancy_boost',
    'vibrancyMode':                'vibrancy_mode',
    'highlightThreshold':          'highlight_threshold',
    'highlightBoost':              'highlight_boost',
    'paletteReduction':            'palette_reduction',
    'enablePaletteReduction':      'enable_palette_reduction',
    'substrateTolerance':          'substrate_tolerance',
    'substrateMode':               'substrate_mode',
    'hueLockAngle':                'hue_lock_angle',
    'enableHueGapAnalysis':        'enable_hue_gap_analysis',
    'shadowPoint':                 'shadow_point',
    'colorMode':                   'color_mode',
    'preserveWhite':               'preserve_white',
    'preserveBlack':               'preserve_black',
    'ignoreTransparent':           'ignore_transparent',
    'centroidStrategy':            'centroid_strategy',
    'splitMode':                   'split_mode',
    'quantizer':                   'quantizer',
    'refinementPasses':            'refinement_passes',
    'neutralSovereigntyThreshold': 'neutral_sovereignty_threshold',
    'chromaGate':                  'chroma_gate',
    'detailRescue':                'detail_rescue',
    'speckleRescue':               'speckle_rescue',
    'medianPass':                  'median_pass',
    'minVolume':                   'min_volume',
    'shadowClamp':                 'shadow_clamp',
    'shadowChromaGateL':           'shadow_chroma_gate_l',
    'distanceMetric':              'distance_metric',
    'ditherType':                  'dither_type',
    'maxColors':                   'target_colors',
    'preprocessingIntensity':      'preprocessing_intensity',
}


def to_pyreveal_config(interp_params: dict, dna: dict | None = None) -> dict:
    """Convert InterpolatorEngine camelCase output to the snake_case config dict
    expected by ParameterGenerator.to_engine_options().

    The returned config sets engine_type='distilled' (Chameleon always uses
    distilled) and neutral_centroid_clamp_threshold=0.5 (fixed safety floor).
    Callers may override either field after the call.
    """
    config: dict = {}
    for camel, snake in _CAMEL_TO_SNAKE.items():
        val = interp_params.get(camel)
        if val is not None:
            config[snake] = val
    config['engine_type'] = 'distilled'
    config['neutral_centroid_clamp_threshold'] = 0.5
    config.setdefault('preprocessing', {'enabled': False})
    if dna is not None:
        config['range_clamp'] = [dna.get('min_l', 0), dna.get('max_l', 100)]
    return config


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-loaded)
# ---------------------------------------------------------------------------

_engine: InterpolatorEngine | None = None


def get_engine() -> InterpolatorEngine:
    global _engine
    if _engine is None:
        with open(_MODEL_PATH, 'r') as f:
            model = json.load(f)
        _engine = InterpolatorEngine(model)
    return _engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _euclidean(a: list, b: list) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

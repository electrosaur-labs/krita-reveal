"""
DNAFidelity — Closed-loop posterization audit via DNA comparison.

Compares input DNA (original image) to output DNA (posterized result)
to detect structural drift that per-pixel ΔE cannot catch.

Example: grey fur (C*≈2) mapped to light blue (C*≈10) has small
per-pixel ΔE but the image's "soul" shifted from neutral to chromatic.
DNAFidelity catches this via chroma drift alert.

Pure math — no I/O, no dependencies beyond DNAGenerator.
"""

from __future__ import annotations

import math

from ..analysis.dna import DNAGenerator


# ── Dimension metadata for normalisation and alerting ──────────────────────
#
# Calibrated against SP100 (147 images) + TESTIMAGES (40 images), 2026-02-19.
# Wide ranges dampen quantisation-noise dimensions; tight chroma range
# amplifies its contribution so chroma accounts for >20 % of cost.
# Decay=1.2 targets: typical posterisation ≈ F=75, best ≈ F=90+.

_GLOBAL_DIMS = [
    {'key': 'l',                     'range': 100, 'weight': 1.0},
    {'key': 'c',                     'range': 30,  'weight': 2.5},   # Primary quality signal
    {'key': 'k',                     'range': 200, 'weight': 0.8},   # Inherent to posterisation
    {'key': 'l_std_dev',             'range': 40,  'weight': 1.2},
    {'key': 'hue_entropy',           'range': 3,   'weight': 0.3},   # Quantisation noise
    {'key': 'temperature_bias',      'range': 6,   'weight': 0.3},   # Quantisation noise
    {'key': 'primary_sector_weight', 'range': 3,   'weight': 0.8},   # Quantisation noise
]

# Alert thresholds — calibrated to fire only on anomalous drift
_ALERT_RULES = [
    {
        'key': 'c',
        'test': lambda d: abs(d) > 5.0,
        'label': lambda d: f"Chroma drift ({'+' if d >= 0 else ''}{d:.1f})",
    },
    {
        'key': 'hue_entropy',
        'test': lambda d: d < -0.40,
        'label': lambda d: f"Entropy collapse ({d:.2f})",
    },
    {
        'key': 'temperature_bias',
        'test': lambda d: abs(d) > 0.8,
        'label': lambda d: f"Temperature shift ({'+' if d >= 0 else ''}{d:.1f})",
    },
    {
        'key': 'l_std_dev',
        'test': lambda d: d < -5.0,
        'label': lambda d: f"Contrast loss ({d:.1f})",
    },
    {
        'key': 'primary_sector_weight',
        'test': lambda d: d > 0.20,
        'label': lambda d: f"Ink imbalance (+{d:.2f})",
    },
]

_SECTOR_DRIFT_THRESHOLD = 1.0
_FIDELITY_DECAY = 1.2   # exp(−decay × distance) — targets avg F≈80 on TESTIMAGES


class DNAFidelity:

    @staticmethod
    def compare(input_dna: dict | None, output_dna: dict | None) -> dict:
        """Compare input DNA to output DNA.

        Returns {global, sectors, sectorDrift, fidelity, alerts}.
        Returns fidelity=100 / no alerts when either DNA is missing.
        """
        if (not input_dna or not input_dna.get('global')
                or not output_dna or not output_dna.get('global')):
            return {'global': {}, 'sectors': {}, 'sectorDrift': 0, 'fidelity': 100, 'alerts': []}

        in_g = input_dna['global']
        out_g = output_dna['global']

        # ── Global dimension diffs ────────────────────────────────────────
        global_result = {}
        sum_sq_norm = 0.0

        for dim in _GLOBAL_DIMS:
            key = dim['key']
            in_val = float(in_g.get(key) or 0)
            out_val = float(out_g.get(key) or 0)
            delta = out_val - in_val

            global_result[key] = {
                'input': in_val,
                'output': out_val,
                'delta': round(delta, 4),
            }

            norm = delta / dim['range']
            sum_sq_norm += dim['weight'] * norm * norm

        # ── Sector weight diffs ───────────────────────────────────────────
        sectors_result = {}
        sector_drift = 0.0

        in_sectors = input_dna.get('sectors') or {}
        out_sectors = output_dna.get('sectors') or {}

        all_names = set(in_sectors.keys()) | set(out_sectors.keys())
        for name in all_names:
            in_w = float((in_sectors.get(name) or {}).get('weight') or 0)
            out_w = float((out_sectors.get(name) or {}).get('weight') or 0)
            delta = out_w - in_w

            sectors_result[name] = {
                'input': in_w,
                'output': out_w,
                'delta': round(delta, 4),
            }
            sector_drift += abs(delta)

        sector_drift = round(sector_drift, 4)

        # ── Fidelity score (0-100) ────────────────────────────────────────
        distance = math.sqrt(sum_sq_norm)
        fidelity = round(100 * math.exp(-_FIDELITY_DECAY * distance))

        # ── Alerts ───────────────────────────────────────────────────────
        alerts = []
        for rule in _ALERT_RULES:
            key = rule['key']
            delta = global_result[key]['delta'] if key in global_result else 0.0
            if rule['test'](delta):
                alerts.append(rule['label'](delta))

        if sector_drift > _SECTOR_DRIFT_THRESHOLD:
            alerts.append(f"Sector redistribution ({sector_drift:.2f})")

        return {
            'global': global_result,
            'sectors': sectors_result,
            'sectorDrift': sector_drift,
            'fidelity': fidelity,
            'alerts': alerts,
        }

    @staticmethod
    def from_indices(
        input_dna: dict,
        color_indices,
        lab_palette: list,
        width: int,
        height: int,
    ) -> dict:
        """Full pipeline: compute output DNA from indices, then compare.

        input_dna:     pre-computed input DNA
        color_indices: posterized pixel indices
        lab_palette:   palette in perceptual Lab [{L, a, b}, ...]
        Returns same structure as compare().
        """
        palette_tuples = [(c['L'], c['a'], c['b']) for c in lab_palette]
        output_dna = DNAGenerator.from_indices(color_indices, palette_tuples, width, height)
        return DNAFidelity.compare(input_dna, output_dna)

"""
stats.py — UI logic for displaying DNA analysis and fidelity statistics.
"""

from __future__ import annotations
from PyQt5.QtWidgets import QLabel


class StatsManager:
    def __init__(self, dock):
        self.dock = dock

    def update_stats(self, res, matched, archetype_scores):
        self.dock._stat_archetype.setText(matched.get('name', 'Custom').upper())
        self.dock._stat_archetype.setVisible(True)
        self.dock._stat_colors.setText(f"{res['metadata']['final_colors']} colors")
        self.dock._stat_colors.setVisible(True)
        self.dock._sep_colors.setVisible(True)
        
        self.set_stat_rated(self.dock._stat_delta, 'deltaE', res.get('meanDeltaE', matched.get('meanDeltaE', 0)), 'ΔE ')
        self.dock._stat_delta.setVisible(True)
        self.dock._sep_delta.setVisible(True)
        
        self.set_stat_rated(self.dock._stat_dna, 'dna', res.get('dnaFidelity', {}).get('fidelity', 100), 'DNA ')
        self.dock._stat_dna.setVisible(True)
        self.dock._sep_dna.setVisible(True)
        
        ms = 0
        if archetype_scores:
            aid = matched.get('id')
            for s in archetype_scores:
                if s['id'] == aid:
                    ms = s['score']
                    break
                    
        self.set_stat_rated(self.dock._stat_match, 'match', ms, 'Match ', '%')
        self.dock._stat_match.setVisible(True)
        self.dock._sep_match.setVisible(True)

    def set_stat_rated(self, lbl, met, val, pre, suf=''):
        if val is None:
            lbl.setText('')
            return
        lbl.setText(f"{pre}{val:.1f}{suf}" if isinstance(val, float) else f"{pre}{val}{suf}")
        c = '#ccc'
        if met == 'deltaE':
            if val < 6: c = '#5cd65c'
            elif val < 10: c = '#8bd68b'
            elif val < 15: c = '#e0c97f'
            else: c = '#e07f7f'
        elif met == 'dna':
            if val > 80: c = '#5cd65c'
            elif val > 65: c = '#8bd68b'
            elif val > 50: c = '#e0c97f'
            else: c = '#e07f7f'
        elif met == 'match':
            if val > 70: c = '#5cd65c'
            elif val > 55: c = '#8bd68b'
            elif val > 40: c = '#e0c97f'
            else: c = '#e07f7f'
        lbl.setStyleSheet(f'color: {c}; font-size: 14px; font-weight: 600;')

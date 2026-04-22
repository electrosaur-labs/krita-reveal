"""
archetypes.py — UI logic for archetype matching and color suggestions.
"""

from __future__ import annotations
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame, QComboBox, QApplication
from PyQt5.QtCore import Qt


class ArchetypeManager:
    def __init__(self, dock):
        self.dock = dock

    def render_archetypes(self, archs, mid):
        self.dock._archetype_combo.blockSignals(True)
        self.dock._archetype_combo.clear()
        sorted_a = sorted(archs, key=lambda a: a['score'], reverse=True)
        self.dock._archetype_list = sorted_a
        top = sorted_a[:6]
        rest = sorted_a[6:]
        
        for a in top:
            self.dock._archetype_combo.addItem(f"{a['name']}  {int(a['score'])}%", a['id'])
            
        if rest:
            if getattr(self.dock, '_others_expanded', False):
                self.dock._archetype_combo.insertSeparator(self.dock._archetype_combo.count())
                for a in rest:
                    self.dock._archetype_combo.addItem(f"{a['name']}  {int(a['score'])}%", a['id'])
                self.dock._archetype_combo.addItem('▴ Collapse others', '__others_toggle__')
            else:
                self.dock._archetype_combo.addItem(f'▾ Others ({len(rest)})…', '__others_toggle__')
                
        idx = self.dock._archetype_combo.findData(mid)
        if idx >= 0:
            self.dock._archetype_combo.setCurrentIndex(idx)
        self.dock._archetype_combo.blockSignals(False)

    def render_suggestions(self, sugs):
        while self.dock._suggested_grid.count():
            item = self.dock._suggested_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
                
        if not sugs:
            self.dock._suggested_widget.setVisible(False)
            return
            
        for s in sugs:
            row = QWidget()
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 4, 0)
            rlay.setSpacing(3)
            
            blk = QFrame()
            blk.setFixedSize(19, 19)
            blk.setStyleSheet(f"background: {s['hex']}; border: 1px solid rgba(255,255,255,0.15); border-radius: 2px;")
            
            slbl = QLabel(str(int(s['score'])))
            slbl.setStyleSheet('color: #999; font-size: 10px;')
            
            rlay.addWidget(blk)
            rlay.addWidget(slbl)
            row.setToolTip(f"{s['hex']}\n{s['reason']}")
            self.dock._suggested_grid.addWidget(row)
            
        self.dock._suggested_grid.addStretch()
        self.dock._suggested_widget.setVisible(True)

    def apply_matched_archetype(self, ma):
        if not ma:
            return
            
        M = {
            'colors': 'colors',
            'density': 'density',
            'speckle': 'speckle',
            'clamp': 'clamp',
            'vibrancy_boost': 'vibrancy_boost',
            'vibrancy_mode': 'vibrancy_mode',
            'l_weight': 'l_weight',
            'c_weight': 'c_weight',
            'black_bias': 'black_bias',
            'shadow_point': 'shadow_point',
            'palette_reduction': 'palette_reduction',
            'enable_palette_reduction': 'enable_palette_reduction',
            'enable_hue_gap_analysis': 'enable_hue_gap',
            'hue_lock_angle': 'hue_lock_angle',
            'preserve_white': 'preserve_white',
            'preserve_black': 'preserve_black',
            'preprocessing': 'preprocessing',
            'engine_type': 'engine_type',
            'color_mode': 'color_mode',
            'dither_type': 'dither_type',
            'distance_metric': 'distance_metric',
            'centroid_strategy': 'centroid_strategy',
            'split_mode': 'split_mode',
            'quantizer': 'quantizer',
            'neutral_sovereignty_threshold': 'neutral_sovereignty',
            'chroma_gate': 'chroma_gate',
            'highlight_threshold': 'highlight_threshold',
            'highlight_boost': 'highlight_boost',
            'median_pass': 'median_pass',
            'detail_rescue': 'detail_rescue',
            'substrate_mode': 'substrate_mode',
            'substrate_tolerance': 'substrate_tolerance',
            'ignore_transparent': 'ignore_transparent',
            'mesh_size': 'mesh_size',
            'trap_size': 'trap_size'
        }
        
        for mk, ck in M.items():
            val = ma.get(mk)
            if val is not None and ck in self.dock._controls:
                self.dock._controls[ck].set_archetype_default(val)
                self.dock._controls[ck].set_value(val, programmatic=True)
                self.dock._archetype_defaults[ck] = val

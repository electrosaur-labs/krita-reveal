"""
sections.py — Builders for UI control sections (Basic, Screen Printing, Advanced).
"""

from __future__ import annotations
from PyQt5.QtWidgets import QLabel, QFrame
from .widgets import _RevealSlider, _RevealCombo, _RevealCheck


class SectionBuilder:
    def __init__(self, dock):
        self.dock = dock
        self.controls = {}

    def _line_sep(self):
        s = QFrame()
        s.setFixedHeight(3)
        s.setStyleSheet('background-color: #666; margin-top: 10px; margin-bottom: 10px;')
        return s

    def slider(self, k, l, mn, mx, d, st, f, h=None, rr=True, t=None):
        w = _RevealSlider(k, l, mn, mx, d, st, f, h)
        self.controls[k] = w
        if rr:
            w.valueChanged.connect(self.dock._schedule_rerun)
        t.addWidget(w)
        return w

    def combo(self, k, l, opts, d, rr=True, t=None, h=None):
        w = _RevealCombo(k, l, opts, d, help_text=h)
        self.controls[k] = w
        if rr:
            w.valueChanged.connect(self.dock._schedule_rerun)
        t.addWidget(w)
        return w

    def check(self, k, l, d, rr=True, t=None, h=None):
        w = _RevealCheck(k, l, d, help_text=h)
        self.controls[k] = w
        if rr:
            w.valueChanged.connect(self.dock._schedule_rerun)
        t.addWidget(w)
        return w

    def sub(self, txt, t=None, hr=True):
        if hr:
            t.addWidget(self._line_sep())
        lbl = QLabel(txt)
        lbl.setStyleSheet(
            'color: #e0e0e0; font-size: 11px; font-weight: 600; '
            'letter-spacing: 0.5px; margin-top: 6px; padding-bottom: 2px;'
        )
        t.addWidget(lbl)

    def build_all(self, bas, sph, adv):
        # --- Basic Section ---
        self.sub('Colors', t=bas, hr=False)
        self.slider('colors', 'Target Colors', 3, 12, 6, 1, lambda v: str(int(v)), 'Target number of output colors.', t=bas)
        self.check('preserve_white', 'Preserve White', True, t=bas, h='Prevents highlights from being washed out.')
        self.check('preserve_black', 'Preserve Black', True, t=bas, h='Ensures black detail is retained.')
        self.check('enable_palette_reduction', 'Auto Merge', True, t=bas, h='Merges colors closer than Merge Distance.')
        bas.addWidget(self._line_sep())

        self.sub('Substrate', t=bas, hr=False)
        combo_opts = [
            ('auto', 'Auto'),
            ('white', 'White'),
            ('black', 'Black'),
            ('none', 'None')
        ]
        self.combo('substrate_mode', 'Substrate', combo_opts, 'auto', t=bas, h='What you are printing on. Auto detects background from corners. White/Black set color explicitly. None treats background as a printable color.')
        self.sub('Refinement', t=bas)
        self.slider('density', 'Minimum Coverage', 0, 5, 0.5, 0.1, lambda v: f'{v:.1f}%', 'Colors covering less than this percentage get merged.', t=bas)
        self.slider('speckle', 'Despeckle', 0, 30, 0, 1, lambda v: f'{int(v)} px', 'Remove isolated pixel clusters.', t=bas)
        self.slider('clamp', 'Minimum Opacity', 0, 40, 0, 0.5, lambda v: f'{int(v)}%', 'Minimum mask density for each color.', t=bas)

        self.sub('Post-processing', t=bas, hr=True)
        dither_opts = [
            ('none', 'None'),
            ('floyd-steinberg', 'FS'),
            ('atkinson', 'Atkinson'),
            ('atkinson-lite', 'Atkinson Lite'),
            ('bayer', 'Bayer')
        ]
        self.combo('dither_type', 'Dither', dither_opts, 'none', rr=False, t=bas, h='Select halftone dithering algorithm.')
        bas.addWidget(self._line_sep())

        # --- Screen Printing Section ---
        mesh_opts = [
            (0, 'No Mesh'),
            (110, '110 TPI'),
            (156, '156 TPI'),
            (200, '200 TPI'),
            (230, '230 TPI'),
            (280, '280 TPI'),
            (305, '305 TPI'),
            (355, '355 TPI')
        ]
        self.combo('mesh_size', 'Mesh', mesh_opts, 230, rr=False, t=sph, h='Target screen mesh resolution.')
        self.slider('trap_size', 'Trap Width', 0, 10, 0, 1, lambda v: f'{int(v)} pt', 'Expansion to prevent registration gaps.', rr=False, t=sph)

        # --- Advanced Section ---
        self.sub('Algorithm', t=adv, hr=False)
        engine_opts = [
            ('reveal-mk1.5', 'Standard'),
            ('distilled', 'Adaptive'),
            ('reveal', 'Hue-Aware'),
            ('balanced', 'Fast'),
            ('stencil', 'Stencil')
        ]
        self.combo('engine_type', 'Method', engine_opts, 'reveal-mk1.5', t=adv, h='Core separation engine logic.')
        
        color_mode_opts = [('color', 'Color'), ('bw', 'B/W'), ('grayscale', 'Gray')]
        self.combo('color_mode', 'Color Mode', color_mode_opts, 'color', t=adv, h='Restrict palette generation.')
        
        split_opts = [('variance', 'Detail'), ('median', 'Color')]
        self.combo('split_mode', 'Initial Palette', split_opts, 'median', t=adv, h='Starting point for quantization.')
        
        quant_opts = [('wu', 'Wu'), ('median_cut', 'Median Cut')]
        self.combo('quantizer', 'Quantizer', quant_opts, 'wu', t=adv, h='Vector quantization algorithm.')
        
        dist_opts = [('cie76', 'CIE76'), ('cie94', 'CIE94'), ('cie2000', 'CIE2000')]
        self.combo('distance_metric', 'Color Match', dist_opts, 'cie76', t=adv, h='ΔE distance calculation method.')
        
        centroid_opts = [
            ('SALIENCY', 'Saliency'), 
            ('ROBUST_SALIENCY', 'Robust'), 
            ('VOLUMETRIC', 'Volumetric'), 
            ('AVERAGE', 'Average')
        ]
        self.combo('centroid_strategy', 'Selection', centroid_opts, 'ROBUST_SALIENCY', t=adv, h='Seed selection for centroids.')
        self.slider('neutral_sovereignty', 'Gray Protection', 0, 100, 0, 1, lambda v: str(int(v)), t=adv)

        self.sub('Saturation', t=adv)
        self.slider('vibrancy_boost', 'Vibrancy', 0.5, 3.0, 1.4, 0.05, lambda v: f'{v:.1f}', t=adv)
        vibe_opts = [
            ('linear', 'Linear'), 
            ('subtle', 'Subtle'), 
            ('moderate', 'Moderate'), 
            ('aggressive', 'Aggressive')
        ]
        self.combo('vibrancy_mode', 'Curve', vibe_opts, 'moderate', t=adv)
        self.slider('chroma_gate', 'Gate', 1.0, 3.0, 1.0, 0.1, lambda v: f'{v:.1f}', t=adv)

        self.sub('Hue Recovery', t=adv)
        self.check('enable_hue_gap', 'Enabled', True, t=adv, h='Detects and separates distinct hues.')
        self.slider('hue_lock_angle', 'Lock Angle', 10, 60, 20, 5, lambda v: f'{int(v)}°', t=adv)

        self.sub('Weighting', t=adv)
        self.slider('l_weight', 'Lightness', 0.5, 3.0, 1.2, 0.1, lambda v: f'{v:.1f}', t=adv)
        self.slider('c_weight', 'Chroma', 0.5, 5.0, 2.0, 0.1, lambda v: f'{v:.1f}', t=adv)
        self.slider('black_bias', 'Black Pull', 0, 10, 3.0, 0.5, lambda v: f'{v:.1f}', t=adv)

        self.sub('Tone', t=adv)
        self.slider('highlight_threshold', 'Highlight Thr', 80, 100, 90, 1, lambda v: str(int(v)), t=adv)
        self.slider('highlight_boost', 'Highlight Boost', 0, 3.0, 1.5, 0.1, lambda v: f'{v:.1f}', t=adv)
        self.slider('shadow_point', 'Shadow Point', 0, 30, 15, 1, lambda v: str(int(v)), t=adv)

        self.sub('Morphology', t=adv)
        self.slider('palette_reduction', 'Merge Dist', 2, 14, 6.0, 0.5, lambda v: f'{v:.1f}', t=adv)
        self.slider('substrate_tolerance', 'Substrate Tol', 0, 5, 2.0, 0.5, lambda v: f'{v:.1f}', t=adv)
        self.check('ignore_transparent', 'Ignore Alpha', True, t=adv, h='Prevents transparent background from poisoning palette.')

        self.sub('Smoothing', t=adv)
        bilat_opts = [('off', 'Off'), ('auto', 'Auto'), ('light', 'Light'), ('medium', 'Medium')]
        self.combo('preprocessing', 'Bilateral', bilat_opts, 'off', t=adv, h='Edge-preserving noise reduction.')
        self.check('median_pass', 'Median Filter', False, t=adv, h='Morphological smoothing pass.')
        self.slider('detail_rescue', 'Detail Rescue', 0, 20, 0, 1, lambda v: str(int(v)), t=adv)

        return self.controls

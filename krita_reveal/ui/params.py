"""
params.py — Logic for gathering UI control values into engine parameters.
"""

from __future__ import annotations


class ParamsManager:
    def __init__(self, dock):
        self.dock = dock

    def collect_params(self):
        p = {k: c.value() for k, c in self.dock._controls.items()}
        res = int(self.dock._proxy_combo.currentData() or 1000)
        return {
            'colors': int(p['colors']), 
            'density': p['density'], 
            'speckle': int(p['speckle']), 
            'clamp': p['clamp'], 
            'vibrancy_boost': p['vibrancy_boost'], 
            'vibrancy_mode': p['vibrancy_mode'], 
            'l_weight': p['l_weight'], 
            'c_weight': p['c_weight'], 
            'black_bias': p['black_bias'], 
            'shadow_point': p['shadow_point'], 
            'palette_reduction': p['palette_reduction'], 
            'enable_palette_reduction': p['enable_palette_reduction'], 
            'enable_hue_gap': p['enable_hue_gap'], 
            'hue_lock_angle': p['hue_lock_angle'], 
            'preserve_white': p['preserve_white'], 
            'preserve_black': p['preserve_black'], 
            'preprocessing': p['preprocessing'], 
            'engine_type': p['engine_type'], 
            'color_mode': p['color_mode'], 
            'dither_type': p['dither_type'], 
            'distance_metric': p['distance_metric'], 
            'centroid_strategy': p['centroid_strategy'], 
            'split_mode': p['split_mode'], 
            'quantizer': p['quantizer'], 
            'neutral_sovereignty': p['neutral_sovereignty'], 
            'chroma_gate': p['chroma_gate'], 
            'highlight_threshold': p['highlight_threshold'], 
            'highlight_boost': p['highlight_boost'], 
            'median_pass': p['median_pass'], 
            'detail_rescue': p['detail_rescue'], 
            'proxy_resolution': res, 
            'substrate_mode': p['substrate_mode'], 
            'substrate_tolerance': p['substrate_tolerance'], 
            'ignore_transparent': p['ignore_transparent'], 
            'mesh_size': int(p['mesh_size']), 
            'trap_size': int(p['trap_size'])
        }

    def get_worker_options(self, params):
        aid = self.dock._archetype_combo.currentData() or '__auto__'
        opts = {'_archetype_id': aid}
        
        # If we are in auto-match mode, we only want to pass parameters if they
        # have been explicitly changed from the archetype defaults.
        # However, for simplicity and expected behavior during initial load,
        # if aid is __auto__, we skip passing the overrides entirely.
        if aid == '__auto__':
            opts.update({
                '_preprocessing_intensity': str(params['preprocessing']),
                'min_volume': params['density'],
                'speckle_rescue': int(params['speckle']),
                'shadow_clamp': params['clamp']
            })
            return opts

        floats = (
            'vibrancy_boost', 'l_weight', 'c_weight', 'black_bias', 'shadow_point', 
            'palette_reduction', 'hue_lock_angle', 'chroma_gate', 
            'highlight_threshold', 'highlight_boost', 'detail_rescue', 
            'substrate_tolerance'
        )
        for k in floats:
            if k in params:
                opts[k] = float(params[k])
                
        # Handle Neutral Sovereignty (UI name -> internal name)
        if 'neutral_sovereignty' in params:
            opts['neutral_sovereignty_threshold'] = float(params['neutral_sovereignty'])
            
        strings = (
            'vibrancy_mode', 'substrate_mode', 'engine_type', 'color_mode', 
            'dither_type', 'distance_metric', 'centroid_strategy', 'split_mode', 'quantizer'
        )
        for k in strings:
            if k in params:
                opts[k] = str(params[k])
            
        bools = (
            'enable_palette_reduction', 'preserve_white', 'preserve_black', 
            'median_pass', 'ignore_transparent'
        )
        for k in bools:
            if k in params:
                opts[k] = bool(params[k])
                
        # Handle Hue Gap Analysis (UI name -> internal name)
        if 'enable_hue_gap' in params:
            opts['enable_hue_gap_analysis'] = bool(params['enable_hue_gap'])
            
        ints = ('mesh_size', 'trap_size')
        for k in ints:
            if k in params:
                opts[k] = int(params[k])
            
        opts.update({
            '_preprocessing_intensity': str(params['preprocessing']),
            'min_volume': params['density'],
            'speckle_rescue': int(params['speckle']),
            'shadow_clamp': params['clamp']
        })
        return opts

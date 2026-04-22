import os
import datetime

_log_path = os.path.join(os.path.dirname(__file__), 'reveal.log')

def log(msg):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    try:
        with open(_log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {msg}\n")
    except:
        pass
    print(f"[Reveal] {msg}")

DEFAULTS = {
    'colors': 6,
    'density': 0.5,
    'speckle': 0,
    'clamp': 0,
    'vibrancy_boost': 1.4,
    'vibrancy_mode': 'moderate',
    'l_weight': 1.2,
    'c_weight': 2.0,
    'black_bias': 3.0,
    'shadow_point': 15,
    'palette_reduction': 6.0,
    'enable_palette_reduction': True,
    'enable_hue_gap': True,
    'hue_lock_angle': 20,
    'preserve_white': True,
    'preserve_black': True,
    'preprocessing': 'off',
    'engine_type': 'reveal-mk1.5',
    'color_mode': 'color',
    'dither_type': 'none',
    'distance_metric': 'cie76',
    'centroid_strategy': 'ROBUST_SALIENCY',
    'split_mode': 'median',
    'quantizer': 'wu',
    'neutral_sovereignty': 0,
    'chroma_gate': 1.0,
    'highlight_threshold': 90,
    'highlight_boost': 1.5,
    'median_pass': False,
    'detail_rescue': 0,
    'proxy_resolution': 800,
    'mesh_size': 230,
    'trap_size': 0,
    'substrate_mode': 'auto',
    'substrate_tolerance': 2.0,
    'ignore_transparent': True,
}

HUD_KEYS = {
    'colors', 'preserve_white', 'preserve_black',
    'density', 'speckle', 'clamp', 'dither_type'
}

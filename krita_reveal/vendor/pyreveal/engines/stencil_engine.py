"""
stencil_engine.py — High-precision 1D quantization for B/W and Grayscale modes.

Ensures strict luminance-only mapping, bypassing 3D color math to prevent
chromatic leakage and maintain tonal fidelity.
"""

from __future__ import annotations
import time
from ..color.encoding import lab_to_rgb_d50 as lab_to_rgb

class StencilEngine:
    @classmethod
    def posterize(cls, pixels: list[int], width: int, height: int, 
                  target_colors: int, options: dict) -> dict:
        """
        Quantize image to strict B/W or Grayscale levels.
        
        Returns result dict with palette, assignments, and metadata.
        """
        start_time = time.perf_counter()
        color_mode = options.get('color_mode', 'grayscale')
        
        # 1. Determine target levels
        if color_mode == 'bw':
            levels = 2
        else:
            levels = max(2, min(256, target_colors))
            
        # 2. Extract luminance and quantize
        # pyreveal engine16: L is 0-32768
        n_pixels = len(pixels) // 3
        assignments = bytearray(n_pixels)
        
        palette_lab = []
        if color_mode == 'bw':
            # Strict B/W: L=0 and L=100
            palette_lab = [{'L': 0.0, 'a': 0.0, 'b': 0.0}, {'L': 100.0, 'a': 0.0, 'b': 0.0}]
            threshold = 16384 # 50% of 32768
            for i in range(n_pixels):
                l_in = pixels[i * 3]
                assignments[i] = 1 if l_in >= threshold else 0
        else:
            # Grayscale: spread 'levels' evenly across 0-100 L*
            step = 32768 / (levels - 1) if levels > 1 else 32768
            for i in range(levels):
                l_val = round((i * 100.0) / (levels - 1))
                palette_lab.append({'L': float(l_val), 'a': 0.0, 'b': 0.0})
            
            # Fast 1D assignment pass
            for i in range(n_pixels):
                l_in = pixels[i * 3]
                # Round to nearest level
                idx = max(0, min(levels - 1, int((l_in + step/2) / step)))
                assignments[i] = idx
            
        # 3. Convert palette to RGB for UI
        palette_rgb = []
        for p in palette_lab:
            r, g, b = lab_to_rgb(p['L'], 0, 0)
            palette_rgb.append({'r': r, 'g': g, 'b': b})
            
        duration = time.perf_counter() - start_time
            
        return {
            'palette': palette_rgb,
            'palette_lab': palette_lab,
            'assignments': assignments,
            'metadata': {
                'engine': 'stencil',
                'levels': levels,
                'final_colors': levels,
                'color_mode': color_mode,
                'duration': round(duration, 3)
            }
        }

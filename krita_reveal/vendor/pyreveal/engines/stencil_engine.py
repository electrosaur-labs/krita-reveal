"""
stencil_engine.py — High-precision 1D quantization for B/W and Grayscale modes.

Ensures strict luminance-only mapping, bypassing 3D color math to prevent
chromatic leakage and maintain tonal fidelity.
"""

from __future__ import annotations
import math

class StencilEngine:
    @classmethod
    def posterize(cls, pixels: list[int], width: int, height: int, 
                  target_colors: int, options: dict) -> dict:
        """
        Quantize image to strict B/W or Grayscale levels.
        
        Returns result dict with palette, assignments, and metadata.
        """
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
        
        # Use simple linear quantization for the stencil
        # In Grayscale mode, we spread 'levels' evenly across 0-100 L*
        step = 32768 / (levels - 1) if levels > 1 else 32768
        
        palette_lab = []
        for i in range(levels):
            l_val = round((i * 32768) / (levels - 1)) / 327.68
            palette_lab.append({'L': l_val, 'a': 0.0, 'b': 0.0})
            
        # Fast 1D assignment pass
        for i in range(n_pixels):
            l_in = pixels[i * 3]
            idx = max(0, min(levels - 1, int((l_in + step/2) / step)))
            assignments[i] = idx
            
        # 3. Convert palette to RGB for UI
        from ..color.encoding import lab_to_rgb
        palette_rgb = []
        for p in palette_lab:
            r, g, b = lab_to_rgb(p['L'], 0, 0)
            palette_rgb.append({'r': r, 'g': g, 'b': b})
            
        return {
            'palette': palette_rgb,
            'palette_lab': palette_lab,
            'assignments': assignments,
            'metadata': {
                'engine': 'stencil',
                'levels': levels,
                'final_colors': levels,
                'color_mode': color_mode
            }
        }

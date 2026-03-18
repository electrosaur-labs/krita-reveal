"""
pipeline.py — Krita pixel I/O + pyreveal integration.

Handles the encoding mismatch between Krita's Lab16 and pyreveal's Lab16:
  Krita:    L/a/b each 0-65535  (uint16 LE, 3 channels × 2 bytes per pixel)
  pyreveal: L/a/b each 0-32768  (neutral a/b = 16384)

Conversion: pyreveal_val = krita_val >> 1  (integer divide by 2)
"""

from __future__ import annotations

import struct


def krita_pixels_to_pyreveal(raw: bytes, pixel_count: int) -> list:
    """Decode Krita Lab16 raw bytes → flat pyreveal 16-bit Lab list."""
    result = [0] * (pixel_count * 3)
    for i in range(pixel_count):
        off = i * 6  # 3 channels × 2 bytes (uint16 LE)
        L, a, b = struct.unpack_from('<HHH', raw, off)
        j = i * 3
        result[j]     = L >> 1
        result[j + 1] = a >> 1
        result[j + 2] = b >> 1
    return result


def read_document_pixels(doc, node=None):
    """Read Lab16 pixels from the active (merged) document.

    Returns (pixels, width, height) where pixels is a pyreveal-encoded list.
    Falls back to active node if no merged pixel source is available.
    """
    width  = doc.width()
    height = doc.height()

    if node is None:
        node = doc.projection(0, 0, width, height)
        raw = bytes(node)
        pixel_count = width * height
        return krita_pixels_to_pyreveal(raw, pixel_count), width, height

    raw = bytes(node.pixelData(0, 0, width, height))
    pixel_count = width * height
    return krita_pixels_to_pyreveal(raw, pixel_count), width, height


def downsample_pixels(pixels: list, width: int, height: int, max_dim: int = 800):
    """Nearest-neighbour downsample to fit within max_dim × max_dim.

    Returns (downsampled_pixels, new_width, new_height, scale_x, scale_y).
    If image already fits, returns original data unchanged with scale 1.0.
    """
    scale = min(1.0, max_dim / max(width, height))
    if scale >= 1.0:
        return pixels, width, height, 1.0, 1.0

    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))

    result = []
    for y in range(new_h):
        src_y = int(y / scale)
        for x in range(new_w):
            src_x = int(x / scale)
            off = (src_y * width + src_x) * 3
            result.extend([pixels[off], pixels[off + 1], pixels[off + 2]])

    return result, new_w, new_h, width / new_w, height / new_h


def run_separation(pixels: list, width: int, height: int, target_colors: int = 6) -> dict:
    """Run the full pyreveal pipeline.

    Returns the posterize_image() result dict:
      palette, palette_lab, assignments, lab_pixels, metadata, ...
    """
    import pyreveal

    dna    = pyreveal.analyze_image(pixels, width, height, {'bit_depth': 16})
    config = pyreveal.generate_configuration(dna)

    preprocessing = config.get('preprocessing', {})
    if preprocessing.get('enabled'):
        pyreveal.preprocess_image(pixels, width, height, preprocessing)

    return pyreveal.posterize_image(
        pixels, width, height, target_colors,
        {'substrate_mode': 'none'},
    )

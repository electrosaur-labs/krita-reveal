"""
pipeline.py — Krita pixel I/O + pyreveal integration.

Krita Lab16 encoding:   L/a/b each 0-65535 (uint16 LE, 3 channels × 2 bytes/pixel)
pyreveal Lab16 encoding: L/a/b each 0-32768 (neutral a/b = 16384)

Conversion: pyreveal_val = krita_val >> 1
"""

from __future__ import annotations

import struct


def krita_pixels_to_pyreveal(raw: bytes, pixel_count: int) -> list:
    """Decode Krita LABA U16 raw bytes → flat pyreveal 16-bit Lab list.

    Krita LABA U16: 4 channels × 2 bytes = 8 bytes/pixel (L, a, b, alpha).
    pyreveal engine16: 3 values/pixel, range 0-32768 = krita_val >> 1.
    """
    result = [0] * (pixel_count * 3)
    for i in range(pixel_count):
        off = i * 8                              # 4 channels × 2 bytes
        L, a, b = struct.unpack_from('<HHH', raw, off)   # skip alpha
        j = i * 3
        result[j]     = L >> 1
        result[j + 1] = a >> 1
        result[j + 2] = b >> 1
    return result


def read_document_pixels(doc, node=None):
    """Read Lab16 pixels from a Krita document node.

    Returns (pixels, width, height) — pixels in pyreveal encoding.
    """
    width  = doc.width()
    height = doc.height()

    if node is None:
        # projectionPixelData returns merged Lab16 bytes in the document's colorspace
        raw = bytes(doc.rootNode().projectionPixelData(0, 0, width, height))
    else:
        raw = bytes(node.pixelData(0, 0, width, height))

    return krita_pixels_to_pyreveal(raw, width * height), width, height


def downsample_pixels(pixels: list, width: int, height: int, max_dim: int = 800):
    """Nearest-neighbour downsample to fit within max_dim × max_dim.

    Returns (pixels, new_width, new_height).
    """
    scale = min(1.0, max_dim / max(width, height))
    if scale >= 1.0:
        return pixels, width, height

    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))

    result = []
    for y in range(new_h):
        src_y = int(y / scale)
        for x in range(new_w):
            src_x = int(x / scale)
            off = (src_y * width + src_x) * 3
            result.extend([pixels[off], pixels[off + 1], pixels[off + 2]])

    return result, new_w, new_h


def run_separation(pixels: list, width: int, height: int, target_colors: int = 6) -> dict:
    """Run the full pyreveal pipeline and return posterize_image() result."""
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

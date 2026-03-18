"""
layer_builder.py — Create fill+mask layers in a Krita document.

For each palette colour, builds a group layer containing:
  - A paint layer filled with the solid RGB colour
  - A transparency mask derived from the binary separation mask

Layer naming: hex colour code, e.g. "#A34F2B"
Group is inserted above the current active layer.
"""

from __future__ import annotations

import struct

from krita import Krita, InfoObject


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f'#{r:02X}{g:02X}{b:02X}'


def _make_fill_layer(doc, name: str, r: int, g: int, b: int, width: int, height: int):
    """Create a paint layer filled with a flat RGB colour."""
    layer = doc.createNode(name, 'paintlayer')
    # Fill with solid colour: BGRA byte order (Krita's default for 8-bit sRGB)
    pixel = bytes([b, g, r, 255])
    layer.setPixelData(pixel * (width * height), 0, 0, width, height)
    return layer


def _make_mask_layer(doc, name: str, mask_bytes: bytearray, width: int, height: int):
    """Create a transparency mask layer from a binary mask (255=opaque, 0=transparent)."""
    mask = doc.createNode(name, 'transparencymask')
    mask.setPixelData(bytes(mask_bytes), 0, 0, width, height)
    return mask


def build_separation_layers(doc, result: dict) -> int:
    """Build one group layer per palette colour in the active document.

    result: dict from pyreveal.posterize_image()
    Returns number of colour layers created.
    """
    from pyreveal import generate_mask

    palette     = result['palette']        # [{r, g, b}]
    assignments = result['assignments']    # bytearray, len = w*h
    width       = doc.width()
    height      = doc.height()

    root = doc.rootNode()

    # Create a top-level group for all separation layers
    group = doc.createNode('Reveal Separation', 'grouplayer')
    root.addChildNode(group, None)

    for i, color in enumerate(palette):
        r, g, b  = color['r'], color['g'], color['b']
        hex_name = _rgb_to_hex(r, g, b)

        mask_bytes = generate_mask(assignments, i, width, height)

        color_group = doc.createNode(hex_name, 'grouplayer')

        fill  = _make_fill_layer(doc, 'fill',  r, g, b, width, height)
        mask  = _make_mask_layer(doc, 'mask',  mask_bytes, width, height)

        color_group.addChildNode(fill, None)
        fill.addChildNode(mask, None)   # mask is child of fill layer

        group.addChildNode(color_group, None)

    doc.refreshProjection()
    return len(palette)

"""
layer_builder.py — Create fill+mask layers in a Krita document.

For each palette colour, builds a group layer containing:
  - A paint layer filled with the solid Lab colour
  - A transparency mask derived from the binary separation mask

Krita Lab16 (LABA U16) encoding — same as PSD ICC 16-bit:
  L:     0-65535  (0=black, 65535=L*100)
  a, b:  0-65535  (32768=neutral/0)
  alpha: 0-65535  (65535=fully opaque)

Conversion from perceptual Lab (L 0-100, a/b -128..+127):
  L_k = round(L / 100 * 65535)
  a_k = round(a * 256 + 32768)
  b_k = round(b * 256 + 32768)
"""

from __future__ import annotations

import struct

from krita import Selection


def _upsample_assignments(assignments, src_w, src_h, dst_w, dst_h):
    """Nearest-neighbour upsample a flat assignment bytearray to (dst_w, dst_h)."""
    if src_w == dst_w and src_h == dst_h:
        return assignments
    x_scale = src_w / dst_w
    y_scale = src_h / dst_h
    # Precompute lookup tables — avoids repeated float math in the inner loop
    x_map = [min(int(x * x_scale), src_w - 1) for x in range(dst_w)]
    y_map = [min(int(y * y_scale), src_h - 1) * src_w for y in range(dst_h)]
    out = bytearray(dst_w * dst_h)
    pos = 0
    for sy_off in y_map:
        for sx in x_map:
            out[pos] = assignments[sy_off + sx]
            pos += 1
    return out


def _lab_to_krita16(L: float, a: float, b: float) -> bytes:
    """Encode one perceptual Lab colour as 8 bytes of Krita Lab16 (LABA U16 LE)."""
    Lk = max(0, min(65535, round(L / 100 * 65535)))
    ak = max(0, min(65535, round(a * 256 + 32768)))
    bk = max(0, min(65535, round(b * 256 + 32768)))
    return struct.pack('<HHHH', Lk, ak, bk, 65535)  # alpha = opaque


def build_separation_layers(doc, result: dict) -> int:
    """Build one group layer per palette colour.

    result: dict from pyreveal.posterize_image()
    Returns number of colour layers created.
    """
    from pyreveal import generate_mask

    palette_rgb = result['palette']
    palette_lab = result['palette_lab']
    proxy_w     = result.get('_proxy_w', doc.width())
    proxy_h     = result.get('_proxy_h', doc.height())
    width       = doc.width()
    height      = doc.height()

    assignments = _upsample_assignments(
        result['assignments'], proxy_w, proxy_h, width, height
    )

    root  = doc.rootNode()
    group = doc.createNode('Reveal Separation', 'grouplayer')
    root.addChildNode(group, None)

    pixel_count = width * height

    for i, (rgb, lab) in enumerate(zip(palette_rgb, palette_lab)):
        r, g, b = rgb['r'], rgb['g'], rgb['b']
        hex_name = f'#{r:02X}{g:02X}{b:02X}'

        color_group = doc.createGroupLayer(hex_name)
        fill  = doc.createNode('fill', 'paintlayer')
        tmask = doc.createTransparencyMask('mask')

        # Top-down attachment
        group.addChildNode(color_group, None)
        color_group.addChildNode(fill, None)
        fill.addChildNode(tmask, None)

        # Fill: solid Lab colour
        pixel = _lab_to_krita16(lab['L'], lab['a'], lab['b'])
        fill.setPixelData(pixel * pixel_count, 0, 0, width, height)

        # Mask via Selection API (TransparencyMask uses setSelection, not setPixelData)
        mask_bytes = generate_mask(assignments, i, width, height)
        sel = Selection()
        sel.setPixelData(bytes(mask_bytes), 0, 0, width, height)
        tmask.setSelection(sel)

    doc.refreshProjection()
    return len(palette_rgb)

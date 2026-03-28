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
    from pyreveal import generate_mask, despeckle_mask
    from pyreveal.engines.separation import SeparationEngine

    palette_rgb       = result['palette']
    palette_lab       = result['palette_lab']
    proxy_w           = result.get('_proxy_w', doc.width())
    proxy_h           = result.get('_proxy_h', doc.height())
    width             = doc.width()
    height            = doc.height()
    matched           = result.get('_matched_archetype') or {}
    speckle_threshold = matched.get('speckle', 5)

    root  = doc.rootNode()
    group = doc.createNode('Reveal Separation', 'grouplayer')
    root.addChildNode(group, None)

    pixel_count = width * height

    # If proxy resolution differs from document resolution, re-run the separation
    # pass at full document resolution.  Upsampling proxy assignments via
    # nearest-neighbour produces heavily aliased edges; a proper full-res
    # separation pass with dithering matches JS ProductionWorker behaviour.
    if proxy_w != width or proxy_h != height:
        from .pipeline import read_document_pixels
        full_pixels, _, _ = read_document_pixels(doc)
        assignments = SeparationEngine.map_pixels_to_palette(
            full_pixels,
            palette_lab,
            width,
            height,
            {
                'dither_type':     matched.get('dither_type', 'none'),
                'distance_metric': matched.get('distance_metric', 'cie76'),
            },
        )
    else:
        assignments = result['assignments']

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

        # Generate and despeckle mask at full document resolution
        mask_bytes = generate_mask(assignments, i, width, height)
        if speckle_threshold > 0:
            despeckle_mask(mask_bytes, width, height, speckle_threshold)

        sel = Selection()
        sel.setPixelData(bytes(mask_bytes), 0, 0, width, height)
        tmask.setSelection(sel)

    doc.refreshProjection()

    return len(palette_rgb)

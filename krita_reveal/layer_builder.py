"""
layer_builder.py — Create fill+mask layers in a Krita document.

For each palette colour, builds a group layer containing:
  - A generator fill layer with the Lab16 colour (ManagedColor via toXML)
  - A transparency mask derived from the binary separation mask

ManagedColor Lab16 normalisation (setComponents takes [0,1]):
  L_norm = L / 100
  a_norm = (a * 256 + 32768) / 65535
  b_norm = (b * 256 + 32768) / 65535
  alpha  = 1.0 (opaque)
"""

from __future__ import annotations

from krita import ManagedColor, InfoObject, Selection


def _make_lab_fill_layer(doc, name: str, lab_profile: str, L: float, a: float, b: float):
    """Create a generator fill layer with a native Lab16 colour.

    Uses ManagedColor.toXML() so the colour is stored as Lab16 without
    any sRGB conversion — equivalent to a PS solid fill layer.
    """
    l_n = L / 100.0
    a_n = (a * 256 + 32768) / 65535.0
    b_n = (b * 256 + 32768) / 65535.0

    c = ManagedColor("LABA", "U16", lab_profile)
    c.setComponents([l_n, a_n, b_n, 1.0])

    info = InfoObject()
    info.setProperty("color", c.toXML())

    # Selection covering the full canvas (required by createFillLayer API)
    full_sel = Selection()
    full_sel.select(0, 0, doc.width(), doc.height(), 255)

    layer = doc.createFillLayer(name, "color", info, full_sel)
    # setGenerator must be called after attachment to force the colour to apply
    layer.setGenerator("color", info)
    return layer


def build_separation_layers(doc, result: dict, on_progress=None) -> int:
    """Build one group layer per palette colour.

    result:      dict from pyreveal.posterize_image()
    on_progress: optional callable(message: str) called during separation
                 and between layer creations so the UI can update.
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
    lab_profile       = doc.colorProfile()
    total             = len(palette_rgb)

    # ── Full-resolution separation ────────────────────────────────────────
    # Run before creating any layers so no phantom group entry appears in
    # Krita's undo history during the slow separation pass.
    if proxy_w != width or proxy_h != height:
        if on_progress:
            on_progress(f'Separating at full resolution ({width}×{height})…')
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

    # ── Layer creation ────────────────────────────────────────────────────
    # Create the group and add layers one by one, refreshing after each so
    # the user sees the image build up progressively.
    root  = doc.rootNode()
    group = doc.createNode('Reveal Separation', 'grouplayer')
    root.addChildNode(group, None)

    for i, (rgb, lab) in enumerate(zip(palette_rgb, palette_lab)):
        r, g, b = rgb['r'], rgb['g'], rgb['b']
        hex_name = f'#{r:02X}{g:02X}{b:02X}'

        if on_progress:
            on_progress(f'Building layer {i + 1}/{total}  {hex_name}')

        color_group = doc.createGroupLayer(hex_name)
        group.addChildNode(color_group, None)

        # Fill: generator fill layer with native Lab16 colour
        fill = _make_lab_fill_layer(doc, 'fill', lab_profile, lab['L'], lab['a'], lab['b'])
        color_group.addChildNode(fill, None)

        # Mask
        mask_bytes = generate_mask(assignments, i, width, height)
        if speckle_threshold > 0:
            despeckle_mask(mask_bytes, width, height, speckle_threshold)

        tmask = doc.createTransparencyMask('mask')
        fill.addChildNode(tmask, None)
        sel = Selection()
        sel.setPixelData(bytes(mask_bytes), 0, 0, width, height)
        tmask.setSelection(sel)

        # Refresh after each layer so the user sees progressive build-up
        doc.refreshProjection()
        if on_progress:
            on_progress(f'Building layer {i + 1}/{total}  {hex_name}')

    return total

"""
pyreveal — Pure Python color separation engine for screen printing.

Reduces full-color images to 3-12 spot colors via CIELAB quantization.
Zero runtime dependencies.

Pipeline (mirrors reveal-core JS index.js):

  1. analyze_image()          — DNA analysis (7D vector + 12-sector hue)
  2. generate_configuration() — DNA → engine config (archetype matching)
  3. preprocess_image()       — optional bilateral filter (noise reduction)
  4. posterize_image()        — Lab median cut → colour palette + assignments
  5. separate_image()         — pixel → palette index mapping (with dithering)
  6. generate_mask()          — binary mask for one palette colour channel

Colour conversions:
  rgb_to_lab() / lab_to_rgb()
"""

from __future__ import annotations

__version__ = "0.1.0"

from .analysis.dna import DNAGenerator
from .analysis.archetype_mapper import ArchetypeMapper
from .analysis.parameter_generator import ParameterGenerator
from .engines.posterization_engine import posterize as _posterize, palette_to_hex
from .engines.separation import SeparationEngine
from .color.encoding import rgb_to_lab, lab_to_rgb
from .preprocessing.bilateral_filter import (
    apply_bilateral_filter_lab,
    calculate_entropy_score_lab,
    create_preprocessing_config,
)


# ── Tool 1: Analyze Image ────────────────────────────────────────────────────

def analyze_image(lab_pixels, width: int, height: int, options: dict | None = None) -> dict:
    """Compute DNA from Lab pixel data.

    lab_pixels: flat sequence of Lab values (L, a, b, L, a, b, ...).
                Bit depth set via options['bit_depth'] (8, 16, or 'perceptual').
    Returns DNA v2.0 dict: {version, global, sectors, dominant_sector, metadata}.
    """
    gen = DNAGenerator()
    return gen.generate(lab_pixels, width, height, options)


# ── Tool 1.5: Generate Configuration ─────────────────────────────────────────

def generate_configuration(dna: dict, options: dict | None = None) -> dict:
    """Map DNA to a complete posterization configuration.

    Runs archetype matching and parameter generation.
    Returns config dict with engine_type, target_colors, snap_threshold, etc.
    """
    return ParameterGenerator.generate(dna, options)


# ── Tool 2: Preprocess Image ─────────────────────────────────────────────────

def preprocess_image(lab_data: list, width: int, height: int, config: dict) -> dict:
    """Apply bilateral filter to 16-bit Lab data (in-place, optional step).

    config: preprocessing config from generate_configuration()['preprocessing']
            or create_preprocessing_config().
    Returns {'processed': bool, 'intensity': str, 'reason': str}.
    """
    if not config or not config.get('enabled'):
        return {'processed': False, 'reason': 'Preprocessing disabled'}

    apply_bilateral_filter_lab(
        lab_data,
        width,
        height,
        config.get('radius', 4),
        config.get('sigmaR', 3000),
    )
    return {
        'processed': True,
        'intensity': config.get('intensity', 'auto'),
        'reason': config.get('reason', ''),
    }


# ── Tool 3: Posterize Image ───────────────────────────────────────────────────

def posterize_image(
    lab_pixels,
    width: int,
    height: int,
    color_count: int,
    parameters: dict | None = None,
) -> dict:
    """Reduce image to a limited colour palette.

    lab_pixels: flat 16-bit Lab engine values (3 per pixel) or RGBA uint8
                when parameters['format']='rgb'.
    color_count: target palette size (1-20).
    parameters:  posterization options (engine_type, snap_threshold, etc.)

    Returns:
      palette        list of {r, g, b} dicts
      palette_lab    list of {L, a, b} dicts
      assignments    bytearray of palette indices (255 = transparent)
      lab_pixels     flat [L, a, b, ...] perceptual float list
      substrate_lab  {L, a, b} or None
      substrate_index int or None
      metadata       {target_colors, final_colors, snap_threshold, duration}
    """
    return _posterize(lab_pixels, width, height, color_count, parameters or {})


# ── Tool 4: Separate Image ────────────────────────────────────────────────────

def separate_image(
    lab_pixels,
    palette: list,
    width: int,
    height: int,
    parameters: dict | None = None,
) -> dict:
    """Map each pixel to its nearest palette colour.

    lab_pixels: 8-bit Lab bytes (3 per pixel) — legacy byte-encoded path.
    palette:    list of {L, a, b} dicts in perceptual Lab.
    parameters: {'dither_type': 'none'|'floyd-steinberg'|'bayer'|'atkinson'|'stucki',
                 'distance_metric': 'cie76'|'cie94', ...}

    Returns:
      color_indices  bytearray of palette indices
      metadata       {total_pixels, palette_size, dither_type, distance_metric}
    """
    params = parameters or {}
    dither_type = params.get('dither_type', 'none')
    distance_metric = params.get('distance_metric', 'cie76')

    color_indices = SeparationEngine.map_pixels_to_palette(
        lab_pixels, palette, width, height,
        {'dither_type': dither_type, 'distance_metric': distance_metric},
    )

    return {
        'color_indices': color_indices,
        'metadata': {
            'total_pixels': len(color_indices),
            'palette_size': len(palette),
            'dither_type': dither_type,
            'distance_metric': distance_metric,
        },
    }


# ── Tool 5: Generate Mask ─────────────────────────────────────────────────────

def generate_mask(color_indices, color_index: int, width: int, height: int) -> bytearray:
    """Create a binary mask for one palette colour channel.

    Returns bytearray of length width×height: 255 where pixel == color_index, 0 elsewhere.
    """
    return SeparationEngine.generate_layer_mask(color_indices, color_index, width, height)


# ── Colour utilities ──────────────────────────────────────────────────────────

def calculate_entropy(lab_data, width: int, height: int, sample_rate: int = 4) -> float:
    """Measure local variance in 16-bit Lab data to detect noise (0-100)."""
    return calculate_entropy_score_lab(lab_data, width, height, sample_rate)


# ── Tool 6: Despeckle Mask ────────────────────────────────────────────────────

def despeckle_mask(mask: bytearray, width: int, height: int, threshold: int = 5) -> dict:
    """Remove isolated pixel clusters smaller than threshold from a layer mask.

    Modifies mask in-place (same bytearray returned by generate_mask).
    Uses 8-connected DFS to find connected components; clusters with fewer
    than threshold pixels are zeroed out.

    threshold: minimum cluster size to keep (default 5, matches JS default).
    Returns {'clusters_removed': int, 'pixels_removed': int}.
    """
    return SeparationEngine.despeckle_mask(mask, width, height, threshold)

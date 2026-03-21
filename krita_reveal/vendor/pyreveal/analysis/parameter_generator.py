"""
ParameterGenerator v4.0 — DNA v2.0 data-driven archetype system.

Maps Image DNA → nearest archetype → RevealConfig.
Archetype JSON field names are camelCase (JS heritage); config output is snake_case.
"""

from __future__ import annotations

from .archetype_loader import ArchetypeLoader

# Dither type normalization: JSON / UI names → canonical engine names
_DITHER_NORMALIZE = {
    'floydsteinberg':   'floyd-steinberg',
    'floyd-steinberg':  'floyd-steinberg',
    'atkinson':         'atkinson',
    'stucki':           'stucki',
    'bayer':            'bayer',
    'none':             'none',
}


class ParameterGenerator:
    """Maps DNA analysis to a complete RevealConfig dict."""

    CONFIG_CATEGORIES = {
        'STRUCTURAL': [
            'target_colors', 'engine_type', 'centroid_strategy', 'distance_metric',
            'l_weight', 'c_weight', 'black_bias',
            'vibrancy_mode', 'vibrancy_boost',
            'highlight_threshold', 'highlight_boost',
            'palette_reduction', 'enable_palette_reduction',
            'substrate_mode', 'substrate_tolerance',
            'enable_hue_gap_analysis', 'hue_lock_angle',
            'shadow_point', 'color_mode',
            'preserve_white', 'preserve_black', 'ignore_transparent',
            'neutral_sovereignty_threshold',
            'chroma_gate', 'preprocessing_intensity',
            'refinement_passes', 'split_mode', 'quantizer',
            'detail_rescue', 'median_pass',
        ],
        'MECHANICAL': ['min_volume', 'speckle_rescue', 'shadow_clamp'],
        'PRODUCTION': ['trap_size', 'mesh_size', 'dither_type'],
    }

    KNOB_DEFAULTS = {
        'MECHANICAL': {'min_volume': 0, 'speckle_rescue': 0, 'shadow_clamp': 0},
        'PRODUCTION': {'trap_size': 0, 'mesh_size': 230},
    }

    @classmethod
    def generate(cls, dna: dict, options: dict | None = None) -> dict:
        """Generate a RevealConfig from DNA analysis.

        options:
          manual_archetype_id: bypass DNA matching, use this archetype
          preprocessing_intensity: 'off'|'auto'|'light'|'heavy'
        """
        if options is None:
            options = {}

        archetype = ArchetypeLoader.match_archetype(
            dna, options.get('manual_archetype_id')
        )

        # Deep-copy params so we don't mutate the cached archetype
        import copy
        params = copy.deepcopy(archetype.get('parameters', {}))

        # DNA v2.0 conditional overrides (sector-based adjustments)
        if dna.get('version') == '2.0' and dna.get('sectors'):
            cls._apply_dna_v2_overrides(params, dna, archetype)

        # Chroma gate: boost cWeight for high-chroma images
        cls._apply_chroma_gate(params, dna)

        bit_depth = (dna.get('bit_depth') or
                     (dna.get('metadata') or {}).get('bit_depth') or 8)

        # Preprocessing stub — BilateralFilter not yet ported
        preprocessing_intensity = (options.get('preprocessing_intensity') or
                                   params.get('preprocessingIntensity', 'auto'))
        if preprocessing_intensity == 'none':
            preprocessing_intensity = 'off'
        preprocessing = {'enabled': False}  # TODO: port BilateralFilter

        # Normalize dither type
        raw_dither     = (params.get('ditherType', 'atkinson') or 'atkinson').lower()
        dither_type    = _DITHER_NORMALIZE.get(raw_dither, raw_dither)

        # Adaptive target color count
        target_colors = (cls._compute_adaptive_color_count(dna, archetype) or
                         params.get('targetColorsSlider') or
                         params.get('targetColors') or 8)

        engine_type = 'distilled' if archetype.get('engine') == 'distilled' else 'reveal-mk1.5'

        config = {
            # Identity
            'id':          archetype['id'],
            'name':        archetype['name'],
            'engine_type': engine_type,

            # Core
            'target_colors':   target_colors,
            'dither_type':     dither_type,
            'distance_metric': params.get('distanceMetric', 'cie76'),

            # Saliency weights
            'l_weight':   params.get('lWeight',   1.2),
            'c_weight':   params.get('cWeight',   2.0),
            'b_weight':   params.get('bWeight',   1.0),
            'black_bias': params.get('blackBias', 3.0),

            # Vibrancy
            'vibrancy_mode':  params.get('vibrancyMode',  'moderate'),
            'vibrancy_boost': params.get('vibrancyBoost', 1.4),

            # Highlights
            'highlight_threshold': params.get('highlightThreshold', 90),
            'highlight_boost':     params.get('highlightBoost',     1.5),

            # Palette merging
            'palette_reduction':        params.get('paletteReduction',       6.0),
            'enable_palette_reduction': params.get('enablePaletteReduction', True),

            # Substrate
            'substrate_mode':      params.get('substrateMode',      'auto'),
            'substrate_tolerance': params.get('substrateTolerance', 2.0),

            # Hue analysis
            'enable_hue_gap_analysis': params.get('enableHueGapAnalysis', True),
            'hue_lock_angle':          params.get('hueLockAngle',         20),

            # Tonal
            'shadow_point': params.get('shadowPoint', 15),

            # Color mode
            'color_mode':          params.get('colorMode',          'color'),
            'preserve_white':      params.get('preserveWhite',      True),
            'preserve_black':      params.get('preserveBlack',      True),
            'ignore_transparent':  params.get('ignoreTransparent',  True),

            # Quantization strategy
            'centroid_strategy':       params.get('centroidStrategy',      'SALIENCY'),
            'split_mode':              params.get('splitMode',             'median'),
            'quantizer':               params.get('quantizer',             'wu'),
            'refinement_passes':       params.get('refinementPasses',      1),
            'chroma_axis_weight':      params.get('chromaAxisWeight',      0),
            'neutral_isolation_threshold': params.get('neutralIsolationThreshold', 0),
            'warm_a_boost':            params.get('warmABoost',            1.0),

            # Neutral handling
            'neutral_centroid_clamp_threshold': 0.5,  # fixed safety floor
            'neutral_sovereignty_threshold':    params.get('neutralSovereigntyThreshold', 0),

            # PeakFinder
            'peak_finder_max_peaks':           params.get('peakFinderMaxPeaks', 1),
            'peak_finder_blacklisted_sectors': params.get('peakFinderBlacklistedSectors', [3, 4]),

            # Mechanical knobs
            'shadow_clamp':  params.get('shadowClamp',  0),
            'chroma_gate':   params.get('chromaGate',   1.0),
            'detail_rescue': params.get('detailRescue', 0),
            'speckle_rescue': params.get('speckleRescue', 0),
            'median_pass':   params.get('medianPass',   False),
            'min_volume':    params.get('minVolume',    0),

            # Shadow chroma gate
            'shadow_chroma_gate_l': params.get('shadowChromaGateL', 0),

            # Preprocessing
            'preprocessing_intensity': preprocessing_intensity,
            'preprocessing':           preprocessing,

            # Legacy
            'range_clamp': [dna.get('min_l', 0), dna.get('max_l', 100)],

            # Metadata
            'meta': {
                'archetype':     archetype['name'],
                'archetype_id':  archetype['id'],
                'peak_chroma':   dna.get('max_c') or dna.get('c') or 0,
                'is_photo':      any(tag in archetype['name'] for tag in ('Photo', 'Cinematic')),
                'is_graphic':    any(tag in archetype['name'] for tag in ('Graphic', 'Neon')),
                'is_archive':    bit_depth == 16,
                'bit_depth':     bit_depth,
                'match_version': archetype.get('match_version', '1.0'),
                'match_distance': archetype.get('match_distance', 0),
                'match_score':   archetype.get('match_score'),
                'match_breakdown': archetype.get('match_breakdown'),
                'match_ranking': archetype.get('match_ranking'),
            },
        }

        return config

    @classmethod
    def to_engine_options(cls, config: dict, overrides: dict | None = None) -> dict:
        """Convert a RevealConfig to engine-ready options dict."""
        if overrides is None:
            overrides = {}
        opts = {
            'target_colors':        config['target_colors'],
            'format':               'lab',
            'engine_type':          config.get('engine_type', 'reveal'),
            'centroid_strategy':    config.get('centroid_strategy', 'SALIENCY'),
            'distance_metric':      config.get('distance_metric', 'cie76'),
            'dither_type':          config.get('dither_type', 'atkinson'),
            'l_weight':             config.get('l_weight'),
            'c_weight':             config.get('c_weight'),
            'b_weight':             config.get('b_weight'),
            'black_bias':           config.get('black_bias'),
            'vibrancy_mode':        config.get('vibrancy_mode'),
            'vibrancy_boost':       config.get('vibrancy_boost'),
            'highlight_threshold':  config.get('highlight_threshold'),
            'highlight_boost':      config.get('highlight_boost'),
            'enable_palette_reduction': config.get('enable_palette_reduction'),
            'palette_reduction':    config.get('palette_reduction'),
            'substrate_mode':       config.get('substrate_mode'),
            'substrate_tolerance':  config.get('substrate_tolerance'),
            'enable_hue_gap_analysis': config.get('enable_hue_gap_analysis'),
            'hue_lock_angle':       config.get('hue_lock_angle'),
            'shadow_point':         config.get('shadow_point'),
            'color_mode':           config.get('color_mode', 'color'),
            'grayscale_only':       config.get('color_mode', 'color') in ('bw', 'grayscale'),
            'preserve_white':       config.get('preserve_white'),
            'preserve_black':       config.get('preserve_black'),
            'ignore_transparent':   config.get('ignore_transparent'),
            'shadow_clamp':         config.get('shadow_clamp'),
            'chroma_gate':          config.get('chroma_gate'),
            'detail_rescue':        config.get('detail_rescue'),
            'speckle_rescue':       config.get('speckle_rescue'),
            'median_pass':          config.get('median_pass'),
            'min_volume':           config.get('min_volume'),
            'shadow_chroma_gate_l': config.get('shadow_chroma_gate_l'),
            'neutral_centroid_clamp_threshold': config.get('neutral_centroid_clamp_threshold'),
            'neutral_sovereignty_threshold':    config.get('neutral_sovereignty_threshold'),
            'chroma_axis_weight':   config.get('chroma_axis_weight'),
            'neutral_isolation_threshold': config.get('neutral_isolation_threshold'),
            'warm_a_boost':         config.get('warm_a_boost'),
            'peak_finder_max_peaks': config.get('peak_finder_max_peaks'),
            'peak_finder_blacklisted_sectors': config.get('peak_finder_blacklisted_sectors'),
            'refinement_passes':    config.get('refinement_passes'),
            'split_mode':           config.get('split_mode', 'median'),
            'quantizer':            config.get('quantizer', 'wu'),
            **overrides,
        }
        if config.get('mesh_size') is not None:
            opts['mesh_count'] = config['mesh_size']
        return opts

    @classmethod
    def extract_mechanical_knobs(cls, source: dict) -> dict:
        """Extract mechanical + production knobs from a config dict."""
        return {
            'min_volume':    source.get('min_volume', 0),
            'speckle_rescue': source.get('speckle_rescue', 0),
            'shadow_clamp':  source.get('shadow_clamp', 0),
            'trap_size':     source.get('trap_size', 0),
        }

    @classmethod
    def get_dominant_sector_trait(cls, sector: str) -> dict | None:
        """Return surgical parameter overrides for a dominant hue sector."""
        TRAITS = {
            'red':        {'lWeight': 1.0, 'cWeight': 4.5},
            'orange':     {'lWeight': 1.0, 'paletteReduction': 6.5},
            'yellow':     {'hueLockAngle': 30, 'substrateTolerance': 1.2, 'paletteReduction': 6.0},
            'chartreuse': {'enableHueGapAnalysis': True, 'paletteReduction': 6.0},
            'green':      {'lWeight': 1.4, 'blackBias': 7.0},
            'cyan':       {'neutralSovereigntyThreshold': 0, 'cWeight': 3.5},
            'azure':      {'paletteReduction': 6.0, 'cWeight': 3.5},
            'blue':       {'neutralSovereigntyThreshold': 0, 'enableHueGapAnalysis': True},
            'purple':     {'blackBias': 8.0, 'lWeight': 1.2},
            'magenta':    {'paletteReduction': 6.0},
            'pink':       {'lWeight': 1.6, 'paletteReduction': 6.0},
            'rose':       {'lWeight': 1.1, 'cWeight': 4.0},
        }
        overrides = TRAITS.get(sector)
        if overrides is None:
            return None
        return {'overrides': overrides}

    # -------------------------------------------------------------------------
    # Private helpers

    @classmethod
    def _compute_adaptive_color_count(cls, dna: dict, archetype: dict) -> int | None:
        """Compute adaptive target color count from DNA sectors.

        Returns None if DNA lacks sector data.
        """
        if 'sectors' not in dna or 'global' not in dna:
            return None

        SECTOR_MIN = 0.03
        NEUTRAL_THRESHOLD = 0.10
        SECTOR_CENTER = {
            'red': 0, 'orange': 30, 'yellow': 60, 'chartreuse': 90,
            'green': 120, 'cyan': 150, 'azure': 180, 'blue': 210,
            'purple': 240, 'magenta': 270, 'pink': 300, 'rose': 330,
        }

        occupied = 0
        total_sector_weight = 0.0
        occupied_angles = []

        for name, sector in dna['sectors'].items():
            w = sector['weight']
            total_sector_weight += w
            if w > SECTOR_MIN:
                occupied += 1
                if name in SECTOR_CENTER:
                    occupied_angles.append(SECTOR_CENTER[name])

        count = occupied

        # Neutral mass bonus (tiered)
        neutral_mass = 1.0 - total_sector_weight
        if neutral_mass > NEUTRAL_THRESHOLD: count += 1
        if neutral_mass > 0.25:             count += 1
        if neutral_mass > 0.40:             count += 1

        # Tonal range bonus
        if dna['global']['l_std_dev'] > 22:    count += 1

        # Entropy bonus
        if dna['global']['hue_entropy'] > 0.7: count += 1

        # Hue spread bonus
        if len(occupied_angles) >= 3:
            occupied_angles.sort()
            max_gap = 0
            for i in range(1, len(occupied_angles)):
                gap = occupied_angles[i] - occupied_angles[i - 1]
                if gap > max_gap:
                    max_gap = gap
            # Wrap-around gap
            wrap_gap = 360 - occupied_angles[-1] + occupied_angles[0]
            if wrap_gap > max_gap:
                max_gap = wrap_gap
            if (360 - max_gap) > 150:
                count += 1

        params = archetype.get('parameters', {})
        min_colors = params.get('minColors', 4)
        max_colors = params.get('maxColors', 12)
        return max(min_colors, min(max_colors, round(count)))

    @classmethod
    def _apply_dna_v2_overrides(cls, params: dict, dna: dict, archetype: dict) -> None:
        """Apply chromatic fingerprint-based parameter adjustments (three-level trait stack)."""
        refinable = {
            'fine_art_scan', 'warm_tonal_optimized', 'detail_recovery',
            'vivid_photo', 'vivid_poster', 'neon',
        }
        if archetype.get('id') not in refinable:
            return

        g = dna.get('global', {})
        hue_entropy      = g.get('hue_entropy')
        temperature_bias = g.get('temperature_bias')

        # Level 2: dominant sector trait
        dominant_sector = dna.get('dominant_sector')
        if dominant_sector and dominant_sector != 'none':
            dominant_weight = (dna['sectors'].get(dominant_sector) or {}).get('weight', 0)
            if dominant_weight > 0.2:
                trait = cls.get_dominant_sector_trait(dominant_sector)
                if trait:
                    for key, value in trait['overrides'].items():
                        params[key] = value

        # Level 3: entropy delta
        if hue_entropy is not None:
            if hue_entropy < 0.3:
                params['lWeight'] = max(params.get('lWeight', 1.2), 1.8)
                params['paletteReduction'] = max(params.get('paletteReduction', 6.0), 8.0)
                params['enableHueGapAnalysis'] = False
            elif hue_entropy > 0.8:
                params['enableHueGapAnalysis'] = True
                params['paletteReduction'] = min(params.get('paletteReduction', 6.0), 4.0)

        # Cool outlier protection (Blue Door Fix)
        sectors = dna.get('sectors', {})
        cool_presence = (sectors.get('blue',  {}).get('weight', 0) +
                         sectors.get('cyan',  {}).get('weight', 0) +
                         sectors.get('azure', {}).get('weight', 0))
        if (cool_presence > 0.05 and
                temperature_bias is not None and temperature_bias > 0.5):
            params['neutralSovereigntyThreshold'] = 0
            params['enableHueGapAnalysis'] = True

    @classmethod
    def _apply_chroma_gate(cls, params: dict, dna: dict) -> None:
        """Boost cWeight for high-chroma images."""
        if not params.get('chromaGate') or dna.get('max_c') is None:
            return
        if dna['max_c'] > 60:
            params['cWeight'] = params.get('cWeight', 1.0) * params['chromaGate']

from .dithering import (
    BAYER_MATRIX,
    DITHERING_STRATEGIES,
    get_nearest,
    get_two_nearest,
    floyd_steinberg,
    atkinson,
    stucki,
    bayer,
)
from .separation import SeparationEngine
from .knobs import MechanicalKnobs
from .trap import TrapEngine
from .centroid_strategies import saliency, robust_saliency, volumetric, CENTROID_STRATEGIES
from .hue_gap_recovery import (
    _get_hue_sector,
    _analyze_image_hue_sectors,
    _analyze_palette_hue_coverage,
    _identify_hue_gaps,
    _find_true_missing_hues,
    _force_include_hue_gaps,
)
from .palette_ops import (
    calculate_cielab_distance,
    apply_perceptual_snap,
    _calculate_lab_centroid,
    _prune_palette,
    _apply_density_floor,
    _refine_k_means,
    _get_adaptive_snap_threshold,
    _merge_lab_colors,
    _snap_to_source,
    _find_nearest_in_palette,
    consolidate_near_duplicates,
)
from .lab_median_cut import (
    median_cut_in_lab_space,
    _calculate_box_metadata,
    _box_contains_hue_sector,
    _calculate_split_priority,
    _split_box_lab,
    _analyze_color_space,
)
from .posterization_engine import (
    posterize,
    palette_to_hex,
    auto_detect_substrate,
    _normalize_bit_depth,
    _build_tuning_from_config,
    _posterize_reveal_mk1_0,
    _posterize_balanced,
    _posterize_stencil,
)

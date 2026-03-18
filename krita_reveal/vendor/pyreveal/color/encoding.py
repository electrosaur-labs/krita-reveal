"""
Lab color encoding constants and conversions.

Three encoding spaces used in the pipeline:
  8-bit PSD:      L: 0-255,   a/b: 0-255    (neutral a/b = 128)
  Engine 16-bit:  L: 0-32768, a/b: 0-32768  (neutral a/b = 16384)
  PSD ICC 16-bit: L: 0-65535, a/b: 0-65535  (neutral a/b = 32768)
  Perceptual:     L: 0-100,   a/b: -128..+127

Engine 16-bit is the canonical internal representation.
Conversions to/from 8-bit or perceptual happen only at system boundaries.
"""

from __future__ import annotations

from array import array

# ============================================================================
# Constants
# ============================================================================

LAB16_L_MAX = 32768          # Maximum L in engine 16-bit (maps to L=100)
LAB16_AB_NEUTRAL = 16384     # Neutral a/b in engine 16-bit (maps to 0)
L_SCALE = LAB16_L_MAX / 100  # 327.68: perceptual L → engine 16-bit
AB_SCALE = LAB16_AB_NEUTRAL / 128  # 128.0: perceptual a/b offset → engine 16-bit offset
LAB8_AB_NEUTRAL = 128
PSD16_SCALE = 257            # 65535 / 255: PSD ICC 16-bit ↔ 8-bit

# ============================================================================
# Bulk Buffer Conversions
# ============================================================================

def convert_8bit_to_16bit_lab(lab8: bytes | bytearray, pixel_count: int) -> array:
    """Convert 8-bit PSD Lab buffer to engine 16-bit Lab buffer.

    8-bit PSD:      L: 0-255,   a/b: 0-255   (128=neutral)
    Engine 16-bit:  L: 0-32768, a/b: 0-32768 (16384=neutral)
    """
    lab16 = array('H', [0] * (pixel_count * 3))
    l_factor = LAB16_L_MAX / 255
    for i in range(pixel_count):
        off = i * 3
        lab16[off]     = round(lab8[off] * l_factor)
        lab16[off + 1] = int((lab8[off + 1] - LAB8_AB_NEUTRAL) * AB_SCALE + LAB16_AB_NEUTRAL)
        lab16[off + 2] = int((lab8[off + 2] - LAB8_AB_NEUTRAL) * AB_SCALE + LAB16_AB_NEUTRAL)
    return lab16


def convert_psd16bit_to_engine_lab(psd16: array, pixel_count: int) -> array:
    """Convert PSD ICC 16-bit Lab buffer to engine 16-bit Lab buffer.

    PSD ICC 16-bit: L: 0-65535, a/b: 0-65535 (32768=neutral)
    Engine 16-bit:  L: 0-32768, a/b: 0-32768 (16384=neutral)

    Simple right-shift by 1. Max L=65535 → 32767 (off by 1 at ceiling, 0.003% error).
    """
    lab16 = array('H', [0] * (pixel_count * 3))
    for i in range(pixel_count):
        off = i * 3
        lab16[off]     = psd16[off] >> 1
        lab16[off + 1] = psd16[off + 1] >> 1
        lab16[off + 2] = psd16[off + 2] >> 1
    return lab16


def convert_psd16bit_to_8bit_lab(psd16: array, pixel_count: int) -> bytearray:
    """Convert PSD ICC 16-bit Lab buffer to 8-bit PSD Lab buffer.

    Standard ICC 16→8 scaling: divide by 257 (= 65535/255).
    Neutral a/b: 32768/257 = 127.5 → 128.
    """
    lab8 = bytearray(pixel_count * 3)
    for i in range(pixel_count):
        off = i * 3
        lab8[off]     = round(psd16[off] / PSD16_SCALE)
        lab8[off + 1] = round(psd16[off + 1] / PSD16_SCALE)
        lab8[off + 2] = round(psd16[off + 2] / PSD16_SCALE)
    return lab8


def convert_engine16bit_to_8bit_lab(lab16: array, pixel_count: int) -> bytearray:
    """Convert engine 16-bit Lab buffer to 8-bit PSD Lab buffer.

    Engine 16-bit: L: 0-32768, a/b: 0-32768 (16384=neutral)
    8-bit PSD:     L: 0-255,   a/b: 0-255   (128=neutral)

    NOTE: NOT the same as dividing by 257 (which assumes PSD ICC 0-65535 range).
    """
    lab8 = bytearray(pixel_count * 3)
    l_scale = 255 / LAB16_L_MAX
    for i in range(pixel_count):
        off = i * 3
        lab8[off]     = round(min(255, lab16[off] * l_scale))
        lab8[off + 1] = round(min(255, (lab16[off + 1] - LAB16_AB_NEUTRAL) / AB_SCALE + LAB8_AB_NEUTRAL))
        lab8[off + 2] = round(min(255, (lab16[off + 2] - LAB16_AB_NEUTRAL) / AB_SCALE + LAB8_AB_NEUTRAL))
    return lab8


# Aliases matching JS naming conventions
lab8to16 = convert_8bit_to_16bit_lab
lab16to8 = convert_engine16bit_to_8bit_lab

# ============================================================================
# Single-Pixel Conversions
# ============================================================================

def perceptual_to_engine16(L: float, a: float, b: float) -> tuple[int, int, int]:
    """Convert perceptual Lab to engine 16-bit (L16, a16, b16).

    Perceptual:    L: 0-100, a/b: -128..+127
    Engine 16-bit: L: 0-32768, a/b: 0-32768 (16384=neutral)
    """
    return (
        round((L / 100) * LAB16_L_MAX),
        round(a * AB_SCALE + LAB16_AB_NEUTRAL),
        round(b * AB_SCALE + LAB16_AB_NEUTRAL),
    )


def engine16_to_perceptual(L16: int, a16: int, b16: int) -> tuple[float, float, float]:
    """Convert engine 16-bit to perceptual Lab (L, a, b).

    Engine 16-bit: L: 0-32768, a/b: 0-32768 (16384=neutral)
    Perceptual:    L: 0-100, a/b: -128..+127
    """
    return (
        (L16 / LAB16_L_MAX) * 100,
        (a16 - LAB16_AB_NEUTRAL) / AB_SCALE,
        (b16 - LAB16_AB_NEUTRAL) / AB_SCALE,
    )

# ============================================================================
# Display Conversions
# ============================================================================

def lab8bit_to_rgb(lab8: bytes | bytearray, pixel_count: int) -> bytearray:
    """Convert 8-bit Lab buffer to RGB buffer.

    Uses Lab→XYZ→sRGB pipeline with D50 reference white and Bradford
    chromatic adaptation. Includes sRGB gamma correction.
    """
    rgb = bytearray(pixel_count * 3)
    for i in range(pixel_count):
        off = i * 3
        L = (lab8[off] / 255) * 100
        a = lab8[off + 1] - 128
        b = lab8[off + 2] - 128

        fy = (L + 16) / 116
        fx = a / 500 + fy
        fz = fy - b / 200

        xr = fx * fx * fx if fx > 0.206893 else (fx - 16 / 116) / 7.787
        yr = fy * fy * fy if fy > 0.206893 else (fy - 16 / 116) / 7.787
        zr = fz * fz * fz if fz > 0.206893 else (fz - 16 / 116) / 7.787

        X = xr * 96.422
        Y = yr * 100.0
        Z = zr * 82.521

        # XYZ → sRGB with D50→D65 Bradford adaptation baked in
        R  = ( 3.1338561 * X - 1.6168667 * Y - 0.4906146 * Z) / 100
        G  = (-0.9787684 * X + 1.9161415 * Y + 0.0334540 * Z) / 100
        Bv = ( 0.0719453 * X - 0.2289914 * Y + 1.4052427 * Z) / 100

        R  = 1.055 * max(0.0, R)  ** (1 / 2.4) - 0.055 if R  > 0.0031308 else 12.92 * R
        G  = 1.055 * max(0.0, G)  ** (1 / 2.4) - 0.055 if G  > 0.0031308 else 12.92 * G
        Bv = 1.055 * max(0.0, Bv) ** (1 / 2.4) - 0.055 if Bv > 0.0031308 else 12.92 * Bv

        rgb[off]     = max(0, min(255, round(R  * 255)))
        rgb[off + 1] = max(0, min(255, round(G  * 255)))
        rgb[off + 2] = max(0, min(255, round(Bv * 255)))
    return rgb


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB (0-255) to hex string, e.g. '#ff0000'."""
    return f'#{round(r):02x}{round(g):02x}{round(b):02x}'

# ============================================================================
# Single-Color sRGB ↔ Perceptual Lab (D65 illuminant)
# ============================================================================

def _gamma_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_gamma(c: float) -> float:
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * max(0.0, c) ** (1 / 2.4) - 0.055


def _xyz_to_lab_f(t: float) -> float:
    delta = 6 / 29
    return t ** (1 / 3) if t > delta ** 3 else t / (3 * delta * delta) + 4 / 29


def _lab_to_xyz_f(t: float) -> float:
    delta = 6 / 29
    return t ** 3 if t > delta else 3 * delta * delta * (t - 4 / 29)


def rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to perceptual Lab. D65 illuminant.

    Returns (L, a, b) in perceptual space.
    """
    rl = _gamma_to_linear(r / 255)
    gl = _gamma_to_linear(g / 255)
    bl = _gamma_to_linear(b / 255)

    x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041

    fx = _xyz_to_lab_f(x / 0.95047)
    fy = _xyz_to_lab_f(y / 1.00000)
    fz = _xyz_to_lab_f(z / 1.08883)

    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_to_rgb(L: float, a: float, b: float) -> tuple[int, int, int]:
    """Convert perceptual Lab to sRGB (0-255). D65 illuminant.

    Out-of-gamut colors have chroma iteratively reduced (up to 20 iterations)
    to force into sRGB gamut, preserving hue and preventing clipping artifacts.
    """
    ca, cb = a, b
    for _ in range(20):
        y = (L + 16) / 116
        x = ca / 500 + y
        z = y - cb / 200

        xv = _lab_to_xyz_f(x) * 0.95047
        yv = _lab_to_xyz_f(y) * 1.00000
        zv = _lab_to_xyz_f(z) * 1.08883

        r  =  xv *  3.2404542 + yv * -1.5371385 + zv * -0.4985314
        g  =  xv * -0.9692660 + yv *  1.8760108 + zv *  0.0415560
        bv =  xv *  0.0556434 + yv * -0.2040259 + zv *  1.0572252

        r  = _linear_to_gamma(r)
        g  = _linear_to_gamma(g)
        bv = _linear_to_gamma(bv)

        if 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= bv <= 1.0:
            return (round(r * 255), round(g * 255), round(bv * 255))

        ca *= 0.95
        cb *= 0.95

    # Fallback: clamp
    y = (L + 16) / 116
    x = ca / 500 + y
    z = y - cb / 200

    xv = _lab_to_xyz_f(x) * 0.95047
    yv = _lab_to_xyz_f(y) * 1.00000
    zv = _lab_to_xyz_f(z) * 1.08883

    r  =  xv *  3.2404542 + yv * -1.5371385 + zv * -0.4985314
    g  =  xv * -0.9692660 + yv *  1.8760108 + zv *  0.0415560
    bv =  xv *  0.0556434 + yv * -0.2040259 + zv *  1.0572252

    return (
        max(0, min(255, round(_linear_to_gamma(r)  * 255))),
        max(0, min(255, round(_linear_to_gamma(g)  * 255))),
        max(0, min(255, round(_linear_to_gamma(bv) * 255))),
    )

# ============================================================================
# D50 Single-Color Conversion (matches Photoshop rendering)
# ============================================================================

def lab_to_rgb_d50(L: float, a: float, b: float) -> tuple[int, int, int]:
    """Convert perceptual Lab to sRGB (0-255). D50+Bradford (matches Photoshop).

    Uses simple clamping (not iterative chroma reduction) for out-of-gamut colors,
    preserving vibrancy for high-chroma warm/yellow Lab colors.
    """
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200

    xr = fx * fx * fx if fx > 0.206893 else (fx - 16 / 116) / 7.787
    yr = fy * fy * fy if fy > 0.206893 else (fy - 16 / 116) / 7.787
    zr = fz * fz * fz if fz > 0.206893 else (fz - 16 / 116) / 7.787

    X = xr * 96.422
    Y = yr * 100.0
    Z = zr * 82.521

    R  = ( 3.1338561 * X - 1.6168667 * Y - 0.4906146 * Z) / 100
    G  = (-0.9787684 * X + 1.9161415 * Y + 0.0334540 * Z) / 100
    Bv = ( 0.0719453 * X - 0.2289914 * Y + 1.4052427 * Z) / 100

    R  = 1.055 * max(0.0, R)  ** (1 / 2.4) - 0.055 if R  > 0.0031308 else 12.92 * R
    G  = 1.055 * max(0.0, G)  ** (1 / 2.4) - 0.055 if G  > 0.0031308 else 12.92 * G
    Bv = 1.055 * max(0.0, Bv) ** (1 / 2.4) - 0.055 if Bv > 0.0031308 else 12.92 * Bv

    return (
        max(0, min(255, round(R  * 255))),
        max(0, min(255, round(G  * 255))),
        max(0, min(255, round(Bv * 255))),
    )


def lab_gamut_info(L: float, a: float, b: float) -> dict:
    """Check sRGB gamut coverage using D50+Bradford (matching lab_to_rgb_d50).

    Returns:
        dict with keys: in_gamut (bool), iterations (int), chroma_loss (float 0-100%)
    """
    ca, cb = a, b
    for i in range(21):
        fy = (L + 16) / 116
        fx = ca / 500 + fy
        fz = fy - cb / 200

        xr = fx * fx * fx if fx > 0.206893 else (fx - 16 / 116) / 7.787
        yr = fy * fy * fy if fy > 0.206893 else (fy - 16 / 116) / 7.787
        zr = fz * fz * fz if fz > 0.206893 else (fz - 16 / 116) / 7.787

        r  = ( 3.1338561 * xr * 96.422 - 1.6168667 * yr * 100 - 0.4906146 * zr * 82.521) / 100
        g  = (-0.9787684 * xr * 96.422 + 1.9161415 * yr * 100 + 0.0334540 * zr * 82.521) / 100
        bv = ( 0.0719453 * xr * 96.422 - 0.2289914 * yr * 100 + 1.4052427 * zr * 82.521) / 100

        r  = _linear_to_gamma(r)
        g  = _linear_to_gamma(g)
        bv = _linear_to_gamma(bv)

        if 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= bv <= 1.0:
            chroma_loss = (1 - 0.95 ** i) * 100
            return {'in_gamut': i == 0, 'iterations': i, 'chroma_loss': chroma_loss}

        ca *= 0.95
        cb *= 0.95

    return {'in_gamut': False, 'iterations': 20, 'chroma_loss': (1 - 0.95 ** 20) * 100}

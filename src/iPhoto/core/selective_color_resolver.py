"""Selective Color adjustment resolver.

Implements a CPU-based selective colour adjustment that targets six predefined
hue ranges (Red, Yellow, Green, Cyan, Blue, Magenta) with independent Hue
shift, Saturation scale and Luminance lift controls.  The algorithm mirrors
the industry-standard approach used in the companion GLSL shader:

1. Convert each pixel from RGB to HSL.
2. For each of the six colour ranges, compute a feathered hue-distance mask
   multiplied by a saturation gate (to avoid neutral pixels).
3. Apply the per-range Hue / Saturation / Luminance adjustments weighted by
   the mask, then convert back to RGB.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_RANGES = 6

#: Default hue centres in normalised [0, 1) space for the six colour groups.
DEFAULT_CENTERS: List[float] = [
    0.0 / 360.0,    # Red
    60.0 / 360.0,   # Yellow
    120.0 / 360.0,  # Green
    180.0 / 360.0,  # Cyan
    240.0 / 360.0,  # Blue
    300.0 / 360.0,  # Magenta
]

#: Default half-width (in normalised hue space) for each range (~30°).
DEFAULT_WIDTH: float = 30.0 / 360.0

#: Default range slider value (0..1), mapped to 5°–70° during rendering.
DEFAULT_RANGE_SLIDER: float = 0.5

#: Saturation gating thresholds – avoids adjusting near-neutral pixels.
SAT_GATE_LO: float = 0.05
SAT_GATE_HI: float = 0.20

# Each range is stored as [center_hue, range_slider, hue_shift, sat_adj, lum_adj]
# center_hue: 0..1, range_slider: 0..1, hue_shift/sat_adj/lum_adj: -1..1
RANGE_LEN = 5  # elements per range

#: The default parameter list for all six ranges.
DEFAULT_SELECTIVE_COLOR_RANGES: List[List[float]] = [
    [DEFAULT_CENTERS[i], DEFAULT_RANGE_SLIDER, 0.0, 0.0, 0.0]
    for i in range(NUM_RANGES)
]


def is_identity(ranges: List[List[float]] | None) -> bool:
    """Return ``True`` when *ranges* represent a no-op adjustment."""

    if ranges is None:
        return True
    if not isinstance(ranges, list) or len(ranges) != NUM_RANGES:
        return True
    for r in ranges:
        if not isinstance(r, (list, tuple)) or len(r) < RANGE_LEN:
            return True
        # Check hue_shift, sat_adj, lum_adj are all zero
        if abs(r[2]) > 1e-6 or abs(r[3]) > 1e-6 or abs(r[4]) > 1e-6:
            return False
    return True


# ---------------------------------------------------------------------------
# CPU implementation (NumPy vectorised)
# ---------------------------------------------------------------------------

def _rgb_to_hsl(rgb: np.ndarray) -> np.ndarray:
    """Convert an (H, W, 3) float32 RGB array to HSL in-place-friendly form."""

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    l = (max_c + min_c) * 0.5
    d = max_c - min_c

    s = np.zeros_like(l)
    mask = d > 1e-6
    denom = 1.0 - np.abs(2.0 * l - 1.0)
    denom = np.where(denom < 1e-8, 1e-8, denom)
    s[mask] = d[mask] / denom[mask]
    s = np.clip(s, 0.0, 1.0)

    h = np.zeros_like(l)
    mask_r = mask & (max_c == r)
    mask_g = mask & (max_c == g) & ~mask_r
    mask_b = mask & ~mask_r & ~mask_g

    h[mask_r] = ((g[mask_r] - b[mask_r]) / d[mask_r]) % 6.0
    h[mask_g] = (b[mask_g] - r[mask_g]) / d[mask_g] + 2.0
    h[mask_b] = (r[mask_b] - g[mask_b]) / d[mask_b] + 4.0
    h = h / 6.0
    h = h % 1.0

    return np.stack([h, s, l], axis=-1)


def _hsl_to_rgb(hsl: np.ndarray) -> np.ndarray:
    """Convert an (H, W, 3) float32 HSL array back to RGB."""

    h, s, l = hsl[..., 0], hsl[..., 1], hsl[..., 2]

    q = np.where(l < 0.5, l * (1.0 + s), l + s - l * s)
    p = 2.0 * l - q

    def hue2rgb(pp, qq, t):
        t = t % 1.0
        out = np.copy(pp)
        mask1 = t < 1.0 / 6.0
        mask2 = (~mask1) & (t < 0.5)
        mask3 = (~mask1) & (~mask2) & (t < 2.0 / 3.0)
        out[mask1] = pp[mask1] + (qq[mask1] - pp[mask1]) * 6.0 * t[mask1]
        out[mask2] = qq[mask2]
        out[mask3] = pp[mask3] + (qq[mask3] - pp[mask3]) * (2.0 / 3.0 - t[mask3]) * 6.0
        return out

    r = hue2rgb(p, q, h + 1.0 / 3.0)
    g = hue2rgb(p, q, h)
    b = hue2rgb(p, q, h - 1.0 / 3.0)

    grey_mask = s < 1e-6
    r[grey_mask] = l[grey_mask]
    g[grey_mask] = l[grey_mask]
    b[grey_mask] = l[grey_mask]

    return np.stack([r, g, b], axis=-1)


def _hue_dist(h1: np.ndarray, h2: float) -> np.ndarray:
    """Circular distance on [0, 1)."""
    d = np.abs(h1 - h2)
    return np.minimum(d, 1.0 - d)


def apply_selective_color(
    arr: np.ndarray,
    ranges: List[List[float]],
) -> np.ndarray:
    """Apply selective colour adjustments to an (H, W, 4) uint8 RGBA array.

    *ranges* is a list of six sub-lists, each ``[center_hue, range_slider,
    hue_shift, sat_adj, lum_adj]`` with values in the ranges documented in the
    module docstring.

    Returns a new (H, W, 4) uint8 array.
    """

    if is_identity(ranges):
        return arr

    height, width = arr.shape[:2]
    rgb = arr[..., :3].astype(np.float32) / 255.0
    alpha = arr[..., 3:4]

    hsl = _rgb_to_hsl(rgb)

    for i in range(NUM_RANGES):
        rng = ranges[i]
        center = float(rng[0])
        range_slider = float(np.clip(rng[1], 0.0, 1.0))
        hue_shift_n = float(np.clip(rng[2], -1.0, 1.0))
        sat_adj_n = float(np.clip(rng[3], -1.0, 1.0))
        lum_adj_n = float(np.clip(rng[4], -1.0, 1.0))

        if abs(hue_shift_n) < 1e-6 and abs(sat_adj_n) < 1e-6 and abs(lum_adj_n) < 1e-6:
            continue

        # Convert range slider to hue half-width in normalised space
        deg = 5.0 + (70.0 - 5.0) * range_slider
        width_hue = float(np.clip(deg / 360.0, 0.001, 0.5))

        # Feathered hue mask
        dist = _hue_dist(hsl[..., 0], center)
        feather = max(0.001, width_hue * 0.50)
        mask = 1.0 - np.clip((dist - width_hue) / feather, 0.0, 1.0)

        # Saturation gate
        sat_gate = np.clip(
            (hsl[..., 1] - SAT_GATE_LO) / max(1e-6, SAT_GATE_HI - SAT_GATE_LO),
            0.0, 1.0,
        )
        mask = mask * sat_gate

        significant = mask > 1e-5
        if not np.any(significant):
            continue

        # Mapping (matching the GLSL shader)
        hue_shift = hue_shift_n * (30.0 / 360.0)
        sat_scale = 1.0 + sat_adj_n
        lum_lift = lum_adj_n * 0.25

        h2 = (hsl[..., 0] + hue_shift) % 1.0
        s2 = np.clip(hsl[..., 1] * sat_scale, 0.0, 1.0)
        l2 = np.clip(hsl[..., 2] + lum_lift, 0.0, 1.0)

        # Blend by mask
        mask_3d = mask[..., np.newaxis]
        hsl_adj = np.stack([h2, s2, l2], axis=-1)
        hsl = hsl * (1.0 - mask_3d) + hsl_adj * mask_3d

    rgb_out = _hsl_to_rgb(hsl)
    rgb_out = np.clip(rgb_out * 255.0, 0, 255).astype(np.uint8)
    return np.concatenate([rgb_out, alpha], axis=-1)

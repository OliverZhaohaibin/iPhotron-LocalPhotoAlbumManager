"""Curve adjustment data structures and LUT generation utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

from .spline import MonotoneCubicSpline


@dataclass
class CurvePoint:
    """A single control point on a curve."""
    x: float
    y: float

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    @staticmethod
    def from_tuple(t: Tuple[float, float]) -> "CurvePoint":
        return CurvePoint(x=t[0], y=t[1])


@dataclass
class CurveChannel:
    """Control points for a single channel curve."""
    points: List[CurvePoint] = field(default_factory=lambda: [
        CurvePoint(0.0, 0.0),
        CurvePoint(1.0, 1.0)
    ])

    def to_list(self) -> List[Tuple[float, float]]:
        return [p.to_tuple() for p in self.points]

    @staticmethod
    def from_list(points: List[Tuple[float, float]]) -> "CurveChannel":
        return CurveChannel(points=[CurvePoint.from_tuple(t) for t in points])

    def is_identity(self) -> bool:
        """Return True if this curve is effectively identity (no adjustment)."""
        if len(self.points) != 2:
            return False
        return (
            abs(self.points[0].x) < 1e-6 and
            abs(self.points[0].y) < 1e-6 and
            abs(self.points[1].x - 1.0) < 1e-6 and
            abs(self.points[1].y - 1.0) < 1e-6
        )


@dataclass
class CurveParams:
    """Complete curve adjustment parameters for all channels."""
    rgb: CurveChannel = field(default_factory=CurveChannel)
    red: CurveChannel = field(default_factory=CurveChannel)
    green: CurveChannel = field(default_factory=CurveChannel)
    blue: CurveChannel = field(default_factory=CurveChannel)
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True if all curves are identity (no adjustment)."""
        return (
            self.rgb.is_identity() and
            self.red.is_identity() and
            self.green.is_identity() and
            self.blue.is_identity()
        )

    def to_dict(self) -> dict:
        """Serialize curve params to a dictionary for storage."""
        return {
            "Curve_Enabled": self.enabled,
            "Curve_RGB": self.rgb.to_list(),
            "Curve_Red": self.red.to_list(),
            "Curve_Green": self.green.to_list(),
            "Curve_Blue": self.blue.to_list(),
        }

    @staticmethod
    def from_dict(data: dict) -> "CurveParams":
        """Deserialize curve params from a dictionary."""
        params = CurveParams()
        params.enabled = bool(data.get("Curve_Enabled", False))
        if "Curve_RGB" in data:
            params.rgb = CurveChannel.from_list(data["Curve_RGB"])
        if "Curve_Red" in data:
            params.red = CurveChannel.from_list(data["Curve_Red"])
        if "Curve_Green" in data:
            params.green = CurveChannel.from_list(data["Curve_Green"])
        if "Curve_Blue" in data:
            params.blue = CurveChannel.from_list(data["Curve_Blue"])
        return params


def _build_channel_spline(channel: CurveChannel) -> MonotoneCubicSpline:
    """Build a spline from a CurveChannel."""
    points = sorted(channel.points, key=lambda p: p.x)
    x = [p.x for p in points]
    y = [p.y for p in points]
    return MonotoneCubicSpline(x, y)


def _evaluate_with_clamping(
    spline: MonotoneCubicSpline,
    inputs: np.ndarray,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> np.ndarray:
    """Evaluate spline with clamping outside the defined range."""
    vals = spline.evaluate(inputs).copy()
    mask_low = inputs < start_x
    mask_high = inputs > end_x
    vals[mask_low] = start_y
    vals[mask_high] = end_y
    return np.clip(vals, 0.0, 1.0)


def generate_curve_lut(params: CurveParams) -> np.ndarray:
    """Generate a 256x3 LUT from curve parameters.

    Returns:
        numpy array of shape (256, 3) with float32 values in [0, 1] range.
        Each row contains [R, G, B] output values for the corresponding input level.
    """
    xs = np.linspace(0, 1, 256)

    # Build splines for each channel
    rgb_spline = _build_channel_spline(params.rgb)
    red_spline = _build_channel_spline(params.red)
    green_spline = _build_channel_spline(params.green)
    blue_spline = _build_channel_spline(params.blue)

    # Get start/end points for clamping
    rgb_pts = sorted(params.rgb.points, key=lambda p: p.x)
    red_pts = sorted(params.red.points, key=lambda p: p.x)
    green_pts = sorted(params.green.points, key=lambda p: p.x)
    blue_pts = sorted(params.blue.points, key=lambda p: p.x)

    # Evaluate individual channel curves
    r_curve = _evaluate_with_clamping(
        red_spline, xs,
        red_pts[0].x, red_pts[0].y,
        red_pts[-1].x, red_pts[-1].y
    )
    g_curve = _evaluate_with_clamping(
        green_spline, xs,
        green_pts[0].x, green_pts[0].y,
        green_pts[-1].x, green_pts[-1].y
    )
    b_curve = _evaluate_with_clamping(
        blue_spline, xs,
        blue_pts[0].x, blue_pts[0].y,
        blue_pts[-1].x, blue_pts[-1].y
    )

    # Apply master RGB curve to each channel
    r_final = _evaluate_with_clamping(
        rgb_spline, r_curve,
        rgb_pts[0].x, rgb_pts[0].y,
        rgb_pts[-1].x, rgb_pts[-1].y
    )
    g_final = _evaluate_with_clamping(
        rgb_spline, g_curve,
        rgb_pts[0].x, rgb_pts[0].y,
        rgb_pts[-1].x, rgb_pts[-1].y
    )
    b_final = _evaluate_with_clamping(
        rgb_spline, b_curve,
        rgb_pts[0].x, rgb_pts[0].y,
        rgb_pts[-1].x, rgb_pts[-1].y
    )

    # Stack into (256, 3) array
    lut = np.stack([r_final, g_final, b_final], axis=1).astype(np.float32)
    return lut


def apply_curve_lut_to_image(
    image_array: np.ndarray,
    lut: np.ndarray,
) -> np.ndarray:
    """Apply a curve LUT to an image array.

    Args:
        image_array: numpy array of shape (H, W, 3) or (H, W, 4) with uint8 values
        lut: numpy array of shape (256, 3) with float32 values in [0, 1]

    Returns:
        numpy array of same shape as input with curve applied
    """
    # Convert to indices (0-255)
    result = image_array.copy()

    # Apply LUT to each channel
    for c in range(min(3, result.shape[2])):
        channel = result[:, :, c]
        # Map through LUT
        lut_channel = (lut[:, c] * 255).astype(np.uint8)
        result[:, :, c] = lut_channel[channel]

    return result


# Session key for curve data
CURVE_KEYS = (
    "Curve_Enabled",
    "Curve_RGB",
    "Curve_Red",
    "Curve_Green",
    "Curve_Blue",
)

# Default curve points (identity)
DEFAULT_CURVE_POINTS = [(0.0, 0.0), (1.0, 1.0)]


def curve_params_from_session_values(values: dict) -> CurveParams:
    """Extract CurveParams from an EditSession values dict."""
    params = CurveParams()
    params.enabled = bool(values.get("Curve_Enabled", False))

    for key, attr in [
        ("Curve_RGB", "rgb"),
        ("Curve_Red", "red"),
        ("Curve_Green", "green"),
        ("Curve_Blue", "blue"),
    ]:
        raw = values.get(key)
        if raw and isinstance(raw, list):
            setattr(params, attr, CurveChannel.from_list(raw))

    return params


def session_values_from_curve_params(params: CurveParams) -> dict:
    """Convert CurveParams to a dict suitable for EditSession."""
    return params.to_dict()

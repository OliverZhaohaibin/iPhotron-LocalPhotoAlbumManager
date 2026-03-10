"""Read/write helpers for crop, curve, levels, and selective-color XML sections."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple
import xml.etree.ElementTree as ET

from ..core.curve_resolver import DEFAULT_CURVE_POINTS, CurveChannel
from ..core.levels_resolver import DEFAULT_LEVELS_HANDLES
from ..core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES, NUM_RANGES

# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------


def _find_child_case_insensitive(parent: ET.Element, tag: str) -> ET.Element | None:
    """Return the first child whose tag matches *tag* ignoring case."""
    lowered = tag.lower()
    for child in parent:
        if child.tag.lower() == lowered:
            return child
    return None


def _remove_children_case_insensitive(parent: ET.Element, tag: str) -> None:
    """Remove all children of *parent* whose tag matches *tag* (case-insensitive)."""
    lowered = tag.lower()
    for child in list(parent):
        if child.tag.lower() == lowered:
            parent.remove(child)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float_or_default(value: object | None, default: float) -> float:
    """Return ``value`` converted to ``float`` or ``default`` when conversion fails."""
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


# ---------------------------------------------------------------------------
# Crop constants
# ---------------------------------------------------------------------------

_CROP_NODE = "crop"
_CROP_CHILD_X = "x"
_CROP_CHILD_Y = "y"
_CROP_CHILD_W = "w"
_CROP_CHILD_H = "h"
_CROP_CHILD_STRAIGHTEN = "straighten"
_CROP_CHILD_ROTATE = "rotate90"
_CROP_CHILD_FLIP = "flipHorizontal"
_CROP_CHILD_VERTICAL = "vertical"
_CROP_CHILD_HORIZONTAL = "horizontal"
_LEGACY_CROP_NODE = "Crop"
_ATTR_CX = "cx"
_ATTR_CY = "cy"
_ATTR_WIDTH = "w"
_ATTR_HEIGHT = "h"

# ---------------------------------------------------------------------------
# Crop helpers
# ---------------------------------------------------------------------------


def _normalised_crop_components(values: Mapping[str, float | bool]) -> tuple[float, float, float, float]:
    """Return ``(left, top, width, height)`` from centre-based crop adjustments."""

    cx = _clamp01(_float_or_default(values.get("Crop_CX"), 0.5))
    cy = _clamp01(_float_or_default(values.get("Crop_CY"), 0.5))
    width = _clamp01(_float_or_default(values.get("Crop_W"), 1.0))
    height = _clamp01(_float_or_default(values.get("Crop_H"), 1.0))

    half_w = width * 0.5
    half_h = height * 0.5
    cx = max(half_w, min(1.0 - half_w, cx))
    cy = max(half_h, min(1.0 - half_h, cy))
    left = max(0.0, min(1.0 - width, cx - half_w))
    top = max(0.0, min(1.0 - height, cy - half_h))
    return (left, top, width, height)


def _centre_crop_from_top_left(left: float, top: float, width: float, height: float) -> dict[str, float]:
    """Convert top-left crop coordinates to the centre-based representation."""

    width = _clamp01(width)
    height = _clamp01(height)
    left = max(0.0, min(1.0 - width, _clamp01(left)))
    top = max(0.0, min(1.0 - height, _clamp01(top)))
    cx = left + width * 0.5
    cy = top + height * 0.5
    return {
        "Crop_CX": cx,
        "Crop_CY": cy,
        "Crop_W": width,
        "Crop_H": height,
    }


def _read_crop_from_node(node: ET.Element) -> dict[str, float]:
    """Return crop adjustments described by the structured ``<crop>`` *node*."""

    def _child_value(tag: str, default: float) -> float:
        child = _find_child_case_insensitive(node, tag)
        text = child.text.strip() if child is not None and child.text is not None else None
        return _float_or_default(text, default)

    left = _child_value(_CROP_CHILD_X, 0.0)
    top = _child_value(_CROP_CHILD_Y, 0.0)
    width = _child_value(_CROP_CHILD_W, 1.0)
    height = _child_value(_CROP_CHILD_H, 1.0)
    straighten_node = _find_child_case_insensitive(node, _CROP_CHILD_STRAIGHTEN)
    straighten = _float_or_default(
        straighten_node.text if straighten_node is not None and straighten_node.text is not None else None,
        0.0,
    )
    rotate_child = _find_child_case_insensitive(node, _CROP_CHILD_ROTATE)
    rotate_steps = int(round(_float_or_default(rotate_child.text if rotate_child is not None else None, 0.0)))
    flip_child = _find_child_case_insensitive(node, _CROP_CHILD_FLIP)
    flip_text = flip_child.text.strip().lower() if flip_child is not None and flip_child.text else "false"
    flip_enabled = flip_text in {"1", "true", "yes", "on"}
    vertical = _child_value(_CROP_CHILD_VERTICAL, 0.0)
    horizontal = _child_value(_CROP_CHILD_HORIZONTAL, 0.0)
    values = _centre_crop_from_top_left(left, top, width, height)
    values.update(
        {
            "Crop_Straighten": straighten,
            "Crop_Rotate90": float(max(0, min(3, rotate_steps))),
            "Crop_FlipH": flip_enabled,
            "Perspective_Vertical": vertical,
            "Perspective_Horizontal": horizontal,
        }
    )
    return values


def _read_crop_from_legacy_attributes(node: ET.Element) -> dict[str, float]:
    """Return crop adjustments stored as legacy ``<Crop cx=...`` attributes."""

    cx = _clamp01(_float_or_default(node.get(_ATTR_CX), 0.5))
    cy = _clamp01(_float_or_default(node.get(_ATTR_CY), 0.5))
    width = _clamp01(_float_or_default(node.get(_ATTR_WIDTH), 1.0))
    height = _clamp01(_float_or_default(node.get(_ATTR_HEIGHT), 1.0))
    return {
        "Crop_CX": cx,
        "Crop_CY": cy,
        "Crop_W": width,
        "Crop_H": height,
        "Crop_Straighten": 0.0,
        "Crop_Rotate90": 0.0,
        "Crop_FlipH": False,
    }


def _write_crop_node(root: ET.Element, values: Mapping[str, float | bool]) -> None:
    """Insert/replace the ``<crop>`` section under *root* using *values*."""

    _remove_children_case_insensitive(root, _CROP_NODE)
    crop = ET.SubElement(root, _CROP_NODE)
    left, top, width, height = _normalised_crop_components(values)
    for tag, numeric in (
        (_CROP_CHILD_X, left),
        (_CROP_CHILD_Y, top),
        (_CROP_CHILD_W, width),
        (_CROP_CHILD_H, height),
    ):
        child = ET.SubElement(crop, tag)
        child.text = f"{numeric:.6f}"
    extra_children = {
        _CROP_CHILD_STRAIGHTEN: float(values.get("Crop_Straighten", 0.0)),
        _CROP_CHILD_ROTATE: float(max(0, min(3, int(round(float(values.get("Crop_Rotate90", 0.0))))))),
        _CROP_CHILD_VERTICAL: float(values.get("Perspective_Vertical", 0.0)),
        _CROP_CHILD_HORIZONTAL: float(values.get("Perspective_Horizontal", 0.0)),
    }
    for tag, numeric in extra_children.items():
        child = ET.SubElement(crop, tag)
        child.text = f"{numeric:.6f}"
    flip_child = ET.SubElement(crop, _CROP_CHILD_FLIP)
    flip_child.text = "true" if bool(values.get("Crop_FlipH", False)) else "false"


# ---------------------------------------------------------------------------
# Curve constants
# ---------------------------------------------------------------------------

_CURVE_NODE = "Curve"
_CURVE_ENABLED = "enabled"
_CURVE_CHANNEL_RGB = "rgb"
_CURVE_CHANNEL_RED = "red"
_CURVE_CHANNEL_GREEN = "green"
_CURVE_CHANNEL_BLUE = "blue"
_CURVE_POINT = "point"

# ---------------------------------------------------------------------------
# Curve helpers
# ---------------------------------------------------------------------------


def _read_curve_channel_points(channel_node: ET.Element) -> List[Tuple[float, float]]:
    """Read curve control points from a channel XML node."""
    points: List[Tuple[float, float]] = []
    for point_node in channel_node.findall(_CURVE_POINT):
        x_attr = point_node.get("x")
        y_attr = point_node.get("y")
        if x_attr is not None and y_attr is not None:
            try:
                x = float(x_attr)
                y = float(y_attr)
                points.append((x, y))
            except ValueError:
                continue
    # Sort by x and ensure we have at least identity points
    if len(points) < 2:
        return list(DEFAULT_CURVE_POINTS)
    return sorted(points, key=lambda p: p[0])


def _read_curve_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return curve adjustments described by the ``<Curve>`` *node*."""
    result: Dict[str, Any] = {}

    # Read enabled state
    enabled_attr = node.get(_CURVE_ENABLED)
    if enabled_attr is not None:
        result["Curve_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["Curve_Enabled"] = False

    # Read each channel
    for xml_tag, key in [
        (_CURVE_CHANNEL_RGB, "Curve_RGB"),
        (_CURVE_CHANNEL_RED, "Curve_Red"),
        (_CURVE_CHANNEL_GREEN, "Curve_Green"),
        (_CURVE_CHANNEL_BLUE, "Curve_Blue"),
    ]:
        channel_node = _find_child_case_insensitive(node, xml_tag)
        if channel_node is not None:
            result[key] = _read_curve_channel_points(channel_node)
        else:
            result[key] = list(DEFAULT_CURVE_POINTS)

    return result


def _write_curve_channel_points(parent: ET.Element, tag: str, points: List[Tuple[float, float]]) -> None:
    """Write curve control points to a channel XML node."""
    channel = ET.SubElement(parent, tag)
    for x, y in points:
        point = ET.SubElement(channel, _CURVE_POINT)
        point.set("x", f"{float(x):.6f}")
        point.set("y", f"{float(y):.6f}")


def _write_curve_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<Curve>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _CURVE_NODE)

    # Only write curve node if there's curve data to save
    curve_enabled = bool(values.get("Curve_Enabled", False))
    curve_rgb = values.get("Curve_RGB", DEFAULT_CURVE_POINTS)
    curve_red = values.get("Curve_Red", DEFAULT_CURVE_POINTS)
    curve_green = values.get("Curve_Green", DEFAULT_CURVE_POINTS)
    curve_blue = values.get("Curve_Blue", DEFAULT_CURVE_POINTS)

    # Check if any curve is non-identity (worth saving)
    def _check_identity(pts) -> bool:
        if not isinstance(pts, list):
            return True
        channel = CurveChannel.from_list(pts)
        return channel.is_identity()

    all_identity = (
        _check_identity(curve_rgb) and
        _check_identity(curve_red) and
        _check_identity(curve_green) and
        _check_identity(curve_blue)
    )

    # Skip writing if all curves are identity and not enabled
    if not curve_enabled and all_identity:
        return

    curve = ET.SubElement(root, _CURVE_NODE)
    curve.set(_CURVE_ENABLED, "true" if curve_enabled else "false")

    _write_curve_channel_points(curve, _CURVE_CHANNEL_RGB, curve_rgb if isinstance(curve_rgb, list) else list(DEFAULT_CURVE_POINTS))
    _write_curve_channel_points(curve, _CURVE_CHANNEL_RED, curve_red if isinstance(curve_red, list) else list(DEFAULT_CURVE_POINTS))
    _write_curve_channel_points(curve, _CURVE_CHANNEL_GREEN, curve_green if isinstance(curve_green, list) else list(DEFAULT_CURVE_POINTS))
    _write_curve_channel_points(curve, _CURVE_CHANNEL_BLUE, curve_blue if isinstance(curve_blue, list) else list(DEFAULT_CURVE_POINTS))


# ---------------------------------------------------------------------------
# Levels constants
# ---------------------------------------------------------------------------

_LEVELS_NODE = "Levels"
_LEVELS_ENABLED = "enabled"
_LEVELS_HANDLE = "handle"

# ---------------------------------------------------------------------------
# Levels helpers
# ---------------------------------------------------------------------------


def _read_levels_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return levels adjustments described by the ``<Levels>`` *node*."""
    result: Dict[str, Any] = {}

    enabled_attr = node.get(_LEVELS_ENABLED)
    if enabled_attr is not None:
        result["Levels_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["Levels_Enabled"] = False

    handles: List[float] = []
    for handle_node in node.findall(_LEVELS_HANDLE):
        val = handle_node.get("value")
        if val is not None:
            try:
                handles.append(float(val))
            except ValueError:
                continue
    if len(handles) == 5:
        result["Levels_Handles"] = handles
    else:
        result["Levels_Handles"] = list(DEFAULT_LEVELS_HANDLES)

    return result


def _write_levels_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<Levels>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _LEVELS_NODE)

    levels_enabled = bool(values.get("Levels_Enabled", False))
    handles = values.get("Levels_Handles", DEFAULT_LEVELS_HANDLES)
    if not isinstance(handles, list) or len(handles) != 5:
        handles = list(DEFAULT_LEVELS_HANDLES)

    is_identity = all(
        abs(h - d) < 1e-6 for h, d in zip(handles, DEFAULT_LEVELS_HANDLES)
    )
    if not levels_enabled and is_identity:
        return

    levels = ET.SubElement(root, _LEVELS_NODE)
    levels.set(_LEVELS_ENABLED, "true" if levels_enabled else "false")
    for v in handles:
        h = ET.SubElement(levels, _LEVELS_HANDLE)
        h.set("value", f"{float(v):.6f}")


# ---------------------------------------------------------------------------
# Selective Color constants
# ---------------------------------------------------------------------------

_SELECTIVE_COLOR_NODE = "SelectiveColor"
_SC_ENABLED = "enabled"
_SC_RANGE = "range"

# ---------------------------------------------------------------------------
# Selective Color helpers
# ---------------------------------------------------------------------------


def _read_selective_color_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return Selective Color adjustments described by the ``<SelectiveColor>`` *node*."""
    result: Dict[str, Any] = {}

    enabled_attr = node.get(_SC_ENABLED)
    if enabled_attr is not None:
        result["SelectiveColor_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["SelectiveColor_Enabled"] = False

    ranges: List[List[float]] = []
    for range_node in node.findall(_SC_RANGE):
        try:
            center = float(range_node.get("center", "0"))
            width = float(range_node.get("width", "0.5"))
            hue_shift = float(range_node.get("hue_shift", "0"))
            sat_adj = float(range_node.get("sat_adj", "0"))
            lum_adj = float(range_node.get("lum_adj", "0"))
            ranges.append([center, width, hue_shift, sat_adj, lum_adj])
        except (ValueError, TypeError):
            continue

    if len(ranges) == NUM_RANGES:
        result["SelectiveColor_Ranges"] = ranges
    else:
        result["SelectiveColor_Ranges"] = [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES]

    return result


def _write_selective_color_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<SelectiveColor>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _SELECTIVE_COLOR_NODE)

    sc_enabled = bool(values.get("SelectiveColor_Enabled", False))
    ranges = values.get("SelectiveColor_Ranges")
    if not isinstance(ranges, list) or len(ranges) != NUM_RANGES:
        ranges = [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES]

    # Check if identity (all shifts zero)
    all_identity = all(
        abs(r[2]) < 1e-6 and abs(r[3]) < 1e-6 and abs(r[4]) < 1e-6
        for r in ranges
        if isinstance(r, (list, tuple)) and len(r) >= 5
    )
    if not sc_enabled and all_identity:
        return

    sc_node = ET.SubElement(root, _SELECTIVE_COLOR_NODE)
    sc_node.set(_SC_ENABLED, "true" if sc_enabled else "false")
    for r in ranges:
        if isinstance(r, (list, tuple)) and len(r) >= 5:
            rn = ET.SubElement(sc_node, _SC_RANGE)
            rn.set("center", f"{float(r[0]):.6f}")
            rn.set("width", f"{float(r[1]):.6f}")
            rn.set("hue_shift", f"{float(r[2]):.6f}")
            rn.set("sat_adj", f"{float(r[3]):.6f}")
            rn.set("lum_adj", f"{float(r[4]):.6f}")


# ---------------------------------------------------------------------------
# Definition constants
# ---------------------------------------------------------------------------

_DEFINITION_NODE = "Definition"
_DEFINITION_ENABLED = "enabled"
_DEFINITION_VALUE = "value"

# ---------------------------------------------------------------------------
# Definition helpers
# ---------------------------------------------------------------------------


def _read_definition_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return definition adjustments described by the ``<Definition>`` *node*."""
    result: Dict[str, Any] = {}

    enabled_attr = node.get(_DEFINITION_ENABLED)
    if enabled_attr is not None:
        result["Definition_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["Definition_Enabled"] = False

    value_attr = node.get(_DEFINITION_VALUE)
    if value_attr is not None:
        try:
            result["Definition_Value"] = max(0.0, min(1.0, float(value_attr)))
        except (ValueError, TypeError):
            result["Definition_Value"] = 0.0
    else:
        result["Definition_Value"] = 0.0

    return result


def _write_definition_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<Definition>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _DEFINITION_NODE)

    def_enabled = bool(values.get("Definition_Enabled", False))
    def_value = float(values.get("Definition_Value", 0.0))

    if not def_enabled and abs(def_value) < 1e-6:
        return

    node = ET.SubElement(root, _DEFINITION_NODE)
    node.set(_DEFINITION_ENABLED, "true" if def_enabled else "false")
    node.set(_DEFINITION_VALUE, f"{def_value:.6f}")


# ---------------------------------------------------------------------------
# Denoise (Noise Reduction) node constants
# ---------------------------------------------------------------------------

_DENOISE_NODE = "Denoise"
_DENOISE_ENABLED = "enabled"
_DENOISE_AMOUNT = "amount"

# ---------------------------------------------------------------------------
# Denoise helpers
# ---------------------------------------------------------------------------


def _read_denoise_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return denoise adjustments described by the ``<Denoise>`` *node*."""
    result: Dict[str, Any] = {}

    enabled_attr = node.get(_DENOISE_ENABLED)
    if enabled_attr is not None:
        result["Denoise_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["Denoise_Enabled"] = False

    amount_attr = node.get(_DENOISE_AMOUNT)
    if amount_attr is not None:
        try:
            result["Denoise_Amount"] = max(0.0, min(5.0, float(amount_attr)))
        except (ValueError, TypeError):
            result["Denoise_Amount"] = 0.0
    else:
        result["Denoise_Amount"] = 0.0

    return result


def _write_denoise_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<Denoise>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _DENOISE_NODE)

    dn_enabled = bool(values.get("Denoise_Enabled", False))
    dn_amount = float(values.get("Denoise_Amount", 0.0))

    if not dn_enabled and abs(dn_amount) < 1e-6:
        return

    node = ET.SubElement(root, _DENOISE_NODE)
    node.set(_DENOISE_ENABLED, "true" if dn_enabled else "false")
    node.set(_DENOISE_AMOUNT, f"{dn_amount:.6f}")


# ---------------------------------------------------------------------------
# Vignette node constants
# ---------------------------------------------------------------------------

_VIGNETTE_NODE = "Vignette"
_VIGNETTE_ENABLED = "enabled"
_VIGNETTE_STRENGTH = "strength"
_VIGNETTE_RADIUS = "radius"
_VIGNETTE_SOFTNESS = "softness"

# ---------------------------------------------------------------------------
# Vignette helpers
# ---------------------------------------------------------------------------


def _read_vignette_from_node(node: ET.Element) -> Dict[str, Any]:
    """Return vignette adjustments described by the ``<Vignette>`` *node*."""
    result: Dict[str, Any] = {}

    enabled_attr = node.get(_VIGNETTE_ENABLED)
    if enabled_attr is not None:
        result["Vignette_Enabled"] = enabled_attr.lower() in {"1", "true", "yes", "on"}
    else:
        result["Vignette_Enabled"] = False

    for attr, key, lo, hi, default in (
        (_VIGNETTE_STRENGTH, "Vignette_Strength", 0.0, 1.0, 0.0),
        (_VIGNETTE_RADIUS, "Vignette_Radius", 0.0, 1.0, 0.50),
        (_VIGNETTE_SOFTNESS, "Vignette_Softness", 0.0, 1.0, 0.0),
    ):
        raw = node.get(attr)
        if raw is not None:
            try:
                result[key] = max(lo, min(hi, float(raw)))
            except (ValueError, TypeError):
                result[key] = default
        else:
            result[key] = default

    return result


def _write_vignette_node(root: ET.Element, values: Mapping[str, Any]) -> None:
    """Insert/replace the ``<Vignette>`` section under *root* using *values*."""
    _remove_children_case_insensitive(root, _VIGNETTE_NODE)

    vig_enabled = bool(values.get("Vignette_Enabled", False))
    vig_strength = float(values.get("Vignette_Strength", 0.0))

    if not vig_enabled and abs(vig_strength) < 1e-6:
        return

    node = ET.SubElement(root, _VIGNETTE_NODE)
    node.set(_VIGNETTE_ENABLED, "true" if vig_enabled else "false")
    node.set(_VIGNETTE_STRENGTH, f"{vig_strength:.6f}")
    node.set(_VIGNETTE_RADIUS, f"{float(values.get('Vignette_Radius', 0.50)):.6f}")
    node.set(_VIGNETTE_SOFTNESS, f"{float(values.get('Vignette_Softness', 0.0)):.6f}")

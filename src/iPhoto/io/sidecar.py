"""Read/write helpers for ``.ipo`` XML sidecar files."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping
import xml.etree.ElementTree as ET

from ..core.light_resolver import LIGHT_KEYS, resolve_light_vector
from ..core.color_resolver import COLOR_KEYS, ColorResolver, ColorStats

BW_KEYS = (
    "BW_Master",
    "BW_Intensity",
    "BW_Neutrals",
    "BW_Tone",
    "BW_Grain",
)

BW_DEFAULTS = {
    "BW_Master": 0.5,
    "BW_Intensity": 0.5,
    "BW_Neutrals": 0.0,
    "BW_Tone": 0.0,
    "BW_Grain": 0.0,
}

_BW_RANGE_KEYS = {"BW_Master", "BW_Intensity", "BW_Neutrals", "BW_Tone"}


def _normalise_bw_value(key: str, value: float) -> float:
    """Return *value* mapped to the modern ``[0, 1]`` range for B&W controls."""

    numeric = float(value)
    if key in _BW_RANGE_KEYS and (numeric < 0.0 or numeric > 1.0):
        numeric = (numeric + 1.0) * 0.5
    return max(0.0, min(1.0, numeric))

_SIDE_CAR_ROOT = "iPhotoAdjustments"
_LIGHT_NODE = "Light"
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
_VERSION_ATTR = "version"
_CURRENT_VERSION = "1.0"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float_or_default(value: object | None, default: float) -> float:
    """Return ``value`` converted to ``float`` or ``default`` when conversion fails."""

    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _new_sidecar_root() -> ET.Element:
    """Return a fresh ``<iPhotoAdjustments>`` element with the current version."""

    root = ET.Element(_SIDE_CAR_ROOT)
    root.set(_VERSION_ATTR, _CURRENT_VERSION)
    return root


def _load_or_create_root(sidecar_path: Path) -> ET.Element:
    """Parse *sidecar_path* if possible, otherwise return a new root element."""

    if sidecar_path.exists():
        try:
            tree = ET.parse(sidecar_path)
            root = tree.getroot()
        except (ET.ParseError, OSError):
            return _new_sidecar_root()
        if root.tag != _SIDE_CAR_ROOT:
            return _new_sidecar_root()
        root.set(_VERSION_ATTR, _CURRENT_VERSION)
        return root
    return _new_sidecar_root()


def _remove_children_case_insensitive(parent: ET.Element, tag: str) -> None:
    """Remove all children of *parent* whose tag matches *tag* (case-insensitive)."""

    lowered = tag.lower()
    for child in list(parent):
        if child.tag.lower() == lowered:
            parent.remove(child)


def _find_child_case_insensitive(parent: ET.Element, tag: str) -> ET.Element | None:
    """Return the first child whose tag matches *tag* ignoring case."""

    lowered = tag.lower()
    for child in parent:
        if child.tag.lower() == lowered:
            return child
    return None


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


def sidecar_path_for_asset(asset_path: Path) -> Path:
    """Return the expected sidecar path for *asset_path*."""

    return asset_path.with_suffix(".ipo")


def load_adjustments(asset_path: Path) -> Dict[str, float | bool]:
    """Return light adjustments stored alongside *asset_path*.

    Missing files or parsing errors are treated as an empty adjustment set so the
    caller can continue working with the unmodified image.  Individual entries
    that fail to parse fall back to ``0.0`` rather than aborting the load, which
    keeps the feature resilient against manual edits or older file formats.
    """

    sidecar_path = sidecar_path_for_asset(asset_path)
    if not sidecar_path.exists():
        return {}

    try:
        tree = ET.parse(sidecar_path)
    except ET.ParseError:
        return {}
    root = tree.getroot()
    if root.tag != _SIDE_CAR_ROOT:
        return {}

    result: Dict[str, float | bool] = {}
    light_node = root.find(_LIGHT_NODE)
    if light_node is not None:
        master_element = light_node.find("Light_Master")
        if master_element is not None and master_element.text is not None:
            try:
                result["Light_Master"] = float(master_element.text.strip())
            except ValueError:
                result["Light_Master"] = 0.0
        enabled_element = light_node.find("Light_Enabled")
        if enabled_element is not None and enabled_element.text is not None:
            text = enabled_element.text.strip().lower()
            result["Light_Enabled"] = text in {"1", "true", "yes", "on"}
        else:
            result["Light_Enabled"] = True
        for key in LIGHT_KEYS:
            element = light_node.find(key)
            if element is None or element.text is None:
                continue
            try:
                result[key] = float(element.text.strip())
            except ValueError:
                continue
        color_master = light_node.find("Color_Master")
        if color_master is not None and color_master.text is not None:
            try:
                result["Color_Master"] = float(color_master.text.strip())
            except ValueError:
                result["Color_Master"] = 0.0
        color_enabled = light_node.find("Color_Enabled")
        if color_enabled is not None and color_enabled.text is not None:
            text = color_enabled.text.strip().lower()
            result["Color_Enabled"] = text in {"1", "true", "yes", "on"}
        else:
            result["Color_Enabled"] = True
        for key in COLOR_KEYS:
            element = light_node.find(key)
            if element is None or element.text is None:
                continue
            try:
                result[key] = float(element.text.strip())
            except ValueError:
                continue
        bw_enabled = light_node.find("BW_Enabled")
        if bw_enabled is not None and bw_enabled.text is not None:
            text = bw_enabled.text.strip().lower()
            result["BW_Enabled"] = text in {"1", "true", "yes", "on"}
        else:
            result["BW_Enabled"] = False

        for key in BW_KEYS:
            element = light_node.find(key)
            if element is None or element.text is None:
                continue
            try:
                result[key] = float(element.text.strip())
            except ValueError:
                continue

    crop_node = _find_child_case_insensitive(root, _CROP_NODE)
    if crop_node is None:
        crop_node = root.find(_LEGACY_CROP_NODE)
    if crop_node is not None:
        if any(child.tag.lower() == _CROP_CHILD_X for child in crop_node):
            result.update(_read_crop_from_node(crop_node))
        else:
            result.update(_read_crop_from_legacy_attributes(crop_node))

    return result


def save_adjustments(asset_path: Path, adjustments: Mapping[str, float | bool]) -> Path:
    """Persist *adjustments* next to *asset_path* and return the sidecar path."""

    sidecar_path = sidecar_path_for_asset(asset_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    root = _load_or_create_root(sidecar_path)
    _remove_children_case_insensitive(root, _LIGHT_NODE)
    light = ET.SubElement(root, _LIGHT_NODE)
    master_element = ET.SubElement(light, "Light_Master")
    master_value = float(adjustments.get("Light_Master", 0.0))
    master_element.text = f"{master_value:.2f}"

    enabled_element = ET.SubElement(light, "Light_Enabled")
    enabled = bool(adjustments.get("Light_Enabled", True))
    enabled_element.text = "true" if enabled else "false"

    for key in LIGHT_KEYS:
        value = float(adjustments.get(key, 0.0))
        child = ET.SubElement(light, key)
        child.text = f"{value:.2f}"

    color_master = ET.SubElement(light, "Color_Master")
    color_master_value = float(adjustments.get("Color_Master", 0.0))
    color_master.text = f"{color_master_value:.2f}"

    color_enabled = ET.SubElement(light, "Color_Enabled")
    color_enabled_value = bool(adjustments.get("Color_Enabled", True))
    color_enabled.text = "true" if color_enabled_value else "false"

    for key in COLOR_KEYS:
        value = float(adjustments.get(key, 0.0))
        child = ET.SubElement(light, key)
        child.text = f"{value:.2f}"

    bw_enabled = ET.SubElement(light, "BW_Enabled")
    bw_enabled_value = bool(adjustments.get("BW_Enabled", False))
    bw_enabled.text = "true" if bw_enabled_value else "false"

    for key in BW_KEYS:
        # Only normalize Master and Intensity to [0, 1]. Neutrals and Tone use [-1, 1].
        # Grain uses [0, 1] but doesn't need legacy normalization.
        raw = float(adjustments.get(key, BW_DEFAULTS.get(key, 0.0)))
        if key in ("BW_Master", "BW_Intensity"):
            value = _normalise_bw_value(key, raw)
        elif key == "BW_Grain":
            value = max(0.0, min(1.0, raw))
        else:
            # Neutrals and Tone: clamp to [-1, 1] for safety but don't normalize to [0, 1]
            value = max(-1.0, min(1.0, raw))
        child = ET.SubElement(light, key)
        child.text = f"{value:.2f}"

    _write_crop_node(root, adjustments)

    tmp_path = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    tree = ET.ElementTree(root)
    tree.write(tmp_path, encoding="utf-8", xml_declaration=True)

    try:
        tmp_path.replace(sidecar_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return sidecar_path


def resolve_render_adjustments(
    adjustments: Mapping[str, float | bool] | None,
    *,
    color_stats: ColorStats | None = None,
) -> Dict[str, float]:
    """Return Light adjustments suitable for rendering pipelines.

    ``load_adjustments`` exposes the raw session values, which now contain the master slider
    (`Light_Master`) and enable toggle (`Light_Enabled`) alongside the seven per-control deltas.
    Rendering helpers expect the final per-slider values rather than the stored deltas, so the
    helpers outside the edit session must resolve the vector before handing it to
    :func:`apply_adjustments`.
    """

    if not adjustments:
        return {}

    try:
        master_value = float(adjustments.get("Light_Master", 0.0))
    except (TypeError, ValueError):
        master_value = 0.0

    light_enabled = bool(adjustments.get("Light_Enabled", True))

    resolved: Dict[str, float] = {}
    overrides: Dict[str, float] = {}
    color_overrides: Dict[str, float] = {}
    for key, value in adjustments.items():
        if key in ("Light_Master", "Light_Enabled"):
            continue
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if key in LIGHT_KEYS:
            overrides[key] = numeric_value
        elif key in COLOR_KEYS:
            color_overrides[key] = numeric_value
        elif key == "BW_Master":
            continue
        else:
            resolved[key] = numeric_value

    if light_enabled:
        light_values = resolve_light_vector(master_value, overrides, mode="delta")
    else:
        light_values = {key: 0.0 for key in LIGHT_KEYS}
    resolved.update(light_values)
    stats = color_stats or ColorStats()
    color_master = float(adjustments.get("Color_Master", 0.0))
    color_enabled = bool(adjustments.get("Color_Enabled", True))
    if color_enabled:
        color_values = ColorResolver.resolve_color_vector(
            color_master,
            color_overrides,
            stats=stats,
            mode="delta",
        )
    else:
        color_values = {key: 0.0 for key in COLOR_KEYS}
    gain_r, gain_g, gain_b = stats.white_balance_gain
    resolved.update(color_values)
    resolved["Color_Gain_R"] = gain_r
    resolved["Color_Gain_G"] = gain_g
    resolved["Color_Gain_B"] = gain_b

    bw_enabled = bool(adjustments.get("BW_Enabled", False))
    if bw_enabled:
        # BWIntensity is stored as [0, 1] (0.5 Neutral) but Shader expects [-1, 1] (0.0 Neutral).
        norm_intensity = _normalise_bw_value("BW_Intensity", float(adjustments.get("BW_Intensity", 0.5)))
        resolved["BWIntensity"] = norm_intensity * 2.0 - 1.0

        # BWNeutrals/Tone are stored as [-1, 1]. Shader expects [-1, 1].
        resolved["BWNeutrals"] = float(adjustments.get("BW_Neutrals", 0.0))
        resolved["BWTone"] = float(adjustments.get("BW_Tone", 0.0))

        # BWGrain is stored as [0, 1]. Shader expects [0, 1].
        resolved["BWGrain"] = max(0.0, min(1.0, float(adjustments.get("BW_Grain", 0.0))))
    else:
        # Defaults mapped to shader range:
        # Intensity 0.5 (Neutral) -> 0.0
        # Neutrals 0.0 (Neutral) -> 0.0
        # Tone 0.0 (Neutral) -> 0.0
        resolved["BWIntensity"] = 0.0
        resolved["BWNeutrals"] = 0.0
        resolved["BWTone"] = 0.0
        resolved["BWGrain"] = 0.0

    return resolved

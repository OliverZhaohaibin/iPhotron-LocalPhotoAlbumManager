"""Read/write helpers for ``.ipo`` XML sidecar files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping
import xml.etree.ElementTree as ET

from ..core.light_resolver import LIGHT_KEYS, resolve_light_vector
from ..core.color_resolver import COLOR_KEYS, ColorResolver, ColorStats
from ..core.selective_color_resolver import NUM_RANGES
from ..core.wb_resolver import WB_KEYS, WB_DEFAULTS

from .sidecar_sections import (
    _find_child_case_insensitive,
    _remove_children_case_insensitive,
    _read_crop_from_node,
    _read_crop_from_legacy_attributes,
    _write_crop_node,
    _read_curve_from_node,
    _write_curve_node,
    _read_levels_from_node,
    _write_levels_node,
    _read_selective_color_from_node,
    _write_selective_color_node,
    _read_definition_from_node,
    _write_definition_node,
    _read_denoise_from_node,
    _write_denoise_node,
    _read_vignette_from_node,
    _write_vignette_node,
    _CROP_NODE,
    _CROP_CHILD_X,
    _LEGACY_CROP_NODE,
    _CURVE_NODE,
    _LEVELS_NODE,
    _SELECTIVE_COLOR_NODE,
    _DEFINITION_NODE,
    _DENOISE_NODE,
    _VIGNETTE_NODE,
)

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
_VERSION_ATTR = "version"
_CURRENT_VERSION = "1.0"


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


def sidecar_path_for_asset(asset_path: Path) -> Path:
    """Return the expected sidecar path for *asset_path*."""

    return asset_path.with_suffix(".ipo")


def load_adjustments(asset_path: Path) -> Dict[str, Any]:
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

    result: Dict[str, Any] = {}
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

        wb_enabled = light_node.find("WB_Enabled")
        if wb_enabled is not None and wb_enabled.text is not None:
            text = wb_enabled.text.strip().lower()
            result["WB_Enabled"] = text in {"1", "true", "yes", "on"}
        else:
            result["WB_Enabled"] = False

        for key in WB_KEYS:
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

    # Load curve adjustments
    curve_node = _find_child_case_insensitive(root, _CURVE_NODE)
    if curve_node is not None:
        result.update(_read_curve_from_node(curve_node))

    # Load levels adjustments
    levels_node = _find_child_case_insensitive(root, _LEVELS_NODE)
    if levels_node is not None:
        result.update(_read_levels_from_node(levels_node))

    # Load Selective Color adjustments
    sc_node = _find_child_case_insensitive(root, _SELECTIVE_COLOR_NODE)
    if sc_node is not None:
        result.update(_read_selective_color_from_node(sc_node))

    # Load Definition adjustments
    def_node = _find_child_case_insensitive(root, _DEFINITION_NODE)
    if def_node is not None:
        result.update(_read_definition_from_node(def_node))

    # Load Denoise adjustments
    dn_node = _find_child_case_insensitive(root, _DENOISE_NODE)
    if dn_node is not None:
        result.update(_read_denoise_from_node(dn_node))

    # Load Vignette adjustments
    vig_node = _find_child_case_insensitive(root, _VIGNETTE_NODE)
    if vig_node is not None:
        result.update(_read_vignette_from_node(vig_node))

    return result


def save_adjustments(asset_path: Path, adjustments: Mapping[str, Any]) -> Path:
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

    wb_enabled_el = ET.SubElement(light, "WB_Enabled")
    wb_enabled_val = bool(adjustments.get("WB_Enabled", False))
    wb_enabled_el.text = "true" if wb_enabled_val else "false"

    for key in WB_KEYS:
        raw = float(adjustments.get(key, WB_DEFAULTS.get(key, 0.0)))
        value = max(-1.0, min(1.0, raw))
        child = ET.SubElement(light, key)
        child.text = f"{value:.2f}"

    _write_crop_node(root, adjustments)

    # Write curve adjustments
    _write_curve_node(root, adjustments)

    # Write levels adjustments
    _write_levels_node(root, adjustments)

    # Write Selective Color adjustments
    _write_selective_color_node(root, adjustments)

    # Write Definition adjustments
    _write_definition_node(root, adjustments)

    # Write Denoise adjustments
    _write_denoise_node(root, adjustments)

    # Write Vignette adjustments
    _write_vignette_node(root, adjustments)

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
    adjustments: Mapping[str, Any] | None,
    *,
    color_stats: ColorStats | None = None,
) -> Dict[str, Any]:
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

    resolved: Dict[str, Any] = {}
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

    # White Balance adjustments
    wb_enabled = bool(adjustments.get("WB_Enabled", False))
    resolved["WB_Enabled"] = wb_enabled
    if wb_enabled:
        resolved["WBWarmth"] = max(-1.0, min(1.0, float(adjustments.get("WB_Warmth", 0.0))))
        resolved["WBTemperature"] = max(-1.0, min(1.0, float(adjustments.get("WB_Temperature", 0.0))))
        resolved["WBTint"] = max(-1.0, min(1.0, float(adjustments.get("WB_Tint", 0.0))))
    else:
        resolved["WBWarmth"] = 0.0
        resolved["WBTemperature"] = 0.0
        resolved["WBTint"] = 0.0

    # Curve adjustments - pass through to renderer as-is
    curve_enabled = bool(adjustments.get("Curve_Enabled", False))
    resolved["Curve_Enabled"] = curve_enabled
    if curve_enabled:
        # Pass curve data for LUT generation
        for key in ("Curve_RGB", "Curve_Red", "Curve_Green", "Curve_Blue"):
            curve_data = adjustments.get(key)
            if curve_data and isinstance(curve_data, list):
                resolved[key] = curve_data

    # Levels adjustments - pass through to renderer as-is
    levels_enabled = bool(adjustments.get("Levels_Enabled", False))
    resolved["Levels_Enabled"] = levels_enabled
    if levels_enabled:
        handles = adjustments.get("Levels_Handles")
        if isinstance(handles, list) and len(handles) == 5:
            resolved["Levels_Handles"] = handles

    # Selective Color adjustments - pass through to renderer as-is
    sc_enabled = bool(adjustments.get("SelectiveColor_Enabled", False))
    resolved["SelectiveColor_Enabled"] = sc_enabled
    if sc_enabled:
        sc_ranges = adjustments.get("SelectiveColor_Ranges")
        if isinstance(sc_ranges, list) and len(sc_ranges) == NUM_RANGES:
            resolved["SelectiveColor_Ranges"] = sc_ranges

    # Definition adjustments - pass through to renderer as-is
    def_enabled = bool(adjustments.get("Definition_Enabled", False))
    resolved["Definition_Enabled"] = def_enabled
    if def_enabled:
        resolved["Definition_Value"] = max(0.0, min(1.0, float(adjustments.get("Definition_Value", 0.0))))

    # Denoise adjustments - pass through to renderer as-is
    dn_enabled = bool(adjustments.get("Denoise_Enabled", False))
    resolved["Denoise_Enabled"] = dn_enabled
    if dn_enabled:
        resolved["Denoise_Amount"] = max(0.0, min(5.0, float(adjustments.get("Denoise_Amount", 0.0))))

    # Vignette adjustments - pass through to renderer as-is
    vig_enabled = bool(adjustments.get("Vignette_Enabled", False))
    resolved["Vignette_Enabled"] = vig_enabled
    if vig_enabled:
        resolved["Vignette_Strength"] = max(0.0, min(1.0, float(adjustments.get("Vignette_Strength", 0.0))))
        resolved["Vignette_Radius"] = max(0.0, min(1.0, float(adjustments.get("Vignette_Radius", 0.50))))
        resolved["Vignette_Softness"] = max(0.0, min(1.0, float(adjustments.get("Vignette_Softness", 0.0))))

    return resolved

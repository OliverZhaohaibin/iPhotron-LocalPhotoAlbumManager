"""Read/write helpers for ``.ipo`` XML sidecar files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping
import xml.etree.ElementTree as ET

from ..core.adjustment_mapping import (
    BW_DEFAULTS,
    BW_KEYS,
    VIDEO_TRIM_IN_KEY,
    VIDEO_TRIM_OUT_KEY,
    has_non_default_adjustments,
    normalise_bw_value,
    normalise_video_trim,
    resolve_adjustment_mapping,
    trim_is_non_default,
    video_has_visible_edits,
    video_requires_adjusted_preview,
)
from ..core.color_resolver import ColorStats
from ..core.color_resolver import COLOR_KEYS
from ..core.light_resolver import LIGHT_KEYS
from ..core.wb_resolver import WB_DEFAULTS, WB_KEYS

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
    _read_sharpen_from_node,
    _write_sharpen_node,
    _read_vignette_from_node,
    _write_vignette_node,
    _read_video_from_node,
    _write_video_node,
    _CROP_NODE,
    _CROP_CHILD_X,
    _LEGACY_CROP_NODE,
    _CURVE_NODE,
    _LEVELS_NODE,
    _SELECTIVE_COLOR_NODE,
    _DEFINITION_NODE,
    _DENOISE_NODE,
    _SHARPEN_NODE,
    _VIGNETTE_NODE,
    _VIDEO_NODE,
)

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

    # Load Sharpen adjustments
    sh_node = _find_child_case_insensitive(root, _SHARPEN_NODE)
    if sh_node is not None:
        result.update(_read_sharpen_from_node(sh_node))

    # Load Vignette adjustments
    vig_node = _find_child_case_insensitive(root, _VIGNETTE_NODE)
    if vig_node is not None:
        result.update(_read_vignette_from_node(vig_node))

    video_node = _find_child_case_insensitive(root, _VIDEO_NODE)
    if video_node is not None:
        result.update(_read_video_from_node(video_node))

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
            value = normalise_bw_value(key, raw)
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

    # Write Sharpen adjustments
    _write_sharpen_node(root, adjustments)

    # Write Vignette adjustments
    _write_vignette_node(root, adjustments)

    # Write video trim adjustments
    _write_video_node(root, adjustments)

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
    """Return session adjustments resolved into render/export semantics."""

    return resolve_adjustment_mapping(
        adjustments,
        stats=color_stats,
        bool_as_float=False,
        normalize_bw_for_render=True,
    )


__all__ = [
    "VIDEO_TRIM_IN_KEY",
    "VIDEO_TRIM_OUT_KEY",
    "has_non_default_adjustments",
    "load_adjustments",
    "normalise_video_trim",
    "resolve_render_adjustments",
    "save_adjustments",
    "sidecar_path_for_asset",
    "video_requires_adjusted_preview",
    "trim_is_non_default",
    "video_has_visible_edits",
]

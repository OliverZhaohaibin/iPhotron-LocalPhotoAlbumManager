"""Shared helpers for non-destructive adjustment state and video trim logic."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from .color_resolver import COLOR_KEYS, ColorResolver, ColorStats
from .curve_resolver import DEFAULT_CURVE_POINTS
from .definition_resolver import DEFAULT_DEFINITION
from .denoise_resolver import DEFAULT_DENOISE
from .levels_resolver import DEFAULT_LEVELS_HANDLES
from .light_resolver import LIGHT_KEYS, resolve_light_vector
from .selective_color_resolver import (
    DEFAULT_SELECTIVE_COLOR_RANGES,
    NUM_RANGES,
)
from .sharpen_resolver import (
    DEFAULT_SHARPEN_EDGES,
    DEFAULT_SHARPEN_FALLOFF,
    DEFAULT_SHARPEN_INTENSITY,
)
from .vignette_resolver import (
    DEFAULT_VIGNETTE_RADIUS,
    DEFAULT_VIGNETTE_SOFTNESS,
    DEFAULT_VIGNETTE_STRENGTH,
)
from .wb_resolver import WB_DEFAULTS, WB_KEYS

VIDEO_TRIM_IN_KEY = "Video_Trim_In_Sec"
VIDEO_TRIM_OUT_KEY = "Video_Trim_Out_Sec"

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

CURVE_LIST_KEYS = {"Curve_RGB", "Curve_Red", "Curve_Green", "Curve_Blue"}
LIST_KEYS = CURVE_LIST_KEYS | {"Levels_Handles", "SelectiveColor_Ranges"}


def default_adjustment_values() -> dict[str, Any]:
    """Return the canonical default edit-session values."""

    values: dict[str, Any] = {
        "Light_Master": 0.0,
        "Light_Enabled": True,
        "Color_Master": 0.0,
        "Color_Enabled": True,
        "BW_Master": 0.5,
        "BW_Enabled": False,
        "BW_Intensity": 0.5,
        "BW_Neutrals": 0.0,
        "BW_Tone": 0.0,
        "BW_Grain": 0.0,
        "Crop_CX": 0.5,
        "Crop_CY": 0.5,
        "Crop_W": 1.0,
        "Crop_H": 1.0,
        "Perspective_Vertical": 0.0,
        "Perspective_Horizontal": 0.0,
        "Crop_Straighten": 0.0,
        "Crop_Rotate90": 0.0,
        "Crop_FlipH": False,
        "WB_Enabled": False,
        "WB_Warmth": 0.0,
        "WB_Temperature": 0.0,
        "WB_Tint": 0.0,
        "Curve_Enabled": False,
        "Curve_RGB": list(DEFAULT_CURVE_POINTS),
        "Curve_Red": list(DEFAULT_CURVE_POINTS),
        "Curve_Green": list(DEFAULT_CURVE_POINTS),
        "Curve_Blue": list(DEFAULT_CURVE_POINTS),
        "Levels_Enabled": False,
        "Levels_Handles": list(DEFAULT_LEVELS_HANDLES),
        "SelectiveColor_Enabled": False,
        "SelectiveColor_Ranges": [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
        "Definition_Enabled": False,
        "Definition_Value": DEFAULT_DEFINITION,
        "Denoise_Enabled": False,
        "Denoise_Amount": DEFAULT_DENOISE,
        "Sharpen_Enabled": False,
        "Sharpen_Intensity": DEFAULT_SHARPEN_INTENSITY,
        "Sharpen_Edges": DEFAULT_SHARPEN_EDGES,
        "Sharpen_Falloff": DEFAULT_SHARPEN_FALLOFF,
        "Vignette_Enabled": False,
        "Vignette_Strength": DEFAULT_VIGNETTE_STRENGTH,
        "Vignette_Radius": DEFAULT_VIGNETTE_RADIUS,
        "Vignette_Softness": DEFAULT_VIGNETTE_SOFTNESS,
        VIDEO_TRIM_IN_KEY: 0.0,
        VIDEO_TRIM_OUT_KEY: 0.0,
    }
    values.update({key: 0.0 for key in LIGHT_KEYS})
    values.update({key: 0.0 for key in COLOR_KEYS})
    return values


def normalise_bw_value(key: str, value: float) -> float:
    """Return *value* mapped into the persisted B&W range."""

    numeric = float(value)
    if key in _BW_RANGE_KEYS and (numeric < 0.0 or numeric > 1.0):
        numeric = (numeric + 1.0) * 0.5
    return max(0.0, min(1.0, numeric))


def resolve_adjustment_mapping(
    session_values: Mapping[str, Any] | None,
    *,
    stats: ColorStats | None = None,
    bool_as_float: bool = False,
    normalize_bw_for_render: bool = False,
) -> dict[str, Any]:
    """Return a renderer-friendly adjustment mapping.

    Parameters
    ----------
    bool_as_float:
        When ``True`` stores booleans as ``0.0``/``1.0`` for shader callers
        that prefer numeric uniform values.
    normalize_bw_for_render:
        When ``True`` maps the Black & White intensity control from the session
        range into the shader/export range where ``0.0`` is neutral.
    """

    if not session_values:
        return {}

    resolved: dict[str, Any] = {}
    overrides: dict[str, float] = {}
    color_overrides: dict[str, float] = {}
    list_values: dict[str, list] = {}

    master_value = _float_or_default(session_values.get("Light_Master"), 0.0)
    light_enabled = bool(session_values.get("Light_Enabled", True))
    color_master = _float_or_default(session_values.get("Color_Master"), 0.0)
    color_enabled = bool(session_values.get("Color_Enabled", True))

    for key, value in session_values.items():
        if key in ("Light_Master", "Light_Enabled", "Color_Master", "Color_Enabled"):
            continue
        if key == "BW_Master":
            continue
        if key in LIST_KEYS:
            if isinstance(value, list):
                list_values[key] = value
            continue
        if key in LIGHT_KEYS:
            overrides[key] = _float_or_default(value, 0.0)
        elif key in COLOR_KEYS:
            color_overrides[key] = _float_or_default(value, 0.0)
        elif isinstance(value, bool):
            resolved[key] = 1.0 if value and bool_as_float else (0.0 if bool_as_float else value)
        else:
            numeric = _coerce_numeric(value)
            if numeric is not None:
                resolved[key] = numeric

    if light_enabled:
        resolved.update(resolve_light_vector(master_value, overrides, mode="delta"))
    else:
        resolved.update({key: 0.0 for key in LIGHT_KEYS})

    stats_obj = stats or ColorStats()
    if color_enabled:
        resolved.update(
            ColorResolver.resolve_color_vector(
                color_master,
                color_overrides,
                stats=stats_obj,
                mode="delta",
            )
        )
    else:
        resolved.update({key: 0.0 for key in COLOR_KEYS})

    gain_r, gain_g, gain_b = stats_obj.white_balance_gain
    resolved["Color_Gain_R"] = float(gain_r)
    resolved["Color_Gain_G"] = float(gain_g)
    resolved["Color_Gain_B"] = float(gain_b)

    bw_enabled = bool(session_values.get("BW_Enabled", False))
    resolved["BW_Enabled"] = _bool_value(bw_enabled, bool_as_float=bool_as_float)
    resolved["BWEnabled"] = 1.0 if bw_enabled else 0.0
    if bw_enabled:
        bw_intensity = normalise_bw_value(
            "BW_Intensity",
            _float_or_default(session_values.get("BW_Intensity"), BW_DEFAULTS["BW_Intensity"]),
        )
        resolved["BWIntensity"] = (bw_intensity * 2.0 - 1.0) if normalize_bw_for_render else bw_intensity
        resolved["BWNeutrals"] = max(-1.0, min(1.0, _float_or_default(session_values.get("BW_Neutrals"), 0.0)))
        resolved["BWTone"] = max(-1.0, min(1.0, _float_or_default(session_values.get("BW_Tone"), 0.0)))
        resolved["BWGrain"] = max(0.0, min(1.0, _float_or_default(session_values.get("BW_Grain"), 0.0)))
    else:
        resolved["BWIntensity"] = 0.0 if normalize_bw_for_render else 0.5
        resolved["BWNeutrals"] = 0.0
        resolved["BWTone"] = 0.0
        resolved["BWGrain"] = 0.0

    wb_enabled = bool(session_values.get("WB_Enabled", False))
    resolved["WB_Enabled"] = _bool_value(wb_enabled, bool_as_float=bool_as_float)
    resolved["WBEnabled"] = 1.0 if wb_enabled else 0.0
    if wb_enabled:
        resolved["WBWarmth"] = max(-1.0, min(1.0, _float_or_default(session_values.get("WB_Warmth"), 0.0)))
        resolved["WBTemperature"] = max(-1.0, min(1.0, _float_or_default(session_values.get("WB_Temperature"), 0.0)))
        resolved["WBTint"] = max(-1.0, min(1.0, _float_or_default(session_values.get("WB_Tint"), 0.0)))
    else:
        resolved["WBWarmth"] = 0.0
        resolved["WBTemperature"] = 0.0
        resolved["WBTint"] = 0.0

    curve_enabled = bool(session_values.get("Curve_Enabled", False))
    levels_enabled = bool(session_values.get("Levels_Enabled", False))
    sc_enabled = bool(session_values.get("SelectiveColor_Enabled", False))
    def_enabled = bool(session_values.get("Definition_Enabled", False))
    dn_enabled = bool(session_values.get("Denoise_Enabled", False))
    sh_enabled = bool(session_values.get("Sharpen_Enabled", False))
    vig_enabled = bool(session_values.get("Vignette_Enabled", False))

    resolved["Curve_Enabled"] = _bool_value(curve_enabled, bool_as_float=bool_as_float)
    resolved["Levels_Enabled"] = _bool_value(levels_enabled, bool_as_float=bool_as_float)
    resolved["SelectiveColor_Enabled"] = _bool_value(sc_enabled, bool_as_float=bool_as_float)
    resolved["Definition_Enabled"] = _bool_value(def_enabled, bool_as_float=bool_as_float)
    resolved["Denoise_Enabled"] = _bool_value(dn_enabled, bool_as_float=bool_as_float)
    resolved["Sharpen_Enabled"] = _bool_value(sh_enabled, bool_as_float=bool_as_float)
    resolved["Vignette_Enabled"] = _bool_value(vig_enabled, bool_as_float=bool_as_float)

    if def_enabled:
        resolved["Definition_Value"] = max(0.0, min(1.0, _float_or_default(session_values.get("Definition_Value"), 0.0)))
    elif "Definition_Value" in session_values and not bool_as_float:
        resolved["Definition_Value"] = _float_or_default(session_values.get("Definition_Value"), 0.0)

    if dn_enabled:
        resolved["Denoise_Amount"] = max(0.0, min(5.0, _float_or_default(session_values.get("Denoise_Amount"), 0.0)))
    elif "Denoise_Amount" in session_values and not bool_as_float:
        resolved["Denoise_Amount"] = _float_or_default(session_values.get("Denoise_Amount"), 0.0)

    if sh_enabled:
        resolved["Sharpen_Intensity"] = max(0.0, min(1.0, _float_or_default(session_values.get("Sharpen_Intensity"), 0.0)))
        resolved["Sharpen_Edges"] = max(0.0, min(1.0, _float_or_default(session_values.get("Sharpen_Edges"), 0.0)))
        resolved["Sharpen_Falloff"] = max(0.0, min(1.0, _float_or_default(session_values.get("Sharpen_Falloff"), 0.0)))
    elif not bool_as_float:
        for key in ("Sharpen_Intensity", "Sharpen_Edges", "Sharpen_Falloff"):
            if key in session_values:
                resolved[key] = _float_or_default(session_values.get(key), 0.0)

    if vig_enabled:
        resolved["Vignette_Strength"] = max(0.0, min(1.0, _float_or_default(session_values.get("Vignette_Strength"), 0.0)))
        resolved["Vignette_Radius"] = max(0.0, min(1.0, _float_or_default(session_values.get("Vignette_Radius"), 0.5)))
        resolved["Vignette_Softness"] = max(0.0, min(1.0, _float_or_default(session_values.get("Vignette_Softness"), 0.0)))
    elif not bool_as_float:
        for key in ("Vignette_Strength", "Vignette_Radius", "Vignette_Softness"):
            if key in session_values:
                resolved[key] = _float_or_default(session_values.get(key), 0.0)

    if curve_enabled:
        for key in CURVE_LIST_KEYS:
            curve_data = list_values.get(key)
            if curve_data is not None:
                resolved[key] = curve_data
    if levels_enabled:
        handles = list_values.get("Levels_Handles")
        if isinstance(handles, list) and len(handles) == 5:
            resolved["Levels_Handles"] = handles
    if sc_enabled:
        ranges = list_values.get("SelectiveColor_Ranges")
        if isinstance(ranges, list) and len(ranges) == NUM_RANGES:
            resolved["SelectiveColor_Ranges"] = ranges

    if bool_as_float:
        resolved.update(list_values)
    else:
        for key in (VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY):
            if key in session_values:
                numeric = _coerce_numeric(session_values.get(key))
                if numeric is not None:
                    resolved[key] = numeric

    return resolved


def normalise_video_trim(
    adjustments: Mapping[str, Any] | None,
    duration_sec: float | None,
) -> tuple[float, float]:
    """Return a safe ``(trim_in, trim_out)`` pair in seconds."""

    duration = _positive_or_none(duration_sec)
    trim_in = _float_or_default(
        adjustments.get(VIDEO_TRIM_IN_KEY) if adjustments else None,
        0.0,
    )
    trim_out_default = duration if duration is not None else max(trim_in, 0.0)
    # 0.0 is the in-memory "full duration" sentinel; treat it the same as
    # a missing value so that persisted sentinel values in old sidecar files
    # don't corrupt the trim range on reload.
    _raw_trim_out = adjustments.get(VIDEO_TRIM_OUT_KEY) if adjustments else None
    _positive_trim_out = _positive_or_none(_raw_trim_out)
    trim_out = _float_or_default(_positive_trim_out, trim_out_default)

    trim_in = max(0.0, trim_in)
    trim_out = max(0.0, trim_out)
    if duration is not None:
        trim_in = min(trim_in, duration)
        trim_out = min(trim_out, duration)
        if trim_out <= trim_in:
            return (0.0, duration)
        return (trim_in, trim_out)
    if trim_out <= trim_in:
        return (0.0, max(trim_out_default, trim_in, 0.0))
    return (trim_in, trim_out)


def trim_is_non_default(
    adjustments: Mapping[str, Any] | None,
    duration_sec: float | None,
) -> bool:
    """Return ``True`` when the stored trim differs from the full duration."""

    duration = _positive_or_none(duration_sec)
    if duration is None:
        return any(
            _positive_or_none((adjustments or {}).get(key)) is not None
            for key in (VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY)
        )
    trim_in, trim_out = normalise_video_trim(adjustments, duration)
    return abs(trim_in) > 1e-6 or abs(trim_out - duration) > 1e-6


def has_non_default_adjustments(adjustments: Mapping[str, Any] | None) -> bool:
    """Return ``True`` when *adjustments* differ from the edit defaults."""

    if not adjustments:
        return False

    defaults = default_adjustment_values()
    for key, value in adjustments.items():
        if key in (VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY):
            continue
        if key not in defaults:
            continue
        default = defaults[key]
        if key in LIST_KEYS:
            if list(value) != list(default):
                return True
            continue
        if isinstance(default, bool):
            if bool(value) != default:
                return True
            continue
        numeric = _coerce_numeric(value)
        if numeric is None:
            continue
        if abs(numeric - float(default)) > 1e-6:
            return True
    return False


def video_requires_adjusted_preview(adjustments: Mapping[str, Any] | None) -> bool:
    """Return ``True`` when video playback must use the GL adjusted preview.

    Pure 90° video rotation can be handled directly by the native
    ``VideoRendererWidget`` path, which keeps playback framing aligned with the
    standard video surface across platforms. All other non-default adjustments
    still require the adjusted GL preview.
    """

    if not adjustments:
        return False

    filtered = {
        key: value
        for key, value in adjustments.items()
        if key != "Crop_Rotate90"
    }
    return has_non_default_adjustments(filtered)


def video_has_visible_edits(
    adjustments: Mapping[str, Any] | None,
    duration_sec: float | None,
) -> bool:
    """Return ``True`` when a video has non-default trim or adjustments."""

    return has_non_default_adjustments(adjustments) or trim_is_non_default(adjustments, duration_sec)


def _bool_value(value: bool, *, bool_as_float: bool) -> bool | float:
    if bool_as_float:
        return 1.0 if value else 0.0
    return value


def _coerce_numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: Any, default: float) -> float:
    numeric = _coerce_numeric(value)
    if numeric is None:
        return float(default)
    return float(numeric)


def _positive_or_none(value: Any) -> float | None:
    numeric = _coerce_numeric(value)
    if numeric is None or not math.isfinite(numeric) or numeric <= 0.0:
        return None
    return float(numeric)


__all__ = [
    "BW_DEFAULTS",
    "BW_KEYS",
    "CURVE_LIST_KEYS",
    "LIST_KEYS",
    "VIDEO_TRIM_IN_KEY",
    "VIDEO_TRIM_OUT_KEY",
    "default_adjustment_values",
    "has_non_default_adjustments",
    "normalise_bw_value",
    "normalise_video_trim",
    "resolve_adjustment_mapping",
    "video_requires_adjusted_preview",
    "trim_is_non_default",
    "video_has_visible_edits",
]

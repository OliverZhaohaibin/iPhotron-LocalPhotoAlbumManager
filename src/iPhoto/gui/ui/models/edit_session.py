"""State container for the non-destructive editing workflow."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from PySide6.QtCore import QObject, Signal

from ....core.light_resolver import LIGHT_KEYS
from ....core.color_resolver import COLOR_KEYS, COLOR_RANGES, ColorStats
from ....core.curve_resolver import DEFAULT_CURVE_POINTS
from ....core.levels_resolver import DEFAULT_LEVELS_HANDLES
from ....core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES

_BW_RANGE_KEYS = {"BW_Master", "BW_Intensity", "BW_Neutrals", "BW_Tone"}

# Keys that store list data (curve control points, levels handles) instead of floats
_CURVE_LIST_KEYS = {"Curve_RGB", "Curve_Red", "Curve_Green", "Curve_Blue"}
_LIST_KEYS = _CURVE_LIST_KEYS | {"Levels_Handles", "SelectiveColor_Ranges"}


def _coerce_bw_range(key: str, value: float) -> float:
    """Map legacy ``[-1, 1]`` values into the new ``[0, 1]`` range."""

    if key in _BW_RANGE_KEYS and (value < 0.0 or value > 1.0):
        return (float(value) + 1.0) * 0.5
    return float(value)


class EditSession(QObject):
    """Hold the adjustment values for the active editing session."""

    valueChanged = Signal(str, object)
    """Emitted when a single adjustment changes."""

    valuesChanged = Signal(dict)
    """Emitted after one or more adjustments have been updated."""

    resetPerformed = Signal()
    """Emitted when :meth:`reset` restores every adjustment to its default."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._values: "OrderedDict[str, float | bool | List[Tuple[float, float]]]" = OrderedDict()
        self._ranges: dict[str, tuple[float, float]] = {}
        self._color_stats: ColorStats | None = None
        # The master slider value feeds the resolver that generates the derived light adjustments.
        self._values["Light_Master"] = 0.0
        self._ranges["Light_Master"] = (-1.0, 1.0)
        # ``Light_Enabled`` toggles whether the resolved adjustments should be applied.  Storing the
        # state alongside the numeric adjustments keeps the session serialisable through
        # :meth:`values` without coordinating multiple containers.
        self._values["Light_Enabled"] = True
        self._ranges["Light_Enabled"] = (-1.0, 1.0)
        for key in LIGHT_KEYS:
            self._values[key] = 0.0
            self._ranges[key] = (-1.0, 1.0)

        self._values["Color_Master"] = 0.0
        self._ranges["Color_Master"] = (-1.0, 1.0)
        self._values["Color_Enabled"] = True
        self._ranges["Color_Enabled"] = (-1.0, 1.0)
        for key in COLOR_KEYS:
            self._values[key] = 0.0
            self._ranges[key] = (-1.0, 1.0)

        # ``BW_*`` parameters drive the GPU-only black & white pass.  The updated effect mirrors the
        # Photos-compatible shader that operates in the ``[0.0, 1.0]`` domain, so the persisted state
        # and all UI controls adopt the same bounds.  ``BW_Master`` stores the aggregate slider
        # position, while ``BW_Enabled`` mirrors the Light/Color toggles so the user can disable the
        # conversion without losing their tuned values.
        self._values["BW_Master"] = 0.5
        self._ranges["BW_Master"] = (0.0, 1.0)
        self._values["BW_Enabled"] = False
        self._ranges["BW_Enabled"] = (0.0, 1.0)
        self._values["BW_Intensity"] = 0.5
        self._ranges["BW_Intensity"] = (0.0, 1.0)
        self._values["BW_Neutrals"] = 0.0
        self._ranges["BW_Neutrals"] = (0.0, 1.0)
        self._values["BW_Tone"] = 0.0
        self._ranges["BW_Tone"] = (0.0, 1.0)
        self._values["BW_Grain"] = 0.0
        self._ranges["BW_Grain"] = (0.0, 1.0)

        # Cropping parameters are stored in normalised image space so the
        # session can persist non-destructive crop boxes alongside colour
        # adjustments.  ``Crop_*`` values are clamped to ``[0.0, 1.0]``.
        self._values["Crop_CX"] = 0.5
        self._ranges["Crop_CX"] = (0.0, 1.0)
        self._values["Crop_CY"] = 0.5
        self._ranges["Crop_CY"] = (0.0, 1.0)
        self._values["Crop_W"] = 1.0
        self._ranges["Crop_W"] = (0.0, 1.0)
        self._values["Crop_H"] = 1.0
        self._ranges["Crop_H"] = (0.0, 1.0)

        # Perspective correction sliders operate in a symmetric range so they
        # can skew in both directions.  The shader interprets the normalised
        # ``[-1.0, 1.0]`` inputs as ±20° rotations around the horizontal and
        # vertical axes.  Straighten and rotation controls operate in degrees so
        # the persisted state matches the user-facing UI exactly.
        self._values["Perspective_Vertical"] = 0.0
        self._ranges["Perspective_Vertical"] = (-1.0, 1.0)
        self._values["Perspective_Horizontal"] = 0.0
        self._ranges["Perspective_Horizontal"] = (-1.0, 1.0)
        self._values["Crop_Straighten"] = 0.0
        self._ranges["Crop_Straighten"] = (-45.0, 45.0)
        self._values["Crop_Rotate90"] = 0
        self._ranges["Crop_Rotate90"] = (0.0, 3.0)
        self._values["Crop_FlipH"] = False
        self._ranges["Crop_FlipH"] = (-1.0, 1.0)

        # White Balance parameters.
        self._values["WB_Enabled"] = False
        self._ranges["WB_Enabled"] = (0.0, 1.0)
        self._values["WB_Warmth"] = 0.0
        self._ranges["WB_Warmth"] = (-1.0, 1.0)
        self._values["WB_Temperature"] = 0.0
        self._ranges["WB_Temperature"] = (-1.0, 1.0)
        self._values["WB_Tint"] = 0.0
        self._ranges["WB_Tint"] = (-1.0, 1.0)

        # Curve adjustment parameters store control point lists for each channel.
        # ``Curve_Enabled`` toggles whether curve adjustments are applied.
        self._values["Curve_Enabled"] = False
        self._ranges["Curve_Enabled"] = (0.0, 1.0)
        # Each curve channel stores a list of (x, y) control points.
        # Default is identity: [(0.0, 0.0), (1.0, 1.0)]
        self._values["Curve_RGB"] = list(DEFAULT_CURVE_POINTS)
        self._values["Curve_Red"] = list(DEFAULT_CURVE_POINTS)
        self._values["Curve_Green"] = list(DEFAULT_CURVE_POINTS)
        self._values["Curve_Blue"] = list(DEFAULT_CURVE_POINTS)

        # Levels adjustment parameters store the 5 handle positions.
        # ``Levels_Enabled`` toggles whether levels adjustments are applied.
        self._values["Levels_Enabled"] = False
        self._ranges["Levels_Enabled"] = (0.0, 1.0)
        self._values["Levels_Handles"] = list(DEFAULT_LEVELS_HANDLES)

        # Selective Color parameters store per-range adjustment data for 6 hue
        # ranges.  Each range is a list [center_hue, range_slider, hue_shift,
        # sat_adj, lum_adj].
        self._values["SelectiveColor_Enabled"] = False
        self._ranges["SelectiveColor_Enabled"] = (0.0, 1.0)
        self._values["SelectiveColor_Ranges"] = [
            list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES
        ]

    # ------------------------------------------------------------------
    # Accessors
    def value(self, key: str) -> float | bool | List[Tuple[float, float]]:
        """Return the stored value for *key*, defaulting to ``0.0`` or ``False``."""

        default: Any = 0.0
        if key in _LIST_KEYS:
            if key == "Levels_Handles":
                default = list(DEFAULT_LEVELS_HANDLES)
            elif key == "SelectiveColor_Ranges":
                default = [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES]
            else:
                default = list(DEFAULT_CURVE_POINTS)
        return self._values.get(key, default)

    def values(self) -> Dict[str, float | bool | List[Tuple[float, float]]]:
        """Return a shallow copy of every stored adjustment."""

        return dict(self._values)

    # ------------------------------------------------------------------
    # Mutation helpers
    def set_value(self, key: str, value) -> None:
        """Update *key* with *value* while honouring the stored type."""

        if key not in self._values:
            return
        current = self._values[key]

        # Handle list data (curve control points, levels handles)
        if key in _LIST_KEYS:
            if not isinstance(value, list):
                return
            # Deep copy the list to avoid reference issues
            if key in _CURVE_LIST_KEYS:
                normalised = [tuple(pt) for pt in value]
            else:
                normalised = list(value)
            if normalised == current:
                return
            self._values[key] = normalised
            self.valueChanged.emit(key, normalised)
            self.valuesChanged.emit(self.values())
            return

        if isinstance(current, bool):
            normalised = bool(value)
            if normalised is current:
                return
        else:
            minimum, maximum = self._ranges.get(key, (-1.0, 1.0))
            numeric = _coerce_bw_range(key, float(value))
            normalised = max(minimum, min(maximum, numeric))
            if abs(normalised - float(current)) < 1e-4:
                return
        self._values[key] = normalised
        self.valueChanged.emit(key, normalised)
        self.valuesChanged.emit(self.values())

    def set_values(self, updates: Mapping[str, float | bool | List[Tuple[float, float]]], *, emit_individual: bool = True) -> None:
        """Update multiple *updates* at once."""

        changed: list[tuple[str, float | bool | List[Tuple[float, float]]]] = []
        for key, value in updates.items():
            if key not in self._values:
                continue
            current = self._values[key]

            # Handle list data (curve control points, levels handles)
            if key in _LIST_KEYS:
                if not isinstance(value, list):
                    continue
                if key in _CURVE_LIST_KEYS:
                    normalised = [tuple(pt) for pt in value]
                else:
                    normalised = list(value)
                if normalised == current:
                    continue
                self._values[key] = normalised
                changed.append((key, normalised))
                continue

            if isinstance(current, bool):
                normalised = bool(value)
                if normalised is current:
                    continue
            else:
                minimum, maximum = self._ranges.get(key, (-1.0, 1.0))
                numeric = _coerce_bw_range(key, float(value))
                normalised = max(minimum, min(maximum, numeric))
                if abs(normalised - float(current)) < 1e-4:
                    continue
            self._values[key] = normalised
            changed.append((key, normalised))
        if not changed:
            return
        if emit_individual:
            for key, value in changed:
                self.valueChanged.emit(key, value)
        self.valuesChanged.emit(self.values())

    def reset(self) -> None:
        """Restore the master and fine-tuning adjustments to their defaults."""

        defaults: dict[str, float | bool | List[Tuple[float, float]]] = {
            "Light_Master": 0.0,
            "Light_Enabled": True,
            "Color_Master": 0.0,
            "Color_Enabled": True,
        }
        defaults.update({key: 0.0 for key in LIGHT_KEYS})
        defaults.update({key: 0.0 for key in COLOR_KEYS})
        defaults.update({
            "BW_Master": 0.5,
            "BW_Enabled": False,
            "BW_Intensity": 0.5,
            "BW_Neutrals": 0.0,
            "BW_Tone": 0.0,
            "BW_Grain": 0.0,
        })
        defaults.update({
            "WB_Enabled": False,
            "WB_Warmth": 0.0,
            "WB_Temperature": 0.0,
            "WB_Tint": 0.0,
        })
        defaults.update(
            {
                "Crop_CX": 0.5,
                "Crop_CY": 0.5,
                "Crop_W": 1.0,
                "Crop_H": 1.0,
                "Perspective_Vertical": 0.0,
                "Perspective_Horizontal": 0.0,
                "Crop_Straighten": 0.0,
                "Crop_Rotate90": 0.0,
                "Crop_FlipH": False,
            }
        )
        # Curve defaults
        defaults.update({
            "Curve_Enabled": False,
            "Curve_RGB": list(DEFAULT_CURVE_POINTS),
            "Curve_Red": list(DEFAULT_CURVE_POINTS),
            "Curve_Green": list(DEFAULT_CURVE_POINTS),
            "Curve_Blue": list(DEFAULT_CURVE_POINTS),
        })
        # Levels defaults
        defaults.update({
            "Levels_Enabled": False,
            "Levels_Handles": list(DEFAULT_LEVELS_HANDLES),
        })
        # Selective Color defaults
        defaults.update({
            "SelectiveColor_Enabled": False,
            "SelectiveColor_Ranges": [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
        })
        self.set_values(defaults, emit_individual=True)
        self.resetPerformed.emit()

    # ------------------------------------------------------------------
    def set_color_stats(self, stats: ColorStats | None) -> None:
        """Persist *stats* for use by Color adjustment resolvers."""

        self._color_stats = stats

    def color_stats(self) -> ColorStats | None:
        """Return the most recently assigned :class:`ColorStats` instance."""

        return self._color_stats

    # ------------------------------------------------------------------
    # Convenience helpers used by tests and controllers
    def load_from_mapping(self, mapping: Mapping[str, float]) -> None:
        """Replace the current state using *mapping* while emitting signals."""

        self.set_values(mapping, emit_individual=True)

    def iter_items(self) -> Iterable[tuple[str, float]]:
        """Yield the adjustment keys in their canonical order."""

        return self._values.items()

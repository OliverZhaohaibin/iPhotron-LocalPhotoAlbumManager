"""
LUT generation and GPU upload for curve and levels adjustments.

Encapsulates the logic that was previously inlined in ``GLImageViewer``
for building look-up tables from adjustment parameters and uploading
them to the active GL renderer.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from .....core.curve_resolver import (
    CurveParams,
    CurveChannel,
    CurvePoint,
    generate_curve_lut,
)
from .....core.levels_resolver import (
    DEFAULT_LEVELS_HANDLES,
    build_levels_lut,
)

_LOGGER = logging.getLogger(__name__)


def _normalise_curve_points(raw: Any) -> tuple[tuple[float, float], ...]:
    """Return a hashable representation of curve control points."""

    if not isinstance(raw, list):
        return ()
    points: list[tuple[float, float]] = []
    for pt in raw:
        if (
            isinstance(pt, (list, tuple))
            and len(pt) >= 2
            and isinstance(pt[0], (int, float))
            and isinstance(pt[1], (int, float))
        ):
            points.append((float(pt[0]), float(pt[1])))
    return tuple(points)


def _curve_signature(adjustments: dict[str, Any]) -> tuple:
    """Return a stable signature for the current curve configuration."""

    if not bool(adjustments.get("Curve_Enabled", False)):
        return ("disabled",)
    return (
        "enabled",
        _normalise_curve_points(adjustments.get("Curve_RGB")),
        _normalise_curve_points(adjustments.get("Curve_Red")),
        _normalise_curve_points(adjustments.get("Curve_Green")),
        _normalise_curve_points(adjustments.get("Curve_Blue")),
    )


def _levels_signature(adjustments: dict[str, Any]) -> tuple:
    """Return a stable signature for the current levels configuration."""

    if not bool(adjustments.get("Levels_Enabled", False)):
        return ("disabled",)
    handles = adjustments.get("Levels_Handles")
    if not isinstance(handles, list) or len(handles) != 5:
        return ("invalid",)
    return ("enabled", tuple(float(value) for value in handles))


class AdjustmentApplicator:
    """Manages curve and levels LUT generation and GPU upload.

    Parameters
    ----------
    renderer_provider:
        Callable returning the current ``GLRenderer`` (or ``None``).
    make_current:
        Callable that makes the GL context current.
    done_current:
        Callable that releases the GL context.
    """

    def __init__(
        self,
        renderer_provider: Callable,
        make_current: Callable[[], None],
        done_current: Callable[[], None],
    ) -> None:
        self._renderer_provider = renderer_provider
        self._make_current = make_current
        self._done_current = done_current
        self._current_curve_lut: np.ndarray | None = None
        self._current_curve_signature: tuple | None = None
        self._current_levels_signature: tuple | None = None

    def invalidate_cache(self) -> None:
        """Forget cached LUT signatures after renderer/resource recreation."""

        self._current_curve_signature = None
        self._current_levels_signature = None

    # ------------------------------------------------------------------
    # Curve LUT
    # ------------------------------------------------------------------

    def update_curve_lut_if_needed(self, adjustments: dict[str, Any]) -> None:
        """Update the curve LUT texture if curve parameters have changed."""
        curve_enabled = bool(adjustments.get("Curve_Enabled", False))
        renderer = self._renderer_provider()
        signature = _curve_signature(adjustments)

        if renderer is None:
            return
        if signature == self._current_curve_signature:
            return

        if not curve_enabled:
            self._make_current()
            try:
                try:
                    identity_params = CurveParams(enabled=False)
                    identity_lut = generate_curve_lut(identity_params)
                except Exception as e:
                    _LOGGER.warning("Failed to generate identity curve LUT: %s", e)
                    return

                renderer.upload_curve_lut(identity_lut)
                self._current_curve_lut = identity_lut
                self._current_curve_signature = signature
            finally:
                self._done_current()
            return

        # Build CurveParams from adjustment data
        params = CurveParams(enabled=curve_enabled)

        for key, attr in [
            ("Curve_RGB", "rgb"),
            ("Curve_Red", "red"),
            ("Curve_Green", "green"),
            ("Curve_Blue", "blue"),
        ]:
            raw = adjustments.get(key)
            if raw and isinstance(raw, list):
                points: list[CurvePoint] = []
                for x_coord, y_coord in _normalise_curve_points(raw):
                    points.append(CurvePoint(x=x_coord, y=y_coord))
                if points:
                    setattr(params, attr, CurveChannel(points=points))

        try:
            lut = generate_curve_lut(params)
        except Exception as e:
            _LOGGER.warning("Failed to generate curve LUT: %s", e)
            return

        self._make_current()
        try:
            renderer.upload_curve_lut(lut)
            self._current_curve_lut = lut
            self._current_curve_signature = signature
        finally:
            self._done_current()

    # ------------------------------------------------------------------
    # Levels LUT
    # ------------------------------------------------------------------

    def update_levels_lut_if_needed(self, adjustments: dict[str, Any]) -> None:
        """Update the levels LUT texture if levels parameters have changed."""
        levels_enabled = bool(adjustments.get("Levels_Enabled", False))
        renderer = self._renderer_provider()
        signature = _levels_signature(adjustments)

        if renderer is None:
            return
        if signature == self._current_levels_signature:
            return

        if not levels_enabled:
            self._make_current()
            try:
                try:
                    identity_lut = build_levels_lut(list(DEFAULT_LEVELS_HANDLES))
                except Exception as e:
                    _LOGGER.warning("Failed to generate identity levels LUT: %s", e)
                    return
                renderer.upload_levels_lut(identity_lut)
                self._current_levels_signature = signature
            finally:
                self._done_current()
            return

        handles = adjustments.get("Levels_Handles")
        if not isinstance(handles, list) or len(handles) != 5:
            return

        try:
            lut = build_levels_lut(handles)
        except Exception as e:
            _LOGGER.warning("Failed to generate levels LUT: %s", e)
            return

        self._make_current()
        try:
            renderer.upload_levels_lut(lut)
            self._current_levels_signature = signature
        finally:
            self._done_current()

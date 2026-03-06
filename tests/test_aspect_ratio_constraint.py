"""Tests for the crop aspect-ratio constraint logic."""

import pytest
from unittest.mock import MagicMock

from iPhoto.gui.ui.widgets.gl_crop.controller import (
    CropInteractionController,
    _fit_crop_aspect,
)
from iPhoto.gui.ui.widgets.gl_crop.utils import CropBoxState, CropHandle
from iPhoto.gui.ui.widgets.gl_crop.strategies.resize_strategy import ResizeStrategy
from iPhoto.gui.ui.widgets.gl_crop.model import CropSessionModel


# ---------------------------------------------------------------------------
# _fit_crop_aspect
# ---------------------------------------------------------------------------

class TestFitCropAspect:
    def test_wider_than_target_shrinks_width(self):
        state = CropBoxState()
        state.cx, state.cy, state.width, state.height = 0.5, 0.5, 0.8, 0.4
        # current aspect = 2.0, target = 1.0 → shrink width
        _fit_crop_aspect(state, 1.0)
        assert abs(state.width / state.height - 1.0) < 1e-5

    def test_taller_than_target_shrinks_height(self):
        state = CropBoxState()
        state.cx, state.cy, state.width, state.height = 0.5, 0.5, 0.4, 0.8
        # current aspect = 0.5, target = 1.0 → shrink height
        _fit_crop_aspect(state, 1.0)
        assert abs(state.width / state.height - 1.0) < 1e-5

    def test_already_correct_ratio_unchanged(self):
        state = CropBoxState()
        state.cx, state.cy, state.width, state.height = 0.5, 0.5, 0.6, 0.4
        _fit_crop_aspect(state, 1.5)
        assert abs(state.width - 0.6) < 1e-5
        assert abs(state.height - 0.4) < 1e-5


# ---------------------------------------------------------------------------
# CropInteractionController.set_locked_aspect_ratio
# ---------------------------------------------------------------------------

def _make_controller():
    texture_provider = MagicMock(return_value=(300, 200))
    clamp_fn = MagicMock()
    transform_ctrl = MagicMock()
    transform_ctrl.get_effective_scale.return_value = 1.0
    transform_ctrl.convert_image_to_viewport.return_value = MagicMock()
    on_crop_changed = MagicMock()
    on_update = MagicMock()
    return CropInteractionController(
        texture_size_provider=texture_provider,
        clamp_image_center_to_crop=clamp_fn,
        transform_controller=transform_ctrl,
        on_crop_changed=on_crop_changed,
        on_cursor_change=MagicMock(),
        on_request_update=on_update,
    )


class TestControllerLockedAspect:
    def test_default_is_freeform(self):
        ctrl = _make_controller()
        assert ctrl._locked_aspect == 0.0

    def test_set_locked_aspect_ratio_stores_value(self):
        ctrl = _make_controller()
        ctrl.set_locked_aspect_ratio(16 / 9)
        assert abs(ctrl._locked_aspect - 16 / 9) < 1e-6

    def test_set_locked_aspect_ratio_immediately_applies_when_active(self):
        """When crop mode is active, setting a positive aspect ratio should
        immediately adjust the crop box and emit a change callback."""
        ctrl = _make_controller()
        # Activate crop mode with a non-square crop (width=1.0, height=1.0 → square default)
        ctrl.set_active(True, {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 0.8, "Crop_H": 0.4})
        state = ctrl.get_crop_state()
        # Verify crop is 0.8 x 0.4 (ratio 2.0)
        assert abs(state.width - 0.8) < 1e-5
        assert abs(state.height - 0.4) < 1e-5

        # Now set aspect ratio to 1.0 (square) — should immediately adjust
        ctrl.set_locked_aspect_ratio(1.0)
        state = ctrl.get_crop_state()
        assert abs(state.width / state.height - 1.0) < 1e-4
        # The on_crop_changed callback should have been called
        ctrl._on_crop_changed_callback.assert_called()

    def test_set_locked_aspect_ratio_no_change_when_inactive(self):
        """When crop mode is not active, setting a ratio should only store it."""
        ctrl = _make_controller()
        ctrl.set_locked_aspect_ratio(1.0)
        # Should not trigger any crop changed callback
        ctrl._on_crop_changed_callback.assert_not_called()


# ---------------------------------------------------------------------------
# ResizeStrategy._enforce_aspect
# ---------------------------------------------------------------------------

def _make_resize_strategy(handle, locked_aspect=1.0):
    model = CropSessionModel()
    return ResizeStrategy(
        handle=handle,
        model=model,
        texture_size_provider=MagicMock(return_value=(1000, 1000)),
        get_effective_scale=MagicMock(return_value=1.0),
        get_dpr=MagicMock(return_value=1.0),
        on_crop_changed=MagicMock(),
        apply_edge_push_zoom=MagicMock(),
        locked_aspect=locked_aspect,
    )


class TestEnforceAspect:
    """Verify that _enforce_aspect adjusts edges to match the locked ratio."""

    def _crop(self, l, b, r, t):
        return {"left": l, "bottom": b, "right": r, "top": t}

    def _bounds(self):
        return {"left": -500, "bottom": -500, "right": 500, "top": 500}

    def test_right_edge_aspect_1(self):
        strat = _make_resize_strategy(CropHandle.RIGHT, locked_aspect=1.0)
        crop = self._crop(-100, -50, 200, 50)  # 300 x 100 → should become square
        strat._enforce_aspect(crop, CropHandle.RIGHT, self._bounds(), 1, 1)
        w = crop["right"] - crop["left"]
        h = crop["top"] - crop["bottom"]
        assert abs(w / h - 1.0) < 1e-3

    def test_bottom_edge_aspect_16_9(self):
        strat = _make_resize_strategy(CropHandle.BOTTOM, locked_aspect=16 / 9)
        crop = self._crop(-80, -90, 80, 90)  # 160 x 180
        strat._enforce_aspect(crop, CropHandle.BOTTOM, self._bounds(), 1, 1)
        w = crop["right"] - crop["left"]
        h = crop["top"] - crop["bottom"]
        assert abs(w / h - 16 / 9) < 1e-3

    def test_corner_tl_aspect_4_3(self):
        strat = _make_resize_strategy(CropHandle.TOP_LEFT, locked_aspect=4 / 3)
        crop = self._crop(-200, -100, 100, 100)  # 300 x 200
        strat._enforce_aspect(crop, CropHandle.TOP_LEFT, self._bounds(), 1, 1)
        w = crop["right"] - crop["left"]
        h = crop["top"] - crop["bottom"]
        assert abs(w / h - 4 / 3) < 1e-3

    def test_corner_br_aspect_square(self):
        strat = _make_resize_strategy(CropHandle.BOTTOM_RIGHT, locked_aspect=1.0)
        crop = self._crop(-100, -50, 100, 50)  # 200 x 100
        strat._enforce_aspect(crop, CropHandle.BOTTOM_RIGHT, self._bounds(), 1, 1)
        w = crop["right"] - crop["left"]
        h = crop["top"] - crop["bottom"]
        assert abs(w / h - 1.0) < 1e-3

    def test_no_constraint_when_freeform(self):
        """locked_aspect=0 → _enforce_aspect should never be called."""
        strat = _make_resize_strategy(CropHandle.RIGHT, locked_aspect=0.0)
        # _enforce_aspect is skipped by the on_drag logic when locked_aspect <= 0
        assert strat._locked_aspect == 0.0

    def test_clamped_to_image_bounds(self):
        strat = _make_resize_strategy(CropHandle.RIGHT, locked_aspect=1.0)
        # Make the crop close to the image boundary
        crop = self._crop(400, -50, 500, 50)  # 100 x 100
        bounds = self._bounds()
        strat._enforce_aspect(crop, CropHandle.RIGHT, bounds, 1, 1)
        assert crop["right"] <= bounds["right"]
        assert crop["left"] >= bounds["left"]
        assert crop["top"] <= bounds["top"]
        assert crop["bottom"] >= bounds["bottom"]


import pytest
from unittest.mock import MagicMock
from src.iPhoto.gui.ui.widgets.gl_crop.controller import CropInteractionController

def test_update_perspective_applies_new_crop_on_rotation_change():
    # Setup
    texture_provider = MagicMock(return_value=(300, 200))
    clamp_fn = MagicMock()
    transform_ctrl = MagicMock()
    # Mock methods of transform_ctrl used in _apply_crop_values
    transform_ctrl.get_effective_scale.return_value = 1.0
    transform_ctrl.convert_image_to_viewport.return_value = MagicMock()

    on_crop_changed = MagicMock()
    on_update = MagicMock()

    controller = CropInteractionController(
        texture_size_provider=texture_provider,
        clamp_image_center_to_crop=clamp_fn,
        transform_controller=transform_ctrl,
        on_crop_changed=on_crop_changed,
        on_cursor_change=MagicMock(),
        on_request_update=on_update,
    )

    # 1. Initial State
    # Simulate setup
    controller.update_perspective(0, 0, 0, 0, False)

    # Set a crop
    initial_crop = {'Crop_CX': 0.2, 'Crop_CY': 0.5, 'Crop_W': 0.4, 'Crop_H': 1.0}
    controller._apply_crop_values(initial_crop)

    # 2. Rotation Change
    # Prepare NEW logical crop
    new_crop_values = {'Crop_CX': 0.8, 'Crop_CY': 0.5, 'Crop_W': 0.4, 'Crop_H': 1.0}

    # Call update_perspective with rotation change (0 -> 1)
    controller.update_perspective(
        0, 0, 0, 1, False,
        new_crop_values=new_crop_values
    )

    # Verify
    # 1. Rotation updated in model
    assert controller._model._rotate_steps == 1

    # 2. Crop state should match new_crop_values
    state = controller.get_crop_state()
    assert state.cx == 0.8
    assert state.cy == 0.5

    # 3. on_request_update called
    assert on_update.call_count >= 1

def test_update_perspective_ignores_new_crop_if_rotation_unchanged():
    # Setup
    texture_provider = MagicMock(return_value=(300, 200))
    transform_ctrl = MagicMock()
    transform_ctrl.get_effective_scale.return_value = 1.0

    controller = CropInteractionController(
        texture_size_provider=texture_provider,
        clamp_image_center_to_crop=MagicMock(),
        transform_controller=transform_ctrl,
        on_crop_changed=MagicMock(),
        on_cursor_change=MagicMock(),
        on_request_update=MagicMock(),
    )

    controller.update_perspective(0, 0, 0, 0, False)

    # Set initial crop
    initial_crop = {'Crop_CX': 0.5, 'Crop_CY': 0.5, 'Crop_W': 1.0, 'Crop_H': 1.0}
    controller._apply_crop_values(initial_crop)

    # Call update with SAME rotation, but providing new values (should be ignored)
    new_crop_values = {'Crop_CX': 0.1, 'Crop_CY': 0.1, 'Crop_W': 0.1, 'Crop_H': 0.1}

    controller.update_perspective(
        0, 0, 0, 0, False,
        new_crop_values=new_crop_values
    )

    # Crop should REMAIN initial (0.5) because we ignored new_values
    state = controller.get_crop_state()
    assert state.cx == 0.5
    assert state.width == 1.0

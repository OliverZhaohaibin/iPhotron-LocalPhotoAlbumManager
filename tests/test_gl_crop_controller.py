from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPointF

from iPhoto.gui.ui.widgets.gl_crop.controller import CropInteractionController
from iPhoto.gui.ui.widgets.gl_crop.model import CropSessionModel
from iPhoto.gui.ui.widgets.gl_crop.strategies.pan_strategy import PanStrategy


def create_controller():
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


def test_update_perspective_applies_new_crop_on_rotation_change():
    controller = create_controller()

    # Initial State
    controller.update_perspective(0, 0, 0, 0, False)
    initial_crop = {'Crop_CX': 0.2, 'Crop_CY': 0.5, 'Crop_W': 0.4, 'Crop_H': 1.0}
    controller._apply_crop_values(initial_crop)

    # Rotation Change (0 -> 1)
    new_crop_values = {'Crop_CX': 0.8, 'Crop_CY': 0.5, 'Crop_W': 0.4, 'Crop_H': 1.0}

    controller.update_perspective(
        0, 0, 0, 1, False,
        new_crop_values=new_crop_values
    )

    # Verify applied
    state = controller.get_crop_state()
    assert state.cx == 0.8
    assert controller._model._rotate_steps == 1


def test_update_perspective_ignores_new_crop_if_rotation_unchanged_and_active():
    controller = create_controller()
    controller.update_perspective(0, 0, 0, 0, False)

    initial_crop = {'Crop_CX': 0.5, 'Crop_CY': 0.5, 'Crop_W': 1.0, 'Crop_H': 1.0}
    controller.set_active(True, initial_crop)

    # Update with SAME rotation, new values
    new_crop_values = {'Crop_CX': 0.1, 'Crop_CY': 0.1, 'Crop_W': 0.1, 'Crop_H': 0.1}

    controller.update_perspective(
        0, 0, 0, 0, False,
        new_crop_values=new_crop_values
    )

    # Verify IGNORED (because active)
    state = controller.get_crop_state()
    assert state.cx == 0.5
    controller.set_active(False)


def test_update_perspective_applies_new_crop_if_inactive():
    controller = create_controller()
    controller.update_perspective(0, 0, 0, 0, False)

    initial_crop = {'Crop_CX': 0.5, 'Crop_CY': 0.5, 'Crop_W': 1.0, 'Crop_H': 1.0}
    # Do not set active. Just apply initial values.
    controller._apply_crop_values(initial_crop)
    assert not controller.is_active()

    # Update with SAME rotation, new values (e.g. from Undo/Redo)
    new_crop_values = {'Crop_CX': 0.1, 'Crop_CY': 0.1, 'Crop_W': 0.1, 'Crop_H': 0.1}

    controller.update_perspective(
        0, 0, 0, 0, False,
        new_crop_values=new_crop_values
    )

    # Verify APPLIED (because inactive)
    state = controller.get_crop_state()
    assert state.cx == 0.1
    assert state.width == 0.1


def test_active_crop_feedback_with_same_values_does_not_reapply_transform():
    controller = create_controller()
    values = {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 0.8, "Crop_H": 0.7}
    controller.set_active(True, values)
    controller._transform_controller.apply_image_center_pixels.reset_mock()

    controller.set_active(True, dict(values))

    controller._transform_controller.apply_image_center_pixels.assert_not_called()


def test_active_drag_ignores_external_crop_feedback():
    controller = create_controller()
    initial = {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 0.8, "Crop_H": 0.7}
    controller.set_active(True, initial)
    controller._crop_dragging = True
    controller._transform_controller.apply_image_center_pixels.reset_mock()

    controller.set_active(
        True,
        {"Crop_CX": 0.2, "Crop_CY": 0.3, "Crop_W": 0.4, "Crop_H": 0.5},
    )

    assert controller.is_interacting()
    assert controller.get_crop_state().as_mapping() == initial
    controller._transform_controller.apply_image_center_pixels.assert_not_called()


def test_active_crop_applies_external_restore_when_not_dragging():
    controller = create_controller()
    controller.set_active(
        True,
        {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 0.8, "Crop_H": 0.7},
    )
    restored = {"Crop_CX": 0.4, "Crop_CY": 0.6, "Crop_W": 0.5, "Crop_H": 0.4}
    controller._transform_controller.apply_image_center_pixels.reset_mock()

    controller.set_active(True, restored)

    assert not controller.is_interacting()
    assert controller.get_crop_state().as_mapping() == restored
    controller._transform_controller.apply_image_center_pixels.assert_called_once()


def test_animation_frame_ignores_invalid_transform_geometry():
    controller = create_controller()
    controller._on_request_update.reset_mock()

    controller._on_animation_frame(1.0, QPointF(10, 10))

    controller._on_request_update.assert_not_called()


def test_current_crop_rect_pixels_maps_viewport_logical_to_device_pixels():
    controller = create_controller()
    transform = controller._transform_controller
    transform.convert_image_to_viewport.side_effect = lambda x, y: QPointF(x / 3.0, y / 2.0)
    transform.viewport_logical_to_device.side_effect = lambda point: QPointF(
        point.x() * 3.0,
        point.y() * 2.0,
    )

    controller.set_active(True, {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 1.0, "Crop_H": 1.0})

    assert controller.current_crop_rect_pixels() == {
        "left": 0.0,
        "top": 0.0,
        "right": 300.0,
        "bottom": 200.0,
    }


def test_inside_pan_moves_image_while_crop_frame_stays_stationary():
    model = CropSessionModel()
    state = model.get_crop_state()
    state.width = 0.6
    state.height = 0.6
    pan = [QPointF(10.0, 20.0)]
    set_pan = MagicMock(side_effect=lambda value: pan.__setitem__(0, QPointF(value)))
    on_crop_changed = MagicMock()
    strategy = PanStrategy(
        model=model,
        texture_size_provider=lambda: (300, 200),
        get_effective_scale=lambda: 2.0,
        get_dpr=lambda: 1.0,
        get_pan_pixels=lambda: QPointF(pan[0]),
        set_pan_pixels=set_pan,
        get_viewport_device_scale=lambda: (1.0, 1.0),
        on_crop_changed=on_crop_changed,
    )
    old_left_screen = (state.cx - state.width * 0.5) * 300.0 * 2.0 + pan[0].x()
    old_top_screen = (state.cy - state.height * 0.5) * 200.0 * 2.0 - pan[0].y()

    strategy.on_drag(QPointF(20.0, 10.0))

    assert state.cx == pytest.approx(0.5 - 10.0 / 300.0)
    assert state.cy == pytest.approx(0.5 - 5.0 / 200.0)
    assert pan[0] == QPointF(30.0, 10.0)
    new_left_screen = (state.cx - state.width * 0.5) * 300.0 * 2.0 + pan[0].x()
    new_top_screen = (state.cy - state.height * 0.5) * 200.0 * 2.0 - pan[0].y()
    assert new_left_screen == pytest.approx(old_left_screen)
    assert new_top_screen == pytest.approx(old_top_screen)
    on_crop_changed.assert_called_once_with()

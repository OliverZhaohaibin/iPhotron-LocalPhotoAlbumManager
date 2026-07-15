from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QInputDevice

from iPhoto.gui.ui.widgets.view_transform_controller import ViewTransformController
from maps.map_widget.input_handler import InputHandler
from maps.touchpad_input import (
    event_position,
    is_scroll_end_event,
    is_trackpad_wheel_event,
    pan_delta_from_wheel,
    zoom_factor_from_native_gesture,
    zoom_factor_from_touchpad_pinch_wheel,
)


class _Device:
    def __init__(self, device_type: QInputDevice.DeviceType) -> None:
        self._device_type = device_type

    def type(self):
        return self._device_type


class _WheelEvent:
    def __init__(
        self,
        *,
        pixel: QPoint | None = None,
        angle: QPoint | None = None,
        phase: Qt.ScrollPhase = Qt.ScrollPhase.NoScrollPhase,
        device_type: QInputDevice.DeviceType | None = None,
        position: QPointF | None = None,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        self._pixel = QPoint(pixel) if pixel is not None else QPoint()
        self._angle = QPoint(angle) if angle is not None else QPoint()
        self._phase = phase
        self._device_type = device_type
        self._position = QPointF(position) if position is not None else QPointF(10.0, 20.0)
        self._modifiers = modifiers
        self.accepted = False

    def device(self):
        if self._device_type is None:
            raise RuntimeError("device metadata unavailable")
        return _Device(self._device_type)

    def pixelDelta(self) -> QPoint:
        return self._pixel

    def angleDelta(self) -> QPoint:
        return self._angle

    def phase(self):
        return self._phase

    def position(self) -> QPointF:
        return self._position

    def modifiers(self):
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True


class _NativeGestureEvent:
    def __init__(
        self,
        value: float,
        position: QPointF | None = None,
        gesture_type: Qt.NativeGestureType = Qt.NativeGestureType.ZoomNativeGesture,
    ) -> None:
        self._value = value
        self._position = QPointF(position) if position is not None else QPointF(12.0, 34.0)
        self._gesture_type = gesture_type
        self.accepted = False

    def gestureType(self):
        return self._gesture_type

    def value(self) -> float:
        return self._value

    def position(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True


class _Viewer:
    def __init__(self) -> None:
        self.update_count = 0

    def width(self) -> int:
        return 100

    def height(self) -> int:
        return 100

    def devicePixelRatioF(self) -> float:
        return 2.0

    def update(self) -> None:
        self.update_count += 1

    def setCursor(self, _cursor) -> None:
        return None

    def unsetCursor(self) -> None:
        return None


def test_touchpad_detection_prefers_real_device_type() -> None:
    touchpad = _WheelEvent(
        pixel=QPoint(8, -6),
        device_type=QInputDevice.DeviceType.TouchPad,
    )
    mouse = _WheelEvent(
        pixel=QPoint(8, -6),
        device_type=QInputDevice.DeviceType.Mouse,
    )
    fallback = _WheelEvent(pixel=QPoint(8, -6))

    assert is_trackpad_wheel_event(touchpad)
    assert not is_trackpad_wheel_event(mouse)
    assert is_trackpad_wheel_event(fallback)
    assert pan_delta_from_wheel(touchpad) == QPointF(8.0, -6.0)


def test_touchpad_phases_and_native_zoom_factor() -> None:
    end = _WheelEvent(
        phase=Qt.ScrollPhase.ScrollEnd,
        device_type=QInputDevice.DeviceType.TouchPad,
    )
    pinch = _NativeGestureEvent(0.05)

    assert is_trackpad_wheel_event(end)
    assert is_scroll_end_event(end)
    assert zoom_factor_from_native_gesture(pinch) == pytest.approx(1.05)
    assert event_position(pinch) == QPointF(12.0, 34.0)


def test_native_zoom_factor_supports_shrink_clamps_and_rejects_other_gestures() -> None:
    assert zoom_factor_from_native_gesture(_NativeGestureEvent(-0.08)) == pytest.approx(0.92)
    assert zoom_factor_from_native_gesture(_NativeGestureEvent(5.0)) == pytest.approx(1.18)
    assert zoom_factor_from_native_gesture(_NativeGestureEvent(-5.0)) == pytest.approx(0.85)
    assert (
        zoom_factor_from_native_gesture(
            _NativeGestureEvent(
                0.25,
                gesture_type=Qt.NativeGestureType.RotateNativeGesture,
            )
        )
        is None
    )


def test_map_input_separates_touchpad_pan_mouse_wheel_and_pinch() -> None:
    handler = InputHandler(min_zoom=2.0, max_zoom=19.0)
    pans: list[QPointF] = []
    zooms: list[tuple[float, QPointF]] = []
    finished: list[bool] = []
    handler.trackpad_pan_requested.connect(lambda delta: pans.append(QPointF(delta)))
    handler.trackpad_pan_finished.connect(lambda: finished.append(True))
    handler.zoom_requested.connect(
        lambda zoom, anchor: zooms.append((float(zoom), QPointF(anchor)))
    )

    pan_event = _WheelEvent(
        pixel=QPoint(7, -9),
        angle=QPoint(0, 120),
        device_type=QInputDevice.DeviceType.TouchPad,
    )
    assert handler.handle_wheel_event(pan_event, 4.0)
    assert pans == [QPointF(7.0, -9.0)]
    assert zooms == []

    end_event = _WheelEvent(
        phase=Qt.ScrollPhase.ScrollEnd,
        device_type=QInputDevice.DeviceType.TouchPad,
    )
    assert handler.handle_wheel_event(end_event, 4.0)
    assert finished == [True]

    wheel_event = _WheelEvent(
        angle=QPoint(0, 120),
        device_type=QInputDevice.DeviceType.Mouse,
    )
    assert handler.handle_wheel_event(wheel_event, 4.0)
    assert zooms[-1][0] == pytest.approx(4.4)

    pinch = _NativeGestureEvent(0.05)
    assert handler.handle_native_gesture_event(pinch, 4.0)
    assert zooms[-1][0] == pytest.approx(4.2)
    assert zooms[-1][1] == QPointF(12.0, 34.0)


def test_ctrl_modified_touchpad_wheel_is_pinch_not_pan() -> None:
    event = _WheelEvent(
        angle=QPoint(0, 120),
        pixel=QPoint(0, 5),
        device_type=QInputDevice.DeviceType.TouchPad,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )
    assert zoom_factor_from_touchpad_pinch_wheel(event) == pytest.approx(1.1)

    handler = InputHandler(min_zoom=2.0, max_zoom=19.0)
    pans: list[QPointF] = []
    zooms: list[tuple[float, QPointF]] = []
    handler.trackpad_pan_requested.connect(lambda delta: pans.append(QPointF(delta)))
    handler.zoom_requested.connect(
        lambda zoom, anchor: zooms.append((float(zoom), QPointF(anchor)))
    )

    assert handler.handle_wheel_event(event, 4.0)
    assert pans == []
    assert zooms == [(pytest.approx(4.4), QPointF(10.0, 20.0))]


def test_detail_transform_pans_touchpad_even_when_wheel_navigates() -> None:
    viewer = _Viewer()
    next_items: list[bool] = []
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: (200.0, 200.0),
        on_zoom_changed=lambda _zoom: None,
        on_next_item=lambda: next_items.append(True),
    )
    controller.set_wheel_action("navigate")
    event = _WheelEvent(
        pixel=QPoint(5, 10),
        angle=QPoint(0, -120),
        device_type=QInputDevice.DeviceType.TouchPad,
    )

    controller.handle_wheel(event)

    assert event.accepted
    assert controller.get_pan_pixels() == QPointF(10.0, -20.0)
    assert next_items == []


def test_detail_ctrl_touchpad_wheel_zooms_even_when_wheel_navigates() -> None:
    viewer = _Viewer()
    next_items: list[bool] = []
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: (200.0, 200.0),
        on_zoom_changed=lambda _zoom: None,
        on_next_item=lambda: next_items.append(True),
    )
    controller.set_wheel_action("navigate")
    event = _WheelEvent(
        angle=QPoint(0, 120),
        device_type=QInputDevice.DeviceType.TouchPad,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )

    controller.handle_wheel(event)

    assert event.accepted
    assert controller.get_zoom_factor() == pytest.approx(1.1)
    assert next_items == []


def test_detail_native_pinch_zooms_both_directions_and_respects_limits() -> None:
    viewer = _Viewer()
    emitted: list[float] = []
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: (200.0, 200.0),
        on_zoom_changed=emitted.append,
    )
    controller.set_zoom_limits(0.9, 1.1)

    grow = _NativeGestureEvent(0.5)
    assert controller.handle_native_gesture(grow)
    assert grow.accepted
    assert controller.get_zoom_factor() == pytest.approx(1.1)

    shrink = _NativeGestureEvent(-0.1)
    assert controller.handle_native_gesture(shrink)
    assert shrink.accepted
    assert controller.get_zoom_factor() == pytest.approx(0.99)

    assert controller.handle_native_gesture(_NativeGestureEvent(-5.0))
    assert controller.handle_native_gesture(_NativeGestureEvent(-5.0))
    assert controller.get_zoom_factor() == pytest.approx(0.9)
    assert emitted == pytest.approx([1.1, 0.99, 0.9])


@pytest.mark.parametrize("anchor", [QPointF(25.0, 40.0), QPointF(0.0, 0.0)])
def test_detail_native_pinch_keeps_anchor_texture_coordinate_stable(anchor: QPointF) -> None:
    viewer = _Viewer()
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: (200.0, 200.0),
        on_zoom_changed=lambda _zoom: None,
    )
    def texture_coordinate_at_anchor() -> QPointF:
        anchor_device = controller.viewport_logical_to_device(anchor)
        anchor_vector = QPointF(anchor_device.x() - 100.0, 100.0 - anchor_device.y())
        pan = controller.get_pan_pixels()
        scale = 2.0 * controller.get_zoom_factor()
        return QPointF(
            (anchor_vector.x() - pan.x()) / scale,
            (anchor_vector.y() - pan.y()) / scale,
        )

    before = texture_coordinate_at_anchor()
    assert controller.handle_native_gesture(_NativeGestureEvent(0.12), anchor=anchor)
    after = texture_coordinate_at_anchor()

    assert after.x() == pytest.approx(before.x())
    assert after.y() == pytest.approx(before.y())

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

import os

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QNativeGestureEvent, QPointingDevice, QWindow
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QApplication,
    QGestureEvent,
    QPinchGesture,
    QVBoxLayout,
    QWidget,
)

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
from maps import touchpad_input as touchpad_input_module


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _native_gesture_event(
    gesture_type: Qt.NativeGestureType,
    global_anchor: QPointF,
    *,
    value: float = 0.1,
    sequence_id: int = 1,
) -> QNativeGestureEvent:
    return QNativeGestureEvent(
        gesture_type,
        QPointingDevice.primaryPointingDevice(),
        2,
        global_anchor,
        global_anchor,
        global_anchor,
        value,
        QPointF(),
        sequence_id,
    )


def test_gl_image_viewer_queues_one_post_load_view_transform(qapp) -> None:
    viewer = GLImageViewer()
    viewer._pending_post_load_view_transform = True

    spy = QSignalSpy(viewer.viewTransformChanged)
    viewer._schedule_post_load_view_transform()
    qapp.processEvents()

    assert spy.count() == 1
    assert viewer._pending_post_load_view_transform is False
    assert viewer._post_load_view_transform_scheduled is False


def test_gl_image_viewer_maps_image_geometry_before_texture_upload(qapp) -> None:
    viewer = GLImageViewer()
    viewer.resize(420, 320)
    image = QImage(420, 320, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)

    viewer.set_image(image, {}, image_source="startup-still")
    qapp.processEvents()

    renderer = getattr(viewer, "_renderer", None)
    assert renderer is None or not renderer.has_texture()

    rect = viewer.image_rect_to_viewport(
        100,
        80,
        120,
        90,
        image_width=420,
        image_height=320,
    )
    assert rect.isEmpty() is False
    assert rect.left() == pytest.approx(100.0)
    assert rect.top() == pytest.approx(80.0)
    assert rect.width() == pytest.approx(120.0)
    assert rect.height() == pytest.approx(90.0)

    image_point = viewer.viewport_to_image(
        QPointF(210.0, 160.0),
        image_width=420,
        image_height=320,
    )
    assert image_point.x() == pytest.approx(210.0)
    assert image_point.y() == pytest.approx(160.0)


def test_gl_image_viewer_routes_window_native_pinch_to_local_anchor(qapp) -> None:
    host = QWidget()
    host.resize(360, 240)
    layout = QVBoxLayout(host)
    viewer = GLImageViewer()
    layout.addWidget(viewer)
    host.show()
    qapp.processEvents()

    try:
        global_anchor = QPointF(viewer.mapToGlobal(viewer.rect().center()))
        event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            global_anchor,
        )

        assert QApplication.sendEvent(host.windowHandle(), event)
        assert event.isAccepted()
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(1.1)

        detached_receiver = QWindow()
        detached_event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            global_anchor,
            sequence_id=2,
        )
        assert QApplication.sendEvent(detached_receiver, detached_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(1.21)

        viewer.set_touchpad_gestures_enabled(False)
        disabled_event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            global_anchor,
            sequence_id=3,
        )
        QApplication.sendEvent(host.windowHandle(), disabled_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(1.21)
    finally:
        host.close()
        qapp.processEvents()


def test_gl_image_viewer_accepts_qt_pinch_gesture_fallback(qapp) -> None:
    host = QWidget()
    host.resize(360, 240)
    layout = QVBoxLayout(host)
    viewer = GLImageViewer()
    layout.addWidget(viewer)
    host.show()
    qapp.processEvents()

    try:
        global_anchor = QPointF(viewer.mapToGlobal(viewer.rect().center()))
        pinch = QPinchGesture()
        pinch.setCenterPoint(global_anchor)
        pinch.setScaleFactor(1.12)
        pinch.setChangeFlags(QPinchGesture.ChangeFlag.ScaleFactorChanged)
        event = QGestureEvent([pinch])

        assert QApplication.sendEvent(viewer, event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(1.12)
    finally:
        host.close()
        qapp.processEvents()


def test_gl_image_viewer_accepts_native_cocoa_magnification_fallback(qapp) -> None:
    host = QWidget()
    host.resize(360, 240)
    layout = QVBoxLayout(host)
    viewer = GLImageViewer()
    layout.addWidget(viewer)
    host.show()
    qapp.processEvents()

    try:
        router = touchpad_input_module._native_gesture_router()
        assert router is not None
        global_anchor = QPointF(viewer.mapToGlobal(viewer.rect().center()))

        assert router.route_native_magnification(0.08, global_anchor)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(1.08)
    finally:
        host.close()
        qapp.processEvents()


def test_gl_image_viewer_native_pinch_filter_is_scoped_to_visible_viewer(qapp) -> None:
    host = QWidget()
    host.resize(420, 300)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(60, 50, 60, 50)
    viewer = GLImageViewer()
    layout.addWidget(viewer)

    other_host = QWidget()
    other_host.resize(160, 120)
    other_host.move(700, 500)
    host.show()
    other_host.show()
    qapp.processEvents()

    try:
        assert viewer._native_gesture_router_registered
        initial_zoom = viewer._transform_controller.get_zoom_factor()
        viewer_anchor = QPointF(viewer.mapToGlobal(viewer.rect().center()))
        other_window_event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            QPointF(other_host.mapToGlobal(other_host.rect().center())),
            sequence_id=10,
        )
        QApplication.sendEvent(other_host.windowHandle(), other_window_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(initial_zoom)

        outside_anchor = QPointF(host.mapToGlobal(QPoint(5, 5)))
        outside_event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            outside_anchor,
            sequence_id=11,
        )
        QApplication.sendEvent(host.windowHandle(), outside_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(initial_zoom)

        rotate_event = _native_gesture_event(
            Qt.NativeGestureType.RotateNativeGesture,
            viewer_anchor,
            sequence_id=12,
        )
        QApplication.sendEvent(host.windowHandle(), rotate_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(initial_zoom)

        viewer.hide()
        qapp.processEvents()
        assert not viewer._native_gesture_router_registered
        hidden_event = _native_gesture_event(
            Qt.NativeGestureType.ZoomNativeGesture,
            viewer_anchor,
            sequence_id=13,
        )
        QApplication.sendEvent(host.windowHandle(), hidden_event)
        assert viewer._transform_controller.get_zoom_factor() == pytest.approx(initial_zoom)
    finally:
        other_host.close()
        host.close()
        qapp.processEvents()

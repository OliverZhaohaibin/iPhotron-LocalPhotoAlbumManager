"""Shared helpers for distinguishing touchpad gestures from mouse wheels."""

from __future__ import annotations

import math
import sys
import weakref
from ctypes import CFUNCTYPE, c_char_p, c_double, c_ulong, c_void_p, cast, cdll
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QCursor, QInputDevice, QNativeGestureEvent, QPointingDevice
from PySide6.QtWidgets import QApplication, QWidget

_MIN_GESTURE_ZOOM_FACTOR = 0.85
_MAX_GESTURE_ZOOM_FACTOR = 1.18
_MAC_GENERIC_NS_EVENT = b"mac_generic_NSEvent"
_NS_EVENT_TYPE_MAGNIFY = 30


def _event_device_type(event: Any) -> tuple[object | None, bool]:
    """Return ``(device_type, available)`` without trusting fragile Qt wrappers."""

    device_getter = getattr(event, "device", None)
    if not callable(device_getter):
        return None, False
    try:
        device = device_getter()
        type_getter = getattr(device, "type", None)
        if device is None or not callable(type_getter):
            return None, False
        return type_getter(), True
    except (RuntimeError, TypeError):
        return None, False


def is_trackpad_wheel_event(event: Any) -> bool:
    """Return whether *event* is a two-finger touchpad scroll.

    A real Qt device classification is authoritative.  ``pixelDelta`` is used
    only as a compatibility fallback for tests and platform wrappers that do
    not expose the originating pointing device.
    """

    device_type, device_available = _event_device_type(event)
    if device_available:
        return device_type == QInputDevice.DeviceType.TouchPad

    pixel_delta_getter = getattr(event, "pixelDelta", None)
    if not callable(pixel_delta_getter):
        return False
    try:
        return not pixel_delta_getter().isNull()
    except (RuntimeError, TypeError, AttributeError):
        return False


def pan_delta_from_wheel(event: Any) -> QPointF | None:
    """Return the direct-manipulation pixel delta for a touchpad wheel event."""

    if not is_trackpad_wheel_event(event):
        return None
    pixel_delta_getter = getattr(event, "pixelDelta", None)
    if not callable(pixel_delta_getter):
        return QPointF()
    try:
        delta = pixel_delta_getter()
        return QPointF(float(delta.x()), float(delta.y()))
    except (RuntimeError, TypeError, AttributeError):
        return QPointF()


def is_scroll_end_event(event: Any) -> bool:
    """Return whether *event* terminates a phased touchpad scroll."""

    phase_getter = getattr(event, "phase", None)
    if not callable(phase_getter):
        return False
    try:
        return phase_getter() == Qt.ScrollPhase.ScrollEnd
    except RuntimeError:
        return False


def event_position(event: Any) -> QPointF:
    """Return the local gesture position using current and legacy Qt APIs."""

    for name in ("position", "pos"):
        getter = getattr(event, name, None)
        if not callable(getter):
            continue
        try:
            return QPointF(getter())
        except (RuntimeError, TypeError):
            continue
    return QPointF()


def event_global_position(event: Any) -> QPointF | None:
    """Return the screen-space event position across current and legacy Qt APIs."""

    for name in ("globalPosition", "screenPos", "globalPos"):
        getter = getattr(event, name, None)
        if not callable(getter):
            continue
        try:
            return QPointF(getter())
        except (RuntimeError, TypeError):
            continue
    return None


def global_position_in_widget(global_position: QPointF, widget: QWidget) -> QPointF | None:
    """Map a screen-space point into a visible widget."""

    if not widget.isVisible():
        return None
    try:
        local_position = QPointF(widget.mapFromGlobal(global_position.toPoint()))
    except (RuntimeError, TypeError):
        return None
    if not QRectF(widget.rect()).contains(local_position):
        return None
    return local_position


def global_event_position_in_widget(event: Any, widget: QWidget) -> QPointF | None:
    """Map a global event into a visible widget without trusting its receiver.

    On macOS, QRhi, OpenGL window containers, and native child views can cause
    ``QNativeGestureEvent`` to be delivered to an internal ``QWindow`` rather
    than the logical widget.  Screen-space hit testing remains stable across
    those surfaces.  Selection between overlapping gesture targets is handled
    centrally by :class:`_NativeGestureRouter`.
    """

    global_position = event_global_position(event)
    if global_position is None:
        return None
    return global_position_in_widget(global_position, widget)


def zoom_factor_from_pinch_gesture(event: Any) -> float | None:
    """Return the incremental factor carried by a ``QPinchGesture`` event."""

    gesture_getter = getattr(event, "gesture", None)
    if not callable(gesture_getter):
        return None
    try:
        pinch = gesture_getter(Qt.GestureType.PinchGesture)
    except (RuntimeError, TypeError):
        return None
    if pinch is None:
        return None
    try:
        factor = float(pinch.scaleFactor())
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return None
    if not math.isfinite(factor) or factor <= 0.0 or abs(factor - 1.0) <= 1e-6:
        return None
    return max(_MIN_GESTURE_ZOOM_FACTOR, min(_MAX_GESTURE_ZOOM_FACTOR, factor))


def pinch_gesture_global_position(event: Any) -> QPointF | None:
    """Return the screen-space center of a ``QPinchGesture`` event."""

    gesture_getter = getattr(event, "gesture", None)
    if not callable(gesture_getter):
        return None
    try:
        pinch = gesture_getter(Qt.GestureType.PinchGesture)
        return None if pinch is None else QPointF(pinch.centerPoint())
    except (RuntimeError, TypeError, AttributeError):
        return None


def zoom_factor_from_gesture_event(event: Any) -> float | None:
    """Parse either a native zoom event or a Qt pinch gesture event."""

    return zoom_factor_from_native_gesture(event) or zoom_factor_from_pinch_gesture(event)


def gesture_global_position(event: Any) -> QPointF | None:
    """Return the global anchor for native and recognized pinch gestures."""

    return event_global_position(event) or pinch_gesture_global_position(event)


def zoom_factor_from_touchpad_pinch_wheel(event: Any) -> float | None:
    """Parse the Ctrl-modified touchpad wheel fallback used by some Qt surfaces."""

    if not is_trackpad_wheel_event(event):
        return None
    modifiers_getter = getattr(event, "modifiers", None)
    angle_getter = getattr(event, "angleDelta", None)
    if not callable(modifiers_getter) or not callable(angle_getter):
        return None
    try:
        modifiers = modifiers_getter()
        if not modifiers & Qt.KeyboardModifier.ControlModifier:
            return None
        delta = float(angle_getter().y())
    except (RuntimeError, TypeError, AttributeError):
        return None
    if not math.isfinite(delta) or abs(delta) <= 1e-6:
        return None
    factor = 1.0 + delta / 1200.0
    return max(_MIN_GESTURE_ZOOM_FACTOR, min(_MAX_GESTURE_ZOOM_FACTOR, factor))


class _NativeGestureRouter(QObject):
    """Choose exactly one visible viewport for each application native gesture."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._order = 0
        self._targets: dict[
            int,
            tuple[
                weakref.ReferenceType[QWidget],
                weakref.ReferenceType[Any],
                int,
            ],
        ] = {}
        app.installEventFilter(self)
        self._mac_native_filter: _MacNativeMagnifyFilter | None = None
        if sys.platform == "darwin":
            native_filter = _MacNativeMagnifyFilter(self)
            if native_filter.available:
                app.installNativeEventFilter(native_filter)
                self._mac_native_filter = native_filter

    def register(self, widget: QWidget, callback: Any) -> None:
        self._order += 1
        try:
            callback_ref: weakref.ReferenceType[Any] = weakref.WeakMethod(callback)
        except TypeError:
            callback_ref = weakref.ref(callback)
        key = id(widget)
        self._targets[key] = (
            weakref.ref(widget, lambda _ref, target_key=key: self._targets.pop(target_key, None)),
            callback_ref,
            self._order,
        )
        widget.grabGesture(Qt.GestureType.PinchGesture)

    def unregister(self, widget: QWidget) -> None:
        self._targets.pop(id(widget), None)
        try:
            widget.ungrabGesture(Qt.GestureType.PinchGesture)
        except RuntimeError:
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() not in (QEvent.Type.NativeGesture, QEvent.Type.Gesture):
            return False
        if zoom_factor_from_gesture_event(event) is None:
            return False

        global_positions: list[QPointF] = []
        global_position = gesture_global_position(event)
        if global_position is not None:
            global_positions.append(global_position)
        if event.type() == QEvent.Type.Gesture:
            event_widget_getter = getattr(event, "widget", None)
            event_widget = event_widget_getter() if callable(event_widget_getter) else None
            if isinstance(event_widget, QWidget):
                center = pinch_gesture_global_position(event)
                if center is not None:
                    try:
                        mapped_center = QPointF(event_widget.mapToGlobal(center.toPoint()))
                    except (RuntimeError, TypeError):
                        mapped_center = None
                    if mapped_center is not None and mapped_center not in global_positions:
                        global_positions.append(mapped_center)
        if not global_positions:
            return False

        candidates: list[tuple[int, int, int, Any, QPointF]] = []
        stale_keys: list[int] = []
        for key, (widget_ref, callback_ref, order) in self._targets.items():
            widget = widget_ref()
            callback = callback_ref()
            if widget is None or callback is None:
                stale_keys.append(key)
                continue
            for candidate_global_position in global_positions:
                local_position = global_position_in_widget(candidate_global_position, widget)
                if local_position is None:
                    continue
                try:
                    hit_widget = QApplication.widgetAt(candidate_global_position.toPoint())
                except (RuntimeError, TypeError):
                    hit_widget = None
                hit_score = int(
                    hit_widget is widget
                    or (
                        hit_widget is not None
                        and widget.isAncestorOf(hit_widget)
                    )
                )
                window_score = int(self._receiver_shares_window(watched, widget))
                candidates.append((hit_score, window_score, order, callback, local_position))

        for key in stale_keys:
            self._targets.pop(key, None)
        candidates.sort(key=lambda candidate: candidate[:3], reverse=True)
        for _hit_score, _window_score, _order, callback, local_position in candidates:
            try:
                if callback(event, local_position):
                    return True
            except RuntimeError:
                continue
        return False

    def route_native_magnification(
        self,
        magnification: float,
        global_position: QPointF,
    ) -> bool:
        """Route a Cocoa magnification before Qt converts it to a QEvent."""

        if not math.isfinite(magnification) or abs(magnification) <= 1e-6:
            return False
        synthetic = QNativeGestureEvent(
            Qt.NativeGestureType.ZoomNativeGesture,
            QPointingDevice.primaryPointingDevice(),
            2,
            global_position,
            global_position,
            global_position,
            float(magnification),
            QPointF(),
            0,
        )
        return self.eventFilter(self._app, synthetic)

    @staticmethod
    def _receiver_shares_window(watched: QObject, widget: QWidget) -> bool:
        top_level = widget.window()
        if isinstance(watched, QWidget):
            return watched.window() is top_level
        window_handle = top_level.windowHandle()
        current: QObject | None = watched
        while current is not None:
            if current is top_level or current is window_handle:
                return True
            try:
                current = current.parent()
            except RuntimeError:
                break
        return False


class _MacNativeMagnifyFilter(QAbstractNativeEventFilter):
    """Read Cocoa magnification events before a Qt surface can lose them."""

    def __init__(self, router: _NativeGestureRouter) -> None:
        super().__init__()
        self._router_ref = weakref.ref(router)
        self._event_type_message: Any | None = None
        self._magnification_message: Any | None = None
        if sys.platform != "darwin":
            return
        try:
            objc = cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            objc.sel_registerName.argtypes = [c_char_p]
            objc.sel_registerName.restype = c_void_p
            message_address = cast(objc.objc_msgSend, c_void_p).value
            if message_address is None:
                return
            self._event_type_selector = objc.sel_registerName(b"type")
            self._magnification_selector = objc.sel_registerName(b"magnification")
            self._event_type_message = CFUNCTYPE(c_ulong, c_void_p, c_void_p)(
                message_address
            )
            self._magnification_message = CFUNCTYPE(c_double, c_void_p, c_void_p)(
                message_address
            )
        except (AttributeError, OSError, TypeError, ValueError):
            self._event_type_message = None
            self._magnification_message = None

    @property
    def available(self) -> bool:
        return self._event_type_message is not None and self._magnification_message is not None

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if not self.available:
            return False
        try:
            native_event_type = bytes(event_type)
        except (TypeError, ValueError):
            return False
        if native_event_type != _MAC_GENERIC_NS_EVENT:
            return False
        try:
            event_pointer = c_void_p(int(message))
            native_type = self._event_type_message(
                event_pointer,
                self._event_type_selector,
            )
            if native_type != _NS_EVENT_TYPE_MAGNIFY:
                return False
            magnification = float(
                self._magnification_message(event_pointer, self._magnification_selector)
            )
        except (OverflowError, TypeError, ValueError):
            return False
        router = self._router_ref()
        if router is None:
            return False
        return router.route_native_magnification(
            magnification,
            QPointF(QCursor.pos()),
        )


_NATIVE_GESTURE_ROUTER: _NativeGestureRouter | None = None


def _native_gesture_router() -> _NativeGestureRouter | None:
    global _NATIVE_GESTURE_ROUTER
    app = QApplication.instance()
    if app is None:
        return None
    if _NATIVE_GESTURE_ROUTER is None or _NATIVE_GESTURE_ROUTER.parent() is not app:
        _NATIVE_GESTURE_ROUTER = _NativeGestureRouter(app)
    return _NATIVE_GESTURE_ROUTER


def register_native_gesture_target(widget: QWidget, callback: Any) -> bool:
    """Register a visible photo or map viewport with the shared gesture router."""

    router = _native_gesture_router()
    if router is None:
        return False
    router.register(widget, callback)
    return True


def unregister_native_gesture_target(widget: QWidget) -> None:
    """Remove a viewport from shared native gesture routing."""

    router = _NATIVE_GESTURE_ROUTER
    if router is not None:
        router.unregister(widget)


def zoom_factor_from_native_gesture(event: Any) -> float | None:
    """Return a bounded incremental factor for ``ZoomNativeGesture``."""

    gesture_type_getter = getattr(event, "gestureType", None)
    value_getter = getattr(event, "value", None)
    if not callable(gesture_type_getter) or not callable(value_getter):
        return None
    try:
        if gesture_type_getter() != Qt.NativeGestureType.ZoomNativeGesture:
            return None
        value = float(value_getter())
    except (RuntimeError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or abs(value) <= 1e-6:
        return None
    return max(
        _MIN_GESTURE_ZOOM_FACTOR,
        min(_MAX_GESTURE_ZOOM_FACTOR, 1.0 + value),
    )


__all__ = [
    "event_global_position",
    "event_position",
    "gesture_global_position",
    "global_event_position_in_widget",
    "global_position_in_widget",
    "is_scroll_end_event",
    "is_trackpad_wheel_event",
    "pan_delta_from_wheel",
    "register_native_gesture_target",
    "unregister_native_gesture_target",
    "zoom_factor_from_gesture_event",
    "zoom_factor_from_native_gesture",
    "zoom_factor_from_pinch_gesture",
    "zoom_factor_from_touchpad_pinch_wheel",
]

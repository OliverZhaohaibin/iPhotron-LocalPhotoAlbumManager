from __future__ import annotations

import math
import os
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for photo map tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore is required for photo map tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for photo map tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="QtWidgets is required for photo map tests", exc_type=ImportError)

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.widgets import photo_map_view as photo_map_view_module
from maps.map_sources import MapBackendMetadata, MapSourceSpec
from maps.map_widget.map_gl_widget import MapGLWidget
from maps.map_widget.native_osmand_widget import NativeOsmAndWidget
from maps.map_widget.qt_location_map_widget import QtLocationMapWidget


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeNativeLibrary:
    def __init__(self) -> None:
        self.zoom = 2.0
        self.center_lon = 0.0
        self.center_lat = 0.0

    def osmand_create_map_widget(self, *_args) -> int:
        return 1

    def osmand_widget_get_zoom(self, _pointer) -> float:
        return self.zoom

    def osmand_widget_get_min_zoom(self, _pointer) -> float:
        return 2.0

    def osmand_widget_get_max_zoom(self, _pointer) -> float:
        return 19.0

    def osmand_widget_set_zoom(self, _pointer, zoom_level: float) -> None:
        self.zoom = float(zoom_level)

    def osmand_widget_reset_view(self, _pointer) -> None:
        self.zoom = 2.0
        self.center_lon = 0.0
        self.center_lat = 0.0

    def osmand_widget_pan_by_pixels(self, _pointer, delta_x: float, delta_y: float) -> None:
        self.center_lon -= float(delta_x) * 0.05
        self.center_lat = max(-80.0, min(80.0, self.center_lat + float(delta_y) * 0.05))

    def osmand_widget_set_center_lonlat(self, _pointer, longitude: float, latitude: float) -> None:
        self.center_lon = float(longitude)
        self.center_lat = float(latitude)

    def osmand_widget_get_center_lonlat(self, _pointer, longitude, latitude) -> None:
        longitude._obj.value = self.center_lon
        latitude._obj.value = self.center_lat

    def osmand_widget_project_lonlat(self, _pointer, longitude, latitude, screen_x, screen_y) -> int:
        screen_x._obj.value = float(longitude) * 10.0 + 5.0
        screen_y._obj.value = float(latitude) * 10.0 + 7.0
        return 1


class _FakeNativeChild(QWidget):
    def __init__(self, library: _FakeNativeLibrary) -> None:
        super().__init__()
        self._library = library
        self._dragging = False
        self._last_mouse_pos = QPointF()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            current_pos = event.position()
            delta = current_pos - self._last_mouse_pos
            self._last_mouse_pos = current_pos
            if not delta.isNull():
                self._library.osmand_widget_pan_by_pixels(None, delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta:
            zoom_factor = 1.0 + float(delta) / 1200.0
            self._library.osmand_widget_set_zoom(None, self._library.zoom * zoom_factor)
            event.accept()
            return
        super().wheelEvent(event)


class _DummyThumbnailLoader(QObject):
    ready = Signal(Path, str, QPixmap)

    def reset_for_album(self, root: Path) -> None:
        del root
        return None

    def request(self, *args, **kwargs):
        del args, kwargs
        return None


class _DummyMarkerController(QObject):
    clustersUpdated = Signal(list)
    citiesUpdated = Signal(list)
    assetActivated = Signal(str)
    clusterActivated = Signal(list)
    thumbnailUpdated = Signal(str, QPixmap)
    thumbnailsInvalidated = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        del args, kwargs

    def handle_view_changed(self, *args, **kwargs) -> None:
        del args, kwargs
        return None

    def handle_pan(self, *args, **kwargs) -> None:
        del args, kwargs
        return None

    def handle_pan_finished(self, *args, **kwargs) -> None:
        del args, kwargs
        return None

    def handle_thumbnail_ready(self, *args, **kwargs) -> None:
        del args, kwargs
        return None

    def cluster_at(self, position: QPointF):
        del position
        return None

    def handle_marker_click(self, cluster) -> None:
        del cluster
        return None

    def set_assets(self, *args, **kwargs) -> None:
        del args, kwargs
        return None

    def clear(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def handle_resize(self) -> None:
        return None


class _FallbackMapWidget(QWidget):
    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()

    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent)
        self._zoom = 2.0
        self._metadata = MapBackendMetadata(2.0, 19.0, True, "raster", "xyz")
        self._map_source = map_source

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        self._zoom = float(zoom)

    def reset_view(self) -> None:
        self._zoom = 2.0

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        del delta_x, delta_y
        return None

    def center_lonlat(self) -> tuple[float, float]:
        return 0.0, 0.0

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        del lon, lat
        return None

    def center_on(self, lon: float, lat: float) -> None:
        del lon, lat
        return None

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        del lon, lat
        self._zoom += float(zoom_delta)

    def shutdown(self) -> None:
        return None

    def map_backend_metadata(self) -> MapBackendMetadata:
        return self._metadata

    def set_city_annotations(self, cities) -> None:
        del cities
        return None

    def city_at(self, position: QPointF) -> str | None:
        del position
        return None

    def event_target(self) -> QWidget:
        return self


def test_choose_map_widget_backend_prefers_native_when_runtime_is_available(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "probe_native_widget_runtime", lambda root: (True, None))
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: True)

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is NativeOsmAndWidget
    assert backend_kind == "osmand_native"
    assert resolved_source is not None
    assert resolved_source.kind == "osmand_obf"


def test_choose_map_widget_backend_falls_back_to_python_obf_when_native_probe_fails(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "probe_native_widget_runtime", lambda root: (False, "runtime error"))
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: True)

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is MapGLWidget
    assert backend_kind == "osmand_python"
    assert resolved_source is not None
    assert resolved_source.kind == "osmand_obf"


def test_choose_map_widget_backend_uses_qt_location_when_obf_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: False)

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is QtLocationMapWidget
    assert resolved_source is None
    assert backend_kind == "qtlocation"


def test_native_osmand_widget_bridges_drag_release_and_wheel_events(qapp: QApplication, monkeypatch, tmp_path) -> None:
    fake_library = _FakeNativeLibrary()
    fake_child = _FakeNativeChild(fake_library)
    dummy_dll = tmp_path / "osmand_native_widget.dll"
    dummy_dll.write_bytes(b"dll")

    monkeypatch.setattr(
        photo_map_view_module,
        "check_opengl_support",
        lambda: True,
    )
    monkeypatch.setattr(
        "maps.map_widget.native_osmand_widget.resolve_osmand_native_widget_library",
        lambda root: dummy_dll,
    )
    monkeypatch.setattr(
        "maps.map_widget.native_osmand_widget._load_bridge",
        lambda path: type("Bridge", (), {"library": fake_library})(),
    )
    monkeypatch.setattr("maps.map_widget.native_osmand_widget.shiboken6.getCppPointer", lambda widget: (1,))
    monkeypatch.setattr(
        "maps.map_widget.native_osmand_widget.shiboken6.wrapInstance",
        lambda pointer, cls: fake_child,
    )

    source = MapSourceSpec(
        kind="osmand_obf",
        data_path=tmp_path / "world.obf",
        resources_root=tmp_path,
        style_path=tmp_path / "style.xml",
    )
    Path(source.data_path).write_bytes(b"obf")
    Path(source.style_path).write_text("<renderingStyle />", encoding="utf-8")

    widget = NativeOsmAndWidget(map_source=source)
    panned_spy = QSignalSpy(widget.panned)
    pan_finished_spy = QSignalSpy(widget.panFinished)
    view_changed_spy = QSignalSpy(widget.viewChanged)
    initial_view_change_count = view_changed_spy.count()
    diagnostics = photo_map_view_module.format_map_runtime_diagnostics(
        widget,
        backend_kind="osmand_native",
        map_source=source,
    )

    try:
        event_target = cast(QWidget, widget.event_target())

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 20.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, press_event)

        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(44.0, 28.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, move_event)
        qapp.processEvents()

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(44.0, 28.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, release_event)

        wheel_event = QWheelEvent(
            QPointF(44.0, 28.0),
            QPointF(44.0, 28.0),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(event_target, wheel_event)
        qapp.processEvents()

        projected = widget.project_lonlat(1.5, 2.5)

        assert panned_spy.count() >= 1
        assert pan_finished_spy.count() == 1
        assert view_changed_spy.count() > initial_view_change_count
        assert math.isclose(fake_library.zoom, 2.2, rel_tol=1e-6)
        assert projected is not None
        assert math.isclose(projected.x(), 20.0, rel_tol=1e-6)
        assert math.isclose(projected.y(), 32.0, rel_tol=1e-6)
        assert "backend=osmand_native" in diagnostics
        assert "confirmed_gl=true" in diagnostics
        assert "widget=NativeOsmAndWidget" in diagnostics
        assert f"native_dll={dummy_dll.resolve()}" in diagnostics
    finally:
        widget.shutdown()
        widget.close()


def test_photo_map_view_falls_back_to_python_widget_when_native_init_fails(
    qapp: QApplication,
    monkeypatch,
    tmp_path,
) -> None:
    del qapp

    source = MapSourceSpec(
        kind="osmand_obf",
        data_path=tmp_path / "world.obf",
        resources_root=tmp_path,
        style_path=tmp_path / "style.xml",
    )

    class _RaisingNativeWidget(QWidget):
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("native init failed")

    monkeypatch.setattr(
        photo_map_view_module,
        "choose_map_widget_backend",
        lambda map_source, use_opengl: (_RaisingNativeWidget, source, "osmand_native"),
    )
    monkeypatch.setattr(photo_map_view_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(photo_map_view_module, "MapGLWidget", _FallbackMapWidget)
    monkeypatch.setattr(photo_map_view_module, "ThumbnailLoader", _DummyThumbnailLoader)
    monkeypatch.setattr(photo_map_view_module, "MarkerController", _DummyMarkerController)

    view = photo_map_view_module.PhotoMapView(map_source=source)
    try:
        assert isinstance(view.map_widget(), _FallbackMapWidget)
        assert "backend=osmand_python" in view.runtime_diagnostics()
        assert "confirmed_gl=true" in view.runtime_diagnostics()
    finally:
        view.close()

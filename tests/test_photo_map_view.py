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

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QHideEvent,
    QImage,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
    QWindow,
)
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.widgets import photo_map_view as photo_map_view_module
from maps.map_sources import MapBackendMetadata, MapSourceSpec
from maps.map_widget import map_gl_widget as map_gl_widget_module
from maps.map_widget import map_widget as map_widget_module
from maps.map_widget import native_osmand_widget as native_osmand_widget_module
from maps.map_widget.map_gl_widget import MapGLWidget, MapGLWindowWidget
from maps.map_widget.native_osmand_widget import NativeOsmAndWidget, _render_marker_buffer


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _override_cursor_shape() -> Qt.CursorShape | None:
    cursor = QApplication.overrideCursor()
    return None if cursor is None else cursor.shape()


def _clear_override_cursors() -> None:
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


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


def test_map_gl_surface_format_requests_alpha_on_macos() -> None:
    surface_format = map_gl_widget_module._map_gl_surface_format(platform="darwin")

    assert surface_format.depthBufferSize() == 24
    assert surface_format.stencilBufferSize() == 8
    assert surface_format.alphaBufferSize() == 8
    assert surface_format.samples() == 0


def test_map_gl_surface_format_is_opaque_and_unsampled_on_non_macos() -> None:
    surface_format = map_gl_widget_module._map_gl_surface_format(platform="linux")

    assert surface_format.depthBufferSize() == 24
    assert surface_format.stencilBufferSize() == 8
    assert surface_format.alphaBufferSize() == 0
    assert surface_format.samples() == 0


def test_map_gl_prefers_no_partial_update_on_macos_and_linux() -> None:
    assert map_gl_widget_module._map_gl_uses_no_partial_update(platform="darwin", environ={})
    assert map_gl_widget_module._map_gl_uses_no_partial_update(platform="linux", environ={})
    assert not map_gl_widget_module._map_gl_uses_no_partial_update(platform="win32", environ={})
    assert not map_gl_widget_module._map_gl_uses_no_partial_update(
        platform="darwin",
        environ={"IPHOTO_OSMAND_GL_PARTIAL_UPDATE": "1"},
    )


def test_map_gl_uses_stack_on_top_only_on_macos() -> None:
    assert map_gl_widget_module._map_gl_uses_window_container(platform="darwin")
    assert not map_gl_widget_module._map_gl_uses_window_container(platform="linux")
    assert not map_gl_widget_module._map_gl_uses_window_container(platform="win32")


def test_map_gl_debug_switch_is_opt_in() -> None:
    assert map_gl_widget_module._map_gl_debug_enabled({"IPHOTO_MAP_GL_DEBUG": "1"})
    assert not map_gl_widget_module._map_gl_debug_enabled({})


def test_map_gl_widget_uses_opaque_attrs_and_full_updates_on_macos(
    qapp: QApplication,
    monkeypatch,
) -> None:
    del qapp

    class _Controller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def add_view_listener(self, *_args) -> None:
            return None

        def add_pan_listener(self, *_args) -> None:
            return None

        def add_pan_finished_listener(self, *_args) -> None:
            return None

        def view_state(self) -> tuple[float, float, float]:
            return (0.5, 0.5, 2.0)

        def shutdown(self) -> None:
            return None

    scheduled_callbacks: list[tuple[int, object]] = []
    monkeypatch.setattr(map_gl_widget_module, "MapWidgetController", _Controller)
    monkeypatch.setattr(map_gl_widget_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        map_gl_widget_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled_callbacks.append((delay, callback)),
    )
    monkeypatch.delenv("IPHOTO_OSMAND_GL_PARTIAL_UPDATE", raising=False)

    widget = map_gl_widget_module.MapGLWidget()
    try:
        assert widget.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        assert (
            widget.updateBehavior()
            == map_gl_widget_module.QOpenGLWidget.UpdateBehavior.NoPartialUpdate
        )
        assert widget.format().alphaBufferSize() == 8
        widget.showEvent(QShowEvent())
        widget.resizeEvent(QResizeEvent(QSize(100, 80), QSize(90, 70)))
        assert scheduled_callbacks == [
            (0, widget.request_full_update),
            (0, widget.request_full_update),
        ]
        widget.setUpdatesEnabled(True)
        widget.hideEvent(QHideEvent())
        assert widget.updatesEnabled()
    finally:
        widget.close()


def test_legacy_map_widget_uses_opaque_attrs(qapp: QApplication, monkeypatch) -> None:
    del qapp

    class _Controller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def add_view_listener(self, *_args) -> None:
            return None

        def add_pan_listener(self, *_args) -> None:
            return None

        def add_pan_finished_listener(self, *_args) -> None:
            return None

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(map_widget_module, "MapWidgetController", _Controller)

    widget = map_widget_module.MapWidget()
    try:
        assert widget.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert widget.autoFillBackground()
        assert widget.palette().color(widget.backgroundRole()).alpha() == 255
    finally:
        widget.close()


def test_photo_map_view_renders_markers_inside_gl_widget(
    qapp: QApplication,
    monkeypatch,
    tmp_path,
) -> None:
    del qapp

    source = MapSourceSpec(
        kind="legacy_pbf",
        data_path=tmp_path / "tiles",
        style_path=tmp_path / "style.json",
    )

    class _FakeGLMapWidget(_FallbackMapWidget):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.post_render_painters = []
            self.update_requests = 0

        def add_post_render_painter(self, callback) -> None:
            self.post_render_painters.append(callback)

        def remove_post_render_painter(self, callback) -> None:
            self.post_render_painters = [
                existing for existing in self.post_render_painters if existing != callback
            ]

        def request_full_update(self) -> None:
            self.update_requests += 1

    monkeypatch.setattr(photo_map_view_module, "MapGLWidget", _FakeGLMapWidget)
    monkeypatch.setattr(
        photo_map_view_module,
        "choose_map_widget_backend",
        lambda map_source, use_opengl: (photo_map_view_module.MapGLWidget, source, "legacy_python"),
    )
    monkeypatch.setattr(photo_map_view_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(photo_map_view_module, "ThumbnailLoader", _DummyThumbnailLoader)
    monkeypatch.setattr(photo_map_view_module, "MarkerController", _DummyMarkerController)

    view = photo_map_view_module.PhotoMapView(map_source=source)
    try:
        map_widget = view.map_widget()
        assert isinstance(map_widget, _FakeGLMapWidget)
        assert len(map_widget.post_render_painters) == 1
        assert view._overlay.parent() is None
        view._overlay.set_clusters([])
        assert map_widget.update_requests == 1
    finally:
        view.close()


def test_marker_callout_background_is_opaque(qapp: QApplication, tmp_path) -> None:
    del qapp

    asset = photo_map_view_module.GeotaggedAsset(
        library_relative="album/photo.jpg",
        album_relative="photo.jpg",
        absolute_path=tmp_path / "album" / "photo.jpg",
        album_path=tmp_path / "album",
        asset_id="asset-1",
        latitude=10.0,
        longitude=20.0,
        is_image=True,
        is_video=False,
        still_image_time=None,
        duration=None,
        location_name=None,
        live_photo_group_id=None,
        live_partner_rel=None,
    )
    cluster = photo_map_view_module._MarkerCluster(
        representative=asset,
        screen_pos=QPointF(100.0, 100.0),
    )
    layer = photo_map_view_module._MarkerLayer()
    image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black)

    painter = photo_map_view_module.QPainter(image)
    try:
        layer.set_clusters([cluster])
        layer.paint_markers(painter)
    finally:
        painter.end()

    sample = image.pixelColor(100, 24)
    assert sample.alpha() == 255
    assert sample.red() == 255
    assert sample.green() == 255
    assert sample.blue() == 255


def test_native_marker_overlay_precomposes_opaque_marker_buffer(
    qapp: QApplication,
    tmp_path,
) -> None:
    del qapp

    asset = photo_map_view_module.GeotaggedAsset(
        library_relative="album/photo.jpg",
        album_relative="photo.jpg",
        absolute_path=tmp_path / "album" / "photo.jpg",
        album_path=tmp_path / "album",
        asset_id="asset-1",
        latitude=10.0,
        longitude=20.0,
        is_image=True,
        is_video=False,
        still_image_time=None,
        duration=None,
        location_name=None,
        live_photo_group_id=None,
        live_partner_rel=None,
    )
    cluster = photo_map_view_module._MarkerCluster(
        representative=asset,
        screen_pos=QPointF(100.0, 100.0),
    )
    layer = photo_map_view_module._MarkerLayer()
    layer.set_clusters([cluster])

    image = _render_marker_buffer(QSize(200, 200), 1.0, [layer.paint_markers])
    sample = image.pixelColor(100, 24)

    assert not image.isNull()
    assert sample.alpha() == 255
    assert sample.red() == 255
    assert sample.green() == 255
    assert sample.blue() == 255


def test_photo_map_view_uses_widget_overlay_when_post_render_is_unsupported(
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

    class _FakeNativeWithoutPostRender(_FallbackMapWidget):
        def supports_post_render_painter(self) -> bool:
            return False

        def add_post_render_painter(self, _callback) -> None:
            raise AssertionError("post-render painter should not be registered")

    monkeypatch.setattr(
        photo_map_view_module,
        "choose_map_widget_backend",
        lambda map_source, use_opengl: (_FakeNativeWithoutPostRender, source, "osmand_native"),
    )
    monkeypatch.setattr(photo_map_view_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(photo_map_view_module, "ThumbnailLoader", _DummyThumbnailLoader)
    monkeypatch.setattr(photo_map_view_module, "MarkerController", _DummyMarkerController)

    view = photo_map_view_module.PhotoMapView(map_source=source)
    try:
        assert isinstance(view.map_widget(), _FakeNativeWithoutPostRender)
        assert view._overlay.parent() is view
    finally:
        view.close()


def test_choose_map_widget_backend_prefers_native_when_runtime_files_are_available(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: True)
    monkeypatch.setattr(photo_map_view_module, "probe_native_widget_runtime", lambda root: (True, None))

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is NativeOsmAndWidget
    assert backend_kind == "osmand_native"
    assert resolved_source is not None
    assert resolved_source.kind == "osmand_obf"


def test_choose_map_widget_backend_still_prefers_native_when_generic_opengl_probe_failed(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: True)
    monkeypatch.setattr(photo_map_view_module, "probe_native_widget_runtime", lambda root: (True, None))
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=False,
    )

    assert widget_cls is NativeOsmAndWidget
    assert backend_kind == "osmand_native"
    assert resolved_source is not None
    assert resolved_source.kind == "osmand_obf"


def test_photo_map_check_opengl_support_accepts_valid_context_when_offscreen_make_current_fails(
    monkeypatch,
) -> None:
    class FakeSurface:
        def create(self) -> None:
            return None

        def isValid(self) -> bool:
            return True

    class FakeContext:
        def create(self) -> bool:
            return True

        def isValid(self) -> bool:
            return True

        def makeCurrent(self, surface) -> bool:
            del surface
            return False

        def doneCurrent(self) -> None:
            return None

    monkeypatch.setattr(photo_map_view_module, "QOffscreenSurface", lambda: FakeSurface())
    monkeypatch.setattr(photo_map_view_module, "QOpenGLContext", lambda: FakeContext())
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "linux")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    assert photo_map_view_module.check_opengl_support() is True


def test_photo_map_check_opengl_support_requires_make_current_on_macos(monkeypatch) -> None:
    class FakeSurface:
        def create(self) -> None:
            return None

        def isValid(self) -> bool:
            return True

    class FakeContext:
        def create(self) -> bool:
            return True

        def isValid(self) -> bool:
            return True

        def makeCurrent(self, surface) -> bool:
            del surface
            return False

        def doneCurrent(self) -> None:
            return None

    monkeypatch.setattr(photo_map_view_module, "QOffscreenSurface", lambda: FakeSurface())
    monkeypatch.setattr(photo_map_view_module, "QOpenGLContext", lambda: FakeContext())
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "darwin")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    assert photo_map_view_module.check_opengl_support() is False


def test_choose_map_widget_backend_uses_python_obf_when_native_widget_files_are_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "linux")
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: False)
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


def test_choose_map_widget_backend_uses_python_obf_when_native_runtime_probe_fails(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "linux")
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: True)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: True)
    monkeypatch.setattr(
        photo_map_view_module,
        "probe_native_widget_runtime",
        lambda root: (False, "OSError: runtime mismatch"),
    )

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is MapGLWidget
    assert backend_kind == "osmand_python"
    assert resolved_source is not None
    assert resolved_source.kind == "osmand_obf"


def test_probe_native_widget_runtime_does_not_load_before_qapplication_on_macos(
    monkeypatch,
    tmp_path,
) -> None:
    dummy_dylib = tmp_path / "osmand_native_widget.dylib"
    dummy_dylib.write_bytes(b"dylib")
    load_calls: list[Path] = []

    monkeypatch.setattr(native_osmand_widget_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        native_osmand_widget_module,
        "resolve_osmand_native_widget_library",
        lambda root: dummy_dylib,
    )
    monkeypatch.setattr(native_osmand_widget_module, "_has_qapplication_instance", lambda: False)
    monkeypatch.setattr(
        native_osmand_widget_module,
        "_load_bridge",
        lambda path: load_calls.append(path),
    )

    available, reason = native_osmand_widget_module.probe_native_widget_runtime(tmp_path)

    assert available is False
    assert reason is not None
    assert "QApplication" in reason
    assert load_calls == []


def test_probe_native_widget_runtime_loads_after_qapplication_on_macos(
    monkeypatch,
    tmp_path,
) -> None:
    dummy_dylib = tmp_path / "osmand_native_widget.dylib"
    dummy_dylib.write_bytes(b"dylib")
    load_calls: list[Path] = []

    monkeypatch.setattr(native_osmand_widget_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        native_osmand_widget_module,
        "resolve_osmand_native_widget_library",
        lambda root: dummy_dylib,
    )
    monkeypatch.setattr(native_osmand_widget_module, "_has_qapplication_instance", lambda: True)
    monkeypatch.setattr(
        native_osmand_widget_module,
        "_load_bridge",
        lambda path: load_calls.append(path) or object(),
    )

    available, reason = native_osmand_widget_module.probe_native_widget_runtime(tmp_path)

    assert available is True
    assert reason is None
    assert load_calls == [dummy_dylib]


def test_choose_map_widget_backend_prefers_python_obf_when_native_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "linux")
    monkeypatch.setattr(photo_map_view_module, "prefer_osmand_native_widget", lambda: False)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: True)
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


def test_choose_map_widget_backend_falls_back_to_legacy_when_obf_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "linux")
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr(photo_map_view_module, "_has_resolved_osmand_assets", lambda source: False)

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        None,
        use_opengl=True,
    )

    assert widget_cls is MapGLWidget
    assert resolved_source is not None
    assert resolved_source.kind == "legacy_pbf"
    assert Path(resolved_source.data_path) == photo_map_view_module._MAPS_PACKAGE_ROOT / "tiles"
    assert backend_kind == "legacy_python"


def test_choose_map_widget_backend_keeps_legacy_gl_on_macos(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(photo_map_view_module.sys, "platform", "darwin")
    source = MapSourceSpec(
        kind="legacy_pbf",
        data_path=tmp_path / "tiles",
        style_path=tmp_path / "style.json",
    )

    widget_cls, resolved_source, backend_kind = photo_map_view_module.choose_map_widget_backend(
        source,
        use_opengl=True,
    )

    assert widget_cls is MapGLWindowWidget
    assert resolved_source is not None
    assert resolved_source.kind == "legacy_pbf"
    assert backend_kind == "legacy_python"


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
        widget.resize(180, 140)
        widget.show()
        qapp.processEvents()

        assert event_target.minimumWidth() == 0
        assert event_target.minimumHeight() == 0
        assert event_target.geometry() == widget.contentsRect()

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 20.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, press_event)
        assert event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert _override_cursor_shape() == Qt.CursorShape.ClosedHandCursor

        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(44.0, 28.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, move_event)
        qapp.processEvents()
        assert event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert _override_cursor_shape() == Qt.CursorShape.ClosedHandCursor

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(44.0, 28.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(event_target, release_event)
        assert event_target.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert _override_cursor_shape() is None

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
        _clear_override_cursors()


def test_native_osmand_widget_uses_exported_event_target(qapp: QApplication, monkeypatch, tmp_path) -> None:
    class _FakeNativeLibraryWithEventTarget(_FakeNativeLibrary):
        def osmand_widget_get_event_target(self, _pointer) -> int:
            return 2

    fake_library = _FakeNativeLibraryWithEventTarget()
    fake_host = QWidget()
    fake_event_target = QWindow()
    dummy_dll = tmp_path / "osmand_native_widget.dll"
    dummy_dll.write_bytes(b"dll")
    monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")

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
        lambda pointer, cls: fake_event_target if int(pointer) == 2 else fake_host,
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

    try:
        assert widget.event_target() is fake_event_target
        assert widget.supports_post_render_painter()

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 20.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(fake_event_target, press_event)
        assert fake_event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert _override_cursor_shape() == Qt.CursorShape.ClosedHandCursor

        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(32.0, 25.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(fake_event_target, move_event)
        assert fake_event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert _override_cursor_shape() == Qt.CursorShape.ClosedHandCursor

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(32.0, 25.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(fake_event_target, release_event)
        assert fake_event_target.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert _override_cursor_shape() is None
        qapp.processEvents()

        assert panned_spy.count() >= 1
        assert pan_finished_spy.count() == 1
    finally:
        widget.shutdown()
        widget.close()
        _clear_override_cursors()


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
    monkeypatch.setattr(photo_map_view_module, "MapGLWindowWidget", _FallbackMapWidget)
    monkeypatch.setattr(photo_map_view_module, "ThumbnailLoader", _DummyThumbnailLoader)
    monkeypatch.setattr(photo_map_view_module, "MarkerController", _DummyMarkerController)

    view = photo_map_view_module.PhotoMapView(map_source=source)
    try:
        assert isinstance(view.map_widget(), _FallbackMapWidget)
        assert "backend=osmand_python" in view.runtime_diagnostics()
        assert "confirmed_gl=true" in view.runtime_diagnostics()
    finally:
        view.close()


def test_photo_map_view_falls_back_to_cpu_widget_when_legacy_gl_init_fails(
    qapp: QApplication,
    monkeypatch,
    tmp_path,
) -> None:
    del qapp, tmp_path

    source = MapSourceSpec(
        kind="legacy_pbf",
        data_path="tiles",
        style_path="style.json",
    )

    class _RaisingGLWidget(QWidget):
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("gl init failed")

    monkeypatch.setattr(
        photo_map_view_module,
        "choose_map_widget_backend",
        lambda map_source, use_opengl: (photo_map_view_module.MapGLWidget, source, "legacy_python"),
    )
    monkeypatch.setattr(photo_map_view_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(photo_map_view_module, "MapGLWidget", _RaisingGLWidget)
    monkeypatch.setattr(photo_map_view_module, "MapWidget", _FallbackMapWidget)
    monkeypatch.setattr(photo_map_view_module, "ThumbnailLoader", _DummyThumbnailLoader)
    monkeypatch.setattr(photo_map_view_module, "MarkerController", _DummyMarkerController)

    view = photo_map_view_module.PhotoMapView(map_source=source)
    try:
        assert isinstance(view.map_widget(), _FallbackMapWidget)
        assert "backend=legacy_python" in view.runtime_diagnostics()
        assert "confirmed_gl=false" in view.runtime_diagnostics()
    finally:
        view.close()

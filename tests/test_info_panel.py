"""Focused tests for the floating info panel widget."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap, QWindow
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.i18n import formatters
from iPhoto.gui.ui.widgets import info_location_map as info_location_map_module
from iPhoto.gui.ui.widgets import info_panel as info_panel_module
from iPhoto.gui.ui.widgets.info_panel import (
    _FACE_ADD_BUTTON_SIZE,
    _FACE_ADD_ICON_SIZE,
    _FACE_AVATAR_DIAMETER,
    InfoPanel,
)
from iPhoto.gui.ui.widgets.recognition_annotations import (
    RecognitionAnnotation,
    RecognitionIdentitySuggestion,
)
from maps.map_sources import MapBackendMetadata, MapSourceSpec


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Ensure a single QApplication instance exists for widget tests."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeMiniMapWidget(QWidget):
    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()
    firstFramePresented = Signal()

    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent)
        self._map_source = map_source
        self._zoom = 2.0
        self._center: tuple[float, float] = (0.0, 0.0)
        self.shutdown_calls = 0
        self.auto_present_first_frame = True
        self.setMinimumSize(640, 480)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        self._zoom = float(zoom)
        self.viewChanged.emit(0.5, 0.5, self._zoom)

    def center_on(self, lon: float, lat: float) -> None:
        self._center = (float(lon), float(lat))
        self.viewChanged.emit(0.5, 0.5, self._zoom)

    def center_lonlat(self) -> tuple[float, float]:
        return self._center

    def reset_view(self) -> None:
        self._center = (0.0, 0.0)
        self._zoom = 2.0
        self.viewChanged.emit(0.5, 0.5, self._zoom)

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        self.panned.emit(QPointF(float(delta_x), float(delta_y)))

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        del lon, lat
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def map_backend_metadata(self) -> MapBackendMetadata:
        return MapBackendMetadata(2.0, 19.0, True, "raster", "xyz")

    def present_first_frame(self) -> None:
        self.firstFramePresented.emit()


@pytest.fixture(autouse=True)
def _shutdown_info_panels_after_test(qapp: QApplication):
    """Keep reusable map runtimes from leaking between widget tests."""

    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, InfoPanel):
            widget.shutdown()
            widget.close()
    qapp.processEvents()


def _process_deferred_panel_content(qapp: QApplication) -> None:
    for _ in range(4):
        qapp.processEvents()
        for widget in QApplication.topLevelWidgets():
            if not isinstance(widget, InfoPanel):
                continue
            map_widget = widget._location_map._map_widget
            if isinstance(map_widget, _FakeMiniMapWidget) and map_widget.auto_present_first_frame:
                map_widget.present_first_frame()


def _expected_panel_size(panel: InfoPanel) -> QSize:
    screen = panel._panel_screen()
    if screen is None:
        return QSize(panel._PANEL_WIDTH, panel._PANEL_HEIGHT)
    available = screen.availableGeometry()
    return QSize(
        min(panel._PANEL_WIDTH, available.width() - panel._SCREEN_HORIZONTAL_MARGIN),
        min(panel._PANEL_HEIGHT, available.height() - panel._SCREEN_VERTICAL_MARGIN),
    )


class _DelayedProjectionMiniMapWidget(_FakeMiniMapWidget):
    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent, map_source=map_source)
        self._projected_point = QPointF(80.0, 80.0)
        self._pending_projected_point = QPointF(self._projected_point)

    def set_zoom(self, zoom: float) -> None:
        self._zoom = float(zoom)
        self._pending_projected_point = QPointF(self.width() / 2.0, self.height() / 2.0)
        self.viewChanged.emit(0.5, 0.5, self._zoom)
        QTimer.singleShot(0, self._apply_pending_projection)

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        del lon, lat
        return QPointF(self._projected_point)

    def _apply_pending_projection(self) -> None:
        self._projected_point = QPointF(self._pending_projected_point)


class _DeferredCenterMiniMapWidget(_FakeMiniMapWidget):
    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent, map_source=map_source)
        self._pending_center: tuple[float, float] | None = None

    def center_on(self, lon: float, lat: float) -> None:
        target = (float(lon), float(lat))
        if not self.isVisible():
            self._pending_center = target
            return
        self._center = target
        self._pending_center = None
        self.viewChanged.emit(0.5, 0.5, self._zoom)

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        center_lon, center_lat = self._center
        screen_x = self.width() / 2.0 + ((float(lon) - center_lon) * 100.0)
        screen_y = self.height() / 2.0 - ((float(lat) - center_lat) * 100.0)
        return QPointF(screen_x, screen_y)


class _PostRenderMiniMapWidget(_FakeMiniMapWidget):
    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent, map_source=map_source)
        self.post_render_painters: list[object] = []
        self.removed_post_render_painters: list[object] = []
        self.full_update_count = 0

    def add_post_render_painter(self, callback) -> None:
        if callback not in self.post_render_painters:
            self.post_render_painters.append(callback)

    def remove_post_render_painter(self, callback) -> None:
        self.removed_post_render_painters.append(callback)
        self.post_render_painters = [
            existing for existing in self.post_render_painters if existing != callback
        ]

    def request_full_update(self) -> None:
        self.full_update_count += 1


class _EventTargetMiniMapWidget(_FakeMiniMapWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
    ) -> None:
        super().__init__(parent, map_source=map_source)
        self._event_target = QWidget(self)
        self._event_target.setObjectName("fakeInfoLocationMapEventTarget")
        self._event_target.setGeometry(self.rect())
        self._event_target.show()

    def event_target(self) -> QWidget:
        return self._event_target

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._event_target.setGeometry(self.rect())


class _WindowEventTargetMiniMapWidget(_FakeMiniMapWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
    ) -> None:
        super().__init__(parent, map_source=map_source)
        self._event_target = QWindow()
        self._event_target.setObjectName("fakeInfoLocationMapWindowEventTarget")
        self._event_target.resize(self.size())

    def event_target(self) -> QWindow:
        return self._event_target

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._event_target.resize(self.size())

    def shutdown(self) -> None:
        self._event_target.destroy()
        return None


def _fake_choose_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_FakeMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _FakeMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "legacy_python",
    )


def _fake_choose_delayed_projection_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_DelayedProjectionMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _DelayedProjectionMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "legacy_python",
    )


def _fake_choose_deferred_center_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_DeferredCenterMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _DeferredCenterMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "legacy_python",
    )


def _fake_choose_post_render_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_PostRenderMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _PostRenderMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "legacy_python",
    )


def _fake_choose_event_target_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_EventTargetMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _EventTargetMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "osmand_native",
    )


def _fake_choose_window_event_target_map_widget_backend(
    _map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
) -> tuple[type[_WindowEventTargetMiniMapWidget], MapSourceSpec, str]:
    del use_opengl
    return (
        _WindowEventTargetMiniMapWidget,
        MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
        "osmand_native",
    )


def _clear_override_cursors() -> None:
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


def _send_mouse_event(
    target: QWidget,
    event_type: QEvent.Type,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    local_pos = QPointF(12.0, 12.0)
    global_pos = QPointF(target.mapToGlobal(local_pos.toPoint()))
    event = QMouseEvent(
        event_type,
        local_pos,
        global_pos,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QCoreApplication.sendEvent(target, event)


def test_info_panel_formats_video_metadata(qapp: QApplication) -> None:
    """Verify that video-specific fields render with human readable text."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "dt": "2024-02-18T12:34:56Z",
        "make": "Apple",
        "model": "Apple iPhone 13 Pro",
        "is_video": True,
        "w": 1920,
        "h": 1080,
        "bytes": 24192000,
        "codec": "hevc",
        "frame_rate": 59.94,
        "dur": 8.0,
    }

    panel.set_asset_metadata(metadata)

    assert panel.current_rel() == "clip.MOV"
    assert panel._camera_label.text() == "Apple iPhone 13 Pro"
    summary_text = panel._summary_label.text()
    assert "1920 × 1080" in summary_text
    assert "23.1 MB" in summary_text
    assert "HEVC" in summary_text
    details_text = panel._exposure_label.text()
    assert "fps" in details_text
    assert "0:08" in details_text
    assert not panel._lens_label.isVisible()
    panel.close()


def test_info_panel_video_shows_lens_when_available(qapp: QApplication) -> None:
    """When a video asset has lens metadata the lens label must be visible."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "Apple",
        "model": "Apple iPhone 12",
        "lens": "iPhone 12 back camera 4.2mm f/1.6",
        "w": 1920,
        "h": 1080,
        "bytes": 8_000_000,
        "codec": "hevc",
        "frame_rate": 30.0,
        "dur": 5.0,
    }

    panel.set_asset_metadata(metadata)

    assert not panel._lens_label.isHidden()
    assert "iPhone 12 back camera 4.2mm f/1.6" in panel._lens_label.text()
    panel.close()


def test_info_panel_body_scrolls_long_content_without_resizing_or_changing_width(
    qapp: QApplication,
) -> None:
    panel = InfoPanel()
    sparse = {
        "rel": "photo.jpg",
        "name": "photo.jpg",
        "make": "Apple",
        "model": "iPhone 16 Pro",
    }
    panel.set_asset_metadata(sparse)
    panel.show()
    qapp.processEvents()

    panel_size = panel.size()
    viewport_width = panel._body_scroll.viewport().width()
    body_scrollbar = panel._body_scroll.verticalScrollBar()
    assert panel_size == _expected_panel_size(panel)
    assert not hasattr(panel, "_metadata_scroll")
    assert body_scrollbar.maximum() == 0
    assert not body_scrollbar.isEnabled()
    assert "transparent" in body_scrollbar.styleSheet()
    assert panel._body_scroll.horizontalScrollBar().maximum() == 0

    rich = dict(sparse)
    rich.update(
        {
            "lens": "iPhone 16 Pro back triple camera " * 100,
            "w": 4032,
            "h": 3024,
            "bytes": 901_600,
            "codec": "heif",
        }
    )
    panel.set_asset_metadata(rich)
    for _ in range(3):
        qapp.processEvents()

    assert panel.size() == panel_size
    assert panel._body_scroll.viewport().width() == viewport_width
    assert body_scrollbar.maximum() > 0
    assert body_scrollbar.isEnabled()
    assert body_scrollbar.styleSheet() == ""
    assert panel._body_scroll.horizontalScrollBar().maximum() == 0

    body_scrollbar.setValue(min(40, body_scrollbar.maximum()))
    retained_scroll = body_scrollbar.value()
    panel.set_asset_metadata({**rich, "iso": 640})
    qapp.processEvents()
    assert body_scrollbar.value() == retained_scroll

    panel.set_asset_metadata({**sparse, "rel": "other.jpg", "name": "other.jpg"})
    qapp.processEvents()
    assert panel.size() == panel_size
    assert body_scrollbar.value() == 0
    assert body_scrollbar.maximum() == 0


def test_info_panel_short_content_keeps_natural_section_gaps_and_bottom_slack(
    qapp: QApplication,
) -> None:
    panel = InfoPanel()
    panel.set_asset_metadata(
        {
            "rel": "IMG_1424.HEIC",
            "name": "IMG_1424.HEIC",
            "dt": "2026-01-27T18:43:16Z",
            "make": "Apple",
            "model": "Apple iPhone 16 Pro",
            "lens": "iPhone 16 Pro back triple camera 6.765mm f/1.78",
            "w": 3024,
            "h": 4032,
            "bytes": 901_600,
            "codec": "heif",
            "iso": 640,
            "focal_length": 6.8,
            "exposure_compensation": 0,
            "f_number": 1.78,
            "exposure_time": "1/25",
        }
    )
    panel.show()
    qapp.processEvents()

    spacing = panel._content_layout.spacing()
    adjacent_sections = (
        (panel._filename_label, panel._timestamp_label),
        (panel._timestamp_label, panel._metadata_frame),
        (panel._exposure_container, panel._face_separator),
    )
    for previous, following in adjacent_sections:
        assert following.y() - (previous.y() + previous.height()) == spacing

    tail_item = panel._content_layout.itemAt(panel._content_layout.count() - 1)
    tail_spacer = tail_item.spacerItem()
    assert tail_spacer is not None
    assert tail_spacer.geometry().height() > 0
    assert tail_spacer.geometry().top() >= (
        panel._location_container.y() + panel._location_container.height()
    )
    assert panel.size() == _expected_panel_size(panel)
    panel.close()


def test_info_panel_long_text_wraps_fully_before_body_scrolls(qapp: QApplication) -> None:
    panel = InfoPanel()
    panel.set_asset_metadata(
        {
            "rel": "long.jpg",
            "name": "very-long-file-name-" * 30,
            "lens": "Long translated lens metadata " * 50,
        }
    )
    panel._set_label_text(
        panel._timestamp_label,
        "Long localized timestamp value " * 20,
    )
    panel._set_label_text(
        panel._exposure_label,
        "Long localized exposure value " * 20,
    )
    panel._refresh_panel_geometry()
    panel.show()
    for _ in range(3):
        qapp.processEvents()

    for label in (
        panel._filename_label,
        panel._timestamp_label,
        panel._lens_label,
        panel._exposure_label,
    ):
        assert label.wordWrap() is True
        assert label.height() >= label.heightForWidth(label.width())

    assert panel._body_scroll.verticalScrollBar().maximum() > 0
    assert panel.size() == _expected_panel_size(panel)
    panel.close()


def test_info_panel_video_missing_details_shows_fallback(qapp: QApplication) -> None:
    """When metadata is sparse the video fallback string should be displayed."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
    }

    panel.set_asset_metadata(metadata)

    assert panel._exposure_label.text() == "Detailed video information is unavailable."
    assert not panel._summary_label.isVisible()
    panel.close()


def test_info_panel_loading_state_shows_loading_message(qapp: QApplication) -> None:
    """Sparse metadata should show a loading hint while enrichment is pending."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "_metadata_loading": True,
    }

    panel.set_asset_metadata(metadata)

    assert panel._exposure_label.text() == "Loading detailed video information..."
    panel.close()


def test_info_panel_frameless_window_flags(qapp: QApplication) -> None:
    """The info panel should use a frameless window hint."""

    from PySide6.QtCore import Qt

    panel = InfoPanel()
    flags = panel.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    panel.close()


def test_info_panel_close_event_dismisses_without_shutdown(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = InfoPanel()
    shutdown_calls: list[bool] = []
    dismissed_calls: list[bool] = []

    monkeypatch.setattr(panel, "shutdown", lambda: shutdown_calls.append(True))
    panel.dismissed.connect(lambda: dismissed_calls.append(True))

    panel.show()
    qapp.processEvents()
    panel.close()
    qapp.processEvents()

    assert not panel.isVisible()
    assert dismissed_calls == [True]
    assert shutdown_calls == []


def test_info_panel_location_map_is_reused_until_explicit_shutdown(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)
    map_widget = panel._location_map._map_widget
    assert isinstance(map_widget, _FakeMiniMapWidget)

    for _ in range(20):
        panel.close()
        qapp.processEvents()
        assert panel._location_map._map_widget is map_widget
        assert map_widget.shutdown_calls == 0
        panel.show()
        qapp.processEvents()
        assert panel._location_map._map_widget is map_widget

    panel.shutdown()
    panel.shutdown()

    assert panel._location_map._map_widget is None
    assert map_widget.shutdown_calls == 1


def test_info_panel_map_placeholder_precedes_lazy_backend_without_resizing(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
            "location": "Munich",
        }
    )

    panel.prepare_for_presentation()

    map_view = panel._location_map
    panel_size = panel.size()
    assert isinstance(map_view._map_widget, _FakeMiniMapWidget)
    map_view._map_widget.auto_present_first_frame = False
    assert not map_view._placeholder.isHidden()
    assert map_view.width() == map_view.height()
    placeholder = QPixmap(map_view._map_clip_frame.size())
    map_view._map_clip_frame.render(placeholder)
    center = placeholder.toImage().pixelColor(
        placeholder.width() // 2,
        placeholder.height() // 2,
    )
    assert center.name().lower() == "#88a8c2"

    panel.show()
    _process_deferred_panel_content(qapp)
    assert not map_view._placeholder.isHidden()

    map_view._map_widget.present_first_frame()
    qapp.processEvents()

    assert map_view._placeholder.isHidden()
    assert panel.size() == panel_size


def test_info_panel_dismiss_before_first_frame_reuses_stable_map_surface(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
        }
    )

    panel.show()
    map_widget = panel._location_map._map_widget
    panel.dismiss()
    _process_deferred_panel_content(qapp)

    assert isinstance(map_widget, _FakeMiniMapWidget)
    assert panel._location_map._map_widget is map_widget
    assert map_widget.shutdown_calls == 0

    panel.show()
    _process_deferred_panel_content(qapp)
    assert panel._location_map._map_widget is map_widget


def test_info_panel_deferred_native_failure_falls_back_behind_placeholder(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingDeferredNativeMap(_FakeMiniMapWidget):
        def start_deferred_content(self) -> None:
            raise RuntimeError("native resources unavailable")

    source = MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd())
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        lambda *_args, **_kwargs: (_FailingDeferredNativeMap, source, "osmand_native"),
    )
    monkeypatch.setattr(
        info_location_map_module,
        "_preferred_python_widget_class",
        lambda **_kwargs: _FakeMiniMapWidget,
    )
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
        }
    )

    panel.show()
    _process_deferred_panel_content(qapp)

    assert isinstance(panel._location_map._map_widget, _FakeMiniMapWidget)
    assert not isinstance(panel._location_map._map_widget, _FailingDeferredNativeMap)
    assert panel._location_map._backend_kind == "osmand_python"


def test_info_panel_lazy_map_uses_latest_location_and_does_not_recreate_after_shutdown(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
        }
    )
    panel.show()
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 35.6764, "lon": 139.6500},
        }
    )

    _process_deferred_panel_content(qapp)

    map_widget = panel._location_map._map_widget
    assert isinstance(map_widget, _FakeMiniMapWidget)
    assert map_widget.center_lonlat() == (139.6500, 35.6764)

    panel.shutdown()
    _process_deferred_panel_content(qapp)
    assert panel._location_map._map_widget is None


def test_info_panel_close_button_matches_main_window(qapp: QApplication) -> None:
    """The close button dimensions should match the main window's controls."""

    from iPhoto.gui.ui.widgets.main_window_metrics import (
        WINDOW_CONTROL_BUTTON_SIZE,
        WINDOW_CONTROL_GLYPH_SIZE,
    )

    panel = InfoPanel()
    btn = panel.close_button
    assert btn is not None
    assert btn.toolTip() == "Close"
    assert btn.iconSize() == WINDOW_CONTROL_GLYPH_SIZE
    assert btn.size() == WINDOW_CONTROL_BUTTON_SIZE
    panel.close()


def test_info_panel_close_button_closes(qapp: QApplication) -> None:
    """Clicking the close button should hide the panel."""

    panel = InfoPanel()
    panel.show()
    assert panel.isVisible()
    panel.close_button.click()
    assert not panel.isVisible()


def test_info_panel_face_add_button_stays_visible_across_rebuilds(qapp: QApplication) -> None:
    """Repeated face-strip rebuilds should not leave the add button hidden."""

    panel = InfoPanel()

    panel.set_asset_faces([])
    assert panel._face_add_button.isHidden() is False
    assert panel._face_layout.indexOf(panel._face_add_button) >= 0

    panel.set_asset_metadata(
        {
            "rel": "photo.jpg",
            "name": "photo.jpg",
            "is_video": False,
        }
    )
    panel.set_asset_faces([])

    assert panel._face_add_button.isHidden() is False
    assert panel._face_add_button.parent() is panel._face_container
    assert panel._face_layout.indexOf(panel._face_add_button) >= 0
    panel.close()


def test_info_panel_face_strip_uses_enlarged_avatar_and_matched_plus_button_sizes(
    qapp: QApplication,
) -> None:
    """The face strip should enlarge avatars and size the plus button from the SVG metrics."""

    from iPhoto.people.repository import AssetFaceAnnotation

    panel = InfoPanel()
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Alice",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
        ]
    )

    avatar = panel._face_layout.itemAt(0).widget()
    assert avatar is not None
    assert avatar.size() == QSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
    assert panel._face_add_button.iconSize() == _FACE_ADD_ICON_SIZE
    assert panel._face_add_button.size() == _FACE_ADD_BUTTON_SIZE
    panel.close()


def test_info_panel_face_avatar_context_menu_labels_and_submenu(qapp: QApplication) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

    panel = InfoPanel()
    panel.set_face_action_candidates(
        [
            PersonSummary(
                person_id="person-1",
                name="Alice",
                key_face_id="face-1",
                face_count=3,
                thumbnail_path=None,
                created_at="2024-01-01T00:00:00+00:00",
            ),
            PersonSummary(
                person_id="person-2",
                name="Bob",
                key_face_id="face-2",
                face_count=2,
                thumbnail_path=None,
                created_at="2024-01-02T00:00:00+00:00",
            ),
        ]
    )
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Alice",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
        ]
    )

    avatar = panel._face_layout.itemAt(0).widget()
    assert avatar is not None
    delete_label, not_this_label, submenu_labels = avatar._menu_action_labels()
    assert delete_label == "Delete"
    assert not_this_label == "Not Alice"
    assert submenu_labels == (
        ("choose_someone_else", "Choose Someone Else…"),
        ("new_person", "New Name…"),
    )
    menu = avatar._build_context_menu()
    assert menu is not None
    submenu = menu.actions()[1].menu()
    assert submenu is not None
    assert [action.data() for action in submenu.actions()] == [
        "choose_someone_else",
        "new_person",
    ]
    panel.close()


def test_info_panel_face_avatar_context_menu_uses_fallback_name_when_unnamed(
    qapp: QApplication,
) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation

    panel = InfoPanel()
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id=None,
                display_name=None,
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
        ]
    )

    avatar = panel._face_layout.itemAt(0).widget()
    assert avatar is not None
    assert avatar._menu_action_labels()[1] == "Not This Name"
    panel.close()


def test_info_panel_face_candidates_update_existing_avatars_without_rebuild(
    qapp: QApplication,
) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

    panel = InfoPanel()
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Alice",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
        ]
    )
    avatar = panel._face_layout.itemAt(0).widget()
    candidates = [
        PersonSummary(
            person_id="person-2",
            name="Bob",
            key_face_id="face-2",
            face_count=2,
            thumbnail_path=None,
            created_at="2024-01-02T00:00:00+00:00",
        )
    ]

    panel.set_face_action_candidates(candidates)

    assert panel._face_layout.itemAt(0).widget() is avatar
    assert avatar._candidates == candidates
    panel.close()


def test_info_panel_face_avatar_highlight_toggles_with_menu_state(qapp: QApplication) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation

    panel = InfoPanel()
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Alice",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
        ]
    )

    avatar = panel._face_layout.itemAt(0).widget()
    assert avatar is not None
    assert "#0A84FF" not in avatar.styleSheet()

    avatar._set_menu_active(True)
    assert "#0A84FF" in avatar.styleSheet()

    avatar._set_menu_active(False)
    assert "#0A84FF" not in avatar.styleSheet()
    panel.close()


def test_info_panel_choose_person_reuses_group_people_dialog(
    monkeypatch, qapp: QApplication
) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

    dialog_calls: list[dict[str, object]] = []

    class _FakeDialog:
        def __init__(self, summaries, **kwargs) -> None:
            dialog_calls.append(
                {
                    "summaries": summaries,
                    "kwargs": kwargs,
                }
            )

        def exec(self) -> int:
            return 1

        def selected_person_ids(self) -> list[str]:
            return ["person-2"]

    monkeypatch.setattr(info_panel_module, "GroupPeopleDialog", _FakeDialog)

    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-1",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )
    avatar = info_panel_module._FaceAvatarWidget(
        annotation,
        [
            PersonSummary(
                person_id="person-1",
                name="Alice",
                key_face_id="face-1",
                face_count=3,
                thumbnail_path=None,
                created_at="2024-01-01T00:00:00+00:00",
            ),
            PersonSummary(
                person_id="person-2",
                name="Bob",
                key_face_id="face-2",
                face_count=2,
                thumbnail_path=None,
                created_at="2024-01-02T00:00:00+00:00",
            ),
        ],
    )
    moved: list[tuple[object, str]] = []
    avatar.moveRequested.connect(lambda face, person_id: moved.append((face, person_id)))

    avatar._prompt_choose_person()

    assert len(dialog_calls) == 1
    assert [summary.person_id for summary in dialog_calls[0]["summaries"]] == ["person-2"]
    assert dialog_calls[0]["kwargs"]["title_text"] == "Choose Someone Else"
    assert dialog_calls[0]["kwargs"]["prompt_text"] == "Assign to"
    assert dialog_calls[0]["kwargs"]["confirm_text"] == "Choose"
    assert dialog_calls[0]["kwargs"]["min_selection"] == 1
    assert dialog_calls[0]["kwargs"]["max_selection"] == 1
    assert dialog_calls[0]["kwargs"]["dark_mode"] is False
    assert moved == [(annotation, "person-2")]
    avatar.close()


def test_info_panel_choose_person_filters_current_prefixed_identity(
    monkeypatch,
    qapp: QApplication,
) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation

    dialog_calls: list[dict[str, object]] = []

    class _FakeDialog:
        def __init__(self, summaries, **kwargs) -> None:
            dialog_calls.append({"summaries": summaries, "kwargs": kwargs})

        def exec(self) -> int:
            return 0

        def selected_person_ids(self) -> list[str]:
            return []

    monkeypatch.setattr(info_panel_module, "GroupPeopleDialog", _FakeDialog)

    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-1",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )
    avatar = info_panel_module._FaceAvatarWidget(
        annotation,
        [
            RecognitionIdentitySuggestion("person:person-1", "Alice", None, 3),
            RecognitionIdentitySuggestion("person:person-2", "Bob", None, 2),
        ],
    )

    avatar._prompt_choose_person()

    assert len(dialog_calls) == 1
    assert [summary.person_id for summary in dialog_calls[0]["summaries"]] == [
        "person:person-2"
    ]
    avatar.close()


def test_info_panel_pet_avatar_uses_neutral_menu_and_mixed_candidate_shape(
    monkeypatch,
    qapp: QApplication,
) -> None:
    dialog_calls: list[dict[str, object]] = []

    class _FakeDialog:
        def __init__(self, summaries, **kwargs) -> None:
            dialog_calls.append({"summaries": summaries, "kwargs": kwargs})

        def exec(self) -> int:
            return 1

        def selected_person_ids(self) -> list[str]:
            return ["pet:pet-b"]

    monkeypatch.setattr(info_panel_module, "GroupPeopleDialog", _FakeDialog)

    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-a",
        source_identity_kind="pet",
        source_identity_id="pet-a",
        canonical_identity_kind="pet",
        canonical_identity_id="pet-a",
        canonical_display_name="Miso",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )
    avatar = info_panel_module._FaceAvatarWidget(
        annotation,
        [
            RecognitionIdentitySuggestion("person:person-a", "Alice", None, 3),
            RecognitionIdentitySuggestion("pet:pet-a", "Miso", None, 1),
            RecognitionIdentitySuggestion("pet:pet-b", "Nori", None, 2),
        ],
    )
    moved: list[tuple[object, str]] = []
    avatar.moveRequested.connect(lambda face, identity_key: moved.append((face, identity_key)))

    delete_label, not_this_label, submenu_labels = avatar._menu_action_labels()
    assert delete_label == "Delete"
    assert not_this_label == "Not Miso"
    assert submenu_labels == (
        ("choose_someone_else", "Choose Someone Else…"),
        ("new_person", "New Name…"),
    )

    avatar._prompt_choose_person()

    assert len(dialog_calls) == 1
    summaries = dialog_calls[0]["summaries"]
    assert [summary.person_id for summary in summaries] == ["pet:pet-b"]
    assert [summary.name for summary in summaries] == ["Nori"]
    assert dialog_calls[0]["kwargs"]["title_text"] == "Choose Someone Else"
    assert dialog_calls[0]["kwargs"]["prompt_text"] == "Assign to"
    assert all("pet:" not in str(summary.name) for summary in summaries)
    assert moved == [(annotation, "pet:pet-b")]
    avatar.close()


def test_info_panel_choose_person_passes_dark_mode_to_group_dialog(
    monkeypatch, qapp: QApplication
) -> None:
    from types import SimpleNamespace

    from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

    dialog_calls: list[dict[str, object]] = []

    class _Theme:
        def get_effective_theme_mode(self) -> str:
            return "dark"

    class _FakeDialog:
        def __init__(self, summaries, **kwargs) -> None:
            dialog_calls.append(
                {
                    "summaries": summaries,
                    "kwargs": kwargs,
                }
            )

        def exec(self) -> int:
            return 0

        def selected_person_ids(self) -> list[str]:
            return []

    monkeypatch.setattr(info_panel_module, "GroupPeopleDialog", _FakeDialog)

    host = QWidget()
    host.coordinator = SimpleNamespace(
        _context=SimpleNamespace(theme=_Theme(), settings=None)
    )
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-1",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )
    avatar = info_panel_module._FaceAvatarWidget(
        annotation,
        [
            PersonSummary(
                person_id="person-2",
                name="Bob",
                key_face_id="face-2",
                face_count=2,
                thumbnail_path=None,
                created_at="2024-01-02T00:00:00+00:00",
            ),
        ],
        parent=host,
    )

    avatar._prompt_choose_person()

    assert len(dialog_calls) == 1
    assert dialog_calls[0]["kwargs"]["dark_mode"] is True
    avatar.close()
    host.close()


def test_info_panel_emits_dismissed_when_closed(qapp: QApplication) -> None:
    """Closing the panel should emit the dismissed signal exactly once."""

    panel = InfoPanel()
    dismissed = []
    panel.dismissed.connect(lambda: dismissed.append(True))

    panel.show()
    panel.close_button.click()
    qapp.processEvents()

    assert dismissed == [True]


def test_info_panel_centers_on_parent(qapp: QApplication) -> None:
    """The panel should center itself over its parent on first show."""

    from PySide6.QtWidgets import QMainWindow

    parent = QMainWindow()
    parent.setGeometry(200, 200, 800, 600)
    parent.show()

    panel = InfoPanel(parent)
    panel.show()
    qapp.processEvents()

    parent_center = parent.geometry().center()
    panel_center = panel.geometry().center()

    assert abs(panel_center.x() - parent_center.x()) <= 120
    assert abs(panel_center.y() - parent_center.y()) <= 120

    panel.close()
    parent.close()


def test_info_panel_metadata_enrichment_keeps_panel_height_stable(qapp: QApplication) -> None:
    """Async metadata enrichment must not resize the visible Info Panel."""

    sparse = {
        "rel": "IMG_3686.HEIC",
        "name": "IMG_3686.HEIC",
        "is_video": False,
        "_metadata_loading": True,
    }
    rich = {
        **sparse,
        "name": "IMG_3686.HEIC",
        "dt": "2025-09-16T12:08:36Z",
        "make": "Apple",
        "model": "Apple iPhone 12",
        "lens": "iPhone 12 back dual wide camera",
        "w": 4032,
        "h": 3024,
        "iso": 250,
        "focal_length": 1.6,
        "exposure_compensation": 0,
        "f_number": 2.4,
        "exposure_time": "1/99",
    }

    panel = InfoPanel()
    panel.set_asset_metadata(sparse)
    panel.show()
    qapp.processEvents()
    sparse_height = panel.height()

    panel.set_asset_metadata(rich)
    for _ in range(3):
        qapp.processEvents()

    assert panel.height() == sparse_height
    panel.close()


def test_info_panel_first_show_uses_fixed_shell_without_deferred_resize(
    qapp: QApplication,
) -> None:
    """The first presentation should use the screen-bounded design shell."""

    panel = InfoPanel()
    panel.show()
    first_size = panel.size()
    qapp.processEvents()

    assert first_size == _expected_panel_size(panel)
    assert panel.size() == first_size
    panel.close()


def test_info_panel_shell_is_capped_once_for_small_screen(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp

    class _SmallScreen:
        @staticmethod
        def name() -> str:
            return "small-screen"

        @staticmethod
        def availableGeometry() -> QRect:
            return QRect(0, 0, 500, 600)

    panel = InfoPanel()
    monkeypatch.setattr(panel, "_panel_screen", lambda: _SmallScreen())

    assert panel._apply_shell_size(force=True) is True
    assert panel.size() == QSize(320, 520)

    panel.set_asset_metadata(
        {
            "rel": "long.jpg",
            "name": "long.jpg",
            "lens": "Long translated metadata " * 100,
        }
    )
    assert panel.size() == QSize(320, 520)


def test_info_panel_faces_wrap_without_horizontal_overflow(qapp: QApplication) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation

    panel = InfoPanel()
    panel.set_asset_faces(
        [
            AssetFaceAnnotation(
                face_id=f"face-{index}",
                person_id=f"person-{index}",
                display_name=f"Person {index}",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            )
            for index in range(14)
        ]
    )
    panel.show()
    qapp.processEvents()

    assert panel._face_container.height() > _FACE_AVATAR_DIAMETER
    assert panel._body_scroll.horizontalScrollBar().maximum() == 0
    assert panel._body_content.width() == panel._body_scroll.viewport().width()
    assert panel.size() == _expected_panel_size(panel)
    panel.close()


def test_info_panel_visible_metadata_update_refreshes_body_without_resizing_shell(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible metadata updates should affect only scrollable body geometry."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "codec": "hevc",
    }

    panel.show()
    qapp.processEvents()
    panel_size = panel.size()
    refresh = Mock(wraps=panel._refresh_panel_geometry)
    monkeypatch.setattr(panel, "_refresh_panel_geometry", refresh)

    panel.set_asset_metadata(metadata)

    assert refresh.call_count >= 1
    assert panel.size() == panel_size
    panel.close()


def test_info_panel_title_label_drag_moves_panel(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dragging from the title label should move the panel, not just blank title-bar space."""

    panel = InfoPanel()
    monkeypatch.setattr(panel, "_try_start_system_drag", Mock(return_value=False))
    panel.show()
    qapp.processEvents()
    start_pos = panel.pos()

    label = panel._title_label
    press_local = QPointF(8.0, 8.0)
    press_global = QPointF(label.mapToGlobal(press_local.toPoint()))
    move_global = press_global + QPointF(36.0, 24.0)

    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        press_local,
        press_global,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        press_local,
        move_global,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        press_local,
        move_global,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert panel.eventFilter(label, press_event) is True
    assert panel._drag_active is True
    assert panel.eventFilter(label, move_event) is True
    assert panel.pos() != start_pos or panel._drag_offset is not None
    assert panel.eventFilter(label, release_event) is True
    assert panel._drag_active is False
    panel.close()


def test_info_panel_has_shadow_margin(qapp: QApplication) -> None:
    """The root layout should reserve right/bottom margins for the shadow."""

    panel = InfoPanel()
    layout = panel.layout()
    margins = layout.contentsMargins()
    shadow = InfoPanel._SHADOW_SIZE
    assert margins.left() == 0
    assert margins.top() == 0
    assert margins.right() == shadow
    assert margins.bottom() == shadow
    panel.close()


def test_info_panel_video_shows_lens_spec_string_when_no_model_name(qapp: QApplication) -> None:
    """When only a lens spec string (e.g. Fujifilm LensInfo '23mm f/2') is available,
    the lens label must be visible with the spec text."""

    panel = InfoPanel()
    meta = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "23mm f/2",
        "w": 1920,
        "h": 1080,
        "bytes": 12_000_000,
        "codec": "h264",
        "frame_rate": 25.0,
        "dur": 10.0,
    }

    panel.set_asset_metadata(meta)

    assert not panel._lens_label.isHidden()
    assert "23mm f/2" in panel._lens_label.text()
    panel.close()


def test_info_panel_lens_spec_string_not_duplicated_when_focal_and_fnumber_also_present(
    qapp: QApplication,
) -> None:
    """When the lens string is a spec string (e.g. '23mm f/2') AND separate
    focal_length / f_number fields are also present, the label must show
    the lens string exactly once — not a garbled duplication like '2323 22'."""

    panel = InfoPanel()
    meta = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "23mm f/2",
        "focal_length": 23.0,
        "f_number": 2.0,
        "w": 1920,
        "h": 1080,
        "bytes": 12_000_000,
        "codec": "h264",
        "frame_rate": 25.0,
        "dur": 10.0,
    }

    panel.set_asset_metadata(meta)

    label_text = panel._lens_label.text()
    assert not panel._lens_label.isHidden()
    assert label_text == "23mm f/2"
    panel.close()


def test_info_panel_named_lens_model_gets_focal_appended(
    qapp: QApplication,
) -> None:
    """A named lens model string like 'XF23mmF2 R WR' should have the separate
    focal_length / f_number fields appended because it is not a complete spec
    string (no 'f/' prefix in the aperture token).  The old broad _FOCAL_LENGTH_RE
    would have incorrectly suppressed the append."""

    panel = InfoPanel()
    meta = {
        "rel": "img.jpg",
        "name": "img.jpg",
        "is_video": False,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "XF23mmF2 R WR",
        "focal_length": 23.0,
        "f_number": 2.0,
    }

    panel.set_asset_metadata(meta)

    label_text = panel._lens_label.text()
    assert not panel._lens_label.isHidden()
    # The named model should be present and enriched with focal + aperture info.
    assert "XF23mmF2 R WR" in label_text
    assert "23" in label_text   # focal length must appear
    assert "ƒ2" in label_text  # aperture must appear
    panel.close()


def test_info_panel_location_map_stays_square(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    assert map_view.width() == map_view.height()
    assert map_view._map_host.size() == map_view.size()
    panel.close()


def test_info_panel_location_map_restores_outer_rounded_corners(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    assert not map_view.mask().contains(QPoint(0, 0))
    assert not map_view.mask().contains(QPoint(map_view.width() - 1, 0))
    assert not map_view.mask().contains(QPoint(0, map_view.height() - 1))
    assert not map_view.mask().contains(QPoint(map_view.width() - 1, map_view.height() - 1))
    assert not map_view._map_clip_frame.mask().contains(QPoint(0, 0))
    assert not map_view._map_host.mask().contains(QPoint(0, 0))
    assert map_view._map_clip_frame.mask().contains(
        QPoint(map_view._map_clip_frame.width() // 2, map_view._map_clip_frame.height() // 2)
    )
    panel.close()


def test_info_panel_location_map_clips_embedded_event_target_corners(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_event_target_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _EventTargetMiniMapWidget)
    event_target = map_widget.event_target()
    assert not event_target.mask().contains(QPoint(0, 0))
    assert not event_target.mask().contains(QPoint(event_target.width() - 1, 0))
    assert event_target.mask().contains(
        QPoint(event_target.width() // 2, event_target.height() // 2)
    )
    panel.close()


def test_info_panel_location_map_clips_qwindow_event_target_corners(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_window_event_target_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _WindowEventTargetMiniMapWidget)
    event_target = map_widget.event_target()
    assert not event_target.mask().contains(QPoint(0, 0))
    assert not event_target.mask().contains(QPoint(event_target.width() - 1, 0))
    assert event_target.mask().contains(
        QPoint(event_target.width() // 2, event_target.height() // 2)
    )
    panel.close()


def test_info_panel_repeated_same_gps_metadata_does_not_reset_location_map(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )
    metadata = {
        "rel": "map.jpg",
        "name": "map.jpg",
        "gps": {"lat": 37.7749, "lon": -122.4194},
        "location": "San Francisco",
    }

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(metadata)
    panel.show()
    _process_deferred_panel_content(qapp)

    set_location = Mock(wraps=panel._location_map.set_location)
    monkeypatch.setattr(panel._location_map, "set_location", set_location)

    panel.set_asset_metadata(dict(metadata))

    set_location.assert_not_called()
    assert not panel._location_map.isHidden()
    panel.close()


def test_info_panel_missing_location_hides_map_without_repaint(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_post_render_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
            "location": "Munich",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _PostRenderMiniMapWidget)
    assert not map_view.isHidden()
    full_update_count = map_widget.full_update_count

    panel.set_asset_metadata({"rel": "plain.jpg", "name": "plain.jpg"})
    qapp.processEvents()

    assert map_view.isHidden()
    assert map_view.current_location() == (None, None)
    assert map_view._screen_point is None
    assert not map_view._pin_sync_timer.isActive()
    assert not map_view._pin_settle_timer.isActive()
    assert map_widget.full_update_count == full_update_count
    panel.close()


def test_info_panel_content_update_batches_visible_geometry_refresh(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.show()
    qapp.processEvents()

    refresh = Mock(wraps=panel._refresh_panel_geometry)
    monkeypatch.setattr(panel, "_refresh_panel_geometry", refresh)

    with panel.content_update():
        panel.set_asset_metadata(
            {
                "rel": "plain.jpg",
                "name": "plain.jpg",
                "dt": "2024-01-01T00:00:00Z",
                "make": "Apple",
                "model": "iPhone",
                "w": 4032,
                "h": 3024,
                "bytes": 1000,
            }
        )
        panel.set_face_action_candidates(
            [
                PersonSummary(
                    person_id="person-1",
                    name="Alice",
                    key_face_id="face-1",
                    face_count=1,
                    thumbnail_path=None,
                    created_at="2024-01-01T00:00:00+00:00",
                )
            ]
        )
        panel.set_asset_faces(
            [
                AssetFaceAnnotation(
                    face_id="face-1",
                    person_id="person-1",
                    display_name="Alice",
                    box_x=0,
                    box_y=0,
                    box_w=10,
                    box_h=10,
                    image_width=100,
                    image_height=100,
                )
            ]
        )

    assert refresh.call_count == 1
    first_height = panel.height()
    qapp.processEvents()
    assert panel.height() == first_height
    assert refresh.call_count == 1
    panel.close()


def test_info_panel_location_map_reflow_stabilizes_on_first_event_pass(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "location": "Munich",
            "gps": {"lat": 48.137154, "lon": 11.576124},
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    first_size = panel.size()
    assert first_size == _expected_panel_size(panel)
    assert panel._location_map.width() == panel._location_map.height()

    for _ in range(3):
        qapp.processEvents()

    assert panel.size() == first_size
    panel.close()


def test_info_panel_location_suggestions_use_non_focus_floating_tool(
    qapp: QApplication,
) -> None:
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata({"rel": "map.jpg", "name": "map.jpg"})
    panel.show()
    qapp.processEvents()
    height_before = panel.height()

    panel._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
    panel.set_location_suggestions(
        [
            SimpleNamespace(display_name="Munich", secondary_text="Germany"),
            SimpleNamespace(display_name="Munich Airport", secondary_text="Germany"),
        ]
    )
    qapp.processEvents()

    assert not panel._location_results.isHidden()
    assert panel._location_results.windowType() == Qt.WindowType.Tool
    assert panel._location_results.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert panel._location_results.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert panel._location_layout.indexOf(panel._location_results) == -1
    assert panel.height() == height_before
    panel.close()


def test_info_panel_body_scroll_hides_location_suggestions(qapp: QApplication) -> None:
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "long.jpg",
            "name": "long.jpg",
            "lens": "Long metadata content " * 100,
        }
    )
    panel.show()
    qapp.processEvents()
    panel.set_location_suggestions(
        [SimpleNamespace(display_name="Munich", secondary_text="Germany")]
    )
    assert not panel._location_results.isHidden()

    scrollbar = panel._body_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(min(40, scrollbar.maximum()))
    qapp.processEvents()

    assert panel._location_results.isHidden()
    panel.close()


def test_info_panel_location_keyboard_navigation_autocompletes_and_confirms(
    qapp: QApplication,
) -> None:
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata({"rel": "map.jpg", "name": "map.jpg"})
    panel.show()
    qapp.processEvents()
    suggestions = [
        SimpleNamespace(display_name="Munich", secondary_text="Germany"),
        SimpleNamespace(display_name="Munich Airport", secondary_text="Germany"),
    ]
    calls: list[tuple[str, object]] = []
    panel.locationConfirmRequested.connect(
        lambda query, suggestion: calls.append((query, suggestion))
    )

    panel._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
    panel.set_location_suggestions(suggestions)
    qapp.processEvents()

    assert panel._location_editor.text() == ""
    assert panel._location_results.currentRow() == 0

    down_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Down,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel._location_editor, down_event) is True
    assert panel._location_results.currentRow() == 1
    assert panel._location_editor.text() == "Munich Airport"

    up_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Up,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel._location_editor, up_event) is True
    assert panel._location_results.currentRow() == 0
    assert panel._location_editor.text() == "Munich"

    assert panel.eventFilter(panel._location_editor, down_event) is True
    enter_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel._location_editor, enter_event) is True
    qapp.processEvents()

    assert calls == [("Munich Airport", suggestions[1])]
    assert panel._location_results.isHidden()
    panel.close()


def test_info_panel_location_keyboard_escape_closes_suggestions_without_confirm(
    qapp: QApplication,
) -> None:
    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata({"rel": "map.jpg", "name": "map.jpg"})
    panel.show()
    qapp.processEvents()
    calls: list[tuple[str, object]] = []
    panel.locationConfirmRequested.connect(
        lambda query, suggestion: calls.append((query, suggestion))
    )

    panel._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
    panel.set_location_suggestions(
        [
            SimpleNamespace(display_name="Munich", secondary_text="Germany"),
            SimpleNamespace(display_name="Munich Airport", secondary_text="Germany"),
        ]
    )
    qapp.processEvents()

    escape_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel._location_editor, escape_event) is True

    assert calls == []
    assert panel._location_results.isHidden()
    assert panel._selected_location_suggestion is None
    assert not panel._location_confirm_button.isEnabled()
    panel.close()


def test_info_panel_common_location_show_path_keeps_fixed_shell(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )
    panel = InfoPanel()

    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    qapp.processEvents()

    assert panel.size() == _expected_panel_size(panel)
    assert panel._location_map.width() == panel._location_map.height()
    panel.close()


def test_info_panel_shows_download_button_when_location_extension_is_unavailable(
    qapp: QApplication,
) -> None:
    del qapp
    panel = InfoPanel()
    panel.set_location_capability(
        enabled=False,
        fallback_text="Install the map extension to use Assign a Location.",
    )
    panel.set_asset_metadata({"rel": "img.jpg", "name": "img.jpg"})

    assert not panel._location_fallback_label.isHidden()
    assert not panel._location_download_button.isHidden()
    assert panel._location_editor_row.isHidden()
    panel.close()


def test_info_panel_shows_download_prompt_when_search_is_unavailable_with_location_metadata(
    qapp: QApplication,
) -> None:
    del qapp
    panel = InfoPanel()
    panel.set_location_capability(
        enabled=False,
        fallback_text="Install the map extension to use Assign a Location.",
    )
    panel.set_asset_metadata(
        {
            "rel": "img.jpg",
            "name": "img.jpg",
            "location": "Munich",
        }
    )

    assert panel._location_editor_row.isHidden()
    assert not panel._location_fallback_label.isHidden()
    assert not panel._location_download_button.isHidden()
    assert panel._location_results.isHidden()
    assert not panel._location_confirm_button.isEnabled()
    panel.close()


def test_info_panel_emits_download_request_from_fallback_button(qapp: QApplication) -> None:
    panel = InfoPanel()
    panel.set_location_capability(enabled=False)
    panel.set_asset_metadata({"rel": "img.jpg", "name": "img.jpg"})
    panel.show()
    qapp.processEvents()

    calls: list[str] = []
    panel.downloadMapExtensionRequested.connect(lambda: calls.append("clicked"))
    panel._location_download_button.click()

    assert calls == ["clicked"]
    panel.close()


def test_info_panel_shows_map_preview_without_download_prompt_when_preview_runtime_exists(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=False, preview_enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    qapp.processEvents()

    assert panel._location_editor_row.isHidden()
    assert panel._location_fallback_label.isHidden()
    assert panel._location_download_button.isHidden()
    assert not panel._location_map.isHidden()
    panel.close()


def test_info_panel_retries_map_preview_when_runtime_is_bound_after_metadata(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnavailableMiniMapWidget(QWidget):
        def __init__(
            self,
            parent: QWidget | None = None,
            *,
            map_source: MapSourceSpec | None = None,
        ) -> None:
            del parent, map_source
            raise RuntimeError("backend unavailable")

    def _fake_unavailable_map_widget_backend(
        _map_source: MapSourceSpec | None,
        *,
        use_opengl: bool,
    ) -> tuple[type[_UnavailableMiniMapWidget], MapSourceSpec, str]:
        del use_opengl
        return (
            _UnavailableMiniMapWidget,
            MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
            "legacy_python",
        )

    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_unavailable_map_widget_backend,
    )
    monkeypatch.setattr(
        info_location_map_module,
        "_choose_map_widget_backend_with_runtime",
        lambda _map_source, *, use_opengl, runtime_capabilities: (
            _FakeMiniMapWidget,
            MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
            "legacy_python",
        ),
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=False, preview_enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    panel_size = panel.size()
    _process_deferred_panel_content(qapp)

    assert panel._location_map._map_widget is None
    assert not panel._location_map._message_label.isHidden()
    assert panel.size() == panel_size

    panel.set_map_runtime(
        SimpleNamespace(
            capabilities=lambda: SimpleNamespace(
                python_gl_available=False,
                display_available=True,
                location_search_available=False,
            )
        )
    )
    _process_deferred_panel_content(qapp)

    assert isinstance(panel._location_map._map_widget, _FakeMiniMapWidget)
    assert panel._location_map._message_label.isHidden()
    assert not panel._location_map.isHidden()
    assert panel.size() == panel_size
    panel.close()


def test_info_location_map_unavailable_message_retranslates(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnavailableMiniMapWidget(QWidget):
        def __init__(
            self,
            parent: QWidget | None = None,
            *,
            map_source: MapSourceSpec | None = None,
        ) -> None:
            del parent, map_source
            raise RuntimeError("backend unavailable")

    def _fake_unavailable_map_widget_backend(
        _map_source: MapSourceSpec | None,
        *,
        use_opengl: bool,
    ) -> tuple[type[_UnavailableMiniMapWidget], MapSourceSpec, str]:
        del use_opengl
        return (
            _UnavailableMiniMapWidget,
            MapSourceSpec.legacy_default(Path.cwd()).resolved(Path.cwd()),
            "legacy_python",
        )

    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_unavailable_map_widget_backend,
    )

    view = info_location_map_module.InfoLocationMapView()
    try:
        view.set_location(37.7749, -122.4194)
        view.prepare_surface()
        view.show()
        view.start_deferred_content()
        _process_deferred_panel_content(qapp)

        assert not view._message_label.isHidden()
        assert view._message_label.text() == "Map preview unavailable"

        view._message_label.setText("stale")
        view.retranslate_ui()

        assert view._message_label.text() == "Map preview unavailable"
    finally:
        view.shutdown()
        view.close()


def test_info_panel_map_runtime_package_root_controls_embedded_map_source(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_map_source: list[MapSourceSpec] = []

    def _capture_runtime_backend(
        map_source: MapSourceSpec | None,
        *,
        use_opengl: bool,
        runtime_capabilities,
    ) -> tuple[type[_FakeMiniMapWidget], MapSourceSpec, str]:
        del use_opengl, runtime_capabilities
        assert map_source is not None
        captured_map_source.append(map_source)
        return (
            _FakeMiniMapWidget,
            map_source,
            "legacy_python",
        )

    monkeypatch.setattr(
        info_location_map_module,
        "_choose_map_widget_backend_with_runtime",
        _capture_runtime_backend,
    )

    package_root = tmp_path / "maps-root"
    panel = InfoPanel()
    panel.set_map_runtime(
        SimpleNamespace(
            capabilities=lambda: SimpleNamespace(
                python_gl_available=False,
                display_available=True,
                location_search_available=True,
            ),
            package_root=lambda: package_root,
        )
    )
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 37.7749, "lon": -122.4194},
            "location": "San Francisco",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    assert captured_map_source
    assert Path(captured_map_source[-1].data_path) == (
        package_root / "tiles" / "extension" / "World_basemap_2.obf"
    )
    panel.close()


def test_info_panel_location_map_overlay_tracks_actual_embedded_map_size(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
            "location": "Munich",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _FakeMiniMapWidget)
    assert map_widget.size() == map_view._map_host.size()
    assert map_view._overlay.size() == map_widget.size()

    screen_point = map_view._screen_point
    assert screen_point is not None
    assert abs(screen_point.x() - map_widget.width() / 2.0) <= 1.0
    assert abs(screen_point.y() - map_widget.height() / 2.0) <= 1.0

    pin_rect = map_view._overlay._pin_label.geometry()
    pin_tip_x = pin_rect.x() + (
        pin_rect.width() * info_location_map_module._PIN_ANCHOR_X_RATIO
    )
    pin_tip_y = pin_rect.y() + (
        pin_rect.height() * info_location_map_module._PIN_ANCHOR_Y_RATIO
    )
    assert abs(pin_tip_x - screen_point.x()) <= 1.0
    assert abs(pin_tip_y - screen_point.y()) <= 1.0
    panel.close()


def test_info_panel_location_map_drag_cursor_tracks_event_target(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_override_cursors()
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_event_target_map_widget_backend,
    )

    panel = InfoPanel()
    try:
        panel.set_location_capability(enabled=True)
        panel.set_asset_metadata(
            {
                "rel": "map.jpg",
                "name": "map.jpg",
                "gps": {"lat": 48.137154, "lon": 11.576124},
                "location": "Munich",
            }
        )
        panel.show()
        _process_deferred_panel_content(qapp)

        map_view = panel._location_map
        map_widget = map_view._map_widget
        assert isinstance(map_widget, _EventTargetMiniMapWidget)
        event_target = map_widget.event_target()
        assert event_target is not map_widget

        _send_mouse_event(
            event_target,
            QEvent.Type.MouseButtonPress,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.LeftButton,
        )

        override_cursor = QApplication.overrideCursor()
        assert event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert map_widget.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert map_view.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert override_cursor is not None
        assert override_cursor.shape() == Qt.CursorShape.ClosedHandCursor

        _send_mouse_event(
            event_target,
            QEvent.Type.MouseMove,
            button=Qt.MouseButton.NoButton,
            buttons=Qt.MouseButton.LeftButton,
        )

        override_cursor = QApplication.overrideCursor()
        assert event_target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert override_cursor is not None
        assert override_cursor.shape() == Qt.CursorShape.ClosedHandCursor

        _send_mouse_event(
            event_target,
            QEvent.Type.MouseButtonRelease,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )

        assert QApplication.overrideCursor() is None
        assert event_target.cursor().shape() == Qt.CursorShape.ArrowCursor
    finally:
        panel.shutdown()
        panel.close()
        _clear_override_cursors()


def test_info_panel_location_map_drag_cursor_resets_on_hide_and_shutdown(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_override_cursors()
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_event_target_map_widget_backend,
    )

    panel = InfoPanel()
    try:
        panel.set_location_capability(enabled=True)
        panel.set_asset_metadata(
            {
                "rel": "map.jpg",
                "name": "map.jpg",
                "gps": {"lat": 48.137154, "lon": 11.576124},
                "location": "Munich",
            }
        )
        panel.show()
        _process_deferred_panel_content(qapp)

        map_view = panel._location_map
        map_widget = map_view._map_widget
        assert isinstance(map_widget, _EventTargetMiniMapWidget)
        event_target = map_widget.event_target()

        _send_mouse_event(
            event_target,
            QEvent.Type.MouseButtonPress,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.LeftButton,
        )
        assert QApplication.overrideCursor() is not None

        map_view.hide()
        qapp.processEvents()

        assert QApplication.overrideCursor() is None
        assert event_target.cursor().shape() == Qt.CursorShape.ArrowCursor

        _send_mouse_event(
            event_target,
            QEvent.Type.MouseButtonPress,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.LeftButton,
        )
        assert QApplication.overrideCursor() is not None

        map_view.shutdown()

        assert QApplication.overrideCursor() is None
        assert map_view._map_event_targets == []
    finally:
        panel.close()
        _clear_override_cursors()


def test_info_panel_location_map_drag_cursor_uses_global_map_host_filter(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_override_cursors()
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_event_target_map_widget_backend,
    )

    panel = InfoPanel()
    try:
        panel.set_location_capability(enabled=True)
        panel.set_asset_metadata(
            {
                "rel": "map.jpg",
                "name": "map.jpg",
                "gps": {"lat": 48.137154, "lon": 11.576124},
                "location": "Munich",
            }
        )
        panel.show()
        _process_deferred_panel_content(qapp)

        map_view = panel._location_map
        fallback_receiver = QWidget(map_view._map_host)
        fallback_receiver.setGeometry(0, 0, 24, 24)
        fallback_receiver.show()

        assert map_view._application_event_filter_installed is True
        local_pos = QPointF(12.0, 12.0)
        outside_host = QPointF(-10_000.0, -10_000.0)
        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local_pos,
            outside_host,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        map_view.eventFilter(fallback_receiver, press_event)

        override_cursor = QApplication.overrideCursor()
        assert fallback_receiver not in map_view._map_event_targets
        assert override_cursor is not None, (
            "map-host application filter did not start drag: "
            f"installed={map_view._application_event_filter_installed}, "
            f"dragging={map_view._dragging}, "
            f"receiver_parent_is_host={fallback_receiver.parent() is map_view._map_host}"
        )
        assert override_cursor.shape() == Qt.CursorShape.ClosedHandCursor

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            local_pos,
            outside_host,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        map_view.eventFilter(fallback_receiver, release_event)

        assert QApplication.overrideCursor() is None, (
            "map-host application filter did not release the drag cursor: "
            f"dragging={map_view._dragging}"
        )
    finally:
        panel.shutdown()
        panel.close()
        _clear_override_cursors()


def test_info_panel_formatter_locale_controls_numbers(qapp: QApplication) -> None:
    del qapp
    panel = None
    try:
        formatters.set_current_locale("de_DE")
        panel = InfoPanel()
        panel.set_asset_metadata(
            {
                "rel": "clip.MOV",
                "name": "clip.MOV",
                "is_video": True,
                "bytes": 24_192_000,
                "frame_rate": 59.94,
            }
        )

        assert "23,1 MB" in panel._summary_label.text()
        assert "59,94 fps" in panel._exposure_label.text()
    finally:
        formatters.set_current_locale("en_US")
        if panel is not None:
            panel.close()


def test_info_panel_retranslate_refreshes_static_text_and_metadata(qapp: QApplication) -> None:
    del qapp
    panel = InfoPanel()
    try:
        panel.set_asset_metadata(
            {
                "rel": "clip.MOV",
                "name": "clip.MOV",
                "is_video": True,
                "_metadata_loading": True,
            }
        )
        panel.set_location_capability(enabled=False)

        assert panel._title_label.text() == "Info"
        assert panel._location_download_button.text() == "Download Map Extension"
        assert panel._exposure_label.text() == "Loading detailed video information..."

        panel.retranslate_ui()

        assert panel._title_label.text() == "Info"
        assert panel._location_download_button.text() == "Download Map Extension"
        assert panel._exposure_label.text() == "Loading detailed video information..."
    finally:
        panel.close()


def test_info_panel_location_map_uses_post_render_pin_when_available(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_post_render_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 48.137154, "lon": 11.576124},
            "location": "Munich",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _PostRenderMiniMapWidget)
    assert len(map_widget.post_render_painters) == 1
    callback = map_widget.post_render_painters[0]
    assert callback is map_view._pin_paint_callback
    assert map_view._overlay.isHidden()

    screen_point = map_view._screen_point
    assert screen_point is not None
    assert abs(screen_point.x() - map_widget.width() / 2.0) <= 1.0
    assert abs(screen_point.y() - map_widget.height() / 2.0) <= 1.0

    pixmap = QPixmap(map_widget.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    callback(painter)
    painter.end()
    image = pixmap.toImage()
    sample_x = int(round(screen_point.x()))
    sample_y = int(round(screen_point.y())) - 12
    sample_y = max(0, min(image.height() - 1, sample_y))
    assert image.pixelColor(sample_x, sample_y).alpha() > 0

    map_view.shutdown()
    assert map_widget.removed_post_render_painters == [callback]
    panel.close()


def test_info_panel_location_map_resyncs_pin_after_delayed_zoom_projection(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_delayed_projection_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 35.6764, "lon": 139.6500},
            "location": "Tokyo",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _DelayedProjectionMiniMapWidget)

    map_widget.set_zoom(9.0)
    qapp.processEvents()

    screen_point = map_view._screen_point
    assert screen_point is not None
    assert abs(screen_point.x() - map_widget.width() / 2.0) <= 1.0
    assert abs(screen_point.y() - map_widget.height() / 2.0) <= 1.0
    panel.close()


def test_info_panel_location_map_recenters_view_after_widget_becomes_visible(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(info_location_map_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(
        info_location_map_module,
        "choose_map_widget_backend",
        _fake_choose_deferred_center_map_widget_backend,
    )

    panel = InfoPanel()
    panel.set_location_capability(enabled=True)
    panel.set_asset_metadata(
        {
            "rel": "map.jpg",
            "name": "map.jpg",
            "gps": {"lat": 51.5074, "lon": -0.1278},
            "location": "London",
        }
    )
    panel.show()
    _process_deferred_panel_content(qapp)

    map_view = panel._location_map
    map_widget = map_view._map_widget
    assert isinstance(map_widget, _DeferredCenterMiniMapWidget)

    center_lon, center_lat = map_widget.center_lonlat()
    assert abs(center_lon - (-0.1278)) <= 1e-6
    assert abs(center_lat - 51.5074) <= 1e-6

    screen_point = map_view._screen_point
    assert screen_point is not None
    assert abs(screen_point.x() - map_widget.width() / 2.0) <= 1.0
    assert abs(screen_point.y() - map_widget.height() / 2.0) <= 1.0
    panel.close()

"""Focused tests for the floating info panel widget."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.widgets.info_panel import (
    InfoPanel,
    _FACE_ADD_BUTTON_SIZE,
    _FACE_ADD_ICON_SIZE,
    _FACE_AVATAR_DIAMETER,
)
from iPhoto.gui.ui.widgets import info_location_map as info_location_map_module
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

    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent)
        self._map_source = map_source
        self._zoom = 2.0
        self._center: tuple[float, float] = (0.0, 0.0)
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
        return None

    def map_backend_metadata(self) -> MapBackendMetadata:
        return MapBackendMetadata(2.0, 19.0, True, "raster", "xyz")


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
    assert delete_label == "删除"
    assert not_this_label == "这不是Alice"
    assert submenu_labels == ("选择其他人…", "给此人重命名…")
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
    assert avatar._menu_action_labels()[1] == "这不是此人"
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


def test_info_panel_hidden_metadata_update_recomputes_height(qapp: QApplication) -> None:
    """Updating metadata while hidden should expand the panel on the next show."""

    sparse = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
    }
    rich = {
        "rel": "IMG_3686.HEIC",
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

    panel.hide()
    qapp.processEvents()
    panel.set_asset_metadata(rich)

    panel.show()
    qapp.processEvents()
    layout = panel.layout()
    expected_height = layout.totalHeightForWidth(max(panel.width(), panel.minimumWidth()))
    assert panel.height() > sparse_height
    assert panel.height() >= expected_height
    panel.close()


def test_info_panel_first_show_schedules_post_show_reflow(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel should queue a deferred geometry reflow on the first show."""

    panel = InfoPanel()
    schedule = Mock()

    monkeypatch.setattr(panel, "_schedule_post_show_reflow", schedule)

    panel.show()
    qapp.processEvents()

    schedule.assert_called_once_with(recenter=True)
    panel.close()


def test_info_panel_visible_metadata_update_schedules_post_show_reflow(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible metadata refreshes should queue a deferred reflow."""

    panel = InfoPanel()
    schedule = Mock()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "codec": "hevc",
    }

    panel.show()
    qapp.processEvents()
    monkeypatch.setattr(panel, "_schedule_post_show_reflow", schedule)

    panel.set_asset_metadata(metadata)

    schedule.assert_called_once_with(recenter=False)
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
    qapp.processEvents()

    map_view = panel._location_map
    assert map_view.width() == map_view.height()
    assert map_view._map_host.size() == map_view.size()
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
    qapp.processEvents()

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
    qapp.processEvents()

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
    qapp.processEvents()

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

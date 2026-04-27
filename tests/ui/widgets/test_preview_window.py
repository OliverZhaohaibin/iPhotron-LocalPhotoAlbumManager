from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for preview window tests", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.preview_window import (
    PreviewWindow,
    _RHI_PREVIEW_SHADOW_PADDING,
    _PreviewWheelGuard,
    _RhiPreviewPopup,
)


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_do_close_unloads_media_and_hides_window() -> None:
    window = PreviewWindow.__new__(PreviewWindow)
    window._close_timer = Mock()
    window._media = Mock()
    window._rhi_popup = Mock()
    window.hide = Mock()

    PreviewWindow._do_close(window)

    window._close_timer.stop.assert_called_once_with()
    window._media.unload.assert_called_once_with()
    window._rhi_popup.close_preview.assert_called_once_with()
    window.hide.assert_called_once_with()


def _make_preview_window_for_show() -> PreviewWindow:
    window = PreviewWindow.__new__(PreviewWindow)
    window._close_timer = Mock()
    window._media = Mock()
    window._rhi_popup = Mock()
    window._current_native_size = None
    window._native_size_seeded_from_probe = True
    window._native_size_seeded = True
    window._pending_orientation_flip = 90
    window._aspect_ratio_hint = 16 / 9
    window._active_probe_request_id = 0
    window._active_source_key = ""
    window._anchor_rect = None
    window._anchor_point = None
    window._prime_native_size_async = Mock()
    window._apply_layout_for_anchor = Mock()
    window.hide = Mock()
    window.show = Mock()
    window.raise_ = Mock()
    return window


def test_show_preview_uses_rhi_popup_for_unedited_video_on_macos() -> None:
    window = _make_preview_window_for_show()
    source = Path("/fake/video.mov")

    with patch("iPhoto.gui.ui.widgets.preview_window.sys.platform", "darwin"):
        PreviewWindow.show_preview(window, source, adjusted_preview=False)

    assert window._using_rhi_popup is True
    window._media.unload.assert_called_once_with()
    window._media.load.assert_not_called()
    window._media.play.assert_not_called()
    window._rhi_popup.close_preview.assert_called_once_with()
    window._rhi_popup.show_preview.assert_called_once_with(
        source,
        adjustments=None,
        trim_range_ms=None,
        adjusted_preview=False,
    )
    window.hide.assert_called_once_with()
    window.show.assert_not_called()
    window.raise_.assert_not_called()


def test_show_preview_keeps_plain_media_path_for_unedited_video_off_macos() -> None:
    window = _make_preview_window_for_show()
    source = Path("/fake/video.mp4")

    with patch("iPhoto.gui.ui.widgets.preview_window.sys.platform", "linux"):
        PreviewWindow.show_preview(window, source, adjusted_preview=False)

    assert window._using_rhi_popup is False
    window._media.unload.assert_called_once_with()
    window._media.load.assert_called_once_with(source)
    window._media.play.assert_called_once_with()
    window._rhi_popup.close_preview.assert_called_once_with()
    window._rhi_popup.show_preview.assert_not_called()
    window.show.assert_called_once_with()
    window.raise_.assert_called_once_with()
    window.hide.assert_not_called()


def test_rhi_popup_resize_preview_includes_shadow_padding(qapp) -> None:
    del qapp
    popup = _RhiPreviewPopup()

    try:
        popup.resize_preview(QSize(320, 180))

        padding = _RHI_PREVIEW_SHADOW_PADDING
        content_rect = QRect(padding, padding, 320, 180)
        assert popup.size() == QSize(320 + 2 * padding, 180 + 2 * padding)
        assert popup._shadow_frame.geometry() == content_rect
        assert popup._content_frame.geometry() == content_rect
        assert not popup._content_frame.mask().isEmpty()
        assert popup._video_area.geometry() == QRect(0, 0, 320, 180)
        assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert popup.mask().isEmpty()
    finally:
        popup.close_preview()
        popup.deleteLater()


def _make_rhi_popup_for_show() -> _RhiPreviewPopup:
    popup = _RhiPreviewPopup.__new__(_RhiPreviewPopup)
    popup._video_area = Mock()
    popup._set_rounding_mode = Mock()
    popup.show = Mock()
    popup.raise_ = Mock()
    return popup


def test_rhi_popup_forces_single_adjusted_surface_on_macos() -> None:
    popup = _make_rhi_popup_for_show()
    source = Path("/fake/unedited.mov")

    with patch("iPhoto.gui.ui.widgets.preview_window.sys.platform", "darwin"):
        _RhiPreviewPopup.show_preview(
            popup,
            source,
            adjustments=None,
            trim_range_ms=None,
            adjusted_preview=False,
        )

    popup._video_area.stop.assert_called_once_with()
    popup._video_area.load_video.assert_called_once_with(
        source,
        adjustments=None,
        trim_range_ms=None,
        adjusted_preview=True,
    )
    popup._video_area.play.assert_called_once_with()


def test_rhi_popup_keeps_plain_surface_off_macos_for_unedited_preview() -> None:
    popup = _make_rhi_popup_for_show()
    source = Path("/fake/unedited.mp4")

    with patch("iPhoto.gui.ui.widgets.preview_window.sys.platform", "linux"):
        _RhiPreviewPopup.show_preview(
            popup,
            source,
            adjustments=None,
            trim_range_ms=None,
            adjusted_preview=False,
        )

    popup._video_area.load_video.assert_called_once_with(
        source,
        adjustments=None,
        trim_range_ms=None,
        adjusted_preview=False,
    )


def test_preview_wheel_guard_blocks_wheel_events(qapp) -> None:
    del qapp
    guard = _PreviewWheelGuard()
    event = QWheelEvent(
        QPointF(12.0, 18.0),
        QPointF(12.0, 18.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    event.ignore()

    assert guard.eventFilter(Mock(), event) is True
    assert event.isAccepted()


def test_preview_wheel_guard_ignores_non_wheel_events(qapp) -> None:
    del qapp
    guard = _PreviewWheelGuard()
    event = QEvent(QEvent.Type.MouseMove)

    assert guard.eventFilter(Mock(), event) is False

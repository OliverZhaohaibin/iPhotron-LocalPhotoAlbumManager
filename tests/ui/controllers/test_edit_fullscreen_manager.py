from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QSplitter, QWidget

from iPhoto.gui.ui.controllers.edit_fullscreen_manager import EditFullscreenManager


def test_fullscreen_round_trip_resets_and_relayouts_active_video_viewport(qapp) -> None:
    window = QWidget()
    splitter = QSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    fallback_still_viewport = Mock()
    active_video_viewport = Mock()
    ui = SimpleNamespace(
        edit_sidebar=QWidget(),
        window_chrome=QWidget(),
        menu_bar_container=QWidget(),
        menu_bar=QWidget(),
        sidebar=QWidget(),
        status_bar=QWidget(),
        edit_header_container=QWidget(),
        splitter=splitter,
        edit_image_viewer=fallback_still_viewport,
    )
    manager = EditFullscreenManager(
        ui,
        window,
        active_viewport_provider=lambda: active_video_viewport,
    )

    assert manager.enter_fullscreen_preview() is True
    assert manager.exit_fullscreen_preview() is True

    active_video_viewport.reset_zoom.assert_called_once_with()
    assert active_video_viewport.request_viewport_relayout.call_count == 2
    assert active_video_viewport.request_viewport_relayout.call_args_list[0].kwargs == {}
    assert active_video_viewport.request_viewport_relayout.call_args_list[1].kwargs == {
        "reset_view": True
    }
    fallback_still_viewport.reset_zoom.assert_not_called()
    fallback_still_viewport.request_viewport_relayout.assert_not_called()

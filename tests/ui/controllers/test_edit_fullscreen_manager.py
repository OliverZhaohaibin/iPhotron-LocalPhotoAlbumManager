from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from iPhoto.gui import windowed_fullscreen
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

    active_video_viewport.reset_zoom.assert_not_called()
    assert active_video_viewport.request_viewport_relayout.call_count == 2
    assert active_video_viewport.request_viewport_relayout.call_args_list[0].kwargs == {
        "reset_view": True
    }
    assert active_video_viewport.request_viewport_relayout.call_args_list[1].kwargs == {
        "reset_view": True
    }
    fallback_still_viewport.reset_zoom.assert_not_called()
    fallback_still_viewport.request_viewport_relayout.assert_not_called()


def _visible_editor(qapp):
    window = QWidget()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    window.setGeometry(60, 80, 600, 400)
    window.show()
    splitter = QSplitter(window)
    splitter.resize(500, 200)
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    fields = (
        "edit_sidebar",
        "window_chrome",
        "menu_bar_container",
        "menu_bar",
        "sidebar",
        "status_bar",
        "edit_header_container",
    )
    ui = SimpleNamespace(
        **{field: QWidget(window) for field in fields}, splitter=splitter, edit_image_viewer=Mock()
    )
    for field in fields:
        getattr(ui, field).show()
    ui.edit_sidebar.setMinimumWidth(120)
    ui.edit_sidebar.setMaximumWidth(240)
    ui.status_bar.hide()  # Preserve intentionally hidden chrome too.
    for _ in range(4):
        qapp.processEvents()
    manager = EditFullscreenManager(ui, window)
    return window, ui, manager


def _pump(qapp):
    for _ in range(10):
        qapp.processEvents()


def test_system_maximize_restores_edit_chrome_and_next_fullscreen_round_trip(qapp, monkeypatch):
    window, ui, manager = _visible_editor(qapp)
    monkeypatch.setattr(windowed_fullscreen, "_use_windowed_fullscreen", lambda _: True)
    try:
        assert manager.enter_fullscreen_preview()
        _pump(qapp)
        assert manager.is_in_fullscreen()
        assert not ui.edit_header_container.isVisible()
        window.showMaximized()
        normal = Mock(wraps=window.showNormal)
        restore = Mock(wraps=window.restoreGeometry)
        monkeypatch.setattr(window, "showNormal", normal)
        monkeypatch.setattr(window, "restoreGeometry", restore)
        _pump(qapp)
        assert window.isMaximized()
        assert not manager.is_in_fullscreen()
        assert ui.edit_header_container.isVisible()
        assert ui.edit_sidebar.minimumWidth() == 120
        assert ui.edit_sidebar.maximumWidth() == 240
        assert not ui.status_bar.isVisible()
        normal.assert_not_called()
        restore.assert_not_called()
        assert manager.enter_fullscreen_preview()
        _pump(qapp)
        assert manager.is_in_fullscreen()
        assert manager.exit_fullscreen_preview()
        _pump(qapp)
        assert window.isMaximized()
        assert ui.edit_header_container.isVisible()
        assert not ui.status_bar.isVisible()
    finally:
        window.close()


def test_failed_windowed_and_native_entry_restore_edit_ui(qapp, monkeypatch):
    window, ui, manager = _visible_editor(qapp)
    original = window.geometry()
    monkeypatch.setattr(windowed_fullscreen, "_use_windowed_fullscreen", lambda _: True)
    monkeypatch.setattr(window, "setGeometry", Mock())
    monkeypatch.setattr(window, "showFullScreen", Mock())
    try:
        manager.enter_fullscreen_preview()
        _pump(qapp)
        assert not manager.is_in_fullscreen()
        assert not windowed_fullscreen.is_media_fullscreen(window)
        assert window.geometry() == original
        assert ui.edit_header_container.isVisible()
        assert ui.edit_sidebar.minimumWidth() == 120
        assert ui.edit_sidebar.maximumWidth() == 240
        assert not ui.status_bar.isVisible()
    finally:
        window.close()


def test_stale_reconciliation_does_not_end_a_new_edit_fullscreen(qapp, monkeypatch):
    window, ui, manager = _visible_editor(qapp)
    monkeypatch.setattr(windowed_fullscreen, "_use_windowed_fullscreen", lambda _: True)
    try:
        manager.enter_fullscreen_preview()
        manager.exit_fullscreen_preview()
        manager.enter_fullscreen_preview()
        _pump(qapp)
        assert manager.is_in_fullscreen()
        assert not ui.edit_header_container.isVisible()
        manager.exit_fullscreen_preview()
    finally:
        window.close()

"""Exercise Done with the desktop coordinators and real Qt layouts offscreen."""

from __future__ import annotations

from threading import Thread
from time import monotonic, sleep

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy
from shiboken6 import isValid

from iPhoto.bootstrap.runtime_context import RuntimeContext
from iPhoto.core.adjustment_mapping import VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.coordinators import edit_coordinator
from iPhoto.gui.coordinators.desktop_coordinator_runtime import DesktopCoordinatorRuntime
from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.ui.media import MediaRestoreRequest
from iPhoto.gui.ui.ui_main_window import FeatureKind
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.infrastructure.services.thumbnail_artifact import thumbnail_revision
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from iPhoto.settings import SettingsManager


class _GeometryRecorder(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.changes = []
        window.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.changes.append(watched.geometry())
        return False


def _wait_until(qapp, condition) -> None:
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        qapp.processEvents()
        # Let Python worker threads run while waiting for their queued Qt signals.
        sleep(0.001)
    assert condition()


def _finish_transition(qapp, manager, completion, direction, *, realtime) -> None:
    animation = manager._transition_group
    assert animation is not None
    if realtime:
        assert completion.wait(5000)
    else:
        # Drive the real animation and process layout on each side of the header swap.
        animation.pause()
        for fraction in (0.4, 0.6, 1.0):
            animation.setCurrentTime(round(animation.duration() * fraction))
            qapp.processEvents()
    assert completion.count() == 1
    assert completion.at(0) == [direction]
    assert not manager.is_transition_active()
    qapp.processEvents()


@pytest.fixture
def desktop(qapp, tmp_path, request):
    stylesheet, palette = qapp.styleSheet(), qapp.palette()
    request.addfinalizer(lambda: qapp.setStyleSheet(stylesheet))
    request.addfinalizer(lambda: qapp.setPalette(palette))
    settings = SettingsManager(tmp_path / "settings.json")
    settings.set("ui.theme", "light")
    context = RuntimeContext.create(defer_startup=True, settings=settings)
    window = MainWindow(context)

    def destroy_window() -> None:
        if isValid(window):
            window.close()
            window.deleteLater()
            # The shared application could otherwise delete it in a later test.
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    request.addfinalizer(destroy_window)
    window.ui.prepare_detail_native_hierarchy()
    window.show()
    qapp.processEvents()
    window.ui.ensure_feature(FeatureKind.DETAIL)

    root = tmp_path.resolve() / "library"
    root.mkdir()
    library = context.open_library(root)
    runtime = DesktopCoordinatorRuntime(window, context)
    window.bind_coordinators(runtime, runtime.gallery, runtime.detail)
    runtime.start()
    return window, runtime, library


@pytest.mark.parametrize("error_source", ["facade", "library"])
def test_queued_runtime_errors_follow_the_main_window_lifetime(
    qapp, desktop, monkeypatch, error_source
) -> None:
    window, runtime, _library = desktop
    source = getattr(runtime._context, error_source)
    shown: list[str] = []
    monkeypatch.setattr(dialogs, "show_error", lambda _parent, message: shown.append(message))

    def queue_error(message: str) -> None:
        worker = Thread(target=source.errorRaised.emit, args=(message,))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()

    queue_error("window open")
    assert shown == []
    qapp.processEvents()
    assert shown == ["window open"]

    shutdown_playback = runtime._playback.shutdown

    def deliver_during_shutdown() -> None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
        shutdown_playback()

    monkeypatch.setattr(runtime._playback, "shutdown", deliver_during_shutdown)
    queue_error("window closing")
    assert window.close()
    queue_error("window closed")
    qapp.processEvents()
    queue_error("window destroyed")
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    assert not isValid(window)
    qapp.processEvents()
    assert shown == ["window open"]


def _prepare_asset(qapp, runtime, library, ui, *, video, monkeypatch):
    root = library.library_root
    image = QImage(160, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("navy"))
    source = root / ("clip.mp4" if video else "photo.png")
    if video:
        # Replace native source loading while exercising the Qt video widget and restore path.
        source.write_bytes(b"video fixture")
        monkeypatch.setattr(ui.video_area._player, "setSource", lambda _url: None)
        monkeypatch.setattr(edit_coordinator, "read_video_meta", lambda _: {"dur": 4.0})
        monkeypatch.setattr(edit_coordinator, "probe_video_rotation", lambda _: (0, 160, 120))

        def supply_trim_thumbnail(worker):
            worker.signals.ready.emit([image], worker._generation)
            worker.signals.finished.emit(worker._generation)

        monkeypatch.setattr(edit_coordinator.VideoTrimThumbnailWorker, "run", supply_trim_thumbnail)
    else:
        assert image.save(str(source))

    # Publish fixture pixels for Filmstrip and keep thumbnail regeneration outside this test.
    key = thumbnail_cache_key(source)
    thumbnail_path = thumbnail_cache_file_for_key(library.assets.path.parent / "cache" / "thumbs", key)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    assert image.save(str(thumbnail_path))
    monkeypatch.setattr(library.asset_queries, "_queue_thumbnail_backfill", lambda *args, **kwargs: 0)

    stat = source.stat()
    library.assets.append_rows([{
        "rel": source.name,
        "id": "edit-done-asset",
        "media_type": int(video),
        "bytes": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "w": image.width(),
        "h": image.height(),
        "dur": 6.0 if video else 0.0,
        "video_rotation_cw": 0,
        "video_linux_180_hint": False,
        "thumbnail_state": "ready",
        "thumb_cache_key": key,
        "thumb_revision": thumbnail_revision(source),
        "micro_thumbnail": thumbnail_path.read_bytes(),
    }])
    runtime._gallery_store.load_selection(root, query=AssetQuery())
    _wait_until(qapp, lambda: runtime._gallery_store.asset_at(0) is not None)
    runtime._detail_vm.show_row(0)
    player = runtime._player_view_controller
    if video:
        _wait_until(qapp, lambda: ui.video_area._presentation_committed)
        _submit_video_frame(qapp, ui.video_area)
    else:
        # The CPU decoder supplies the still surface. Acknowledge it at the GPU boundary.
        _wait_until(qapp, lambda: ui.image_viewer.current_image_source() is not None)
        key = ui.image_viewer.current_image_source()
        if not player.has_current_render_session(source):
            generation = runtime._detail_vm.presentation.value.request_generation
            ui.image_viewer.stillFrameSubmitted.emit(key, generation)
            qapp.processEvents()
            ui.image_viewer.frameSubmitted.emit()
        assert player.has_current_render_session(source)
    qapp.processEvents()
    return source


def _submit_video_frame(qapp, video_area) -> None:
    # Drive the same callbacks as an uploaded frame, including Windows' composition barrier.
    video_area.video_view().videoFramePresented.emit(video_area._media_generation, 1)
    qapp.processEvents()
    video_area.surfaceCompositionSubmitted.emit()
    qapp.processEvents()


@pytest.mark.parametrize("video", [False, True], ids=["photo", "video"])
@pytest.mark.parametrize("filmstrip", [False, True], ids=["filmstrip-off", "filmstrip-on"])
@pytest.mark.parametrize("maximized", [False, True], ids=["windowed", "maximized"])
def test_done_button_preserves_main_window_geometry(
    qapp, qtbot, desktop, monkeypatch, video, filmstrip, maximized
) -> None:
    window, runtime, library = desktop
    ui = window.ui
    ui.toggle_filmstrip_action.setChecked(filmstrip)
    source = _prepare_asset(qapp, runtime, library, ui, video=video, monkeypatch=monkeypatch)
    original_media = source.read_bytes()
    video_area = ui.video_area
    if maximized:
        window.showMaximized()
    else:
        window.resize(900, 640)
    qapp.processEvents()
    assert window.isMaximized() == maximized
    if not maximized:
        assert window.size() == QSize(900, 640)
    baseline = window.geometry()
    splitter_sizes = ui.splitter.sizes()
    geometry = _GeometryRecorder(window)
    restores = []
    runtime._media_session.restoreRequested.connect(restores.append)
    commits = QSignalSpy(runtime._adjustment_committer.adjustmentsCommitted)
    still_session = runtime._player_view_controller._current_render_session
    original_presentation = runtime._detail_vm.presentation.value
    texture_key = ui.image_viewer.current_image_source()
    assert ui.filmstrip_view.isVisible() == filmstrip

    for cycle in range(20):
        assert ui.edit_button.isVisible() and ui.edit_button.isEnabled()
        qtbot.mouseClick(ui.edit_button, Qt.MouseButton.LeftButton)
        edit = runtime._edit
        assert edit.is_editing()
        manager = edit._transition_manager
        _finish_transition(
            qapp, manager, QSignalSpy(manager.transition_finished), "enter", realtime=cycle == 0
        )
        assert window.geometry() == baseline
        assert ui.edit_header_container.isVisible()
        assert not ui.detail_chrome_container.isVisible()
        assert not ui.filmstrip_view.isVisible()
        assert ui.video_trim_bar.isVisible() == video

        ui.edit_mode_control.setCurrentIndex(1)
        session = ui.edit_sidebar.session()
        exposure = (cycle + 1) / 25
        session.set_value("Exposure", exposure)
        if video:
            # A missing out point makes playback use the probed duration from the restore request.
            trim_out_sec = 3.5 if cycle % 2 else 0.0
            session.set_values({VIDEO_TRIM_IN_KEY: 0.5, VIDEO_TRIM_OUT_KEY: trim_out_sec})
            edit_media_generation = video_area._media_generation

        assert ui.edit_done_button.isVisible() and ui.edit_done_button.isEnabled()
        exit_completed = QSignalSpy(manager.transition_finished)
        qtbot.mouseClick(ui.edit_done_button, Qt.MouseButton.LeftButton)
        assert ui.edit_header_container.isVisible()
        assert not ui.detail_chrome_container.isVisible()
        if video and cycle == 0:
            # A Gallery refresh can republish indexed metadata during the queued restore.
            runtime._detail_vm.refresh_current()
        _finish_transition(qapp, manager, exit_completed, "exit", realtime=cycle == 0)

        assert commits.count() == cycle + 1
        assert commits.at(cycle) == [source, "edit_done"]
        saved = library.edit.read_adjustments(source)
        assert saved["Exposure"] == pytest.approx(exposure)
        assert runtime._media_session.current_source() == source
        assert ui.view_stack.currentWidget() is ui.detail_page
        restored = runtime._detail_vm.presentation.value
        assert restored.path == source

        if video:
            assert restores == [MediaRestoreRequest(source, "edit_done", 4.0)] * (cycle + 1)
            assert restored.reload_token == original_presentation.reload_token + cycle + 1
            _wait_until(
                qapp,
                lambda: video_area._presentation_committed
                and video_area._media_generation > edit_media_generation,
            )
            _submit_video_frame(qapp, video_area)
            assert ui.player_stack.currentWidget() is video_area
            assert video_area.current_source() == source
            assert video_area.controls_enabled()
            assert video_area.adjusted_preview_enabled()
            assert video_area.trim_range_ms() == (500, int((trim_out_sec or 4.0) * 1000))
            expected_state = library.edit.describe_adjustments(source, duration_hint=4.0)
            assert video_area.edit_viewer._adjustments == expected_state.resolved_adjustments
            assert saved[VIDEO_TRIM_IN_KEY] == 0.5
            assert saved.get(VIDEO_TRIM_OUT_KEY, 0.0) == trim_out_sec
        else:
            assert restores == []
            assert restored.render_key == original_presentation.render_key
            assert restored.request_generation == original_presentation.request_generation
            assert runtime._player_view_controller._current_render_session is still_session
            assert ui.player_stack.currentWidget() is ui.image_viewer
            assert ui.image_viewer.current_image_source() == texture_key
            assert still_session.edit_references == 0
            assert still_session.baseline_state.revision[0] == "commit"
            assert still_session.baseline_state.raw_adjustments["Exposure"] == pytest.approx(exposure)
            assert ui.image_viewer._adjustments == still_session.baseline_state.shader_adjustments

        assert not edit.is_editing()
        assert ui.edit_sidebar.session() is None
        assert ui.detail_chrome_container.isVisible()
        assert not ui.edit_header_container.isVisible()
        assert not ui.edit_sidebar.isVisible()
        assert not ui.video_trim_bar.isVisible()
        assert ui.filmstrip_view.isVisible() == filmstrip
        assert ui.splitter.sizes() == splitter_sizes
        assert window.isMaximized() == maximized
        assert window.geometry() == baseline
        assert not geometry.changes, f"geometry changed during Edit/Done cycle {cycle + 1}"

    assert source.read_bytes() == original_media

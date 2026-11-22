from __future__ import annotations

from importlib.resources import as_file, files
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for transition tests", exc_type=ImportError)

from PySide6.QtCore import QEvent, QObject, QTranslator
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from iPhoto.gui.ui.controllers import edit_fullscreen_manager
from iPhoto.gui.ui.controllers.edit_view_transition import EditViewTransitionManager
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.ui.ui_main_window import FeatureKind
from iPhoto.library.runtime_controller import LibraryRuntimeController


class _SidebarWidget(QWidget):
    def relax_minimum_width_for_animation(self) -> None:
        self.setMinimumWidth(0)

    def restore_minimum_width_after_animation(self) -> None:
        self.setMinimumWidth(160)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _build_test_ui() -> tuple[QWidget, SimpleNamespace]:
    window = QWidget()
    root_layout = QHBoxLayout(window)
    root_layout.setContentsMargins(0, 0, 0, 0)

    splitter = QSplitter()
    root_layout.addWidget(splitter)

    sidebar = _SidebarWidget()
    sidebar.setMinimumWidth(160)
    sidebar.setMaximumWidth(320)

    right_panel = QFrame()
    right_layout = QVBoxLayout(right_panel)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(0)

    detail_chrome_container = QWidget()
    detail_chrome_container.setFixedHeight(48)
    edit_header_container = QWidget()
    edit_header_container.setFixedHeight(48)
    edit_header_container.hide()

    viewport = QWidget()
    viewport.setObjectName("viewport")
    viewport.setMinimumHeight(100)
    filmstrip_view = QWidget()
    filmstrip_view.setFixedHeight(96)

    right_layout.addWidget(detail_chrome_container)
    right_layout.addWidget(edit_header_container)
    right_layout.addWidget(viewport, 1)
    right_layout.addWidget(filmstrip_view)

    splitter.addWidget(sidebar)
    splitter.addWidget(right_panel)

    edit_sidebar = QWidget(right_panel)
    edit_sidebar.setProperty("defaultPreferredWidth", 280)
    edit_sidebar.setProperty("defaultMinimumWidth", 140)
    edit_sidebar.setProperty("defaultMaximumWidth", 420)
    edit_sidebar.setMinimumWidth(0)
    edit_sidebar.setMaximumWidth(0)
    edit_sidebar.hide()

    ui = SimpleNamespace(
        splitter=splitter,
        sidebar=sidebar,
        edit_sidebar=edit_sidebar,
        detail_chrome_container=detail_chrome_container,
        edit_header_container=edit_header_container,
        filmstrip_view=filmstrip_view,
        viewport=viewport,
    )
    return window, ui


def test_leave_edit_mode_restores_splitter_and_viewport_height(qapp) -> None:
    window, ui = _build_test_ui()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()

    ui.splitter.setSizes([260, 940])
    qapp.processEvents()
    baseline_splitter_sizes = [int(v) for v in ui.splitter.sizes()]
    baseline_viewport_height = ui.viewport.height()

    manager = EditViewTransitionManager(ui, window)
    manager.enter_edit_mode(animate=False)
    qapp.processEvents()

    edit_mode_viewport_height = ui.viewport.height()
    assert edit_mode_viewport_height > baseline_viewport_height

    manager.leave_edit_mode(animate=False, show_filmstrip=True)
    qapp.processEvents()

    restored_sizes = [int(v) for v in ui.splitter.sizes()]
    for restored, baseline in zip(restored_sizes, baseline_splitter_sizes):
        assert abs(restored - baseline) <= 1

    assert abs(ui.viewport.height() - baseline_viewport_height) <= 2
    assert ui.filmstrip_view.isVisible() is True


class _PlayerBarStub(QWidget):
    def retranslate_ui(self) -> None:
        pass


class _ResizeRecorder(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.sizes = []
        window.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self.sizes.append(event.size())
        return False


@pytest.fixture
def detail_window(qapp, qtbot, monkeypatch, request):
    stylesheet = qapp.styleSheet()
    request.addfinalizer(lambda: qapp.setStyleSheet(stylesheet))
    window = MainWindow(SimpleNamespace(library=LibraryRuntimeController()))
    qtbot.addWidget(window)
    ui = window.ui
    detail = ui.prepare_detail_native_hierarchy()
    # Exercise the real widget layouts without starting the multimedia runtime.
    monkeypatch.setattr(detail.image_viewer, "complete_runtime", lambda: None)
    monkeypatch.setattr(detail.video_area, "complete_runtime", lambda: None)
    detail.video_area._player_bar = _PlayerBarStub(detail.video_area)

    window.show()
    qapp.processEvents()
    ui.ensure_feature(FeatureKind.DETAIL)
    ui.view_stack.setCurrentWidget(detail)
    window.resize(window.width(), 640)
    qapp.processEvents()
    return window


def _settle_transition(qapp, manager: EditViewTransitionManager) -> None:
    if manager.is_transition_active():
        assert QSignalSpy(manager.transition_finished).wait(2000)
    qapp.processEvents()


@pytest.fixture
def german_ui(qapp):
    translator = QTranslator()
    with as_file(files("iPhoto.resources.i18n") / "iPhoto_de.qm") as path:
        assert translator.load(str(path))
        qapp.installTranslator(translator)
        try:
            yield
        finally:
            qapp.removeTranslator(translator)


@pytest.mark.parametrize("animate", [False, True], ids=["immediate", "animated"])
def test_edit_in_german_preserves_narrow_window_width(
    qapp, german_ui, detail_window, animate
) -> None:
    window = detail_window
    ui = window.ui
    ui.ensure_detail_edit_bundle()
    ui.zoom_widget.show()
    window.resize(900, 640)
    qapp.processEvents()
    assert ui.edit_button.text() == "Bearbeiten"
    assert window.width() == 900
    baseline = window.geometry()
    resizes = _ResizeRecorder(window)
    header = HeaderController(ui.location_label, ui.timestamp_label, ui)
    manager = EditViewTransitionManager(ui, window)

    header.switch_to_edit_mode()
    manager.enter_edit_mode(animate=animate)
    _settle_transition(qapp, manager)
    assert window.geometry() == baseline

    header.restore_detail_mode()
    manager.leave_edit_mode(animate=animate)
    _settle_transition(qapp, manager)
    assert window.geometry() == baseline
    assert not resizes.sizes


def test_edit_fullscreen_keeps_navigation_relaxed_until_edit_exits(
    detail_window, monkeypatch
) -> None:
    window = detail_window
    ui = window.ui
    ui.ensure_detail_edit_bundle()
    manager = EditViewTransitionManager(ui, window)
    fullscreen = edit_fullscreen_manager.EditFullscreenManager(ui, window)
    monkeypatch.setattr(edit_fullscreen_manager, "enter_media_fullscreen", lambda _: None)
    monkeypatch.setattr(edit_fullscreen_manager, "exit_media_fullscreen", lambda _: None)

    original_minimum = ui.sidebar.minimumWidth()
    manager.enter_edit_mode(animate=False)
    assert ui.sidebar.minimumWidth() == 0

    assert fullscreen.enter_fullscreen_preview()
    assert fullscreen.exit_fullscreen_preview()
    assert ui.sidebar.minimumWidth() == 0

    manager.leave_edit_mode(animate=False)
    assert ui.sidebar.minimumWidth() == original_minimum


@pytest.mark.parametrize("video", [False, True], ids=["photo", "video"])
@pytest.mark.parametrize("filmstrip", [False, True], ids=["filmstrip-off", "filmstrip-on"])
@pytest.mark.parametrize("animate, cycles", [(True, 1), (False, 20)], ids=["animated", "immediate"])
def test_edit_done_preserves_main_window_geometry(
    qapp, detail_window, video, filmstrip, animate, cycles
) -> None:
    window = detail_window
    ui = window.ui
    ui.player_stack.setCurrentWidget(ui.video_area if video else ui.image_viewer)
    ui.filmstrip_view.setVisible(filmstrip)
    qapp.processEvents()
    baseline = window.geometry()
    resizes = _ResizeRecorder(window)

    assert not hasattr(ui, "edit_header_container")
    ui.ensure_detail_edit_bundle()
    qapp.processEvents()
    assert window.geometry() == baseline
    manager = EditViewTransitionManager(ui, window)

    for _ in range(cycles):
        ui.edit_sidebar.set_video_edit_mode(video)
        ui.video_trim_bar.setVisible(video)
        manager.enter_edit_mode(animate=animate)
        _settle_transition(qapp, manager)
        assert window.geometry() == baseline
        assert ui.edit_header_container.isVisible()

        ui.video_trim_bar.hide()
        ui.edit_sidebar.set_video_edit_mode(False)
        manager.leave_edit_mode(animate=animate, show_filmstrip=filmstrip)
        _settle_transition(qapp, manager)
        assert window.geometry() == baseline
        assert ui.detail_chrome_container.isVisible()
        assert ui.filmstrip_view.isVisible() == filmstrip
        assert not ui.edit_header_container.isVisible()
        assert not ui.edit_sidebar.isVisible()

    assert not resizes.sizes


def test_edit_done_preserves_maximized_main_window(qapp, detail_window) -> None:
    window = detail_window
    ui = window.ui
    window.showMaximized()
    qapp.processEvents()
    baseline = window.geometry()

    ui.ensure_detail_edit_bundle()
    manager = EditViewTransitionManager(ui, window)
    manager.enter_edit_mode()
    _settle_transition(qapp, manager)
    assert window.isMaximized()
    assert window.geometry() == baseline

    manager.leave_edit_mode()
    _settle_transition(qapp, manager)
    assert window.isMaximized()
    assert window.geometry() == baseline


def test_edit_done_keeps_geometry_after_resize_in_edit(qapp, detail_window) -> None:
    window = detail_window
    ui = window.ui
    ui.ensure_detail_edit_bundle()
    manager = EditViewTransitionManager(ui, window)
    manager.enter_edit_mode(animate=False)
    _settle_transition(qapp, manager)

    target_height = window.height() + 40
    window.resize(window.width(), target_height)
    qapp.processEvents()
    assert window.height() == target_height
    resized = window.geometry()

    manager.leave_edit_mode()
    _settle_transition(qapp, manager)
    assert window.geometry() == resized


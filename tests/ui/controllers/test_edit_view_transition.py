from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for transition tests", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from iPhoto.gui.ui.controllers.edit_view_transition import EditViewTransitionManager


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


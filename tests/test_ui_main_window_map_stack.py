from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for UI stack tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required for UI stack tests", exc_type=ImportError)

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QStackedLayout,
    QStackedWidget,
)

from iPhoto.gui.ui.ui_main_window import Ui_MainWindow, _configure_main_view_stack


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _DummyMapView:
    def __init__(self, *, native: bool) -> None:
        self._native = native

    def uses_native_osmand_widget(self) -> bool:
        return self._native


def test_configure_main_view_stack_leaves_native_map_page_default_stack_mode(qapp: QApplication) -> None:
    del qapp
    stack = QStackedWidget()
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())

    _configure_main_view_stack(stack, _DummyMapView(native=True))

    assert isinstance(stack.layout(), QStackedLayout)
    assert stack.layout().stackingMode() == QStackedLayout.StackOne


def test_configure_main_view_stack_can_opt_in_to_keep_native_map_page_alive(qapp: QApplication, monkeypatch) -> None:
    del qapp
    monkeypatch.setattr("iPhoto.gui.ui.ui_main_window.sys.platform", "linux")
    monkeypatch.setenv("IPHOTO_KEEP_NATIVE_MAP_PAGE_ALIVE", "1")
    stack = QStackedWidget()
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())

    _configure_main_view_stack(stack, _DummyMapView(native=True))

    assert isinstance(stack.layout(), QStackedLayout)
    assert stack.layout().stackingMode() == QStackedLayout.StackAll


def test_configure_main_view_stack_leaves_python_backends_unchanged(qapp: QApplication) -> None:
    del qapp
    stack = QStackedWidget()
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())

    _configure_main_view_stack(stack, _DummyMapView(native=False))

    assert isinstance(stack.layout(), QStackedLayout)
    assert stack.layout().stackingMode() == QStackedLayout.StackOne


def test_detail_prepare_and_feature_publication_are_idempotent(
    qapp: QApplication,
    monkeypatch,
) -> None:
    del qapp

    class _FakeDetailPage(QWidget):
        def __init__(self, _main_window, parent=None, *, staged=False) -> None:
            super().__init__(parent)
            self.staged = staged
            self.complete_calls = 0

        def complete_feature(self) -> None:
            self.complete_calls += 1

    monkeypatch.setattr(
        "iPhoto.gui.ui.widgets.detail_page.DetailPageWidget",
        _FakeDetailPage,
    )
    main_window = QMainWindow()
    stack = QStackedWidget(main_window)
    gallery = QWidget(stack)
    stack.addWidget(gallery)
    stack.setCurrentWidget(gallery)
    main_window.setCentralWidget(stack)
    ui = Ui_MainWindow()
    ui._main_window = main_window
    ui.view_stack = stack
    published = []
    ui.featureCreated.connect(lambda feature, page: published.append((feature, page)))

    prepared = ui.prepare_detail_native_hierarchy()
    assert ui.prepare_detail_native_hierarchy() is prepared
    assert prepared.staged is True
    assert stack.currentWidget() is gallery

    def _complete_detail():
        prepared.complete_feature()
        return prepared

    monkeypatch.setattr(ui, "_create_detail_feature", _complete_detail)
    completed = ui.ensure_feature("detail")
    assert ui.ensure_feature("detail") is completed
    assert completed is prepared
    assert prepared.complete_calls == 1
    assert published == [("detail", completed)]

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for UI stack tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required for UI stack tests", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QWidget, QStackedLayout, QStackedWidget

from iPhoto.gui.ui.ui_main_window import _configure_main_view_stack


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

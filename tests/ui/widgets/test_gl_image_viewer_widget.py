"""Smoke tests for the QRhi-based GLImageViewer widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QRhiWidget

from iPhoto.gui.ui.widgets.gl_image_viewer.widget import GLImageViewer


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestGLImageViewerWidget:
    def test_uses_opengl_rhi_backend(self, qapp):
        viewer = GLImageViewer()
        assert viewer.api() == QRhiWidget.Api.OpenGL

    def test_marks_widget_as_opaque(self, qapp):
        viewer = GLImageViewer()
        assert viewer.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

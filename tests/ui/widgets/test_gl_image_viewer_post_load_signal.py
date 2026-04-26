from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

import os

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_gl_image_viewer_queues_one_post_load_view_transform(qapp) -> None:
    viewer = GLImageViewer()
    viewer._pending_post_load_view_transform = True

    spy = QSignalSpy(viewer.viewTransformChanged)
    viewer._schedule_post_load_view_transform()
    qapp.processEvents()

    assert len(spy) == 1
    assert viewer._pending_post_load_view_transform is False
    assert viewer._post_load_view_transform_scheduled is False

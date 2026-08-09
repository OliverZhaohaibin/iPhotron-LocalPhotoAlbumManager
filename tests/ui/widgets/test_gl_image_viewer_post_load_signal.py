from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

import os

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
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

    assert spy.count() == 1
    assert viewer._pending_post_load_view_transform is False
    assert viewer._post_load_view_transform_scheduled is False


def test_gl_image_viewer_maps_image_geometry_before_texture_upload(qapp) -> None:
    viewer = GLImageViewer()
    viewer.resize(420, 320)
    image = QImage(420, 320, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)

    viewer.set_image(image, {}, image_source="startup-still")
    qapp.processEvents()

    renderer = getattr(viewer, "_renderer", None)
    assert renderer is None or not renderer.has_texture()

    rect = viewer.image_rect_to_viewport(
        100,
        80,
        120,
        90,
        image_width=420,
        image_height=320,
    )
    assert rect.isEmpty() is False
    assert rect.left() == pytest.approx(100.0)
    assert rect.top() == pytest.approx(80.0)
    assert rect.width() == pytest.approx(120.0)
    assert rect.height() == pytest.approx(90.0)

    image_point = viewer.viewport_to_image(
        QPointF(210.0, 160.0),
        image_width=420,
        image_height=320,
    )
    assert image_point.x() == pytest.approx(210.0)
    assert image_point.y() == pytest.approx(160.0)

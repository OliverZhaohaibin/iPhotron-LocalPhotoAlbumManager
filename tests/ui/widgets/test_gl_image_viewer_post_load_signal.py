from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer


def test_gl_image_viewer_queues_one_post_load_view_transform(qtbot) -> None:
    viewer = GLImageViewer()
    qtbot.addWidget(viewer)
    viewer._pending_post_load_view_transform = True

    with qtbot.waitSignal(viewer.viewTransformChanged):
        viewer._schedule_post_load_view_transform()

    assert viewer._pending_post_load_view_transform is False
    assert viewer._post_load_view_transform_scheduled is False

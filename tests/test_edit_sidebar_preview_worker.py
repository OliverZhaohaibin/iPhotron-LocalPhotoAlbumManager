import os
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for sidebar preview tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test utilities unavailable", exc_type=ImportError)

from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.tasks import image_scaling
from iPhoto.gui.ui.tasks.edit_sidebar_preview_worker import EditSidebarPreviewWorker
from iPhoto.gui.ui.tasks.thumbnail_generator_worker import ThumbnailGeneratorWorker


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _solid_image(width: int = 640, height: int = 480, color: QColor | None = None) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color or QColor("#336699"))
    return image


def test_sidebar_preview_worker_scales_and_emits(qapp: QApplication) -> None:
    worker = EditSidebarPreviewWorker(
        _solid_image(),
        generation=7,
        target_height=160,
    )
    ready_spy = QSignalSpy(worker.signals.ready)
    finished_spy = QSignalSpy(worker.signals.finished)

    worker.run()

    assert ready_spy.count() == 1
    result, generation = ready_spy.at(0)
    assert generation == 7
    assert result.image.height() == 160
    assert result.image.format() == QImage.Format.Format_ARGB32
    # The worker should compute colour statistics for the preview, providing white balance data
    # for the Color adjustments even before the thumbnails finish rendering.
    assert hasattr(result.stats, "white_balance_gain")
    assert finished_spy.count() == 1


def test_macos_worker_scaling_uses_pillow_path(qapp: QApplication) -> None:
    pytest.importorskip("PIL.Image", reason="Pillow is required for the macOS worker scaling path")

    with (
        patch("iPhoto.gui.ui.tasks.image_scaling.sys.platform", "darwin"),
        patch(
            "iPhoto.gui.ui.tasks.image_scaling._qt_scaled_to_height",
            side_effect=AssertionError("Qt image transforms must not run in macOS workers"),
        ),
    ):
        scaled = image_scaling.scale_qimage_to_height_for_worker(_solid_image(), 160)

    assert scaled.height() == 160
    assert scaled.format() == QImage.Format.Format_ARGB32


def test_thumbnail_worker_avoids_qt_scaling_on_macos(qapp: QApplication) -> None:
    pytest.importorskip("PIL.Image", reason="Pillow is required for the macOS worker scaling path")
    worker = ThumbnailGeneratorWorker(
        _solid_image(),
        [0.0],
        lambda image, _value: image,
        target_height=96,
        generation_id=13,
    )
    ready_spy = QSignalSpy(worker.signals.thumbnail_ready)

    with (
        patch("iPhoto.gui.ui.tasks.image_scaling.sys.platform", "darwin"),
        patch(
            "iPhoto.gui.ui.tasks.image_scaling._qt_scaled_to_height",
            side_effect=AssertionError("Qt image transforms must not run in macOS workers"),
        ),
    ):
        worker.run()

    assert ready_spy.count() == 1
    _index, image, generation = ready_spy.at(0)
    assert generation == 13
    assert image.height() == 96
    assert image.format() == QImage.Format.Format_ARGB32

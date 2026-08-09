from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for GUI tests", exc_type=ImportError)

from PySide6.QtGui import QImage

from iPhoto.gui.ui.controllers.player_view_controller import _AdjustedImageWorker


def test_adjusted_image_worker_skips_color_stats_without_sidecar() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = False
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
    ) as compute_stats:
        worker = _AdjustedImageWorker(source, signals, edit_service)
        worker.run()

    edit_service.describe_adjustments.assert_not_called()
    compute_stats.assert_not_called()
    signals.completed.emit.assert_called_once_with(source, image, {})


def test_adjusted_image_worker_resolves_adjustments_when_sidecar_exists() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = True
    edit_service.describe_adjustments.return_value = Mock(
        resolved_adjustments={"Exposure": 0.5},
    )
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
        return_value="stats",
    ) as compute_stats:
        worker = _AdjustedImageWorker(source, signals, edit_service)
        worker.run()

    compute_stats.assert_called_once_with(image)
    edit_service.describe_adjustments.assert_called_once_with(source, color_stats="stats")
    signals.completed.emit.assert_called_once_with(source, image, {"Exposure": 0.5})

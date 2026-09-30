"""Unit tests for EditPipelineLoader."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage

from iPhoto.gui.ui.controllers.edit_pipeline_loader import EditPipelineLoader


@pytest.fixture
def pipeline_loader():
    return EditPipelineLoader()


def test_loader_has_no_independent_full_image_decode_api(pipeline_loader):
    assert not hasattr(pipeline_loader, "load_image")
    assert not hasattr(pipeline_loader, "imageLoaded")
    assert not hasattr(pipeline_loader, "imageLoadFailed")


@patch("iPhoto.gui.ui.controllers.edit_pipeline_loader.EditSidebarPreviewWorker")
def test_prepare_sidebar_preview_starts_worker(MockWorker, pipeline_loader):
    """Verify prepare_sidebar_preview starts a worker with correct params."""
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    target_height = 50

    pipeline_loader.prepare_sidebar_preview(image, target_height)

    MockWorker.assert_called_once()
    args, kwargs = MockWorker.call_args
    assert args[0] == image
    assert kwargs['generation'] == pipeline_loader._sidebar_preview_generation
    assert kwargs['target_height'] == target_height

    # Case where scaling is NOT needed
    image_small = QImage(50, 50, QImage.Format.Format_RGB32)
    target_height_large = 100
    # 50 > 100 * 1.5 (=150) is False

    MockWorker.reset_mock()
    pipeline_loader.prepare_sidebar_preview(image_small, target_height_large)
    _, kwargs = MockWorker.call_args
    assert kwargs['target_height'] == -1


@patch("iPhoto.gui.ui.controllers.edit_pipeline_loader.QThreadPool.globalInstance")
def test_prepare_sidebar_preview_inline_emits_without_threadpool(
    mock_global_pool,
    pipeline_loader,
):
    """Verify inline sidebar preview preparation avoids the thread pool."""
    image = QImage(320, 240, QImage.Format.Format_ARGB32)
    image.fill(0xFF336699)
    mock_slot = Mock()
    pipeline_loader.sidebarPreviewReady.connect(mock_slot)

    pipeline_loader.prepare_sidebar_preview_inline(image, 80)

    mock_global_pool.assert_not_called()
    mock_slot.assert_called_once()
    result = mock_slot.call_args.args[0]
    assert result.image.height() == 80
    assert result.image.format() == QImage.Format.Format_ARGB32
    assert hasattr(result.stats, "white_balance_gain")


def test_cancel_pending_operations(pipeline_loader):
    """Verify cancellation invalidates workers."""
    initial_gen = pipeline_loader._sidebar_preview_generation

    pipeline_loader.cancel_pending_operations()

    assert pipeline_loader._sidebar_preview_generation == initial_gen + 1


def test_stale_preview_ignored(pipeline_loader):
    """Verify results from stale generations are ignored."""
    mock_slot = Mock()
    pipeline_loader.sidebarPreviewReady.connect(mock_slot)

    current_gen = pipeline_loader._sidebar_preview_generation
    stale_gen = current_gen - 1

    result = Mock()
    pipeline_loader._handle_sidebar_preview_ready(result, stale_gen)

    mock_slot.assert_not_called()

    # Correct generation
    pipeline_loader._handle_sidebar_preview_ready(result, current_gen)
    mock_slot.assert_called_once_with(result)

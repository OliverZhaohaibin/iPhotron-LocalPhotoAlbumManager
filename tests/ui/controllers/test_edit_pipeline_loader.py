"""Unit tests for EditPipelineLoader."""

from __future__ import annotations

from unittest.mock import Mock, patch
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from src.iPhoto.gui.ui.controllers.edit_pipeline_loader import EditPipelineLoader


@pytest.fixture
def pipeline_loader():
    return EditPipelineLoader()


@patch("src.iPhoto.gui.ui.controllers.edit_pipeline_loader.ImageLoadWorker")
def test_load_image_starts_worker(MockWorker, pipeline_loader):
    """Verify load_image creates and starts an ImageLoadWorker."""
    path = Path("/path/to/image.jpg")
    mock_worker = MockWorker.return_value

    pipeline_loader.load_image(path)

    MockWorker.assert_called_once_with(path)
    # Check that signals are connected
    mock_worker.signals.imageLoaded.connect.assert_called()
    mock_worker.signals.loadFailed.connect.assert_called()
    # Ensure it's active
    assert pipeline_loader._active_image_worker == mock_worker


def test_image_loaded_signal_emission(pipeline_loader):
    """Verify imageLoaded signal is emitted when worker completes."""
    path = Path("/path/to/image.jpg")
    image = QImage()

    # Simulate a worker being active
    mock_worker = Mock()
    pipeline_loader._active_image_worker = mock_worker

    # Create a mock slot
    mock_slot = Mock()
    pipeline_loader.imageLoaded.connect(mock_slot)

    # Trigger the internal handler
    pipeline_loader._on_image_loaded(path, image)

    mock_slot.assert_called_once_with(path, image)
    # The active worker is NOT cleared to avoid race conditions
    assert pipeline_loader._active_image_worker is not None


def test_image_load_failed_signal_emission(pipeline_loader):
    """Verify imageLoadFailed signal is emitted on failure."""
    path = Path("/path/to/bad_image.jpg")
    error_msg = "Corrupt file"

    mock_worker = Mock()
    pipeline_loader._active_image_worker = mock_worker

    mock_slot = Mock()
    pipeline_loader.imageLoadFailed.connect(mock_slot)

    pipeline_loader._on_image_load_failed(path, error_msg)

    mock_slot.assert_called_once_with(path, error_msg)
    # The active worker is NOT cleared to avoid race conditions
    assert pipeline_loader._active_image_worker is not None


@patch("src.iPhoto.gui.ui.controllers.edit_pipeline_loader.EditSidebarPreviewWorker")
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


def test_cancel_pending_operations(pipeline_loader):
    """Verify cancellation invalidates workers."""
    pipeline_loader._active_image_worker = Mock()
    initial_gen = pipeline_loader._sidebar_preview_generation

    pipeline_loader.cancel_pending_operations()

    assert pipeline_loader._active_image_worker is None
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

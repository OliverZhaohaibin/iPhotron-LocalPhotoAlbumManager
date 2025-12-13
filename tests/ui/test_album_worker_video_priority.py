"""Test regarding priority of image selection in AlbumDataWorker.

Ensures that if the index contains both videos and images, the worker prioritizes
finding the first image for the cover, rather than just taking the first asset (which might be a video).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject

# Import worker from the module
from src.iPhoto.gui.ui.widgets.albums_dashboard import AlbumDataWorker, DashboardLoaderSignals

@pytest.fixture
def mock_signals():
    """Mock signals object."""
    signals = MagicMock(spec=DashboardLoaderSignals)
    signals.albumReady = MagicMock()
    signals.albumReady.emit = MagicMock()
    return signals

@pytest.fixture
def mock_node(tmp_path):
    """Mock AlbumNode with a path."""
    node = MagicMock()
    node.path = tmp_path / "test_album"
    node.path.mkdir()
    return node

def test_worker_prioritizes_image_over_video(tmp_path, mock_node, mock_signals):
    """Test that worker selects the first image from index even if a video is first."""

    # 1. Setup Filesystem
    # Create the files physically (so fallback checks would pass if reached,
    # and cover_path checks pass).
    (mock_node.path / "video.mov").touch()
    (mock_node.path / "image.jpg").touch()

    # 2. Setup IndexStore Mock
    # Order matters: video first.
    # If the worker just takes index[0], it gets video.mov
    # If the worker searches for image, it gets image.jpg
    index_data = [
        {"rel": "video.mov", "mime": "video/quicktime"},
        {"rel": "image.jpg", "mime": "image/jpeg"}
    ]

    worker = AlbumDataWorker(mock_node, mock_signals, generation=1)

    with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.Album.open") as mock_open:
        mock_album = MagicMock()
        mock_album.manifest = {} # No manual cover
        mock_open.return_value = mock_album

        with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.IndexStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.read_all.return_value = index_data
            mock_store_cls.return_value = mock_store

            # Execute synchronously
            worker.run()

    # 3. Assertions
    mock_signals.albumReady.emit.assert_called_once()
    args = mock_signals.albumReady.emit.call_args[0]

    # args: (node, count, cover_path, root, generation)
    count = args[1]
    cover_path = args[2]

    # Count should be 2 (video + image)
    assert count == 2

    # Cover path should be the IMAGE, not the video
    expected_cover = mock_node.path / "image.jpg"

    # This assertion is expected to FAIL with current code (which picks video.mov)
    assert cover_path == expected_cover, f"Expected {expected_cover}, got {cover_path}"

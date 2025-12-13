"""Tests for AlbumDataWorker logic, including fallback cover detection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure we can import the worker even if it's not exported
# We rely on src being in pythonpath which is standard for this repo's tests
from src.iPhoto.gui.ui.widgets.albums_dashboard import AlbumDataWorker, DashboardLoaderSignals

@pytest.fixture
def mock_signals():
    """Mock signals object to capture emitted values."""
    signals = MagicMock(spec=DashboardLoaderSignals)
    # We need to mock albumReady.emit
    signals.albumReady = MagicMock()
    signals.albumReady.emit = MagicMock()
    return signals

@pytest.fixture
def mock_node():
    """Mock AlbumNode."""
    node = MagicMock()
    return node

def test_worker_fallback_empty_folder(tmp_path, mock_node, mock_signals):
    """Test worker behavior when folder is empty (no cover, no index)."""
    # Setup
    album_path = tmp_path / "empty_album"
    album_path.mkdir()
    mock_node.path = album_path

    # Run worker
    worker = AlbumDataWorker(mock_node, mock_signals, generation=1)

    # Mock Album.open to return empty manifest
    with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.Album.open") as mock_open:
        mock_album = MagicMock()
        mock_album.manifest = {}
        mock_open.return_value = mock_album

        # Mock IndexStore to be empty
        with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.IndexStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.read_all.return_value = []
            mock_store_cls.return_value = mock_store

            worker.run()

    # Verify signal emission
    # albumReady(node, count, cover_path, root, generation)
    mock_signals.albumReady.emit.assert_called_once()
    args = mock_signals.albumReady.emit.call_args[0]

    # Check count is 0
    assert args[1] == 0
    # Check cover_path is None
    assert args[2] is None

def test_worker_fallback_with_image(tmp_path, mock_node, mock_signals):
    """Test worker fallback finds image in folder when index/manifest are empty."""
    # Setup
    album_path = tmp_path / "image_album"
    album_path.mkdir()
    mock_node.path = album_path

    # Create a fallback image
    (album_path / "fallback.jpg").touch()

    # Run worker
    worker = AlbumDataWorker(mock_node, mock_signals, generation=1)

    # Mock Album.open to return empty manifest
    with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.Album.open") as mock_open:
        mock_album = MagicMock()
        mock_album.manifest = {}
        mock_open.return_value = mock_album

        # Mock IndexStore to be empty
        with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.IndexStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.read_all.return_value = []
            mock_store_cls.return_value = mock_store

            worker.run()

    # Verify signal emission
    mock_signals.albumReady.emit.assert_called_once()
    args = mock_signals.albumReady.emit.call_args[0]

    # cover_path should be the fallback image
    assert args[2] == album_path / "fallback.jpg"

    # count should be 1
    assert args[1] == 1

def test_worker_fallback_ignores_non_images(tmp_path, mock_node, mock_signals):
    """Test worker ignores non-image files during fallback scan."""
    # Setup
    album_path = tmp_path / "mixed_album"
    album_path.mkdir()
    mock_node.path = album_path

    # Create non-image files
    (album_path / "doc.txt").touch()
    (album_path / "data.json").touch()

    # Create image file (ensure name sorts after if implementation iterates alphabetically,
    # but iteration order depends on OS. safe to rely on valid extension filtering)
    (album_path / "z_image.png").touch()

    worker = AlbumDataWorker(mock_node, mock_signals, generation=1)

    with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.Album.open") as mock_open:
        mock_album = MagicMock()
        mock_album.manifest = {}
        mock_open.return_value = mock_album

        with patch("src.iPhoto.gui.ui.widgets.albums_dashboard.IndexStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.read_all.return_value = []
            mock_store_cls.return_value = mock_store

            worker.run()

    mock_signals.albumReady.emit.assert_called_once()
    args = mock_signals.albumReady.emit.call_args[0]

    assert args[2] == album_path / "z_image.png"
    assert args[1] == 1

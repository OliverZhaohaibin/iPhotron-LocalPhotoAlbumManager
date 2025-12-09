"""Tests for parallel file discovery and scanning."""

import queue
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.iPhoto.io.scanner import FileDiscoverer, scan_album


@pytest.fixture
def temp_album_structure(tmp_path):
    """Create a temporary directory structure with dummy files."""
    root = tmp_path / "album"
    root.mkdir()

    # Create 50 image files
    for i in range(50):
        (root / f"img_{i}.jpg").touch()

    return root


class TestFileDiscoverer:
    def test_discovery_completes(self, temp_album_structure):
        """Test that discoverer finds all files."""
        q = queue.Queue()
        discoverer = FileDiscoverer(temp_album_structure, ["*"], [], q)
        discoverer.start()

        items = []
        while True:
            item = q.get()
            if item is None:
                break
            items.append(item)

        discoverer.join()

        assert len(items) == 50
        assert discoverer.total_found == 50

    def test_stop_mechanism(self, temp_album_structure):
        """Test that discoverer stops when requested."""
        q = queue.Queue()
        discoverer = FileDiscoverer(temp_album_structure, ["*"], [], q)

        # We need to make rglob slow or endless to test stopping mid-way
        # or just call stop immediately.

        with patch.object(Path, "rglob") as mock_rglob:
            # Infinite generator
            def infinite_files():
                i = 0
                while True:
                    yield temp_album_structure / f"img_{i}.jpg"
                    i += 1
                    time.sleep(0.01)

            mock_rglob.return_value = infinite_files()

            discoverer.start()
            time.sleep(0.1)  # Let it find some files
            discoverer.stop()

            # Consume queue until None
            count = 0
            while True:
                try:
                    item = q.get(timeout=1.0)
                    if item is None:
                        break
                    count += 1
                except queue.Empty:
                    # Should finish by putting None
                    pytest.fail("Discoverer did not signal finish after stop()")

            discoverer.join(timeout=1.0)
            assert not discoverer.is_alive()
            assert count > 0  # Should have found some
            # But not infinite

    def test_total_found_accuracy(self, temp_album_structure):
        """Test total_found matches queued items exactly."""
        q = queue.Queue()
        discoverer = FileDiscoverer(temp_album_structure, ["*"], [], q)
        discoverer.start()
        discoverer.join()

        # Drain queue
        items = []
        while True:
            item = q.get()
            if item is None:
                break
            items.append(item)

        assert discoverer.total_found == len(items)


class TestScanAlbumProgress:
    def test_progress_callback(self, temp_album_structure):
        """Verify progress callback is invoked with increasing values."""

        updates = []
        def callback(processed, total):
            updates.append((processed, total))

        # Create mock metadata payloads
        def mock_metadata(paths):
            return [{"SourceFile": str(p), "MIMEType": "image/jpeg"} for p in paths]

        # Mock get_metadata_batch to return realistic data
        with patch("src.iPhoto.io.scanner.get_metadata_batch", side_effect=mock_metadata):
            # Consume the generator
            for _ in scan_album(temp_album_structure, ["*"], [], progress_callback=callback):
                pass

        assert len(updates) > 0

        # Check that processed count increases
        processed_counts = [u[0] for u in updates]
        assert processed_counts[-1] == 50
        assert processed_counts == sorted(processed_counts)

        # Check that total matches at the end
        total_counts = [u[1] for u in updates]
        assert total_counts[-1] == 50

    def test_progress_throttling(self, temp_album_structure):
        """Verify that progress updates are throttled (not every single file)."""

        updates = []
        def callback(processed, total):
            updates.append(processed)

        # Create mock metadata payloads
        def mock_metadata(paths):
            return [{"SourceFile": str(p), "MIMEType": "image/jpeg"} for p in paths]

        with patch("src.iPhoto.io.scanner.get_metadata_batch", side_effect=mock_metadata):
            for _ in scan_album(temp_album_structure, ["*"], [], progress_callback=callback):
                pass

        # With 50 files and throttling every 25 files (as in the implementation),
        # we expect updates at 0, 25, and 50 (start, mid, and end), so at most 3 updates.
        # If the throttling interval changes, update the expected_max_updates accordingly.
        throttling_interval = 25
        total_files = 50
        expected_max_updates = (total_files // throttling_interval) + 2  # +1 for initial, +1 for final
        assert len(updates) <= expected_max_updates, (
            f"Expected at most {expected_max_updates} updates, got {len(updates)}"
        )
        assert 0 in updates
        assert 50 in updates

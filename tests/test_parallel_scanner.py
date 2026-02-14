"""Tests for ParallelScanner — parallel file scanning with ThreadPoolExecutor."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iPhoto.application.services.parallel_scanner import ParallelScanner, ScanResult
from iPhoto.domain.models.core import Asset, MediaType
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanProgressEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_album(tmp_path: Path) -> Path:
    """Create a temporary album directory with a handful of media files."""
    for name in ("a.jpg", "b.png", "c.heic", "d.mov"):
        (tmp_path / name).write_text("fake")
    # Non-media file – should be ignored
    (tmp_path / "readme.txt").write_text("not media")
    # Hidden directory – should be skipped
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.jpg").write_text("hidden")
    return tmp_path


@pytest.fixture()
def nested_album(tmp_path: Path) -> Path:
    """Album with nested sub-directories."""
    (tmp_path / "photo.jpg").write_text("root")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.png").write_text("nested")
    return tmp_path


def _make_asset(path: Path) -> Asset:
    """Helper that produces a minimal Asset from a path."""
    return Asset(
        id=path.name,
        album_id="test",
        path=path,
        media_type=MediaType.IMAGE,
        size_bytes=0,
    )


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------

class TestScanResult:
    def test_empty_result(self):
        r = ScanResult()
        assert r.assets == []
        assert r.errors == []
        assert r.total_processed == 0

    def test_total_processed(self):
        r = ScanResult(
            assets=[_make_asset(Path("x.jpg"))],
            errors=[(Path("y.jpg"), "err")],
        )
        assert r.total_processed == 2


# ---------------------------------------------------------------------------
# ParallelScanner._discover_files
# ---------------------------------------------------------------------------

class TestDiscoverFiles:
    def test_discovers_media_files(self, tmp_album: Path):
        scanner = ParallelScanner()
        found = sorted(scanner._discover_files(tmp_album), key=lambda p: p.name)
        names = [p.name for p in found]
        assert "a.jpg" in names
        assert "b.png" in names
        assert "c.heic" in names
        assert "d.mov" in names

    def test_skips_non_media(self, tmp_album: Path):
        scanner = ParallelScanner()
        found = list(scanner._discover_files(tmp_album))
        names = [p.name for p in found]
        assert "readme.txt" not in names

    def test_skips_hidden_directories(self, tmp_album: Path):
        scanner = ParallelScanner()
        found = list(scanner._discover_files(tmp_album))
        names = [p.name for p in found]
        assert "secret.jpg" not in names

    def test_recurses_into_subdirectories(self, nested_album: Path):
        scanner = ParallelScanner()
        found = list(scanner._discover_files(nested_album))
        names = [p.name for p in found]
        assert "photo.jpg" in names
        assert "deep.png" in names


class TestIsSupported:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("photo.jpg", True),
            ("photo.JPEG", True),
            ("video.mov", True),
            ("readme.txt", False),
            ("noext", False),
            (".hidden", False),
        ],
    )
    def test_extension_check(self, filename: str, expected: bool):
        assert ParallelScanner._is_supported(filename) is expected


# ---------------------------------------------------------------------------
# ParallelScanner.scan
# ---------------------------------------------------------------------------

class TestScan:
    def test_scan_with_custom_fn(self, tmp_album: Path):
        scanner = ParallelScanner(scan_file_fn=_make_asset)
        result = scanner.scan(tmp_album)
        assert len(result.assets) == 4
        assert result.errors == []

    def test_scan_captures_errors(self, tmp_album: Path):
        def _failing(path: Path):
            raise RuntimeError("boom")

        scanner = ParallelScanner(scan_file_fn=_failing)
        result = scanner.scan(tmp_album)
        assert result.assets == []
        assert len(result.errors) == 4
        assert all(msg == "boom" for _, msg in result.errors)

    def test_scan_empty_directory(self, tmp_path: Path):
        scanner = ParallelScanner(scan_file_fn=_make_asset)
        result = scanner.scan(tmp_path)
        assert result.assets == []
        assert result.errors == []

    def test_scan_respects_max_workers(self, tmp_album: Path):
        scanner = ParallelScanner(max_workers=1, scan_file_fn=_make_asset)
        result = scanner.scan(tmp_album)
        assert len(result.assets) == 4

    def test_scan_publishes_progress_events(self, tmp_album: Path):
        bus = EventBus()
        events_received: list[ScanProgressEvent] = []
        bus.subscribe(ScanProgressEvent, events_received.append)

        scanner = ParallelScanner(
            batch_size=2,
            event_bus=bus,
            scan_file_fn=_make_asset,
        )
        result = scanner.scan(tmp_album)

        # At least the final progress event should be published
        assert len(events_received) >= 1
        last_evt = events_received[-1]
        assert last_evt.processed == last_evt.total

    def test_scan_no_events_without_bus(self, tmp_album: Path):
        scanner = ParallelScanner(scan_file_fn=_make_asset)
        result = scanner.scan(tmp_album)
        # Should not raise; just ensure we have results
        assert len(result.assets) == 4

    def test_scan_mixed_success_and_failure(self, tmp_album: Path):
        call_count = 0

        def _sometimes_fail(path: Path):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ValueError("fail")
            return _make_asset(path)

        scanner = ParallelScanner(scan_file_fn=_sometimes_fail)
        result = scanner.scan(tmp_album)
        assert len(result.assets) + len(result.errors) == 4

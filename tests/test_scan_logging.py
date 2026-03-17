from __future__ import annotations

import logging
from pathlib import Path

from iPhoto import _native
from iPhoto.core import pairing
from iPhoto.io import scanner_adapter


class _StubMetadataProvider:
    def get_metadata_batch(self, paths):
        return [{"SourceFile": path.as_posix()} for path in paths]

    def normalize_metadata(self, root: Path, file_path: Path, raw_metadata):
        return {
            "rel": file_path.relative_to(root).as_posix(),
            "bytes": file_path.stat().st_size,
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1704067200000000,
            "id": "as_test",
            "mime": "image/jpeg",
            "media_type": 0,
        }


class _StubThumbnailGenerator:
    def generate_micro_thumbnail(self, _path: Path):
        return None


def test_process_media_paths_logs_runtime_mode(caplog, monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "photo.jpg"
    file_path.write_bytes(b"content")

    monkeypatch.setattr(scanner_adapter, "_metadata_provider", _StubMetadataProvider())
    monkeypatch.setattr(scanner_adapter, "_thumbnail_generator", _StubThumbnailGenerator())
    monkeypatch.setattr(
        _native,
        "get_runtime_status",
        lambda: _native.NativeStatus(
            runtime_mode=_native.RUNTIME_MODE_PYTHON_FALLBACK,
            available_features=(),
            failure_reason="native library missing",
        ),
    )
    monkeypatch.setattr(_native, "runtime_mode_label", lambda: "Python fallback")

    caplog.set_level(logging.INFO, logger="iPhoto")

    rows = list(scanner_adapter.process_media_paths(tmp_path, [file_path], []))

    assert len(rows) == 1
    assert "Scan backend: Python fallback" in caplog.text
    assert "prepare/hash finished in" in caplog.text
    assert "Scan finished (Python fallback) in" in caplog.text


def test_scan_album_logs_runtime_mode_once(caplog, monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "photo.jpg"
    file_path.write_bytes(b"content")

    monkeypatch.setattr(scanner_adapter, "_metadata_provider", _StubMetadataProvider())
    monkeypatch.setattr(scanner_adapter, "_thumbnail_generator", _StubThumbnailGenerator())
    monkeypatch.setattr(
        _native,
        "get_runtime_status",
        lambda: _native.NativeStatus(
            runtime_mode=_native.RUNTIME_MODE_C_EXTENSION,
            available_features=_native._ALL_FEATURES,
            failure_reason=None,
        ),
    )
    monkeypatch.setattr(_native, "runtime_mode_label", lambda: "C extension")

    class _StubDiscoverer:
        def __init__(self, root: Path, queue_obj, include, exclude):
            self._root = root
            self._queue = queue_obj
            self.total_found = 1
            self.total_chunks = 1
            self.elapsed_s = 0.01

        def start(self) -> None:
            self._queue.put([self._root / "photo.jpg"])
            self._queue.put(None)

        def stop(self) -> None:
            return None

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            return None

    monkeypatch.setattr(scanner_adapter, "FileDiscoveryThread", _StubDiscoverer)
    caplog.set_level(logging.INFO, logger="iPhoto")

    rows = list(scanner_adapter.scan_album(tmp_path, ["*.jpg"], []))

    assert len(rows) == 1
    assert caplog.text.count("Scan backend: C extension") == 1
    assert "discovery finished in" in caplog.text
    assert "prepare/hash finished in" in caplog.text
    assert "Scan finished (C extension) in" in caplog.text


def test_pair_live_logs_stage_and_falls_back(monkeypatch, caplog) -> None:
    rows = [
        {
            "rel": "IMG_0001.HEIC",
            "mime": "image/heic",
            "dt": "2024-01-01T12:00:00Z",
            "content_id": "CID1",
        },
        {
            "rel": "IMG_0001.MOV",
            "mime": "video/quicktime",
            "dt": "2024-01-01T12:00:00Z",
            "content_id": "CID1",
            "dur": 1.5,
        },
    ]
    monkeypatch.setattr(_native, "pair_rows", lambda _rows, **_kwargs: None)
    caplog.set_level(logging.INFO, logger="iPhoto")

    groups = pairing.pair_live(rows)

    assert len(groups) == 1
    assert groups[0].motion == "IMG_0001.MOV"
    assert "pair_live finished in" in caplog.text

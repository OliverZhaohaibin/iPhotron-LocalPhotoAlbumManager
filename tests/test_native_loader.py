from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from iPhoto import _native


@pytest.fixture(autouse=True)
def _reset_native_state() -> None:
    _native._reset_state_for_tests()
    yield
    _native._reset_state_for_tests()


def test_native_loader_reports_missing_library(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_native, "_library_path", lambda: tmp_path / "_scan_utils.dll")

    status = _native.get_runtime_status()

    assert status.runtime_mode == _native.RUNTIME_MODE_PYTHON_FALLBACK
    assert status.available_features == ()
    assert status.failure_reason is not None
    assert "missing" in status.failure_reason


def test_native_loader_reports_load_failure(monkeypatch, tmp_path: Path) -> None:
    library_path = tmp_path / "_scan_utils.dll"
    library_path.write_bytes(b"not-a-real-library")

    monkeypatch.setattr(_native, "_library_path", lambda: library_path)
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda _path: (_ for _ in ()).throw(OSError("bad image")),
    )

    status = _native.get_runtime_status()

    assert status.runtime_mode == _native.RUNTIME_MODE_PYTHON_FALLBACK
    assert status.failure_reason is not None
    assert "load failed" in status.failure_reason


def test_native_loader_reports_missing_symbols(monkeypatch, tmp_path: Path) -> None:
    library_path = tmp_path / "_scan_utils.dll"
    library_path.write_bytes(b"placeholder")

    class IncompleteLibrary:
        pass

    monkeypatch.setattr(_native, "_library_path", lambda: library_path)
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: IncompleteLibrary())

    status = _native.get_runtime_status()

    assert status.runtime_mode == _native.RUNTIME_MODE_PYTHON_FALLBACK
    assert status.failure_reason is not None
    assert "symbol binding failed" in status.failure_reason

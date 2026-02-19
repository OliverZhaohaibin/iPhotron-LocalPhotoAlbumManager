"""Tests for the parallel thumbnail extraction in demo/video-demo.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import the module-level function under test
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "video_demo",
    os.path.join(os.path.dirname(__file__), "..", "demo", "video-demo.py"),
)
video_demo = importlib.util.module_from_spec(_spec)

# Prevent PySide6 imports from failing in headless CI
import sys
for mod_name in [
    "PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
]:
    sys.modules.setdefault(mod_name, MagicMock())

_spec.loader.exec_module(video_demo)

_extract_segment = video_demo._extract_segment


class TestExtractSegment:
    """Unit tests for the _extract_segment worker function."""

    def test_returns_file_list_on_success(self, tmp_path: Path) -> None:
        """When ffmpeg succeeds and produces files, they are returned sorted."""
        out_dir = str(tmp_path)
        seg_index = 0

        # Pre-create fake thumbnail files that ffmpeg would produce
        for i in range(1, 4):
            (tmp_path / f"seg{seg_index:03d}_{i:04d}.jpg").write_bytes(b"\xff")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 5.0, 1.0, 42, out_dir, seg_index)
            result = _extract_segment(args)

        assert len(result) == 3
        assert all(p.endswith(".jpg") for p in result)
        # Verify sorted order
        basenames = [os.path.basename(p) for p in result]
        assert basenames == sorted(basenames)

    def test_returns_empty_on_ffmpeg_failure(self, tmp_path: Path) -> None:
        """When ffmpeg raises an exception, return empty list."""
        out_dir = str(tmp_path)

        with patch.object(subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            args = ("video.mp4", 0.0, 5.0, 1.0, 42, out_dir, 0)
            result = _extract_segment(args)

        assert result == []

    def test_only_returns_own_segment_files(self, tmp_path: Path) -> None:
        """Each segment only collects its own files, not files from other segments."""
        out_dir = str(tmp_path)

        # Create files for segment 0 and segment 1
        (tmp_path / "seg000_0001.jpg").write_bytes(b"\xff")
        (tmp_path / "seg001_0001.jpg").write_bytes(b"\xff")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            # Extract segment 0 only
            args = ("video.mp4", 0.0, 5.0, 1.0, 42, out_dir, 0)
            result = _extract_segment(args)

        assert len(result) == 1
        assert "seg000_" in os.path.basename(result[0])

    def test_ffmpeg_command_uses_ss_and_t(self, tmp_path: Path) -> None:
        """Verify that ffmpeg is called with -ss (seek) and -t (duration) flags."""
        out_dir = str(tmp_path)
        captured_cmd = []

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 10.5, 5.0, 2.0, 42, out_dir, 1)
            _extract_segment(args)

            captured_cmd = mock_popen.call_args[0][0]

        assert "-ss" in captured_cmd
        assert "-t" in captured_cmd
        ss_idx = captured_cmd.index("-ss")
        t_idx = captured_cmd.index("-t")
        assert "10.5000" in captured_cmd[ss_idx + 1]
        assert "5.0000" in captured_cmd[t_idx + 1]

    def test_nice_is_called_on_unix(self, tmp_path: Path) -> None:
        """On Unix, os.nice(10) is called to lower worker priority."""
        out_dir = str(tmp_path)

        with patch.object(subprocess, "Popen") as mock_popen, \
             patch("os.nice") as mock_nice:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 5.0, 1.0, 42, out_dir, 0)
            _extract_segment(args)

            mock_nice.assert_called_once_with(10)

    def test_windows_low_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, BELOW_NORMAL_PRIORITY_CLASS is passed to Popen."""
        out_dir = str(tmp_path)
        monkeypatch.setattr(os, "name", "nt")

        # Mock STARTUPINFO for non-Windows platforms
        mock_startupinfo = MagicMock()
        mock_startupinfo.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_startupinfo, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 5.0, 1.0, 42, out_dir, 0)
            _extract_segment(args)

            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs.get("creationflags") == 0x00004000

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

_extract_single_frame = video_demo._extract_single_frame
_get_video_info = video_demo._get_video_info


class TestExtractSingleFrame:
    """Unit tests for the _extract_single_frame worker function."""

    def test_returns_path_on_success(self, tmp_path: Path) -> None:
        """When ffmpeg succeeds and produces a file, its path is returned."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            # Pre-create the output file to simulate ffmpeg writing it
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 5.0, 42, out_path)
            result = _extract_single_frame(args)

        assert result == out_path

    def test_returns_none_on_ffmpeg_failure(self, tmp_path: Path) -> None:
        """When ffmpeg raises an exception, return None."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            args = ("video.mp4", 0.0, 42, out_path)
            result = _extract_single_frame(args)

        assert result is None

    def test_returns_none_on_empty_output(self, tmp_path: Path) -> None:
        """When ffmpeg produces an empty file, return None."""
        out_path = str(tmp_path / "thumb_0000.jpg")
        Path(out_path).write_bytes(b"")  # empty file

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 42, out_path)
            result = _extract_single_frame(args)

        assert result is None

    def test_ffmpeg_command_uses_ss_and_frames(self, tmp_path: Path) -> None:
        """Verify ffmpeg is called with -ss (fast-seek) and -frames:v 1."""
        out_path = str(tmp_path / "thumb_0001.jpg")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 10.5, 42, out_path)
            _extract_single_frame(args)

            captured_cmd = mock_popen.call_args[0][0]

        assert "-ss" in captured_cmd
        assert "-frames:v" in captured_cmd
        ss_idx = captured_cmd.index("-ss")
        assert "10.5000" in captured_cmd[ss_idx + 1]
        frames_idx = captured_cmd.index("-frames:v")
        assert captured_cmd[frames_idx + 1] == "1"

    def test_unix_preexec_fn_sets_nice(self, tmp_path: Path) -> None:
        """On Unix, preexec_fn is passed to Popen to nice the ffmpeg child."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 42, out_path)
            _extract_single_frame(args)

            call_kwargs = mock_popen.call_args[1]
            # preexec_fn should be set (it calls os.nice(10))
            assert "preexec_fn" in call_kwargs
            # Actually invoke it to verify it calls os.nice(10)
            with patch("os.nice") as mock_nice:
                call_kwargs["preexec_fn"]()
                mock_nice.assert_called_once_with(10)

    def test_windows_low_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, BELOW_NORMAL_PRIORITY_CLASS is passed to Popen."""
        out_path = str(tmp_path / "thumb_0000.jpg")
        monkeypatch.setattr(os, "name", "nt")

        mock_startupinfo = MagicMock()
        mock_startupinfo.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_startupinfo, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock

            args = ("video.mp4", 0.0, 42, out_path)
            _extract_single_frame(args)

            call_kwargs = mock_popen.call_args[1]
            # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            assert call_kwargs.get("creationflags") == 0x00004000


class TestGetVideoInfo:
    """Unit tests for _get_video_info."""

    def test_returns_width_height_duration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful probe returns (width, height, duration)."""
        fake_output = '{"streams":[{"width":1920,"height":1080,"duration":"30.0"}]}'
        fake_result = subprocess.CompletedProcess([], 0, stdout=fake_output, stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        w, h, d = _get_video_info("test.mp4")
        assert (w, h, d) == (1920, 1080, 30.0)

    def test_returns_zeros_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On probe failure, returns (0, 0, 0)."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )

        w, h, d = _get_video_info("nonexistent.mp4")
        assert (w, h, d) == (0, 0, 0)

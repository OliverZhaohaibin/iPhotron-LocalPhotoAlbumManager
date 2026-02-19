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
_detect_hwaccel = video_demo._detect_hwaccel
_build_hwaccel_output_format = video_demo._build_hwaccel_output_format
_run_pipe_cmd = video_demo._run_pipe_cmd
_try_extract_pipe_hwaccel = video_demo._try_extract_pipe_hwaccel
_try_extract_pipe_sw = video_demo._try_extract_pipe_sw
_extract_frame_pipe = video_demo._extract_frame_pipe
_build_popen_priority_kwargs = video_demo._build_popen_priority_kwargs


@pytest.fixture(autouse=True)
def _reset_hwaccel_cache():
    """Reset the global hwaccel cache before each test."""
    video_demo._hwaccel_cache = None
    yield
    video_demo._hwaccel_cache = None


class TestDetectHwaccel:
    """Tests for _detect_hwaccel()."""

    def test_detects_d3d11va_with_scale_d3d11(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ffmpeg reports d3d11va + scale_d3d11 filter."""
        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="d3d11va\ncuda\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale_d3d11\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'd3d11va'
        assert hw['scale_filter'] == 'scale_d3d11'
        assert 'hwdownload' in hw['download_filter']

    def test_detects_d3d11va_without_gpu_scale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """d3d11va decode but no GPU scale filter → CPU scale after hwdownload."""
        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="d3d11va\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'd3d11va'
        assert hw['scale_filter'] == 'scale'
        assert 'hwdownload' in hw['download_filter']

    def test_detects_videotoolbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """macOS videotoolbox detection."""
        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="videotoolbox\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale_vt\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'videotoolbox'
        assert hw['scale_filter'] == 'scale_vt'

    def test_detects_vaapi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux VAAPI detection."""
        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="vaapi\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale_vaapi\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'vaapi'
        assert hw['scale_filter'] == 'scale_vaapi'

    def test_no_hwaccel_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no hardware acceleration is available, returns None hwaccel."""
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] is None
        assert hw['scale_filter'] == 'scale'

    def test_detection_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call returns cached result without running subprocess."""
        call_count = 0

        def fake_run(cmd, **kw):
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(cmd, 0, stdout="d3d11va\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _detect_hwaccel()
        first_count = call_count
        _detect_hwaccel()
        assert call_count == first_count  # no additional subprocess calls

    def test_handles_ffmpeg_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ffmpeg is not installed, gracefully returns no hwaccel."""
        monkeypatch.setattr(
            subprocess, "run", MagicMock(side_effect=FileNotFoundError("ffmpeg")),
        )
        hw = _detect_hwaccel()
        assert hw['hwaccel'] is None


class TestBuildHwaccelOutputFormat:
    """Tests for _build_hwaccel_output_format()."""

    def test_d3d11va_maps_to_d3d11(self) -> None:
        assert _build_hwaccel_output_format('d3d11va') == 'd3d11'

    def test_videotoolbox_maps_to_vld(self) -> None:
        assert _build_hwaccel_output_format('videotoolbox') == 'videotoolbox_vld'

    def test_vaapi_maps_to_vaapi(self) -> None:
        assert _build_hwaccel_output_format('vaapi') == 'vaapi'

    def test_unknown_returns_itself(self) -> None:
        assert _build_hwaccel_output_format('cuda') == 'cuda'


class TestRunPipeCmd:
    """Tests for _run_pipe_cmd()."""

    def test_returns_tuple_on_correct_size(self) -> None:
        """When stdout has exactly W*H*4 bytes, returns (w, h, bytes)."""
        w, h = 4, 2
        expected = b'\x00' * (w * h * 4)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=expected, stderr=b"",
            )
            result = _run_pipe_cmd(['ffmpeg', 'test'], w, h)

        assert result is not None
        assert result == (w, h, expected)

    def test_returns_none_on_wrong_size(self) -> None:
        """When stdout size doesn't match, returns None."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=b"short", stderr=b"",
            )
            result = _run_pipe_cmd(['ffmpeg', 'test'], 10, 10)

        assert result is None

    def test_returns_none_on_nonzero_exit(self) -> None:
        """When ffmpeg exits with error, returns None."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 1, stdout=b"", stderr=b"error",
            )
            result = _run_pipe_cmd(['ffmpeg', 'test'], 4, 2)

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        """When subprocess raises, returns None."""
        with patch.object(subprocess, "run", side_effect=FileNotFoundError("ffmpeg")):
            result = _run_pipe_cmd(['ffmpeg', 'test'], 4, 2)

        assert result is None


class TestTryExtractPipeHwaccel:
    """Tests for _try_extract_pipe_hwaccel()."""

    def test_builds_gpu_scale_command(self) -> None:
        """When GPU scale filter is available, builds correct -vf chain."""
        hw = {
            'hwaccel': 'd3d11va',
            'scale_filter': 'scale_d3d11',
            'download_filter': 'hwdownload,format=bgra',
            'pix_fmt': 'bgra',
        }
        w, h = 160, 90
        expected_buf = b'\x00' * (w * h * 4)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=expected_buf, stderr=b"",
            )
            result = _try_extract_pipe_hwaccel("video.mp4", 5.0, w, h, hw)

            cmd = mock_run.call_args[0][0]

        assert result is not None
        assert result == (w, h, expected_buf)
        assert '-hwaccel' in cmd
        assert 'd3d11va' in cmd
        assert '-hwaccel_output_format' in cmd
        assert 'd3d11' in cmd
        # Check the -vf filter chain
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'scale_d3d11=160:90' in vf
        assert 'hwdownload' in vf
        assert '-f' in cmd
        assert 'rawvideo' in cmd
        assert 'pipe:1' in cmd

    def test_hwdownload_then_cpu_scale(self) -> None:
        """When no GPU scale filter, hwdownload first then CPU scale."""
        hw = {
            'hwaccel': 'd3d11va',
            'scale_filter': 'scale',
            'download_filter': 'hwdownload,format=bgra',
            'pix_fmt': 'bgra',
        }
        w, h = 160, 90
        expected_buf = b'\x00' * (w * h * 4)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=expected_buf, stderr=b"",
            )
            result = _try_extract_pipe_hwaccel("video.mp4", 5.0, w, h, hw)
            cmd = mock_run.call_args[0][0]

        assert result is not None
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'hwdownload,format=bgra' in vf
        assert 'scale=160:90' in vf


class TestTryExtractPipeSw:
    """Tests for _try_extract_pipe_sw()."""

    def test_sw_pipe_command(self) -> None:
        """Software pipe uses scale + format=bgra."""
        w, h = 80, 45
        expected_buf = b'\x00' * (w * h * 4)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=expected_buf, stderr=b"",
            )
            result = _try_extract_pipe_sw("video.mp4", 10.0, w, h)
            cmd = mock_run.call_args[0][0]

        assert result is not None
        assert result == (w, h, expected_buf)
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert f'scale={w}:{h}' in vf
        assert 'format=bgra' in vf
        assert '-hwaccel' not in cmd  # no hwaccel in SW path


class TestExtractSingleFrame:
    """Unit tests for the _extract_single_frame worker function."""

    def test_pipe_path_returns_pipe_tuple(self) -> None:
        """When pipe extraction succeeds, returns ('pipe', w, h, bytes)."""
        w, h = 80, 42
        fake_buf = b'\x00' * (w * h * 4)

        with patch.object(video_demo, "_extract_frame_pipe") as mock_pipe:
            mock_pipe.return_value = (w, h, fake_buf)

            # 5-tuple with thumb_w
            args = ("video.mp4", 5.0, h, "/tmp/out.jpg", w)
            result = _extract_single_frame(args)

        assert result is not None
        assert result[0] == 'pipe'
        assert result[1] == w
        assert result[2] == h
        assert result[3] == fake_buf

    def test_file_fallback_on_pipe_failure(self, tmp_path: Path) -> None:
        """When pipe fails, falls back to file-based extraction."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(video_demo, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 5.0, 42, out_path, 80)
            result = _extract_single_frame(args)

        assert result is not None
        assert result[0] == 'file'
        assert result[1] == out_path

    def test_file_fallback_without_thumb_w(self, tmp_path: Path) -> None:
        """When no thumb_w (4-tuple args), goes directly to file fallback."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 5.0, 42, out_path)
            result = _extract_single_frame(args)

        assert result is not None
        assert result[0] == 'file'

    def test_returns_none_on_total_failure(self, tmp_path: Path) -> None:
        """When both pipe and file fail, returns None."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(video_demo, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            args = ("video.mp4", 0.0, 42, out_path, 80)
            result = _extract_single_frame(args)

        assert result is None

    def test_file_fallback_ffmpeg_uses_ss(self, tmp_path: Path) -> None:
        """File fallback still uses -ss and -frames:v 1."""
        out_path = str(tmp_path / "thumb_0001.jpg")

        with patch.object(video_demo, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 10.5, 42, out_path, 80)
            _extract_single_frame(args)
            captured_cmd = mock_popen.call_args[0][0]

        assert "-ss" in captured_cmd
        assert "-frames:v" in captured_cmd

    def test_unix_preexec_fn_sets_nice(self, tmp_path: Path) -> None:
        """On Unix, file fallback preexec_fn is set to nice the ffmpeg child."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(video_demo, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 0.0, 42, out_path, 80)
            _extract_single_frame(args)

            call_kwargs = mock_popen.call_args[1]
            assert "preexec_fn" in call_kwargs
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

        with patch.object(video_demo, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 0.0, 42, out_path, 80)
            _extract_single_frame(args)

            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs.get("creationflags") == 0x00004000


class TestBuildPopenPriorityKwargs:
    """Tests for _build_popen_priority_kwargs()."""

    def test_unix_returns_preexec_fn(self) -> None:
        """On Unix, returns preexec_fn in kwargs."""
        startupinfo, kwargs = _build_popen_priority_kwargs()
        assert startupinfo is None
        assert "preexec_fn" in kwargs

    def test_windows_returns_creationflags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, returns BELOW_NORMAL_PRIORITY_CLASS."""
        monkeypatch.setattr(os, "name", "nt")
        mock_startupinfo = MagicMock()
        mock_startupinfo.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_startupinfo, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        startupinfo, kwargs = _build_popen_priority_kwargs()
        assert startupinfo is not None
        assert kwargs.get("creationflags") == 0x00004000


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

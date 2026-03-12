"""Tests for the video thumbnail extraction — demo/video/ package."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Module setup: add demo/video/ to sys.path so bare imports resolve, and
# mock PySide6 to allow headless CI execution.
# ---------------------------------------------------------------------------
import sys

_demo_dir = os.path.join(os.path.dirname(__file__), "..", "demo")
_video_dir = os.path.join(_demo_dir, "video")
for _p in (_video_dir, _demo_dir):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Prevent PySide6 imports from failing in headless CI
for mod_name in [
    "PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
]:
    sys.modules.setdefault(mod_name, MagicMock())

# Import the modules under test (bare imports matching the package convention)
import probe as probe_mod
import hwaccel as hwaccel_mod
import extraction as extraction_mod

# Convenience aliases matching original test convention
_extract_single_frame = extraction_mod._extract_single_frame
_get_video_info = probe_mod._get_video_info
_get_video_info_pyav = probe_mod._get_video_info_pyav
_extract_thumbnails_pyav = extraction_mod._extract_thumbnails_pyav
_pyav_extract_segment = extraction_mod._pyav_extract_segment
_detect_hwaccel = hwaccel_mod._detect_hwaccel
_build_hwaccel_output_format = hwaccel_mod._build_hwaccel_output_format
_run_pipe_cmd = extraction_mod._run_pipe_cmd
_try_extract_pipe_hwaccel = extraction_mod._try_extract_pipe_hwaccel
_try_extract_pipe_sw = extraction_mod._try_extract_pipe_sw
_try_extract_pipe_auto = extraction_mod._try_extract_pipe_auto
_extract_frame_pipe = extraction_mod._extract_frame_pipe
_build_popen_priority_kwargs = extraction_mod._build_popen_priority_kwargs
_build_single_pass_cmd = extraction_mod._build_single_pass_cmd
_detect_rotation_pyav = probe_mod._detect_rotation_pyav
_parse_rotation_from_ffprobe = probe_mod._parse_rotation_from_ffprobe
_displaymatrix_has_vflip = probe_mod._displaymatrix_has_vflip
_get_keyframe_timestamps_pyav = extraction_mod._get_keyframe_timestamps_pyav
_snap_to_keyframes = extraction_mod._snap_to_keyframes
_build_contact_sheet_cmd = extraction_mod._build_contact_sheet_cmd
_run_contact_sheet = extraction_mod._run_contact_sheet
_split_strip_bgra = extraction_mod._split_strip_bgra
_split_strip_bgra_to_rgb = extraction_mod._split_strip_bgra_to_rgb


def _seed_hwaccel_cache(value):
    """Set the hwaccel cache on both the hwaccel module and any bare-import
    alias that may exist in extraction (they share the same module object
    when sys.path is set correctly)."""
    hwaccel_mod._hwaccel_cache = value


@pytest.fixture(autouse=True)
def _reset_hwaccel_cache():
    """Reset the global hwaccel cache before each test."""
    _seed_hwaccel_cache(None)
    yield
    _seed_hwaccel_cache(None)


class TestDetectHwaccel:
    """Tests for _detect_hwaccel()."""

    def test_detects_d3d11va_with_scale_d3d11(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ffmpeg reports d3d11va + scale_d3d11 filter."""
        monkeypatch.setattr(os, "name", "nt")
        mock_si = MagicMock()
        mock_si.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_si, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="d3d11va\ncuda\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale_d3d11\nscale_cuda\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        # On Windows, cuda is preferred over d3d11va
        assert hw['hwaccel'] == 'cuda'
        assert hw['scale_filter'] == 'scale_cuda'
        assert 'hwdownload' in hw['download_filter']

    def test_detects_d3d11va_when_no_cuda(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """d3d11va is selected when CUDA is not available (AMD/Intel GPU)."""
        monkeypatch.setattr(os, "name", "nt")
        mock_si = MagicMock()
        mock_si.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_si, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="d3d11va\n", stderr="")
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
        monkeypatch.setattr(os, "name", "nt")
        mock_si = MagicMock()
        mock_si.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_si, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

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

    def test_detects_cuda_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CUDA detection on Linux (NVIDIA GPU)."""
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="cuda\nvaapi\n", stderr="")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="scale_cuda\nscale_vaapi\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'cuda'
        assert hw['scale_filter'] == 'scale_cuda'

    def test_detects_videotoolbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """macOS videotoolbox detection."""
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "darwin")

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
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")

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

    def test_detects_hwaccel_from_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some ffmpeg versions output hwaccel info to stderr."""
        monkeypatch.setattr(os, "name", "nt")
        mock_si = MagicMock()
        mock_si.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_si, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        def fake_run(cmd, **kw):
            if '-hwaccels' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="d3d11va\n")
            if '-filters' in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="scale_d3d11\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        hw = _detect_hwaccel()
        assert hw['hwaccel'] == 'd3d11va'
        assert hw['scale_filter'] == 'scale_d3d11'

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

    def test_cuda_maps_to_cuda(self) -> None:
        assert _build_hwaccel_output_format('cuda') == 'cuda'

    def test_videotoolbox_maps_to_vld(self) -> None:
        assert _build_hwaccel_output_format('videotoolbox') == 'videotoolbox_vld'

    def test_vaapi_maps_to_vaapi(self) -> None:
        assert _build_hwaccel_output_format('vaapi') == 'vaapi'

    def test_qsv_maps_to_qsv(self) -> None:
        assert _build_hwaccel_output_format('qsv') == 'qsv'

    def test_unknown_returns_itself(self) -> None:
        assert _build_hwaccel_output_format('dxva2') == 'dxva2'


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
                [], 1, stdout=b"", stderr=b"error message",
            )
            result = _run_pipe_cmd(['-hwaccel', 'auto', 'test'], 4, 2)

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
            'download_filter': 'hwdownload',
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
        assert '-nostdin' in cmd
        assert '-probesize' in cmd
        # Check the -vf filter chain
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'scale_d3d11=160:90' in vf
        assert 'hwdownload' in vf
        # format=bgra must be a separate filter AFTER hwdownload
        assert vf.endswith('format=bgra')
        assert '-f' in cmd
        assert 'rawvideo' in cmd
        assert 'pipe:1' in cmd

    def test_hwdownload_then_cpu_scale(self) -> None:
        """When no GPU scale filter, hwdownload first then CPU scale."""
        hw = {
            'hwaccel': 'd3d11va',
            'scale_filter': 'scale',
            'download_filter': 'hwdownload',
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
        # Order: hwdownload → scale → format=bgra
        assert vf == 'hwdownload,scale=160:90,format=bgra'


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
        assert '-nostdin' in cmd  # stdin blocking prevention


class TestTryExtractPipeAuto:
    """Tests for _try_extract_pipe_auto()."""

    def test_auto_gpu_pipe_uses_hwaccel_auto(self) -> None:
        """Auto GPU pipe uses -hwaccel auto for robust GPU detection."""
        w, h = 80, 42
        expected_buf = b'\x00' * (w * h * 4)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=expected_buf, stderr=b"",
            )
            result = _try_extract_pipe_auto("video.mp4", 5.0, w, h)
            cmd = mock_run.call_args[0][0]

        assert result is not None
        assert result == (w, h, expected_buf)
        assert '-hwaccel' in cmd
        hwaccel_idx = cmd.index('-hwaccel')
        assert cmd[hwaccel_idx + 1] == 'auto'
        # Should NOT have -hwaccel_output_format (auto doesn't need it)
        assert '-hwaccel_output_format' not in cmd
        assert '-nostdin' in cmd
        assert '-probesize' in cmd
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert f'scale={w}:{h}' in vf
        assert 'format=bgra' in vf

    def test_auto_gpu_returns_none_on_failure(self) -> None:
        """When auto GPU fails, returns None."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 1, stdout=b"", stderr=b"hwaccel error",
            )
            result = _try_extract_pipe_auto("video.mp4", 5.0, 80, 42)

        assert result is None


class TestExtractSingleFrame:
    """Unit tests for the _extract_single_frame worker function."""

    def test_pipe_path_returns_pipe_tuple(self) -> None:
        """When pipe extraction succeeds, returns ('pipe', w, h, bytes)."""
        w, h = 80, 42
        fake_buf = b'\x00' * (w * h * 4)

        with patch.object(extraction_mod, "_extract_frame_pipe") as mock_pipe:
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

        with patch.object(extraction_mod, "_extract_frame_pipe", return_value=None), \
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

        with patch.object(extraction_mod, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            args = ("video.mp4", 0.0, 42, out_path, 80)
            result = _extract_single_frame(args)

        assert result is None

    def test_file_fallback_ffmpeg_uses_ss(self, tmp_path: Path) -> None:
        """File fallback still uses -ss and -frames:v 1 with perf flags."""
        out_path = str(tmp_path / "thumb_0001.jpg")

        with patch.object(extraction_mod, "_extract_frame_pipe", return_value=None), \
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
        assert "-nostdin" in captured_cmd
        assert "-probesize" in captured_cmd

    def test_unix_lower_priority_after_popen(self, tmp_path: Path) -> None:
        """On Unix, _lower_process_priority is called after Popen to nice the child."""
        out_path = str(tmp_path / "thumb_0000.jpg")

        with patch.object(extraction_mod, "_extract_frame_pipe", return_value=None), \
             patch.object(subprocess, "Popen") as mock_popen, \
             patch.object(extraction_mod, "_lower_process_priority") as mock_lower:
            proc_mock = MagicMock()
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            Path(out_path).write_bytes(b"\xff\xd8\xff")

            args = ("video.mp4", 0.0, 42, out_path, 80)
            _extract_single_frame(args)

            mock_lower.assert_called_once_with(proc_mock)

    def test_windows_low_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, BELOW_NORMAL_PRIORITY_CLASS is passed to Popen."""
        out_path = str(tmp_path / "thumb_0000.jpg")
        monkeypatch.setattr(os, "name", "nt")

        mock_startupinfo = MagicMock()
        mock_startupinfo.dwFlags = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: mock_startupinfo, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)

        with patch.object(extraction_mod, "_extract_frame_pipe", return_value=None), \
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

    def test_unix_returns_no_preexec_fn(self) -> None:
        """On Unix, returns empty kwargs (priority lowered post-Popen instead)."""
        startupinfo, kwargs = _build_popen_priority_kwargs()
        assert startupinfo is None
        assert "preexec_fn" not in kwargs

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
        """Successful probe returns (width, height, duration, rotation, vflip)."""
        fake_output = '{"streams":[{"width":1920,"height":1080,"duration":"30.0"}]}'
        fake_result = subprocess.CompletedProcess([], 0, stdout=fake_output, stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        w, h, d, rot, vf = _get_video_info("test.mp4")
        assert (w, h, d, rot, vf) == (1920, 1080, 30.0, 0, False)

    def test_returns_zeros_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On probe failure, returns (0, 0, 0, 0, False)."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )

        w, h, d, rot, vf = _get_video_info("nonexistent.mp4")
        assert (w, h, d, rot, vf) == (0, 0, 0, 0, False)

    def test_rotation_90_swaps_dimensions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When rotate=90 tag is present, width and height are swapped."""
        fake_output = '{"streams":[{"width":1920,"height":1080,"duration":"30.0","tags":{"rotate":"90"}}]}'
        fake_result = subprocess.CompletedProcess([], 0, stdout=fake_output, stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        w, h, d, rot, vf = _get_video_info("portrait.mp4")
        assert (w, h) == (1080, 1920)
        assert rot == 90

    def test_rotation_270_swaps_dimensions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When rotate=270 tag is present, width and height are swapped."""
        fake_output = '{"streams":[{"width":1920,"height":1080,"duration":"30.0","tags":{"rotate":"270"}}]}'
        fake_result = subprocess.CompletedProcess([], 0, stdout=fake_output, stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        w, h, d, rot, vf = _get_video_info("portrait.mp4")
        assert (w, h) == (1080, 1920)
        assert rot == 270

    def test_rotation_180_keeps_dimensions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When rotate=180 tag is present, dimensions stay the same."""
        fake_output = '{"streams":[{"width":1920,"height":1080,"duration":"30.0","tags":{"rotate":"180"}}]}'
        fake_result = subprocess.CompletedProcess([], 0, stdout=fake_output, stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        w, h, d, rot, vf = _get_video_info("flipped.mp4")
        assert (w, h) == (1920, 1080)
        assert rot == 180


class TestBuildSinglePassCmd:
    """Tests for _build_single_pass_cmd()."""

    def test_gpu_keyframe_command(self) -> None:
        """GPU + keyframe-only produces correct flags."""
        cmd = _build_single_pass_cmd(
            "video.mp4", 80, 42, 0.5,
            hwaccel=True, keyframe_only=True,
        )
        assert '-hwaccel' in cmd
        assert 'auto' in cmd
        assert '-skip_frame' in cmd
        assert 'nokey' in cmd
        assert '-an' in cmd
        assert '-f' in cmd
        assert 'rawvideo' in cmd
        assert 'pipe:1' in cmd
        assert '-nostdin' in cmd
        assert '-probesize' in cmd
        assert '-fflags' in cmd
        assert '-vsync' in cmd
        assert 'vfr' in cmd
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'fps=' in vf
        assert 'scale=80:42' in vf
        assert 'format=bgra' in vf
        # -skip_frame nokey already limits to keyframes; select filter is
        # redundant and should NOT be present (removed for performance).
        assert "select=" not in vf

    def test_cpu_keyframe_command(self) -> None:
        """CPU + keyframe-only: no -hwaccel, has -skip_frame."""
        cmd = _build_single_pass_cmd(
            "video.mp4", 80, 42, 0.5,
            hwaccel=False, keyframe_only=True,
        )
        assert '-hwaccel' not in cmd
        assert '-skip_frame' in cmd
        assert 'nokey' in cmd
        assert '-nostdin' in cmd

    def test_cpu_full_decode_command(self) -> None:
        """CPU without keyframe skip: no -hwaccel, no -skip_frame, no select."""
        cmd = _build_single_pass_cmd(
            "video.mp4", 80, 42, 0.5,
            hwaccel=False, keyframe_only=False,
        )
        assert '-hwaccel' not in cmd
        assert '-skip_frame' not in cmd
        assert '-nostdin' in cmd
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert "select=" not in vf

    def test_fps_rate_in_vf(self) -> None:
        """fps rate is correctly embedded in the -vf filter chain."""
        cmd = _build_single_pass_cmd(
            "video.mp4", 160, 90, 0.123456,
            hwaccel=False, keyframe_only=False,
        )
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'fps=0.123456' in vf
        assert 'scale=160:90' in vf


# ---------------------------------------------------------------------------
# PyAV-based extraction tests
# ---------------------------------------------------------------------------

class TestGetVideoInfoPyav:
    """Tests for _get_video_info_pyav()."""

    def test_returns_width_height_duration(self) -> None:
        """Successful probe returns (width, height, duration, rotation, vflip)."""
        mock_stream = MagicMock()
        mock_stream.codec_context.width = 1920
        mock_stream.codec_context.height = 1080
        mock_stream.duration = 900000
        mock_stream.time_base = MagicMock()
        mock_stream.time_base.__float__ = lambda self: 1 / 30000.0
        mock_stream.time_base.__mul__ = lambda self, other: other * (1 / 30000.0)
        mock_stream.time_base.__rmul__ = lambda self, other: other * (1 / 30000.0)
        mock_stream.metadata = {}
        mock_stream.side_data = {}

        mock_container = MagicMock()
        mock_container.streams.video = [mock_stream]
        mock_container.duration = None

        with patch.object(probe_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mock_container
            w, h, d, rot, vf = _get_video_info_pyav("test.mp4")

        assert w == 1920
        assert h == 1080
        assert d == pytest.approx(30.0, abs=0.1)
        assert rot == 0
        assert vf is False

    def test_falls_back_to_container_duration(self) -> None:
        """When stream.duration is None, uses container.duration."""
        mock_stream = MagicMock()
        mock_stream.codec_context.width = 3840
        mock_stream.codec_context.height = 2160
        mock_stream.duration = None
        mock_stream.time_base = None
        mock_stream.metadata = {}
        mock_stream.side_data = {}

        mock_container = MagicMock()
        mock_container.streams.video = [mock_stream]
        mock_container.duration = 60_000_000  # microseconds

        with patch.object(probe_mod, '_av_module') as mock_av:
            mock_av.time_base = 1_000_000
            mock_av.open.return_value = mock_container
            w, h, d, rot, vf = _get_video_info_pyav("test.mp4")

        assert w == 3840
        assert h == 2160
        assert d == pytest.approx(60.0, abs=0.1)

    def test_returns_zeros_on_failure(self) -> None:
        """On probe failure, returns (0, 0, 0, 0, False)."""
        with patch.object(probe_mod, '_av_module') as mock_av:
            mock_av.open.side_effect = Exception("file not found")
            w, h, d, rot, vf = _get_video_info_pyav("nonexistent.mp4")

        assert (w, h, d, rot, vf) == (0, 0, 0, 0, False)

    def test_rotation_90_swaps_dimensions(self) -> None:
        """When rotate=90 metadata tag, dimensions are swapped."""
        mock_stream = MagicMock()
        mock_stream.codec_context.width = 1920
        mock_stream.codec_context.height = 1080
        mock_stream.duration = 300000
        mock_stream.time_base = MagicMock()
        mock_stream.time_base.__float__ = lambda self: 1 / 30000.0
        mock_stream.time_base.__mul__ = lambda self, o: o * (1 / 30000.0)
        mock_stream.time_base.__rmul__ = lambda self, o: o * (1 / 30000.0)
        mock_stream.metadata = {'rotate': '90'}
        mock_stream.side_data = {}

        mock_container = MagicMock()
        mock_container.streams.video = [mock_stream]
        mock_container.duration = None

        with patch.object(probe_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mock_container
            w, h, d, rot, vf = _get_video_info_pyav("portrait.mp4")

        assert (w, h) == (1080, 1920)
        assert rot == 90


class TestPyavExtractSegment:
    """Tests for _pyav_extract_segment() (individual seeks per frame)."""

    def _make_mock_container(self, tb=1/30000.0, frame_pts_list=None):
        """Build a mock container whose decode() yields frames with PTS.

        Each call to decode() returns the next single-frame iterator
        (matching the per-seek pattern used by _pyav_extract_segment).
        """
        if frame_pts_list is None:
            frame_pts_list = [0, 75000, 150000]  # 0s, 2.5s, 5s at tb

        mock_img = MagicMock()
        mock_img.tobytes.return_value = b'\x00' * (80 * 42 * 3)
        mock_img.transpose.return_value = mock_img

        from fractions import Fraction
        time_base = Fraction(1, 30000)

        frames = []
        for pts in frame_pts_list:
            f = MagicMock()
            f.pts = pts
            f.to_image.return_value = mock_img
            frames.append(f)

        mock_stream = MagicMock()
        mock_stream.time_base = time_base

        mock_container = MagicMock()
        mock_container.streams.video = [mock_stream]
        # Each call to decode() returns a fresh single-frame iterator
        mock_container.decode.side_effect = [
            iter([f]) for f in frames
        ]
        return mock_container, frames, mock_img

    def test_extracts_frames_for_indices(self) -> None:
        """Returns one result per (index, time) pair."""
        mc, _, _ = self._make_mock_container(
            frame_pts_list=[0, 75000, 150000],
        )
        indices = [(0, 0.0), (1, 2.5), (2, 5.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            results = _pyav_extract_segment("t.mp4", indices, 80, 42)

        assert len(results) == 3
        for global_idx, w, h, data in results:
            assert w == 80
            assert h == 42

    def test_preserves_global_index(self) -> None:
        """Returned tuples contain the correct global index in order."""
        mc, _, _ = self._make_mock_container(
            frame_pts_list=[300000, 600000],
        )
        indices = [(5, 10.0), (9, 20.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            results = _pyav_extract_segment("t.mp4", indices, 80, 42)

        idx_list = [r[0] for r in results]
        assert idx_list == [5, 9]

    def test_uses_fast_bilinear(self) -> None:
        """frame.to_image uses FAST_BILINEAR interpolation."""
        mc, frames, _ = self._make_mock_container(
            frame_pts_list=[0],
        )
        indices = [(0, 0.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _pyav_extract_segment("t.mp4", indices, 80, 42)
        frames[0].to_image.assert_called_with(
            width=80, height=42, interpolation='FAST_BILINEAR',
        )

    def test_sets_thread_count(self) -> None:
        """Codec context thread_count is set to 2 to reduce contention."""
        mc, _, _ = self._make_mock_container(frame_pts_list=[0])
        indices = [(0, 0.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _pyav_extract_segment("t.mp4", indices, 80, 42)
        stream = mc.streams.video[0]
        assert stream.codec_context.thread_count == 2

    def test_seeks_per_frame(self) -> None:
        """Container.seek() is called once per target frame."""
        mc, _, _ = self._make_mock_container(
            frame_pts_list=[0, 75000, 150000],
        )
        indices = [(0, 0.0), (1, 2.5), (2, 5.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _pyav_extract_segment("t.mp4", indices, 80, 42)
        # Individual seeks: one seek per target
        assert mc.seek.call_count == 3

    def test_rotation_90_swaps_extract_dims(self) -> None:
        """For 90° rotation, extract at swapped dims then rotate."""
        mc, frames, mock_img = self._make_mock_container(
            frame_pts_list=[0],
        )
        indices = [(0, 0.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            results = _pyav_extract_segment(
                "t.mp4", indices, 80, 42, rotation=90,
            )
        # Should extract at swapped dims (h, w) = (42, 80)
        frames[0].to_image.assert_called_with(
            width=42, height=80, interpolation='FAST_BILINEAR',
        )
        # Should call transpose for rotation
        from PIL import Image
        mock_img.transpose.assert_called_with(Image.Transpose.ROTATE_270)
        assert len(results) == 1
        assert results[0][1] == 80  # display width
        assert results[0][2] == 42  # display height

    def test_rotation_180_keeps_dims(self) -> None:
        """For 180° rotation, extract at same dims then rotate."""
        mc, frames, mock_img = self._make_mock_container(
            frame_pts_list=[0],
        )
        indices = [(0, 0.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            results = _pyav_extract_segment(
                "t.mp4", indices, 80, 42, rotation=180,
            )
        frames[0].to_image.assert_called_with(
            width=80, height=42, interpolation='FAST_BILINEAR',
        )
        from PIL import Image
        mock_img.transpose.assert_called_with(Image.Transpose.ROTATE_180)

    def test_vflip_applied(self) -> None:
        """Vertical flip is applied when vflip=True."""
        mc, _, mock_img = self._make_mock_container(
            frame_pts_list=[0],
        )
        indices = [(0, 0.0)]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _pyav_extract_segment(
                "t.mp4", indices, 80, 42, vflip=True,
            )
        from PIL import Image
        mock_img.transpose.assert_called_with(
            Image.Transpose.FLIP_TOP_BOTTOM,
        )

    def test_closes_container_on_error(self) -> None:
        """Container is closed even when decode fails."""
        mc = MagicMock()
        mc.streams.video = [MagicMock()]
        mc.streams.video[0].time_base = MagicMock()
        mc.streams.video[0].time_base.__float__ = lambda self: 1/30000.0
        mc.decode.side_effect = Exception("decode error")
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            results = _pyav_extract_segment("t.mp4", [(0, 0.0)], 80, 42)
        assert results == []
        mc.close.assert_called_once()

    def test_returns_empty_on_open_error(self) -> None:
        """Returns empty list when av.open fails."""
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.side_effect = Exception("file error")
            results = _pyav_extract_segment("bad.mp4", [(0, 0.0)], 80, 42)
        assert results == []

    def test_returns_empty_on_empty_indices(self) -> None:
        """Returns empty list when indices list is empty."""
        results = _pyav_extract_segment("t.mp4", [], 80, 42)
        assert results == []


class TestExtractThumbnailsPyav:
    """Tests for _extract_thumbnails_pyav() (keyframe-aware multi-threaded)."""

    def _make_mock_container(self, tb=1/30000.0, duration=300000):
        mock_img = MagicMock()
        mock_img.tobytes.return_value = b'\x00' * (80 * 42 * 3)
        mock_img.transpose.return_value = mock_img

        from fractions import Fraction
        time_base = Fraction(1, 30000)

        mock_stream = MagicMock()
        mock_stream.duration = duration
        mock_stream.time_base = time_base

        mock_container = MagicMock()
        mock_container.streams.video = [mock_stream]
        mock_container.duration = None

        # For continuous decode, return frames with PTS values
        def make_frames(s):
            frames = []
            for i in range(10):
                f = MagicMock()
                f.pts = int(i * 2.0 / float(time_base))
                f.to_image.return_value = mock_img
                frames.append(f)
            return iter(frames)
        mock_container.decode.side_effect = make_frames
        return mock_container, mock_img

    def test_extracts_correct_number_of_frames(self) -> None:
        """Returns the requested number of thumbnails."""
        mc, _ = self._make_mock_container()
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            with patch('os.cpu_count', return_value=1):
                with patch.object(
                    extraction_mod, '_get_keyframe_timestamps_pyav',
                    return_value=[],
                ):
                    results = _extract_thumbnails_pyav(
                        "test.mp4", 5, 80, 42,
                    )
        assert len(results) == 5
        for w, h, data in results:
            assert w == 80
            assert h == 42
            assert len(data) == 80 * 42 * 3

    def test_calls_callback_for_each_frame(self) -> None:
        """Callback is called for each extracted frame."""
        mc, _ = self._make_mock_container()
        callback = MagicMock()
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            with patch('os.cpu_count', return_value=1):
                with patch.object(
                    extraction_mod, '_get_keyframe_timestamps_pyav',
                    return_value=[],
                ):
                    _extract_thumbnails_pyav(
                        "test.mp4", 3, 80, 42, callback=callback,
                    )
        assert callback.call_count == 3

    def test_returns_empty_on_error(self) -> None:
        """Returns empty list when PyAV fails."""
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.side_effect = Exception("codec error")
            results = _extract_thumbnails_pyav("bad.mp4", 5, 80, 42)
        assert results == []

    def test_multithreaded_with_multiple_cpus(self) -> None:
        """Uses multiple threads when CPU count > 2."""
        mc, _ = self._make_mock_container()
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            with patch('os.cpu_count', return_value=8):
                with patch.object(
                    extraction_mod, '_get_keyframe_timestamps_pyav',
                    return_value=[],
                ):
                    results = _extract_thumbnails_pyav(
                        "test.mp4", 8, 80, 42,
                    )
        assert len(results) == 8

    def test_results_are_sorted_by_index(self) -> None:
        """Results are returned in frame order regardless of thread."""
        mc, _ = self._make_mock_container()
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            with patch('os.cpu_count', return_value=4):
                with patch.object(
                    extraction_mod, '_get_keyframe_timestamps_pyav',
                    return_value=[],
                ):
                    results = _extract_thumbnails_pyav(
                        "test.mp4", 10, 80, 42,
                    )
        assert len(results) == 10

    def test_uses_keyframe_snapping(self) -> None:
        """When keyframes available, snaps targets to nearest keyframe."""
        mc, _ = self._make_mock_container()
        kf_times = [0.0, 2.0, 4.0, 6.0, 8.0]
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            with patch('os.cpu_count', return_value=1):
                with patch.object(
                    extraction_mod, '_get_keyframe_timestamps_pyav',
                    return_value=kf_times,
                ):
                    results = _extract_thumbnails_pyav(
                        "test.mp4", 3, 80, 42,
                    )
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Rotation / flip detection tests
# ---------------------------------------------------------------------------

class TestParseRotationFromFfprobe:
    """Tests for _parse_rotation_from_ffprobe()."""

    def test_no_rotation(self) -> None:
        rot, vflip = _parse_rotation_from_ffprobe({})
        assert rot == 0
        assert vflip is False

    def test_rotate_tag_90(self) -> None:
        rot, vflip = _parse_rotation_from_ffprobe(
            {'tags': {'rotate': '90'}},
        )
        assert rot == 90

    def test_rotate_tag_270(self) -> None:
        rot, vflip = _parse_rotation_from_ffprobe(
            {'tags': {'rotate': '270'}},
        )
        assert rot == 270

    def test_rotate_tag_180(self) -> None:
        rot, vflip = _parse_rotation_from_ffprobe(
            {'tags': {'rotate': '180'}},
        )
        assert rot == 180

    def test_display_matrix_rotation(self) -> None:
        """side_data_list with Display Matrix rotation=-90 → 90° CW."""
        sd = {
            'side_data_list': [{
                'side_data_type': 'Display Matrix',
                'rotation': -90,
            }],
        }
        rot, vflip = _parse_rotation_from_ffprobe(sd)
        assert rot == 90

    def test_display_matrix_rotation_negative_270(self) -> None:
        """rotation=-270 → 270° CW."""
        sd = {
            'side_data_list': [{
                'side_data_type': 'Display Matrix',
                'rotation': -270,
            }],
        }
        rot, vflip = _parse_rotation_from_ffprobe(sd)
        assert rot == 270

    def test_snaps_to_nearest_90(self) -> None:
        """Non-standard rotation (e.g. 89°) snaps to nearest 90°."""
        rot, _ = _parse_rotation_from_ffprobe({'tags': {'rotate': '89'}})
        assert rot == 90
        rot, _ = _parse_rotation_from_ffprobe({'tags': {'rotate': '91'}})
        assert rot == 90

    def test_rotate_tag_takes_priority_over_side_data(self) -> None:
        """When both rotate tag and side_data exist, rotate tag wins."""
        sd = {
            'tags': {'rotate': '180'},
            'side_data_list': [{
                'side_data_type': 'Display Matrix',
                'rotation': -90,
            }],
        }
        rot, _ = _parse_rotation_from_ffprobe(sd)
        assert rot == 180


class TestDisplaymatrixHasVflip:
    """Tests for _displaymatrix_has_vflip()."""

    def test_normal_matrix(self) -> None:
        # Identity-like: positive [1][1] → no vflip
        dm = "00010000 00000000 00000000\n00000000 00010000 00000000\n00000000 00000000 40000000"
        assert _displaymatrix_has_vflip(dm) is False

    def test_vflip_matrix(self) -> None:
        # Negative [1][1] in second row → vflip
        dm = "00010000 00000000 00000000\n00000000 FFFF0000 00000000\n00000000 00000000 40000000"
        assert _displaymatrix_has_vflip(dm) is True

    def test_empty_string(self) -> None:
        assert _displaymatrix_has_vflip("") is False

    def test_invalid_string(self) -> None:
        assert _displaymatrix_has_vflip("not a matrix") is False


class TestDetectRotationPyav:
    """Tests for _detect_rotation_pyav()."""

    def test_no_rotation_metadata(self) -> None:
        stream = MagicMock()
        stream.metadata = {}
        rot, vflip = _detect_rotation_pyav(stream)
        assert rot == 0
        assert vflip is False

    def test_rotate_tag_90(self) -> None:
        stream = MagicMock()
        stream.metadata = {'rotate': '90'}
        rot, vflip = _detect_rotation_pyav(stream)
        assert rot == 90

    def test_rotate_tag_270(self) -> None:
        stream = MagicMock()
        stream.metadata = {'rotate': '270'}
        rot, vflip = _detect_rotation_pyav(stream)
        assert rot == 270

    def test_no_side_data_attr(self) -> None:
        """Gracefully handles missing side_data attribute."""
        stream = MagicMock(spec=[])
        stream.metadata = {'rotate': '180'}
        rot, vflip = _detect_rotation_pyav(stream)
        assert rot == 180

    def test_frame_rotation_90(self) -> None:
        """Detects 90° CW from frame.rotation (CCW=-90)."""
        stream = MagicMock()
        stream.metadata = {}
        frame = MagicMock()
        frame.rotation = -90  # CCW → 90° CW
        frame.side_data = {}
        container = MagicMock()
        container.decode.return_value = iter([frame])
        rot, vflip = _detect_rotation_pyav(stream, container)
        assert rot == 90

    def test_frame_rotation_270(self) -> None:
        """Detects 270° CW from frame.rotation (CCW=90)."""
        stream = MagicMock()
        stream.metadata = {}
        frame = MagicMock()
        frame.rotation = 90  # CCW → 270° CW
        frame.side_data = {}
        container = MagicMock()
        container.decode.return_value = iter([frame])
        rot, vflip = _detect_rotation_pyav(stream, container)
        assert rot == 270

    def test_frame_rotation_180(self) -> None:
        """Detects 180° from frame.rotation."""
        stream = MagicMock()
        stream.metadata = {}
        frame = MagicMock()
        frame.rotation = -180
        frame.side_data = {}
        container = MagicMock()
        container.decode.return_value = iter([frame])
        rot, vflip = _detect_rotation_pyav(stream, container)
        assert rot == 180

    def test_rotate_tag_takes_priority_over_frame(self) -> None:
        """When rotate tag exists, frame.rotation is not checked."""
        stream = MagicMock()
        stream.metadata = {'rotate': '90'}
        frame = MagicMock()
        frame.rotation = -180  # would give 180 if checked
        container = MagicMock()
        container.decode.return_value = iter([frame])
        rot, vflip = _detect_rotation_pyav(stream, container)
        assert rot == 90

    def test_no_container_no_frame_check(self) -> None:
        """Without container, only metadata tag is checked."""
        stream = MagicMock()
        stream.metadata = {}
        rot, vflip = _detect_rotation_pyav(stream, container=None)
        assert rot == 0


# ---------------------------------------------------------------------------
# Keyframe-aware sampling tests
# ---------------------------------------------------------------------------

class TestGetKeyframeTimestampsPyav:
    """Tests for _get_keyframe_timestamps_pyav() (packet-level demux)."""

    def test_returns_keyframe_timestamps(self) -> None:
        """Extracts timestamps from keyframe packets (no decoding)."""
        from fractions import Fraction
        tb = Fraction(1, 30000)
        mock_packets = []
        for pts, is_kf in [(0, True), (15000, False), (90000, True),
                           (105000, False), (180000, True)]:
            p = MagicMock()
            p.pts = pts
            p.is_keyframe = is_kf
            mock_packets.append(p)
        # Flush packet at end (pts=None)
        flush = MagicMock()
        flush.pts = None
        flush.is_keyframe = False
        mock_packets.append(flush)

        mock_stream = MagicMock()
        mock_stream.time_base = tb

        mc = MagicMock()
        mc.streams.video = [mock_stream]
        mc.demux.return_value = iter(mock_packets)

        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            kf = _get_keyframe_timestamps_pyav("test.mp4")

        # Only keyframe packets (3 of 5 + flush)
        assert len(kf) == 3
        assert abs(kf[0] - 0.0) < 0.01
        assert abs(kf[1] - 3.0) < 0.01
        assert abs(kf[2] - 6.0) < 0.01

    def test_uses_demux_not_decode(self) -> None:
        """Uses container.demux() (packet-level) not decode (no decoding)."""
        mc = MagicMock()
        mc.streams.video = [MagicMock()]
        mc.streams.video[0].time_base = MagicMock()
        mc.streams.video[0].time_base.__float__ = lambda self: 1/30000.0
        mc.demux.return_value = iter([])
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _get_keyframe_timestamps_pyav("test.mp4")
        mc.demux.assert_called_once()
        mc.decode.assert_not_called()

    def test_returns_empty_on_error(self) -> None:
        """Returns empty list on failure."""
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.side_effect = Exception("bad file")
            kf = _get_keyframe_timestamps_pyav("bad.mp4")
        assert kf == []

    def test_closes_container(self) -> None:
        """Container is closed after scanning."""
        mc = MagicMock()
        mc.streams.video = [MagicMock()]
        mc.streams.video[0].time_base = MagicMock()
        mc.streams.video[0].time_base.__float__ = lambda self: 1/30000.0
        mc.demux.return_value = iter([])
        with patch.object(extraction_mod, '_av_module') as mock_av:
            mock_av.open.return_value = mc
            _get_keyframe_timestamps_pyav("test.mp4")
        mc.close.assert_called_once()


class TestSnapToKeyframes:
    """Tests for _snap_to_keyframes()."""

    def test_snaps_to_nearest(self) -> None:
        """Each target maps to its nearest keyframe."""
        keyframes = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
        targets = [0.9, 3.1, 7.9]
        result = _snap_to_keyframes(targets, keyframes)
        assert len(result) == 3
        assert result[0] == (0, 0.0)   # 0.9 → nearest 0.0
        assert result[1] == (1, 4.0)   # 3.1 → nearest 4.0
        assert result[2] == (2, 8.0)   # 7.9 → nearest 8.0

    def test_empty_keyframes_returns_original(self) -> None:
        """With no keyframes, returns original targets."""
        targets = [1.0, 2.0, 3.0]
        result = _snap_to_keyframes(targets, [])
        assert result == [(0, 1.0), (1, 2.0), (2, 3.0)]

    def test_single_keyframe(self) -> None:
        """All targets snap to the single available keyframe."""
        result = _snap_to_keyframes([0.5, 2.0, 5.0], [1.0])
        assert all(t == 1.0 for _, t in result)

    def test_preserves_index(self) -> None:
        """Original index is preserved in output."""
        result = _snap_to_keyframes([0.0, 5.0], [0.0, 10.0])
        assert result[0][0] == 0
        assert result[1][0] == 1

    def test_exact_match(self) -> None:
        """Exact keyframe timestamps are matched directly."""
        keyframes = [0.0, 2.0, 4.0]
        result = _snap_to_keyframes([2.0, 4.0], keyframes)
        assert result[0] == (0, 2.0)
        assert result[1] == (1, 4.0)


# ---------------------------------------------------------------------------
# Contact-sheet strategy tests
# ---------------------------------------------------------------------------

class TestBuildContactSheetCmd:
    """Tests for _build_contact_sheet_cmd()."""

    def test_keyframe_only_command(self) -> None:
        """Keyframe-only produces -skip_frame nokey and tile filter."""
        cmd = _build_contact_sheet_cmd(
            "video.mp4", 80, 42, 10, 0.5, keyframe_only=True,
        )
        assert '-skip_frame' in cmd
        assert 'nokey' in cmd
        assert '-hwaccel' in cmd
        assert 'auto' in cmd
        assert '-frames:v' in cmd
        assert '1' in cmd
        assert '-nostdin' in cmd
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'tile=10x1' in vf
        assert 'scale=80:42' in vf
        assert 'format=bgra' in vf
        assert 'fps=' in vf

    def test_no_keyframe_skip(self) -> None:
        """Without keyframe-only, -skip_frame is omitted."""
        cmd = _build_contact_sheet_cmd(
            "video.mp4", 80, 42, 5, 0.3, keyframe_only=False,
        )
        assert '-skip_frame' not in cmd
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'tile=5x1' in vf

    def test_fps_rate_embedded(self) -> None:
        """fps rate is correctly embedded in the filter chain."""
        cmd = _build_contact_sheet_cmd(
            "video.mp4", 160, 90, 20, 0.654321, keyframe_only=True,
        )
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'fps=0.654321' in vf


class TestRunContactSheet:
    """Tests for _run_contact_sheet()."""

    def test_returns_strip_on_success(self) -> None:
        """Returns (strip_w, strip_h, bytes) on success."""
        thumb_w, thumb_h, count = 80, 42, 3
        strip_w = thumb_w * count
        expected_size = strip_w * thumb_h * 4
        fake_data = b'\x00' * expected_size

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=fake_data, stderr=b"",
            )
            result = _run_contact_sheet(
                "video.mp4", thumb_w, thumb_h, count, 0.5,
            )

        assert result is not None
        assert result[0] == strip_w
        assert result[1] == thumb_h
        assert len(result[2]) == expected_size

    def test_returns_none_on_failure(self) -> None:
        """Returns None when ffmpeg fails."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 1, stdout=b"", stderr=b"error",
            )
            result = _run_contact_sheet("video.mp4", 80, 42, 3, 0.5)

        assert result is None

    def test_accepts_partial_strip(self) -> None:
        """Accepts partial output when fewer frames than requested."""
        thumb_w, thumb_h = 80, 42
        # Only 2 frames instead of 3
        actual_frames = 2
        partial_size = thumb_w * actual_frames * thumb_h * 4
        fake_data = b'\xAB' * partial_size

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=fake_data, stderr=b"",
            )
            result = _run_contact_sheet("video.mp4", thumb_w, thumb_h, 3, 0.5)

        assert result is not None
        assert result[0] == thumb_w * actual_frames


class TestSplitStripBgra:
    """Tests for _split_strip_bgra()."""

    def test_splits_into_correct_count(self) -> None:
        """Returns correct number of frame buffers."""
        # Create a 2x1 tile strip: 2 frames of 4x2 pixels
        thumb_w, thumb_h, count = 4, 2, 2
        strip_w = thumb_w * count  # 8 pixels wide
        # Build strip data: row-by-row, tiles side by side
        strip = bytearray(strip_w * thumb_h * 4)
        # Fill frame 0 with 0xAA and frame 1 with 0xBB
        for y in range(thumb_h):
            row_start = y * strip_w * 4
            for x in range(thumb_w):
                offset = row_start + x * 4
                strip[offset:offset + 4] = b'\xAA\xAA\xAA\xAA'
            for x in range(thumb_w):
                offset = row_start + (thumb_w + x) * 4
                strip[offset:offset + 4] = b'\xBB\xBB\xBB\xBB'

        frames = _split_strip_bgra(bytes(strip), thumb_w, thumb_h, count)
        assert len(frames) == 2
        assert len(frames[0]) == thumb_w * thumb_h * 4
        assert len(frames[1]) == thumb_w * thumb_h * 4
        # All bytes of frame 0 should be 0xAA
        assert all(b == 0xAA for b in frames[0])
        # All bytes of frame 1 should be 0xBB
        assert all(b == 0xBB for b in frames[1])

    def test_single_frame(self) -> None:
        """Single-frame strip returns one buffer."""
        thumb_w, thumb_h = 4, 2
        data = b'\xFF' * (thumb_w * thumb_h * 4)
        frames = _split_strip_bgra(data, thumb_w, thumb_h, 1)
        assert len(frames) == 1
        assert frames[0] == data


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------

class TestCache:
    """Tests for cache_get() and cache_put()."""

    def test_round_trip(self, tmp_path: Path) -> None:
        """Data stored with cache_put() can be retrieved with cache_get()."""
        import cache as cache_mod
        # Create a temporary "video" file
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake video data")

        # Override CACHE_DIR to use tmp_path
        old_dir = cache_mod.CACHE_DIR
        cache_mod.CACHE_DIR = str(tmp_path / "cache")
        try:
            thumb_w, thumb_h, count = 80, 42, 5
            data = b'\x42' * (thumb_w * thumb_h * 3 * count)
            cache_mod.cache_put(str(video), thumb_w, thumb_h, count, data)

            result = cache_mod.cache_get(str(video), thumb_h, count)
            assert result is not None
            w, h, n, retrieved = result
            assert w == thumb_w
            assert h == thumb_h
            assert n == count
            assert retrieved == data
        finally:
            cache_mod.CACHE_DIR = old_dir

    def test_cache_miss(self, tmp_path: Path) -> None:
        """cache_get() returns None for uncached video."""
        import cache as cache_mod
        old_dir = cache_mod.CACHE_DIR
        cache_mod.CACHE_DIR = str(tmp_path / "cache")
        try:
            video = tmp_path / "test.mp4"
            video.write_bytes(b"fake")
            result = cache_mod.cache_get(str(video), 42, 5)
            assert result is None
        finally:
            cache_mod.CACHE_DIR = old_dir


# ---------------------------------------------------------------------------
# C extension tests
# ---------------------------------------------------------------------------

class TestNativeExtension:
    """Tests for the C-accelerated pixel helpers."""

    def test_split_strip_matches_python(self) -> None:
        """C split_strip_bgra produces same output as Python version."""
        try:
            from _native import split_strip_bgra as c_split
            from _native import _find_or_build_lib
            if _find_or_build_lib() is None:
                pytest.skip("C extension not available")
        except ImportError:
            pytest.skip("C extension not importable")

        thumb_w, thumb_h, count = 8, 4, 3
        strip_w = thumb_w * count
        import random
        random.seed(42)
        strip = bytes(random.getrandbits(8) for _ in range(strip_w * thumb_h * 4))

        c_frames = c_split(strip, thumb_w, thumb_h, count)

        # Verify via Python reference implementation
        py_frames = extraction_mod._split_strip_bgra.__wrapped__(strip, thumb_w, thumb_h, count) \
            if hasattr(extraction_mod._split_strip_bgra, '__wrapped__') \
            else _python_split_strip(strip, thumb_w, thumb_h, count)

        assert len(c_frames) == count
        for i in range(count):
            assert c_frames[i] == py_frames[i], f"Frame {i} mismatch"

    def test_bgra_to_rgb(self) -> None:
        """C bgra_to_rgb produces correct RGB output."""
        try:
            from _native import bgra_to_rgb as c_convert
            from _native import _find_or_build_lib
            if _find_or_build_lib() is None:
                pytest.skip("C extension not available")
        except ImportError:
            pytest.skip("C extension not importable")

        # BGRA: B=0x10, G=0x20, R=0x30, A=0xFF
        bgra = bytes([0x10, 0x20, 0x30, 0xFF, 0x40, 0x50, 0x60, 0x80])
        rgb = c_convert(bgra, 2)
        # Expected RGB: R=0x30, G=0x20, B=0x10, R=0x60, G=0x50, B=0x40
        assert rgb == bytes([0x30, 0x20, 0x10, 0x60, 0x50, 0x40])


def _python_split_strip(strip_buf, thumb_w, thumb_h, count):
    """Pure Python reference for split verification."""
    frame_bytes = thumb_w * thumb_h * 4
    row_bytes = thumb_w * count * 4
    frames = [bytearray(frame_bytes) for _ in range(count)]
    mv = memoryview(strip_buf)
    for y in range(thumb_h):
        row_start = y * row_bytes
        for i in range(count):
            src_off = row_start + i * thumb_w * 4
            dst_off = y * thumb_w * 4
            frames[i][dst_off:dst_off + thumb_w * 4] = mv[src_off:src_off + thumb_w * 4]
    return [bytes(f) for f in frames]


# ---------------------------------------------------------------------------
# Tests for new C-accelerated functions
# ---------------------------------------------------------------------------

def _skip_if_no_c():
    """Skip the test if the C extension is not available."""
    try:
        from _native import _find_or_build_lib
        if _find_or_build_lib() is None:
            pytest.skip("C extension not available")
    except ImportError:
        pytest.skip("C extension not importable")


class TestSplitStripBgraToRgb:
    """Tests for _split_strip_bgra_to_rgb() — combined split+convert."""

    def test_correct_output_size(self) -> None:
        """Output size is thumb_w * thumb_h * 3 * count."""
        thumb_w, thumb_h, count = 4, 2, 3
        strip_w = thumb_w * count
        strip = bytes([0x10, 0x20, 0x30, 0xFF] * (strip_w * thumb_h))
        rgb = _split_strip_bgra_to_rgb(strip, thumb_w, thumb_h, count)
        assert len(rgb) == thumb_w * thumb_h * 3 * count

    def test_matches_separate_split_and_convert(self) -> None:
        """Combined function produces same RGB as split + bgra_to_rgb."""
        try:
            from _native import bgra_to_rgb as c_bgra
        except ImportError:
            c_bgra = None

        thumb_w, thumb_h, count = 4, 3, 2
        strip_w = thumb_w * count
        import random
        random.seed(99)
        strip = bytes(random.getrandbits(8) for _ in range(strip_w * thumb_h * 4))

        # Combined path
        combined_rgb = _split_strip_bgra_to_rgb(strip, thumb_w, thumb_h, count)

        # Separate path: split then convert each frame
        frames = _split_strip_bgra(strip, thumb_w, thumb_h, count)
        separate_parts = []
        n_pixels = thumb_w * thumb_h
        for f in frames:
            if c_bgra is not None:
                separate_parts.append(c_bgra(f, n_pixels))
            else:
                # Pure Python convert
                mv = memoryview(f)
                rgb = bytearray(n_pixels * 3)
                for i in range(n_pixels):
                    s = i * 4
                    d = i * 3
                    rgb[d] = mv[s + 2]
                    rgb[d + 1] = mv[s + 1]
                    rgb[d + 2] = mv[s]
                separate_parts.append(bytes(rgb))
        separate_rgb = b"".join(separate_parts)

        assert combined_rgb == separate_rgb

    def test_single_frame(self) -> None:
        """Single-frame strip returns correctly converted RGB."""
        # BGRA pixel: B=0x10, G=0x20, R=0x30, A=0xFF
        thumb_w, thumb_h = 2, 2
        bgra_pixel = bytes([0x10, 0x20, 0x30, 0xFF])
        strip = bgra_pixel * (thumb_w * thumb_h)
        rgb = _split_strip_bgra_to_rgb(strip, thumb_w, thumb_h, 1)
        # Expected RGB: R=0x30, G=0x20, B=0x10 for each pixel
        expected_pixel = bytes([0x30, 0x20, 0x10])
        expected = expected_pixel * (thumb_w * thumb_h)
        assert rgb == expected


class TestSplitStripBgraToRgbNative:
    """Tests for the C implementation of split_strip_bgra_to_rgb."""

    def test_c_matches_python_random(self) -> None:
        """C split_strip_bgra_to_rgb matches Python fallback for random data."""
        _skip_if_no_c()
        from _native import split_strip_bgra_to_rgb as c_func

        thumb_w, thumb_h, count = 8, 6, 5
        strip_w = thumb_w * count
        import random
        random.seed(77)
        strip = bytes(random.getrandbits(8) for _ in range(strip_w * thumb_h * 4))

        c_rgb = c_func(strip, thumb_w, thumb_h, count)

        # Python reference: force the wrapper down its pure-Python fallback
        with patch.object(extraction_mod, "_c_split_strip_bgra_to_rgb", None):
            py_rgb = _split_strip_bgra_to_rgb(strip, thumb_w, thumb_h, count)

        assert c_rgb == py_rgb


class TestBgraToRgbMulti:
    """Tests for bgra_to_rgb_multi — batch BGRA→RGB conversion."""

    def test_matches_individual_calls(self) -> None:
        """Batch convert produces same result as N individual conversions."""
        _skip_if_no_c()
        from _native import bgra_to_rgb as c_single
        from _native import bgra_to_rgb_multi as c_multi

        pixels_per_frame = 16
        n_frames = 4
        import random
        random.seed(55)
        bgra = bytes(random.getrandbits(8)
                     for _ in range(pixels_per_frame * n_frames * 4))

        # Batch
        batch_rgb = c_multi(bgra, pixels_per_frame, n_frames)

        # Individual
        parts = []
        frame_bgra_sz = pixels_per_frame * 4
        for i in range(n_frames):
            part = bgra[i * frame_bgra_sz:(i + 1) * frame_bgra_sz]
            parts.append(c_single(part, pixels_per_frame))
        individual_rgb = b"".join(parts)

        assert batch_rgb == individual_rgb

    def test_output_size(self) -> None:
        """Output size is pixels_per_frame * n_frames * 3."""
        _skip_if_no_c()
        from _native import bgra_to_rgb_multi as c_multi
        bgra = bytes([0x10, 0x20, 0x30, 0xFF] * 8)
        rgb = c_multi(bgra, 4, 2)
        assert len(rgb) == 4 * 2 * 3


class TestRotateBgra:
    """Tests for rotate_bgra — BGRA rotation."""

    def _make_2x2_bgra(self):
        """Create a 2×2 BGRA image with distinct pixels.

        Layout (B, G, R, A):
          (0,0)=TL  (1,0)=TR
          (0,1)=BL  (1,1)=BR
        """
        TL = bytes([0x00, 0x00, 0xFF, 0xFF])  # red
        TR = bytes([0x00, 0xFF, 0x00, 0xFF])  # green
        BL = bytes([0xFF, 0x00, 0x00, 0xFF])  # blue
        BR = bytes([0xFF, 0xFF, 0xFF, 0xFF])  # white
        return TL + TR + BL + BR, TL, TR, BL, BR

    def _pixel_at(self, buf, w, x, y):
        off = (y * w + x) * 4
        return buf[off:off + 4]

    def test_no_rotation(self) -> None:
        """0° rotation returns identical buffer."""
        _skip_if_no_c()
        from _native import rotate_bgra
        img, TL, TR, BL, BR = self._make_2x2_bgra()
        out = rotate_bgra(img, 2, 2, degrees=0, vflip=False)
        assert out == img

    def test_rotation_180(self) -> None:
        """180° rotation swaps diagonally."""
        _skip_if_no_c()
        from _native import rotate_bgra
        img, TL, TR, BL, BR = self._make_2x2_bgra()
        out = rotate_bgra(img, 2, 2, degrees=180, vflip=False)
        # After 180: (0,0)=BR, (1,0)=BL, (0,1)=TR, (1,1)=TL
        assert self._pixel_at(out, 2, 0, 0) == BR
        assert self._pixel_at(out, 2, 1, 0) == BL
        assert self._pixel_at(out, 2, 0, 1) == TR
        assert self._pixel_at(out, 2, 1, 1) == TL

    def test_rotation_90(self) -> None:
        """90° CW rotation produces correct pixel mapping."""
        _skip_if_no_c()
        from _native import rotate_bgra
        img, TL, TR, BL, BR = self._make_2x2_bgra()
        out = rotate_bgra(img, 2, 2, degrees=90, vflip=False)
        # After 90° CW: dst is 2×2 (src_h × src_w = 2×2)
        # (0,0)=BL, (1,0)=TL, (0,1)=BR, (1,1)=TR
        assert self._pixel_at(out, 2, 0, 0) == BL
        assert self._pixel_at(out, 2, 1, 0) == TL
        assert self._pixel_at(out, 2, 0, 1) == BR
        assert self._pixel_at(out, 2, 1, 1) == TR

    def test_rotation_270(self) -> None:
        """270° CW rotation produces correct pixel mapping."""
        _skip_if_no_c()
        from _native import rotate_bgra
        img, TL, TR, BL, BR = self._make_2x2_bgra()
        out = rotate_bgra(img, 2, 2, degrees=270, vflip=False)
        # After 270° CW: dst is 2×2
        # (0,0)=TR, (1,0)=BR, (0,1)=TL, (1,1)=BL
        assert self._pixel_at(out, 2, 0, 0) == TR
        assert self._pixel_at(out, 2, 1, 0) == BR
        assert self._pixel_at(out, 2, 0, 1) == TL
        assert self._pixel_at(out, 2, 1, 1) == BL

    def test_vflip(self) -> None:
        """vflip swaps top and bottom rows."""
        _skip_if_no_c()
        from _native import rotate_bgra
        img, TL, TR, BL, BR = self._make_2x2_bgra()
        out = rotate_bgra(img, 2, 2, degrees=0, vflip=True)
        # After vflip: (0,0)=BL, (1,0)=BR, (0,1)=TL, (1,1)=TR
        assert self._pixel_at(out, 2, 0, 0) == BL
        assert self._pixel_at(out, 2, 1, 0) == BR
        assert self._pixel_at(out, 2, 0, 1) == TL
        assert self._pixel_at(out, 2, 1, 1) == TR

    def test_rectangular_rotation_90(self) -> None:
        """90° rotation of 3×2 image produces 2×3 output."""
        _skip_if_no_c()
        from _native import rotate_bgra
        # 3×2 image (3 wide, 2 tall)
        src_w, src_h = 3, 2
        img = bytes(range(src_w * src_h * 4))
        out = rotate_bgra(img, src_w, src_h, degrees=90, vflip=False)
        # Output should be src_h × src_w = 2 × 3
        assert len(out) == src_w * src_h * 4  # same total pixels


class TestSnapToKeyframesNative:
    """Tests for the C snap_to_keyframes function."""

    def test_matches_python_bisect(self) -> None:
        """C snap produces same results as Python bisect version."""
        _skip_if_no_c()
        from _native import snap_to_keyframes as c_snap

        keyframes = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
        targets = [0.5, 1.5, 3.0, 5.5, 7.0, 9.9, 11.0]

        c_result = c_snap(targets, keyframes)

        # Force Python fallback so comparison is meaningful
        with patch.object(extraction_mod, "_c_snap_to_keyframes", None):
            py_result = _snap_to_keyframes(targets, keyframes)

        assert len(c_result) == len(py_result)
        for (ci, ct), (pi, pt) in zip(c_result, py_result):
            assert ci == pi
            assert abs(ct - pt) < 1e-9, f"Mismatch at index {ci}: C={ct}, Py={pt}"

    def test_empty_keyframes(self) -> None:
        """Empty keyframes returns original targets."""
        _skip_if_no_c()
        from _native import snap_to_keyframes as c_snap

        targets = [1.0, 2.0, 3.0]
        result = c_snap(targets, [])
        assert result == [(0, 1.0), (1, 2.0), (2, 3.0)]

    def test_single_keyframe(self) -> None:
        """All targets snap to the single keyframe."""
        _skip_if_no_c()
        from _native import snap_to_keyframes as c_snap

        result = c_snap([0.5, 5.0, 100.0], [3.0])
        for _, t in result:
            assert t == 3.0


class TestScaleBilinearBgra:
    """Tests for scale_bilinear_bgra — bilinear downscaling."""

    def test_output_size(self) -> None:
        """Output has correct dimensions."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        src_w, src_h = 8, 6
        dst_w, dst_h = 4, 3
        src = bytes([128] * (src_w * src_h * 4))
        out = scale_bilinear_bgra(src, src_w, src_h, dst_w, dst_h)
        assert len(out) == dst_w * dst_h * 4

    def test_uniform_color_preserved(self) -> None:
        """Uniform color image downscales to same color."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        src_w, src_h = 10, 10
        dst_w, dst_h = 3, 3
        # All pixels (B=50, G=100, R=150, A=200)
        pixel = bytes([50, 100, 150, 200])
        src = pixel * (src_w * src_h)
        out = scale_bilinear_bgra(src, src_w, src_h, dst_w, dst_h)
        # Every output pixel should be the same color
        for i in range(dst_w * dst_h):
            p = out[i * 4:(i + 1) * 4]
            assert p == pixel, f"Pixel {i} mismatch: {list(p)}"

    def test_identity_scale(self) -> None:
        """Scaling to same size preserves pixels."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        src_w, src_h = 4, 4
        import random
        random.seed(33)
        src = bytes(random.getrandbits(8) for _ in range(src_w * src_h * 4))
        out = scale_bilinear_bgra(src, src_w, src_h, src_w, src_h)
        assert out == src

    def test_single_pixel_source(self) -> None:
        """1×1 source fills entire destination with the single pixel."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        pixel = bytes([10, 20, 30, 255])
        out = scale_bilinear_bgra(pixel, 1, 1, 3, 3)
        assert len(out) == 3 * 3 * 4
        for i in range(9):
            assert out[i * 4:(i + 1) * 4] == pixel

    def test_single_row_source(self) -> None:
        """1-pixel-tall source does not segfault and produces correct size."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        src_w = 4
        src = bytes(i % 256 for i in range(src_w * 4))
        out = scale_bilinear_bgra(src, src_w, 1, 2, 2)
        assert len(out) == 2 * 2 * 4

    def test_single_column_source(self) -> None:
        """1-pixel-wide source does not segfault and produces correct size."""
        _skip_if_no_c()
        from _native import scale_bilinear_bgra

        src_h = 4
        src = bytes(i % 256 for i in range(src_h * 4))
        out = scale_bilinear_bgra(src, 1, src_h, 2, 2)
        assert len(out) == 2 * 2 * 4

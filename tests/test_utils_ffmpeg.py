"""Tests for the lightweight ffmpeg helpers."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from iPhoto.utils import ffmpeg
from iPhoto.errors import ExternalToolError


def _fake_completed_process(command: list[str], stdout: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")


@pytest.fixture(autouse=True)
def _clear_rotation_probe_cache() -> None:
    ffmpeg._probe_video_rotation_info_cached.cache_clear()
    ffmpeg._LINUX_180_HINT_CACHE.clear()


# -----------------------------------------------------------------------
# probe_video_rotation
# -----------------------------------------------------------------------


class TestProbeVideoRotation:
    """Tests for ``probe_video_rotation``."""

    def test_returns_rotation_from_display_matrix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Display Matrix side data with -90° (CCW) → 90° CW."""

        video = tmp_path / "portrait.mov"

        def fake_probe(src: Path) -> dict:
            assert src == video
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1440,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -90,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, raw_w, raw_h = ffmpeg.probe_video_rotation(video)
        assert cw == 90
        assert raw_w == 1920
        assert raw_h == 1440

    def test_returns_zero_for_no_rotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A video without display matrix returns 0."""

        video = tmp_path / "landscape.mp4"

        def fake_probe(src: Path) -> dict:
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, raw_w, raw_h = ffmpeg.probe_video_rotation(video)
        assert cw == 0
        assert raw_w == 1920
        assert raw_h == 1080

    def test_returns_zero_on_ffprobe_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When ffprobe is unavailable, return (0, 0, 0)."""

        video = tmp_path / "broken.mp4"

        def failing_probe(src: Path) -> dict:
            raise ExternalToolError("ffprobe not found")

        monkeypatch.setattr(ffmpeg, "probe_media", failing_probe)

        assert ffmpeg.probe_video_rotation(video) == (0, 0, 0)

    def test_handles_180_rotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Display Matrix with 180° → 180° CW."""

        video = tmp_path / "upside_down.mp4"

        def fake_probe(src: Path) -> dict:
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": 180,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, _, _ = ffmpeg.probe_video_rotation(video)
        assert cw == 180

    def test_handles_90_cw_rotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Display Matrix rotation value 90 (= 90° CCW) → 270° CW."""

        video = tmp_path / "rotated.mp4"

        def fake_probe(src: Path) -> dict:
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1080,
                        "height": 1920,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": 90,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, _, _ = ffmpeg.probe_video_rotation(video)
        assert cw == 270

    def test_skips_non_video_streams(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Audio streams are ignored; first video stream is used."""

        video = tmp_path / "clip.mov"

        def fake_probe(src: Path) -> dict:
            return {
                "streams": [
                    {"codec_type": "audio", "codec_name": "aac"},
                    {
                        "codec_type": "video",
                        "width": 3840,
                        "height": 2160,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -90,
                            }
                        ],
                    },
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, raw_w, raw_h = ffmpeg.probe_video_rotation(video)
        assert cw == 90
        assert raw_w == 3840
        assert raw_h == 2160

    def test_returns_zero_for_empty_streams(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No streams at all returns (0, 0, 0)."""

        video = tmp_path / "empty.mp4"

        def fake_probe(src: Path) -> dict:
            return {"streams": []}

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        assert ffmpeg.probe_video_rotation(video) == (0, 0, 0)

    def test_snaps_near_90_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A non-exact rotation (e.g. -89.9°) snaps to 90° CW."""

        video = tmp_path / "nearly.mp4"

        def fake_probe(src: Path) -> dict:
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -89.9,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, _, _ = ffmpeg.probe_video_rotation(video)
        assert cw == 90

    def test_probe_video_rotation_info_sets_quicktime_180_hint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """QuickTime/Core Media 180° streams should surface Linux hint."""

        video = tmp_path / "iphone.mov"

        def fake_probe(src: Path) -> dict:
            return {
                "format": {"tags": {"major_brand": "qt  "}},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1280,
                        "height": 720,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -180,
                            }
                        ],
                        "tags": {"handler_name": "Core Media Video"},
                    }
                ],
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, raw_w, raw_h, linux_180_hint = ffmpeg.probe_video_rotation_info(video)
        assert (cw, raw_w, raw_h) == (180, 1280, 720)
        assert linux_180_hint is True

    def test_probe_video_rotation_info_180_without_apple_metadata_has_no_hint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-QuickTime 180° streams should not set Linux pre-rotation hint."""

        video = tmp_path / "generic.mp4"

        def fake_probe(src: Path) -> dict:
            return {
                "format": {"tags": {"major_brand": "mp42"}},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": 180,
                            }
                        ],
                        "tags": {"handler_name": "VideoHandler"},
                    }
                ],
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        cw, raw_w, raw_h, linux_180_hint = ffmpeg.probe_video_rotation_info(video)
        assert (cw, raw_w, raw_h) == (180, 1920, 1080)
        assert linux_180_hint is False

    def test_probe_video_rotation_info_reuses_cached_result_for_unchanged_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Repeated probes for the same file should avoid extra ffprobe calls."""

        video = tmp_path / "cached.mov"
        video.write_bytes(b"one")
        calls: list[Path] = []

        def fake_probe(src: Path) -> dict:
            calls.append(src)
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": -90,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        assert ffmpeg.probe_video_rotation_info(video) == (90, 1920, 1080, False)
        assert ffmpeg.probe_video_rotation_info(video) == (90, 1920, 1080, False)
        assert calls == [video.resolve()]

    def test_probe_video_rotation_info_cache_invalidates_after_file_change(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Changing file metadata should force a fresh ffprobe inspection."""

        video = tmp_path / "updated.mov"
        video.write_bytes(b"one")
        call_count = 0

        def fake_probe(src: Path) -> dict:
            nonlocal call_count
            call_count += 1
            rotation = -90 if call_count == 1 else 180
            return {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {
                                "side_data_type": "Display Matrix",
                                "rotation": rotation,
                            }
                        ],
                    }
                ]
            }

        monkeypatch.setattr(ffmpeg, "probe_media", fake_probe)

        assert ffmpeg.probe_video_rotation_info(video) == (90, 1920, 1080, False)
        video.write_bytes(b"updated-content")
        assert ffmpeg.probe_video_rotation_info(video) == (180, 1920, 1080, False)
        assert call_count == 2


def test_extract_video_frame_uses_yuv_format_for_jpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure JPEG extractions request a YUV pixel format."""

    input_path = tmp_path / "movie.mp4"
    input_path.touch()
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = command
        return _fake_completed_process(command, stdout=b"jpeg")

    monkeypatch.setattr(ffmpeg, "_run_command", fake_run)

    data = ffmpeg.extract_video_frame(input_path, at=0.5, scale=(320, 240), format="jpeg")

    assert data == b"jpeg"
    assert "cmd" in captured
    command = captured["cmd"]

    # Check for hardware acceleration
    assert "-hwaccel" in command
    assert "auto" in command

    # Check for pipe output
    assert "pipe:1" in command

    assert "-vf" in command
    vf_index = command.index("-vf")
    vf_expression = command[vf_index + 1]
    assert "format=yuv420p" in vf_expression
    assert "scale='min(320,iw)':'min(240,ih)':force_original_aspect_ratio=decrease" in vf_expression
    assert "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'" in vf_expression
    assert "format=rgba" not in vf_expression


def test_extract_video_frame_uses_rgba_for_png(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PNG extractions keep the alpha channel when available."""

    input_path = tmp_path / "movie.mov"
    input_path.touch()
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = command
        return _fake_completed_process(command, stdout=b"png")

    monkeypatch.setattr(ffmpeg, "_run_command", fake_run)

    data = ffmpeg.extract_video_frame(input_path, at=None, scale=None, format="png")

    assert data == b"png"
    assert "cmd" in captured
    command = captured["cmd"]

    # Check for hardware acceleration
    assert "-hwaccel" in command
    assert "auto" in command

    # Check for pipe output
    assert "pipe:1" in command

    assert "-vf" in command
    vf_index = command.index("-vf")
    vf_expression = command[vf_index + 1]
    assert "format=rgba" in vf_expression
    assert "format=yuv420p" not in vf_expression
    assert "scale=max(2,trunc(iw/2)*2):max(2,trunc(ih/2)*2)" not in vf_expression


def test_extract_video_frame_without_scale_enforces_even_dimensions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """JPEG requests without scaling still force even-sized frames."""

    input_path = tmp_path / "clip.mp4"
    input_path.touch()
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = command
        return _fake_completed_process(command, stdout=b"jpeg")

    monkeypatch.setattr(ffmpeg, "_run_command", fake_run)

    data = ffmpeg.extract_video_frame(input_path, at=None, scale=None, format="jpeg")

    assert data == b"jpeg"
    assert "cmd" in captured
    command = captured["cmd"]
    assert "-vf" in command
    vf_index = command.index("-vf")
    vf_expression = command[vf_index + 1]
    assert "scale=iw:ih" in vf_expression
    assert "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'" in vf_expression


def test_extract_video_frame_falls_back_to_opencv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When ffmpeg fails, OpenCV fallback results are returned if available."""

    input_path = tmp_path / "clip.mov"
    input_path.touch()

    def fake_ffmpeg(*args: object, **kwargs: object) -> bytes:
        raise ExternalToolError("boom")

    fallback_data = b"opencv"

    def fake_opencv(*args: object, **kwargs: object) -> bytes:
        return fallback_data

    monkeypatch.setattr(ffmpeg, "_extract_with_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(ffmpeg, "_extract_with_opencv", fake_opencv)

    data = ffmpeg.extract_video_frame(input_path, at=0.1, scale=(100, 100), format="jpeg")

    assert data is fallback_data


def test_extract_video_frame_propagates_error_when_no_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without OpenCV, the original ffmpeg error is raised."""

    input_path = tmp_path / "clip.mov"
    input_path.touch()

    def fake_ffmpeg(*args: object, **kwargs: object) -> bytes:
        raise ExternalToolError("missing tool")

    monkeypatch.setattr(ffmpeg, "_extract_with_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(ffmpeg, "_extract_with_opencv", lambda *args, **kwargs: None)

    with pytest.raises(ExternalToolError):
        ffmpeg.extract_video_frame(input_path, format="jpeg")


def test_extract_with_opencv_scales_and_encodes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The OpenCV helper rescales frames and encodes JPEG output."""

    input_path = tmp_path / "video.mp4"
    input_path.touch()

    class FakeBuffer:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __bytes__(self) -> bytes:
            return self._data

        def tobytes(self) -> bytes:  # pragma: no cover - compatibility shim
            return self._data

    class FakeFrame:
        def __init__(self, width: int, height: int) -> None:
            self.shape = (height, width, 3)
            self.width = width
            self.height = height

    class FakeCapture:
        def __init__(self) -> None:
            self.set_calls: list[tuple[int, float]] = []
            self.released = False

        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: float) -> bool:
            self.set_calls.append((prop, value))
            return True

        def get(self, prop: int) -> float:
            return 24.0 if prop == 1 else 0.0

        def read(self) -> tuple[bool, FakeFrame]:
            return True, FakeFrame(7, 5)

        def release(self) -> None:
            self.released = True

    capture = FakeCapture()
    resize_calls: list[tuple[int, int]] = []
    encode_calls: list[tuple[str, tuple[int, int], list[int]]] = []

    class FakeCV2:
        CAP_PROP_POS_MSEC = 0
        CAP_PROP_FPS = 1
        CAP_PROP_POS_FRAMES = 2
        INTER_AREA = 3
        IMWRITE_JPEG_QUALITY = 4

        @staticmethod
        def VideoCapture(path: str) -> FakeCapture:  # type: ignore[override]
            assert path == str(input_path)
            return capture

        @staticmethod
        def resize(frame: FakeFrame, size: tuple[int, int], interpolation: int) -> FakeFrame:
            resize_calls.append(size)
            return FakeFrame(size[0], size[1])

        @staticmethod
        def imencode(ext: str, frame: FakeFrame, params: list[int]) -> tuple[bool, FakeBuffer]:
            encode_calls.append((ext, frame.shape[:2], params))
            return True, FakeBuffer(b"encoded")

    monkeypatch.setattr(ffmpeg, "cv2", FakeCV2)

    data = ffmpeg._extract_with_opencv(input_path, at=0.5, scale=(4, 4), format="jpeg")

    assert data == b"encoded"
    assert len(resize_calls) == 1
    resized_width, resized_height = resize_calls[0]
    assert resized_width <= 4 and resized_height <= 4
    assert resized_width % 2 == 0 and resized_height % 2 == 0
    assert encode_calls == [
        (".jpg", (resized_height, resized_width), [FakeCV2.IMWRITE_JPEG_QUALITY, 92])
    ]
    assert capture.set_calls[0][0] == FakeCV2.CAP_PROP_POS_MSEC
    assert capture.released is True


def test_extract_with_opencv_applies_display_rotation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OpenCV fallback should rotate decoded frames using display-matrix metadata."""

    input_path = tmp_path / "video.mp4"
    input_path.touch()

    class FakeFrame:
        def __init__(self, width: int, height: int) -> None:
            self.shape = (height, width, 3)

    class FakeCapture:
        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: float) -> bool:
            return True

        def read(self) -> tuple[bool, FakeFrame]:
            return True, FakeFrame(160, 90)

        def release(self) -> None:
            return None

    rotate_calls: list[int] = []
    encoded_shapes: list[tuple[int, int]] = []

    class FakeCV2:
        CAP_PROP_POS_MSEC = 0
        CAP_PROP_FPS = 1
        CAP_PROP_POS_FRAMES = 2
        INTER_AREA = 3
        IMWRITE_JPEG_QUALITY = 4
        ROTATE_90_CLOCKWISE = 10

        @staticmethod
        def VideoCapture(path: str) -> FakeCapture:  # type: ignore[override]
            assert path == str(input_path)
            return FakeCapture()

        @staticmethod
        def rotate(frame: FakeFrame, flag: int) -> FakeFrame:
            rotate_calls.append(flag)
            assert flag == FakeCV2.ROTATE_90_CLOCKWISE
            h, w = frame.shape[:2]
            return FakeFrame(h, w)

        @staticmethod
        def imencode(ext: str, frame: FakeFrame, params: list[int]) -> tuple[bool, object]:
            encoded_shapes.append(frame.shape[:2])

            class _Buffer:
                def __bytes__(self) -> bytes:
                    return b"encoded"

            return True, _Buffer()

    monkeypatch.setattr(ffmpeg, "cv2", FakeCV2)
    monkeypatch.setattr(
        ffmpeg,
        "probe_video_rotation_info",
        lambda src: (90, 160, 90, False) if src == input_path else (0, 0, 0, False),
    )

    data = ffmpeg._extract_with_opencv(input_path, at=None, scale=None, format="jpeg")

    assert data == b"encoded"
    assert rotate_calls == [FakeCV2.ROTATE_90_CLOCKWISE]
    assert encoded_shapes == [(160, 90)]

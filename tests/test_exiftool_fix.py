import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.errors import ExternalToolError
from iPhoto.utils import exiftool
from iPhoto.utils.exiftool import get_metadata_batch, write_gps_metadata


def _make_executable(path: Path) -> Path:
    candidate = path
    if os.name == "nt" and not candidate.suffix:
        candidate = candidate.with_suffix(".exe")
        candidate.write_bytes(b"MZ")
    else:
        candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    candidate.chmod(0o755)
    return candidate


def test_resolve_exiftool_prefers_explicit_env_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _make_executable(tmp_path / "custom-exiftool")
    monkeypatch.setenv("IPHOTO_EXIFTOOL_PATH", str(configured))
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")

    assert exiftool._resolve_exiftool_executable() == str(configured)


def test_resolve_exiftool_prefers_path_before_macos_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = _make_executable(tmp_path / "fallback-exiftool")
    monkeypatch.delenv("IPHOTO_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr(exiftool.sys, "platform", "darwin")
    monkeypatch.setattr(exiftool, "_MACOS_EXIFTOOL_CANDIDATES", (fallback,))
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/custom/bin/exiftool")

    assert exiftool._resolve_exiftool_executable() == "/custom/bin/exiftool"


def test_resolve_exiftool_uses_macos_fallback_with_minimal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = _make_executable(tmp_path / "homebrew-exiftool")
    monkeypatch.delenv("IPHOTO_EXIFTOOL_PATH", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setattr(exiftool.sys, "platform", "darwin")
    monkeypatch.setattr(exiftool, "_MACOS_EXIFTOOL_CANDIDATES", (fallback,))
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: None)

    assert exiftool._resolve_exiftool_executable() == str(fallback)


def test_resolve_exiftool_uses_bundled_fallback_after_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = _make_executable(tmp_path / "exiftool.exe")
    monkeypatch.delenv("IPHOTO_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr(exiftool.sys, "platform", "win32")
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(exiftool, "_bundled_exiftool_candidates", lambda: (bundled,))

    assert exiftool._resolve_exiftool_executable() == str(bundled)


def test_resolve_exiftool_error_includes_search_locations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-exiftool"
    monkeypatch.delenv("IPHOTO_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr(exiftool.sys, "platform", "darwin")
    monkeypatch.setattr(exiftool, "_MACOS_EXIFTOOL_CANDIDATES", (missing,))
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: None)

    with pytest.raises(ExternalToolError) as exc_info:
        exiftool._resolve_exiftool_executable()

    message = str(exc_info.value)
    assert "PATH" in message
    assert str(missing) in message
    assert "IPHOTO_EXIFTOOL_PATH" in message


def test_resolve_exiftool_invalid_env_path_stops_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "not-executable"
    invalid.write_text("", encoding="utf-8")
    monkeypatch.setenv("IPHOTO_EXIFTOOL_PATH", str(invalid))
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")

    with pytest.raises(ExternalToolError) as exc_info:
        exiftool._resolve_exiftool_executable()

    assert str(invalid) in str(exc_info.value)


def test_get_metadata_batch_uses_posix_paths():
    """Verify that paths are written to the argument file using POSIX style (forward slashes)."""

    # Create a mock path that behaves like a Windows path
    mock_path = MagicMock(spec=Path)
    mock_path.__str__.return_value = "D:\\folder\\file.jpg"
    mock_path.as_posix.return_value = "D:/folder/file.jpg"
    # The code calls path.absolute().as_posix(), so chain the mock properly
    mock_absolute = MagicMock()
    mock_absolute.as_posix.return_value = "D:/folder/file.jpg"
    mock_path.absolute.return_value = mock_absolute

    # We need to ensure that the temp file content is checked before it is deleted.
    # We can do this by inspecting the file inside the mock for subprocess.run

    def check_arg_file(*args, **kwargs):
        # The command is the first argument
        cmd = args[0]
        # The last argument is the path to the temp file
        arg_file_path = cmd[-1]

        with open(arg_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # This is where we verify the fix.
        # If the code uses str(path), it will be "D:\\folder\\file.jpg"
        # If the code uses path.as_posix(), it will be "D:/folder/file.jpg"
        if "D:/folder/file.jpg" not in content:
            raise AssertionError(f"Argument file content incorrect. Found:\n{content}")

        if "D:\\folder\\file.jpg" in content:
            raise AssertionError(f"Argument file contains backslashes. Found:\n{content}")

        return subprocess.CompletedProcess(args, 0, stdout=b"[]", stderr=b"")

    with (
        patch("shutil.which", return_value="/usr/bin/exiftool"),
        patch("subprocess.run", side_effect=check_arg_file) as mock_run,
    ):
        get_metadata_batch([mock_path])

        assert mock_run.called


def test_write_gps_metadata_rejects_missing_source_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")

    with pytest.raises(ExternalToolError) as exc_info:
        write_gps_metadata(
            tmp_path / "missing.jpg",
            latitude=48.137154,
            longitude=11.576124,
            is_video=False,
        )

    assert "Source media file is unavailable" in str(exc_info.value)


def test_write_gps_metadata_passes_source_file_via_arg_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "photo with spaces.jpg"
    asset.write_bytes(b"jpeg-data")
    seen_arg_file = None

    def fake_run(cmd, **kwargs):
        nonlocal seen_arg_file
        assert "-@" in cmd
        seen_arg_file = cmd[cmd.index("-@") + 1]
        with open(seen_arg_file, encoding="utf-8") as handle:
            assert handle.read().strip() == asset.resolve().as_posix()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")
    monkeypatch.setattr(exiftool.subprocess, "run", fake_run)

    write_gps_metadata(
        asset,
        latitude=48.137154,
        longitude=11.576124,
        is_video=False,
    )

    assert seen_arg_file is not None
    assert not Path(seen_arg_file).exists()


def test_write_gps_metadata_writes_explicit_quicktime_tags_for_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "clip.mov"
    asset.write_bytes(b"video-data")
    seen_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        seen_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")
    monkeypatch.setattr(exiftool.subprocess, "run", fake_run)

    write_gps_metadata(
        asset,
        latitude=48.137154,
        longitude=11.576124,
        is_video=True,
    )

    assert "-Keys:GPSCoordinates=+48.137154+11.576124/" in seen_cmd
    assert "-ItemList:GPSCoordinates=+48.137154+11.576124/" in seen_cmd
    assert "-QuickTime:GPSCoordinates=+48.137154+11.576124/" in seen_cmd
    assert "-XMP:GPSLatitude=48.13715400" in seen_cmd
    assert "-XMP:GPSLongitude=11.57612400" in seen_cmd


def test_write_gps_metadata_retries_rename_failure_with_in_place_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "clip.mov"
    asset.write_bytes(b"video-data")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(
                1,
                cmd,
                stderr=f"Error renaming temporary file to {asset}",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")
    monkeypatch.setattr(exiftool.subprocess, "run", fake_run)
    monkeypatch.setattr(exiftool.time, "sleep", lambda _seconds: None)

    write_gps_metadata(
        asset,
        latitude=48.137154,
        longitude=11.576124,
        is_video=False,
    )

    assert "-overwrite_original" in calls[0]
    assert "-overwrite_original_in_place" in calls[1]


def test_write_gps_metadata_retries_no_matching_files_when_source_still_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "clip.mov"
    asset.write_bytes(b"video-data")
    calls = 0

    def fake_run(cmd, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.CalledProcessError(1, cmd, stderr="No matching files")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(exiftool.shutil, "which", lambda _name: "/usr/bin/exiftool")
    monkeypatch.setattr(exiftool.subprocess, "run", fake_run)
    monkeypatch.setattr(exiftool.time, "sleep", lambda _seconds: None)

    write_gps_metadata(
        asset,
        latitude=48.137154,
        longitude=11.576124,
        is_video=True,
    )

    assert calls == 2

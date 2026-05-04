import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.errors import ExternalToolError
from iPhoto.utils import exiftool
from iPhoto.utils.exiftool import get_metadata_batch


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

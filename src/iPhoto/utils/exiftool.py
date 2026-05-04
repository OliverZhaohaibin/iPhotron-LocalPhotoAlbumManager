"""Helpers for invoking the :command:`exiftool` CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
import logging

from ..errors import ExternalToolError

LOGGER = logging.getLogger(__name__)

_EXIFTOOL_ENV_VAR = "IPHOTO_EXIFTOOL_PATH"
_MACOS_EXIFTOOL_CANDIDATES = (
    Path("/opt/homebrew/bin/exiftool"),
    Path("/usr/local/bin/exiftool"),
    Path("/opt/local/bin/exiftool"),
)


def _windows_executable_suffixes() -> tuple[str, ...]:
    raw_value = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    suffixes = tuple(
        entry.strip().lower()
        for entry in raw_value.split(os.pathsep)
        if entry.strip()
    )
    if suffixes:
        return suffixes
    return (".com", ".exe", ".bat", ".cmd")


def _is_executable_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if os.name == "nt":
            return path.suffix.lower() in _windows_executable_suffixes()
        return os.access(path, os.X_OK)
    except OSError:
        return False


def _resolve_exiftool_executable() -> str:
    configured = os.environ.get(_EXIFTOOL_ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if _is_executable_file(candidate):
            return str(candidate)
        raise ExternalToolError(
            f"Configured exiftool executable is not available or executable: {candidate}. "
            f"Set {_EXIFTOOL_ENV_VAR} to a valid executable path."
        )

    executable = shutil.which("exiftool")
    if executable:
        return executable

    searched = ["PATH"]
    if sys.platform == "darwin":
        for candidate in _MACOS_EXIFTOOL_CANDIDATES:
            searched.append(str(candidate))
            if _is_executable_file(candidate):
                return str(candidate)

    raise ExternalToolError(
        "exiftool executable not found. Install it from https://exiftool.org/ "
        f"and ensure it is available on PATH, or set {_EXIFTOOL_ENV_VAR}. "
        f"Searched: {', '.join(searched)}."
    )


def _startup_options() -> tuple[subprocess.STARTUPINFO | None, int]:
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _safe_exiftool_path(path: Path) -> str:
    safe_path = path.absolute()
    path_str = safe_path.as_posix()
    if "\n" in path_str or "\r" in path_str:
        raise ExternalToolError(f"Unsafe newline characters in path: {path_str!r}")
    return path_str


def _run_exiftool_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    startupinfo, creationflags = _startup_options()
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(f"Failed to execute exiftool (FileNotFoundError): {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown error"
        raise ExternalToolError(f"ExifTool failed with an error: {stderr}") from exc


def _format_iso6709(latitude: float, longitude: float) -> str:
    return f"{latitude:+.6f}{longitude:+.6f}/"


def get_metadata_batch(paths: list[Path]) -> list[dict[str, Any]]:
    """Return metadata for *paths* by launching a single ``exiftool`` process."""

    executable = _resolve_exiftool_executable()
    if not paths:
        return []

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp_arg_file:
        for path in paths:
            try:
                tmp_arg_file.write(_safe_exiftool_path(path) + "\n")
            except ExternalToolError:
                LOGGER.warning("Skipping file with unsafe characters in path: %s", path)
                continue
        tmp_arg_path = tmp_arg_file.name

    try:
        cmd = [
            executable,
            "-n",
            "-g1",
            "-json",
            "-charset",
            "filename=utf8",
            "-@",
            tmp_arg_path,
        ]
        try:
            process = _run_exiftool_command(cmd)
            return json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise ExternalToolError(f"Failed to parse JSON output from ExifTool: {exc}") from exc
    except ExternalToolError as exc:
        message = str(exc)
        if "image files read" in message.lower():
            return []
        raise
    finally:
        try:
            os.remove(tmp_arg_path)
        except OSError:
            pass


def write_gps_metadata(
    path: Path,
    *,
    latitude: float,
    longitude: float,
    is_video: bool,
) -> None:
    """Write GPS metadata in-place without generating a sidecar file."""

    executable = _resolve_exiftool_executable()
    safe_path = _safe_exiftool_path(path)
    cmd = [
        executable,
        "-overwrite_original",
        "-charset",
        "filename=utf8",
    ]
    if is_video:
        iso6709 = _format_iso6709(latitude, longitude)
        cmd.append(f"-GPSCoordinates={iso6709}")
    else:
        cmd.extend(
            [
                f"-GPSLatitude={abs(float(latitude)):.8f}",
                f"-GPSLatitudeRef={'S' if latitude < 0 else 'N'}",
                f"-GPSLongitude={abs(float(longitude)):.8f}",
                f"-GPSLongitudeRef={'W' if longitude < 0 else 'E'}",
            ]
        )
    cmd.append(safe_path)
    _run_exiftool_command(cmd)


__all__ = ["get_metadata_batch", "write_gps_metadata"]

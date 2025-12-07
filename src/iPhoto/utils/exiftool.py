"""Batch-oriented helpers for invoking the :command:`exiftool` CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..errors import ExternalToolError


def get_metadata_batch(paths: List[Path]) -> List[Dict[str, Any]]:
    """Return metadata for *paths* by launching a single ``exiftool`` process.

    We use an argument file (via ``-@``) to pass the file list to ``exiftool``.
    This bypasses command-line length limits (especially on Windows) and ensures
    correct handling of non-ASCII filenames by explicitly specifying the charset.

    Parameters
    ----------
    paths:
        The media files that should be inspected.  Passing an empty list returns
        an empty list immediately.

    Raises
    ------
    ExternalToolError
        Raised when the ``exiftool`` executable is missing or when the command
        exits with a non-zero status code.
    """

    executable = shutil.which("exiftool")
    if executable is None:
        raise ExternalToolError(
            "exiftool executable not found. Install it from https://exiftool.org/ "
            "and ensure it is available on PATH."
        )

    if not paths:
        return []

    # Create a temporary argument file
    # We use delete=False to close it before passing to subprocess (Windows lock safety),
    # then manually delete it.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp_arg_file:
        for path in paths:
            # Always use POSIX paths (forward slashes) for ExifTool argument files.
            # This avoids issues with backslashes on Windows, which ExifTool might
            # misinterpret as escape sequences or wildcards when combined with
            # certain non-ASCII characters.
            tmp_arg_file.write(path.as_posix() + "\n")
        tmp_arg_path = tmp_arg_file.name

    try:
        cmd = [
            executable,
            "-n",  # emit numeric GPS values instead of DMS strings
            "-g1",  # keep group information (e.g. Composite, GPS) in the payload
            "-json",
            "-charset",
            "filename=utf8",  # Input filenames are UTF-8 (from arg file)
            "-@",
            tmp_arg_path,
        ]

        # Define startupinfo to hide the window on Windows
        startupinfo = None
        creationflags = 0
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

        try:
            # ``encoding`` forces Python to decode the JSON using UTF-8 even on
            # locales that default to a more restrictive codec such as ``cp1252``.
            # ``errors='replace'`` keeps the scan moving if unexpected byte
            # sequences appear in the metadata.
            process = subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                creationflags=creationflags,
            )

            try:
                return json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise ExternalToolError(f"Failed to parse JSON output from ExifTool: {exc}") from exc

        except FileNotFoundError as exc:
            # Since we checked shutil.which above, this likely means some OS limitation
            # was hit, rather than the executable missing.
            raise ExternalToolError(
                f"Failed to execute exiftool (FileNotFoundError): {exc}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "unknown error"
            # ExifTool reports a successful batch run with summary lines such as
            # ``"2 image files read"`` on stderr. Treat these runs as successful
            # so long as JSON payload is present, otherwise propagate the real
            # failure details to the caller.
            if "image files read" in stderr.lower() and exc.stdout:
                try:
                    return json.loads(exc.stdout)
                except json.JSONDecodeError as json_exc:  # pragma: no cover - defensive
                    raise ExternalToolError(
                        "Failed to parse JSON output from ExifTool: "
                        f"{json_exc}"
                    ) from json_exc

            # If we are here, it's a real failure
            raise ExternalToolError(f"ExifTool failed with an error: {stderr}") from exc

    finally:
        # Cleanup temporary argument file
        try:
            os.remove(tmp_arg_path)
        except OSError:
            pass


__all__ = ["get_metadata_batch"]

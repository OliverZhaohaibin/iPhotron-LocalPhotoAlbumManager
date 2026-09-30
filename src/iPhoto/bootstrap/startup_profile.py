"""Opt-in JSONL profiling for desktop time-to-first-frame diagnostics."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ENABLED = os.environ.get("IPHOTO_STARTUP_PROFILE", "").strip().lower() in _TRUE_VALUES
_STARTED_NS = time.perf_counter_ns()
_LOCK = Lock()
_CONTEXT_LOCK = Lock()
_CONTEXT: dict[str, Any] = {}
_SALT: bytes | None = None

_PATH_KEY_PARTS = ("path", "root", "directory", "database", "filename")
_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\t;,\]\[(){}]+")
_POSIX_PATH = re.compile(r"(?<![\w.])(?:~|/)[^\r\n\t;,\]\[(){}]+")


def enabled() -> bool:
    """Return whether startup profiling was enabled before process import."""

    return _ENABLED


def _state_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def _profile_path() -> Path:
    override = os.environ.get("IPHOTO_STARTUP_PROFILE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return _state_base() / "iPhoto" / "logs" / "startup.jsonl"


def _salt() -> bytes:
    """Return a per-install salt without exposing raw library paths in logs."""

    global _SALT
    if _SALT is not None:
        return _SALT
    configured = os.environ.get("IPHOTO_STARTUP_PROFILE_SALT", "").encode("utf-8")
    if configured:
        _SALT = configured
        return configured
    salt_path = _state_base() / "iPhoto" / "startup-profile.salt"
    try:
        salt_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            value = salt_path.read_bytes()
        except FileNotFoundError:
            value = secrets.token_bytes(32)
            try:
                descriptor = os.open(
                    salt_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                value = salt_path.read_bytes()
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(value)
        _SALT = value or secrets.token_bytes(32)
    except OSError:
        # A process-local salt still prevents a raw path leak on read-only systems.
        _SALT = secrets.token_bytes(32)
    return _SALT


def stable_id(value: object) -> str:
    """Return a stable, installation-local identifier for sensitive values."""

    digest = hmac.new(_salt(), os.fspath(value).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


def _sanitize_text(value: str) -> str:
    value = _WINDOWS_PATH.sub("<redacted-path>", value)
    return _POSIX_PATH.sub("<redacted-path>", value)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return f"id:{stable_id(value)}"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return _sanitize_details(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value))


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, value in details.items():
        key = str(raw_key)
        normalized = key.casefold()
        if any(part in normalized for part in _PATH_KEY_PARTS):
            identifier_key = "library_id" if normalized in {"root", "library_root"} else f"{key}_id"
            sanitized[identifier_key] = stable_id(value)
            continue
        sanitized[key] = _sanitize_value(value)
    return sanitized


def configure(**metadata: Any) -> None:
    """Add non-sensitive metadata to all subsequent startup records."""

    clean = _sanitize_details({key: value for key, value in metadata.items() if value is not None})
    with _CONTEXT_LOCK:
        _CONTEXT.update(clean)


def _default_context() -> dict[str, Any]:
    runtime = (
        "packaged" if getattr(sys, "frozen", False) or "__compiled__" in globals() else "source"
    )
    return {
        "run_id": os.environ.get("IPHOTO_STARTUP_RUN_ID", ""),
        "revision": os.environ.get("IPHOTO_STARTUP_REVISION", "unknown"),
        "runtime": os.environ.get("IPHOTO_STARTUP_RUNTIME", runtime),
        "platform": sys.platform,
        "architecture": platform.machine() or "unknown",
        "qt_backend": os.environ.get("QT_QPA_PLATFORM", "unknown"),
        "graphics_backend": os.environ.get("IPHOTO_STARTUP_GRAPHICS_BACKEND", "default"),
        "cache_state": os.environ.get("IPHOTO_STARTUP_CACHE_STATE", "unknown"),
        "cache_controlled": os.environ.get("IPHOTO_STARTUP_CACHE_CONTROLLED", "0") in _TRUE_VALUES,
        "cache_eviction_method": os.environ.get(
            "IPHOTO_STARTUP_CACHE_EVICTION_METHOD", "uncontrolled"
        ),
        "scenario": os.environ.get("IPHOTO_STARTUP_SCENARIO", "default"),
        "build_environment_fingerprint": os.environ.get(
            "IPHOTO_STARTUP_BUILD_ENVIRONMENT_FINGERPRINT", ""
        ),
        "artifact_sha256": os.environ.get("IPHOTO_STARTUP_ARTIFACT_SHA256", ""),
        "manifest_source_revision": os.environ.get(
            "IPHOTO_STARTUP_MANIFEST_REVISION", ""
        ),
    }


def mark(stage: str, **details: Any) -> None:
    """Append one startup checkpoint; become a no-op in normal launches."""

    if not _ENABLED:
        return
    record = {
        "stage": stage,
        "elapsed_ms": round((time.perf_counter_ns() - _STARTED_NS) / 1_000_000, 3),
        "pid": os.getpid(),
        "wall_time": time.time(),
    }
    context = _default_context()
    with _CONTEXT_LOCK:
        context.update(_CONTEXT)
    record["context"] = context
    if details:
        record["details"] = _sanitize_details(details)
    try:
        path = _profile_path()
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Diagnostics must never prevent the application from starting.
        return


__all__ = ["configure", "enabled", "mark", "stable_id"]

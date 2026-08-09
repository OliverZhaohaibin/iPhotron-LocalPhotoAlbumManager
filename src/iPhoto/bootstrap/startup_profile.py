"""Opt-in JSONL profiling for desktop time-to-first-frame diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ENABLED = os.environ.get("IPHOTO_STARTUP_PROFILE", "").strip().lower() in _TRUE_VALUES
_STARTED_NS = time.perf_counter_ns()
_LOCK = Lock()


def enabled() -> bool:
    """Return whether startup profiling was enabled before process import."""

    return _ENABLED


def _profile_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "iPhoto" / "logs" / "startup.jsonl"


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
    if details:
        record["details"] = details
    try:
        path = _profile_path()
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Diagnostics must never prevent the application from starting.
        return


__all__ = ["enabled", "mark"]

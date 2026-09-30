"""Opt-in runtime hang diagnostics for packaged and source GUI processes."""

from __future__ import annotations

import atexit
import faulthandler
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

_LOGGER = logging.getLogger(__name__)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOCK = threading.Lock()
_STREAM: TextIO | None = None
_ACTIVE = False
_ATEXIT_REGISTERED = False


def runtime_diagnostics_enabled() -> bool:
    """Return whether persistent all-thread stack dumps were requested."""

    return os.environ.get("IPHOTO_RUNTIME_DIAG", "").strip().lower() in _TRUE_VALUES


def runtime_diagnostics_active() -> bool:
    """Return whether the runtime stack watchdog was successfully enabled."""

    with _LOCK:
        return _ACTIVE


def _interval_seconds() -> int:
    raw_value = os.environ.get("IPHOTO_RUNTIME_DIAG_INTERVAL_SEC", "10").strip()
    try:
        return max(5, min(60, int(raw_value)))
    except ValueError:
        return 10


def enable_runtime_diagnostics() -> bool:
    """Start persistent traceback dumps to the configured diagnostic file."""

    global _ACTIVE, _ATEXIT_REGISTERED, _STREAM

    if not runtime_diagnostics_enabled():
        return False
    with _LOCK:
        if _ACTIVE:
            return True

        target_value = os.environ.get("IPHOTO_RUNTIME_DIAG_STACK_PATH", "").strip()
        stream: TextIO | None = None
        owns_stream = False
        try:
            if target_value:
                target = Path(target_value).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = target.open("a", encoding="utf-8", buffering=1)
                owns_stream = True
            elif sys.stderr is not None:
                stream = sys.stderr
            if stream is None:
                return False

            interval = _interval_seconds()
            stream.write(
                json.dumps(
                    {
                        "event": "runtime_diagnostics_started",
                        "wall_time": time.time(),
                        "pid": os.getpid(),
                        "platform": sys.platform,
                        "interval_seconds": interval,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            stream.flush()
            faulthandler.enable(file=stream, all_threads=True)
            faulthandler.dump_traceback_later(
                interval,
                repeat=True,
                file=stream,
                exit=False,
            )
            _STREAM = stream if owns_stream else None
            _ACTIVE = True
            if not _ATEXIT_REGISTERED:
                atexit.register(shutdown_runtime_diagnostics)
                _ATEXIT_REGISTERED = True
            _LOGGER.info(
                "Runtime hang diagnostics enabled; dumping all thread stacks every %ss",
                interval,
            )
            return True
        except Exception:  # noqa: BLE001 - diagnostics cannot break startup
            if owns_stream and stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            _LOGGER.warning("Failed to enable runtime hang diagnostics", exc_info=True)
            return False


def shutdown_runtime_diagnostics() -> None:
    """Stop the watchdog and flush its owned stack stream."""

    global _ACTIVE, _STREAM

    with _LOCK:
        if not _ACTIVE:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001 - shutdown remains best-effort
            pass
        stream = _STREAM
        _STREAM = None
        _ACTIVE = False
        if stream is not None:
            try:
                stream.write(
                    json.dumps(
                        {
                            "event": "runtime_diagnostics_stopped",
                            "wall_time": time.time(),
                            "pid": os.getpid(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                stream.flush()
            except OSError:
                pass
            try:
                stream.close()
            except OSError:
                pass


__all__ = [
    "enable_runtime_diagnostics",
    "runtime_diagnostics_active",
    "runtime_diagnostics_enabled",
    "shutdown_runtime_diagnostics",
]

"""Helpers for lightweight detail-view performance diagnostics."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock

LOGGER = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_PROFILE_LOCK = Lock()


def detail_profile_enabled() -> bool:
    """Return whether detail-view profiling logs should be emitted."""

    return os.environ.get("IPHOTO_DETAIL_PROFILE", "").strip().lower() in _TRUTHY


def log_detail_profile(
    component: str,
    stage: str,
    elapsed_ms: float | None = None,
    **details: object,
) -> None:
    """Emit a structured profiling line when detail profiling is enabled."""

    if not detail_profile_enabled():
        return

    suffix = ""
    if details:
        suffix = " " + " ".join(f"{key}={value}" for key, value in details.items())

    if elapsed_ms is None:
        LOGGER.info("[detail_profile][%s] %s%s", component, stage, suffix)
        return

    LOGGER.info(
        "[detail_profile][%s] %s %.1fms%s",
        component,
        stage,
        elapsed_ms,
        suffix,
    )


def emit_detail_event(stage: str, *, generation: int, **details: object) -> None:
    """Emit a privacy-safe structured Detail event and optional JSONL record."""

    safe_details = {
        key: value
        for key, value in details.items()
        if key not in {"path", "absolute_path", "source"}
    }
    log_detail_profile("open", stage, generation=generation, **safe_details)
    profile_path = os.environ.get("IPHOTO_DETAIL_PROFILE_PATH", "").strip()
    if not profile_path:
        return
    payload = {
        "stage": stage,
        "monotonic_ms": round(time.perf_counter() * 1000.0, 3),
        "wall_time": time.time(),
        "generation": int(generation),
        "details": safe_details,
    }
    try:
        target = Path(profile_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with _PROFILE_LOCK, target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except OSError:
        LOGGER.debug("Failed to append Detail profile event", exc_info=True)


__all__ = ["detail_profile_enabled", "emit_detail_event", "log_detail_profile"]

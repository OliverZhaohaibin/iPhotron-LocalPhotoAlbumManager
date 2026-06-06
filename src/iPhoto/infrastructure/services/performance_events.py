"""Small opt-in performance event helpers."""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any


def perf_logging_enabled() -> bool:
    return os.environ.get("IPHOTO_PERF_LOG", "").strip().lower() in {"1", "true", "yes", "on"}


def explain_enabled() -> bool:
    return os.environ.get("IPHOTO_PERF_EXPLAIN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def fail_on_full_scan_query_enabled() -> bool:
    return os.environ.get("IPHOTO_FAIL_ON_FULL_SCAN_QUERY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0


def emit_perf_event(name: str, **payload: Any) -> None:
    """Emit one JSONL performance event when perf logging is enabled."""

    if not perf_logging_enabled():
        return
    event: dict[str, Any] = {
        "event": name,
        "time_ms": round(time.time() * 1000.0, 3),
    }
    event.update(_json_safe_payload(payload))
    print(json.dumps(event, sort_keys=True, ensure_ascii=False), file=sys.stderr)


def audit_full_scan_query(operation: str, **payload: Any) -> None:
    """Record and optionally fail when GUI collection paths request full scans."""

    if not (perf_logging_enabled() or fail_on_full_scan_query_enabled()):
        return
    caller = _first_relevant_caller()
    audit_payload = dict(payload)
    if caller is not None:
        audit_payload["caller"] = caller
    emit_perf_event(operation, **audit_payload)
    if fail_on_full_scan_query_enabled() and _caller_is_gallery_collection(caller):
        raise AssertionError(f"{operation} called from GUI collection path")


def _first_relevant_caller() -> str | None:
    for frame in inspect.stack(context=0)[2:]:
        filename = frame.filename.replace("\\", "/")
        if "/iPhoto/" not in filename and "/tests/" not in filename:
            continue
        if filename.endswith("performance_events.py"):
            continue
        return f"{filename}:{frame.lineno}:{frame.function}"
    return None


def _caller_is_gallery_collection(caller: str | None) -> bool:
    if caller is None:
        return False
    normalized = caller.replace("\\", "/")
    return (
        "gallery_collection_store.py" in normalized
        or "gallery_list_model_adapter.py" in normalized
        or "library_asset_query_service.py" in normalized
    )


def _json_safe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [
                item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                for item in value
            ]
        elif isinstance(value, dict):
            safe[key] = {
                str(k): v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                for k, v in value.items()
            }
        else:
            safe[key] = str(value)
    return safe


__all__ = [
    "audit_full_scan_query",
    "emit_perf_event",
    "explain_enabled",
    "fail_on_full_scan_query_enabled",
    "monotonic_ms",
    "perf_logging_enabled",
]

"""Small opt-in performance event helpers."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SENSITIVE_FIELDS = {
    "absolute_path",
    "caller",
    "key",
    "old_key",
    "path",
    "paths",
    "source",
    "source_path",
}


def perf_logging_enabled() -> bool:
    return os.environ.get("IPHOTO_PERF_LOG", "").strip().lower() in _TRUE_VALUES


def privacy_safe_perf_logging_enabled() -> bool:
    return os.environ.get("IPHOTO_PERF_PRIVACY_SAFE", "").strip().lower() in _TRUE_VALUES


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
    privacy_safe = privacy_safe_perf_logging_enabled()
    return {
        str(key): _json_safe_value(
            value,
            privacy_safe=privacy_safe,
            sensitive=str(key).lower() in _SENSITIVE_FIELDS,
        )
        for key, value in payload.items()
    }


def _json_safe_value(
    value: Any,
    *,
    privacy_safe: bool,
    sensitive: bool,
) -> Any:
    if isinstance(value, (list, tuple)):
        return [
            _json_safe_value(
                item,
                privacy_safe=privacy_safe,
                sensitive=sensitive,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(
                item,
                privacy_safe=privacy_safe,
                sensitive=sensitive or str(key).lower() in _SENSITIVE_FIELDS,
            )
            for key, item in value.items()
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value

    text = os.fspath(value) if isinstance(value, Path) else str(value)
    if privacy_safe and (sensitive or isinstance(value, Path) or os.path.isabs(text)):
        salt = os.environ.get("IPHOTO_PERF_PRIVACY_SALT", "iPhoto-perf")
        digest = hashlib.sha256(
            f"{salt}|{text}".encode("utf-8", errors="replace")
        ).hexdigest()
        return f"redacted:{digest[:16]}"
    return text


__all__ = [
    "audit_full_scan_query",
    "emit_perf_event",
    "explain_enabled",
    "fail_on_full_scan_query_enabled",
    "monotonic_ms",
    "privacy_safe_perf_logging_enabled",
    "perf_logging_enabled",
]

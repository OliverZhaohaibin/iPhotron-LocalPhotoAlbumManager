"""Asynchronous, privacy-safe Detail performance diagnostics."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Final

LOGGER = logging.getLogger(__name__)

_TRUTHY: Final = {"1", "true", "yes", "on"}
_PROFILE_LOCK = Lock()
_STOP: Final = object()
_WRITER: _AsyncDetailEventWriter | None = None


def detail_profile_enabled() -> bool:
    """Return whether Detail profiling is enabled for this process."""

    return os.environ.get("IPHOTO_DETAIL_PROFILE", "").strip().lower() in _TRUTHY


def detail_profile_log_enabled() -> bool:
    """Return whether verbose human-readable profiling logs are enabled."""

    return os.environ.get("IPHOTO_DETAIL_PROFILE_LOG", "").strip().lower() in _TRUTHY


def log_detail_profile(
    component: str,
    stage: str,
    elapsed_ms: float | None = None,
    **details: object,
) -> None:
    """Emit a human-readable diagnostic line when profiling is enabled."""

    if not detail_profile_enabled() or not detail_profile_log_enabled():
        return

    suffix = ""
    if details:
        suffix = " " + " ".join(f"{key}={value}" for key, value in details.items())
    if elapsed_ms is None:
        LOGGER.info("[detail_profile][%s] %s%s", component, stage, suffix)
    else:
        LOGGER.info(
            "[detail_profile][%s] %s %.1fms%s",
            component,
            stage,
            elapsed_ms,
            suffix,
        )


def _privacy_safe(value: object) -> object:
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str) and os.path.isabs(value):
        return Path(value).name
    if isinstance(value, dict):
        return {
            str(key): _privacy_safe(item)
            for key, item in value.items()
            if str(key) not in {"path", "absolute_path", "source"}
        }
    if isinstance(value, (list, tuple)):
        return [_privacy_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class _AsyncDetailEventWriter:
    """Own the only thread allowed to open and append the JSONL target."""

    def __init__(self, target: Path | None) -> None:
        self.target = target
        self._queue: Queue[dict[str, object] | object] = Queue(maxsize=8192)
        self._thread = Thread(
            target=self._run,
            name="iPhoto-detail-profile-writer",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, payload: dict[str, object]) -> None:
        try:
            self._queue.put_nowait(payload)
        except Full:
            LOGGER.debug("Dropping Detail profile event because the queue is full")

    def close(self, timeout_ms: int) -> None:
        try:
            self._queue.put_nowait(_STOP)
        except Full:
            # A bounded shutdown is more important than preserving diagnostics.
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            try:
                self._queue.put_nowait(_STOP)
            except Full:
                return
        self._thread.join(max(0, int(timeout_ms)) / 1000.0)

    def _run(self) -> None:
        stream = None
        try:
            if self.target is not None:
                self.target.parent.mkdir(parents=True, exist_ok=True)
                stream = self.target.open("a", encoding="utf-8")
            batch: list[dict[str, object]] = []
            stopping = False
            while not stopping:
                try:
                    item = self._queue.get(timeout=0.1)
                except Empty:
                    item = None
                if item is _STOP:
                    stopping = True
                elif isinstance(item, dict):
                    batch.append(item)

                while len(batch) < 64 and not stopping:
                    try:
                        item = self._queue.get_nowait()
                    except Empty:
                        break
                    if item is _STOP:
                        stopping = True
                        break
                    if isinstance(item, dict):
                        batch.append(item)

                if not batch:
                    continue
                for payload in batch:
                    if detail_profile_log_enabled():
                        LOGGER.info(
                            "[detail_profile][open] %s generation=%s",
                            payload["stage"],
                            payload["generation"],
                        )
                    if stream is not None:
                        stream.write(
                            json.dumps(payload, ensure_ascii=False, default=str) + "\n"
                        )
                if stream is not None:
                    stream.flush()
                batch.clear()
        except OSError:
            LOGGER.debug("Detail profile writer failed", exc_info=True)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _writer_for_current_configuration() -> _AsyncDetailEventWriter:
    global _WRITER

    profile_path = os.environ.get("IPHOTO_DETAIL_PROFILE_PATH", "").strip()
    target = Path(profile_path).expanduser() if profile_path else None
    with _PROFILE_LOCK:
        if _WRITER is None or _WRITER.target != target:
            if _WRITER is not None:
                _WRITER.close(500)
            _WRITER = _AsyncDetailEventWriter(target)
        return _WRITER


def emit_detail_event(stage: str, *, generation: int, **details: object) -> None:
    """Queue one timestamped event without filesystem I/O on the caller thread."""

    if not detail_profile_enabled():
        return
    safe_details = {
        key: _privacy_safe(value)
        for key, value in details.items()
        if key not in {"path", "absolute_path", "source"}
    }
    payload: dict[str, object] = {
        "stage": str(stage),
        "monotonic_ms": round(time.perf_counter() * 1000.0, 3),
        "wall_time": time.time(),
        "generation": int(generation),
        "details": safe_details,
    }
    _writer_for_current_configuration().enqueue(payload)


def shutdown_detail_profile(*, timeout_ms: int = 1000) -> None:
    """Flush queued events within a bounded application-shutdown window."""

    global _WRITER
    with _PROFILE_LOCK:
        writer = _WRITER
        _WRITER = None
    if writer is not None:
        writer.close(timeout_ms)


__all__ = [
    "detail_profile_enabled",
    "detail_profile_log_enabled",
    "emit_detail_event",
    "log_detail_profile",
    "shutdown_detail_profile",
]

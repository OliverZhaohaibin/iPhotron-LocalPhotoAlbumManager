"""Deduplicated request scheduling for Detail still-image decoders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QThreadPool, Signal
from PySide6.QtGui import QImage

from iPhoto.gui.detail_profile import emit_detail_event


@dataclass(frozen=True, slots=True)
class DetailDecodeKey:
    """Phase-1 identity for one source decode.

    Source revisions and viewport decode levels intentionally arrive in Phase 2.
    Phase 1 uses the already indexed asset id and absolute path so creating a key
    performs no filesystem work on the GUI thread.
    """

    asset_id: str
    source: Path

    @classmethod
    def create(cls, asset_id: str, source: Path) -> DetailDecodeKey:
        path = Path(source).absolute()
        stable_asset_id = str(asset_id).strip() or path.name
        return cls(asset_id=stable_asset_id, source=path)


@dataclass(slots=True)
class _InflightDecode:
    key: DetailDecodeKey
    worker: Any
    state: Literal["queued", "running"]
    foreground_generation: int | None
    priority: int


class DetailStillRequestScheduler(QObject):
    """Own and deduplicate queued/running Detail still decoders.

    Native image decoders cannot reliably be interrupted once running.  The
    scheduler therefore promotes or subscribes to an existing same-key task
    instead of launching a second decoder, while unrelated running stale tasks
    are allowed to finish without publishing into the active generation.
    """

    ready = Signal(int, Path, QImage, dict, object)
    failed = Signal(int, Path, str)
    finished = Signal(object)

    def __init__(
        self,
        *,
        pool: QThreadPool,
        worker_factory: Callable[[Path], Any],
        reuse_enabled: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool
        self._worker_factory = worker_factory
        self._reuse_enabled = bool(reuse_enabled)
        self._inflight_by_key: dict[DetailDecodeKey, _InflightDecode] = {}
        self._entry_by_worker_id: dict[int, _InflightDecode] = {}
        self._current_generation = 0
        self._shutting_down = False

    @property
    def inflight_count(self) -> int:
        return len(self._inflight_by_key)

    def prefetch(self, *, asset_id: str, source: Path) -> bool:
        """Submit one speculative source unless it is already in flight."""

        if self._shutting_down:
            return False
        key = DetailDecodeKey.create(asset_id, source)
        if key in self._inflight_by_key:
            return False

        # Keep speculative work bounded.  A running prefetch may be promoted by
        # a click, but moving the pointer must not fill both decoder lanes with
        # obsolete speculative work.
        if any(
            entry.foreground_generation is None
            for entry in self._inflight_by_key.values()
        ):
            return False

        entry = self._create_entry(key, generation=None, priority=-1)
        emit_detail_event(
            "scheduled",
            generation=0,
            asset_id=key.asset_id,
            suffix=key.source.suffix.lower(),
        )
        return self._submit(entry)

    def request(self, *, asset_id: str, source: Path, generation: int) -> bool:
        """Submit or attach the active foreground generation."""

        if self._shutting_down:
            return False
        numeric_generation = int(generation)
        self._current_generation = numeric_generation
        key = DetailDecodeKey.create(asset_id, source)
        self._discard_queued_other_keys(key)
        self._detach_running_other_keys(key)

        existing = self._inflight_by_key.get(key)
        if existing is not None and self._reuse_enabled:
            was_prefetch = existing.foreground_generation is None
            existing.foreground_generation = numeric_generation
            emit_detail_event(
                "reused",
                generation=numeric_generation,
                asset_id=key.asset_id,
                state=existing.state,
                suffix=key.source.suffix.lower(),
            )
            if existing.state == "queued" and existing.priority < 1:
                if self._pool.tryTake(existing.worker):
                    existing.priority = 1
                    emit_detail_event(
                        "promoted",
                        generation=numeric_generation,
                        asset_id=key.asset_id,
                        suffix=key.source.suffix.lower(),
                    )
                    self._pool.start(existing.worker, 1)
                elif was_prefetch:
                    # The worker crossed into native decode before its queued
                    # started signal reached the scheduler.  Reusing it is still
                    # correct and avoids a duplicate decode.
                    existing.state = "running"
            return True

        if existing is not None:
            if existing.state == "queued" and self._pool.tryTake(existing.worker):
                self._retire_entry(existing, cancel=True)
            else:
                # Diagnostic fallback recreates the pre-v3 behaviour.  Keep the
                # already-running worker owned until its terminal signal while
                # preventing it from publishing into the foreground.
                existing.foreground_generation = None
                cancel_worker = getattr(existing.worker, "cancel", None)
                if callable(cancel_worker):
                    cancel_worker()
                self._inflight_by_key.pop(existing.key, None)

        entry = self._create_entry(key, generation=numeric_generation, priority=1)
        emit_detail_event(
            "scheduled",
            generation=numeric_generation,
            asset_id=key.asset_id,
            suffix=key.source.suffix.lower(),
        )
        return self._submit(entry)

    def cancel_foreground(self) -> None:
        """Invalidate foreground delivery and remove work not yet running."""

        self._current_generation += 1
        for entry in tuple(self._inflight_by_key.values()):
            entry.foreground_generation = None
            if entry.state == "queued" and self._pool.tryTake(entry.worker):
                self._retire_entry(entry, cancel=True)

    def shutdown(self, *, timeout_ms: int = 1500) -> None:
        """Cancel queued/running work and wait for the dedicated pool."""

        if self._shutting_down:
            return
        self._shutting_down = True
        self._current_generation += 1
        for entry in tuple(self._inflight_by_key.values()):
            entry.foreground_generation = None
            cancel = getattr(entry.worker, "cancel", None)
            if callable(cancel):
                cancel()
            if entry.state == "queued" and self._pool.tryTake(entry.worker):
                self._retire_entry(entry, cancel=False)
        self._pool.clear()
        completed = self._pool.waitForDone(max(0, int(timeout_ms)))
        if completed:
            for entry in tuple(self._inflight_by_key.values()):
                self._retire_entry(entry, cancel=False)

    def _create_entry(
        self,
        key: DetailDecodeKey,
        *,
        generation: int | None,
        priority: int,
    ) -> _InflightDecode:
        worker = self._worker_factory(key.source)
        entry = _InflightDecode(
            key=key,
            worker=worker,
            state="queued",
            foreground_generation=generation,
            priority=int(priority),
        )
        self._inflight_by_key[key] = entry
        self._entry_by_worker_id[id(worker)] = entry
        signals = worker.signals
        signals.started.connect(self._on_worker_started)
        signals.completed.connect(
            lambda source, image, adjustments, active=worker:
            self._on_worker_completed(source, image, adjustments, active)
        )
        signals.failed.connect(
            lambda source, message, active=worker:
            self._on_worker_failed(source, message, active)
        )
        signals.finished.connect(self._on_worker_finished)
        return entry

    def _submit(self, entry: _InflightDecode) -> bool:
        try:
            self._pool.start(entry.worker, entry.priority)
        except RuntimeError:
            generation = entry.foreground_generation
            source = entry.key.source
            self._retire_entry(entry, cancel=True)
            if generation is not None and generation == self._current_generation:
                self.failed.emit(generation, source, "Still-image decoder pool is unavailable")
            return False
        return True

    def _discard_queued_other_keys(self, active_key: DetailDecodeKey) -> None:
        for entry in tuple(self._inflight_by_key.values()):
            if entry.key == active_key or entry.state != "queued":
                continue
            if self._pool.tryTake(entry.worker):
                self._retire_entry(entry, cancel=True)

    def _detach_running_other_keys(self, active_key: DetailDecodeKey) -> None:
        for entry in self._inflight_by_key.values():
            if entry.key != active_key:
                entry.foreground_generation = None

    def _on_worker_started(self, worker: object) -> None:
        entry = self._entry_by_worker_id.get(id(worker))
        if entry is None:
            return
        entry.state = "running"
        emit_detail_event(
            "worker_started",
            generation=entry.foreground_generation or 0,
            asset_id=entry.key.asset_id,
            suffix=entry.key.source.suffix.lower(),
        )

    def _on_worker_completed(
        self,
        source: Path,
        image: QImage,
        adjustments: dict,
        worker: object,
    ) -> None:
        entry = self._entry_by_worker_id.get(id(worker))
        if entry is None:
            return
        generation = entry.foreground_generation
        if generation is None or generation != self._current_generation:
            emit_detail_event(
                "stale",
                generation=generation or 0,
                asset_id=entry.key.asset_id,
                suffix=entry.key.source.suffix.lower(),
            )
            return
        self.ready.emit(
            generation,
            Path(source),
            image,
            dict(adjustments),
            getattr(worker, "frame_identity", None),
        )

    def _on_worker_failed(self, source: Path, message: str, worker: object) -> None:
        entry = self._entry_by_worker_id.get(id(worker))
        if entry is None:
            return
        generation = entry.foreground_generation
        if generation is not None and generation == self._current_generation:
            self.failed.emit(generation, Path(source), str(message))

    def _on_worker_finished(self, worker: object) -> None:
        entry = self._entry_by_worker_id.get(id(worker))
        if entry is None:
            return
        emit_detail_event(
            "worker_finished",
            generation=entry.foreground_generation or 0,
            asset_id=entry.key.asset_id,
            suffix=entry.key.source.suffix.lower(),
            cache_hit=bool(getattr(worker, "cache_hit", False)),
        )
        self.finished.emit(entry.key)
        self._retire_entry(entry, cancel=False)

    def _retire_entry(self, entry: _InflightDecode, *, cancel: bool) -> None:
        if cancel:
            cancel_worker = getattr(entry.worker, "cancel", None)
            if callable(cancel_worker):
                cancel_worker()
        if self._inflight_by_key.get(entry.key) is entry:
            self._inflight_by_key.pop(entry.key, None)
        self._entry_by_worker_id.pop(id(entry.worker), None)
        signals = getattr(entry.worker, "signals", None)
        if signals is not None:
            signals.deleteLater()
        entry.worker.setAutoDelete(True)


__all__ = ["DetailDecodeKey", "DetailStillRequestScheduler"]

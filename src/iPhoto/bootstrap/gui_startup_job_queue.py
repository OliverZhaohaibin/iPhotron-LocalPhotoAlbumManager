"""Cooperative, observable scheduling for GUI-thread startup work."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .startup_profile import mark

_LOGGER = logging.getLogger(__name__)
_DEFAULT_BUDGET_MS = 100.0


@dataclass(frozen=True, slots=True)
class GuiStartupJob:
    """One indivisible unit of startup work executed on the GUI thread."""

    name: str
    generation: int
    callback: Callable[[], Any]
    budget_ms: float = _DEFAULT_BUDGET_MS
    prerequisite: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class GuiStartupJobFailure:
    """Failure details forwarded to the startup state machine."""

    name: str
    generation: int
    exception: Exception


class GuiStartupJobQueue(QObject):
    """Run at most one named startup job per Qt event-loop turn."""

    jobFailed = Signal(object)  # noqa: N815 - Qt signal naming convention

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        scheduler: Callable[[Callable[[], None]], None] | None = None,
        is_generation_current: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._clock_ns = clock_ns
        self._scheduler = scheduler or (lambda callback: QTimer.singleShot(0, callback))
        self._is_generation_current = is_generation_current or (lambda _generation: True)
        self._jobs: deque[GuiStartupJob] = deque()
        self._cancelled_generations: set[int] = set()
        self._scheduled = False
        self._running = False
        self._closed = False

    def enqueue(
        self,
        name: str,
        generation: int,
        callback: Callable[[], Any],
        *,
        budget_ms: float = _DEFAULT_BUDGET_MS,
        prerequisite: Callable[[], bool] | None = None,
    ) -> bool:
        """Append a job and arrange a future event-loop turn for it."""

        generation = int(generation)
        if self._closed or not self._accepts_generation(generation):
            return False
        self._jobs.append(
            GuiStartupJob(
                name=str(name),
                generation=generation,
                callback=callback,
                budget_ms=max(0.0, float(budget_ms)),
                prerequisite=prerequisite,
            )
        )
        self.wake()
        return True

    def wake(self) -> None:
        """Re-check the head job after an external prerequisite changes."""

        if self._closed or self._scheduled or self._running or not self._jobs:
            return
        self._scheduled = True
        self._scheduler(self._run_one)

    def cancel_generation(self, generation: int) -> None:
        """Invalidate queued and future work for one startup generation."""

        generation = int(generation)
        self._cancelled_generations.add(generation)
        self._jobs = deque(job for job in self._jobs if job.generation != generation)

    def close(self) -> None:
        """Permanently reject work and discard all pending callbacks."""

        self._closed = True
        self._jobs.clear()

    @property
    def pending_count(self) -> int:
        return len(self._jobs)

    def _accepts_generation(self, generation: int) -> bool:
        return (
            generation not in self._cancelled_generations
            and self._is_generation_current(generation)
        )

    def _run_one(self) -> None:
        self._scheduled = False
        if self._closed or self._running:
            return

        while self._jobs and not self._accepts_generation(self._jobs[0].generation):
            self._jobs.popleft()
        if not self._jobs:
            return

        job = self._jobs[0]
        if job.prerequisite is not None and not job.prerequisite():
            return
        self._jobs.popleft()
        self._running = True
        try:
            self._execute(job)
        finally:
            self._running = False
        self.wake()

    def _execute(self, job: GuiStartupJob) -> None:
        thread_name = threading.current_thread().name
        mark(
            "startup.gui_job.started",
            job=job.name,
            generation=job.generation,
            duration_ms=0.0,
            budget_ms=job.budget_ms,
            over_budget=False,
            thread=thread_name,
            result="running",
        )
        started_ns = self._clock_ns()
        error: Exception | None = None
        try:
            job.callback()
        except Exception as exc:  # noqa: BLE001 - GUI startup isolation boundary
            error = exc
        finished_ns = self._clock_ns()
        duration_ms = max(0.0, (finished_ns - started_ns) / 1_000_000.0)
        over_budget = duration_ms > job.budget_ms
        result = "error" if error is not None else "success"
        details = {
            "job": job.name,
            "generation": job.generation,
            "duration_ms": round(duration_ms, 3),
            "budget_ms": job.budget_ms,
            "over_budget": over_budget,
            "thread": thread_name,
            "result": result,
        }
        mark("startup.gui_job.finished", **details)
        if over_budget:
            mark("startup.gui_stall", **details)
            _LOGGER.warning(
                "GUI startup job %s exceeded %.1fms budget (%.1fms)",
                job.name,
                job.budget_ms,
                duration_ms,
            )
        if error is None:
            return

        self.cancel_generation(job.generation)
        self.jobFailed.emit(
            GuiStartupJobFailure(
                name=job.name,
                generation=job.generation,
                exception=error,
            )
        )


__all__ = [
    "GuiStartupJob",
    "GuiStartupJobFailure",
    "GuiStartupJobQueue",
]

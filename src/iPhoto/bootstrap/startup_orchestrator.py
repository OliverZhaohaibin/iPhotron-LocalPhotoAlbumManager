"""Deterministic desktop-startup state and failure coordination.

The orchestrator deliberately owns no application services.  It only makes the
otherwise fragile Qt callback chain idempotent, observable, and cancellable.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .startup_profile import mark

_LOGGER = logging.getLogger(__name__)


class StartupPhase(StrEnum):
    BOOTSTRAP = "bootstrap"
    APP_CREATED = "app_created"
    SHELL_SHOWN = "shell_shown"
    INTERACTIVE = "interactive"
    LIBRARY_PROBING = "library_probing"
    LIBRARY_READY = "library_ready"
    GALLERY_READY = "gallery_ready"
    IDLE = "idle"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StartupSnapshot:
    phase: StartupPhase
    generation: int
    elapsed_ms: float
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class StartupFailure:
    phase: StartupPhase
    message: str
    exception_type: str | None = None
    recoverable: bool = True
    code: str = "startup_failed"
    suggested_action: str = "retry"


class StartupOrchestrator(QObject):
    """Own the one-shot transition from a shown shell to app initialisation."""

    phaseChanged = Signal(object)  # noqa: N815 - Qt signal naming convention
    interactiveReady = Signal(object)  # noqa: N815 - Qt signal naming convention
    libraryReady = Signal(object)  # noqa: N815 - Qt signal naming convention
    startupDegraded = Signal(object)  # noqa: N815 - Qt signal naming convention
    startupCompleted = Signal(object)  # noqa: N815 - Qt signal naming convention

    def __init__(self, parent: QObject | None = None, *, watchdog_ms: int = 1800) -> None:
        super().__init__(parent)
        self._watchdog_ms = max(1, int(watchdog_ms))
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._phase = StartupPhase.BOOTSTRAP
        self._generation = 0
        self._started_ns = time.perf_counter_ns()
        self._continuation: Callable[[], None] | None = None
        self._continuation_started = False
        self._cancelled = False
        self._terminal_generations: set[int] = set()
        self._milestones: set[tuple[int, StartupPhase]] = set()

    @property
    def phase(self) -> StartupPhase:
        return self._phase

    @property
    def generation(self) -> int:
        return self._generation

    def begin(self) -> int:
        """Start a new generation and invalidate callbacks from an older one."""

        self._generation += 1
        self._started_ns = time.perf_counter_ns()
        self._continuation = None
        self._continuation_started = False
        self._cancelled = False
        self.transition(StartupPhase.BOOTSTRAP)
        return self._generation

    def shell_shown(self, continuation: Callable[[], None]) -> None:
        """Arm the paint fast path and an independent liveness watchdog."""

        if self._cancelled:
            return
        self._continuation = continuation
        self.transition(StartupPhase.SHELL_SHOWN)
        self._watchdog.start(self._watchdog_ms)

    def first_painted(self) -> None:
        self._run_continuation("first_paint")

    def transition(self, phase: StartupPhase, *, reason: str | None = None) -> StartupSnapshot:
        if self._cancelled and phase is not StartupPhase.CANCELLED:
            return self.snapshot(reason=reason)
        self._phase = phase
        snapshot = self.snapshot(reason=reason)
        mark(
            "startup.phase",
            phase=phase.value,
            generation=self._generation,
            reason=reason,
        )
        milestone = {
            StartupPhase.APP_CREATED: "startup.app_created",
            StartupPhase.INTERACTIVE: "startup.interactive",
            StartupPhase.LIBRARY_READY: "startup.library_ready",
        }.get(phase)
        milestone_key = (self._generation, phase)
        if milestone is not None and milestone_key not in self._milestones:
            self._milestones.add(milestone_key)
            mark(milestone, generation=self._generation, reason=reason)
        self.phaseChanged.emit(snapshot)
        if phase is StartupPhase.INTERACTIVE:
            self.interactiveReady.emit(snapshot)
        elif phase is StartupPhase.LIBRARY_READY:
            self.libraryReady.emit(snapshot)
        elif phase is StartupPhase.IDLE:
            self._emit_terminal("startup.completed")
            self.startupCompleted.emit(snapshot)
        return snapshot

    def _is_terminal(self) -> bool:
        return self._generation in self._terminal_generations

    def _emit_terminal(self, stage: str, **details: Any) -> bool:
        if self._is_terminal():
            return False
        self._terminal_generations.add(self._generation)
        mark(stage, generation=self._generation, **details)
        return True

    def run_guarded(
        self,
        phase: StartupPhase,
        callback: Callable[[], Any],
        *,
        recoverable: bool = True,
    ) -> Any | None:
        """Run one startup step and convert callback exceptions into state."""

        if self._cancelled:
            return None
        self.transition(phase)
        try:
            return callback()
        except Exception as exc:  # startup isolation boundary
            _LOGGER.exception("Startup step %s failed", phase.value)
            self.fail(
                StartupFailure(
                    phase=phase,
                    message=str(exc) or type(exc).__name__,
                    exception_type=type(exc).__name__,
                    recoverable=recoverable,
                )
            )
            return None

    def degrade(self, failure: StartupFailure) -> None:
        if self._cancelled or self._is_terminal():
            return
        self._watchdog.stop()
        self._emit_terminal(
            "startup.degraded",
            failed_phase=failure.phase.value,
            exception_type=failure.exception_type,
            code=failure.code,
        )
        self.transition(StartupPhase.DEGRADED, reason=failure.message)
        self.startupDegraded.emit(failure)

    def fail(self, failure: StartupFailure) -> None:
        if failure.recoverable:
            self.degrade(failure)
            return
        if self._cancelled or self._is_terminal():
            return
        self._watchdog.stop()
        self._emit_terminal(
            "startup.failed",
            failed_phase=failure.phase.value,
            exception_type=failure.exception_type,
            code=failure.code,
        )
        self.transition(StartupPhase.FAILED, reason=failure.message)
        self.startupDegraded.emit(failure)

    def complete(self) -> None:
        if self._cancelled or self._is_terminal():
            return
        self._watchdog.stop()
        self.transition(StartupPhase.IDLE)
        try:
            import faulthandler

            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001 - diagnostics cannot break completion
            _LOGGER.debug("Unable to cancel faulthandler startup timer", exc_info=True)

    def cancel(self) -> None:
        if self._cancelled or self._is_terminal():
            return
        self._cancelled = True
        self._watchdog.stop()
        self._continuation = None
        self._phase = StartupPhase.CANCELLED
        snapshot = self.snapshot(reason="cancelled")
        self._emit_terminal("startup.cancelled")
        self.phaseChanged.emit(snapshot)

    def is_current(self, generation: int) -> bool:
        return not self._cancelled and int(generation) == self._generation

    def snapshot(self, *, reason: str | None = None) -> StartupSnapshot:
        elapsed_ms = (time.perf_counter_ns() - self._started_ns) / 1_000_000.0
        return StartupSnapshot(
            phase=self._phase,
            generation=self._generation,
            elapsed_ms=round(elapsed_ms, 3),
            reason=reason,
        )

    def _on_watchdog(self) -> None:
        _LOGGER.warning("First-paint startup watchdog fired after %dms", self._watchdog_ms)
        mark("startup.first_paint_watchdog", generation=self._generation)
        self._run_continuation("watchdog")

    def _run_continuation(self, reason: str) -> None:
        if self._cancelled or self._continuation_started:
            return
        callback = self._continuation
        if callback is None:
            return
        self._continuation_started = True
        self._watchdog.stop()
        self.transition(StartupPhase.INTERACTIVE, reason=reason)
        try:
            callback()
        except Exception as exc:  # Qt callback isolation boundary
            _LOGGER.exception("Startup continuation failed")
            self.fail(
                StartupFailure(
                    phase=StartupPhase.INTERACTIVE,
                    message=str(exc) or type(exc).__name__,
                    exception_type=type(exc).__name__,
                )
            )


__all__ = [
    "StartupFailure",
    "StartupOrchestrator",
    "StartupPhase",
    "StartupSnapshot",
]

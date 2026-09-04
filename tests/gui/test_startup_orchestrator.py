from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from iPhoto.bootstrap import startup_orchestrator as orchestrator_module
from iPhoto.bootstrap.startup_orchestrator import (
    StartupFailure,
    StartupOrchestrator,
    StartupPhase,
)
from iPhoto.gui.main import _StartupImportRegistry, _StartupModulePreloader


def test_startup_import_registry_isolates_retry_generations() -> None:
    registry = _StartupImportRegistry()
    first_error = RuntimeError("first attempt failed")
    second_value = object()

    registry.fail(1, first_error)
    registry.publish(2, second_value)

    assert registry.ready(1) is True
    assert registry.ready(2) is True
    try:
        registry.resolve(1)
    except RuntimeError as exc:
        assert str(exc) == "first attempt failed"
    else:
        raise AssertionError("first startup generation unexpectedly resolved")
    assert registry.resolve(2) is second_value


def test_startup_module_preloader_close_is_bounded(qapp) -> None:
    started = threading.Event()
    release = threading.Event()
    preloader = _StartupModulePreloader()

    def blocked_loader() -> object:
        started.set()
        release.wait(timeout=2.0)
        return object()

    assert preloader.start(1, blocked_loader)
    assert started.wait(timeout=1.0)
    before = time.monotonic()
    lingering = preloader.close(timeout_ms=10)
    elapsed = time.monotonic() - before
    release.set()

    assert elapsed < 0.5
    assert lingering == ("StartupModulePreloader-1",)
    assert preloader.ready(1) is False


def test_startup_module_preloader_rejects_cancelled_late_result(qapp) -> None:
    started = threading.Event()
    release = threading.Event()
    settled: list[int] = []
    preloader = _StartupModulePreloader()
    preloader.settled.connect(settled.append)

    def slow_loader() -> object:
        started.set()
        release.wait(timeout=2.0)
        return object()

    assert preloader.start(1, slow_loader)
    assert started.wait(timeout=1.0)
    preloader.cancel_generation(1)
    release.set()
    preloader.close()
    qapp.processEvents()

    assert preloader.ready(1) is False
    assert settled == []


def test_startup_module_preloader_forwards_import_failure(qapp) -> None:
    preloader = _StartupModulePreloader()

    def broken_loader() -> object:
        raise RuntimeError("broken import")

    assert preloader.start(2, broken_loader, asynchronous=False)
    assert preloader.ready(2) is True
    with pytest.raises(RuntimeError, match="broken import"):
        preloader.resolve(2)
    preloader.close()


def test_first_paint_and_watchdog_start_continuation_once(qapp) -> None:
    calls: list[str] = []
    orchestrator = StartupOrchestrator(watchdog_ms=10)
    orchestrator.begin()
    orchestrator.shell_shown(lambda: calls.append("continued"))

    orchestrator.first_painted()
    orchestrator._on_watchdog()

    assert calls == ["continued"]
    assert orchestrator.phase is StartupPhase.INTERACTIVE


def test_watchdog_recovers_missing_first_paint(qapp) -> None:
    calls: list[str] = []
    orchestrator = StartupOrchestrator(watchdog_ms=10)
    orchestrator.begin()
    orchestrator.shell_shown(lambda: calls.append("continued"))

    orchestrator._on_watchdog()

    assert calls == ["continued"]
    assert orchestrator.phase is StartupPhase.INTERACTIVE


def test_callback_failure_becomes_degraded_state(qapp) -> None:
    failures: list[StartupFailure] = []
    orchestrator = StartupOrchestrator()
    orchestrator.startupDegraded.connect(failures.append)
    orchestrator.begin()

    def fail() -> None:
        raise RuntimeError("broken startup")

    orchestrator.shell_shown(fail)
    orchestrator.first_painted()

    assert orchestrator.phase is StartupPhase.DEGRADED
    assert failures[0].message == "broken startup"


def test_cancel_rejects_late_callbacks_and_old_generation(qapp) -> None:
    calls: list[str] = []
    orchestrator = StartupOrchestrator()
    generation = orchestrator.begin()
    orchestrator.shell_shown(lambda: calls.append("continued"))

    orchestrator.cancel()
    orchestrator.first_painted()

    assert calls == []
    assert not orchestrator.is_current(generation)
    assert orchestrator.phase is StartupPhase.CANCELLED


def test_new_generation_rejects_previous_token(qapp) -> None:
    orchestrator = StartupOrchestrator()
    old_generation = orchestrator.begin()
    new_generation = orchestrator.begin()

    assert new_generation > old_generation
    assert not orchestrator.is_current(old_generation)
    assert orchestrator.is_current(new_generation)


def test_attempt_cleanup_runs_once_for_complete_and_cancel(qapp) -> None:
    calls: list[str] = []
    orchestrator = StartupOrchestrator()
    orchestrator.begin()
    orchestrator.register_cleanup(lambda: calls.append("cleanup"))

    orchestrator.complete()
    orchestrator.cancel()

    assert calls == ["cleanup"]


def test_superseded_generation_gets_cancelled_terminal_and_cleanup(
    qapp,
    monkeypatch,
) -> None:
    calls: list[str] = []
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        orchestrator_module,
        "mark",
        lambda stage, **details: events.append((stage, details)),
    )
    orchestrator = StartupOrchestrator()
    first = orchestrator.begin()
    orchestrator.register_cleanup(lambda: calls.append("first"))

    second = orchestrator.begin()

    assert second > first
    assert calls == ["first"]
    assert ("startup.cancelled", {"generation": first, "reason": "superseded"}) in events


def test_terminal_event_is_unique_when_completed_startup_closes(qapp, monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        orchestrator_module,
        "mark",
        lambda stage, **details: events.append((stage, details)),
    )
    orchestrator = StartupOrchestrator()
    orchestrator.begin()

    orchestrator.complete()
    orchestrator.cancel()

    terminals = [
        stage
        for stage, _details in events
        if stage.startswith("startup.")
        and stage.split(".")[-1] in {"completed", "degraded", "failed", "cancelled"}
    ]
    assert terminals == ["startup.completed"]
    assert orchestrator.phase is StartupPhase.IDLE


def test_nonrecoverable_failure_emits_failed_terminal_once(qapp, monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        orchestrator_module,
        "mark",
        lambda stage, **details: events.append((stage, details)),
    )
    orchestrator = StartupOrchestrator()
    orchestrator.begin()
    failure = StartupFailure(
        phase=StartupPhase.APP_CREATED,
        message="fatal",
        recoverable=False,
        code="fatal_startup",
    )

    orchestrator.fail(failure)
    orchestrator.fail(failure)

    failed = [details for stage, details in events if stage == "startup.failed"]
    assert failed == [
        {
            "generation": 1,
            "failed_phase": "app_created",
            "exception_type": None,
            "code": "fatal_startup",
        }
    ]


def test_canonical_milestone_is_emitted_once_per_generation(qapp, monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        orchestrator_module,
        "mark",
        lambda stage, **_details: events.append(stage),
    )
    orchestrator = StartupOrchestrator()
    orchestrator.begin()

    orchestrator.transition(StartupPhase.INTERACTIVE)
    orchestrator.transition(StartupPhase.INTERACTIVE)

    assert events.count("startup.interactive") == 1


def test_terminal_keeps_persistent_runtime_diagnostics_active(
    qapp,
    monkeypatch,
) -> None:
    cancel_dump = MagicMock()
    monkeypatch.setattr(
        "iPhoto.runtime_diagnostics.runtime_diagnostics_active",
        lambda: True,
    )
    monkeypatch.setattr(
        "faulthandler.cancel_dump_traceback_later",
        cancel_dump,
    )
    orchestrator = StartupOrchestrator()
    orchestrator.begin()

    orchestrator.complete()

    cancel_dump.assert_not_called()

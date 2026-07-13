from __future__ import annotations

from iPhoto.bootstrap import startup_orchestrator as orchestrator_module
from iPhoto.bootstrap.startup_orchestrator import (
    StartupFailure,
    StartupOrchestrator,
    StartupPhase,
)


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

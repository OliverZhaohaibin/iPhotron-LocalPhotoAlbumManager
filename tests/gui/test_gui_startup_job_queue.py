from __future__ import annotations

from iPhoto.bootstrap import gui_startup_job_queue as queue_module
from iPhoto.bootstrap.gui_startup_job_queue import GuiStartupJobQueue


class _ManualScheduler:
    def __init__(self) -> None:
        self.callbacks = []

    def __call__(self, callback) -> None:
        self.callbacks.append(callback)

    def tick(self) -> None:
        callback = self.callbacks.pop(0)
        callback()


def test_runs_at_most_one_job_per_event_loop_tick(qapp) -> None:
    scheduler = _ManualScheduler()
    calls: list[str] = []
    queue = GuiStartupJobQueue(scheduler=scheduler)

    queue.enqueue("one", 1, lambda: calls.append("one"))
    queue.enqueue("two", 1, lambda: calls.append("two"))

    assert len(scheduler.callbacks) == 1
    scheduler.tick()
    assert calls == ["one"]
    assert len(scheduler.callbacks) == 1
    scheduler.tick()
    assert calls == ["one", "two"]


def test_prerequisite_preserves_order_until_woken(qapp) -> None:
    scheduler = _ManualScheduler()
    ready = False
    calls: list[str] = []
    queue = GuiStartupJobQueue(scheduler=scheduler)
    queue.enqueue("blocked", 1, lambda: calls.append("blocked"), prerequisite=lambda: ready)
    queue.enqueue("after", 1, lambda: calls.append("after"))

    scheduler.tick()
    assert calls == []
    assert scheduler.callbacks == []

    ready = True
    queue.wake()
    scheduler.tick()
    scheduler.tick()
    assert calls == ["blocked", "after"]


def test_records_one_stall_only_when_budget_is_exceeded(qapp, monkeypatch) -> None:
    scheduler = _ManualScheduler()
    clock = iter((0, 100_000_000, 200_000_000, 300_000_001)).__next__
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        queue_module,
        "mark",
        lambda stage, **details: events.append((stage, details)),
    )
    queue = GuiStartupJobQueue(scheduler=scheduler, clock_ns=clock)
    queue.enqueue("within", 1, lambda: None)
    queue.enqueue("over", 1, lambda: None)

    scheduler.tick()
    scheduler.tick()

    stalls = [details for stage, details in events if stage == "startup.gui_stall"]
    assert [event["job"] for event in stalls] == ["over"]
    assert stalls[0]["duration_ms"] == 100.0
    finished = [details for stage, details in events if stage == "startup.gui_job.finished"]
    assert [event["over_budget"] for event in finished] == [False, True]
    assert all(
        set(event)
        == {
            "job",
            "generation",
            "duration_ms",
            "budget_ms",
            "over_budget",
            "thread",
            "result",
        }
        for event in finished
    )


def test_cancelled_and_stale_generations_do_not_run(qapp) -> None:
    scheduler = _ManualScheduler()
    current = 2
    calls: list[str] = []
    queue = GuiStartupJobQueue(
        scheduler=scheduler,
        is_generation_current=lambda generation: generation == current,
    )

    assert queue.enqueue("stale", 1, lambda: calls.append("stale")) is False
    assert queue.enqueue("current", 2, lambda: calls.append("current")) is True
    queue.cancel_generation(2)
    scheduler.tick()
    assert calls == []
    assert queue.enqueue("cancelled", 2, lambda: calls.append("cancelled")) is False


def test_failure_is_reported_once_and_cancels_remaining_generation(qapp) -> None:
    scheduler = _ManualScheduler()
    calls: list[str] = []
    failures = []
    queue = GuiStartupJobQueue(scheduler=scheduler)
    queue.jobFailed.connect(failures.append)

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("broken")

    queue.enqueue("fail", 1, fail)
    queue.enqueue("after", 1, lambda: calls.append("after"))
    scheduler.tick()

    assert calls == ["fail"]
    assert len(failures) == 1
    assert failures[0].name == "fail"
    assert str(failures[0].exception) == "broken"
    assert queue.pending_count == 0


def test_job_enqueued_during_callback_waits_for_next_tick(qapp) -> None:
    scheduler = _ManualScheduler()
    calls: list[str] = []
    queue = GuiStartupJobQueue(scheduler=scheduler)

    def first() -> None:
        calls.append("first")
        queue.enqueue("nested", 1, lambda: calls.append("nested"))

    queue.enqueue("first", 1, first)
    scheduler.tick()
    assert calls == ["first"]
    scheduler.tick()
    assert calls == ["first", "nested"]


def test_close_discards_and_rejects_jobs(qapp) -> None:
    scheduler = _ManualScheduler()
    calls: list[str] = []
    queue = GuiStartupJobQueue(scheduler=scheduler)
    queue.enqueue("pending", 1, lambda: calls.append("pending"))

    queue.close()
    scheduler.tick()

    assert calls == []
    assert queue.enqueue("late", 1, lambda: calls.append("late")) is False

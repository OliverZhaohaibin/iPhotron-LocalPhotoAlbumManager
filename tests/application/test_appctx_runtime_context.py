"""Compatibility tests for AppContext delegating to RuntimeContext."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.legacy.appctx import AppContext


def test_appctx_proxies_runtime_context(monkeypatch) -> None:
    runtime = SimpleNamespace(
        settings=object(),
        library=object(),
        facade=object(),
        event_bus=object(),
        container=object(),
        translation=object(),
        theme=object(),
        asset_runtime=object(),
        library_session=object(),
        recent_albums=[Path("A")],
        defer_startup_tasks=True,
    )
    calls: dict[str, Path | bool | list[bool]] = {"resume": []}

    def _resume(*, defer_scan: bool = False) -> None:
        resume_calls = calls["resume"]
        assert isinstance(resume_calls, list)
        resume_calls.append(defer_scan)

    def _start_deferred_scan() -> None:
        calls["deferred_scan"] = True

    def _remember(root: Path) -> None:
        calls["remember"] = root

    def _open_library(root: Path):
        calls["open"] = root
        return runtime.library_session

    def _close_library() -> None:
        calls["close"] = True

    runtime.resume_startup_tasks = _resume
    runtime.start_deferred_startup_scan = _start_deferred_scan
    runtime.remember_album = _remember
    runtime.open_library = _open_library
    runtime.close_library = _close_library

    monkeypatch.setattr(
        "iPhoto.bootstrap.runtime_context.RuntimeContext.create",
        lambda *, defer_startup=False: runtime,
    )

    context = AppContext(defer_startup_tasks=True)

    assert context.settings is runtime.settings
    assert context.library is runtime.library
    assert context.facade is runtime.facade
    assert context.event_bus is runtime.event_bus
    assert context.container is runtime.container
    assert context.translation is runtime.translation
    assert context.theme is runtime.theme
    assert context.asset_runtime is runtime.asset_runtime
    assert context.library_session is runtime.library_session
    assert context.recent_albums is runtime.recent_albums

    context.resume_startup_tasks()
    context.resume_startup_tasks(defer_scan=True)
    context.start_deferred_startup_scan()
    context.remember_album(Path("B"))
    assert context.open_library(Path("C")) is runtime.library_session
    context.close_library()

    assert calls["resume"] == [False, True]
    assert calls["deferred_scan"] is True
    assert calls["remember"] == Path("B")
    assert calls["open"] == Path("C")
    assert calls["close"] is True

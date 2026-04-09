"""Compatibility tests for AppContext delegating to RuntimeContext."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.appctx import AppContext


def test_appctx_proxies_runtime_context(monkeypatch) -> None:
    runtime = SimpleNamespace(
        settings=object(),
        library=object(),
        facade=object(),
        container=object(),
        theme=object(),
        asset_runtime=object(),
        recent_albums=[Path("A")],
        defer_startup_tasks=True,
    )
    calls: dict[str, Path | bool] = {}

    def _resume() -> None:
        calls["resume"] = True

    def _remember(root: Path) -> None:
        calls["remember"] = root

    runtime.resume_startup_tasks = _resume
    runtime.remember_album = _remember

    monkeypatch.setattr(
        "iPhoto.bootstrap.runtime_context.RuntimeContext.create",
        lambda *, defer_startup=False: runtime,
    )

    context = AppContext(defer_startup_tasks=True)

    assert context.settings is runtime.settings
    assert context.library is runtime.library
    assert context.facade is runtime.facade
    assert context.container is runtime.container
    assert context.theme is runtime.theme
    assert context.asset_runtime is runtime.asset_runtime
    assert context.recent_albums is runtime.recent_albums

    context.resume_startup_tasks()
    context.remember_album(Path("B"))

    assert calls["resume"] is True
    assert calls["remember"] == Path("B")

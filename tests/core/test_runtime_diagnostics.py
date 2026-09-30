from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iPhoto import runtime_diagnostics


@pytest.fixture(autouse=True)
def _reset_runtime_diagnostics(monkeypatch):
    stream = runtime_diagnostics._STREAM
    if stream is not None and not stream.closed:
        stream.close()
    monkeypatch.setattr(runtime_diagnostics, "_ACTIVE", False)
    monkeypatch.setattr(runtime_diagnostics, "_STREAM", None)
    monkeypatch.setattr(runtime_diagnostics, "_ATEXIT_REGISTERED", False)
    yield
    stream = runtime_diagnostics._STREAM
    if stream is not None and not stream.closed:
        stream.close()


def test_runtime_diagnostics_stays_disabled_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("IPHOTO_RUNTIME_DIAG", raising=False)
    enable = MagicMock()
    monkeypatch.setattr(runtime_diagnostics.faulthandler, "enable", enable)

    assert runtime_diagnostics.enable_runtime_diagnostics() is False
    enable.assert_not_called()


def test_runtime_diagnostics_persistently_dumps_and_shuts_down(
    monkeypatch,
    tmp_path,
) -> None:
    stack_path = tmp_path / "runtime-stacks.log"
    monkeypatch.setenv("IPHOTO_RUNTIME_DIAG", "1")
    monkeypatch.setenv("IPHOTO_RUNTIME_DIAG_STACK_PATH", str(stack_path))
    monkeypatch.setenv("IPHOTO_RUNTIME_DIAG_INTERVAL_SEC", "2")
    enable = MagicMock()
    dump = MagicMock()
    cancel = MagicMock()
    monkeypatch.setattr(runtime_diagnostics.faulthandler, "enable", enable)
    monkeypatch.setattr(
        runtime_diagnostics.faulthandler,
        "dump_traceback_later",
        dump,
    )
    monkeypatch.setattr(
        runtime_diagnostics.faulthandler,
        "cancel_dump_traceback_later",
        cancel,
    )

    assert runtime_diagnostics.enable_runtime_diagnostics() is True
    assert runtime_diagnostics.runtime_diagnostics_active() is True
    enable.assert_called_once()
    assert enable.call_args.kwargs["all_threads"] is True
    dump.assert_called_once()
    assert dump.call_args.args[0] == 5
    assert dump.call_args.kwargs["repeat"] is True

    runtime_diagnostics.shutdown_runtime_diagnostics()

    assert runtime_diagnostics.runtime_diagnostics_active() is False
    cancel.assert_called_once_with()
    records = [json.loads(line) for line in stack_path.read_text().splitlines()]
    assert records[0]["event"] == "runtime_diagnostics_started"
    assert records[0]["interval_seconds"] == 5
    assert records[-1]["event"] == "runtime_diagnostics_stopped"

from __future__ import annotations

import json
from pathlib import Path

from iPhoto.infrastructure.services.performance_events import emit_perf_event


def test_privacy_safe_performance_events_hash_paths(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("IPHOTO_PERF_LOG", "1")
    monkeypatch.setenv("IPHOTO_PERF_PRIVACY_SAFE", "1")
    monkeypatch.setenv("IPHOTO_PERF_PRIVACY_SALT", "test-session")
    source = Path("C:/Users/Alice/Pictures/private-name.jpg")

    emit_perf_event(
        "privacy_test",
        path=source,
        caller="C:/checkout/iPhoto/example.py:10:test",
        nested={"source": str(source), "count": 3},
        outcome="resolved",
    )

    event = json.loads(capsys.readouterr().err)
    assert event["path"].startswith("redacted:")
    assert event["caller"].startswith("redacted:")
    assert event["nested"]["source"] == event["path"]
    assert event["nested"]["count"] == 3
    assert event["outcome"] == "resolved"
    assert "private-name.jpg" not in json.dumps(event)


def test_performance_event_privacy_mode_is_opt_in(monkeypatch, capsys) -> None:
    monkeypatch.setenv("IPHOTO_PERF_LOG", "1")
    monkeypatch.delenv("IPHOTO_PERF_PRIVACY_SAFE", raising=False)

    emit_perf_event("plain_test", key="cache-key")

    event = json.loads(capsys.readouterr().err)
    assert event["key"] == "cache-key"

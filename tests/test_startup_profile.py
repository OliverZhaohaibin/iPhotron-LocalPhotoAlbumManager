from __future__ import annotations

import json
from pathlib import Path

from iPhoto.bootstrap import startup_profile


def test_profile_output_isolated_and_sensitive_paths_are_salted(tmp_path, monkeypatch) -> None:
    output = tmp_path / "isolated.jsonl"
    monkeypatch.setattr(startup_profile, "_ENABLED", True)
    monkeypatch.setattr(startup_profile, "_profile_path", lambda: output)
    monkeypatch.setattr(startup_profile, "_SALT", b"test-salt")
    startup_profile._CONTEXT.clear()

    startup_profile.configure(run_id="run-1", qt_backend="offscreen")
    startup_profile.mark(
        "startup.test",
        root=Path("/Users/example/Photos"),
        database_path=Path("/Users/example/Photos/.iPhoto/global_index.db"),
        reason="failed while opening /Users/example/Photos/private.jpg",
    )

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["context"]["run_id"] == "run-1"
    assert record["context"]["qt_backend"] == "offscreen"
    assert record["details"]["library_id"] == startup_profile.stable_id(
        Path("/Users/example/Photos")
    )
    assert record["details"]["database_path_id"]
    assert "/Users/example" not in output.read_text(encoding="utf-8")
    assert record["details"]["reason"] == "failed while opening <redacted-path>"


def test_stable_id_is_stable_but_does_not_embed_value(monkeypatch) -> None:
    monkeypatch.setattr(startup_profile, "_SALT", b"stable-test-salt")
    first = startup_profile.stable_id("/private/library")
    second = startup_profile.stable_id("/private/library")

    assert first == second
    assert "/private/library" not in first
    assert len(first) == 16

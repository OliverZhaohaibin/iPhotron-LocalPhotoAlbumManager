from __future__ import annotations

import json
from pathlib import Path
from threading import current_thread
from threading import enumerate as enumerate_threads

from iPhoto.gui.detail_profile import emit_detail_event, shutdown_detail_profile


def test_profile_jsonl_is_opened_only_by_background_writer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    shutdown_detail_profile(timeout_ms=100)
    target = tmp_path / "detail.jsonl"
    monkeypatch.setenv("IPHOTO_DETAIL_PROFILE", "1")
    monkeypatch.setenv("IPHOTO_DETAIL_PROFILE_PATH", str(target))
    original_open = Path.open
    append_threads: list[str] = []
    write_threads: list[str] = []

    class TrackedAppendStream:
        def __init__(self, stream) -> None:
            self._stream = stream

        def write(self, value: str) -> int:
            write_threads.append(current_thread().name)
            return self._stream.write(value)

        def __getattr__(self, name: str):
            return getattr(self._stream, name)

    def tracked_open(path: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        stream = original_open(path, *args, **kwargs)
        if mode == "a":
            append_threads.append(current_thread().name)
            return TrackedAppendStream(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    emit_detail_event(
        "scheduled",
        generation=4,
        source=tmp_path / "private" / "photo.jpg",
        nested={"absolute_path": str(tmp_path / "secret.jpg")},
    )
    shutdown_detail_profile(timeout_ms=1000)

    assert append_threads == ["iPhoto-detail-profile-writer"]
    assert write_threads == ["iPhoto-detail-profile-writer"]
    assert not any(
        thread.name == "iPhoto-detail-profile-writer"
        for thread in enumerate_threads()
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["stage"] == "scheduled"
    assert payload["generation"] == 4
    serialized = json.dumps(payload)
    assert str(tmp_path) not in serialized
    assert "source" not in payload["details"]


def test_disabled_profile_creates_neither_writer_nor_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    shutdown_detail_profile(timeout_ms=100)
    target = tmp_path / "disabled.jsonl"
    monkeypatch.delenv("IPHOTO_DETAIL_PROFILE", raising=False)
    monkeypatch.setenv("IPHOTO_DETAIL_PROFILE_PATH", str(target))

    emit_detail_event("scheduled", generation=1)

    assert not target.exists()
    assert not any(
        thread.name == "iPhoto-detail-profile-writer"
        for thread in enumerate_threads()
    )


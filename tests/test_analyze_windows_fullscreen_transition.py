from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools.analyze_windows_fullscreen_transition import analyze_session


def _event(
    stage: str,
    at: float,
    *,
    transition_id: int | None = 7,
    **details: object,
) -> dict[str, object]:
    if transition_id is not None:
        details["transition_id"] = transition_id
    return {
        "stage": stage,
        "monotonic_ms": at,
        "wall_time": at,
        "generation": 0,
        "details": details,
    }


def _successful_events() -> list[dict[str, object]]:
    return [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_captured", 2),
        _event("fullscreen_native_state_confirmed", 3),
        _event("fullscreen_first_frame_requested", 4),
        _event("fullscreen_animation_started", 5),
        _event("fullscreen_media_candidate_rejected", 6, reason="clear_or_stale_submission"),
        _event("fullscreen_media_frame_submitted", 7, ordinal=1, target=[1920, 1080]),
        _event("fullscreen_stable_frame_requested", 8),
        _event("fullscreen_media_frame_submitted", 9, ordinal=2, target=[1920, 1080]),
        _event("fullscreen_stable_frame_submitted", 10),
        _event("fullscreen_animation_finished", 11),
        _event("fullscreen_handoff_finished", 12, automatic_lod=True),
        _event("fullscreen_transition_finished", 13),
        _event("lod_upgrade_requested", 14, transition_id=None, reason="resize"),
        _event("fullscreen_playback_resumed", 132),
    ]


def _write_events(root: Path, events: list[dict[str, object]]) -> None:
    (root / "detail_events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_analyzer_accepts_stable_two_frame_handoff(tmp_path: Path) -> None:
    _write_events(tmp_path, _successful_events())

    result = analyze_session(tmp_path)

    assert result["passed"] is True
    transaction = result["transactions"][0]
    assert transaction["outcome"] == "stable"
    assert transaction["media_ordinals"] == [1, 2]
    assert transaction["rejected_candidates"] == 1


def test_analyzer_reads_zip_and_rejects_lod_before_handoff(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    events = _successful_events()
    events.append(_event("lod_upgrade_staging", 8.5, transition_id=None))
    _write_events(session, events)
    archive_path = tmp_path / "session.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(
            session / "detail_events.jsonl",
            "session/detail_events.jsonl",
        )

    result = analyze_session(archive_path)

    assert result["passed"] is False
    assert "transition_7:automatic_lod_before_handoff" in result["failures"]


def test_analyzer_accepts_bounded_timeout_only_when_auto_lod_is_disabled(
    tmp_path: Path,
) -> None:
    events = [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_fallback", 2),
        _event("fullscreen_native_state_confirmed", 3),
        _event("fullscreen_first_frame_requested", 4),
        _event("fullscreen_first_frame_timeout", 504),
        _event("fullscreen_animation_finished", 505),
        _event("fullscreen_late_frame_timeout", 1504),
        _event("fullscreen_handoff_finished", 1505, automatic_lod=False),
        _event("fullscreen_transition_finished", 1506),
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is True
    assert result["transactions"][0]["outcome"] == "degraded"


def test_analyzer_rejects_timeout_that_enables_automatic_lod(tmp_path: Path) -> None:
    events = [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_fallback", 2),
        _event("fullscreen_native_state_confirmed", 3),
        _event("fullscreen_first_frame_requested", 4),
        _event("fullscreen_animation_finished", 5),
        _event("fullscreen_stable_frame_timeout", 254),
        _event("fullscreen_handoff_finished", 255, automatic_lod=True),
        _event("fullscreen_transition_finished", 256),
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is False
    assert "transition_7:timeout_allows_automatic_lod" in result["failures"]

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
        _event("fullscreen_hold_window_shown", 3),
        _event("fullscreen_hold_window_presented", 35),
        _event("fullscreen_animation_started", 36),
        _event("fullscreen_native_state_requested", 37),
        _event("fullscreen_native_state_confirmed", 40),
        _event("fullscreen_first_frame_requested", 41),
        _event("fullscreen_media_candidate_rejected", 42, reason="clear_or_stale_submission"),
        _event("fullscreen_media_frame_submitted", 43, ordinal=1, target=[1920, 1080]),
        _event("fullscreen_stable_frame_requested", 44),
        _event("fullscreen_media_frame_submitted", 45, ordinal=2, target=[1920, 1080]),
        _event("fullscreen_stable_frame_submitted", 46),
        _event("fullscreen_animation_finished", 256),
        _event("fullscreen_handoff_finished", 316, automatic_lod=True),
        _event("fullscreen_transition_finished", 317),
        _event("lod_upgrade_requested", 318, transition_id=None, reason="resize"),
        _event("fullscreen_playback_resumed", 436),
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
    assert transaction["durations_ms"] == {
        "click_to_hold": 34.0,
        "hold_to_native": 2.0,
        "native_to_media": 3.0,
        "media_to_handoff": 271.0,
    }


def test_analyzer_reads_zip_and_rejects_lod_before_handoff(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    events = _successful_events()
    events.append(_event("lod_upgrade_staging", 100, transition_id=None))
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


def test_analyzer_rejects_native_request_before_hold_presentation(tmp_path: Path) -> None:
    events = [
        event
        for event in _successful_events()
        if event["stage"] != "fullscreen_hold_window_presented"
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is False
    assert "transition_7:missing_fullscreen_hold_window_presented" in result["failures"]


def test_analyzer_accepts_bounded_timeout_only_when_auto_lod_is_disabled(
    tmp_path: Path,
) -> None:
    events = [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_fallback", 2),
        _event("fullscreen_hold_window_shown", 3),
        _event("fullscreen_hold_window_presented", 35),
        _event("fullscreen_animation_started", 36),
        _event("fullscreen_native_state_requested", 37),
        _event("fullscreen_native_state_confirmed", 40),
        _event("fullscreen_first_frame_requested", 41),
        _event("fullscreen_first_frame_timeout", 541),
        _event("fullscreen_animation_finished", 542),
        _event("fullscreen_media_frame_submitted", 600, ordinal=1, target=[1920, 1080]),
        _event("fullscreen_stable_frame_timeout", 791),
        _event("fullscreen_handoff_finished", 792, automatic_lod=False),
        _event("fullscreen_transition_finished", 793),
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is True
    assert result["transactions"][0]["outcome"] == "degraded"


def test_analyzer_rejects_timeout_that_enables_automatic_lod(tmp_path: Path) -> None:
    events = [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_fallback", 2),
        _event("fullscreen_hold_window_shown", 3),
        _event("fullscreen_hold_window_presented", 35),
        _event("fullscreen_animation_started", 36),
        _event("fullscreen_native_state_requested", 37),
        _event("fullscreen_native_state_confirmed", 40),
        _event("fullscreen_first_frame_requested", 41),
        _event("fullscreen_animation_finished", 256),
        _event("fullscreen_stable_frame_timeout", 291),
        _event("fullscreen_handoff_finished", 292, automatic_lod=True),
        _event("fullscreen_transition_finished", 293),
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is False
    assert "transition_7:timeout_allows_automatic_lod" in result["failures"]


def test_analyzer_accepts_no_media_rollback_after_windowed_frame(tmp_path: Path) -> None:
    events = [
        _event("fullscreen_enter_requested", 1),
        _event("fullscreen_snapshot_captured", 2),
        _event("fullscreen_hold_window_shown", 3),
        _event("fullscreen_hold_window_presented", 35),
        _event("fullscreen_animation_started", 36),
        _event("fullscreen_native_state_requested", 37),
        _event("fullscreen_native_state_confirmed", 40),
        _event("fullscreen_first_frame_requested", 41),
        _event("fullscreen_first_frame_timeout", 541),
        _event("fullscreen_late_frame_timeout", 1541),
        _event("fullscreen_enter_rollback", 1542),
        _event("fullscreen_rollback_frame_requested", 1543),
        _event("fullscreen_rollback_frame_submitted", 1600),
        _event("fullscreen_hold_window_cancelled", 1601),
    ]
    _write_events(tmp_path, events)

    result = analyze_session(tmp_path)

    assert result["passed"] is True
    assert result["transactions"][0]["outcome"] == "rollback_after_native"

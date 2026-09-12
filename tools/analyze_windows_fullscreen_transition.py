"""Validate Windows fullscreen media handoff ordering from a collector session."""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

_EVENT_NAME = "detail_events.jsonl"
_SNAPSHOT_STAGES = {
    "fullscreen_snapshot_captured",
    "fullscreen_snapshot_fallback",
}
_AUTOMATIC_LOD_STAGES = {
    "lod_upgrade_requested",
    "lod_upgrade_staging",
    "lod_upgrade_resident",
    "lod_upgrade_activated",
    "lod_plan_submitted",
}


def _read_session_text(session: Path, filename: str) -> str:
    if session.is_dir():
        return (session / filename).read_text(encoding="utf-8-sig")
    with zipfile.ZipFile(session) as archive:
        member = next(
            (name for name in archive.namelist() if name.endswith(f"/{filename}")),
            filename if filename in archive.namelist() else None,
        )
        if member is None:
            raise FileNotFoundError(filename)
        return archive.read(member).decode("utf-8-sig")


def _json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _event_time(event: dict[str, Any]) -> float:
    return float(event.get("monotonic_ms", event.get("wall_time", 0.0)))


def _transition_id(event: dict[str, Any]) -> int | None:
    details = event.get("details") or {}
    value = details.get("transition_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("stage") == stage), None)


def _time_of(events: list[dict[str, Any]], stage: str) -> float | None:
    event = _first(events, stage)
    return _event_time(event) if event is not None else None


def _details(event: dict[str, Any] | None) -> dict[str, Any]:
    return dict(event.get("details") or {}) if event is not None else {}


def _require_order(
    failures: list[str],
    times: dict[str, float | None],
    before: str,
    after: str,
) -> None:
    before_time = times.get(before)
    after_time = times.get(after)
    if before_time is None:
        failures.append(f"missing_{before}")
    if after_time is None:
        failures.append(f"missing_{after}")
    if before_time is not None and after_time is not None and before_time > after_time:
        failures.append(f"order_{before}_after_{after}")


def _analyze_transition(
    transition_id: int,
    events: list[dict[str, Any]],
    contextual_events: list[dict[str, Any]],
) -> dict[str, Any]:
    events = sorted(events, key=_event_time)
    failures: list[str] = []
    stages = [str(event.get("stage", "")) for event in events]
    rollback = "fullscreen_enter_rollback" in stages
    terminal = _first(events, "fullscreen_transition_finished")
    if rollback:
        if terminal is not None:
            failures.append("rollback_has_success_terminal")
        return {
            "transition_id": transition_id,
            "outcome": "rollback",
            "passed": not failures,
            "failures": failures,
            "stages": stages,
        }

    snapshot = next(
        (event for event in events if event.get("stage") in _SNAPSHOT_STAGES),
        None,
    )
    first_timeout = _first(events, "fullscreen_first_frame_timeout")
    stable_timeout = _first(events, "fullscreen_stable_frame_timeout")
    late_timeout = _first(events, "fullscreen_late_frame_timeout")
    handoff = _first(events, "fullscreen_handoff_finished")
    animation_finished = _first(events, "fullscreen_animation_finished")
    media_submissions = [
        event for event in events if event.get("stage") == "fullscreen_media_frame_submitted"
    ]
    ordinals = [int(_details(event).get("ordinal", 0)) for event in media_submissions]
    targets = [tuple(_details(event).get("target") or ()) for event in media_submissions]
    degraded = first_timeout is not None or stable_timeout is not None or late_timeout is not None
    times = {
        "fullscreen_enter_requested": _time_of(events, "fullscreen_enter_requested"),
        "snapshot": _event_time(snapshot) if snapshot is not None else None,
        "fullscreen_native_state_confirmed": _time_of(events, "fullscreen_native_state_confirmed"),
        "fullscreen_first_frame_requested": _time_of(events, "fullscreen_first_frame_requested"),
        "frame_1": next(
            (
                _event_time(event)
                for event in media_submissions
                if _details(event).get("ordinal") == 1
            ),
            None,
        ),
        "frame_2": next(
            (
                _event_time(event)
                for event in media_submissions
                if _details(event).get("ordinal") == 2
            ),
            None,
        ),
        "fullscreen_animation_finished": (
            _event_time(animation_finished) if animation_finished is not None else None
        ),
        "fullscreen_handoff_finished": _event_time(handoff) if handoff is not None else None,
        "fullscreen_transition_finished": (_event_time(terminal) if terminal is not None else None),
    }
    _require_order(
        failures,
        times,
        "fullscreen_enter_requested",
        "snapshot",
    )
    _require_order(
        failures,
        times,
        "snapshot",
        "fullscreen_native_state_confirmed",
    )
    _require_order(
        failures,
        times,
        "fullscreen_native_state_confirmed",
        "fullscreen_first_frame_requested",
    )
    _require_order(
        failures,
        times,
        "fullscreen_animation_finished",
        "fullscreen_handoff_finished",
    )
    _require_order(
        failures,
        times,
        "fullscreen_handoff_finished",
        "fullscreen_transition_finished",
    )

    handoff_details = _details(handoff)
    automatic_lod = bool(handoff_details.get("automatic_lod", False))
    if degraded:
        if automatic_lod:
            failures.append("timeout_allows_automatic_lod")
    else:
        _require_order(failures, times, "frame_1", "frame_2")
        _require_order(
            failures,
            times,
            "frame_2",
            "fullscreen_handoff_finished",
        )
        if ordinals[:2] != [1, 2]:
            failures.append("stable_frame_ordinals")
        if len(targets) >= 2 and targets[0] != targets[1]:
            failures.append("stable_frame_target_mismatch")
        if not automatic_lod:
            failures.append("successful_handoff_disables_automatic_lod")

    native_time = times["fullscreen_native_state_confirmed"]
    handoff_time = times["fullscreen_handoff_finished"]
    premature_lod = [
        event
        for event in contextual_events
        if event.get("stage") in _AUTOMATIC_LOD_STAGES
        and native_time is not None
        and handoff_time is not None
        and native_time <= _event_time(event) < handoff_time
    ]
    if premature_lod:
        failures.append("automatic_lod_before_handoff")

    playback = _first(events, "fullscreen_playback_resumed")
    if playback is not None and handoff_time is not None:
        playback_time = _event_time(playback)
        first_timeout_time = _event_time(first_timeout) if first_timeout is not None else None
        if playback_time < handoff_time and (
            first_timeout_time is None or playback_time < first_timeout_time
        ):
            failures.append("playback_before_media_handoff")

    rejected = [
        event
        for event in events
        if event.get("stage")
        in {
            "fullscreen_media_candidate_rejected",
            "fullscreen_media_frame_rejected",
        }
    ]
    return {
        "transition_id": transition_id,
        "outcome": "degraded" if degraded else "stable",
        "passed": not failures,
        "failures": failures,
        "automatic_lod": automatic_lod,
        "media_ordinals": ordinals,
        "media_targets": targets,
        "rejected_candidates": len(rejected),
        "premature_lod_events": len(premature_lod),
        "stages": stages,
    }


def analyze_session(session: Path) -> dict[str, Any]:
    all_events = sorted(
        _json_lines(_read_session_text(session, _EVENT_NAME)),
        key=_event_time,
    )
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in all_events:
        transition_id = _transition_id(event)
        if transition_id is not None:
            grouped[transition_id].append(event)
    if not grouped:
        raise ValueError("No fullscreen transition events found")

    starts = sorted(
        (
            (_event_time(event), transition_id)
            for transition_id, events in grouped.items()
            for event in events
            if event.get("stage") == "fullscreen_enter_requested"
        ),
    )
    analyses: list[dict[str, Any]] = []
    for index, (start_time, transition_id) in enumerate(starts):
        next_start = starts[index + 1][0] if index + 1 < len(starts) else float("inf")
        contextual = [
            event for event in all_events if start_time <= _event_time(event) < next_start
        ]
        analyses.append(_analyze_transition(transition_id, grouped[transition_id], contextual))
    failures = [
        f"transition_{item['transition_id']}:{failure}"
        for item in analyses
        for failure in item["failures"]
    ]
    return {
        "schema": 1,
        "passed": not failures,
        "failures": failures,
        "transactions": analyses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Collector session directory or ZIP")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()
    result = analyze_session(args.session)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

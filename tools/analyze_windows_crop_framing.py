"""Compare cold and warm cropped-still framing from a Windows diagnostic session."""

from __future__ import annotations

import argparse
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

_EVENT_NAME = "detail_events.jsonl"
_MARKER_NAME = "reproduction_markers.jsonl"
_STAGE = "still_first_frame_transform_committed"
_MARKERS = {
    "cold": "cold_crop_visible",
    "warm": "warm_crop_visible",
}
_NUMERIC_FIELDS = (
    "cover_scale",
    "zoom_factor",
    "effective_scale",
    "pan_x",
    "pan_y",
    "center_error_x",
    "center_error_y",
)


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


def _marker_wall_time(marker: dict[str, Any]) -> float:
    return datetime.fromisoformat(str(marker["utc_time"]).replace("Z", "+00:00")).timestamp()


def _latest_stage_before(
    events: list[dict[str, Any]],
    wall_time: float,
) -> dict[str, Any]:
    candidates = [
        event
        for event in events
        if event.get("stage") == _STAGE
        and float(event.get("wall_time", 0.0)) <= wall_time
    ]
    if not candidates:
        raise ValueError(f"No {_STAGE} event precedes marker at {wall_time}")
    return max(candidates, key=lambda event: float(event.get("wall_time", 0.0)))


def analyze_session(session: Path) -> dict[str, Any]:
    events = _json_lines(_read_session_text(session, _EVENT_NAME))
    markers = _json_lines(_read_session_text(session, _MARKER_NAME))
    selected: dict[str, dict[str, Any]] = {}
    for label, marker_name in _MARKERS.items():
        marker = next(
            (item for item in markers if item.get("marker") == marker_name),
            None,
        )
        if marker is None:
            raise ValueError(f"Missing reproduction marker: {marker_name}")
        event = _latest_stage_before(events, _marker_wall_time(marker))
        selected[label] = {
            "generation": int(event.get("generation", 0)),
            **dict(event.get("details") or {}),
        }

    cold = selected["cold"]
    warm = selected["warm"]
    deltas = {
        field: float(warm.get(field, 0.0)) - float(cold.get(field, 0.0))
        for field in _NUMERIC_FIELDS
    }
    failures: list[str] = []
    for field in ("cover_scale", "zoom_factor", "effective_scale"):
        if not math.isclose(
            float(cold.get(field, 0.0)),
            float(warm.get(field, 0.0)),
            rel_tol=1e-4,
            abs_tol=1e-4,
        ):
            failures.append(f"{field}_mismatch")
    for field in ("pan_x", "pan_y"):
        if abs(deltas[field]) > 1.0:
            failures.append(f"{field}_mismatch")
    for label, values in selected.items():
        if values.get("geometry_source") != "viewer_image":
            failures.append(f"{label}_geometry_source")
        if abs(float(values.get("center_error_x", 0.0))) > 1.0:
            failures.append(f"{label}_center_error_x")
        if abs(float(values.get("center_error_y", 0.0))) > 1.0:
            failures.append(f"{label}_center_error_y")
    if (
        int(cold.get("target_width", 0)),
        int(cold.get("target_height", 0)),
    ) != (
        int(warm.get("target_width", 0)),
        int(warm.get("target_height", 0)),
    ):
        failures.append("target_size_mismatch")
    selected_generations = {
        int(values.get("generation", 0)) for values in selected.values()
    }
    if any(
        event.get("stage") == "still_first_frame_cover_drift"
        and int(event.get("generation", 0)) in selected_generations
        for event in events
    ):
        failures.append("first_frame_cover_drift")
    return {
        "schema": 1,
        "cold": cold,
        "warm": warm,
        "deltas": deltas,
        "passed": not failures,
        "failures": failures,
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

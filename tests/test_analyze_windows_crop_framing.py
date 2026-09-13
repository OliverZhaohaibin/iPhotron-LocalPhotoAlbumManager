from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools.analyze_windows_crop_framing import analyze_session


def _write_session(root: Path, *, warm_cover: float = 1.397) -> None:
    events = [
        {
            "stage": "still_first_frame_transform_committed",
            "wall_time": 100.0,
            "generation": 1,
            "details": {
                "target_width": 1600,
                "target_height": 900,
                "geometry_source": "viewer_image",
                "gpu_residency_state": "cold_upload_pending",
                "cover_scale": 1.397,
                "zoom_factor": 1.2,
                "effective_scale": 4.5,
                "pan_x": -120.0,
                "pan_y": 40.0,
                "center_error_x": 0.1,
                "center_error_y": -0.1,
            },
        },
        {
            "stage": "still_first_frame_transform_committed",
            "wall_time": 200.0,
            "generation": 2,
            "details": {
                "target_width": 1600,
                "target_height": 900,
                "geometry_source": "viewer_image",
                "gpu_residency_state": "resident_activation_pending",
                "cover_scale": warm_cover,
                "zoom_factor": 1.2,
                "effective_scale": 4.5,
                "pan_x": -120.0,
                "pan_y": 40.0,
                "center_error_x": 0.0,
                "center_error_y": 0.0,
            },
        },
    ]
    markers = [
        {"marker": "cold_crop_visible", "utc_time": "1970-01-01T00:01:41+00:00"},
        {"marker": "warm_crop_visible", "utc_time": "1970-01-01T00:03:21+00:00"},
    ]
    (root / "detail_events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    (root / "reproduction_markers.jsonl").write_text(
        "\n".join(json.dumps(item) for item in markers) + "\n",
        encoding="utf-8",
    )


def test_analyzer_accepts_matching_cold_and_warm_framing(tmp_path: Path) -> None:
    _write_session(tmp_path)

    result = analyze_session(tmp_path)

    assert result["passed"] is True
    assert result["failures"] == []
    assert result["cold"]["gpu_residency_state"] == "cold_upload_pending"
    assert result["warm"]["gpu_residency_state"] == "resident_activation_pending"


def test_analyzer_reads_collector_zip_and_rejects_cover_drift(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _write_session(session, warm_cover=1.0)
    archive_path = tmp_path / "session.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in session.iterdir():
            archive.write(path, f"session/{path.name}")

    result = analyze_session(archive_path)

    assert result["passed"] is False
    assert "cover_scale_mismatch" in result["failures"]


def test_analyzer_rejects_runtime_cover_drift_event(tmp_path: Path) -> None:
    _write_session(tmp_path)
    with (tmp_path / "detail_events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": "still_first_frame_cover_drift",
                    "wall_time": 100.5,
                    "generation": 1,
                    "details": {},
                }
            )
            + "\n"
        )

    result = analyze_session(tmp_path)

    assert result["passed"] is False
    assert "first_frame_cover_drift" in result["failures"]

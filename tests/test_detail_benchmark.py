from __future__ import annotations

import json

from tools.detail_benchmark import compare, summarize


def test_detail_benchmark_summarizes_completed_and_cancelled(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    events = [
        {"stage": "click_received", "generation": 1, "monotonic_ms": 100.0},
        {"stage": "route_visible", "generation": 1, "monotonic_ms": 105.0},
        {"stage": "image_presented", "generation": 1, "monotonic_ms": 180.0},
        {"stage": "face_presented", "generation": 1, "monotonic_ms": 200.0},
        {"stage": "click_received", "generation": 2, "monotonic_ms": 300.0},
        {"stage": "cancelled", "generation": 2, "monotonic_ms": 310.0},
    ]
    trace.write_text("".join(json.dumps(value) + "\n" for value in events))

    result = summarize([trace])

    assert result["cancelled"] == 1
    assert result["metrics"]["click_to_image"]["p95_ms"] == 80.0
    assert result["metrics"]["image_to_face"]["p50_ms"] == 20.0


def test_detail_benchmark_compares_percent_improvement() -> None:
    baseline = {"metrics": {"click_to_image": {"p50_ms": 100, "p95_ms": 200}}}
    candidate = {"metrics": {"click_to_image": {"p50_ms": 50, "p95_ms": 150}}}

    result = compare(baseline, candidate)

    metric = result["comparisons"]["click_to_image"]
    assert metric["p50_improvement_percent"] == 50.0
    assert metric["p95_improvement_percent"] == 25.0

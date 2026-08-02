from __future__ import annotations

import json

from tools.detail_benchmark import (
    compare,
    summarize,
    validate_comparison,
    validate_summary,
)


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


def test_detail_benchmark_validates_category_relative_improvement() -> None:
    baseline = {
        "categories": {
            "jpeg-cold": {
                "click_to_image": {"p50_ms": 100, "p95_ms": 200},
            }
        }
    }
    candidate = {
        "categories": {
            "jpeg-cold": {
                "click_to_image": {"p50_ms": 60, "p95_ms": 150},
            }
        }
    }

    validation = validate_comparison(compare(baseline, candidate))

    assert validation["passed"] is True
    assert len(validation["checks"]) == 2


def test_detail_benchmark_accepts_production_presented_and_reports_cache_tiers(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    events = [
        {"stage": "click_received", "generation": 4, "monotonic_ms": 10.0},
        {
            "stage": "surface_cache_hit",
            "generation": 4,
            "monotonic_ms": 12.0,
            "details": {"tier": "disk"},
        },
        {"stage": "gpu_upload", "generation": 4, "monotonic_ms": 15.0},
        {
            "stage": "backend_selected",
            "generation": 4,
            "monotonic_ms": 16.0,
            "details": {"backend": "wic"},
        },
        {
            "stage": "decode_fallback",
            "generation": 4,
            "monotonic_ms": 17.0,
            "details": {"fallback": "wic_to_qt"},
        },
        {"stage": "presented", "generation": 4, "monotonic_ms": 20.0},
    ]
    trace.write_text("".join(json.dumps(value) + "\n" for value in events))

    result = summarize([trace])

    assert result["schema"] == 2
    assert result["metrics"]["click_to_image"]["p50_ms"] == 10.0
    assert result["diagnostics"]["surface_cache_hits"] == {"disk": 1}
    assert result["diagnostics"]["gpu_upload_count"] == 1
    assert result["diagnostics"]["backend_distribution"] == {"wic": 1}
    assert result["diagnostics"]["fallback_distribution"] == {"wic_to_qt": 1}


def test_detail_benchmark_excludes_unmeasured_disk_warmup(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    events = [
        {"stage": "click_received", "generation": 1, "monotonic_ms": 10.0},
        {
            "stage": "benchmark_warmup_started",
            "generation": 1,
            "monotonic_ms": 11.0,
            "details": {"category": "heic-hot-disk"},
        },
        {"stage": "gui_task", "generation": 1, "monotonic_ms": 12.0,
         "details": {"duration_ms": 500.0}},
        {"stage": "presented", "generation": 1, "monotonic_ms": 510.0},
        {"stage": "click_received", "generation": 2, "monotonic_ms": 600.0},
        {
            "stage": "benchmark_sample_started",
            "generation": 2,
            "monotonic_ms": 601.0,
            "details": {"category": "heic-hot-disk"},
        },
        {"stage": "gui_task", "generation": 2, "monotonic_ms": 602.0,
         "details": {"duration_ms": 2.0}},
        {"stage": "presented", "generation": 2, "monotonic_ms": 620.0},
    ]
    trace.write_text("".join(json.dumps(value) + "\n" for value in events))

    result = summarize([trace])

    assert result["categories"]["heic-hot-disk"]["click_to_image"]["count"] == 1
    assert result["metrics"]["click_to_image"]["p95_ms"] == 20.0
    assert result["diagnostics"]["gui_task"]["p95_ms"] == 2.0


def test_detail_benchmark_groups_categories_and_validates_strict_gates(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    events = []
    for generation in range(1, 31):
        events.extend(
            [
                {"stage": "click_received", "generation": generation, "monotonic_ms": 100.0},
                {
                    "stage": "benchmark_sample_started",
                    "generation": generation,
                    "monotonic_ms": 101.0,
                    "details": {"category": "jpeg-hot"},
                },
                {"stage": "route_visible", "generation": generation, "monotonic_ms": 110.0},
                {
                    "stage": "gui_task",
                    "generation": generation,
                    "monotonic_ms": 115.0,
                    "details": {"duration_ms": 5.0},
                },
                {"stage": "presented", "generation": generation, "monotonic_ms": 150.0},
            ]
        )
    trace.write_text("".join(json.dumps(value) + "\n" for value in events))

    summary = summarize([trace])
    validation = validate_summary(summary)

    assert summary["categories"]["jpeg-hot"]["click_to_image"]["count"] == 30
    assert validation["passed"] is True
    limits = {check["name"]: check["limit"] for check in validation["checks"]}
    assert limits["click_to_route.p95_ms"] == 32.0
    assert limits["gui_task.p95_ms"] == 40.0
    assert limits["jpeg-hot.click_to_image"] == 100.0

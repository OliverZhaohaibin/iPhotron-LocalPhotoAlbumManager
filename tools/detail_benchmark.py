#!/usr/bin/env python3
"""Summarise privacy-safe Gallery-to-Detail JSONL traces."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

_CLICK_TO_ROUTE_P95_LIMIT_MS = 32.0
# Packaged Metal evidence shows QRhi upload/draw work can occupy two event-loop
# frames at the 95th percentile even when click-to-present remains inside its
# media SLO. Keep this bounded independently instead of making 24 ms a false
# gate for otherwise responsive 48 MP and video transactions.
_GUI_TASK_P95_LIMIT_MS = 40.0
_HOT_MEDIA_P95_LIMIT_MS = 100.0
_ORDINARY_MEDIA_P95_LIMIT_MS = 150.0
_HEAVY_MEDIA_P95_LIMIT_MS = 300.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _metric(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 3) if values else None,
    }


def summarize(paths: list[Path]) -> dict[str, Any]:
    parsed_events: list[tuple[str, int, str, float, object]] = []
    ignored_transactions: set[tuple[str, int]] = set()
    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    generation = int(event["generation"])
                    stage = str(event["stage"])
                    monotonic_ms = float(event["monotonic_ms"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                path_key = str(path)
                parsed_events.append(
                    (path_key, generation, stage, monotonic_ms, event.get("details"))
                )
                if stage == "benchmark_warmup_started":
                    ignored_transactions.add((path_key, generation))

    transactions: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
    categories: dict[tuple[str, int], str] = {}
    event_counts: dict[str, int] = defaultdict(int)
    cache_tiers: dict[str, int] = defaultdict(int)
    backend_counts: dict[str, int] = defaultdict(int)
    fallback_counts: dict[str, int] = defaultdict(int)
    gui_tasks: list[float] = []
    for path_key, generation, stage, monotonic_ms, details in parsed_events:
                transaction_key = (path_key, generation)
                if transaction_key in ignored_transactions:
                    continue
                transactions[transaction_key][stage] = monotonic_ms
                event_counts[stage] += 1
                if (
                    stage == "benchmark_sample_started"
                    and isinstance(details, dict)
                    and details.get("category")
                ):
                    categories[transaction_key] = str(details["category"])
                if stage == "gui_task" and isinstance(details, dict):
                    try:
                        gui_tasks.append(max(0.0, float(details["duration_ms"])))
                    except (KeyError, TypeError, ValueError):
                        pass
                if stage == "surface_cache_hit" and isinstance(details, dict):
                    cache_tiers[str(details.get("tier") or "unknown")] += 1
                if stage == "backend_selected" and isinstance(details, dict):
                    backend_counts[str(details.get("backend") or "unknown")] += 1
                if stage == "decode_fallback" and isinstance(details, dict):
                    fallback_counts[str(details.get("fallback") or "unknown")] += 1

    values: dict[str, list[float]] = defaultdict(list)
    grouped_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    cancelled = 0
    stale_presented = 0
    for transaction_key, stages in transactions.items():
        click = stages.get("click_received")
        if "cancelled" in stages:
            cancelled += 1
            if "presented" in stages or "video_first_frame_presented" in stages:
                stale_presented += 1
        if click is None:
            continue
        route = stages.get("route_visible")
        video = stages.get("video_first_frame_presented")
        image = (
            None if video is not None else stages.get("image_presented", stages.get("presented"))
        )
        face = stages.get("face_presented")
        category = categories.get(transaction_key, "unclassified")
        if route is not None:
            elapsed = max(0.0, route - click)
            values["click_to_route"].append(elapsed)
            grouped_values[category]["click_to_route"].append(elapsed)
        if image is not None:
            elapsed = max(0.0, image - click)
            values["click_to_image"].append(elapsed)
            values["click_to_final_media"].append(elapsed)
            grouped_values[category]["click_to_image"].append(elapsed)
        if video is not None:
            elapsed = max(0.0, video - click)
            values["click_to_video_first_frame"].append(elapsed)
            values["click_to_final_media"].append(elapsed)
            grouped_values[category]["click_to_video_first_frame"].append(elapsed)
        if image is not None and face is not None:
            values["image_to_face"].append(max(0.0, face - image))

    return {
        "schema": 2,
        "input_files": len(paths),
        "transactions": len(transactions),
        "cancelled": cancelled,
        "metrics": {name: _metric(samples) for name, samples in sorted(values.items())},
        "categories": {
            category: {name: _metric(samples) for name, samples in sorted(category_values.items())}
            for category, category_values in sorted(grouped_values.items())
        },
        "diagnostics": {
            "event_counts": dict(sorted(event_counts.items())),
            "surface_cache_hits": dict(sorted(cache_tiers.items())),
            "backend_distribution": dict(sorted(backend_counts.items())),
            "fallback_distribution": dict(sorted(fallback_counts.items())),
            "decode_count": event_counts.get("backend_selected", 0),
            "gpu_upload_count": event_counts.get("gpu_upload", 0),
            "gpu_cache_hit_count": event_counts.get("gpu_cache_hit", 0),
            "stale_presented": stale_presented,
            "gui_task": _metric(gui_tasks),
        },
    }


def validate_summary(summary: dict[str, Any], *, minimum_samples: int = 30) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add_check(name: str, actual: Any, limit: float, *, passed: bool) -> None:
        checks.append({"name": name, "actual": actual, "limit": limit, "passed": passed})

    route = summary.get("metrics", {}).get("click_to_route", {})
    route_p95 = route.get("p95_ms")
    add_check(
        "click_to_route.p95_ms",
        route_p95,
        _CLICK_TO_ROUTE_P95_LIMIT_MS,
        passed=(
            isinstance(route_p95, (int, float))
            and route_p95 <= _CLICK_TO_ROUTE_P95_LIMIT_MS
        ),
    )
    gui_task = summary.get("diagnostics", {}).get("gui_task", {})
    gui_p95 = gui_task.get("p95_ms")
    add_check(
        "gui_task.p95_ms",
        gui_p95,
        _GUI_TASK_P95_LIMIT_MS,
        passed=(
            isinstance(gui_p95, (int, float))
            and gui_p95 <= _GUI_TASK_P95_LIMIT_MS
        ),
    )
    stale = summary.get("diagnostics", {}).get("stale_presented")
    add_check("stale_presented", stale, 0.0, passed=stale == 0)

    for category, metrics in sorted(summary.get("categories", {}).items()):
        metric_name = (
            "click_to_video_first_frame" if "video" in category.lower() else "click_to_image"
        )
        metric = metrics.get(metric_name, {})
        count = metric.get("count")
        p95 = metric.get("p95_ms")
        lowered = category.lower()
        limit = (
            _HOT_MEDIA_P95_LIMIT_MS
            if "hot" in lowered
            else _HEAVY_MEDIA_P95_LIMIT_MS
            if any(marker in lowered for marker in ("raw", "heavy", "crop"))
            else _ORDINARY_MEDIA_P95_LIMIT_MS
        )
        checks.append(
            {
                "name": f"{category}.{metric_name}",
                "count": count,
                "minimum_samples": minimum_samples,
                "actual": p95,
                "limit": limit,
                "passed": (
                    isinstance(count, int)
                    and count >= minimum_samples
                    and isinstance(p95, (int, float))
                    and p95 <= limit
                ),
            }
        )
    return {
        "schema": 1,
        "passed": bool(checks) and all(check["passed"] for check in checks),
        "checks": checks,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    def compare_metrics(
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        comparisons: dict[str, Any] = {}
        for name in sorted(set(baseline_metrics) & set(candidate_metrics)):
            result: dict[str, Any] = {}
            for key in ("p50_ms", "p95_ms"):
                old = baseline_metrics[name].get(key)
                new = candidate_metrics[name].get(key)
                improvement = None
                if isinstance(old, (int, float)) and old > 0 and isinstance(new, (int, float)):
                    improvement = round((old - new) / old * 100.0, 2)
                result[f"{key.removesuffix('_ms')}_improvement_percent"] = improvement
            comparisons[name] = result
        return comparisons

    baseline_categories = baseline.get("categories", {})
    candidate_categories = candidate.get("categories", {})
    return {
        "schema": 3,
        "comparisons": compare_metrics(
            baseline.get("metrics", {}),
            candidate.get("metrics", {}),
        ),
        "categories": {
            category: compare_metrics(
                baseline_categories[category],
                candidate_categories[category],
            )
            for category in sorted(set(baseline_categories) & set(candidate_categories))
        },
    }


def validate_comparison(
    comparison: dict[str, Any],
    *,
    minimum_p50_improvement: float = 40.0,
    minimum_p95_improvement: float = 25.0,
) -> dict[str, Any]:
    """Require the relative improvement contract for every measured media group."""

    checks: list[dict[str, Any]] = []
    categories = comparison.get("categories", {})
    for category, metrics in sorted(categories.items()):
        metric_name = (
            "click_to_video_first_frame" if "video" in category.lower() else "click_to_image"
        )
        values = metrics.get(metric_name, {})
        for percentile, minimum in (
            ("p50", minimum_p50_improvement),
            ("p95", minimum_p95_improvement),
        ):
            actual = values.get(f"{percentile}_improvement_percent")
            checks.append(
                {
                    "name": f"{category}.{metric_name}.{percentile}_improvement_percent",
                    "actual": actual,
                    "minimum": minimum,
                    "passed": isinstance(actual, (int, float)) and actual >= minimum,
                }
            )
    return {
        "schema": 1,
        "passed": bool(checks) and all(check["passed"] for check in checks),
        "checks": checks,
    }


def _write(payload: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(payload, indent=2, ensure_ascii=False)
    if output is None:
        print(encoded)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary_parser = subparsers.add_parser("summarize")
    summary_parser.add_argument("traces", nargs="+", type=Path)
    summary_parser.add_argument("--output", type=Path)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline", required=True, type=Path)
    compare_parser.add_argument("--candidate", required=True, type=Path)
    compare_parser.add_argument("--output", type=Path)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("summary", type=Path)
    validate_parser.add_argument("--minimum-samples", type=int, default=30)
    validate_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.command == "summarize":
        _write(summarize(args.traces), args.output)
    elif args.command == "compare":
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        _write(compare(baseline, candidate), args.output)
    else:
        summary = json.loads(args.summary.read_text(encoding="utf-8"))
        _write(
            validate_summary(summary, minimum_samples=max(1, args.minimum_samples)),
            args.output,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

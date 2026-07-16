#!/usr/bin/env python3
"""Summarise privacy-safe Gallery-to-Detail JSONL traces."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


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
    transactions: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
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
                transactions[(str(path), generation)][stage] = monotonic_ms

    values: dict[str, list[float]] = defaultdict(list)
    cancelled = 0
    for stages in transactions.values():
        click = stages.get("click_received")
        if "cancelled" in stages:
            cancelled += 1
        if click is None:
            continue
        route = stages.get("route_visible")
        image = stages.get("image_presented")
        video = stages.get("video_first_frame_presented")
        face = stages.get("face_presented")
        if route is not None:
            values["click_to_route"].append(max(0.0, route - click))
        if image is not None:
            values["click_to_image"].append(max(0.0, image - click))
            values["click_to_final_media"].append(max(0.0, image - click))
        if video is not None:
            values["click_to_video_first_frame"].append(max(0.0, video - click))
            values["click_to_final_media"].append(max(0.0, video - click))
        if image is not None and face is not None:
            values["image_to_face"].append(max(0.0, face - image))

    return {
        "schema": 1,
        "input_files": len(paths),
        "transactions": len(transactions),
        "cancelled": cancelled,
        "metrics": {name: _metric(samples) for name, samples in sorted(values.items())},
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    baseline_metrics = baseline.get("metrics", {})
    candidate_metrics = candidate.get("metrics", {})
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
    return {"schema": 1, "comparisons": comparisons}


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
    args = parser.parse_args(argv)

    if args.command == "summarize":
        _write(summarize(args.traces), args.output)
    else:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        _write(compare(baseline, candidate), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

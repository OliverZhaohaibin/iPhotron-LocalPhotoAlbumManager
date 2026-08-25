#!/usr/bin/env python3
"""Collect, validate, aggregate, and compare iPhotron startup profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

TERMINAL_STAGES = frozenset(
    {
        "startup.completed",
        "startup.degraded",
        "startup.failed",
        "startup.cancelled",
    }
)
MILESTONE_ALIASES: dict[str, tuple[str, ...]] = {
    "app_created": ("startup.app_created", "qapplication.created"),
    "show": ("startup.show", "main_window.show_called"),
    "first_paint": ("main_window.first_paint",),
    "interactive": ("startup.interactive",),
    "library_ready": ("startup.library_ready",),
    "first_gallery_visible": (
        "startup.first_gallery_visible",
        "gallery_startup_warmup.first_window_applied",
    ),
    "first_usable_thumbnail": (
        "startup.first_usable_thumbnail",
        "gallery_startup_warmup.first_full_thumbnail_ready",
    ),
}
METRIC_ORDER = (
    "process_start_app_created_ms",
    "app_created_show_ms",
    "show_first_paint_ms",
    "first_paint_interactive_ms",
    "show_interactive_ms",
    "interactive_library_ready_ms",
    "library_ready_first_gallery_ms",
    "first_gallery_first_thumbnail_ms",
    "first_gallery_visible_ms",
    "first_usable_thumbnail_ms",
    "probe_ms",
    "max_gui_job_ms",
    "max_post_interactive_gui_stall_ms",
    "interactive_recognition_activation_ms",
)
RESOURCE_SNAPSHOT_NAMES = (
    "interactive",
    "recognition_activation",
    "recognition_plus_1500ms",
    "recognition_plus_5000ms",
)
BATCH_CONTEXT_KEYS = (
    "revision",
    "runtime",
    "platform",
    "architecture",
    "qt_backend",
    "graphics_backend",
    "cache_state",
    "cache_controlled",
    "cache_eviction_method",
    "scenario",
    "scenario_env_names",
    "build_environment_fingerprint",
    "artifact_sha256",
    "manifest_source_revision",
)
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|(?<![\w.])/(?:users|home|mnt|media|volumes|private|tmp)/)"
)


class ProfileError(ValueError):
    """Raised when a profile cannot be used for performance evidence."""


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Return the nearest-rank percentile used by the startup gate."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((float(percentile) / 100.0) * len(ordered)))
    return round(ordered[rank - 1], 3)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_build_manifest(
    path: Path,
    *,
    revision: str,
    command: Sequence[str],
    cwd: Path,
) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read build manifest: {exc}") from exc
    if not isinstance(manifest, dict) or int(manifest.get("schema_version", -1)) != 1:
        raise ProfileError("build manifest schema is missing or unsupported")
    source_revision = str(manifest.get("source_revision") or "")
    if not source_revision or not source_revision.startswith(revision):
        raise ProfileError("build manifest revision does not match --revision")
    environment_fingerprint = str(manifest.get("environment_fingerprint") or "")
    manifest_artifact = str(manifest.get("artifact_sha256") or "")
    if len(environment_fingerprint) != 64 or len(manifest_artifact) != 64:
        raise ProfileError("build manifest fingerprints are missing or invalid")
    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        cwd_candidate = cwd.expanduser().resolve() / executable
        if cwd_candidate.is_file():
            executable = cwd_candidate
    if not executable.is_file():
        resolved = shutil.which(command[0])
        executable = Path(resolved) if resolved else executable
    if not executable.is_file():
        raise ProfileError(f"cannot fingerprint packaged command: {command[0]}")
    artifact_sha256 = _sha256_file(executable.resolve())
    if artifact_sha256 != manifest_artifact:
        raise ProfileError("build manifest artifact does not match packaged command")
    return {
        "build_environment_fingerprint": environment_fingerprint,
        "artifact_sha256": artifact_sha256,
        "manifest_source_revision": source_revision,
    }


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProfileError(f"cannot read profile: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProfileError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("stage"), str):
            raise ProfileError(f"invalid event on line {line_number}")
        try:
            float(event["elapsed_ms"])
            float(event["wall_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(f"invalid timestamp on line {line_number}") from exc
        events.append(event)
    if not events:
        raise ProfileError("profile is empty")
    return events


def _details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details")
    return details if isinstance(details, dict) else {}


def _event(events: Sequence[dict[str, Any]], milestone: str) -> dict[str, Any] | None:
    aliases = MILESTONE_ALIASES[milestone]
    for event in events:
        if event["stage"] in aliases:
            return event
        if event["stage"] == "startup.phase" and _details(event).get("phase") == milestone:
            return event
    return None


def _terminal_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["stage"] in TERMINAL_STAGES]


def _validate_generation_terminals(events: Sequence[dict[str, Any]]) -> list[str]:
    """Require exactly one terminal record for every observed startup generation."""

    observed: set[int] = set()
    terminal_counts: Counter[int] = Counter()
    for event in events:
        details = _details(event)
        raw_generation = details.get("generation")
        try:
            generation = int(raw_generation)
        except (TypeError, ValueError):
            continue
        if generation <= 0:
            continue
        if event["stage"].startswith("startup."):
            observed.add(generation)
        if event["stage"] in TERMINAL_STAGES:
            terminal_counts[generation] += 1
    return [
        f"expected exactly one terminal event for generation {generation}, "
        f"found {terminal_counts[generation]}"
        for generation in sorted(observed)
        if terminal_counts[generation] != 1
    ]


def _duration(start: dict[str, Any] | None, end: dict[str, Any] | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, float(end["elapsed_ms"]) - float(start["elapsed_ms"])), 3)


def _nearest_resource_sample(
    samples: Sequence[dict[str, Any]],
    target_wall_time: float,
) -> dict[str, Any] | None:
    if not samples:
        return None
    return min(
        samples,
        key=lambda item: abs(float(item["wall_time"]) - float(target_wall_time)),
    )


def _resource_snapshots(events: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    samples = [event for event in events if event["stage"] == "launcher.resource_sample"]
    interactive = _event(events, "interactive")
    activation = next(
        (event for event in events if event["stage"] == "recognition.startup.activated"),
        None,
    )
    targets = {
        "interactive": float(interactive["wall_time"]) if interactive is not None else None,
        "recognition_activation": (
            float(activation["wall_time"]) if activation is not None else None
        ),
        "recognition_plus_1500ms": (
            float(activation["wall_time"]) + 1.5 if activation is not None else None
        ),
        "recognition_plus_5000ms": (
            float(activation["wall_time"]) + 5.0 if activation is not None else None
        ),
    }
    snapshots: dict[str, dict[str, Any] | None] = {}
    for name, target in targets.items():
        sample = _nearest_resource_sample(samples, target) if target is not None else None
        snapshots[name] = dict(_details(sample)) if sample is not None else None
    return snapshots


def _contains_path(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_ABSOLUTE_PATH.search(value))
    if isinstance(value, dict):
        return any(_contains_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_path(item) for item in value)
    return False


def _validate_jobs(events: Sequence[dict[str, Any]]) -> list[str]:
    active: dict[tuple[int, str], int] = defaultdict(int)
    errors: list[str] = []
    for event in events:
        if event["stage"] not in {"startup.gui_job.started", "startup.gui_job.finished"}:
            continue
        details = _details(event)
        key = (int(details.get("generation", 0)), str(details.get("job", "")))
        if not key[1]:
            errors.append("GUI job event is missing a name")
            continue
        if event["stage"].endswith("started"):
            active[key] += 1
        elif active[key] <= 0:
            errors.append(f"GUI job finished without start: {key[1]}")
        else:
            active[key] -= 1
    errors.extend(
        f"GUI job started without finish: {job}"
        for (_generation, job), count in active.items()
        if count
    )
    return errors


def analyse_run(path: Path, *, require_gallery: bool = True) -> dict[str, Any]:
    """Validate one profile and return normalized per-run metrics."""

    errors: list[str] = []
    try:
        events = load_events(path)
    except ProfileError as exc:
        return {"path": str(path), "valid": False, "eligible": False, "errors": [str(exc)]}

    previous_elapsed = -1.0
    for event in events:
        if event["stage"] == "launcher.resource_sample":
            continue
        elapsed = float(event["elapsed_ms"])
        if elapsed < previous_elapsed:
            errors.append("event elapsed_ms is out of order")
            break
        previous_elapsed = elapsed
        if _contains_path(event.get("details", {})):
            errors.append(f"sensitive path detected in {event['stage']}")
            break

    errors.extend(_validate_jobs(events))
    launcher_started = next(
        (event for event in events if event["stage"] == "launcher.process_started"), None
    )
    launcher_finished = next(
        (event for event in events if event["stage"] == "launcher.process_finished"), None
    )
    if launcher_started is not None and launcher_finished is None:
        errors.append("launcher process finish event is missing")
    elif launcher_finished is not None:
        finish_details = _details(launcher_finished)
        if bool(finish_details.get("timed_out", False)):
            errors.append("launcher process timed out")
        try:
            return_code = int(finish_details["return_code"])
        except (KeyError, TypeError, ValueError):
            errors.append("launcher process return code is missing or invalid")
        else:
            if return_code != 0:
                errors.append(f"launcher process exited with code {return_code}")
    terminals = _terminal_events(events)
    errors.extend(_validate_generation_terminals(events))
    terminal = terminals[-1] if terminals else None
    completed = terminal is not None and terminal["stage"] == "startup.completed"

    milestones = {name: _event(events, name) for name in MILESTONE_ALIASES}
    if milestones["interactive"] is None:
        milestones["interactive"] = next(
            (event for event in events if event["stage"] in {"startup.degraded", "startup.failed"}),
            None,
        )
    required = ["app_created", "show", "first_paint", "interactive"]
    if completed and require_gallery:
        required.extend(("library_ready", "first_gallery_visible", "first_usable_thumbnail"))
    for name in required:
        if milestones[name] is None:
            errors.append(f"missing milestone: {name}")

    if all(milestones[name] is not None for name in ("show", "interactive")):
        if float(milestones["interactive"]["elapsed_ms"]) < float(milestones["show"]["elapsed_ms"]):
            errors.append("interactive precedes show")

    app_created = milestones["app_created"]
    if launcher_started is not None and app_created is not None:
        process_app_ms = round(
            max(
                0.0,
                (
                    float(app_created["wall_time"])
                    - float(launcher_started["wall_time"])
                )
                * 1000.0,
            ),
            3,
        )
    elif app_created is not None:
        process_app_ms = round(float(app_created["elapsed_ms"]), 3)
    else:
        process_app_ms = None

    interactive = milestones["interactive"]
    post_interactive_durations = [
        float(_details(event).get("duration_ms", 0.0))
        for event in events
        if event["stage"] == "startup.gui_job.finished"
        and interactive is not None
        and float(event["elapsed_ms"]) >= float(interactive["elapsed_ms"])
    ]
    all_gui_job_durations = [
        float(_details(event).get("duration_ms", 0.0))
        for event in events
        if event["stage"] == "startup.gui_job.finished"
    ]
    probe_started = next(
        (event for event in events if event["stage"] == "startup.probe.started"), None
    )
    probe_finished = next(
        (event for event in events if event["stage"] == "startup.probe.finished"), None
    )
    recognition_activated = next(
        (event for event in events if event["stage"] == "recognition.startup.activated"),
        None,
    )

    context = next(
        (event.get("context") for event in events if isinstance(event.get("context"), dict)),
        {},
    )
    cache_state = str(context.get("cache_state", "unknown"))
    cache_controlled = bool(context.get("cache_controlled", False))
    eligible = not errors and (cache_state != "cold" or cache_controlled)
    if cache_state == "cold" and not cache_controlled:
        errors.append("cold cache was not controlled; excluded from formal statistics")
    scenario = str(context.get("scenario", ""))
    if scenario == "recognition-quick-close" and any(
        event["stage"] == "recognition.worker.started" for event in events
    ):
        errors.append("quick-close started a recognition worker")
    if scenario in {
        "recognition-auto-models-present",
        "recognition-auto-missing-models",
        "recognition-auto-50k-pending",
    }:
        if recognition_activated is None:
            errors.append("recognition activation event is missing")
        snapshots = _resource_snapshots(events)
        missing_snapshots = [name for name, value in snapshots.items() if value is None]
        if missing_snapshots:
            errors.append(
                "recognition resource snapshots are missing: "
                + ", ".join(missing_snapshots)
            )

    metrics = {
        "process_start_app_created_ms": process_app_ms,
        "app_created_show_ms": _duration(milestones["app_created"], milestones["show"]),
        "show_first_paint_ms": _duration(milestones["show"], milestones["first_paint"]),
        "first_paint_interactive_ms": _duration(
            milestones["first_paint"], milestones["interactive"]
        ),
        "show_interactive_ms": _duration(milestones["show"], interactive),
        "interactive_library_ready_ms": _duration(interactive, milestones["library_ready"]),
        "library_ready_first_gallery_ms": _duration(
            milestones["library_ready"], milestones["first_gallery_visible"]
        ),
        "first_gallery_first_thumbnail_ms": _duration(
            milestones["first_gallery_visible"], milestones["first_usable_thumbnail"]
        ),
        "first_gallery_visible_ms": (
            round(float(milestones["first_gallery_visible"]["elapsed_ms"]), 3)
            if milestones["first_gallery_visible"] is not None
            else None
        ),
        "first_usable_thumbnail_ms": (
            round(float(milestones["first_usable_thumbnail"]["elapsed_ms"]), 3)
            if milestones["first_usable_thumbnail"] is not None
            else None
        ),
        "probe_ms": _duration(probe_started, probe_finished),
        "max_gui_job_ms": (
            round(max(all_gui_job_durations), 3) if all_gui_job_durations else 0.0
        ),
        "max_post_interactive_gui_stall_ms": (
            round(max(post_interactive_durations), 3) if post_interactive_durations else 0.0
        ),
        "interactive_recognition_activation_ms": _duration(
            interactive,
            recognition_activated,
        ),
    }
    terminal_details = _details(terminal) if terminal is not None else {}
    return {
        "path": str(path),
        "valid": not [error for error in errors if "excluded from" not in error],
        "eligible": eligible,
        "errors": errors,
        "terminal": terminal["stage"] if terminal is not None else None,
        "error_code": terminal_details.get("code"),
        "context": context,
        "metrics": metrics,
        "resource_snapshots": _resource_snapshots(events),
    }


def summarize_profiles(paths: Iterable[Path], *, require_gallery: bool = True) -> dict[str, Any]:
    runs = [analyse_run(Path(path), require_gallery=require_gallery) for path in paths]
    batch_signatures = {
        tuple(run.get("context", {}).get(key) for key in BATCH_CONTEXT_KEYS)
        for run in runs
    }
    if len(batch_signatures) > 1:
        for run in runs:
            run.setdefault("errors", []).append("mixed benchmark batch contexts")
            run["valid"] = False
            run["eligible"] = False
    eligible = [run for run in runs if run.get("eligible")]
    metric_summary: dict[str, dict[str, float | int | None]] = {}
    for metric in METRIC_ORDER:
        values = [
            float(run["metrics"][metric])
            for run in eligible
            if run.get("metrics", {}).get(metric) is not None
        ]
        metric_summary[metric] = {
            "count": len(values),
            "p50": nearest_rank(values, 50),
            "p95": nearest_rank(values, 95),
            "max": round(max(values), 3) if values else None,
        }
    terminal_counts = Counter(run.get("terminal") or "missing" for run in runs)
    error_codes = Counter(str(run["error_code"]) for run in runs if run.get("error_code"))
    context = (
        eligible[0].get("context", {}) if eligible else (runs[0].get("context", {}) if runs else {})
    )
    resource_summary: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for snapshot_name in RESOURCE_SNAPSHOT_NAMES:
        resource_summary[snapshot_name] = {}
        for field in ("cpu_ms", "rss_bytes", "read_bytes", "write_bytes"):
            values = [
                float(snapshot[field])
                for run in eligible
                if isinstance(
                    snapshot := run.get("resource_snapshots", {}).get(snapshot_name),
                    dict,
                )
                and snapshot.get(field) is not None
            ]
            resource_summary[snapshot_name][field] = {
                "count": len(values),
                "p50": nearest_rank(values, 50),
                "p95": nearest_rank(values, 95),
                "max": round(max(values), 3) if values else None,
            }
    return {
        "schema_version": 1,
        "context": context,
        "sample_count": len(runs),
        "valid_count": sum(bool(run.get("valid")) for run in runs),
        "eligible_count": len(eligible),
        "formal_evidence": (
            len(runs) >= 30
            and len(eligible) == len(runs)
            and context.get("runtime") == "packaged"
            and bool(context.get("build_environment_fingerprint"))
            and bool(context.get("artifact_sha256"))
            and str(context.get("manifest_source_revision", "")).startswith(
                str(context.get("revision", "missing"))
            )
        ),
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "error_codes": dict(sorted(error_codes.items())),
        "metrics": metric_summary,
        "resources": resource_summary,
        "runs": runs,
    }


def compare_summaries(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Apply the Phase 2/3 startup performance gates."""

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, actual: Any, limit: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "limit": limit})

    candidate_metrics = candidate.get("metrics", {})
    baseline_metrics = baseline.get("metrics", {})
    baseline_context = baseline.get("context", {})
    candidate_context = candidate.get("context", {})
    matching_context_keys = tuple(
        key
        for key in BATCH_CONTEXT_KEYS
        if key not in {"revision", "artifact_sha256", "manifest_source_revision"}
    )
    mismatched_context = {
        key: (baseline_context.get(key), candidate_context.get(key))
        for key in matching_context_keys
        if baseline_context.get(key) != candidate_context.get(key)
    }
    add(
        "baseline_revision_is_6ff592f7",
        str(baseline_context.get("revision", "")).startswith("6ff592f7"),
        baseline_context.get("revision"),
        "6ff592f7",
    )
    add(
        "candidate_revision_is_distinct",
        bool(candidate_context.get("revision"))
        and candidate_context.get("revision") not in {"unknown", "working-tree"}
        and candidate_context.get("revision") != baseline_context.get("revision"),
        candidate_context.get("revision"),
        "non-baseline commit SHA",
    )
    baseline_build_fingerprint = baseline_context.get("build_environment_fingerprint")
    candidate_build_fingerprint = candidate_context.get("build_environment_fingerprint")
    add(
        "build_environment_fingerprints_match",
        bool(baseline_build_fingerprint)
        and baseline_build_fingerprint == candidate_build_fingerprint,
        (baseline_build_fingerprint, candidate_build_fingerprint),
        "identical dependency/Nuitka/build/native/assets fingerprint",
    )
    baseline_artifact = baseline_context.get("artifact_sha256")
    candidate_artifact = candidate_context.get("artifact_sha256")
    add(
        "packaged_artifacts_are_distinct",
        bool(baseline_artifact)
        and bool(candidate_artifact)
        and baseline_artifact != candidate_artifact,
        (baseline_artifact, candidate_artifact),
        "two present and distinct executable hashes",
    )
    add(
        "manifest_revisions_match_context",
        str(baseline_context.get("manifest_source_revision", "")).startswith(
            str(baseline_context.get("revision", "missing"))
        )
        and str(candidate_context.get("manifest_source_revision", "")).startswith(
            str(candidate_context.get("revision", "missing"))
        ),
        (
            baseline_context.get("manifest_source_revision"),
            candidate_context.get("manifest_source_revision"),
        ),
        "manifest source revisions match benchmark revisions",
    )
    add(
        "baseline_candidate_contexts_match",
        not mismatched_context,
        mismatched_context or "matched",
        "same platform/backend/scenario/cache/build context",
    )
    add(
        "baseline_has_30_eligible_samples",
        baseline.get("eligible_count", 0) >= 30,
        baseline.get("eligible_count", 0),
        30,
    )
    add(
        "candidate_has_30_eligible_samples",
        candidate.get("eligible_count", 0) >= 30,
        candidate.get("eligible_count", 0),
        30,
    )
    add(
        "baseline_is_formal_packaged_evidence",
        bool(baseline.get("formal_evidence")),
        baseline.get("formal_evidence"),
        True,
    )
    add(
        "candidate_is_formal_packaged_evidence",
        bool(candidate.get("formal_evidence")),
        candidate.get("formal_evidence"),
        True,
    )
    show_max = candidate_metrics.get("show_interactive_ms", {}).get("max")
    add(
        "show_to_interactive_max_le_2000ms",
        show_max is not None and show_max <= 2000.0,
        show_max,
        2000.0,
    )
    stall_max = candidate_metrics.get("max_post_interactive_gui_stall_ms", {}).get("max")
    add(
        "post_interactive_gui_stall_max_le_100ms",
        stall_max is not None and stall_max <= 100.0,
        stall_max,
        100.0,
    )
    all_job_max = candidate_metrics.get("max_gui_job_ms", {}).get("max")
    add(
        "all_named_gui_jobs_max_le_100ms",
        all_job_max is not None and all_job_max <= 100.0,
        all_job_max,
        100.0,
    )

    for metric in ("first_gallery_visible_ms", "first_usable_thumbnail_ms"):
        baseline_stats = baseline_metrics.get(metric, {})
        candidate_stats = candidate_metrics.get(metric, {})
        baseline_p50 = baseline_stats.get("p50")
        candidate_p50 = candidate_stats.get("p50")
        p50_limit = round(float(baseline_p50) * 0.7, 3) if baseline_p50 is not None else None
        add(
            f"{metric}_p50_improves_30_percent",
            p50_limit is not None and candidate_p50 is not None and candidate_p50 <= p50_limit,
            candidate_p50,
            p50_limit,
        )
        baseline_p95 = baseline_stats.get("p95")
        candidate_p95 = candidate_stats.get("p95")
        add(
            f"{metric}_p95_not_regressed",
            baseline_p95 is not None
            and candidate_p95 is not None
            and candidate_p95 <= baseline_p95,
            candidate_p95,
            baseline_p95,
        )

    add(
        "all_candidate_samples_valid",
        candidate.get("valid_count") == candidate.get("sample_count")
        and candidate.get("sample_count", 0) > 0,
        candidate.get("valid_count"),
        candidate.get("sample_count"),
    )
    add(
        "all_candidate_samples_eligible",
        candidate.get("eligible_count") == candidate.get("sample_count")
        and candidate.get("sample_count", 0) > 0,
        candidate.get("eligible_count"),
        candidate.get("sample_count"),
    )
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _summary_markdown(summary: dict[str, Any]) -> str:
    context = summary.get("context", {})
    platform_label = (
        f"{context.get('platform', 'unknown')}/{context.get('architecture', 'unknown')}"
    )
    runtime_label = f"{context.get('runtime', 'unknown')} / {context.get('qt_backend', 'unknown')}"
    scenario_label = (
        f"{context.get('scenario', 'unknown')} / {context.get('cache_state', 'unknown')}"
    )
    sample_label = (
        f"{summary.get('sample_count', 0)} total, {summary.get('eligible_count', 0)} eligible"
    )
    lines = [
        "# Startup benchmark summary",
        "",
        f"- Revision: `{context.get('revision', 'unknown')}`",
        f"- Platform: `{platform_label}`",
        f"- Runtime/backend: `{runtime_label}`",
        f"- Scenario/cache: `{scenario_label}`",
        f"- Samples: {sample_label}",
        f"- Formal evidence: `{'yes' if summary.get('formal_evidence') else 'no'}`",
        "",
        "| Metric | Count | P50 (ms) | P95 (ms) | Max (ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in METRIC_ORDER:
        stats = summary.get("metrics", {}).get(metric, {})
        lines.append(
            f"| `{metric}` | {stats.get('count', 0)} | {stats.get('p50')} | "
            f"{stats.get('p95')} | {stats.get('max')} |"
        )
    lines.extend(
        (
            "",
            "## Recognition resource snapshots",
            "",
            "| Snapshot | Resource | Count | P50 | P95 | Max |",
            "|---|---|---:|---:|---:|---:|",
        )
    )
    for snapshot_name in RESOURCE_SNAPSHOT_NAMES:
        snapshot = summary.get("resources", {}).get(snapshot_name, {})
        for field in ("cpu_ms", "rss_bytes", "read_bytes", "write_bytes"):
            stats = snapshot.get(field, {})
            lines.append(
                f"| `{snapshot_name}` | `{field}` | {stats.get('count', 0)} | "
                f"{stats.get('p50')} | {stats.get('p95')} | {stats.get('max')} |"
            )
    pending = []
    platform_name = str(context.get("platform", ""))
    architecture = str(context.get("architecture", ""))
    if platform_name != "win32":
        pending.append("Windows packaged matrix")
    if not platform_name.startswith("linux"):
        pending.append("Linux AppImage XCB/Wayland matrix")
    if platform_name != "darwin" or architecture not in {"x86_64", "AMD64"}:
        pending.append("macOS Intel matrix")
    if context.get("runtime") != "packaged":
        pending.append("matching packaged A/B batch")
    if summary.get("eligible_count", 0) < 30:
        pending.append("30 eligible samples for this exact batch")
    if pending:
        lines.extend(("", "## Pending manual validation", ""))
        lines.extend(f"- `pending_manual_validation`: {item}" for item in pending)
    invalid = [run for run in summary.get("runs", []) if not run.get("eligible")]
    if invalid:
        lines.extend(("", "## Excluded runs", ""))
        lines.extend(f"- `{run['path']}`: {'; '.join(run.get('errors', []))}" for run in invalid)
    return "\n".join(lines) + "\n"


def _comparison_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Startup benchmark gate",
        "",
        f"Overall: **{'PASS' if comparison.get('passed') else 'FAIL'}**",
        "",
        "| Check | Result | Actual | Limit |",
        "|---|---|---:|---:|",
    ]
    for check in comparison.get("checks", []):
        result = "PASS" if check["passed"] else "FAIL"
        lines.append(f"| `{check['name']}` | {result} | {check['actual']} | {check['limit']} |")
    return "\n".join(lines) + "\n"


def _append_event(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _wait_with_resource_sampling(
    process: subprocess.Popen,
    *,
    profile_path: Path,
    launched_wall: float,
    timeout_seconds: float,
    interval_ms: int,
) -> tuple[int, bool]:
    try:
        import psutil
    except ImportError as exc:
        raise ProfileError("resource sampling requires psutil") from exc
    observed = psutil.Process(process.pid)
    deadline = time.monotonic() + float(timeout_seconds)
    timed_out = False
    while process.poll() is None:
        try:
            memory = observed.memory_info()
            cpu = observed.cpu_times()
            io_getter = getattr(observed, "io_counters", None)
            io = io_getter() if callable(io_getter) else None
            details = {
                "cpu_ms": round((float(cpu.user) + float(cpu.system)) * 1000.0, 3),
                "rss_bytes": int(memory.rss),
                "read_bytes": (
                    int(getattr(io, "read_bytes"))
                    if io is not None and hasattr(io, "read_bytes")
                    else None
                ),
                "write_bytes": (
                    int(getattr(io, "write_bytes"))
                    if io is not None and hasattr(io, "write_bytes")
                    else None
                ),
            }
            _append_event(
                profile_path,
                {
                    "stage": "launcher.resource_sample",
                    "elapsed_ms": round((time.time() - launched_wall) * 1000.0, 3),
                    "wall_time": time.time(),
                    "pid": process.pid,
                    "details": details,
                },
            )
        except (psutil.Error, OSError):
            pass
        if time.monotonic() >= deadline:
            timed_out = True
            process.terminate()
            break
        time.sleep(max(10, int(interval_ms)) / 1000.0)
    if timed_out:
        try:
            return process.wait(timeout=2.0), True
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(timeout=2.0), True
    return int(process.wait()), False


def collect(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ProfileError("collect requires a command after --")
    if args.samples < 1:
        raise ProfileError("samples must be positive")
    if not args.confirm_dedicated_library:
        raise ProfileError("refusing to benchmark without --confirm-dedicated-library")
    environment_overrides: dict[str, str] = {}
    for declaration in args.set_env:
        name, separator, value = str(declaration).partition("=")
        if not separator or not name or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ProfileError(f"invalid --set-env declaration: {declaration}")
        environment_overrides[name] = value
    build_identity: dict[str, Any] = {}
    if args.runtime == "packaged":
        if args.build_manifest is None:
            raise ProfileError("packaged collection requires --build-manifest")
        build_identity = _load_build_manifest(
            args.build_manifest.expanduser().resolve(),
            revision=args.revision,
            command=command,
            cwd=args.cwd,
        )
    benchmark_library = args.library.expanduser().resolve()
    if not benchmark_library.is_dir():
        raise ProfileError(f"benchmark library is not a directory: {benchmark_library}")
    controlled = args.cache_state != "cold" or bool(args.confirm_controlled_cold_cache)
    method = args.cache_eviction_method.strip() or "uncontrolled"
    if args.cache_state == "cold" and (not controlled or method == "uncontrolled"):
        controlled = False

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.samples + 1):
        run_id = (
            f"{args.revision}-{args.scenario}-{args.cache_state}-{index:03d}-{uuid.uuid4().hex[:8]}"
        )
        profile_path = output_dir / f"run-{index:03d}.jsonl"
        stdout_path = output_dir / f"run-{index:03d}.stdout.log"
        stderr_path = output_dir / f"run-{index:03d}.stderr.log"
        settings_path = output_dir / f"run-{index:03d}.settings.json"
        if profile_path.exists():
            raise ProfileError(f"refusing to overwrite {profile_path}")
        context = {
            "run_id": run_id,
            "revision": args.revision,
            "runtime": args.runtime,
            "platform": sys.platform,
            "architecture": os.uname().machine
            if hasattr(os, "uname")
            else os.environ.get("PROCESSOR_ARCHITECTURE", "unknown"),
            "qt_backend": args.qt_backend,
            "graphics_backend": args.graphics_backend,
            "cache_state": args.cache_state,
            "cache_controlled": controlled,
            "cache_eviction_method": method,
            "scenario": args.scenario,
            "scenario_env_names": ",".join(sorted(environment_overrides)),
            **build_identity,
        }
        launched_wall = time.time()
        _append_event(
            profile_path,
            {
                "stage": "launcher.process_started",
                "elapsed_ms": 0.0,
                "wall_time": launched_wall,
                "pid": os.getpid(),
                "context": context,
            },
        )
        environment = os.environ.copy()
        environment.update(
            {
                "IPHOTO_STARTUP_PROFILE": "1",
                "IPHOTO_STARTUP_PROFILE_PATH": str(profile_path),
                "IPHOTO_STARTUP_RUN_ID": run_id,
                "IPHOTO_STARTUP_REVISION": args.revision,
                "IPHOTO_STARTUP_RUNTIME": args.runtime,
                "IPHOTO_STARTUP_GRAPHICS_BACKEND": args.graphics_backend,
                "IPHOTO_STARTUP_CACHE_STATE": args.cache_state,
                "IPHOTO_STARTUP_CACHE_CONTROLLED": "1" if controlled else "0",
                "IPHOTO_STARTUP_CACHE_EVICTION_METHOD": method,
                "IPHOTO_STARTUP_SCENARIO": args.scenario,
                "IPHOTO_STARTUP_BUILD_ENVIRONMENT_FINGERPRINT": str(
                    build_identity.get("build_environment_fingerprint", "")
                ),
                "IPHOTO_STARTUP_ARTIFACT_SHA256": str(
                    build_identity.get("artifact_sha256", "")
                ),
                "IPHOTO_STARTUP_MANIFEST_REVISION": str(
                    build_identity.get("manifest_source_revision", "")
                ),
                "IPHOTO_STARTUP_BENCHMARK_AUTO_EXIT_MS": str(args.auto_exit_delay_ms),
                "IPHOTO_STARTUP_BENCHMARK": "1",
                "IPHOTO_SETTINGS_PATH": str(settings_path),
            }
        )
        environment.update(environment_overrides)
        settings_path.write_text(
            json.dumps({"basic_library_path": str(benchmark_library)}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if args.qt_backend != "default":
            environment["QT_QPA_PLATFORM"] = args.qt_backend
        if args.graphics_backend in {"auto", "metal", "opengl"}:
            environment["IPHOTO_RHI_BACKEND"] = args.graphics_backend
        timed_out = False
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(  # noqa: S603 - explicit operator command, no shell
                command,
                cwd=args.cwd,
                env=environment,
                stdout=stdout,
                stderr=stderr,
            )
            if args.sample_resources:
                return_code, timed_out = _wait_with_resource_sampling(
                    process,
                    profile_path=profile_path,
                    launched_wall=launched_wall,
                    timeout_seconds=args.timeout_seconds,
                    interval_ms=args.resource_sample_interval_ms,
                )
            else:
                try:
                    return_code = process.wait(timeout=args.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.terminate()
                    try:
                        return_code = process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        return_code = process.wait(timeout=2.0)
        try:
            last_elapsed_ms = max(float(event["elapsed_ms"]) for event in load_events(profile_path))
        except ProfileError:
            last_elapsed_ms = 0.0
        _append_event(
            profile_path,
            {
                "stage": "launcher.process_finished",
                "elapsed_ms": round(
                    max(last_elapsed_ms, (time.time() - launched_wall) * 1000.0), 3
                ),
                "wall_time": time.time(),
                "pid": process.pid,
                "context": context,
                "details": {"return_code": return_code, "timed_out": timed_out},
            },
        )

    profiles = sorted(output_dir.glob("run-*.jsonl"))
    summary = summarize_profiles(profiles, require_gallery=not args.allow_degraded)
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    return 0 if summary["eligible_count"] == args.samples else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    collect_parser = subparsers.add_parser("collect", help="launch and collect repeated runs")
    collect_parser.add_argument("--revision", required=True)
    collect_parser.add_argument("--scenario", required=True)
    collect_parser.add_argument("--library", type=Path, required=True)
    collect_parser.add_argument("--confirm-dedicated-library", action="store_true")
    collect_parser.add_argument("--runtime", choices=("source", "packaged"), required=True)
    collect_parser.add_argument("--build-manifest", type=Path)
    collect_parser.add_argument("--qt-backend", default="default")
    collect_parser.add_argument("--graphics-backend", default="default")
    collect_parser.add_argument("--cache-state", choices=("cold", "hot"), required=True)
    collect_parser.add_argument("--cache-eviction-method", default="uncontrolled")
    collect_parser.add_argument("--confirm-controlled-cold-cache", action="store_true")
    collect_parser.add_argument("--samples", type=int, default=30)
    collect_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    collect_parser.add_argument("--auto-exit-delay-ms", type=int, default=250)
    collect_parser.add_argument("--sample-resources", action="store_true")
    collect_parser.add_argument("--resource-sample-interval-ms", type=int, default=100)
    collect_parser.add_argument(
        "--set-env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="set a non-secret scenario environment variable in the child process",
    )
    collect_parser.add_argument("--allow-degraded", action="store_true")
    collect_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    collect_parser.add_argument("--output-dir", type=Path, required=True)
    collect_parser.add_argument("command", nargs=argparse.REMAINDER)

    summarize_parser = subparsers.add_parser("summarize", help="aggregate existing profiles")
    summarize_parser.add_argument("--allow-degraded", action="store_true")
    summarize_parser.add_argument("--output-dir", type=Path, required=True)
    summarize_parser.add_argument("profiles", nargs="+", type=Path)

    compare_parser = subparsers.add_parser("compare", help="compare baseline and candidate")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "collect":
            return collect(args)
        if args.action == "summarize":
            summary = summarize_profiles(args.profiles, require_gallery=not args.allow_degraded)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            _write_json(args.output_dir / "summary.json", summary)
            (args.output_dir / "summary.md").write_text(
                _summary_markdown(summary), encoding="utf-8"
            )
            return 0 if summary["eligible_count"] == summary["sample_count"] else 2
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        comparison = compare_summaries(baseline, candidate)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(args.output_dir / "comparison.json", comparison)
        (args.output_dir / "comparison.md").write_text(
            _comparison_markdown(comparison), encoding="utf-8"
        )
        return 0 if comparison["passed"] else 1
    except (OSError, ProfileError, ValueError) as exc:
        print(f"startup benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

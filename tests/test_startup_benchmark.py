from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.startup_benchmark import (
    analyse_run,
    compare_summaries,
    nearest_rank,
    summarize_profiles,
)
from tools.startup_benchmark import (
    main as benchmark_main,
)


def _write_profile(
    path: Path,
    *,
    scale: float = 1.0,
    controlled: bool = True,
    terminal: str = "startup.completed",
    include_terminal: bool = True,
    scenario: str = "local-ssd",
    return_code: int = 0,
    timed_out: bool = False,
) -> None:
    context = {
        "run_id": path.stem,
        "revision": "candidate",
        "runtime": "packaged",
        "platform": "darwin",
        "architecture": "arm64",
        "qt_backend": "cocoa",
        "graphics_backend": "metal",
        "cache_state": "cold",
        "cache_controlled": controlled,
        "cache_eviction_method": "purge" if controlled else "uncontrolled",
        "scenario": scenario,
    }
    events = [
        ("launcher.process_started", 0.0, {}),
        ("startup.app_created", 100.0 * scale, {"generation": 1}),
        ("startup.show", 200.0 * scale, {"generation": 1}),
        ("startup.interactive", 300.0 * scale, {"generation": 1}),
        ("startup.gui_job.started", 320.0 * scale, {"generation": 1, "job": "library.commit"}),
        (
            "startup.gui_job.finished",
            340.0 * scale,
            {"generation": 1, "job": "library.commit", "duration_ms": 20.0 * scale},
        ),
        ("startup.probe.started", 350.0 * scale, {"generation": 1}),
        ("startup.probe.finished", 400.0 * scale, {"generation": 1, "storage_kind": "local"}),
        ("startup.library_ready", 420.0 * scale, {"generation": 1}),
        ("startup.first_gallery_visible", 500.0 * scale, {}),
        ("startup.first_usable_thumbnail", 600.0 * scale, {}),
    ]
    if include_terminal:
        events.append((terminal, 620.0 * scale, {"generation": 1}))
    events.append(
        (
            "launcher.process_finished",
            640.0 * scale,
            {"return_code": return_code, "timed_out": timed_out},
        )
    )
    payloads = [
        {
            "stage": stage,
            "elapsed_ms": elapsed,
            "wall_time": 1000.0 + elapsed / 1000.0,
            "pid": 123,
            "context": context,
            "details": details,
        }
        for stage, elapsed, details in events
    ]
    path.write_text("".join(json.dumps(payload) + "\n" for payload in payloads), encoding="utf-8")


def test_nearest_rank_uses_29th_value_for_30_sample_p95() -> None:
    assert nearest_rank(list(range(1, 31)), 95) == 29.0


def test_analyse_and_summarize_valid_profiles(tmp_path) -> None:
    profiles = []
    for index in range(30):
        path = tmp_path / f"run-{index:03d}.jsonl"
        _write_profile(path, scale=1.0 + index / 100.0)
        profiles.append(path)

    run = analyse_run(profiles[0])
    summary = summarize_profiles(profiles)

    assert run["valid"] is True
    assert run["eligible"] is True
    assert run["metrics"]["show_interactive_ms"] == 100.0
    assert run["metrics"]["first_gallery_first_thumbnail_ms"] == 100.0
    assert summary["sample_count"] == 30
    assert summary["eligible_count"] == 30
    assert summary["metrics"]["first_usable_thumbnail_ms"]["p95"] == 768.0


def test_missing_terminal_and_path_leak_are_rejected(tmp_path) -> None:
    path = tmp_path / "broken.jsonl"
    _write_profile(path, include_terminal=False)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": "startup.extra",
                    "elapsed_ms": 700.0,
                    "wall_time": 1000.7,
                    "pid": 123,
                    "details": {"reason": "/Users/example/Photos/private.jpg"},
                }
            )
            + "\n"
        )

    run = analyse_run(path)

    assert run["valid"] is False
    assert any("terminal" in error for error in run["errors"])
    assert any("sensitive path" in error for error in run["errors"])


def test_uncontrolled_cold_run_is_valid_but_not_eligible(tmp_path) -> None:
    path = tmp_path / "cold.jsonl"
    _write_profile(path, controlled=False)

    run = analyse_run(path)

    assert run["valid"] is True
    assert run["eligible"] is False


def test_nonzero_or_timed_out_process_is_rejected(tmp_path) -> None:
    crashed = tmp_path / "crashed.jsonl"
    timed_out = tmp_path / "timed-out.jsonl"
    _write_profile(crashed, return_code=3)
    _write_profile(timed_out, timed_out=True)

    crashed_run = analyse_run(crashed)
    timed_out_run = analyse_run(timed_out)

    assert crashed_run["eligible"] is False
    assert any("exited with code 3" in error for error in crashed_run["errors"])
    assert timed_out_run["eligible"] is False
    assert any("timed out" in error for error in timed_out_run["errors"])


def test_mixed_batch_contexts_are_rejected(tmp_path) -> None:
    local = tmp_path / "local.jsonl"
    network = tmp_path / "network.jsonl"
    _write_profile(local, scenario="local-ssd")
    _write_profile(network, scenario="network-share")

    summary = summarize_profiles([local, network])

    assert summary["eligible_count"] == 0
    assert summary["formal_evidence"] is False
    assert all(
        "mixed benchmark batch contexts" in run["errors"] for run in summary["runs"]
    )


def test_comparison_enforces_improvement_tail_and_stall_gates(tmp_path) -> None:
    baseline_paths = []
    candidate_paths = []
    for index in range(30):
        baseline_path = tmp_path / f"baseline-{index:03d}.jsonl"
        candidate_path = tmp_path / f"candidate-{index:03d}.jsonl"
        _write_profile(baseline_path, scale=1.0)
        _write_profile(candidate_path, scale=0.6)
        baseline_paths.append(baseline_path)
        candidate_paths.append(candidate_path)
    baseline = summarize_profiles(baseline_paths)
    candidate = summarize_profiles(candidate_paths)

    result = compare_summaries(baseline, candidate)

    assert result["passed"] is True
    assert all(check["passed"] for check in result["checks"])


def test_collect_isolates_profile_and_aggregates_subprocess(tmp_path) -> None:
    child = tmp_path / "fake_app.py"
    child.write_text(
        """
import json
import os
import time
from pathlib import Path

assert os.environ["IPHOTO_STARTUP_BENCHMARK_AUTO_EXIT_MS"] == "25"
path = Path(os.environ["IPHOTO_STARTUP_PROFILE_PATH"])
context = {
    "run_id": os.environ["IPHOTO_STARTUP_RUN_ID"],
    "revision": os.environ["IPHOTO_STARTUP_REVISION"],
    "runtime": "packaged",
    "platform": "darwin",
    "architecture": "arm64",
    "qt_backend": "cocoa",
    "graphics_backend": "metal",
    "cache_state": "hot",
    "cache_controlled": True,
    "cache_eviction_method": "uncontrolled",
    "scenario": "local-ssd",
}
stages = [
    ("startup.app_created", 10),
    ("startup.show", 20),
    ("startup.interactive", 30),
    ("startup.library_ready", 40),
    ("startup.first_gallery_visible", 50),
    ("startup.first_usable_thumbnail", 60),
    ("startup.completed", 70),
]
with path.open("a", encoding="utf-8") as stream:
    for stage, elapsed in stages:
        stream.write(json.dumps({
            "stage": stage,
            "elapsed_ms": elapsed,
            "wall_time": time.time() + elapsed / 1000,
            "pid": os.getpid(),
            "context": context,
            "details": {"generation": 1},
        }) + "\\n")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"

    return_code = benchmark_main(
        [
            "collect",
            "--revision",
            "candidate",
            "--scenario",
            "local-ssd",
            "--runtime",
            "packaged",
            "--qt-backend",
            "cocoa",
            "--graphics-backend",
            "metal",
            "--cache-state",
            "hot",
            "--samples",
            "1",
            "--auto-exit-delay-ms",
            "25",
            "--output-dir",
            str(output),
            "--",
            sys.executable,
            str(child),
        ]
    )

    assert return_code == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["eligible_count"] == 1
    assert len(list(output.glob("run-*.jsonl"))) == 1

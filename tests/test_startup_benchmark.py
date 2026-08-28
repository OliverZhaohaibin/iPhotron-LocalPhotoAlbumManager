from __future__ import annotations

import hashlib
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
    revision: str = "candidate",
    artifact_sha256: str = "a" * 64,
    build_environment_fingerprint: str = "f" * 64,
    scenario_env_names: str = "",
) -> None:
    context = {
        "run_id": path.stem,
        "revision": revision,
        "runtime": "packaged",
        "platform": "darwin",
        "architecture": "arm64",
        "qt_backend": "cocoa",
        "graphics_backend": "metal",
        "cache_state": "cold",
        "cache_controlled": controlled,
        "cache_eviction_method": "purge" if controlled else "uncontrolled",
        "scenario": scenario,
        "scenario_env_names": scenario_env_names,
        "build_environment_fingerprint": build_environment_fingerprint,
        "artifact_sha256": artifact_sha256,
        "manifest_source_revision": revision,
    }
    events = [
        ("launcher.process_started", 0.0, {}),
        ("startup.app_created", 100.0 * scale, {"generation": 1}),
        ("startup.show", 200.0 * scale, {"generation": 1}),
        ("main_window.first_paint", 250.0 * scale, {"generation": 1}),
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


def test_recognition_resource_snapshots_are_correlated_to_activation(tmp_path) -> None:
    path = tmp_path / "resources.jsonl"
    _write_profile(path)
    payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    payloads = [item for item in payloads if item["stage"] != "launcher.process_finished"]
    payloads.extend(
        [
            {
                "stage": "recognition.startup.activated",
                "elapsed_ms": 700.0,
                "wall_time": 1000.7,
                "pid": 123,
                "details": {"generation": 1},
            },
            *[
                {
                    "stage": "launcher.resource_sample",
                    "elapsed_ms": elapsed,
                    "wall_time": 1000.0 + elapsed / 1000.0,
                    "pid": 123,
                    "details": {
                        "cpu_ms": elapsed / 10.0,
                        "rss_bytes": int(elapsed * 1000),
                        "read_bytes": int(elapsed * 10),
                        "write_bytes": int(elapsed * 5),
                    },
                }
                for elapsed in (300.0, 700.0, 2200.0, 5700.0)
            ],
            {
                "stage": "launcher.process_finished",
                "elapsed_ms": 6000.0,
                "wall_time": 1006.0,
                "pid": 123,
                "details": {"return_code": 0, "timed_out": False},
            },
        ]
    )
    payloads.sort(key=lambda item: float(item["elapsed_ms"]))
    path.write_text("".join(json.dumps(item) + "\n" for item in payloads), encoding="utf-8")

    run = analyse_run(path)
    summary = summarize_profiles([path])

    assert run["metrics"]["interactive_recognition_activation_ms"] == 400.0
    assert run["metrics"]["max_post_recognition_gui_stall_ms"] == 0.0
    assert run["resource_snapshots"]["interactive"]["rss_bytes"] == 300000
    assert run["resource_snapshots"]["recognition_activation"]["rss_bytes"] == 700000
    assert run["resource_snapshots"]["recognition_plus_1500ms"]["rss_bytes"] == 2200000
    assert run["resource_snapshots"]["recognition_plus_5000ms"]["rss_bytes"] == 5700000
    assert summary["resources"]["recognition_plus_5000ms"]["cpu_ms"]["p95"] == 570.0


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


def test_quick_close_rejects_recognition_worker_start(tmp_path) -> None:
    path = tmp_path / "quick-close.jsonl"
    _write_profile(path, scenario="recognition-quick-close")
    payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    payloads.append(
        {
            "stage": "recognition.worker.started",
            "elapsed_ms": 610.0,
            "wall_time": 1000.61,
            "pid": 123,
            "details": {"generation": 1, "worker": "face"},
        }
    )
    payloads.sort(key=lambda item: float(item["elapsed_ms"]))
    path.write_text("".join(json.dumps(item) + "\n" for item in payloads), encoding="utf-8")

    run = analyse_run(path)

    assert run["valid"] is False
    assert "quick-close started a recognition worker" in run["errors"]


def test_feature_scoped_ab_arm_does_not_require_auto_activation(tmp_path) -> None:
    path = tmp_path / "feature-scoped.jsonl"
    _write_profile(
        path,
        scenario="recognition-auto-models-present",
        scenario_env_names="IPHOTO_STARTUP_RECOGNITION_AUTO_START",
    )

    run = analyse_run(path)

    assert run["valid"] is True


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
        _write_profile(
            baseline_path,
            scale=1.0,
            revision="6ff592f7",
            artifact_sha256="a" * 64,
        )
        _write_profile(
            candidate_path,
            scale=0.6,
            revision="306326ab",
            artifact_sha256="b" * 64,
        )
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
assert os.environ["IPHOTO_TEST_SCENARIO"] == "enabled"
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
    ("main_window.first_paint", 25),
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
    library = tmp_path / "benchmark-library"
    library.mkdir()
    executable = Path(sys.executable).resolve()
    artifact_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    build_manifest = tmp_path / "build-manifest.json"
    build_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_revision": "candidate",
                "artifact_sha256": artifact_sha256,
                "environment_fingerprint": "f" * 64,
            }
        ),
        encoding="utf-8",
    )

    return_code = benchmark_main(
        [
            "collect",
            "--revision",
            "candidate",
            "--scenario",
            "local-ssd",
            "--library",
            str(library),
            "--confirm-dedicated-library",
            "--runtime",
            "packaged",
            "--build-manifest",
            str(build_manifest),
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
            "--set-env",
            "IPHOTO_TEST_SCENARIO=enabled",
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


def test_packaged_collect_requires_matching_build_manifest(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()

    result = benchmark_main(
        [
            "collect",
            "--revision",
            "candidate",
            "--scenario",
            "local-ssd",
            "--library",
            str(library),
            "--confirm-dedicated-library",
            "--runtime",
            "packaged",
            "--cache-state",
            "hot",
            "--samples",
            "1",
            "--output-dir",
            str(tmp_path / "output"),
            "--",
            sys.executable,
        ]
    )

    assert result == 2


def test_template_restore_is_confined_to_output_active_library(tmp_path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    outside = tmp_path / "outside"
    output = tmp_path / "output"

    result = benchmark_main(
        [
            "collect",
            "--revision",
            "candidate",
            "--scenario",
            "recognition-auto-models-present",
            "--library",
            str(outside),
            "--library-template",
            str(template),
            "--confirm-dedicated-library",
            "--confirm-template-restore",
            "--runtime",
            "source",
            "--cache-state",
            "hot",
            "--samples",
            "1",
            "--output-dir",
            str(output),
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )

    assert result == 2
    assert not outside.exists()


def test_comparison_rejects_environment_mismatch(tmp_path) -> None:
    baseline_path = tmp_path / "baseline.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    _write_profile(
        baseline_path,
        revision="6ff592f7",
        artifact_sha256="a" * 64,
        build_environment_fingerprint="1" * 64,
    )
    _write_profile(
        candidate_path,
        scale=0.6,
        revision="306326ab",
        artifact_sha256="b" * 64,
        build_environment_fingerprint="2" * 64,
    )

    result = compare_summaries(
        summarize_profiles([baseline_path] * 30),
        summarize_profiles([candidate_path] * 30),
    )

    assert result["passed"] is False
    assert any(
        check["name"] == "build_environment_fingerprints_match"
        and not check["passed"]
        for check in result["checks"]
    )

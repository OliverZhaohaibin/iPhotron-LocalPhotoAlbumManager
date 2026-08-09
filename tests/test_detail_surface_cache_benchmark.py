from __future__ import annotations

from pathlib import Path

from tools.detail_surface_cache_benchmark import benchmark, compare


def test_surface_cache_benchmark_records_candidate_contract(tmp_path: Path) -> None:
    result = benchmark(
        sizes_mib=(1,),
        warm_samples=2,
        repo_root=tmp_path,
        label="test",
    )

    row = result["results"][0]
    assert row["namespace"] == "v3"
    assert row["payload_bytes"] == 1024 * 1024
    assert row["checksum_calls"] == 0
    assert row["python_peak_bytes"] <= 8 * 1024 * 1024 + 4096


def test_surface_cache_comparison_applies_memory_and_latency_gates() -> None:
    baseline = {
        "commit_sha": "baseline",
        "results": [{"size_mib": 16, "warm_hit_p95_ms": 20.0}],
    }
    candidate = {
        "commit_sha": "candidate",
        "results": [
            {
                "size_mib": 16,
                "payload_bytes": 16 * 1024 * 1024,
                "checksum_calls": 0,
                "python_peak_bytes": 1024,
                "rss_write_delta_bytes": 1024,
                "warm_hit_p95_ms": 21.0,
            }
        ],
    }

    result = compare(baseline, candidate)

    assert result["passed"] is True
    assert len(result["checks"]) == 4

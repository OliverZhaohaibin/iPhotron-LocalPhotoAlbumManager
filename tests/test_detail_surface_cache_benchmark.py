from __future__ import annotations

from pathlib import Path

from tools.detail_surface_cache_benchmark import (
    _fresh_probe_orders,
    _replace_fresh_process_samples,
    benchmark,
    compare,
)


def _fresh_probe(sample: int) -> dict[str, object]:
    return {
        "load_ms": float(sample),
        "consume_ms": 1.0,
        "load_to_consumed_ms": float(sample + 1),
        "store_initialization_ms": 2.0,
        "page_faults": 3,
        "rss_delta_bytes": 4,
        "checksum_calls": 0,
        "consumption_digest": "digest",
    }


def test_surface_cache_benchmark_records_candidate_contract(tmp_path: Path) -> None:
    result = benchmark(
        sizes_mib=(1,),
        warm_samples=2,
        consume_samples=2,
        repo_root=tmp_path,
        label="test",
    )

    row = result["results"][0]
    assert result["schema"] == 2
    assert result["warm_samples"] == 40
    assert result["consume_samples"] == 40
    assert row["namespace"] == "v3"
    assert row["payload_bytes"] == 1024 * 1024
    assert row["checksum_calls"] == 0
    assert row["fresh_process_checksum_calls"] == 0
    assert row["fresh_process_load_to_consumed_p95_ms"] > 0
    assert row["fresh_process_store_initialization_p95_ms"] > 0
    assert row["warm_load_to_consumed_p95_ms"] > 0
    assert row["consumption_digest"]
    assert len(row["fresh_process_samples"]) == 40
    assert len(row["warm_process_samples"]) == 40
    assert row["python_peak_bytes"] <= 8 * 1024 * 1024 + 4096


def test_surface_cache_fresh_probes_alternate_pair_order() -> None:
    assert _fresh_probe_orders(4) == [
        ("baseline", "candidate"),
        ("candidate", "baseline"),
        ("baseline", "candidate"),
        ("candidate", "baseline"),
    ]


def test_surface_cache_replaces_deferred_fresh_metrics() -> None:
    payload = {
        "consume_samples": 1,
        "results": [
            {
                "size_mib": 16,
                "consumption_digest": "digest",
                "fresh_process_samples": [_fresh_probe(99)],
            }
        ],
    }
    probes = [_fresh_probe(sample) for sample in range(1, 41)]

    _replace_fresh_process_samples(payload, size_mib=16, probes=probes)

    row = payload["results"][0]
    assert payload["consume_samples"] == 40
    assert row["fresh_process_load_p50_ms"] == 20.5
    assert row["fresh_process_load_p95_ms"] == 38.0
    assert row["fresh_process_load_to_consumed_p95_ms"] == 39.0
    assert row["fresh_process_checksum_calls"] == 0
    assert row["fresh_process_samples"] == probes


def test_surface_cache_comparison_applies_memory_and_latency_gates() -> None:
    baseline = {
        "commit_sha": "baseline",
        "results": [
            {
                "size_mib": 16,
                "warm_hit_p95_ms": 20.0,
                "fresh_process_load_to_consumed_p95_ms": 30.0,
                "warm_load_to_consumed_p95_ms": 25.0,
            }
        ],
    }
    candidate = {
        "commit_sha": "candidate",
        "results": [
            {
                "size_mib": 16,
                "payload_bytes": 16 * 1024 * 1024,
                "checksum_calls": 0,
                "fresh_process_checksum_calls": 0,
                "python_peak_bytes": 1024,
                "rss_write_delta_bytes": 1024,
                "warm_hit_p95_ms": 21.0,
                "fresh_process_load_to_consumed_p95_ms": 31.0,
                "warm_load_to_consumed_p95_ms": 26.0,
            }
        ],
    }

    result = compare(baseline, candidate)

    assert result["passed"] is True
    assert result["schema"] == 2
    assert len(result["checks"]) == 6


def test_surface_cache_comparison_rejects_consumed_latency_regression() -> None:
    baseline = {
        "results": [
            {
                "size_mib": 16,
                "warm_hit_p95_ms": 1.0,
                "fresh_process_load_to_consumed_p95_ms": 10.0,
                "warm_load_to_consumed_p95_ms": 10.0,
            }
        ]
    }
    candidate = {
        "results": [
            {
                "size_mib": 16,
                "payload_bytes": 16 * 1024 * 1024,
                "checksum_calls": 0,
                "fresh_process_checksum_calls": 0,
                "python_peak_bytes": 1024,
                "rss_write_delta_bytes": 1024,
                "warm_hit_p95_ms": 1.0,
                "fresh_process_load_to_consumed_p95_ms": 21.0,
                "warm_load_to_consumed_p95_ms": 10.0,
            }
        ]
    }

    result = compare(baseline, candidate)

    assert result["passed"] is False
    failed = [check["name"] for check in result["checks"] if not check["passed"]]
    assert failed == ["16MiB.fresh_process_load_to_consumed_p95_ms"]

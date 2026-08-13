from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = REPOSITORY_ROOT / "tools" / "collect_windows_scan_playback_diagnostics.ps1"
DOCUMENTATION = REPOSITORY_ROOT / "docs" / "WINDOWS_SCAN_PLAYBACK_DIAGNOSTICS.md"


def test_windows_collector_enables_required_runtime_probes() -> None:
    script = COLLECTOR.read_text(encoding="utf-8")

    for variable in (
        "IPHOTO_DETAIL_PROFILE",
        "IPHOTO_PERF_LOG",
        "IPHOTO_PERF_PRIVACY_SAFE",
        "IPHOTO_RUNTIME_DIAG",
        "IPHOTO_RUNTIME_DIAG_STACK_PATH",
        "PYTHONPATH",
        "QT_LOGGING_RULES",
    ):
        assert variable in script
    assert 'Marker "problem_reproduced"' in script
    assert "Resolve-SourceApplicationProcess" in script
    assert "process_metrics.csv" in script
    assert "Compress-Archive" in script
    assert "$replacementValues.ToArray()" in script


def test_windows_collector_does_not_copy_user_media_or_index() -> None:
    script = COLLECTOR.read_text(encoding="utf-8").lower()

    assert "copy-item" not in script
    assert "global_index.db" not in script
    assert "thumbnail" not in script


def test_windows_collector_documentation_describes_reproduction_marker() -> None:
    documentation = DOCUMENTATION.read_text(encoding="utf-8")

    assert "press `R` once" in documentation
    assert "single ZIP" in documentation
    assert "does not copy photos" in documentation

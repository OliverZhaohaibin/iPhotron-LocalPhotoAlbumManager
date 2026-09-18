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


def test_d3d11_ab_protocol_excludes_unsupported_gpu_maps() -> None:
    documentation = DOCUMENTATION.read_text(encoding="utf-8")

    assert "Only the supported OpenGL baseline should" in documentation
    assert "exercise opening/closing Maps" in documentation
    assert "D3D11 run is Detail-only" in documentation
    assert "must not enter Location or create a GPU map widget" in documentation


def test_fullscreen_scenario_pins_opengl_and_documents_pixel_probe() -> None:
    script = COLLECTOR.read_text(encoding="utf-8")
    assert '[ValidateSet("ScanPlayback", "Fullscreen")]' in script
    assert '$diagnosticEnvironment["IPHOTO_RHI_BACKEND"] = "opengl"' in script
    assert '$diagnosticEnvironment["IPHOTO_FULLSCREEN_DIAG"] = "1"' in script
    documentation = DOCUMENTATION.read_text(encoding="utf-8")
    assert "-Scenario Fullscreen" in documentation
    assert "windows_fullscreen_probe.py --cycles 20" in documentation
    assert "does **not** replace" in documentation


def test_source_process_resolution_uses_runtime_identity_and_launcher_ancestry() -> None:
    script = COLLECTOR.read_text(encoding="utf-8")
    resolver = script.split("function Resolve-SourceApplicationProcess", 1)[1].split(
        "function Write-SystemSnapshot", 1
    )[0]
    assert '"runtime_diagnostics_started"' in resolver
    assert "$descendantIds.Contains([int]$header.pid)" in resolver
    assert "$candidate.MainWindowHandle -ne [IntPtr]::Zero" in resolver
    assert 'throw "Could not identify the GUI process' in resolver
    assert "-StackPath $stackPath" in script

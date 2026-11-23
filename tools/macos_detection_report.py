#!/usr/bin/env python3
"""Generate a conservative macOS startup/package detection report."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from tools.startup_benchmark import compare_summaries
except ModuleNotFoundError:  # Direct execution: Python puts tools/ on sys.path.
    from startup_benchmark import compare_summaries


def _command_output(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - command is assembled from fixed local probes.
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _check(name: str, status: str, details: str, *, required: bool = True) -> dict[str, Any]:
    return {"name": name, "status": status, "details": details, "required": required}


def _bundle_checks(bundle: Path | None) -> list[dict[str, Any]]:
    if bundle is None:
        return [_check("app_bundle", "not_checked", "未提供 .app 路径", required=False)]
    bundle = bundle.expanduser().resolve()
    if bundle.suffix != ".app" or not bundle.is_dir():
        return [_check("app_bundle", "fail", "路径不是存在的 macOS .app 包")]
    contents = bundle / "Contents"
    plist_path = contents / "Info.plist"
    checks = [_check("app_bundle", "pass", "发现 .app 包")]
    if not plist_path.is_file():
        return [*checks, _check("info_plist", "fail", "缺少 Contents/Info.plist")]
    try:
        info = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return [*checks, _check("info_plist", "fail", "Info.plist 无法解析")]
    executable_name = str(info.get("CFBundleExecutable", ""))
    executable = contents / "MacOS" / executable_name if executable_name else None
    checks.append(
        _check("info_plist", "pass", f"CFBundleExecutable={executable_name or 'missing'}")
    )
    executable_ok = bool(executable and executable.is_file() and os.access(executable, os.X_OK))
    checks.append(
        _check(
            "bundle_executable",
            "pass" if executable_ok else "fail",
            "主程序存在且可执行" if executable_ok else "主程序缺失或不可执行",
        )
    )

    def exists_in_bundle(relative: str) -> bool:
        return any(
            (root / relative).exists()
            for root in (contents, contents / "Resources", contents / "MacOS")
        )

    qsb_found = any(contents.rglob("*.qsb"))
    checks.extend(
        [
            _check(
                "map_style",
                "pass" if exists_in_bundle("maps/style.json") else "warn",
                "maps/style.json",
            ),
            _check(
                "map_extension_resource",
                (
                    "pass"
                    if exists_in_bundle("maps/extension.tar")
                    or exists_in_bundle("maps/tiles")
                    else "warn"
                ),
                "maps/extension.tar or legacy maps/tiles",
            ),
            _check(
                "qt_metal_shaders",
                "pass" if qsb_found else "warn",
                "发现 Qt QSB shader" if qsb_found else "未发现 .qsb shader",
            ),
        ]
    )
    return checks


def _summary_checks(paths: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for path in paths:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(_check("benchmark_summary", "fail", f"summary 无法读取: {exc}"))
            continue
        summaries.append(summary)
        context = summary.get("context", {})
        formal = bool(summary.get("formal_evidence"))
        eligible = summary.get("eligible_count", 0)
        runtime = context.get("runtime", "unknown")
        sample_count = summary.get("sample_count", 0)
        details = f"{runtime} / {eligible}/{sample_count} eligible"
        checks.append(_check(f"benchmark:{path.name}", "pass" if formal else "warn", details))
    aggregate: dict[str, Any] = {"summaries": [path.name for path in paths]}
    if len(summaries) == 2:
        aggregate["comparison"] = compare_summaries(summaries[0], summaries[1])
        checks.append(
            _check(
                "startup_ab_gate",
                "pass" if aggregate["comparison"]["passed"] else "fail",
                "baseline/candidate gate",
            )
        )
    return checks, aggregate


def _graphics_backend_checks(paths: list[Path]) -> list[dict[str, Any]]:
    observed: set[str] = set()
    for path in paths:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        context = summary.get("context", {})
        if context.get("runtime") != "packaged" or summary.get("eligible_count", 0) < 1:
            continue
        observed.add(str(context.get("graphics_backend", "")).lower())
    return [
        _check(
            "metal_path",
            "pass" if "metal" in observed else "not_checked",
            "eligible packaged Metal batch" if "metal" in observed else "需运行 packaged Metal 批次",
        ),
        _check(
            "opengl_compatibility_path",
            "pass" if "opengl" in observed else "not_checked",
            (
                "eligible packaged OpenGL batch"
                if "opengl" in observed
                else "需运行 packaged OpenGL 批次"
            ),
            required=False,
        ),
    ]


def build_report(
    *,
    bundle: Path | None = None,
    summaries: list[Path] | None = None,
    baseline: Path | None = None,
    candidate: Path | None = None,
) -> dict[str, Any]:
    system_version = (
        _command_output(["sw_vers", "-productVersion"]) or platform.mac_ver()[0] or "unknown"
    )
    machine = platform.machine() or "unknown"
    checks = [
        _check("host_platform", "pass" if sys.platform == "darwin" else "fail", sys.platform),
        _check(
            "host_architecture",
            "pass" if machine in {"arm64", "x86_64", "AMD64"} else "warn",
            machine,
        ),
        _check("macos_version", "pass" if system_version != "unknown" else "warn", system_version),
    ]
    checks.extend(_bundle_checks(bundle))
    summary_paths = list(summaries or [])
    if baseline:
        summary_paths.insert(0, baseline)
    if candidate:
        summary_paths.append(candidate)
    checks.extend(_graphics_backend_checks(summary_paths))
    benchmark_checks, benchmark = _summary_checks(summary_paths)
    checks.extend(benchmark_checks)
    pending = ["manual offline-storage/map-degradation checks"]
    if machine == "arm64":
        pending[0:0] = [
            "macOS Intel matrix",
            "Windows packaged matrix",
            "Linux AppImage XCB/Wayland matrix",
        ]
    elif sys.platform == "darwin":
        pending[0:0] = ["Windows packaged matrix", "Linux AppImage XCB/Wayland matrix"]
    failed = [item["name"] for item in checks if item["status"] == "fail"]
    return {
        "schema_version": 1,
        "report_type": "macos_startup_detection",
        "host": {
            "platform": sys.platform,
            "architecture": machine,
            "macos_version": system_version,
        },
        "checks": checks,
        "pending_manual_validation": pending,
        "benchmark": benchmark,
        "overall": (
            "fail"
            if failed
            else "pending_manual_validation"
            if pending
            else "pass"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    host = report["host"]
    lines = [
        "# macOS startup detection report",
        "",
        f"Overall: **{report['overall'].upper()}**",
        "",
        f"- Host: `{host['platform']}/{host['architecture']}` (`macOS {host['macos_version']}`)",
        "",
        "## Checks",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{item['name']}` | {item['status']} | {item['details']} |" for item in report["checks"]
    )
    lines.extend(["", "## Pending manual validation", ""])
    lines.extend(
        f"- `pending_manual_validation`: {item}"
        for item in report["pending_manual_validation"]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, help="待检查的 macOS .app 包")
    parser.add_argument("--summary", action="append", type=Path, default=[])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        bundle=args.app, summaries=args.summary, baseline=args.baseline, candidate=args.candidate
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "macos-detection-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "macos-detection-report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    return 1 if report["overall"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())

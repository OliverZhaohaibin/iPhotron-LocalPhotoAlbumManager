#!/usr/bin/env python3
"""Run PR #931 Windows checks and package evidence; never update GitHub automatically."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
KIND = "iphoto_windows_fullscreen_validation"
MANUAL_CASES = (
    (
        "ordinary_launch",
        "Ordinary terminal launch (without collector): fullscreen + wheel are stable.",
    ),
    ("pycharm_launch", "PyCharm Run, same interpreter/checkout: fullscreen + wheel are stable."),
    (
        "packaged_launch",
        "Packaged executable: fullscreen + wheel are stable (skip if unavailable).",
    ),
    (
        "crop_straighten_20",
        "Asymmetric crop + straighten: 20 fullscreen round trips, centered with no drift.",
    ),
    (
        "window_lifecycle",
        "Minimize/restore, Alt-Tab, taskbar coverage, double-click and Esc exit work.",
    ),
    (
        "edit_maximize",
        "Edit fullscreen -> OS maximize restores tools; reenter/exit preserves maximize state.",
    ),
    (
        "video_live_photo",
        "Ordinary video and Live Photo playback/switching work; record any crash or hang.",
    ),
    ("dpi_100", "Fullscreen + wheel + crop/straighten tested at Windows 100% scale."),
    ("dpi_150", "Fullscreen + wheel + crop/straighten tested at Windows 150% scale."),
    ("dpi_250", "Fullscreen + wheel + crop/straighten tested at Windows 250% scale."),
    (
        "multi_monitor",
        "Move across different-DPI screens, including exit/reentry; no offset or flicker.",
    ),
)
CONTRACT_TESTS = (
    "tests/gui/test_windowed_fullscreen.py",
    "tests/ui/controllers/test_edit_fullscreen_manager.py",
    "tests/ui/test_window_manager_fullscreen.py",
    "tests/test_windows_desktop_capture.py",
)
GRAPHICS_ENV = (
    "QT_QPA_PLATFORM",
    "QT_SCALE_FACTOR",
    "QT_SCREEN_SCALE_FACTORS",
    "QT_AUTO_SCREEN_SCALE_FACTOR",
    "IPHOTO_RHI_BACKEND",
    "IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN",
    "IPHOTO_WINDOWS_FULLSCREEN_BORDER",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def file_hash(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(*arguments):
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def preflight():
    status = git_output("status", "--porcelain")
    result = {
        "time": utc_now(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "commit": git_output("rev-parse", "HEAD"),
        "branch": git_output("branch", "--show-current"),
        "working_tree_dirty": bool(status) if status is not None else None,
        "inherited_graphics_environment": {key: os.environ.get(key) for key in GRAPHICS_ENV},
        "tool_sha256": {
            name: file_hash(ROOT / "tools" / name)
            for name in (
                "validate_windows_fullscreen.py",
                "windows_fullscreen_probe.py",
                "collect_windows_scan_playback_diagnostics.ps1",
            )
        },
        "pytest_available": importlib.util.find_spec("pytest") is not None,
    }
    probe_tree = ast.parse((ROOT / "tools/windows_fullscreen_probe.py").read_text(encoding="utf-8"))
    probe_functions = {node.name for node in probe_tree.body if isinstance(node, ast.FunctionDef)}
    result["desktop_capture_probe_available"] = {
        "capture_visible_regions",
        "evaluate_capture",
    } <= probe_functions
    try:
        from PySide6 import __version__
        from PySide6.QtCore import qVersion

        result.update(pyside=__version__, qt=qVersion(), qt_available=True)
    except ImportError as error:
        result.update(qt_available=False, qt_import_error=str(error))
    return result


def child_environment(session: Path):
    env = os.environ.copy()
    # Isolate only child processes. Do not persist changes into the user's IDE
    # or terminal. Preserve display-scale settings and record them in preflight.
    for key in (
        "IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN",
        "IPHOTO_WINDOWS_FULLSCREEN_BORDER",
        "IPHOTO_DETAIL_PROFILE_PATH",
        "IPHOTO_LOG_DIR",
        "IPHOTO_RUNTIME_DIAG_STACK_PATH",
    ):
        env.pop(key, None)
    env.update(
        QT_QPA_PLATFORM="windows",
        IPHOTO_RHI_BACKEND="opengl",
        PYTHONUNBUFFERED="1",
        IPHOTO_LOG_DIR=str(session / "app-logs"),
    )
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def stop_owned_process(process):
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # sys.executable can be a venv redirector, so terminate only this owned
        # process tree rather than leaving its GUI child running.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    else:
        process.kill()
    process.wait(timeout=10)


def run_command(command, log: Path, env, timeout, *, interactive=False):
    started = time.monotonic()
    result = {
        "started_at": utc_now(),
        "command": [str(x) for x in command],
        "status": "running",
        "exit_code": None,
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    process = None
    reader = None
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(
                [str(x) for x in command],
                cwd=ROOT,
                env=env,
                stdin=None if interactive else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )

            def copy_output():
                try:
                    for line in iter(process.stdout.readline, b""):
                        output.write(line)
                        output.flush()
                        print(line.decode("utf-8", errors="replace").rstrip(), flush=True)
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=copy_output, daemon=True)
            reader.start()
            result["exit_code"] = process.wait(timeout=timeout)
            result["status"] = "completed"
        except subprocess.TimeoutExpired:
            result.update(status="timeout", error="Stage exceeded its time limit")
        except KeyboardInterrupt:
            result["status"] = "interrupted"
            raise
        except OSError as error:
            result.update(status="error", error=str(error))
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        stop_owned_process(process)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        result["cleanup_error"] = str(error)
                if reader is not None:
                    reader.join(timeout=5)
                    result["console_log_complete"] = not reader.is_alive()
                result["exit_code"] = process.poll()
            result.update(
                finished_at=utc_now(), duration_seconds=round(time.monotonic() - started, 2)
            )
            write_json(log.with_suffix(".process.json"), result)
    return result


def expected_probe_stages(cycles):
    return {
        f"{variant}/{cycle}/{mode}/{action}"
        for variant in ("plain", "crop", "straighten")
        for cycle in range(cycles)
        for mode in ("full", "window")
        for action in (*(f"idle-{i}" for i in range(5)), "wheel--120", "wheel-120")
    }


def assess_probe(path: Path, process_result, cycles):
    result = {
        "status": "error",
        "evidence_type": "desktop_pixel_probe",
        "expected_samples": 42 * cycles,
        "cycles": cycles,
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        samples = payload.get("samples", [])
        stages = [sample["stage"] for sample in samples]
        complete = len(stages) == 42 * cycles and set(stages) == expected_probe_stages(cycles)
        supported = (
            payload.get("schema_version") == 2 and payload.get("capture_method") == "desktop_region"
        )
        region_checks = all(
            sample.get("ok") is True
            and sample.get("regions")
            and all(
                region.get("ok") is True and region.get("status") == "passed"
                for region in sample["regions"]
            )
            for sample in samples
        )
        valid = (
            supported
            and complete
            and payload.get("cycles") == cycles
            and payload.get("fullscreen_overscan") is True
            and payload.get("fullscreen_composition_verifications", 0) > 0
        )
        passed = (
            valid
            and region_checks
            and payload.get("passed") is True
            and payload.get("failures") == []
            and process_result.get("exit_code") == 0
            and process_result.get("status") == "completed"
        )
        result.update(
            status="passed" if passed else "failed",
            complete=complete,
            supported_capture=supported,
            observed_samples=len(samples),
            failed_samples=sum(sample.get("ok") is not True for sample in samples),
            reported_failures=payload.get("failures", []),
        )
        if not complete or not supported:
            result["status"] = "incomplete"
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["error"] = str(error)
    return result


def assess_contracts(path: Path, process_result):
    result = {"status": "error", "evidence_type": "qt_state_contracts_not_gpu_acceptance"}
    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        counts = {
            key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        result.update(counts)
        if (
            counts["errors"]
            or counts["failures"]
            or process_result.get("exit_code") != 0
            or process_result.get("status") != "completed"
        ):
            result["status"] = "failed"
        elif counts["tests"] == 0 or counts["skipped"]:
            result["status"] = "incomplete"
        else:
            result["status"] = "passed"
    except (OSError, ValueError, ET.ParseError) as error:
        result["error"] = str(error)
    return result


def assess_application_bundle(path: Path):
    """Check evidence integrity and obvious failures, never infer visual success."""
    result = {"status": "error", "acceptance": "collection_is_not_visual_acceptance"}
    try:
        with ZipFile(path) as archive:
            files = {name.replace("\\", "/"): archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files["manifest.json"].decode("utf-8-sig"))
        if isinstance(manifest, dict):
            manifest = [manifest]
        required = {
            "system.json",
            "detail_events.jsonl",
            "runtime_stacks.log",
            "reproduction_markers.jsonl",
            "process_metrics.csv",
        }
        if not required <= {item["file"] for item in manifest}:
            raise ValueError("Collector manifest omits required evidence")
        if not manifest or not all(
            item["file"] in files
            and len(files[item["file"]]) == item["bytes"]
            and hashlib.sha256(files[item["file"]]).hexdigest().lower() == item["sha256"].lower()
            for item in manifest
        ):
            raise ValueError("Collector manifest failed verification")
        markers = [
            json.loads(line)
            for line in files["reproduction_markers.jsonl"].decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        metrics = list(
            csv.DictReader(io.StringIO(files["process_metrics.csv"].decode("utf-8-sig")))
        )
        stacks = files["runtime_stacks.log"].decode("utf-8-sig", errors="replace")
        runtime = json.loads(stacks.splitlines()[0])
        started = next(item for item in markers if item["marker"] == "application_started")
        pid = int(started["process_id"])
        if runtime.get("event") != "runtime_diagnostics_started" or int(runtime["pid"]) != pid:
            raise ValueError("Collector and GUI runtime PID disagree")
        if not metrics or any(int(row["pid"]) != pid for row in metrics):
            raise ValueError("Metrics do not identify the GUI process")
        marker_names = [item["marker"] for item in markers]
        issues = []
        if any(row["is_hung"] == "1" for row in metrics):
            issues.append("hung_window_observed")
        if "Windows fatal exception:" in stacks:
            issues.append("native_fatal_exception_recorded")
        events_text = files.get("windows_application_events.json", b"[]").decode("utf-8-sig")
        events = json.loads(events_text.strip() or "[]") or []
        if isinstance(events, dict):
            events = [events]
        started_at = datetime.fromisoformat(started["utc_time"].replace("Z", "+00:00"))
        for event in events:
            match = re.search(
                r"Faulting process id:\s*(0x[0-9a-f]+|[0-9]+)",
                event.get("Message", ""),
                re.IGNORECASE,
            )
            if event.get("Id") == 1000 and match:
                event_pid = int(match[1], 16 if match[1].lower().startswith("0x") else 10)
                event_time = datetime.fromisoformat(event["TimeCreated"].replace("Z", "+00:00"))
                if event_pid == pid and event_time >= started_at:
                    issues.append("matching_windows_application_error")
                    break
        for name in ("problem_reproduced", "collector_forced_stop"):
            if name in marker_names:
                issues.append(name)
        collector_error = any(name.endswith("collector_error.txt") for name in files)
        complete = (
            "application_exited" in marker_names
            and "collector_timeout" not in marker_names
            and not collector_error
        )
        result.update(
            status="failed" if issues else ("completed" if complete else "incomplete"),
            manifest_verified=True,
            gui_pid=pid,
            metrics_count=len(metrics),
            issues=issues,
            markers=marker_names,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
        IndexError,
        BadZipFile,
    ) as error:
        result["error"] = str(error)
    return result


def overall_status(report):
    statuses = [entry.get("status") for entry in report["automatic"].values()]
    statuses += [entry.get("status") for entry in report["manual"].values()]
    if (
        report.get("runner_error")
        or report["application_collection"].get("status") in {"failed", "error", "timeout"}
        or any(status in {"failed", "error", "timeout"} for status in statuses)
    ):
        return "failed"
    if (
        report.get("interrupted")
        or report["cycles"] < 20
        or any(status != "passed" for status in statuses)
        or report["application_collection"].get("status") != "completed"
        or not report["preflight"].get("commit")
        or report["preflight"].get("working_tree_dirty") is not False
    ):
        return "incomplete"
    return "passed"


def ask_status(title, input_fn=None):
    if input_fn is None:
        input_fn = input
    while True:
        choice = (
            input_fn(f"{title}\n  P=passed all steps, F=failed, S=not tested [S]: ").strip().lower()
        )
        if choice in {"p", "f", "s", ""}:
            status = {"p": "passed", "f": "failed"}.get(choice, "skipped")
            note = input_fn("  Optional notes (DPI, monitor, symptoms): ").strip()
            return {
                "status": status,
                "notes": note,
                "evidence_type": "user_attestation",
                "time": utc_now(),
            }


def redactions(session, library_root):
    replacements = {}
    for value, token in (
        (str(session), "<VALIDATION_DIR>"),
        (str(ROOT), "<REPOSITORY>"),
        (str(Path(sys.executable).parent), "<PYTHON_DIR>"),
        (os.environ.get("USERPROFILE"), "<USERPROFILE>"),
        (str(Path.home()), "<USERPROFILE>"),
        (os.environ.get("TEMP"), "<TEMP>"),
        (library_root, "<LIBRARY_ROOT>"),
    ):
        if value:
            for variant in (value, value.replace("\\", "/"), value.replace("\\", "\\\\")):
                replacements[variant] = token
    return sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True)


def payload_files(session):
    bundle_root = session / "application" / "bundles"
    sealed = list(bundle_root.glob("*.zip")) if bundle_root.exists() else []
    result = []
    for path in session.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix in {".tmp", ".redacting"}:
            continue
        if path == session / "manifest.json":
            continue
        if bundle_root in path.parents:
            if sealed and path not in sealed:
                continue  # Do not duplicate or modify the collector's sealed bundle.
            if not sealed and path.name == "manifest.json":
                continue  # Partial collector data is covered by the outer manifest.
        result.append(path)
    return sorted(result)


def package_report(session, report, library_root=""):
    report["overall_status"] = overall_status(report)
    report["finished_at"] = utc_now()
    write_json(session / "validation.json", report)
    lines = [
        "# Windows fullscreen validation",
        "",
        f"Overall: **{report['overall_status']}**",
        f"Source commit: `{report['preflight'].get('commit')}`",
        f"Requested probe cycles: {report['cycles']}",
        "",
        "Automatic evidence and user observations are separate. Skipped/not-run items are not passes.",
        "This bundle does not update a PR or establish acceptance on other machines.",
        "",
        "## Automatic checks",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    lines += [f"| {name} | {entry['status']} |" for name, entry in report["automatic"].items()]
    lines += ["", "## User observations", "", "| Check | Status |", "|---|---|"]
    lines += [f"| {name} | {entry['status']} |" for name, entry in report["manual"].items()]
    lines += [
        "",
        "Details and notes: `validation.json`. Application diagnostics: `application/bundles/`.",
        "Qt state tests are not GPU/compositor acceptance. Filenames or media metadata may appear in logs; review before sharing.",
    ]
    (session / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = payload_files(session)
    replacements = redactions(session, library_root)
    for path in files:
        if path.suffix.lower() not in {".json", ".jsonl", ".log", ".xml", ".txt", ".md", ".csv"}:
            continue
        temporary = path.with_suffix(path.suffix + ".redacting")
        with (
            path.open(encoding="utf-8-sig", errors="replace") as source,
            temporary.open("w", encoding="utf-8") as target,
        ):
            for line in source:
                for original, token in replacements:
                    replacement = (
                        token.replace("<", "&lt;").replace(">", "&gt;")
                        if path.suffix.lower() == ".xml"
                        else token
                    )
                    line = re.sub(
                        re.escape(original),
                        lambda _match, value=replacement: value,
                        line,
                        flags=re.IGNORECASE,
                    )
                target.write(line)
        temporary.replace(path)
    archive = session.with_suffix(".zip")
    temporary_archive = archive.with_suffix(".zip.tmp")
    manifest = []
    with ZipFile(temporary_archive, "w", compression=ZIP_DEFLATED) as zipped:
        for path in files:
            name = path.relative_to(session).as_posix()
            digest, size = hashlib.sha256(), 0
            # Hash exactly the bytes packaged, even for a partial log whose
            # producer failed to terminate cleanly during timeout cleanup.
            with path.open("rb") as source, zipped.open(name, "w", force_zip64=True) as target:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
                    digest.update(block)
                    size += len(block)
            manifest.append({"file": name, "bytes": size, "sha256": digest.hexdigest()})
        write_json(session / "manifest.json", manifest)
        zipped.write(session / "manifest.json", "manifest.json")
    temporary_archive.replace(archive)
    return archive


def desktop_path(powershell):
    if powershell:
        try:
            command = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); [Environment]::GetFolderPath('Desktop')"
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Path.home() / "Desktop"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cycles",
        type=int,
        default=20,
        help="Less than 20 is a smoke run, not complete acceptance",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--app-path",
        type=Path,
        help="Optional packaged app for the manual collector stage; probes still use source",
    )
    parser.add_argument("--library-root-to-redact", default="")
    parser.add_argument("--max-minutes", type=int, default=30)
    parser.add_argument(
        "--probe-timeout", type=int, default=1800, help="Seconds per automatic probe"
    )
    parser.add_argument("--skip-probes", action="store_true")
    parser.add_argument("--skip-contracts", action="store_true")
    parser.add_argument("--skip-app", action="store_true")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Automatic checks only; manual acceptance stays not_run",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this script in an interactive Windows PowerShell terminal")
    if not 1 <= args.cycles <= 100 or not 1 <= args.max_minutes <= 120 or args.probe_timeout < 30:
        parser.error("cycles=1..100, max-minutes=1..120, probe-timeout>=30 required")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    root = args.output_root or desktop_path(powershell)
    session = root / (
        "iPhoto-windows-validation-"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid.uuid4().hex[:6]
    )
    session.mkdir(parents=True)
    report = {
        "kind": KIND,
        "schema_version": 1,
        "started_at": utc_now(),
        "cycles": args.cycles,
        "preflight": {},
        "automatic": {
            name: {"status": "not_run"}
            for name in ("state_contracts", "overscan", "overscan_gl_state")
        },
        "application_collection": {"status": "not_run"},
        "manual": {
            name: {"status": "not_run", "evidence_type": "user_attestation"}
            for name, _ in MANUAL_CASES
        },
    }
    print(f"Evidence directory: {session}\nInterpreter: {sys.executable}", flush=True)
    print(
        "Keep the desktop unlocked and probe windows unobscured. Automatic probes use synthetic green media."
    )
    print(
        "No packages, drivers, display settings or GitHub state will be changed. Ctrl+C keeps a partial bundle."
    )
    checkpoint = lambda: write_json(session / "validation.json", report)
    try:
        report["preflight"] = preflight()
        checkpoint()
        if not report["preflight"]["qt_available"]:
            raise RuntimeError(
                "PySide6 could not be imported; run with the application's Python environment"
            )
        if not report["preflight"]["desktop_capture_probe_available"]:
            raise RuntimeError("Update the checkout: the corrected desktop-region probe is missing")
        checklist = [
            "# Manual Windows fullscreen checklist",
            "",
            "Only mark P after actually testing every substep; use S for anything not tested.",
            "Automatic probe success does not establish these observations.",
            "",
        ]
        checklist += [f"- [ ] {title}" for _, title in MANUAL_CASES]
        checklist += [
            "",
            "Use R in the collector console to mark a problem; close iPhoto normally to finish.",
            "Use Q there for a hung app. Ordinary terminal and PyCharm runs must be tested separately.",
            "Record the results in this validation runner after the collector finishes.",
        ]
        (session / "CHECKLIST.md").write_text("\n".join(checklist) + "\n", encoding="utf-8")
        env = child_environment(session)
        if args.skip_contracts or not report["preflight"]["pytest_available"]:
            report["automatic"]["state_contracts"] = {
                "status": "skipped",
                "reason": "requested or pytest unavailable",
            }
        else:
            folder = session / "automatic" / "state-contracts"
            folder.mkdir(parents=True)
            print("\n[1/4] Qt state and capture contracts (not GPU acceptance)", flush=True)
            process = run_command(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *CONTRACT_TESTS,
                    "--junitxml",
                    str(folder / "junit.xml"),
                ],
                folder / "console.log",
                env,
                300,
            )
            report["automatic"]["state_contracts"] = assess_contracts(folder / "junit.xml", process)
        checkpoint()
        print(f"State contracts: {report['automatic']['state_contracts']['status']}", flush=True)
        for index, (name, extra) in enumerate(
            (("overscan", []), ("overscan_gl_state", ["--poison-gl-state"])), 2
        ):
            if args.skip_probes:
                report["automatic"][name] = {"status": "skipped", "reason": "requested"}
                continue
            folder = session / "automatic" / name
            folder.mkdir(parents=True)
            print(
                f"\n[{index}/4] {name}: {args.cycles} cycles per media variant; do not operate the probe",
                flush=True,
            )
            process = run_command(
                [
                    sys.executable,
                    str(ROOT / "tools/windows_fullscreen_probe.py"),
                    "--cycles",
                    str(args.cycles),
                    "--fullscreen-overscan",
                    "--output",
                    str(folder),
                    *extra,
                ],
                folder / "console.log",
                env,
                args.probe_timeout,
            )
            report["automatic"][name] = assess_probe(folder / "result.json", process, args.cycles)
            print(f"{name}: {report['automatic'][name]['status']}", flush=True)
            if not args.non_interactive:
                report["manual"][name + "_visual"] = ask_status(
                    f"During {name}: no visible flicker, offset or freeze?"
                )
            else:
                report["manual"][name + "_visual"] = {
                    "status": "not_run",
                    "evidence_type": "user_attestation",
                }
            checkpoint()
        print(
            "\n[4/4] Real application manual matrix. P means ALL listed steps were actually tested."
        )
        for number, (_name, title) in enumerate(MANUAL_CASES, 1):
            print(f"  {number}. {title}")
        print(
            "Ordinary launch: python .\\src\\entrypoint.py. Test PyCharm independently; the collector does not verify an IDE run."
        )
        print(
            "In the collector: R marks a problem; close iPhoto to finish; Q force-stops a hung app."
        )
        if args.skip_app or args.non_interactive or not powershell:
            report["application_collection"] = {
                "status": "skipped",
                "reason": "requested, noninteractive, or PowerShell unavailable",
            }
        else:
            answer = (
                input("Close other iPhoto instances. Start the diagnostic application now? [Y/n]: ")
                .strip()
                .lower()
            )
            if answer in {"n", "no"}:
                report["application_collection"] = {"status": "skipped", "reason": "user skipped"}
            else:
                folder = session / "application"
                command = [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "tools/collect_windows_scan_playback_diagnostics.ps1"),
                    "-Scenario",
                    "Fullscreen",
                    "-OutputRoot",
                    str(folder / "bundles"),
                    "-MaxMinutes",
                    str(args.max_minutes),
                ]
                if args.app_path:
                    command += ["-AppPath", str(args.app_path.resolve())]
                else:
                    command += ["-PythonExe", sys.executable]
                if args.library_root_to_redact:
                    command += ["-LibraryRootToRedact", args.library_root_to_redact]
                process = run_command(
                    command,
                    folder / "console.log",
                    env,
                    args.max_minutes * 60 + 180,
                    interactive=True,
                )
                bundles = list((folder / "bundles").glob("*.zip"))
                assessment = (
                    assess_application_bundle(bundles[0])
                    if len(bundles) == 1
                    else {"status": "error", "error": "Expected exactly one diagnostic ZIP"}
                )
                report["application_collection"] = {
                    **assessment,
                    "launch_mode": "packaged" if args.app_path else "source",
                    "bundles": [p.relative_to(session).as_posix() for p in bundles],
                    "exit_code": process["exit_code"],
                    "acceptance": "collection_completed_does_not_mean_app_passed",
                }
                if process["exit_code"] != 0 or process["status"] != "completed":
                    report["application_collection"]["status"] = "error"
        checkpoint()
        print(
            f"Application diagnostics: {report['application_collection']['status']} (not a visual verdict)",
            flush=True,
        )
        if not args.non_interactive:
            print(
                "\nNow test any remaining ordinary/PyCharm/DPI cases yourself. Enter S for anything not actually tested."
            )
            for name, title in MANUAL_CASES:
                report["manual"][name] = ask_status(title)
                checkpoint()
    except KeyboardInterrupt:
        report["interrupted"] = True
        print("\nInterrupted; preserving completed and partial evidence.")
    except Exception as error:
        report["runner_error"] = str(error)
        report["interrupted"] = True
        print(f"\nRunner error: {error}")
    finally:
        archive = package_report(session, report, args.library_root_to_redact)
        print(
            f"\nStatus: {report['overall_status']}\nSend this ONE ZIP after reviewing it:\n{archive}",
            flush=True,
        )
    return {"passed": 0, "failed": 1, "incomplete": 2}[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture a Windows Detail/rotation reproduction without copying the photo.

The script launches iPhotron with Detail profiling enabled, captures its console
and file logs, records process memory, and collects the target image's sidecar
and filesystem metadata. Close iPhotron after reproducing the failure; the
script then creates one zip archive suitable for attaching to a bug report.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture an iPhotron Windows Detail/rotation reproduction."
    )
    parser.add_argument(
        "--photo",
        type=Path,
        required=True,
        help="The affected photo. The image itself is never copied.",
    )
    parser.add_argument(
        "--app",
        type=Path,
        help=(
            "Packaged iPhotron executable. Omit this when running from the source "
            "checkout with the current Python environment."
        ),
    )
    parser.add_argument(
        "--app-arg",
        action="append",
        default=[],
        help="Extra argument passed to the app; repeat for multiple arguments.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("windows-detail-diagnostics"),
        help="Directory in which the timestamped diagnostic bundle is created.",
    )
    return parser


def _run_text(command: list[str], *, timeout: int = 30) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _photo_report(photo: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "name": photo.name,
        "suffix": photo.suffix.lower(),
        "is_onedrive_path": "onedrive" in str(photo).lower(),
        "exists": photo.exists(),
        "sidecar_name": photo.with_suffix(".ipo").name,
    }
    try:
        stat = photo.stat()
    except OSError as exc:
        report["stat_error"] = f"{type(exc).__name__}: {exc}"
        return report
    report.update(
        {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "mode": oct(stat.st_mode),
            "sha256": _sha256(photo),
        }
    )
    try:
        from PIL import Image

        with Image.open(photo) as image:
            report["decoded_format"] = image.format
            report["width"] = image.width
            report["height"] = image.height
            report["mode_name"] = image.mode
    except Exception as exc:  # Pillow is optional and codecs vary.
        report["pillow_probe"] = f"{type(exc).__name__}: {exc}"
    return report


def _sidecar_report(photo: Path, bundle_dir: Path) -> dict[str, object]:
    sidecar = photo.with_suffix(".ipo")
    report: dict[str, object] = {
        "exists": sidecar.is_file(),
        "name": sidecar.name,
    }
    if not sidecar.is_file():
        return report
    try:
        stat = sidecar.stat()
        report.update(
            {
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": _sha256(sidecar),
            }
        )
        shutil.copy2(sidecar, bundle_dir / "target-sidecar.ipo")
        root = ElementTree.parse(sidecar).getroot()
        report["xml_root"] = root.tag
        report["xml_values"] = {
            element.tag: element.text
            for element in root.iter()
            if element is not root and element.text is not None
        }
    except (OSError, ElementTree.ParseError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def _windows_process_memory(pid: int) -> dict[str, int] | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    process_query_limited_information = 0x1000
    process_vm_read = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information | process_vm_read,
        False,
        pid,
    )
    if not handle:
        return None
    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    try:
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            return None
        return {
            "working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
            "private_bytes": int(counters.PrivateUsage),
            "pagefile_bytes": int(counters.PagefileUsage),
        }
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _monitor_memory(process: subprocess.Popen, target: Path, stop: threading.Event) -> None:
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "wall_time",
                "pid",
                "working_set_bytes",
                "peak_working_set_bytes",
                "private_bytes",
                "pagefile_bytes",
            ],
        )
        writer.writeheader()
        while not stop.wait(0.25):
            if process.poll() is not None:
                break
            sample = _windows_process_memory(process.pid)
            if sample is None:
                continue
            writer.writerow({"wall_time": time.time(), "pid": process.pid, **sample})
            stream.flush()


def _system_report() -> dict[str, object]:
    report: dict[str, object] = {
        "platform": platform.platform(),
        "windows_version": platform.win32_ver(),
        "python": sys.version,
        "python_executable": Path(sys.executable).name,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell:
            report["video_controllers"] = _run_text(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_VideoController | "
                        "Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor | "
                        "ConvertTo-Json -Depth 3"
                    ),
                ]
            )
    return report


def _filesystem_report(photo: Path) -> dict[str, object]:
    report = {
        "attrib": _run_text(["attrib", str(photo)]) if os.name == "nt" else {},
        "icacls": _run_text(["icacls", str(photo)]) if os.name == "nt" else {},
    }
    sidecar = photo.with_suffix(".ipo")
    if sidecar.exists() and os.name == "nt":
        report["sidecar_attrib"] = _run_text(["attrib", str(sidecar)])
        report["sidecar_icacls"] = _run_text(["icacls", str(sidecar)])
    return report


def _zip_bundle(bundle_dir: Path) -> Path:
    archive = bundle_dir.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(bundle_dir))
    return archive


def main() -> int:
    args = _parser().parse_args()
    photo = args.photo.expanduser().absolute()
    if not photo.is_file():
        print(f"Photo does not exist: {photo}", file=sys.stderr)
        return 2
    if os.name != "nt":
        print("Warning: this diagnostic is intended to be run on Windows.", file=sys.stderr)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_dir.expanduser().absolute()
    bundle_dir = output_root / f"iphoto-detail-{timestamp}"
    bundle_dir.mkdir(parents=True, exist_ok=False)
    log_dir = bundle_dir / "app-logs"
    log_dir.mkdir()

    if args.app is None:
        command = [sys.executable, "-m", "iPhoto.entrypoint", *args.app_arg]
        app_cwd = Path.cwd()
    else:
        app = args.app.expanduser().absolute()
        if not app.is_file():
            print(f"Application does not exist: {app}", file=sys.stderr)
            return 2
        command = [str(app), *args.app_arg]
        app_cwd = app.parent

    environment = os.environ.copy()
    environment.update(
        {
            "IPHOTO_DETAIL_PROFILE": "1",
            "IPHOTO_DETAIL_PROFILE_PATH": str(bundle_dir / "detail-events.jsonl"),
            "IPHOTO_PERF_LOG": "1",
            "IPHOTO_LOG_DIR": str(log_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )
    runtime = {
        "command_executable": Path(command[0]).name,
        "arguments": command[1:],
        "started_at": time.time(),
        "photo": _photo_report(photo),
        "sidecar": _sidecar_report(photo, bundle_dir),
        "filesystem": _filesystem_report(photo),
        "system": _system_report(),
        "environment": {
            "IPHOTO_RHI_BACKEND": environment.get("IPHOTO_RHI_BACKEND", "auto"),
            "QT_OPENGL": environment.get("QT_OPENGL", ""),
            "QT_RHI_BACKEND": environment.get("QT_RHI_BACKEND", ""),
        },
    }
    report_path = bundle_dir / "report.json"
    report_path.write_text(
        json.dumps(runtime, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    console_path = bundle_dir / "app-console.log"
    print("iPhotron will start with Detail diagnostics enabled.")
    print("Reproduce the rapid-rotation blank screen, click Edit once, then close iPhotron.")
    print("Do not close this diagnostic console while iPhotron is running.")
    stop_monitor = threading.Event()
    try:
        with console_path.open("w", encoding="utf-8", errors="replace") as console:
            process = subprocess.Popen(
                command,
                cwd=app_cwd,
                env=environment,
                stdout=console,
                stderr=subprocess.STDOUT,
                text=True,
            )
            monitor = threading.Thread(
                target=_monitor_memory,
                args=(process, bundle_dir / "process-memory.csv", stop_monitor),
                daemon=True,
            )
            monitor.start()
            try:
                returncode = process.wait()
            except KeyboardInterrupt:
                process.terminate()
                try:
                    returncode = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    returncode = process.wait()
            finally:
                stop_monitor.set()
                monitor.join(timeout=2)
    except OSError as exc:
        runtime["launch_error"] = f"{type(exc).__name__}: {exc}"
        returncode = -1

    runtime["returncode"] = returncode
    runtime["finished_at"] = time.time()
    runtime["sidecar_after"] = _sidecar_report(photo, bundle_dir)
    report_path.write_text(
        json.dumps(runtime, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    archive = _zip_bundle(bundle_dir)
    print(f"Diagnostic bundle created: {archive}")
    print("The zip contains local paths in application logs and a copy of the .ipo sidecar.")
    print("It does not contain the original photo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

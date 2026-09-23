import hashlib
import json
import os
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from tools import validate_windows_fullscreen as runner


def probe_result(cycles=1):
    return {
        "schema_version": 2,
        "capture_method": "desktop_region",
        "cycles": cycles,
        "fullscreen_overscan": True,
        "fullscreen_composition_verifications": 1,
        "passed": True,
        "failures": [],
        "samples": [
            {"stage": stage, "ok": True, "regions": [{"ok": True, "status": "passed"}]}
            for stage in sorted(runner.expected_probe_stages(cycles))
        ],
    }


def successful_process():
    return {"status": "completed", "exit_code": 0}


@pytest.mark.parametrize(
    "case", ["valid", "truncated", "duplicate", "old_capture", "empty_regions", "exit_failure"]
)
def test_probe_acceptance_requires_complete_supported_successful_run(tmp_path, case):
    data = probe_result()
    process = successful_process()
    if case == "truncated":
        data["samples"].pop()
    elif case == "duplicate":
        data["samples"][-1] = data["samples"][0]
    elif case == "old_capture":
        data["capture_method"] = "window_hwnd"
    elif case == "empty_regions":
        data["samples"][0]["regions"] = []
    elif case == "exit_failure":
        process["exit_code"] = 1
    path = tmp_path / "result.json"
    runner.write_json(path, data)
    result = runner.assess_probe(path, process, 1)
    assert (result["status"] == "passed") == (case == "valid")
    assert result["expected_samples"] == 42


def full_report():
    return {
        "kind": runner.KIND,
        "cycles": 20,
        "preflight": {"commit": "a688e437", "working_tree_dirty": False},
        "automatic": {
            name: {"status": "passed"}
            for name in ("state_contracts", "overscan", "overscan_gl_state")
        },
        "application_collection": {"status": "completed"},
        "manual": {
            name: {"status": "passed", "evidence_type": "user_attestation"}
            for name, _ in runner.MANUAL_CASES
        },
    }


def test_manual_failure_overrides_passing_pixels_and_skip_is_never_passed():
    report = full_report()
    assert runner.overall_status(report) == "passed"
    report["manual"]["pycharm_launch"]["status"] = "skipped"
    assert runner.overall_status(report) == "incomplete"
    report["manual"]["pycharm_launch"]["status"] = "failed"
    assert runner.overall_status(report) == "failed"


@pytest.mark.parametrize("field,value", [("cycles", 3), ("interrupted", True)])
def test_smoke_or_interrupted_run_cannot_be_complete_acceptance(field, value):
    report = full_report()
    report[field] = value
    assert runner.overall_status(report) == "incomplete"


def test_unknown_or_dirty_revision_is_not_verified_pr_acceptance():
    for value in (True, None):
        report = full_report()
        report["preflight"]["working_tree_dirty"] = value
        assert runner.overall_status(report) == "incomplete"


def test_default_manual_answer_is_skipped():
    answers = iter(["", "not available"])
    result = runner.ask_status("Cross-monitor", lambda _: next(answers))
    assert result["status"] == "skipped"
    assert result["evidence_type"] == "user_attestation"


def test_contracts_are_incomplete_if_skipped_or_missing(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1"/></testsuites>'
    )
    assert runner.assess_contracts(path, successful_process())["status"] == "incomplete"
    assert (
        runner.assess_contracts(tmp_path / "missing.xml", successful_process())["status"] == "error"
    )


def collector_bundle(
    path, *, pid=123, runtime_pid=123, hung=False, fatal=False, forced=False, events=()
):
    files = {
        "system.json": b"{}",
        "detail_events.jsonl": b"",
        "windows_application_events.json": json.dumps(events).encode(),
        "runtime_stacks.log": (
            json.dumps({"event": "runtime_diagnostics_started", "pid": runtime_pid})
            + ("\nWindows fatal exception: access violation" if fatal else "")
        ).encode(),
        "process_metrics.csv": f"pid,is_hung\n{pid},{int(hung)}\n".encode(),
        "reproduction_markers.jsonl": (
            json.dumps(
                {
                    "marker": "application_started",
                    "process_id": pid,
                    "utc_time": "2026-09-23T00:00:00Z",
                }
            )
            + "\n"
            + (json.dumps({"marker": "collector_forced_stop"}) + "\n" if forced else "")
            + json.dumps({"marker": "application_exited"})
        ).encode(),
    }
    manifest = [
        {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    ]
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))


@pytest.mark.parametrize("case", ["valid", "wrong_pid", "hung", "fatal", "forced"])
def test_collector_checks_pid_integrity_and_failure_evidence(tmp_path, case):
    path = tmp_path / "app.zip"
    collector_bundle(
        path,
        runtime_pid=999 if case == "wrong_pid" else 123,
        hung=case == "hung",
        fatal=case == "fatal",
        forced=case == "forced",
    )
    result = runner.assess_application_bundle(path)
    expected = "completed" if case == "valid" else "error" if case == "wrong_pid" else "failed"
    assert result["status"] == expected


def test_tampered_collector_manifest_fails(tmp_path):
    path = tmp_path / "bad.zip"
    collector_bundle(path)
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["runtime_stacks.log"] += b"tampered"
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    assert runner.assess_application_bundle(path)["status"] == "error"


@pytest.mark.parametrize("case", ["current", "historical", "other_pid", "no_events"])
def test_windows_application_errors_are_correlated_to_this_process_and_run(tmp_path, case):
    path = tmp_path / "app.zip"
    events = (
        None
        if case == "no_events"
        else [
            {
                "Id": 1000,
                "Message": "Faulting process id: " + ("0x7b" if case != "other_pid" else "0x8b"),
                "TimeCreated": "2026-09-22T23:59:00Z"
                if case == "historical"
                else "2026-09-23T00:01:00Z",
            }
        ]
    )
    collector_bundle(path, events=events)
    result = runner.assess_application_bundle(path)
    assert result["status"] == ("failed" if case == "current" else "completed")


def test_packaging_redacts_text_preserves_xml_and_sealed_bundle_and_hashes(tmp_path):
    session = tmp_path / "validation"
    session.mkdir()
    report = full_report()
    report["preflight"]["example"] = str(session / "console.log")
    (session / "console.log").write_text(
        f"root={runner.ROOT}\noutput={session}\n", encoding="utf-8"
    )
    (session / "junit.xml").write_text(
        f"<testsuite><system-out>{session}</system-out></testsuite>", encoding="utf-8"
    )
    bundles = session / "application" / "bundles"
    bundles.mkdir(parents=True)
    sealed = bundles / "collector.zip"
    collector_bundle(sealed)
    original_hash = runner.file_hash(sealed)
    expanded = bundles / "collector"
    expanded.mkdir()
    (expanded / "raw.log").write_text("Should not be duplicated or rewritten")
    archive_path = runner.package_report(session, report)
    with ZipFile(archive_path) as archive:
        assert "application/bundles/collector/raw.log" not in archive.namelist()
        assert (
            hashlib.sha256(archive.read("application/bundles/collector.zip")).hexdigest()
            == original_hash
        )
        assert str(session).encode() not in archive.read("validation.json")
        assert b"<VALIDATION_DIR>" in archive.read("validation.json")
        ET.fromstring(archive.read("junit.xml"))
        for item in json.loads(archive.read("manifest.json")):
            data = archive.read(item["file"])
            assert len(data) == item["bytes"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"]


def test_partial_collector_logs_are_packaged_without_stale_inner_manifest(tmp_path):
    session = tmp_path / "partial"
    partial = session / "application" / "bundles" / "collector"
    partial.mkdir(parents=True)
    (partial / "stderr.log").write_text(str(runner.ROOT))
    (partial / "manifest.json").write_text("stale")
    report = full_report()
    report["interrupted"] = True
    archive_path = runner.package_report(session, report)
    with ZipFile(archive_path) as archive:
        assert "application/bundles/collector/manifest.json" not in archive.namelist()
        assert b"<REPOSITORY>" in archive.read("application/bundles/collector/stderr.log")
        assert json.loads(archive.read("validation.json"))["overall_status"] == "incomplete"


def test_child_environment_does_not_modify_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", "0")
    env = runner.child_environment(tmp_path)
    assert env["QT_QPA_PLATFORM"] == "windows"
    assert env["IPHOTO_RHI_BACKEND"] == "opengl"
    assert "IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN" not in env
    assert os.environ["IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN"] == "0"


def test_command_failure_keeps_output_and_exit_status(tmp_path):
    result = runner.run_command(
        [sys.executable, "-c", "print('test evidence'); raise SystemExit(3)"],
        tmp_path / "console.log",
        os.environ.copy(),
        10,
    )
    assert result["exit_code"] == 3
    assert "test evidence" in (tmp_path / "console.log").read_text()
    assert (tmp_path / "console.process.json").exists()


def test_command_timeout_keeps_partial_evidence(tmp_path):
    result = runner.run_command(
        [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(10)"],
        tmp_path / "console.log",
        os.environ.copy(),
        0.2,
    )
    assert result["status"] == "timeout"
    assert result["exit_code"] is not None
    assert (tmp_path / "console.process.json").exists()


@pytest.mark.parametrize("mode", ["automatic_only", "interrupted", "interactive"])
def test_one_entrypoint_packages_results_even_when_incomplete_or_interrupted(
    tmp_path, monkeypatch, mode
):
    args = SimpleNamespace(
        cycles=20,
        output_root=tmp_path,
        app_path=None,
        library_root_to_redact="",
        max_minutes=1,
        probe_timeout=30,
        skip_probes=False,
        skip_contracts=False,
        skip_app=False,
        non_interactive=mode != "interactive",
    )
    monkeypatch.setattr(runner.argparse.ArgumentParser, "parse_args", lambda _: args)
    monkeypatch.setattr(runner, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        runner,
        "preflight",
        lambda: {
            "commit": "test-head",
            "working_tree_dirty": False,
            "qt_available": True,
            "pytest_available": True,
            "desktop_capture_probe_available": True,
        },
    )

    def fake_run(command, log, env, timeout, *, interactive=False):
        assert env["IPHOTO_RHI_BACKEND"] == "opengl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("partial evidence", encoding="utf-8")
        if "--junitxml" in command:
            path = runner.Path(command[command.index("--junitxml") + 1])
            path.write_text(
                '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>'
            )
        elif "-File" in command:
            assert interactive
            folder = runner.Path(command[command.index("-OutputRoot") + 1])
            folder.mkdir(parents=True)
            collector_bundle(folder / "collector.zip")
        else:
            assert "--fullscreen-overscan" in command
            if mode == "interrupted":
                raise KeyboardInterrupt
            folder = runner.Path(command[command.index("--output") + 1])
            runner.write_json(folder / "result.json", probe_result(20))
        return successful_process()

    monkeypatch.setattr(runner, "run_command", fake_run)
    answers = iter(
        ["p", "", "p", "", "y"] + [answer for _ in runner.MANUAL_CASES for answer in ("s", "")]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert runner.main() == 2
    archives = list(tmp_path.glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        report = json.loads(archive.read("validation.json"))
        assert report["overall_status"] == "incomplete"
        assert report["automatic"]["state_contracts"]["status"] == "passed"
        if mode == "interrupted":
            assert report["interrupted"]
            assert archive.read("automatic/overscan/console.log") == b"partial evidence"
        elif mode == "interactive":
            assert report["application_collection"]["manifest_verified"]
            assert report["manual"]["pycharm_launch"]["status"] == "skipped"
        else:
            assert report["automatic"]["overscan"]["status"] == "passed"
            assert report["manual"]["pycharm_launch"]["status"] == "not_run"

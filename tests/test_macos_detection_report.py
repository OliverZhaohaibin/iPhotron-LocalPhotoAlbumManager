from __future__ import annotations

import json
import plistlib
from pathlib import Path

from tools.macos_detection_report import build_report, main


def test_bundle_checks_and_pending_matrix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tools.macos_detection_report.sys.platform", "darwin")
    monkeypatch.setattr("tools.macos_detection_report.platform.machine", lambda: "arm64")
    bundle = tmp_path / "iPhotron.app"
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    executable = contents / "MacOS" / "iPhotron"
    executable.write_text("stub", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | 0o111)
    (contents / "Resources" / "maps").mkdir(parents=True)
    (contents / "Resources" / "maps" / "extension.tar").write_bytes(b"tar")
    (contents / "Resources" / "maps" / "style.json").write_text("{}", encoding="utf-8")
    (contents / "Resources" / "main.qsb").write_bytes(b"qsb")
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "iPhotron"}))

    report = build_report(bundle=bundle)

    assert report["overall"] == "pending_manual_validation"
    assert "macOS Intel matrix" in report["pending_manual_validation"]
    assert all(item["status"] != "fail" for item in report["checks"])
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["map_extension_resource"]["status"] == "pass"
    assert checks["metal_path"]["status"] == "not_checked"


def test_cli_writes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tools.macos_detection_report.sys.platform", "darwin")
    monkeypatch.setattr("tools.macos_detection_report.platform.machine", lambda: "arm64")
    output = tmp_path / "report"

    assert main(["--output-dir", str(output)]) == 0
    payload = json.loads((output / "macos-detection-report.json").read_text(encoding="utf-8"))
    assert payload["report_type"] == "macos_startup_detection"
    assert (output / "macos-detection-report.md").is_file()

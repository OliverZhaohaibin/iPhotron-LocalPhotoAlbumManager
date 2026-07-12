from __future__ import annotations

import json
from pathlib import Path

from iPhoto.bootstrap.bootstrap_settings import load_bootstrap_settings
from iPhoto.settings.manager import SettingsManager


def test_bootstrap_settings_tolerates_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")

    snapshot = load_bootstrap_settings(path)

    assert snapshot.basic_library_path is None
    assert snapshot.load_error is not None


def test_settings_recovery_preserves_corrupt_file_and_writes_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    manager = SettingsManager(path)

    warning = manager.load_with_recovery()

    assert warning is not None
    assert manager.get("ui.theme") == "system"
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "iPhoto/settings@1"
    backups = list(tmp_path.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"

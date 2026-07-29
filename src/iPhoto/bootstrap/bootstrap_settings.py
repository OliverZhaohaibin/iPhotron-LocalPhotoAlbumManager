"""Small standard-library-only settings reader for pre-Qt startup."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def bootstrap_settings_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "iPhoto" / "settings.json"
        return Path.home() / "AppData" / "Roaming" / "iPhoto" / "settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "iPhoto" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "iPhoto" / "settings.json"


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    path: Path
    basic_library_path: str | None = None
    theme: str = "system"
    language: str = "system"
    load_error: str | None = None


def load_bootstrap_settings(path: Path | None = None) -> BootstrapSettings:
    resolved = path or bootstrap_settings_path()
    try:
        if not resolved.exists():
            return BootstrapSettings(path=resolved)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("settings root must be an object")
        ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
        library = payload.get("basic_library_path")
        theme = ui.get("theme", "system")
        language = ui.get("language", "system")
        return BootstrapSettings(
            path=resolved,
            basic_library_path=library if isinstance(library, str) and library else None,
            theme=theme if theme in {"system", "light", "dark"} else "system",
            language=language if language in {"system", "de", "zh-CN"} else "system",
        )
    except Exception as exc:  # noqa: BLE001 - bootstrap must always reach Qt
        return BootstrapSettings(
            path=resolved,
            load_error=f"{type(exc).__name__}: {exc}",
        )


__all__ = ["BootstrapSettings", "bootstrap_settings_path", "load_bootstrap_settings"]

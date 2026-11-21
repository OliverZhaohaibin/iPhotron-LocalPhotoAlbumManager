"""User-scoped resolution for installed People & Pets model directories."""

import os
import sys
from pathlib import Path


def installed_recognition_model_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "iPhoto" / "extensions" / "recognition" / "v1" / "models"


def previous_recognition_model_root(target: Path) -> Path:
    return target.parent / ".backup" / target.name


def installed_recognition_model_roots() -> tuple[Path, ...]:
    target = installed_recognition_model_root()
    # The previous directory remains usable across an interrupted promotion.
    return target, previous_recognition_model_root(target)

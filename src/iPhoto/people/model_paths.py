"""Resolution for existing and automatically acquired InsightFace models."""

import hashlib
import json
import os
import sys
from pathlib import Path

from iPhoto.utils.recognition_model_paths import (
    installed_recognition_model_roots,
    legacy_recognition_model_roots,
)

BUFFALO_S_FILES = ("det_500m.onnx", "w600k_mbf.onnx")
_FACE_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "recognition" / "recognition_package.json"


def bundled_face_model_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "extension" / "models"


def face_model_override_dir() -> Path | None:
    override = os.environ.get("IPHOTO_FACE_MODEL_DIR")
    return Path(override).expanduser() if override else None


def default_face_model_dir() -> Path:
    override = face_model_override_dir()
    if override is not None:
        return override
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "iPhoto" / "extensions" / "faces" / "v1" / "models"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "iPhoto" / "models"


def face_pack_available(model_dir: Path, pack: str) -> bool:
    pack_dir = model_dir / pack
    if pack != "buffalo_s":
        return any(pack_dir.glob("*.onnx"))
    try:
        files = (pack_dir / name for name in BUFFALO_S_FILES)
        return all(path.is_file() and path.stat().st_size > 0 for path in files)
    except OSError:
        return False


def resolve_face_model_dir(cache: Path) -> Path | None:
    """Return only verified official models; explicit custom roots bypass lookup."""
    for root in (*installed_recognition_model_roots(), *legacy_recognition_model_roots(), cache, bundled_face_model_dir()):
        if face_pack_available(root, "buffalo_s") and _face_pack_matches_release(root):
            return root
    return None


def _face_pack_matches_release(root: Path) -> bool:
    """Do not let a damaged v2 pack shadow intact v1/backup/local models.

    Explicit overrides bypass this resolver and retain custom model support.
    Verification runs only when a worker loads models, never during GUI binding.
    """
    try:
        files = json.loads(_FACE_CONTRACT_PATH.read_text(encoding="utf-8"))["files"]
        for name in BUFFALO_S_FILES:
            path = root / "buffalo_s" / name
            expected = files[f"buffalo_s/{name}"]
            if path.stat().st_size != expected["size"]:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False

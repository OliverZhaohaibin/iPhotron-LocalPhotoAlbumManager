"""Resolution for existing and automatically acquired InsightFace models."""

import os
import sys
from pathlib import Path

from iPhoto.utils.recognition_model_paths import installed_recognition_model_roots

BUFFALO_S_FILES = ("det_500m.onnx", "w600k_mbf.onnx")


def bundled_face_model_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "extension" / "models"


def default_face_model_dir() -> Path:
    override = os.environ.get("IPHOTO_FACE_MODEL_DIR")
    if override:
        return Path(override).expanduser()
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


def resolve_face_model_dir(cache: Path) -> Path:
    if os.environ.get("IPHOTO_FACE_MODEL_DIR"):
        return cache
    for root in (*installed_recognition_model_roots(), cache, bundled_face_model_dir()):
        if face_pack_available(root, "buffalo_s"):
            return root
    return cache

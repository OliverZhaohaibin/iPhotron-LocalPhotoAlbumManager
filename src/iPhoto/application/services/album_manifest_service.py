"""Application-level album manifest helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import ALBUM_MANIFEST_NAMES
from ...errors import AlbumNotFoundError, IPhotoError
from ...schemas import validate_album
from ...utils.jsonio import read_json, write_json
from ...utils.logging import logger
from ...utils.pathutils import ensure_work_dir


@dataclass(slots=True)
class ManifestAlbum:
    """Album manifest value used by production runtime and GUI adapters."""

    root: Path
    manifest: dict[str, Any]

    @classmethod
    def open(
        cls,
        root: Path,
    ) -> "ManifestAlbum":
        normalized = Path(root).expanduser().resolve()
        return cls(normalized, _load_manifest(normalized))

    def save(self) -> Path:
        _save_manifest(self.root, self.manifest)
        return self.root

    def set_cover(self, rel: str) -> None:
        self.manifest["cover"] = rel

    def add_featured(self, ref: str) -> None:
        featured = self.manifest.setdefault("featured", [])
        if ref not in featured:
            featured.append(ref)

    def remove_featured(self, ref: str) -> None:
        featured = self.manifest.setdefault("featured", [])
        self.manifest["featured"] = [item for item in featured if item != ref]


Album = ManifestAlbum

__all__ = ["Album", "ManifestAlbum"]


def _load_manifest(root: Path) -> dict[str, Any]:
    if not root.exists():
        raise AlbumNotFoundError(f"Album directory does not exist: {root}")

    ensure_work_dir(root)
    manifest_path = _find_manifest(root)
    changed = False
    if manifest_path is None:
        manifest_path = root / ALBUM_MANIFEST_NAMES[0]
        manifest = _default_manifest(root)
        changed = True
    else:
        try:
            manifest = read_json(manifest_path)
        except IPhotoError as exc:
            logger.error("Failed to read manifest %s: %s", manifest_path, exc)
            manifest = _default_manifest(root)
            changed = True

    changed = _normalize_manifest(root, manifest) or changed
    if changed:
        _write_manifest(root, manifest_path, manifest)
    return dict(manifest)


def _save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    payload = dict(manifest)
    _normalize_manifest(root, payload)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload.setdefault("created", now)
    payload["modified"] = now
    validate_album(payload)
    _write_manifest(
        root,
        _find_manifest(root) or (root / ALBUM_MANIFEST_NAMES[0]),
        payload,
    )


def _find_manifest(root: Path) -> Path | None:
    for name in ALBUM_MANIFEST_NAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _default_manifest(root: Path) -> dict[str, Any]:
    return {
        "schema": "iPhoto/album@1",
        "id": str(uuid.uuid4()),
        "title": root.name,
        "filters": {},
    }


def _normalize_manifest(root: Path, manifest: dict[str, Any]) -> bool:
    changed = False
    if manifest.get("schema") != "iPhoto/album@1":
        manifest["schema"] = "iPhoto/album@1"
        changed = True
    title = manifest.get("title")
    if not isinstance(title, str) or not title or title != root.name:
        manifest["title"] = root.name
        changed = True
    manifest_id = manifest.get("id")
    if not isinstance(manifest_id, str) or not manifest_id:
        manifest["id"] = str(uuid.uuid4())
        changed = True
    if not isinstance(manifest.get("filters"), dict):
        manifest["filters"] = {}
        changed = True
    try:
        validate_album(manifest)
    except IPhotoError as exc:
        logger.error(
            "Manifest for %s failed validation (%s); regenerating with defaults.",
            root,
            exc,
        )
        manifest.clear()
        manifest.update(_default_manifest(root))
        validate_album(manifest)
        changed = True
    return changed


def _write_manifest(root: Path, manifest_path: Path, manifest: dict[str, Any]) -> None:
    backup_dir = ensure_work_dir(root) / "manifest.bak"
    write_json(manifest_path, manifest, backup_dir=backup_dir)

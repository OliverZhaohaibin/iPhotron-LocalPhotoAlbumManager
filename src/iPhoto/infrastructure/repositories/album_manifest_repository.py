"""Infrastructure adapter for album manifest persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...application.ports import AlbumRepositoryPort
from ...cache.lock import FileLock
from ...config import ALBUM_MANIFEST_NAMES
from ...errors import AlbumNotFoundError, IPhotoError
from ...schemas import validate_album
from ...utils.jsonio import read_json, write_json
from ...utils.logging import logger
from ...utils.pathutils import ensure_work_dir


class AlbumManifestRepository(AlbumRepositoryPort):
    """Persist album manifests through the current on-disk compatibility format."""

    def exists(self, root: Path) -> bool:
        try:
            normalized = self._normalize_root(root)
        except AlbumNotFoundError:
            return False
        return self._find_manifest(normalized) is not None

    def load_manifest(self, root: Path) -> dict[str, Any]:
        normalized = self._normalize_root(root)
        ensure_work_dir(normalized)

        manifest_path = self._find_manifest(normalized)
        changed = False

        if manifest_path is None:
            manifest_path = normalized / ALBUM_MANIFEST_NAMES[0]
            manifest = self._default_manifest(normalized)
            changed = True
        else:
            try:
                manifest = read_json(manifest_path)
            except IPhotoError as exc:
                logger.error("Failed to read manifest %s: %s", manifest_path, exc)
                manifest = self._default_manifest(normalized)
                changed = True

        changed = self._normalize_manifest(normalized, manifest) or changed

        if changed:
            backup_dir = ensure_work_dir(normalized) / "manifest.bak"
            try:
                with FileLock(normalized, "manifest"):
                    write_json(manifest_path, manifest, backup_dir=backup_dir)
            except IPhotoError as exc:
                logger.warning(
                    "Failed to persist manifest updates for %s: %s",
                    normalized,
                    exc,
                )

        return dict(manifest)

    def save_manifest(self, root: Path, manifest: dict[str, Any]) -> None:
        normalized = self._normalize_root(root)
        payload = dict(manifest)
        self._normalize_manifest(normalized, payload)

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload.setdefault("created", now)
        payload["modified"] = now
        validate_album(payload)

        manifest_path = self._find_manifest(normalized) or (
            normalized / ALBUM_MANIFEST_NAMES[0]
        )
        backup_dir = ensure_work_dir(normalized) / "manifest.bak"
        with FileLock(normalized, "manifest"):
            write_json(manifest_path, payload, backup_dir=backup_dir)

    def _normalize_root(self, root: Path) -> Path:
        normalized = Path(root).expanduser().resolve()
        if not normalized.exists():
            raise AlbumNotFoundError(f"Album directory does not exist: {root}")
        return normalized

    @staticmethod
    def _find_manifest(root: Path) -> Path | None:
        for name in ALBUM_MANIFEST_NAMES:
            candidate = root / name
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _default_manifest(root: Path) -> dict[str, Any]:
        return {
            "schema": "iPhoto/album@1",
            "id": str(uuid.uuid4()),
            "title": root.name,
            "filters": {},
        }

    def _normalize_manifest(self, root: Path, manifest: dict[str, Any]) -> bool:
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

        filters = manifest.get("filters")
        if not isinstance(filters, dict):
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
            manifest.update(self._default_manifest(root))
            validate_album(manifest)
            changed = True

        return changed


__all__ = ["AlbumManifestRepository"]

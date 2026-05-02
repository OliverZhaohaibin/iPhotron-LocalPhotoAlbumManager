"""Filesystem adapter for ``.ipo`` edit sidecar persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import EditSidecarPort
from ...io import sidecar


class FileSystemEditSidecarRepository(EditSidecarPort):
    """Persist edit adjustments beside the original media file."""

    def sidecar_exists(self, path: Path) -> bool:
        candidate = sidecar.sidecar_path_for_asset(Path(path))
        try:
            return candidate.exists()
        except OSError:
            return False

    def read_adjustments(self, path: Path) -> dict[str, Any]:
        return sidecar.load_adjustments(Path(path))

    def write_adjustments(self, path: Path, adjustments: dict[str, Any]) -> None:
        sidecar.save_adjustments(Path(path), adjustments)


__all__ = ["FileSystemEditSidecarRepository"]

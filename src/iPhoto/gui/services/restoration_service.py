"""Service handling asset restoration from the deleted-items folder."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from ...cache.index_store import get_global_repository

if TYPE_CHECKING:
    from ...library.manager import LibraryManager
    from .asset_move_service import AssetMoveService


class RestorationService(QObject):
    """Return trashed assets to their original album locations."""

    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        move_service: "AssetMoveService",
        library_manager_getter: Callable[[], Optional["LibraryManager"]],
        model_provider_getter: Callable[[], Optional[Callable[[], Any]]],
        restore_prompt_getter: Callable[[], Optional[Callable[[str], bool]]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._move_service = move_service
        self._library_manager_getter = library_manager_getter
        self._model_provider_getter = model_provider_getter
        self._restore_prompt_getter = restore_prompt_getter

    def restore_assets(self, sources: Iterable[Path]) -> bool:
        """Return ``True`` when at least one trashed asset restore is scheduled."""

        library = self._library_manager_getter()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        library_root = library.root()
        if library_root is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        trash_root = library.deleted_directory()
        if trash_root is None:
            self.errorRaised.emit("Recently Deleted folder is unavailable.")
            return False

        def _normalize(path: Path) -> Path:
            try:
                return path.resolve()
            except OSError:
                return path

        normalized: List[Path] = []
        seen: Set[str] = set()
        for raw_path in sources:
            candidate = _normalize(Path(raw_path))
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if not candidate.exists():
                self.errorRaised.emit(f"File not found: {candidate}")
                continue
            try:
                candidate.relative_to(trash_root)
            except ValueError:
                self.errorRaised.emit(
                    f"Selection is outside Recently Deleted: {candidate}"
                )
                continue
            normalized.append(candidate)

        if not normalized:
            return False

        model_provider = self._model_provider_getter()
        model = model_provider() if model_provider else None

        for still_path in list(normalized):
            metadata = None
            if model and hasattr(model, "metadata_for_path"):
                metadata = model.metadata_for_path(still_path)

            if not metadata or not metadata.get("is_live"):
                continue
            motion_raw = metadata.get("live_motion_abs")
            if not motion_raw:
                continue
            motion_path = _normalize(Path(str(motion_raw)))
            motion_key = str(motion_path)
            if motion_key not in seen and motion_path.exists():
                seen.add(motion_key)
                try:
                    motion_path.relative_to(trash_root)
                except ValueError:
                    continue
                normalized.append(motion_path)

        try:
            trash_resolved = trash_root.resolve()
        except OSError:
            trash_resolved = trash_root
        try:
            library_resolved = library_root.resolve()
        except OSError:
            library_resolved = library_root
        try:
            album_path = trash_resolved.relative_to(library_resolved).as_posix()
        except ValueError:
            album_path = None
        store = get_global_repository(library_root)
        if album_path:
            index_rows = list(store.read_album_assets(album_path, include_subalbums=True))
        else:
            index_rows = list(store.read_all())
        row_lookup: Dict[str, dict] = {}
        for row in index_rows:
            if not isinstance(row, dict):
                continue
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                continue
            candidate_path = library_root / rel_value
            key = str(_normalize(candidate_path))
            row_lookup[key] = row

        grouped: Dict[Path, List[Path]] = defaultdict(list)
        for path in normalized:
            try:
                key = str(_normalize(path))
                row = row_lookup.get(key)
                if not row:
                    raise LookupError("metadata unavailable")
                destination_root = self._determine_restore_destination(
                    row=row,
                    library=library,
                    library_root=library_root,
                    filename=path.name,
                )
                if destination_root is None:
                    continue
                destination_root.mkdir(parents=True, exist_ok=True)
            except LookupError:
                self.errorRaised.emit(
                    f"Missing index metadata for {path.name}; skipping restore."
                )
                continue
            except OSError as exc:
                self.errorRaised.emit(
                    f"Could not prepare restore destination '{destination_root}': {exc}"
                )
                continue
            grouped[destination_root].append(path)

        if not grouped:
            return False

        scheduled_restore = False
        for destination_root, paths in grouped.items():
            self._move_service.move_assets(
                paths,
                destination_root,
                operation="restore",
            )
            scheduled_restore = True

        return scheduled_restore

    def _determine_restore_destination(
        self,
        *,
        row: dict,
        library: "LibraryManager",
        library_root: Path,
        filename: str,
    ) -> Optional[Path]:
        """Return the directory that should receive a restored asset."""

        def _offer_restore_to_root(
            skip_reason: str,
            decline_reason: str,
        ) -> Optional[Path]:
            prompt = self._restore_prompt_getter()
            if prompt is None:
                self.errorRaised.emit(skip_reason)
                return None
            if prompt(filename):
                return library_root
            self.errorRaised.emit(decline_reason)
            return None

        original_rel = row.get("original_rel_path")
        if isinstance(original_rel, str) and original_rel:
            candidate_path = library_root / original_rel
            try:
                candidate_path.relative_to(library_root)
            except ValueError:
                pass
            else:
                parent_dir = candidate_path.parent
                if parent_dir.exists():
                    return parent_dir

        album_id = row.get("original_album_id")
        subpath = row.get("original_album_subpath")
        if isinstance(album_id, str) and album_id and isinstance(subpath, str) and subpath:
            node = library.find_album_by_uuid(album_id)
            if node is not None:
                subpath_obj = Path(subpath)
                if subpath_obj.is_absolute() or any(part == ".." for part in subpath_obj.parts):
                    destination_root = node.path
                else:
                    destination_path = node.path / subpath_obj
                    try:
                        destination_path.relative_to(node.path)
                    except ValueError:
                        destination_root = node.path
                    else:
                        destination_root = destination_path.parent
                return destination_root

            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album for {filename} no longer exists; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because its original album is unavailable."
                ),
            )

        if isinstance(original_rel, str) and original_rel:
            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album metadata is unavailable for {filename}; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
                ),
            )
        return _offer_restore_to_root(
            skip_reason=(
                f"Original location is unknown for {filename}; skipping restore."
            ),
            decline_reason=(
                f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
            ),
        )


__all__ = ["RestorationService"]

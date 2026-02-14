"""Album CRUD operations and manifest management."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from ..config import (
    ALBUM_MANIFEST_NAMES,
    EXPORT_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
    WORK_DIR_NAME,
)
from ..errors import (
    AlbumDepthError,
    AlbumNameConflictError,
    AlbumOperationError,
)
from ..models.album import Album
from ..utils.jsonio import read_json
from .tree import AlbumNode

if TYPE_CHECKING:
    pass


class AlbumOperationsMixin:
    """Mixin providing album CRUD operations for LibraryManager."""

    def create_album(self, name: str) -> AlbumNode:
        root = self._require_root()
        target = self._validate_new_name(root, name)
        target.mkdir(parents=False, exist_ok=False)
        node = AlbumNode(target, 1, target.name, False)
        self.ensure_manifest(node)
        self._refresh_tree()
        return self._node_for_path(target)

    def create_subalbum(self, parent: AlbumNode, name: str) -> AlbumNode:
        if parent.level != 1:
            raise AlbumDepthError("Sub-albums can only be created under top-level albums.")
        root = self._require_root()
        if not parent.path.is_relative_to(root):
            parent_path = parent.path.resolve()
            if not str(parent_path).startswith(str(root)):
                raise AlbumOperationError("Parent album is outside the library root.")
        target = self._validate_new_name(parent.path, name)
        target.mkdir(parents=False, exist_ok=False)
        node = AlbumNode(target, 2, target.name, False)
        self.ensure_manifest(node)
        self._refresh_tree()
        return self._node_for_path(target)

    def rename_album(self, node: AlbumNode, new_name: str) -> None:
        parent = node.path.parent
        target = self._validate_new_name(parent, new_name)
        try:
            node.path.rename(target)
        except FileExistsError as exc:
            raise AlbumNameConflictError(f"An album named '{new_name}' already exists.") from exc
        except OSError as exc:  # pragma: no cover - defensive guard
            raise AlbumOperationError(str(exc)) from exc
        # ``Album.open`` now normalises and persists manifest updates so the
        # metadata stays aligned with the renamed directory immediately.
        Album.open(target)
        self._refresh_tree()

    def ensure_manifest(self, node: AlbumNode) -> Path:
        Album.open(node.path)
        marker = node.path / ".iphoto.album"
        if not marker.exists():
            marker.touch()
        manifest = self._find_manifest(node.path)
        if manifest is None:
            manifest = node.path / ALBUM_MANIFEST_NAMES[0]
        return manifest

    def find_album_by_uuid(self, album_id: str) -> Optional[AlbumNode]:
        """Return the library node whose manifest declares *album_id*.

        The lookup tolerates missing or unreadable manifests and merely skips
        those entries so the remaining albums keep their fast-path resolution.
        ``album_id`` comparisons are performed case-insensitively to avoid
        surprises when legacy manifests contain uppercase UUIDs.
        """

        if not album_id:
            return None
        normalized = album_id.strip()
        if not normalized:
            return None
        needle = normalized.casefold()

        # The library root is not included in ``self._nodes`` because the tree
        # structure focuses on first- and second-level albums.  However,
        # trashed assets can originate directly from the root (for example when
        # deleted via the "All Photos" aggregate view).  Those entries store
        # the root's UUID, so we need to compare against the root manifest
        # explicitly before scanning child nodes.
        root = self._root
        if root is not None:
            manifest_path = self._find_manifest(root)
            if manifest_path is not None:
                try:
                    data = read_json(manifest_path)
                except Exception as exc:  # pragma: no cover - defensive guard
                    # Surfacing the failure keeps the UI informed without
                    # breaking the fallback search that follows.
                    self.errorRaised.emit(f"Failed to read root manifest: {exc}")
                else:
                    candidate = data.get("id")
                    if isinstance(candidate, str) and candidate.strip().casefold() == needle:
                        try:
                            album = Album.open(root)
                        except Exception as exc:  # pragma: no cover - defensive guard
                            # If opening the album fails we cannot build a
                            # representative node, so emit the error and allow
                            # the regular search to continue.
                            self.errorRaised.emit(f"Failed to open root album: {exc}")
                        else:
                            title = album.manifest.get("title")
                            if not isinstance(title, str) or not title:
                                title = root.name
                            return AlbumNode(root, level=0, title=title, has_manifest=True)
        for path, node in self._nodes.items():
            manifest_path = self._find_manifest(path)
            if manifest_path is None:
                continue
            try:
                data = read_json(manifest_path)
            except Exception as exc:  # pragma: no cover - defensive guard
                self.errorRaised.emit(str(exc))
                continue
            candidate = data.get("id")
            if isinstance(candidate, str) and candidate.strip().casefold() == needle:
                return node
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _validate_new_name(self, parent: Path, name: str) -> Path:
        candidate = name.strip()
        if not candidate:
            raise AlbumOperationError("Album name cannot be empty.")
        if Path(candidate).name != candidate:
            raise AlbumOperationError("Album name must not contain path separators.")
        target = parent / candidate
        if target.exists():
            raise AlbumNameConflictError(f"An album named '{candidate}' already exists.")
        return target

    def _find_manifest(self, path: Path) -> Path | None:
        for name in ALBUM_MANIFEST_NAMES:
            candidate = path / name
            if candidate.exists():
                return candidate
        return None

    def _describe_album(self, path: Path) -> tuple[str, bool]:
        manifest = self._find_manifest(path)
        if manifest:
            try:
                data = read_json(manifest)
            except Exception as exc:  # pragma: no cover - invalid JSON
                self.errorRaised.emit(str(exc))
            else:
                title = str(data.get("title") or path.name)
                return title, True
            return path.name, True
        marker = path / ".iphoto.album"
        if marker.exists():
            return path.name, True
        return path.name, False

    def _build_node(self, path: Path, *, level: int) -> AlbumNode:
        title, has_manifest = self._describe_album(path)
        return AlbumNode(path, level, title, has_manifest)

    def _node_for_path(self, path: Path) -> AlbumNode:
        node = self._nodes.get(path)
        if node is not None:
            return node
        resolved = path.resolve()
        node = self._nodes.get(resolved)
        if node is not None:
            return node
        raise AlbumOperationError(f"Album node not found for path: {path}")

    def _iter_album_dirs(self, root: Path) -> Iterable[Path]:
        try:
            entries = list(root.iterdir())
        except OSError as exc:  # pragma: no cover - filesystem failure
            self.errorRaised.emit(str(exc))
            return []
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name == WORK_DIR_NAME:
                continue
            if entry.name == RECENTLY_DELETED_DIR_NAME:
                # The trash folder should stay hidden from the regular album list
                # so that it only appears through the dedicated "Recently Deleted"
                # entry in the sidebar.
                continue
            if entry.name == EXPORT_DIR_NAME:
                continue
            yield entry

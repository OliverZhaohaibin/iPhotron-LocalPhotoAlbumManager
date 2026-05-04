"""Library-scoped album metadata commands for one active session."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..application.ports import AlbumRepositoryPort, LibraryStateRepositoryPort
from ..infrastructure.repositories.album_manifest_repository import (
    AlbumManifestRepository,
)
from ..infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)
from ..utils.pathutils import is_descendant_path


@dataclass(frozen=True)
class AlbumFeaturedToggleResult:
    """Outcome of toggling one featured entry."""

    is_featured: bool
    errors: list[str] = field(default_factory=list)


class LibraryAlbumMetadataService:
    """Own durable album metadata mutations for one library session."""

    def __init__(
        self,
        library_root: Path | None,
        *,
        album_repository: AlbumRepositoryPort | None = None,
        state_repository: LibraryStateRepositoryPort | None = None,
        state_repository_factory: Callable[[Path], LibraryStateRepositoryPort] | None = None,
    ) -> None:
        self.library_root = (
            self._normalize_path(Path(library_root)) if library_root is not None else None
        )
        self._album_repository = album_repository or AlbumManifestRepository()
        self._state_repository = state_repository
        self._state_repository_factory = (
            state_repository_factory or IndexStoreLibraryStateRepository
        )

    def set_cover(self, album_root: Path, rel: str) -> None:
        if not rel:
            return
        root = self._normalize_path(album_root)
        manifest = self._album_repository.load_manifest(root)
        manifest["cover"] = rel
        self._album_repository.save_manifest(root, manifest)

    def toggle_featured(
        self,
        album_root: Path,
        ref: str,
    ) -> AlbumFeaturedToggleResult:
        """Toggle *ref* for *album_root* and return the resulting featured state."""

        if not ref:
            return AlbumFeaturedToggleResult(False)

        root = self._normalize_path(album_root)
        manifest = self._album_repository.load_manifest(root)
        featured = manifest.setdefault("featured", [])
        was_featured = ref in featured
        desired_state = not was_featured

        try:
            absolute_asset = (root / ref).resolve()
        except OSError:
            return AlbumFeaturedToggleResult(was_featured)

        targets: list[tuple[Path, str]] = [(root, ref)]
        if self.library_root is not None:
            known_roots = {self.library_root, root}
            containing_roots = self._find_all_containing_albums(
                self.library_root,
                absolute_asset,
                known_roots,
            )
            for candidate_root in containing_roots:
                if self._paths_equal(candidate_root, root):
                    continue
                try:
                    rel = absolute_asset.relative_to(candidate_root).as_posix()
                except ValueError:
                    continue
                targets.append((candidate_root, rel))

        deduped_targets: dict[Path, str] = {}
        for target_root, target_rel in targets:
            deduped_targets[target_root] = target_rel

        primary_success = False
        errors: list[str] = []
        for target_root, target_rel in deduped_targets.items():
            target_manifest = self._album_repository.load_manifest(target_root)
            target_featured = list(target_manifest.get("featured", []))
            if desired_state:
                if target_rel not in target_featured:
                    target_featured.append(target_rel)
            else:
                target_featured = [item for item in target_featured if item != target_rel]
            target_manifest["featured"] = target_featured

            try:
                self._album_repository.save_manifest(target_root, target_manifest)
            except Exception as exc:
                errors.append(str(exc))
                continue

            self._sync_favorite_state(
                absolute_asset=absolute_asset,
                album_root=target_root,
                rel=target_rel,
                desired_state=desired_state,
            )
            if self._paths_equal(target_root, root):
                primary_success = True

        return AlbumFeaturedToggleResult(
            desired_state if primary_success else was_featured,
            errors=errors,
        )

    def ensure_featured_entries(
        self,
        album_root: Path,
        imported: Sequence[Path],
    ) -> None:
        if not imported:
            return

        root = self._normalize_path(album_root)
        manifest = self._album_repository.load_manifest(root)
        featured = list(manifest.get("featured", []))
        updated = False
        for path in imported:
            try:
                rel = Path(path).relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in featured:
                continue
            featured.append(rel)
            updated = True

        if not updated:
            return

        manifest["featured"] = featured
        self._album_repository.save_manifest(root, manifest)

    def _find_all_containing_albums(
        self,
        library_root: Path,
        asset_path: Path,
        known_roots: set[Path] | None = None,
    ) -> list[Path]:
        found: list[Path] = []
        candidate = asset_path.parent
        known = known_roots or set()

        while True:
            if candidate != library_root and not is_descendant_path(candidate, library_root):
                break

            is_album = candidate in known or self._album_repository.exists(candidate)
            if is_album:
                found.append(candidate)

            if self._paths_equal(candidate, library_root):
                break

            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent

        return found

    def _sync_favorite_state(
        self,
        *,
        absolute_asset: Path,
        album_root: Path,
        rel: str,
        desired_state: bool,
    ) -> None:
        if self.library_root is not None:
            try:
                library_rel = absolute_asset.relative_to(self.library_root).as_posix()
            except (ValueError, OSError):
                library_rel = None
            if library_rel is not None:
                self._state_repository_for(self.library_root).set_favorite_status(
                    library_rel,
                    desired_state,
                )
                return

        self._state_repository_for(album_root).set_favorite_status(rel, desired_state)

    def _state_repository_for(self, root: Path) -> LibraryStateRepositoryPort:
        normalized = self._normalize_path(root)
        if (
            self._state_repository is not None
            and self.library_root is not None
            and self._paths_equal(normalized, self.library_root)
        ):
            return self._state_repository
        return self._state_repository_factory(normalized)

    @staticmethod
    def _normalize_path(path: Path) -> Path:
        try:
            return Path(path).expanduser().resolve()
        except OSError:
            return Path(path).expanduser()

    @staticmethod
    def _paths_equal(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except OSError:
            return Path(left) == Path(right)

__all__ = ["AlbumFeaturedToggleResult", "LibraryAlbumMetadataService"]

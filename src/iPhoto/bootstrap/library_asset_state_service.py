"""Library-scoped durable asset state commands."""

from __future__ import annotations

from pathlib import Path

from ..application.ports import AssetFavoriteQueryPort, LibraryStateRepositoryPort


class LibraryAssetStateService:
    """Own asset-state mutations that should flow through session boundaries."""

    def __init__(
        self,
        library_root: Path,
        *,
        state_repository: LibraryStateRepositoryPort,
        favorite_query: AssetFavoriteQueryPort,
    ) -> None:
        self.library_root = Path(library_root)
        self._state_repository = state_repository
        self._favorite_query = favorite_query

    def toggle_favorite(self, path: Path) -> bool:
        """Toggle favorite state for *path* through the session state boundary."""

        rel = self._library_relative_path(path)
        current_state = self._favorite_query.favorite_status_for_path(path)
        if current_state is None:
            return False
        next_state = not current_state
        self._state_repository.set_favorite_status(rel, next_state)
        return next_state

    def _library_relative_path(self, path: Path) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            return candidate.as_posix()
        try:
            return candidate.resolve().relative_to(self.library_root.resolve()).as_posix()
        except (OSError, ValueError):
            try:
                return candidate.relative_to(self.library_root).as_posix()
            except ValueError:
                return candidate.name

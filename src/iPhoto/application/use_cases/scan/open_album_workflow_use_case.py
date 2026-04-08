"""Open album workflow use case.

Encapsulates the "open an album, hydrate from the index or auto-scan if empty"
workflow that was previously inlined inside ``app.py``.

Extracting this logic here:
- Makes the workflow testable without the compatibility shim.
- Keeps ``app.py`` as a pure thin forwarding layer (Phase 3 goal).
- Allows future callers to bypass the compatibility shim entirely.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from ....errors import IndexCorruptedError, ManifestInvalidError
from ....models.album import Album
from ....utils.logging import get_logger

LOGGER = get_logger()


def _is_recoverable(exc: Exception) -> bool:
    return isinstance(exc, (sqlite3.Error, IndexCorruptedError, ManifestInvalidError))


class OpenAlbumWorkflowUseCase:
    """Orchestrate the full album-open sequence.

    This is the authoritative implementation of the legacy ``open_album``
    workflow.  ``app.py`` is a thin forwarding shim that delegates here.

    Parameters
    ----------
    library_root_getter:
        Callable that returns the optional library root path at call time.
    repository_factory:
        Optional callable with the same signature as ``get_global_repository``.
        When provided, it is used instead of the default import so that callers
        (e.g. ``app.py``) can inject a patchable module-level reference.
    ensure_links_fn:
        Optional callable with the same signature as ``ensure_links``.  When
        provided, it is used instead of the default import for the same reason.
    """

    def __init__(
        self,
        library_root_getter: Callable[[], Path | None] = lambda: None,
        repository_factory: Callable[[Path], object] | None = None,
        ensure_links_fn: Callable[..., None] | None = None,
    ) -> None:
        self._library_root_getter = library_root_getter
        self._repository_factory = repository_factory
        self._ensure_links_fn = ensure_links_fn

    def execute(
        self,
        root: Path,
        *,
        autoscan: bool = True,
        library_root: Path | None = None,
        hydrate_index: bool = True,
    ) -> Album:
        """Open *root* and return the populated :class:`~iPhoto.models.album.Album`.

        Parameters
        ----------
        root:
            Path to the album directory.
        autoscan:
            When ``True`` and the index is empty, trigger an automatic scan.
        library_root:
            Optional library root that scopes the global index.
        hydrate_index:
            When ``True`` (default) read all index rows and hydrate the album.
            When ``False`` only count the existing rows and auto-scan if needed.
        """

        from ....application.policies.album_path_policy import AlbumPathPolicy
        from ....cache.index_store import get_global_repository as _default_repo
        from ....index_sync_service import ensure_links as _default_ensure_links
        from ....path_normalizer import compute_album_path as _compute_album_path

        _get_repo = self._repository_factory if self._repository_factory is not None else _default_repo
        _ensure_links = self._ensure_links_fn if self._ensure_links_fn is not None else _default_ensure_links

        album = Album.open(root)
        effective_library_root = library_root or self._library_root_getter()
        db_root = effective_library_root if effective_library_root else root
        store = _get_repo(db_root)
        album_path = _compute_album_path(root, effective_library_root)
        policy = AlbumPathPolicy()

        rows: list[dict] | None = None

        if hydrate_index:
            if album_path:
                rows = list(store.read_album_assets(album_path, include_subalbums=True))
            else:
                rows = list(store.read_all())
        else:
            try:
                existing_count = store.count(
                    filter_hidden=True,
                    album_path=album_path,
                    include_subalbums=True,
                )
            except Exception as exc:
                if not _is_recoverable(exc):
                    raise
                LOGGER.warning(
                    "Index count failed for %s [%s]; assuming empty index: %s",
                    root,
                    type(exc).__name__,
                    exc,
                )
                existing_count = 0

            if existing_count == 0 and autoscan:
                include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
                exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)
                from ....io.scanner_adapter import scan_album

                rows = list(scan_album(root, include, exclude))
                if effective_library_root and album_path:
                    rows = policy.prefix_rows(rows, album_path)
                store.write_rows(rows)
            elif existing_count == 0:
                rows = []

        if rows is not None:
            if album_path:
                album_rows = policy.strip_album_prefix(rows, album_path)
                _ensure_links(root, album_rows, library_root=effective_library_root)
            else:
                _ensure_links(root, rows, library_root=effective_library_root)

        if not effective_library_root:
            try:
                store.sync_favorites(album.manifest.get("featured", []))
            except Exception as exc:
                if not _is_recoverable(exc):
                    raise
                LOGGER.warning(
                    "sync_favorites failed for %s [%s]: %s",
                    root,
                    type(exc).__name__,
                    exc,
                )

        return album


__all__ = ["OpenAlbumWorkflowUseCase"]

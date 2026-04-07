"""Scan-specific-files use case.

Generates index rows for a given set of files and merges them into the
persistent index store for the owning album.

This use case replaces the inline logic that was previously embedded in the
``scan_specific_files`` compatibility shim in ``app.py``.  It contains **no
Qt dependencies** and can be executed synchronously from any context.
"""

from __future__ import annotations

from pathlib import Path

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".heic", ".heif", ".heifs", ".heicf", ".jpg", ".jpeg", ".png"}
)
_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mov", ".mp4", ".m4v", ".qt"})


class ScanSpecificFilesUseCase:
    """Scan a list of specific files and append their index rows.

    Parameters
    ----------
    root:
        Album root directory that owns the files.
    files:
        Absolute paths to the individual files to scan.
    library_root:
        Optional library root used to compute cross-album index paths and to
        locate the correct index store.

    Usage
    -----
    ::

        ScanSpecificFilesUseCase().execute(root, files, library_root=lib)
    """

    def execute(
        self,
        root: Path,
        files: list[Path],
        library_root: Path | None = None,
    ) -> None:
        """Build index rows for *files* and append them to the index store.

        The method classifies each file as an image or video based on its
        extension, runs the appropriate scanner, applies the album-path prefix
        policy, and persists the rows in the global index for *library_root*
        (or *root* when no library root is provided).
        """

        from ...policies.album_path_policy import AlbumPathPolicy
        from ....cache.index_store import get_global_repository
        from ....io.scanner_adapter import process_media_paths
        from ....path_normalizer import compute_album_path as _compute_album_path

        image_paths: list[Path] = []
        video_paths: list[Path] = []

        for f in files:
            suffix = f.suffix.lower()
            if suffix in _IMAGE_EXTENSIONS:
                image_paths.append(f)
            elif suffix in _VIDEO_EXTENSIONS:
                video_paths.append(f)

        rows = list(process_media_paths(root, image_paths, video_paths))

        album_path = _compute_album_path(root, library_root)
        if album_path:
            rows = AlbumPathPolicy().prefix_rows(rows, album_path)

        db_root = library_root if library_root else root
        store = get_global_repository(db_root)
        store.append_rows(rows)


__all__ = ["ScanSpecificFilesUseCase"]

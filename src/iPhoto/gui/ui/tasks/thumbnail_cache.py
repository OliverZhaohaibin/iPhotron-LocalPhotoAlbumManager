"""Thumbnail disk-cache utilities: path generation, validation, and IO."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ....utils.pathutils import ensure_work_dir

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheCleanupTarget:
    """Cache selector with an optional generation-aware unlink operation."""

    abs_path: Path
    size: QSize
    keep_stamp: int | None = None
    unlink_candidate: Callable[[Path], None] | None = None


def _cache_digest(abs_path: Path) -> str:
    """Return the stable digest used by every cache version of an asset."""

    path_str = str(abs_path.resolve())
    return hashlib.blake2b(path_str.encode("utf-8"), digest_size=20).hexdigest()


def safe_unlink(path: Path) -> None:
    """
    Safely delete a file, handling permission errors gracefully.

    Attempts to delete the file at the given path. If a PermissionError occurs,
    the file is renamed with a ".stale" suffix instead. Other OSError exceptions
    (such as the file not existing or being inaccessible) are ignored.

    Parameters:
        path (Path): The path to the file to be deleted.
    """
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        try:
            path.rename(path.with_suffix(path.suffix + ".stale"))
        except OSError:
            # Ignore errors when renaming; file may be locked or already deleted.
            pass
    except OSError:
        # Ignore errors when unlinking; file may not exist or be inaccessible.
        pass


def stat_mtime_ns(stat_result: os.stat_result) -> int:
    stamp = getattr(stat_result, "st_mtime_ns", None)
    if stamp is None:
        stamp = int(stat_result.st_mtime * 1_000_000_000)
    return int(stamp)


def generate_cache_path(library_root: Path, abs_path: Path, size: QSize, stamp: int) -> Path:
    """
    Generate the file path for a cached thumbnail image.

    Args:
        library_root (Path): The root directory of the Basic Library.
        abs_path (Path): The absolute path of the media file.
        size (QSize): The desired size of the thumbnail.
        stamp (int): A timestamp or version identifier for cache invalidation.

    Returns:
        Path: The path to the cache file for the thumbnail image.
    """
    # Use absolute path for global uniqueness
    digest = _cache_digest(abs_path)
    filename = f"{digest}_{stamp}_{size.width()}x{size.height()}.png"
    return ensure_work_dir(library_root) / "thumbs" / filename


def remove_cache_versions(
    library_root: Path,
    abs_path: Path,
    size: QSize,
    *,
    keep_stamp: int | None = None,
) -> None:
    """Delete stamp-addressed cache files for one asset and thumbnail size."""

    remove_cache_versions_many(
        library_root,
        [CacheCleanupTarget(abs_path, size, keep_stamp)],
    )


def remove_cache_versions_many(
    library_root: Path,
    targets: Iterable[CacheCleanupTarget],
) -> None:
    """Delete cache versions for many assets with one directory scan.

    A target with a ``None`` stamp removes every matching version; otherwise
    that exact version is retained.  Its optional unlink callback can enforce
    generation ownership immediately before each file deletion.
    Grouping selectors by digest keeps large removal batches near ``O(R + C)``
    instead of scanning the thumbnail directory once per removed asset.
    """

    selectors: dict[
        str,
        dict[str, tuple[set[str] | None, Callable[[Path], None] | None]],
    ] = {}
    for target in targets:
        try:
            digest = _cache_digest(target.abs_path)
        except OSError:
            continue
        suffix = f"_{target.size.width()}x{target.size.height()}.png"
        digest_selectors = selectors.setdefault(digest, {})
        if target.keep_stamp is None:
            digest_selectors[suffix] = (None, target.unlink_candidate)
            continue
        keep_name = f"{digest}_{target.keep_stamp}{suffix}"
        existing_selector = digest_selectors.get(suffix)
        if existing_selector is None:
            digest_selectors[suffix] = ({keep_name}, target.unlink_candidate)
            continue
        existing_keeps, existing_unlink = existing_selector
        if existing_keeps is not None:
            existing_keeps.add(keep_name)
            digest_selectors[suffix] = (
                existing_keeps,
                target.unlink_candidate or existing_unlink,
            )

    if not selectors:
        return

    try:
        cache_dir = ensure_work_dir(library_root) / "thumbs"
        entries = os.scandir(cache_dir)
    except OSError:
        return

    try:
        with entries:
            for entry in entries:
                name = entry.name
                digest_selectors = selectors.get(name[:40])
                if digest_selectors is None:
                    continue
                for suffix, selector in digest_selectors.items():
                    if not name.endswith(suffix):
                        continue
                    keep_names, unlink_candidate = selector
                    if keep_names is None or name not in keep_names:
                        candidate = Path(entry.path)
                        if unlink_candidate is None:
                            safe_unlink(candidate)
                        else:
                            unlink_candidate(candidate)
                    break
    except OSError:
        return


def write_cache(canvas: QImage, path: Path) -> bool:  # pragma: no cover - worker helper
    """Write *canvas* atomically using a writer-unique adjacent temp file."""

    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_name)
        os.close(file_descriptor)
        if canvas.save(str(tmp_path), "PNG"):
            try:
                tmp_path.replace(path)
                tmp_path = None
                return True
            except OSError:
                pass
    except Exception:
        pass
    finally:
        if tmp_path is not None:
            safe_unlink(tmp_path)
    return False

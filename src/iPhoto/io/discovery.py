"""Shared file discovery helpers for scan workflows."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from threading import Event
from typing import Callable, Iterator, Sequence

from .. import _native
from ..utils.pathutils import expand_globs, should_include_rel_expanded

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredPath:
    path: Path
    rel_path: str
    media_kind: int


def discover_with_callback(
    root: Path,
    *,
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    supported_extensions: Sequence[str] = (),
    skip_dir_names: Sequence[str] = (),
    skip_hidden_dirs: bool = False,
    stop_event: Event | None = None,
    on_found: Callable[[Path, str], bool | None],
) -> int:
    """Discover files under *root* and invoke *on_found* for each match."""

    total = 0
    for chunk in iter_discovered_chunks(
        root,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        supported_extensions=supported_extensions,
        skip_dir_names=skip_dir_names,
        skip_hidden_dirs=skip_hidden_dirs,
        stop_event=stop_event,
    ):
        for item in chunk:
            total += 1
            if on_found(item.path, item.rel_path):
                return total
    return total


def iter_discovered_chunks(
    root: Path,
    *,
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    supported_extensions: Sequence[str] = (),
    skip_dir_names: Sequence[str] = (),
    skip_hidden_dirs: bool = False,
    stop_event: Event | None = None,
    max_items: int = _native.DISCOVERY_CHUNK_ITEMS,
    max_bytes: int = _native.DISCOVERY_CHUNK_BYTES,
) -> Iterator[tuple[DiscoveredPath, ...]]:
    """Yield matching files under *root* in discovery chunks."""

    expanded_include = expand_globs(include_globs)
    expanded_exclude = expand_globs(exclude_globs)

    native_iter = _native.iter_discovery_chunks(
        root,
        include_globs=expanded_include,
        exclude_globs=expanded_exclude,
        supported_extensions=tuple(supported_extensions),
        skip_dir_names=tuple(skip_dir_names),
        skip_hidden_dirs=skip_hidden_dirs,
        max_items=max_items,
        max_bytes=max_bytes,
    )
    if native_iter is not None:
        for native_chunk in native_iter:
            if stop_event is not None and stop_event.is_set():
                break
            yield tuple(
                DiscoveredPath(
                    path=Path(item.abs_path),
                    rel_path=item.rel_path,
                    media_kind=item.media_kind,
                )
                for item in native_chunk
            )
        return

    yield from _iter_discovered_chunks_fallback(
        root,
        include_globs=expanded_include,
        exclude_globs=expanded_exclude,
        supported_extensions=supported_extensions,
        skip_dir_names=skip_dir_names,
        skip_hidden_dirs=skip_hidden_dirs,
        stop_event=stop_event,
        max_items=max_items,
        max_bytes=max_bytes,
    )


def iter_discovered_files(
    root: Path,
    *,
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    supported_extensions: Sequence[str] = (),
    skip_dir_names: Sequence[str] = (),
    skip_hidden_dirs: bool = False,
    stop_event: Event | None = None,
) -> Iterator[Path]:
    """Yield matching files under *root* using the shared discovery backend."""

    for chunk in iter_discovered_chunks(
        root,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        supported_extensions=supported_extensions,
        skip_dir_names=skip_dir_names,
        skip_hidden_dirs=skip_hidden_dirs,
        stop_event=stop_event,
    ):
        for item in chunk:
            yield item.path


def _discover_with_callback_fallback(
    root: Path,
    *,
    include_globs: Sequence[str],
    exclude_globs: Sequence[str],
    supported_extensions: Sequence[str],
    skip_dir_names: Sequence[str],
    skip_hidden_dirs: bool,
    stop_event: Event | None,
    on_found: Callable[[Path, str], bool | None],
) -> int:
    total = 0
    for chunk in _iter_discovered_chunks_fallback(
        root,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        supported_extensions=supported_extensions,
        skip_dir_names=skip_dir_names,
        skip_hidden_dirs=skip_hidden_dirs,
        stop_event=stop_event,
        max_items=_native.DISCOVERY_CHUNK_ITEMS,
        max_bytes=_native.DISCOVERY_CHUNK_BYTES,
    ):
        for item in chunk:
            total += 1
            if on_found(item.path, item.rel_path):
                return total
    return total


def _iter_discovered_chunks_fallback(
    root: Path,
    *,
    include_globs: Sequence[str],
    exclude_globs: Sequence[str],
    supported_extensions: Sequence[str],
    skip_dir_names: Sequence[str],
    skip_hidden_dirs: bool,
    stop_event: Event | None,
    max_items: int,
    max_bytes: int,
) -> Iterator[tuple[DiscoveredPath, ...]]:
    supported = frozenset(ext.lower() for ext in supported_extensions)
    skip_names = frozenset(skip_dir_names)
    discovered: list[DiscoveredPath] = []

    def _walk(current: Path) -> bool:
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if stop_event is not None and stop_event.is_set():
                        return True

                    name = entry.name
                    if entry.is_dir(follow_symlinks=False):
                        if name in skip_names:
                            continue
                        if skip_hidden_dirs and name.startswith("."):
                            continue
                        if _walk(Path(entry.path)):
                            return True
                        continue

                    if not entry.is_file(follow_symlinks=False):
                        continue

                    suffix = Path(name).suffix.lower()
                    if supported and suffix not in supported:
                        continue

                    path = Path(entry.path)
                    rel_path = path.relative_to(root).as_posix()
                    if include_globs or exclude_globs:
                        if not should_include_rel_expanded(
                            rel_path,
                            include_globs,
                            exclude_globs,
                        ):
                            continue

                    discovered.append(
                        DiscoveredPath(
                            path=path,
                            rel_path=rel_path,
                            media_kind=_media_kind_from_suffix(suffix),
                        )
                    )
        except PermissionError:
            LOGGER.warning("Permission denied: %s", current)
        return False

    _walk(root)

    index = 0
    while index < len(discovered):
        if stop_event is not None and stop_event.is_set():
            break
        chunk: list[DiscoveredPath] = []
        chunk_bytes = 0
        while index < len(discovered) and len(chunk) < max_items:
            item = discovered[index]
            item_bytes = len(str(item.path)) + len(item.rel_path)
            if chunk and max_bytes > 0 and chunk_bytes + item_bytes > max_bytes:
                break
            chunk.append(item)
            chunk_bytes += item_bytes
            index += 1
        if chunk:
            yield tuple(chunk)


def _media_kind_from_suffix(suffix: str) -> int:
    lowered = suffix.lower()
    if lowered in {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".heifs",
        ".heicf",
        ".webp",
    }:
        return _native.MEDIA_HINT_IMAGE
    if lowered in {".mov", ".mp4", ".m4v", ".qt", ".avi", ".mkv"}:
        return _native.MEDIA_HINT_VIDEO
    return _native.MEDIA_HINT_UNKNOWN


__all__ = [
    "DiscoveredPath",
    "discover_with_callback",
    "iter_discovered_chunks",
    "iter_discovered_files",
]

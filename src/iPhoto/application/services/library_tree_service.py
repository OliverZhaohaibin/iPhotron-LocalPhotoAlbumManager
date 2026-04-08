"""Library tree service.

Owns tree-construction, album-node building, refresh logic, and list APIs
that were previously implemented directly in ``LibraryManager`` and
``AlbumOperationsMixin``.

``LibraryManager`` delegates tree operations here while maintaining its
existing public API for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List

from ...config import (
    EXPORT_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
    WORK_DIR_NAME,
)
from ...utils.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from ...library.tree import AlbumNode

LOGGER = get_logger()


class LibraryTreeService:
    """Build and refresh the album tree for a library root."""

    def build_tree(
        self,
        root: Path,
        iter_album_dirs_fn,  # callable(root) -> Iterable[Path]
        build_node_fn,       # callable(path, level) -> AlbumNode
    ) -> tuple[List[AlbumNode], Dict[Path, List[AlbumNode]], Dict[Path, AlbumNode]]:
        """Build the sorted album tree for *root*.

        Returns a tuple of:
        - ``albums``: top-level :class:`AlbumNode` list (sorted case-insensitively).
        - ``children``: mapping from album path → sorted child node list.
        - ``nodes``: flat ``path → node`` mapping for fast lookup.
        """
        albums: List[AlbumNode] = []
        children: Dict[Path, List[AlbumNode]] = {}
        nodes: Dict[Path, AlbumNode] = {}

        for album_dir in iter_album_dirs_fn(root):
            node = build_node_fn(album_dir, level=1)
            albums.append(node)
            nodes[album_dir] = node
            child_nodes = [
                build_node_fn(child, level=2)
                for child in iter_album_dirs_fn(album_dir)
            ]
            for child in child_nodes:
                nodes[child.path] = child
            children[album_dir] = child_nodes

        sorted_albums = sorted(albums, key=lambda n: n.title.casefold())
        sorted_children = {
            parent: sorted(kids, key=lambda n: n.title.casefold())
            for parent, kids in children.items()
        }
        return sorted_albums, sorted_children, nodes

    def iter_album_dirs(self, root: Path, error_emitter=None) -> Iterable[Path]:
        """Yield immediate sub-directories that represent albums.

        Skips internal directories (``WORK_DIR_NAME``, ``RECENTLY_DELETED_DIR_NAME``,
        ``EXPORT_DIR_NAME``) and non-directory entries.  Filesystem errors are
        forwarded via *error_emitter* when provided.
        """
        try:
            entries = list(root.iterdir())
        except OSError as exc:
            if error_emitter is not None:
                error_emitter(str(exc))
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in (WORK_DIR_NAME, RECENTLY_DELETED_DIR_NAME, EXPORT_DIR_NAME):
                continue
            yield entry


__all__ = ["LibraryTreeService"]

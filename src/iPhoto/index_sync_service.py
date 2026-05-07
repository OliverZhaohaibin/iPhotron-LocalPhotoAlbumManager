"""Index synchronisation utilities shared by scanning and album workflows."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

from .cache.lock import FileLock
from .config import RECENTLY_DELETED_DIR_NAME
from .core.pairing import pair_live
from .errors import IndexCorruptedError, ManifestInvalidError
from .domain.models.core import LiveGroup
from .path_normalizer import compute_album_path, normalise_rel_key
from .utils.jsonio import read_json, write_json
from .utils.logging import get_logger
from .utils.pathutils import ensure_work_dir

LOGGER = get_logger()

if TYPE_CHECKING:  # pragma: no cover
    from .application.ports import AssetRepositoryPort


def load_incremental_index_cache(
    root: Path,
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
) -> Dict[str, dict]:
    """Load the existing index into a dictionary for incremental scanning.

    This helper encapsulates the logic of reading the index store and normalizing
    keys, allowing it to be reused by both the main application facade and
    background workers.
    
    Args:
        root: The album root directory.
        library_root: If provided, use this as the database root (global database).
    """
    existing_index = {}
    
    # If using global DB, filter by album path
    album_path = compute_album_path(root, library_root)
    
    try:
        if album_path:
            rows = repository.read_album_assets(album_path, include_subalbums=True)
        else:
            rows = repository.read_all()
        for row in rows:
            rel_key = normalise_rel_key(row.get("rel"))
            if rel_key:
                existing_index[rel_key] = row
    except IndexCorruptedError:
        pass
    return existing_index


def update_index_snapshot(
    root: Path,
    materialised_rows: List[dict],
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
) -> None:
    """Apply *materialised_rows* to the global database using additive-only updates.

    This function implements **Constraint #4: Additive-Only "Fact Supplementation"**:
    - Scanning is for discovering facts, not removing them
    - Files not found during a partial scan are NOT deleted from the database
    - Deletion is a separate lifecycle event and never occurs during scan
    
    The function uses idempotent upsert operations to ensure duplicate scans
    don't create duplicate data (Constraint #3).
    
    Args:
        root: The album root directory.
        materialised_rows: List of rows to update/insert.
        library_root: If provided, use this as the database root (global database).
    """
    corrupted_during_read = False
    try:
        # Just verify we can read the database
        list(repository.read_all())
    except IndexCorruptedError:
        corrupted_during_read = True

    fresh_rows: Dict[str, dict] = {}
    for row in materialised_rows:
        rel_key = normalise_rel_key(row.get("rel"))
        if rel_key is None:
            continue
        fresh_rows[rel_key] = row

    materialised_snapshot = list(fresh_rows.values())

    if corrupted_during_read:
        # On corruption, write all rows to rebuild the database
        write_rows = getattr(repository, "write_rows", None)
        if callable(write_rows):
            write_rows(materialised_snapshot)
        else:
            repository.append_rows(materialised_snapshot)
        return

    if not fresh_rows:
        return

    # Additive-only: only append new/updated rows, never delete.
    # Scan merges preserve persisted state fields such as face_status.
    try:
        repository.merge_scan_rows(materialised_snapshot)
    except IndexCorruptedError:
        write_rows = getattr(repository, "write_rows", None)
        if callable(write_rows):
            write_rows(materialised_snapshot)
        else:
            repository.append_rows(materialised_snapshot)


def ensure_links(
    root: Path,
    rows: List[dict],
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
) -> List[LiveGroup]:
    """Ensure DB live-role state is current and refresh the derived links snapshot.
    
    Args:
        root: The album root directory.
        rows: List of asset rows (with album-relative paths).
        library_root: If provided, use this as the database root.
    """
    groups, payload = compute_links_payload(rows)
    sync_live_roles_to_db(
        root,
        groups,
        library_root=library_root,
        repository=repository,
    )

    work_dir = ensure_work_dir(root)
    links_path = work_dir / "links.json"
    if links_path.exists():
        try:
            existing: Dict[str, object] = read_json(links_path)
        except ManifestInvalidError:
            existing = {}
        if existing == payload:
            return groups

    LOGGER.info("Updating links.json for %s", root)
    try:
        write_links(root, payload)
    except Exception as exc:  # pragma: no cover - derived snapshot failure must not break runtime state
        LOGGER.warning("Failed to update derived links.json for %s: %s", root, exc)
    return groups


def compute_links_payload(rows: List[dict]) -> tuple[List[LiveGroup], Dict[str, object]]:
    groups = pair_live(rows)
    payload: Dict[str, object] = {
        "schema": "iPhoto/links@1",
        "live_groups": [asdict(group) for group in groups],
        "clips": [],
    }
    return groups, payload


def write_links(root: Path, payload: Dict[str, object]) -> None:
    work_dir = ensure_work_dir(root)
    with FileLock(root, "links"):
        write_json(work_dir / "links.json", payload, backup_dir=work_dir / "manifest.bak")


def sync_live_roles_to_db(
    root: Path,
    groups: List[LiveGroup],
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
) -> None:
    """Propagate live photo roles from computed groups to the repository.
    
    Args:
        root: The album root directory.
        groups: List of LiveGroup objects to sync.
        library_root: If provided, use this as the database root (global database).
    """
    updates: List[Tuple[str, int, Optional[str]]] = []
    batch_size = 1_000
    replace_scope = True
    
    # Compute album path for library-relative paths
    album_prefix = ""
    if library_root:
        rel = compute_album_path(root, library_root)
        if rel:
            album_prefix = f"{rel}/"

    def flush_updates() -> None:
        nonlocal updates, replace_scope
        if not updates and not replace_scope:
            return
        if album_prefix:
            repository.apply_live_role_updates_for_prefix(
                album_prefix,
                updates,
                replace_scope=replace_scope,
            )
        else:
            repository.apply_live_role_updates(
                updates,
                replace_scope=replace_scope,
            )
        updates = []
        replace_scope = False

    for group in groups:
        if not group.still or not group.motion:
            continue

        # Still image: Role 0 (Primary), Partner = Motion
        still_rel = f"{album_prefix}{group.still}" if album_prefix else group.still
        motion_rel = f"{album_prefix}{group.motion}" if album_prefix else group.motion
        updates.append((still_rel, 0, motion_rel))

        # Motion component: Role 1 (Hidden), Partner = Still
        updates.append((motion_rel, 1, still_rel))
        if len(updates) >= batch_size:
            flush_updates()

    flush_updates()


def prune_index_scope(
    root: Path,
    materialised_rows: Iterable[dict],
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
    exclude_globs: Iterable[str] | None = None,
) -> int:
    """Delete rows under *root* that were not rediscovered by the completed scan.

    Scans remain additive-only at the low-level merge layer. This helper applies
    deletion semantics explicitly at the rescan boundary, scoped strictly to the
    completed scan root so sibling albums remain untouched.
    """

    album_path = compute_album_path(root, library_root)

    fresh_rels = {
        rel_key
        for rel_key in (
            normalise_rel_key(row.get("rel"))
            for row in materialised_rows
        )
        if rel_key is not None
    }

    if album_path:
        scoped_rows = repository.read_album_assets(
            album_path,
            include_subalbums=True,
            sort_by_date=False,
            filter_hidden=False,
        )
    else:
        scoped_rows = repository.read_all(sort_by_date=False, filter_hidden=False)

    preserve_existing_trash_rows = (
        root.name != RECENTLY_DELETED_DIR_NAME
        and library_root is not None
        and any(RECENTLY_DELETED_DIR_NAME in pattern for pattern in (exclude_globs or ()))
    )
    trash_prefix = f"{RECENTLY_DELETED_DIR_NAME}/"
    removable: List[str] = []
    for row in scoped_rows:
        rel_key = normalise_rel_key(row.get("rel"))
        if rel_key is None:
            continue
        if preserve_existing_trash_rows and rel_key.startswith(trash_prefix):
            continue
        if rel_key not in fresh_rels:
            removable.append(rel_key)

    if not removable:
        return 0

    repository.remove_rows(removable)
    return len(removable)


__all__ = [
    "compute_links_payload",
    "ensure_links",
    "load_incremental_index_cache",
    "prune_index_scope",
    "sync_live_roles_to_db",
    "update_index_snapshot",
    "write_links",
]

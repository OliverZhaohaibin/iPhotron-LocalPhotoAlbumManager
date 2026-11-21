"""Index synchronisation utilities shared by scanning and album workflows."""

from __future__ import annotations

import stat
from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

from .cache.lock import FileLock
from .config import RECENTLY_DELETED_DIR_NAME
from .core.pairing import pair_live
from .errors import IPhotoError, IndexCorruptedError, ManifestInvalidError
from .domain.models.core import LiveGroup, normalise_content_id
from .path_normalizer import compute_album_path, normalise_rel_key
from .schemas import validate_links
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
    root_only: bool = False,
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
        if root_only:
            rows = repository.read_album_assets(
                "", include_subalbums=False, sort_by_date=False, filter_hidden=False
            )
        elif album_path:
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
    root_only: bool = False,
) -> List[LiveGroup]:
    """Ensure DB live-role state is current and refresh the derived links snapshot.
    
    Args:
        root: The album root directory.
        rows: List of asset rows (with album-relative paths).
        library_root: If provided, use this as the database root.
    """
    if root_only:
        return _ensure_root_media_links(root, rows, repository)

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


def _ensure_root_media_links(
    root: Path, rows: List[dict], repository: "AssetRepositoryPort"
) -> List[LiveGroup]:
    # Retain valid cross-album pairs from a full scan without reading other albums.
    cross_peers = repository.get_rows_by_rels({
        partner for row in rows
        if isinstance(partner := row.get("live_partner_rel"), str)
        and "/" in partner
    })
    groups: List[LiveGroup] = []
    matched: set[str] = set()
    former_paths = {
        row["rel"] for row in rows if row.get("live_partner_rel") or row.get("live_role")
    }
    active_rows = [row for row in rows if not row.get("is_deleted")]
    for row in rows:
        peer = cross_peers.get(row.get("live_partner_rel"))
        if peer is None or peer.get("live_partner_rel") != row["rel"]:
            continue
        former_paths.add(peer["rel"])
        if row.get("is_deleted") or peer.get("is_deleted"):
            continue
        cross_groups = pair_live([row, peer])
        if cross_groups:
            groups.extend(cross_groups)
            matched.add(row["rel"])
    remaining = {row["rel"]: row for row in active_rows if row["rel"] not in matched}
    candidates = repository.get_unpaired_live_candidates(
        cid for row in remaining.values()
        if (cid := normalise_content_id(row.get("content_id")))
    )
    root_paths = set(remaining)
    remaining.update(candidates)
    groups.extend(
        group for group in pair_live(list(remaining.values()))
        if group.still in root_paths or group.motion in root_paths
    )
    sync_live_roles_to_db(root, groups, repository=repository, former_paths=former_paths)
    _refresh_root_links_snapshot(root, groups)
    return groups


def _refresh_root_links_snapshot(root: Path, groups: List[LiveGroup]) -> None:
    try:
        work_dir = ensure_work_dir(root)
        links_path = work_dir / "links.json"
        # Leave creation of a complete snapshot to the normal full pairing path.
        if not links_path.is_file():
            return
        with FileLock(root, "links"):
            existing = read_json(links_path)
            if not isinstance(existing, dict) or existing.get("schema") != "iPhoto/links@1":
                return
            old_groups = existing.get("live_groups", [])
            if not isinstance(old_groups, list):
                return
            nested_groups = [
                group for group in old_groups
                if isinstance(group, dict)
                and "/" in str(group.get("still", ""))
                and "/" in str(group.get("motion", ""))
            ]
            updated_groups = [*nested_groups, *(asdict(group) for group in groups)]
            if updated_groups == old_groups:
                return
            # Older snapshots wrote absent optional values as JSON null.
            payload = {
                **existing,
                "live_groups": [
                    {key: value for key, value in group.items() if value is not None}
                    for group in updated_groups
                ],
            }
            if payload != existing:
                validate_links(payload)
                write_json(links_path, payload, backup_dir=work_dir / "manifest.bak")
    except (OSError, IPhotoError) as exc:
        LOGGER.warning("Failed to update derived links.json for %s: %s", root, exc)


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
    former_paths: set[str] | None = None,
) -> None:
    """Propagate live photo roles from computed groups to the repository.
    
    Args:
        root: The album root directory.
        groups: List of LiveGroup objects to sync.
        library_root: If provided, use this as the database root (global database).
    """
    updates: List[Tuple[str, int, Optional[str]]] = []
    
    # Compute album path for library-relative paths
    album_prefix = ""
    if library_root:
        rel = compute_album_path(root, library_root)
        if rel:
            album_prefix = f"{rel}/"

    for group in groups:
        if not group.still or not group.motion:
            continue

        # Still image: Role 0 (Primary), Partner = Motion
        still_rel = f"{album_prefix}{group.still}" if album_prefix else group.still
        motion_rel = f"{album_prefix}{group.motion}" if album_prefix else group.motion
        updates.append((still_rel, 0, motion_rel))

        # Motion component: Role 1 (Hidden), Partner = Still
        updates.append((motion_rel, 1, still_rel))

    if former_paths is not None:
        paired_paths = {rel for rel, _role, _partner in updates}
        updates.extend((rel, 0, None) for rel in former_paths - paired_paths)
        repository.update_live_roles(updates)
    elif album_prefix:
        repository.apply_live_role_updates_for_prefix(album_prefix, updates)
    else:
        repository.apply_live_role_updates(updates)


def prune_index_scope(
    root: Path,
    materialised_rows: Iterable[dict],
    library_root: Optional[Path] = None,
    *,
    repository: "AssetRepositoryPort",
    exclude_globs: Iterable[str] | None = None,
    preserve_modified_after_ms: int | None = None,
    current_scan_job_id: str | None = None,
    root_only: bool = False,
    watched_albums: frozenset[Path] = frozenset(),
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

    if root_only:
        scoped_rows = repository.read_album_assets(
            "", include_subalbums=False, sort_by_date=False, filter_hidden=False
        )
    elif album_path:
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

    @cache
    def awaiting_watcher(directory: Path) -> bool:
        # A missing watched album can be a rename. Its watcher reconciliation
        # must verify destinations before any recursive scan removes its rows.
        for album in (directory, *directory.parents):
            if album in watched_albums:
                try:
                    return not album.is_dir()
                except OSError:
                    return True
        return False

    removable: List[str] = []
    for row in scoped_rows:
        rel_key = normalise_rel_key(row.get("rel"))
        if rel_key is None:
            continue
        if preserve_existing_trash_rows and rel_key.startswith(trash_prefix):
            continue
        if rel_key not in fresh_rels:
            if _row_modified_after_scan_start_by_other_job(
                row,
                preserve_modified_after_ms=preserve_modified_after_ms,
                current_scan_job_id=current_scan_job_id,
            ):
                continue
            if watched_albums and awaiting_watcher(((library_root or root) / rel_key).parent):
                continue
            if root_only:
                # Directory notifications can arrive while a source is unreadable.
                try:
                    info = (root / rel_key).stat()
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
                else:
                    if stat.S_ISREG(info.st_mode):
                        continue
            removable.append(rel_key)

    if not removable:
        return 0

    if root_only:
        repository.remove_rows(removable, clear_live_partners=True)
    else:
        repository.remove_rows(removable)
    return len(removable)


def _row_modified_after_scan_start_by_other_job(
    row: dict,
    *,
    preserve_modified_after_ms: int | None,
    current_scan_job_id: str | None,
) -> bool:
    if preserve_modified_after_ms is None:
        return False
    try:
        updated_at = int(row.get("index_updated_at_ms") or 0)
    except (TypeError, ValueError):
        return False
    if updated_at <= int(preserve_modified_after_ms):
        return False
    row_scan_job_id = row.get("scan_job_id")
    if current_scan_job_id and row_scan_job_id == current_scan_job_id:
        return False
    return True


__all__ = [
    "compute_links_payload",
    "ensure_links",
    "load_incremental_index_cache",
    "prune_index_scope",
    "sync_live_roles_to_db",
    "update_index_snapshot",
    "write_links",
]

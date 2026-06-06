"""PendingMove dataclass and move-inclusion helpers."""

from dataclasses import dataclass
from pathlib import Path

from iPhoto.application.dtos import AssetDTO
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery


@dataclass(frozen=True)
class _PendingMove:
    dto: AssetDTO
    source_id: str | None
    source_abs: Path
    source_library_rel: Path
    source_album_path: str
    destination_root: Path
    destination_album_path: str
    destination_abs: Path
    destination_rel: Path
    is_delete: bool


def should_include_pending(pending: _PendingMove, query: AssetQuery) -> bool:
    """Decide whether a buffered pending-move should appear in the given query."""
    if query.asset_ids and pending.dto.id not in set(query.asset_ids):
        return False
    if query.is_favorite is True and not pending.dto.is_favorite:
        return False
    if query.media_types:
        is_video = pending.dto.is_video
        allowed = False
        for media_type in query.media_types:
            if media_type == MediaType.VIDEO and is_video:
                allowed = True
                break
            if media_type == MediaType.IMAGE and not is_video:
                allowed = True
                break
        if not allowed:
            return False
    if pending.is_delete:
        return query.album_path == RECENTLY_DELETED_DIR_NAME
    if query.album_path is None:
        return True
    dest_path = pending.destination_album_path
    if query.include_subalbums and dest_path.startswith(f"{query.album_path}/"):
        return True
    if dest_path == query.album_path:
        return True
    return False


def pending_source_matches_query(pending: _PendingMove, query: AssetQuery) -> bool:
    """Return whether the pending source row belongs to *query* before moving."""

    if query.asset_ids and pending.source_id not in set(query.asset_ids):
        return False
    if query.is_favorite is True and not pending.dto.is_favorite:
        return False
    if query.is_favorite is False and pending.dto.is_favorite:
        return False
    if query.media_types:
        is_video = pending.dto.is_video
        allowed = False
        for media_type in query.media_types:
            if media_type == MediaType.VIDEO and is_video:
                allowed = True
                break
            if media_type == MediaType.IMAGE and not is_video:
                allowed = True
                break
        if not allowed:
            return False
    source_album = pending.source_album_path
    source_in_trash = source_album == RECENTLY_DELETED_DIR_NAME or source_album.startswith(
        f"{RECENTLY_DELETED_DIR_NAME}/"
    )
    if source_in_trash and query.album_path != RECENTLY_DELETED_DIR_NAME:
        return False
    if query.album_path is None:
        return True
    if source_album == query.album_path:
        return True
    return bool(
        query.include_subalbums
        and source_album.startswith(f"{query.album_path}/")
    )

"""Stateless paging-strategy helpers for AssetDataSource."""

from pathlib import Path
from typing import Optional

from iPhoto.domain.models.query import AssetQuery


def should_use_paging(query: AssetQuery) -> bool:
    if query.album_path or query.album_id:
        return False
    if query.is_deleted:
        return False
    return True


def should_validate_paths(query: AssetQuery, library_root: Optional[Path]) -> bool:
    if library_root is None:
        return True
    if query.album_path or query.album_id:
        return True
    if query.is_deleted:
        return True
    return False

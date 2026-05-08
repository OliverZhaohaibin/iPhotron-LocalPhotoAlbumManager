"""Shared gallery query-window types that do not depend on Qt."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GalleryPageAnchor:
    """Cursor anchor describing a materialized row in the current query order."""

    row: int
    dt: str
    asset_id: str

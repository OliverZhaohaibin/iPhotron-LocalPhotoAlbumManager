"""SQL query construction utilities for the asset repository.

This module provides helper functions and builders for constructing complex
SQL queries, particularly for filtering and cursor-based pagination.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...config import RECENTLY_DELETED_DIR_NAME
from ...domain.models.query import CollectionQuery, CollectionType, PageCursor, SortDirection

ESCAPE_CLAUSE = "ESCAPE '\\'"


def normalize_path(path_str: str) -> str:
    """Normalize a path string to use forward slashes (POSIX style).

    This ensures consistent path representation across Windows/Mac/Linux.
    """
    return Path(path_str).as_posix()


def escape_like_pattern(path: str) -> str:
    """Escape special characters in a path for use in SQL LIKE patterns.

    SQLite's LIKE operator treats '%' and '_' as wildcards. This function
    escapes those characters (and backslashes) so they match literally when
    used with 'ESCAPE \\'.

    Args:
        path: The path string to escape.

    Returns:
        The escaped path suitable for use in a LIKE pattern.
    """
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class QueryBuilder:
    """Builds SQL queries with filters, pagination, and sorting.
    
    This class helps construct complex WHERE clauses and avoids string
    concatenation bugs by using parameterized queries.
    """

    # Whitelist of allowed filter modes to prevent injection and logic errors
    _VALID_FILTER_MODES = frozenset({"videos", "live", "favorites"})
    _COLLECTION_SORT_COLUMNS = frozenset({"sort_ts", "ts", "dt", "id", "rel", "bytes"})

    @staticmethod
    def build_filter_clauses(
        filter_params: Optional[Dict[str, Any]]
    ) -> Tuple[List[str], List[Any]]:
        """Build WHERE clauses and parameters from filter params.
        
        Args:
            filter_params: Dictionary of filter criteria.
                Supported keys:
                - 'media_type' (int): Filter by media type.
                - 'filter_mode' (str): Filter mode ("videos", "live", "favorites").
        
        Returns:
            Tuple of (where_clauses, params) for use in SQL queries.
        
        Raises:
            ValueError: If filter parameters are invalid.
        """
        where_clauses: List[str] = []
        params: List[Any] = []

        if not filter_params:
            return where_clauses, params

        if "media_type" in filter_params:
            media_type = filter_params["media_type"]
            if not isinstance(media_type, int):
                raise ValueError(f"Invalid media_type: {media_type} (expected int)")
            where_clauses.append("media_type = ?")
            params.append(media_type)

        if "filter_mode" in filter_params:
            mode = filter_params["filter_mode"]
            # Strict whitelist check for filter mode
            if mode in QueryBuilder._VALID_FILTER_MODES:
                if mode == "videos":
                    where_clauses.append("media_type = 1")
                elif mode == "live":
                    where_clauses.append(
                        "("
                        "(live_role = 0 AND live_partner_rel IS NOT NULL)"
                        ")"
                    )
                elif mode == "favorites":
                    where_clauses.append("is_favorite = 1")
            else:
                valid_modes = ", ".join(sorted(QueryBuilder._VALID_FILTER_MODES))
                raise ValueError(
                    f"Invalid filter_mode: {mode!r}. Valid options are: {valid_modes}"
                )

        prefix = filter_params.get("exclude_path_prefix") if filter_params else None
        if isinstance(prefix, str) and prefix:
            normalized = normalize_path(prefix)
            escaped_prefix = escape_like_pattern(normalized)
            where_clauses.append(
                f"(parent_album_path IS NULL OR (parent_album_path != ? AND parent_album_path NOT LIKE ? {ESCAPE_CLAUSE}))"
            )
            params.extend([normalized, f"{escaped_prefix}/%"])

        return where_clauses, params

    @staticmethod
    def build_album_filter(
        album_path: Optional[str],
        include_subalbums: bool = True,
    ) -> Tuple[List[str], List[Any]]:
        """Build WHERE clauses for album path filtering.
        
        Args:
            album_path: The album path to filter by.
            include_subalbums: If True, include sub-albums using LIKE pattern.
        
        Returns:
            Tuple of (where_clauses, params) for use in SQL queries.
        """
        where_clauses: List[str] = []
        params: List[Any] = []

        if album_path is None:
            return where_clauses, params

        if include_subalbums:
            # Match exact album or any sub-album
            where_clauses.append(
                "(parent_album_path = ? OR parent_album_path LIKE ? ESCAPE '\\')"
            )
            params.append(album_path)
            escaped_path = escape_like_pattern(album_path)
            params.append(f"{escaped_path}/%")
        else:
            where_clauses.append("parent_album_path = ?")
            params.append(album_path)

        return where_clauses, params

    @staticmethod
    def build_cursor_filter(
        cursor_dt: Optional[str],
        cursor_id: Optional[str],
    ) -> Tuple[List[str], List[Any]]:
        """Build WHERE clauses for cursor-based pagination.
        
        Args:
            cursor_dt: The timestamp of the last item from the previous page.
            cursor_id: The ID of the last item from the previous page.
        
        Returns:
            Tuple of (where_clauses, params) for use in SQL queries.
        """
        where_clauses: List[str] = []
        params: List[Any] = []

        if cursor_dt is not None and cursor_id is not None:
            # Row value comparison for efficient seeking
            where_clauses.append("(dt, id) < (?, ?)")
            params.extend([cursor_dt, cursor_id])

        return where_clauses, params

    @staticmethod
    def build_pagination_query(
        select_clause: str = "SELECT *",
        base_where: Optional[List[str]] = None,
        album_path: Optional[str] = None,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
        cursor_dt: Optional[str] = None,
        cursor_id: Optional[str] = None,
        sort_by_date: bool = True,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Tuple[str, List[Any]]:
        """Build a complete paginated query with all filters.
        
        Args:
            select_clause: The SELECT clause (default: "SELECT *").
            base_where: Base WHERE clauses to include.
            album_path: Optional album path filter.
            include_subalbums: Include sub-albums in album filter.
            filter_hidden: Filter out hidden assets (live photo components).
            filter_params: Additional filter parameters.
            cursor_dt: Cursor timestamp for pagination.
            cursor_id: Cursor ID for pagination.
            sort_by_date: Sort results by date descending.
            limit: Maximum number of results to return.
            offset: Number of sorted rows to skip when using limit.
        
        Returns:
            Tuple of (query_string, params) ready for execution.
        """
        where_clauses: List[str] = list(base_where) if base_where else []
        params: List[Any] = []

        # Cursor filter for pagination
        cursor_where, cursor_params = QueryBuilder.build_cursor_filter(cursor_dt, cursor_id)
        where_clauses.extend(cursor_where)
        params.extend(cursor_params)

        # Album path filter
        album_where, album_params = QueryBuilder.build_album_filter(
            album_path, include_subalbums
        )
        where_clauses.extend(album_where)
        params.extend(album_params)

        # Hidden assets filter
        if filter_hidden:
            where_clauses.append("live_role = 0")

        # Additional filters
        if filter_params:
            filter_where, filter_params_list = QueryBuilder.build_filter_clauses(filter_params)
            where_clauses.extend(filter_where)
            params.extend(filter_params_list)

        # Build query
        query = f"{select_clause} FROM assets"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        if sort_by_date:
            query += " ORDER BY dt DESC NULLS LAST, id DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset > 0:
                query += " OFFSET ?"
                params.append(offset)

        return query, params

    @staticmethod
    def build_collection_query(
        collection_query: CollectionQuery,
        *,
        select_clause: str = "SELECT *",
        cursor: PageCursor | None = None,
        limit: int | None = None,
        offset: int = 0,
        include_order: bool = True,
    ) -> Tuple[str, List[Any]]:
        """Build a SQL-first collection query with keyset pagination support."""

        where_clauses, params = QueryBuilder.build_collection_where(collection_query)

        if cursor is not None:
            sort_col = QueryBuilder._collection_sort_column(collection_query)
            direction = collection_query.sort_direction
            if direction == SortDirection.ASC:
                where_clauses.append(f"({sort_col} > ? OR ({sort_col} = ? AND id > ?))")
            else:
                where_clauses.append(f"({sort_col} < ? OR ({sort_col} = ? AND id < ?))")
            params.extend([cursor.sort_ts, cursor.sort_ts, cursor.asset_id])

        query = f"{select_clause} FROM assets"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        if include_order:
            query += " " + QueryBuilder.build_collection_order(collection_query)

        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
            if offset > 0:
                query += " OFFSET ?"
                params.append(max(0, int(offset)))

        return query, params

    @staticmethod
    def build_collection_where(
        collection_query: CollectionQuery,
    ) -> Tuple[List[str], List[Any]]:
        album_path = collection_query.album_path
        is_trash_collection = album_path == RECENTLY_DELETED_DIR_NAME
        where_clauses: List[str] = ["live_role = 0"]
        if not is_trash_collection:
            where_clauses.append("is_deleted = 0")
        params: List[Any] = []

        if collection_query.min_thumbnail_state:
            where_clauses.append("thumbnail_state = ?")
            params.append(collection_query.min_thumbnail_state)
            if collection_query.min_thumbnail_state == "ready":
                where_clauses.append(
                    "TRIM(COALESCE(thumb_cache_key, '')) != ''"
                )

        if collection_query.collection_type == CollectionType.ALBUM or album_path:
            album_where, album_params = QueryBuilder.build_album_filter(
                album_path,
                collection_query.include_subalbums,
            )
            where_clauses.extend(album_where)
            params.extend(album_params)

        if collection_query.collection_type == CollectionType.FAVORITES:
            where_clauses.append("is_favorite = 1")
        elif collection_query.is_favorite is not None:
            where_clauses.append("is_favorite = ?")
            params.append(1 if collection_query.is_favorite else 0)

        media_types = tuple(int(value) for value in collection_query.media_types)
        if collection_query.collection_type == CollectionType.VIDEOS and not media_types:
            media_types = (1,)
        if media_types:
            placeholders = ", ".join(["?"] * len(media_types))
            where_clauses.append(f"media_type IN ({placeholders})")
            params.extend(media_types)

        if collection_query.has_gps is not None:
            if collection_query.has_gps:
                where_clauses.append("has_gps = 1")
            else:
                where_clauses.append("has_gps = 0")

        if collection_query.date_from is not None:
            where_clauses.append("sort_ts >= ?")
            params.append(QueryBuilder._datetime_to_microseconds(collection_query.date_from))
        if collection_query.date_to is not None:
            where_clauses.append("sort_ts <= ?")
            params.append(QueryBuilder._datetime_to_microseconds(collection_query.date_to))

        if collection_query.search_text:
            pattern = f"%{escape_like_pattern(collection_query.search_text)}%"
            where_clauses.append(f"rel LIKE ? {ESCAPE_CLAUSE}")
            params.append(pattern)

        return where_clauses, params

    @staticmethod
    def build_collection_order(collection_query: CollectionQuery) -> str:
        sort_col = QueryBuilder._collection_sort_column(collection_query)
        direction = "ASC" if collection_query.sort_direction == SortDirection.ASC else "DESC"
        return f"ORDER BY {sort_col} {direction}, id {direction}"

    @staticmethod
    def _collection_sort_column(collection_query: CollectionQuery) -> str:
        sort_key = collection_query.sort_key
        if sort_key in {"created_at", "capture_ts"}:
            return "sort_ts"
        if sort_key not in QueryBuilder._COLLECTION_SORT_COLUMNS:
            return "sort_ts"
        return sort_key

    @staticmethod
    def _datetime_to_microseconds(value: datetime) -> int:
        normalized = value
        if normalized.tzinfo is not None:
            normalized = normalized.astimezone(timezone.utc).replace(tzinfo=None)
        return int(normalized.replace(tzinfo=timezone.utc).timestamp() * 1_000_000)

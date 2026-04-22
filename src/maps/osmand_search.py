"""Offline OsmAnd place search bindings used by the info-panel location editor."""

from __future__ import annotations

import ctypes
import json
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import PySide6
import shiboken6

from maps.map_sources import (
    MapSourceSpec,
    default_osmand_search_database,
    resolve_osmand_native_widget_library,
)
from maps.tile_parser import TileLoadingError

_NATIVE_DLL_DIR_HANDLES: list[Any] = []
_PRELOADED_QT_LIBRARIES: list[ctypes.CDLL] = []
_WHITESPACE_RE = re.compile(r"\s+")
_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_PREFIX_CACHE_MAX_QUERY_LEN = 4
_OPTIMIZED_EXACT_SQL = """
SELECT
    geoname_id,
    primary_name,
    asciiname,
    matched_name,
    latitude,
    longitude,
    feature_code,
    country_code,
    admin1_code,
    admin2_code,
    admin3_code,
    admin4_code,
    population
FROM search_index
WHERE norm_name = :query_norm
ORDER BY
    name_priority ASC,
    population DESC,
    geoname_id ASC
LIMIT :result_limit
"""
_OPTIMIZED_PREFIX_CACHE_SQL = """
SELECT
    geoname_id,
    primary_name,
    asciiname,
    matched_name,
    latitude,
    longitude,
    feature_code,
    country_code,
    admin1_code,
    admin2_code,
    admin3_code,
    admin4_code,
    population
FROM prefix_cache
WHERE prefix = :query_norm
ORDER BY rank ASC
LIMIT :result_limit
"""
_OPTIMIZED_PREFIX_RANGE_SQL = """
SELECT
    geoname_id,
    primary_name,
    asciiname,
    matched_name,
    latitude,
    longitude,
    feature_code,
    country_code,
    admin1_code,
    admin2_code,
    admin3_code,
    admin4_code,
    population
FROM search_index
WHERE norm_name >= :query_norm
  AND norm_name < :query_upper
  AND norm_name <> :query_norm
ORDER BY
    norm_name ASC,
    name_priority ASC,
    population DESC,
    geoname_id ASC
LIMIT :result_limit
"""
_FTS_SEARCH_SQL = """
WITH ranked_hits AS (
    SELECT
        an.alt_name_id,
        an.geoname_id,
        an.lang,
        an.name AS matched_name,
        an.norm_name,
        an.is_preferred,
        g.name AS primary_name,
        g.asciiname,
        g.latitude,
        g.longitude,
        g.feature_code,
        g.country_code,
        g.admin1_code,
        g.admin2_code,
        g.admin3_code,
        g.admin4_code,
        g.population,
        CASE WHEN an.norm_name = :query_norm THEN 1 ELSE 0 END AS exact_rank,
        CASE WHEN an.norm_name GLOB :prefix_glob THEN 1 ELSE 0 END AS prefix_rank,
        bm25(alternate_names_fts) AS fts_score
    FROM alternate_names_fts
    JOIN alternate_names AS an
      ON an.alt_name_id = alternate_names_fts.rowid
    JOIN geonames AS g
      ON g.geoname_id = an.geoname_id
    WHERE alternate_names_fts MATCH :fts_query
    ORDER BY
        exact_rank DESC,
        prefix_rank DESC,
        an.is_preferred DESC,
        g.population DESC,
        fts_score ASC,
        an.geoname_id ASC,
        an.alt_name_id ASC
    LIMIT :candidate_limit
),
best_alias_per_geoname AS (
    SELECT *
    FROM (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY geoname_id
                ORDER BY
                    exact_rank DESC,
                    prefix_rank DESC,
                    is_preferred DESC,
                    population DESC,
                    fts_score ASC,
                    geoname_id ASC,
                    alt_name_id ASC
            ) AS rn
        FROM ranked_hits
    )
    WHERE rn = 1
)
SELECT
    geoname_id,
    primary_name,
    asciiname,
    matched_name,
    lang,
    latitude,
    longitude,
    feature_code,
    country_code,
    admin1_code,
    admin2_code,
    admin3_code,
    admin4_code,
    population,
    exact_rank,
    prefix_rank
FROM best_alias_per_geoname
ORDER BY
    exact_rank DESC,
    prefix_rank DESC,
    is_preferred DESC,
    population DESC,
    fts_score ASC,
    geoname_id ASC
LIMIT :result_limit
"""


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return _WHITESPACE_RE.sub(" ", normalized)


def _build_fts_query(query_norm: str) -> str:
    tokens = _FTS_TOKEN_RE.findall(query_norm)
    if not tokens:
        return ""
    if len(tokens) == 1:
        return f"{tokens[0]}*"
    return " ".join([*tokens[:-1], f"{tokens[-1]}*"])


def _next_prefix(value: str) -> str:
    return value + "\U0010FFFF"


def _secondary_text_from_row(row: sqlite3.Row, display_name: str) -> str:
    secondary_parts: list[str] = []
    primary_name = str(row["primary_name"] or "").strip()
    if primary_name and primary_name != display_name:
        secondary_parts.append(primary_name)

    country_code = str(row["country_code"] or "").strip()
    if country_code:
        secondary_parts.append(country_code)

    feature_code = str(row["feature_code"] or "").strip()
    if feature_code:
        secondary_parts.append(feature_code)

    return ", ".join(secondary_parts)


def _suggestion_from_row(row: sqlite3.Row, *, match_kind: str) -> SearchSuggestion | None:
    display_name = str(row["matched_name"] or row["primary_name"] or row["asciiname"] or "").strip()
    if not display_name:
        return None

    return SearchSuggestion(
        display_name=display_name,
        secondary_text=_secondary_text_from_row(row, display_name),
        longitude=float(row["longitude"]),
        latitude=float(row["latitude"]),
        source_kind="geonames",
        match_kind=match_kind,
    )


def _ensure_dll_directory(path: Path) -> None:
    if os.name == "nt" and hasattr(os, "add_dll_directory") and path.exists():
        _NATIVE_DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))


def _prepare_library_load(library_path: Path) -> None:
    if os.name == "nt":
        pyside_root = Path(PySide6.__file__).resolve().parent
        shiboken_root = Path(shiboken6.__file__).resolve().parent
        _ensure_dll_directory(pyside_root)
        _ensure_dll_directory(shiboken_root)

        _ensure_dll_directory(library_path.parent)

    if os.name != "nt":
        pyside_root = Path(PySide6.__file__).resolve().parent
        qt_lib_dir = (pyside_root / "Qt" / "lib").resolve()
        for candidate_dir in [qt_lib_dir, library_path.parent.resolve()]:
            lib_dir = str(candidate_dir)
            ld_path = os.environ.get("LD_LIBRARY_PATH", "")
            if lib_dir not in ld_path.split(os.pathsep):
                os.environ["LD_LIBRARY_PATH"] = lib_dir + (os.pathsep + ld_path if ld_path else "")
        if sys.platform == "darwin":
            for candidate_dir in [qt_lib_dir, library_path.parent.resolve()]:
                lib_dir = str(candidate_dir)
                dy_path = os.environ.get("DYLD_LIBRARY_PATH", "")
                if lib_dir not in dy_path.split(os.pathsep):
                    os.environ["DYLD_LIBRARY_PATH"] = lib_dir + (os.pathsep + dy_path if dy_path else "")
        elif sys.platform.startswith("linux") and qt_lib_dir.is_dir():
            preload_mode = getattr(ctypes, "RTLD_GLOBAL", 0)
            for library_name in [
                "libQt6Core.so.6",
                "libQt6Gui.so.6",
                "libQt6Widgets.so.6",
                "libQt6Network.so.6",
                "libQt6OpenGL.so.6",
                "libQt6OpenGLWidgets.so.6",
            ]:
                candidate = qt_lib_dir / library_name
                if candidate.exists():
                    _PRELOADED_QT_LIBRARIES.append(ctypes.CDLL(str(candidate), mode=preload_mode))


def _load_library(library_path: Path) -> ctypes.CDLL:
    _prepare_library_load(library_path)
    library = ctypes.CDLL(str(library_path))
    library.osmand_create_search_service.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.osmand_create_search_service.restype = ctypes.c_void_p
    library.osmand_destroy_search_service.argtypes = [ctypes.c_void_p]
    library.osmand_destroy_search_service.restype = None
    library.osmand_abort_search.argtypes = [ctypes.c_void_p]
    library.osmand_abort_search.restype = None
    library.osmand_search_query.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.osmand_search_query.restype = ctypes.c_int
    return library


@dataclass(frozen=True)
class SearchSuggestion:
    display_name: str
    secondary_text: str
    longitude: float
    latitude: float
    source_kind: str
    match_kind: str


class NativeOsmAndSearchService:
    """Thin ctypes wrapper around the native OsmAnd search bridge."""

    def __init__(self, map_source: MapSourceSpec | None = None) -> None:
        package_root = Path(__file__).resolve().parent
        self._map_source = (map_source or MapSourceSpec.osmand_default(package_root)).resolved(package_root)
        library_path = resolve_osmand_native_widget_library(package_root)
        if library_path is None:
            raise TileLoadingError("The native OsmAnd widget library is not available for search")

        self._library_path = library_path.resolve()
        self._library = _load_library(self._library_path)
        error_buffer = ctypes.create_unicode_buffer(4096)
        pointer = self._library.osmand_create_search_service(
            str(self._map_source.data_path),
            str(self._map_source.resources_root or ""),
            ctypes.cast(error_buffer, ctypes.c_void_p),
            len(error_buffer),
        )
        if not pointer:
            message = error_buffer.value or "Failed to create the native OsmAnd search service"
            raise TileLoadingError(message)

        self._service_pointer = ctypes.c_void_p(pointer)

    def abort(self) -> None:
        if getattr(self, "_service_pointer", None):
            self._library.osmand_abort_search(self._service_pointer)

    def shutdown(self) -> None:
        if getattr(self, "_service_pointer", None):
            self.abort()
            self._library.osmand_destroy_search_service(self._service_pointer)
            self._service_pointer = None

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        locale: str = "",
        include_poi_fallback: bool = True,
    ) -> list[SearchSuggestion]:
        trimmed_query = query.strip()
        if not trimmed_query:
            return []
        if len(trimmed_query) < 2 and all(ord(character) < 128 for character in trimmed_query):
            return []

        output_buffer = ctypes.create_unicode_buffer(32768)
        error_buffer = ctypes.create_unicode_buffer(4096)
        succeeded = self._library.osmand_search_query(
            self._service_pointer,
            trimmed_query,
            int(limit),
            locale,
            int(include_poi_fallback),
            ctypes.cast(output_buffer, ctypes.c_void_p),
            len(output_buffer),
            ctypes.cast(error_buffer, ctypes.c_void_p),
            len(error_buffer),
        )
        if not succeeded:
            message = error_buffer.value or "The native OsmAnd search query failed"
            raise TileLoadingError(message)

        raw_results = json.loads(output_buffer.value or "[]")
        suggestions: list[SearchSuggestion] = []
        for raw in raw_results:
            suggestions.append(
                SearchSuggestion(
                    display_name=str(raw.get("display_name", "")),
                    secondary_text=str(raw.get("secondary_text", "")),
                    longitude=float(raw.get("longitude", 0.0)),
                    latitude=float(raw.get("latitude", 0.0)),
                    source_kind=str(raw.get("source_kind", "")),
                    match_kind=str(raw.get("match_kind", "")),
                ),
            )
        return suggestions[: max(1, min(int(limit), 5))]

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass


class GeoNamesSearchService:
    """Search the bundled GeoNames SQLite database with schema-aware query selection."""

    def __init__(self, database_path: Path | None = None) -> None:
        package_root = Path(__file__).resolve().parent
        self._database_path = (database_path or default_osmand_search_database(package_root)).resolve()
        if not self._database_path.is_file():
            raise FileNotFoundError(f"GeoNames database not found: {self._database_path}")

        self._connection = sqlite3.connect(
            f"file:{self._database_path.as_posix()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only = 1")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA temp_store = MEMORY")
        self._connection.execute("PRAGMA mmap_size = 268435456")
        self._has_prefix_cache = False
        self._query_mode = self._detect_query_mode()

    def _detect_query_mode(self) -> str:
        table_names = {
            str(row["name"])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')",
            ).fetchall()
        }
        if "search_index" in table_names:
            self._has_prefix_cache = "prefix_cache" in table_names
            return "optimized"
        if "alternate_names_fts" in table_names:
            return "fts"
        raise TileLoadingError(
            "GeoNames database is missing both optimized search tables and the legacy FTS index",
        )

    def abort(self) -> None:
        if getattr(self, "_connection", None) is not None:
            self._connection.interrupt()

    def shutdown(self) -> None:
        if getattr(self, "_connection", None) is not None:
            self._connection.close()
            self._connection = None

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        locale: str = "",
        include_poi_fallback: bool = True,
    ) -> list[SearchSuggestion]:
        del locale, include_poi_fallback

        trimmed_query = query.strip()
        if not trimmed_query:
            return []
        if len(trimmed_query) < 2 and all(ord(character) < 128 for character in trimmed_query):
            return []

        query_norm = _normalize_name(trimmed_query)
        if not query_norm:
            return []

        result_limit = max(1, min(int(limit), 5))

        try:
            if self._query_mode == "optimized":
                suggestions = self._search_optimized(query_norm, result_limit=result_limit)
            else:
                suggestions = self._search_fts(query_norm, result_limit=result_limit)
        except sqlite3.Error as exc:
            raise TileLoadingError(f"GeoNames query failed: {exc}") from exc

        return suggestions[:result_limit]

    def _search_optimized(self, query_norm: str, *, result_limit: int) -> list[SearchSuggestion]:
        exact_rows = self._connection.execute(
            _OPTIMIZED_EXACT_SQL,
            {
                "query_norm": query_norm,
                "result_limit": result_limit,
            },
        ).fetchall()
        suggestions: list[SearchSuggestion] = []
        seen_geoname_ids: set[int] = set()

        for row in exact_rows:
            suggestion = _suggestion_from_row(row, match_kind="exact")
            if suggestion is None:
                continue
            geoname_id = int(row["geoname_id"])
            suggestions.append(suggestion)
            seen_geoname_ids.add(geoname_id)

        if len(suggestions) >= result_limit:
            return suggestions[:result_limit]

        if self._has_prefix_cache and len(query_norm) <= _PREFIX_CACHE_MAX_QUERY_LEN:
            prefix_rows = self._connection.execute(
                _OPTIMIZED_PREFIX_CACHE_SQL,
                {
                    "query_norm": query_norm,
                    "result_limit": result_limit,
                },
            ).fetchall()
        else:
            prefix_rows = self._connection.execute(
                _OPTIMIZED_PREFIX_RANGE_SQL,
                {
                    "query_norm": query_norm,
                    "query_upper": _next_prefix(query_norm),
                    "result_limit": result_limit,
                },
            ).fetchall()

        for row in prefix_rows:
            geoname_id = int(row["geoname_id"])
            if geoname_id in seen_geoname_ids:
                continue
            suggestion = _suggestion_from_row(row, match_kind="prefix")
            if suggestion is None:
                continue
            suggestions.append(suggestion)
            seen_geoname_ids.add(geoname_id)
            if len(suggestions) >= result_limit:
                break

        return suggestions

    def _search_fts(self, query_norm: str, *, result_limit: int) -> list[SearchSuggestion]:
        fts_query = _build_fts_query(query_norm)
        if not fts_query:
            return []

        candidate_limit = max(64, min(256, result_limit * 32))
        rows = self._connection.execute(
            _FTS_SEARCH_SQL,
            {
                "query_norm": query_norm,
                "prefix_glob": f"{query_norm}*",
                "fts_query": fts_query,
                "candidate_limit": candidate_limit,
                "result_limit": result_limit,
            },
        ).fetchall()

        suggestions: list[SearchSuggestion] = []
        for row in rows:
            if int(row["exact_rank"] or 0):
                match_kind = "exact"
            elif int(row["prefix_rank"] or 0):
                match_kind = "prefix"
            else:
                match_kind = "fts"

            suggestion = _suggestion_from_row(row, match_kind=match_kind)
            if suggestion is None:
                continue
            suggestions.append(suggestion)

        return suggestions

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass


class OsmAndSearchService:
    """Prefer GeoNames SQLite search and fall back to the native OsmAnd search bridge."""

    def __init__(self, map_source: MapSourceSpec | None = None) -> None:
        self._map_source = map_source
        self._geonames_service: GeoNamesSearchService | None = None
        self._native_service: NativeOsmAndSearchService | None = None
        self._native_init_error: Exception | None = None

        geonames_error: Exception | None = None
        try:
            self._geonames_service = GeoNamesSearchService()
        except Exception as exc:
            geonames_error = exc

        if self._geonames_service is None:
            try:
                self._native_service = NativeOsmAndSearchService(self._map_source)
            except Exception as exc:
                detail = f"GeoNames search unavailable: {geonames_error}" if geonames_error else "GeoNames search unavailable"
                raise TileLoadingError(f"{detail}; native fallback unavailable: {exc}") from exc

    def _ensure_native_service(self) -> NativeOsmAndSearchService:
        if self._native_service is not None:
            return self._native_service
        if self._native_init_error is not None:
            raise self._native_init_error

        try:
            self._native_service = NativeOsmAndSearchService(self._map_source)
        except Exception as exc:
            self._native_init_error = exc
            raise
        return self._native_service

    def abort(self) -> None:
        if self._geonames_service is not None:
            self._geonames_service.abort()
        if self._native_service is not None:
            self._native_service.abort()

    def shutdown(self) -> None:
        if self._native_service is not None:
            self._native_service.shutdown()
            self._native_service = None
        if self._geonames_service is not None:
            self._geonames_service.shutdown()
            self._geonames_service = None

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        locale: str = "",
        include_poi_fallback: bool = True,
    ) -> list[SearchSuggestion]:
        geonames_error: Exception | None = None
        if self._geonames_service is not None:
            try:
                suggestions = self._geonames_service.search(
                    query,
                    limit=limit,
                    locale=locale,
                    include_poi_fallback=include_poi_fallback,
                )
            except Exception as exc:
                geonames_error = exc
            else:
                if suggestions:
                    return suggestions

        try:
            native_service = self._ensure_native_service()
        except Exception as exc:
            if geonames_error is not None:
                raise TileLoadingError(
                    f"GeoNames search failed: {geonames_error}; native fallback unavailable: {exc}",
                ) from exc
            if self._geonames_service is not None:
                return []
            raise

        try:
            return native_service.search(
                query,
                limit=limit,
                locale=locale,
                include_poi_fallback=include_poi_fallback,
            )
        except Exception as exc:
            if geonames_error is not None:
                raise TileLoadingError(f"GeoNames search failed: {geonames_error}; native fallback failed: {exc}") from exc
            raise

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass


__all__ = ["OsmAndSearchService", "SearchSuggestion"]

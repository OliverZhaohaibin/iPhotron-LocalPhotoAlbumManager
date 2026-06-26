from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from maps import osmand_search as osmand_search_module
from maps.osmand_search import GeoNamesSearchService, OsmAndSearchService
from maps.tile_parser import TileLoadingError


def _create_optimized_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE search_index (
                norm_name TEXT NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (norm_name, name_priority, population DESC, geoname_id)
            ) WITHOUT ROWID;
            CREATE TABLE prefix_cache (
                prefix TEXT NOT NULL,
                rank INTEGER NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (prefix, rank)
            ) WITHOUT ROWID;
            """
        )
        search_rows = [
            ("mu", 0, 1000, 1, "Mu", "Mu", "Mu", 1.0, 1.0, "PPL", "XX", "", "", "", ""),
            (
                "munich",
                0,
                1_488_202,
                2,
                "Munich",
                "Munich",
                "Munich",
                48.137154,
                11.576124,
                "PPLA",
                "DE",
                "02",
                "",
                "",
                "",
            ),
            (
                "munich airport",
                0,
                0,
                3,
                "Munich Airport",
                "Munich Airport",
                "Munich Airport",
                48.353783,
                11.786086,
                "AIRP",
                "DE",
                "02",
                "",
                "",
                "",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO search_index (
                norm_name, name_priority, population, geoname_id, matched_name,
                primary_name, asciiname, latitude, longitude, feature_code, country_code,
                admin1_code, admin2_code, admin3_code, admin4_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            search_rows,
        )
        prefix_rows = [
            ("mu", 1, 0, 1000, 1, "Mu", "Mu", "Mu", 1.0, 1.0, "PPL", "XX", "", "", "", ""),
            (
                "mu",
                2,
                0,
                1_488_202,
                2,
                "Munich",
                "Munich",
                "Munich",
                48.137154,
                11.576124,
                "PPLA",
                "DE",
                "02",
                "",
                "",
                "",
            ),
            (
                "zz",
                1,
                0,
                99,
                4,
                "Zzyzx",
                "Zzyzx",
                "Zzyzx",
                35.143,
                -116.104,
                "PPL",
                "US",
                "CA",
                "",
                "",
                "",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO prefix_cache (
                prefix, rank, name_priority, population, geoname_id, matched_name,
                primary_name, asciiname, latitude, longitude, feature_code, country_code,
                admin1_code, admin2_code, admin3_code, admin4_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prefix_rows,
        )


def _create_optimized_db_without_prefix_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE search_index (
                norm_name TEXT NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (norm_name, name_priority, population DESC, geoname_id)
            ) WITHOUT ROWID;
            """
        )
        conn.execute(
            """
            INSERT INTO search_index (
                norm_name, name_priority, population, geoname_id, matched_name,
                primary_name, asciiname, latitude, longitude, feature_code, country_code,
                admin1_code, admin2_code, admin3_code, admin4_code
            ) VALUES (
                'munich', 0, 1488202, 2, 'Munich', 'Munich', 'Munich',
                48.137154, 11.576124, 'PPLA', 'DE', '02', '', '', ''
            )
            """
        )


def _create_legacy_fts_db(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        try:
            conn.executescript(
                """
                CREATE TABLE geonames (
                    geoname_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    asciiname TEXT,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    feature_code TEXT,
                    country_code TEXT,
                    admin1_code TEXT,
                    admin2_code TEXT,
                    admin3_code TEXT,
                    admin4_code TEXT,
                    population INTEGER NOT NULL
                );
                CREATE TABLE alternate_names (
                    alt_name_id INTEGER PRIMARY KEY,
                    geoname_id INTEGER NOT NULL,
                    lang TEXT,
                    name TEXT NOT NULL,
                    norm_name TEXT NOT NULL,
                    is_preferred INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE alternate_names_fts USING fts5(name);
                """
            )
        except sqlite3.OperationalError:
            return False
        conn.execute(
            """
            INSERT INTO geonames (
                geoname_id, name, asciiname, latitude, longitude, feature_code,
                country_code, admin1_code, admin2_code, admin3_code, admin4_code, population
            ) VALUES (1, 'Paris', 'Paris', 48.8566, 2.3522, 'PPLC', 'FR', '11', '', '', '', 2161000)
            """
        )
        conn.execute(
            """
            INSERT INTO alternate_names (
                alt_name_id, geoname_id, lang, name, norm_name, is_preferred
            ) VALUES (1, 1, 'en', 'Paris', 'paris', 1)
            """
        )
        conn.execute("INSERT INTO alternate_names_fts(rowid, name) VALUES (1, 'Paris')")
    return True


def test_geonames_optimized_exact_and_prefix_range(tmp_path: Path) -> None:
    db_path = tmp_path / "geonames.sqlite3"
    _create_optimized_db(db_path)

    service = GeoNamesSearchService(database_path=db_path)
    try:
        results = service.search("munich")
    finally:
        service.shutdown()

    assert [result.display_name for result in results] == ["Munich", "Munich Airport"]
    assert [result.match_kind for result in results] == ["exact", "prefix"]


def test_geonames_optimized_short_prefix_uses_prefix_cache_and_dedupes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "geonames.sqlite3"
    _create_optimized_db(db_path)

    service = GeoNamesSearchService(database_path=db_path)
    try:
        results = service.search("mu")
        cached_only_results = service.search("zz")
    finally:
        service.shutdown()

    assert [result.display_name for result in results] == ["Mu", "Munich"]
    assert [result.display_name for result in cached_only_results] == ["Zzyzx"]
    assert [result.match_kind for result in cached_only_results] == ["prefix"]


def test_geonames_optimized_mode_allows_missing_prefix_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "geonames.sqlite3"
    _create_optimized_db_without_prefix_cache(db_path)

    service = GeoNamesSearchService(database_path=db_path)
    try:
        results = service.search("mun")
    finally:
        service.shutdown()

    assert service._query_mode == "optimized"
    assert [result.display_name for result in results] == ["Munich"]
    assert [result.match_kind for result in results] == ["prefix"]


def test_geonames_legacy_fts_mode_only_when_optimized_tables_are_absent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    if not _create_legacy_fts_db(db_path):
        pytest.skip("SQLite FTS5 is unavailable")

    service = GeoNamesSearchService(database_path=db_path)
    try:
        assert service._query_mode == "legacy_fts"
        results = service.search("par")
    finally:
        service.shutdown()

    assert [result.display_name for result in results] == ["Paris"]


def test_geonames_rejects_malformed_optimized_schema_even_with_legacy_fts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "malformed.sqlite3"
    if not _create_legacy_fts_db(db_path):
        pytest.skip("SQLite FTS5 is unavailable")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE search_index (norm_name TEXT)")

    with pytest.raises(TileLoadingError, match="search_index"):
        GeoNamesSearchService(database_path=db_path)


def test_osmand_search_can_skip_native_fallback_for_empty_geonames_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "maps"
    db_path = package_root / "tiles" / "extension" / "search" / "geonames.sqlite3"
    _create_optimized_db(db_path)
    native_created = False

    class _NativeSearch:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal native_created
            native_created = True

    monkeypatch.setattr(osmand_search_module, "NativeOsmAndSearchService", _NativeSearch)

    service = OsmAndSearchService(package_root=package_root)
    try:
        assert service.search("no such place", fallback_on_empty=False) == []
    finally:
        service.shutdown()

    assert native_created is False

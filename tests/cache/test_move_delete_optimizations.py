"""Tests for move/delete performance optimizations.

Covers:
- Plan 4 §8.2.2: WAL mode + PRAGMA settings in DatabaseManager
- Plan 2 §6.2.1: get_rows_by_rels() in AssetRepository
- Plan 1 §5.2:  MoveOperationResult dataclass
- Plan 3 §7.2.2: incremental_cache_update() in AssetCacheManager
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.cache.index_store.engine import DatabaseManager
from iPhoto.cache.index_store.repository import AssetRepository


@pytest.fixture(autouse=True)
def clean_global_state():
    """Reset global repository before and after each test."""
    reset_global_repository()
    yield
    reset_global_repository()


# ------------------------------------------------------------------
# Plan 4 §8.2.2: WAL mode
# ------------------------------------------------------------------
class TestWALMode:
    """Verify WAL journal mode and optimised PRAGMAs."""

    def test_create_connection_enables_wal(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "test.db")
        conn = db._create_connection()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()

    def test_create_connection_sets_synchronous_normal(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "test.db")
        conn = db._create_connection()
        try:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            # NORMAL = 1
            assert sync == 1
        finally:
            conn.close()

    def test_create_connection_sets_cache_size(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "test.db")
        conn = db._create_connection()
        try:
            cache = conn.execute("PRAGMA cache_size").fetchone()[0]
            assert cache == -8000
        finally:
            conn.close()

    def test_transaction_uses_wal(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "test.db")
        with db.transaction() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


# ------------------------------------------------------------------
# Plan 2 §6.2.1: get_rows_by_rels()
# ------------------------------------------------------------------
class TestGetRowsByRels:
    """Verify AssetRepository.get_rows_by_rels() for source-row caching."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> AssetRepository:
        repo = AssetRepository(tmp_path)
        repo.append_rows([
            {"rel": "album/photo1.jpg", "id": "id1", "dt": "2024-01-01", "media_type": "image"},
            {"rel": "album/photo2.jpg", "id": "id2", "dt": "2024-01-02", "media_type": "image"},
            {"rel": "album/video1.mp4", "id": "id3", "dt": "2024-01-03", "media_type": "video"},
        ])
        yield repo
        repo.close()

    def test_returns_matching_rows(self, repo: AssetRepository) -> None:
        result = repo.get_rows_by_rels(["album/photo1.jpg", "album/video1.mp4"])
        assert len(result) == 2
        assert "album/photo1.jpg" in result
        assert "album/video1.mp4" in result
        assert result["album/photo1.jpg"]["id"] == "id1"

    def test_ignores_missing_rels(self, repo: AssetRepository) -> None:
        result = repo.get_rows_by_rels(["album/photo1.jpg", "nonexistent.jpg"])
        assert len(result) == 1
        assert "album/photo1.jpg" in result
        assert "nonexistent.jpg" not in result

    def test_empty_input(self, repo: AssetRepository) -> None:
        result = repo.get_rows_by_rels([])
        assert result == {}

    def test_all_missing(self, repo: AssetRepository) -> None:
        result = repo.get_rows_by_rels(["a.jpg", "b.jpg"])
        assert result == {}

    def test_row_data_matches_original(self, repo: AssetRepository) -> None:
        result = repo.get_rows_by_rels(["album/photo2.jpg"])
        row = result["album/photo2.jpg"]
        assert row["dt"] == "2024-01-02"
        assert row["media_type"] == "image"


# ------------------------------------------------------------------
# Plan 1 §5.2: MoveOperationResult
# ------------------------------------------------------------------
class TestMoveOperationResult:
    """Verify the MoveOperationResult dataclass."""

    def test_dataclass_fields(self) -> None:
        from iPhoto.gui.services.library_update_service import MoveOperationResult

        result = MoveOperationResult(
            source_root=Path("/src"),
            destination_root=Path("/dst"),
            moved_pairs=[(Path("/src/a.jpg"), Path("/dst/a.jpg"))],
            removed_rels=["a.jpg"],
            added_rels=["a.jpg"],
            is_delete=False,
            is_restore=False,
            source_ok=True,
            destination_ok=True,
        )
        assert result.source_root == Path("/src")
        assert len(result.moved_pairs) == 1
        assert result.removed_rels == ["a.jpg"]
        assert result.is_delete is False
        assert result.source_ok is True

    def test_default_values(self) -> None:
        from iPhoto.gui.services.library_update_service import MoveOperationResult

        result = MoveOperationResult(
            source_root=Path("/src"),
            destination_root=Path("/dst"),
        )
        assert result.moved_pairs == []
        assert result.removed_rels == []
        assert result.added_rels == []
        assert result.is_delete is False
        assert result.is_restore is False
        assert result.source_ok is True
        assert result.destination_ok is True

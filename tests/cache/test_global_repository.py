"""Tests for the global database singleton pattern."""
from __future__ import annotations

from pathlib import Path
import pytest
from src.iPhoto.cache.index_store import (
    get_global_repository,
    reset_global_repository,
    GLOBAL_INDEX_DB_NAME,
)
from src.iPhoto.config import WORK_DIR_NAME


@pytest.fixture(autouse=True)
def clean_global_state():
    """Reset global repository before and after each test."""
    reset_global_repository()
    yield
    reset_global_repository()


class TestGlobalRepositorySingleton:
    """Tests for the global repository singleton pattern."""

    def test_get_global_repository_creates_instance(self, tmp_path: Path) -> None:
        """Test that get_global_repository creates a new instance."""
        repo = get_global_repository(tmp_path)
        assert repo is not None
        assert repo.library_root.resolve() == tmp_path.resolve()
        assert repo.path == tmp_path / WORK_DIR_NAME / GLOBAL_INDEX_DB_NAME

    def test_get_global_repository_returns_singleton(self, tmp_path: Path) -> None:
        """Test that get_global_repository returns the same instance."""
        repo1 = get_global_repository(tmp_path)
        repo2 = get_global_repository(tmp_path)
        assert repo1 is repo2

    def test_get_global_repository_different_paths_switches(self, tmp_path: Path) -> None:
        """Test that different library roots create different instances."""
        lib1 = tmp_path / "Library1"
        lib2 = tmp_path / "Library2"
        lib1.mkdir()
        lib2.mkdir()

        repo1 = get_global_repository(lib1)
        assert repo1.library_root.resolve() == lib1.resolve()

        repo2 = get_global_repository(lib2)
        assert repo2.library_root.resolve() == lib2.resolve()
        # Should be different instance
        assert repo1 is not repo2

    def test_reset_global_repository_clears_singleton(self, tmp_path: Path) -> None:
        """Test that reset_global_repository clears the singleton."""
        repo1 = get_global_repository(tmp_path)
        reset_global_repository()
        repo2 = get_global_repository(tmp_path)
        # Should be different instances after reset
        assert repo1 is not repo2

    def test_global_repository_persists_data(self, tmp_path: Path) -> None:
        """Test that data persists across singleton accesses."""
        repo1 = get_global_repository(tmp_path)
        repo1.write_rows([{"rel": "test.jpg", "id": "1", "bytes": 100}])

        # Get same repository again
        repo2 = get_global_repository(tmp_path)
        rows = list(repo2.read_all())
        assert len(rows) == 1
        assert rows[0]["rel"] == "test.jpg"


class TestIdempotentWrites:
    """Tests verifying idempotent write behavior (Constraint #3)."""

    def test_duplicate_append_rows_no_duplicates(self, tmp_path: Path) -> None:
        """Test that appending the same rows multiple times doesn't create duplicates."""
        repo = get_global_repository(tmp_path)
        
        row = {"rel": "photo.jpg", "id": "1", "bytes": 100}
        
        # Append the same row 10 times
        for _ in range(10):
            repo.append_rows([row])
        
        rows = list(repo.read_all())
        assert len(rows) == 1
        assert rows[0]["rel"] == "photo.jpg"

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        """Test that upsert updates existing rows rather than duplicating."""
        repo = get_global_repository(tmp_path)
        
        # Insert initial row
        repo.append_rows([{"rel": "photo.jpg", "id": "1", "bytes": 100}])
        
        # Upsert with updated data
        repo.upsert_row("photo.jpg", {"rel": "photo.jpg", "id": "1", "bytes": 200})
        
        rows = list(repo.read_all())
        assert len(rows) == 1
        assert rows[0]["bytes"] == 200

    def test_multiple_scans_same_files_no_duplicates(self, tmp_path: Path) -> None:
        """Simulate multiple scans of the same files (Constraint #3)."""
        repo = get_global_repository(tmp_path)
        
        # First scan - 3 files
        scan1_rows = [
            {"rel": "a.jpg", "id": "1", "bytes": 100},
            {"rel": "b.jpg", "id": "2", "bytes": 200},
            {"rel": "c.jpg", "id": "3", "bytes": 300},
        ]
        repo.append_rows(scan1_rows)
        
        # Second scan - same files
        scan2_rows = [
            {"rel": "a.jpg", "id": "1", "bytes": 100},
            {"rel": "b.jpg", "id": "2", "bytes": 200},
            {"rel": "c.jpg", "id": "3", "bytes": 300},
        ]
        repo.append_rows(scan2_rows)
        
        rows = list(repo.read_all())
        assert len(rows) == 3


class TestAdditiveOnlyScans:
    """Tests verifying additive-only scan behavior (Constraint #4)."""

    def test_partial_scan_does_not_delete(self, tmp_path: Path) -> None:
        """Test that partial scans don't delete files not found (Constraint #4)."""
        repo = get_global_repository(tmp_path)
        
        # Initial full scan - 5 files
        initial_rows = [
            {"rel": "folder_a/img1.jpg", "id": "1"},
            {"rel": "folder_a/img2.jpg", "id": "2"},
            {"rel": "folder_b/img3.jpg", "id": "3"},
            {"rel": "folder_b/img4.jpg", "id": "4"},
            {"rel": "folder_c/img5.jpg", "id": "5"},
        ]
        repo.append_rows(initial_rows)
        
        # Partial scan - only folder_a files
        partial_rows = [
            {"rel": "folder_a/img1.jpg", "id": "1"},
            {"rel": "folder_a/img2.jpg", "id": "2"},
        ]
        repo.append_rows(partial_rows)
        
        # All 5 files should still exist
        rows = list(repo.read_all())
        assert len(rows) == 5
        rels = {r["rel"] for r in rows}
        assert "folder_b/img3.jpg" in rels
        assert "folder_c/img5.jpg" in rels

    def test_append_adds_new_files_only(self, tmp_path: Path) -> None:
        """Test that append_rows only adds new files, doesn't remove missing ones."""
        repo = get_global_repository(tmp_path)
        
        # Initial scan
        repo.append_rows([
            {"rel": "old.jpg", "id": "1"},
        ])
        
        # New scan with only new file
        repo.append_rows([
            {"rel": "new.jpg", "id": "2"},
        ])
        
        rows = list(repo.read_all())
        assert len(rows) == 2
        rels = {r["rel"] for r in rows}
        assert "old.jpg" in rels
        assert "new.jpg" in rels


class TestMultipleScanEntryPoints:
    """Tests verifying multiple scan entry points (Constraint #1)."""

    def test_scans_from_different_subfolders(self, tmp_path: Path) -> None:
        """Test that scans from different subfolders all use same database."""
        repo = get_global_repository(tmp_path)
        
        # Scan from subfolder A
        repo.append_rows([
            {"rel": "SubfolderA/img1.jpg", "id": "1"},
            {"rel": "SubfolderA/img2.jpg", "id": "2"},
        ])
        
        # Scan from subfolder B
        repo.append_rows([
            {"rel": "SubfolderB/img3.jpg", "id": "3"},
        ])
        
        # Scan from root
        repo.append_rows([
            {"rel": "root_img.jpg", "id": "4"},
        ])
        
        # All should be in single database
        rows = list(repo.read_all())
        assert len(rows) == 4
        
        # Verify we can query by album
        album_a = list(repo.read_album_assets("SubfolderA"))
        assert len(album_a) == 2
        
        album_b = list(repo.read_album_assets("SubfolderB"))
        assert len(album_b) == 1

    def test_single_file_scan_integrates(self, tmp_path: Path) -> None:
        """Test that scanning a single file integrates with full database."""
        repo = get_global_repository(tmp_path)
        
        # Existing data
        repo.append_rows([
            {"rel": "existing1.jpg", "id": "1"},
            {"rel": "existing2.jpg", "id": "2"},
        ])
        
        # Single file scan/import
        repo.upsert_row("new_import.jpg", {"rel": "new_import.jpg", "id": "3"})
        
        rows = list(repo.read_all())
        assert len(rows) == 3


class TestSingleWriteGateway:
    """Tests verifying single write gateway (Constraint #2)."""

    def test_all_writes_through_repository(self, tmp_path: Path) -> None:
        """Test that all writes go through the repository."""
        repo = get_global_repository(tmp_path)
        
        # Various write operations
        repo.append_rows([{"rel": "a.jpg", "id": "1"}])
        repo.upsert_row("b.jpg", {"rel": "b.jpg", "id": "2"})
        repo.write_rows([{"rel": "c.jpg", "id": "3"}])
        
        # All should be visible
        rows = list(repo.read_all())
        # write_rows replaces all, so only c.jpg should remain
        assert len(rows) == 1
        assert rows[0]["rel"] == "c.jpg"

    def test_transaction_batching(self, tmp_path: Path) -> None:
        """Test that transactions batch multiple operations atomically."""
        repo = get_global_repository(tmp_path)
        
        with repo.transaction():
            repo.upsert_row("a.jpg", {"rel": "a.jpg", "id": "1"})
            repo.upsert_row("b.jpg", {"rel": "b.jpg", "id": "2"})
            repo.upsert_row("c.jpg", {"rel": "c.jpg", "id": "3"})
        
        rows = list(repo.read_all())
        assert len(rows) == 3

"""Tests for the global database singleton pattern."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest
from iPhoto.cache.index_store import (
    get_global_repository,
    reset_global_repository,
    GLOBAL_INDEX_DB_NAME,
)
from iPhoto.config import WORK_DIR_NAME


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

    def test_global_repository_migrates_face_status_on_existing_db(self, tmp_path: Path) -> None:
        library_root = tmp_path / "Library"
        db_dir = library_root / WORK_DIR_NAME
        db_dir.mkdir(parents=True)
        db_path = db_dir / GLOBAL_INDEX_DB_NAME

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE assets (
                    rel TEXT PRIMARY KEY,
                    id TEXT,
                    dt TEXT,
                    media_type INTEGER,
                    mime TEXT,
                    live_role INTEGER DEFAULT 0
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO assets (rel, id, dt, media_type, mime, live_role)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("photo.jpg", "asset-photo", "2024-01-01T00:00:00Z", 0, "image/jpeg", 0),
                    ("clip.mp4", "asset-video", "2024-01-01T00:00:01Z", 1, "video/mp4", 0),
                ],
            )

        repo = get_global_repository(library_root)
        rows = repo.get_rows_by_ids(["asset-photo", "asset-video"])

        assert rows["asset-photo"]["face_status"] == "pending"
        assert rows["asset-video"]["face_status"] == "skipped"

    def test_face_status_helpers_round_trip(self, tmp_path: Path) -> None:
        repo = get_global_repository(tmp_path)
        repo.write_rows(
            [
                {"rel": "photo.jpg", "id": "asset-photo", "media_type": 0, "face_status": "pending"},
                {"rel": "clip.mp4", "id": "asset-video", "media_type": 1, "face_status": "skipped"},
            ]
        )

        pending_rows = list(repo.read_rows_by_face_status(["pending"]))
        assert [row["id"] for row in pending_rows] == ["asset-photo"]

        repo.update_face_status("asset-photo", "retry")
        repo.update_face_statuses(["asset-video"], "done")

        rows = repo.get_rows_by_ids(["asset-photo", "asset-video"])
        assert rows["asset-photo"]["face_status"] == "retry"
        assert rows["asset-video"]["face_status"] == "done"
        assert repo.count_by_face_status() == {"retry": 1, "done": 1}

    def test_merge_scan_rows_preserves_face_status_and_library_state_for_same_asset(
        self, tmp_path: Path
    ) -> None:
        repo = get_global_repository(tmp_path)
        repo.write_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-photo",
                    "media_type": 0,
                    "face_status": "done",
                    "is_favorite": 1,
                    "original_rel_path": "imports/photo.jpg",
                    "original_album_id": "trash-album",
                    "original_album_subpath": "trash/subpath",
                    "live_role": 1,
                    "live_partner_rel": "album/photo.mov",
                }
            ]
        )

        merged_rows = repo.merge_scan_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-photo",
                    "media_type": 0,
                    "bytes": 123,
                }
            ]
        )

        assert merged_rows[0]["face_status"] == "done"
        row = repo.get_rows_by_ids(["asset-photo"])["asset-photo"]
        assert row["face_status"] == "done"
        assert row["is_favorite"] == 1
        assert row["original_rel_path"] == "imports/photo.jpg"
        assert row["original_album_id"] == "trash-album"
        assert row["original_album_subpath"] == "trash/subpath"
        assert row["live_role"] == 1
        assert row["live_partner_rel"] == "album/photo.mov"

    def test_merge_scan_rows_resets_face_status_when_asset_id_changes(self, tmp_path: Path) -> None:
        repo = get_global_repository(tmp_path)
        repo.write_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-old",
                    "media_type": 0,
                    "face_status": "done",
                    "is_favorite": 1,
                }
            ]
        )

        merged_rows = repo.merge_scan_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-new",
                    "media_type": 0,
                    "bytes": 456,
                }
            ]
        )

        assert merged_rows[0]["face_status"] == "pending"
        row = repo.get_rows_by_ids(["asset-new"])["asset-new"]
        assert row["face_status"] == "pending"
        assert row["is_favorite"] == 1

    def test_merge_scan_rows_preserves_live_role_and_partner_rel_for_changed_asset_id(
        self, tmp_path: Path
    ) -> None:
        repo = get_global_repository(tmp_path)
        repo.write_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-old",
                    "media_type": 0,
                    "face_status": "skipped",
                    "live_role": 1,
                    "live_partner_rel": "album/photo.mov",
                }
            ]
        )

        merged_rows = repo.merge_scan_rows(
            [
                {
                    "rel": "album/photo.jpg",
                    "id": "asset-new",
                    "media_type": 0,
                }
            ]
        )

        assert merged_rows[0]["live_role"] == 1
        assert merged_rows[0]["live_partner_rel"] == "album/photo.mov"
        assert merged_rows[0]["face_status"] == "pending"


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

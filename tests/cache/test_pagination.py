"""Tests for cursor-based pagination in the asset repository."""
from __future__ import annotations

from pathlib import Path
import pytest
from src.iPhoto.cache.index_store import (
    get_global_repository,
    reset_global_repository,
)


@pytest.fixture(autouse=True)
def clean_global_state():
    """Reset global repository before and after each test."""
    reset_global_repository()
    yield
    reset_global_repository()


class TestReadGeometryOnlyPagination:
    """Tests for cursor-based pagination in read_geometry_only."""

    def test_limit_returns_exact_count(self, tmp_path: Path) -> None:
        """Test that passing limit returns exactly that many rows."""
        repo = get_global_repository(tmp_path)

        # Create test files on disk to satisfy the file existence check
        # performed by read_geometry_only (via build_asset_entry).
        for i in range(20):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert 20 test rows with dates
        rows = [
            {
                "rel": f"photo_{i:02d}.jpg",
                "id": str(i),
                "bytes": 100,
                "dt": f"2024-01-{20-i:02d}T12:00:00Z",
                "media_type": 0,
            }
            for i in range(20)
        ]
        repo.append_rows(rows)

        # Read with limit=10
        result = list(repo.read_geometry_only(limit=10))
        assert len(result) == 10

    def test_limit_none_returns_all(self, tmp_path: Path) -> None:
        """Test that limit=None returns all rows."""
        repo = get_global_repository(tmp_path)

        # Create test files
        for i in range(15):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert 15 test rows
        rows = [
            {
                "rel": f"photo_{i:02d}.jpg",
                "id": str(i),
                "bytes": 100,
                "dt": f"2024-01-{15-i:02d}T12:00:00Z",
                "media_type": 0,
            }
            for i in range(15)
        ]
        repo.append_rows(rows)

        # Read without limit
        result = list(repo.read_geometry_only(limit=None))
        assert len(result) == 15

    def test_cursor_returns_next_page(self, tmp_path: Path) -> None:
        """Test that cursor-based pagination returns the correct next page."""
        repo = get_global_repository(tmp_path)

        # Create test files
        for i in range(20):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert 20 test rows with distinct dates
        rows = [
            {
                "rel": f"photo_{i:02d}.jpg",
                "id": str(i),
                "bytes": 100,
                "dt": f"2024-01-{20-i:02d}T12:00:00Z",
                "media_type": 0,
            }
            for i in range(20)
        ]
        repo.append_rows(rows)

        # First page
        page1 = list(repo.read_geometry_only(limit=10))
        assert len(page1) == 10

        # Get cursor from last item of first page
        last_dt = page1[-1]["dt"]
        last_id = page1[-1]["id"]

        # Second page using cursor
        page2 = list(repo.read_geometry_only(
            limit=10,
            cursor_dt=last_dt,
            cursor_id=last_id,
        ))
        assert len(page2) == 10

        # Verify no overlap between pages
        page1_ids = {row["id"] for row in page1}
        page2_ids = {row["id"] for row in page2}
        assert page1_ids.isdisjoint(page2_ids), "Pages should not overlap"

    def test_cursor_exhausts_data(self, tmp_path: Path) -> None:
        """Test that cursor pagination correctly detects end of data."""
        repo = get_global_repository(tmp_path)

        # Create test files
        for i in range(15):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert 15 test rows
        rows = [
            {
                "rel": f"photo_{i:02d}.jpg",
                "id": str(i),
                "bytes": 100,
                "dt": f"2024-01-{15-i:02d}T12:00:00Z",
                "media_type": 0,
            }
            for i in range(15)
        ]
        repo.append_rows(rows)

        # First page of 10
        page1 = list(repo.read_geometry_only(limit=10))
        assert len(page1) == 10

        # Get cursor from last item
        last_dt = page1[-1]["dt"]
        last_id = page1[-1]["id"]

        # Second page should have only 5 items (less than limit)
        page2 = list(repo.read_geometry_only(
            limit=10,
            cursor_dt=last_dt,
            cursor_id=last_id,
        ))
        assert len(page2) == 5  # Only 5 remaining

        # Third page should be empty
        if page2:
            last_dt = page2[-1]["dt"]
            last_id = page2[-1]["id"]
            page3 = list(repo.read_geometry_only(
                limit=10,
                cursor_dt=last_dt,
                cursor_id=last_id,
            ))
            assert len(page3) == 0

    def test_pagination_preserves_order(self, tmp_path: Path) -> None:
        """Test that pagination maintains date descending order."""
        repo = get_global_repository(tmp_path)

        # Create test files
        for i in range(20):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert 20 test rows with distinct dates
        rows = [
            {
                "rel": f"photo_{i:02d}.jpg",
                "id": str(i),
                "bytes": 100,
                "dt": f"2024-01-{i+1:02d}T12:00:00Z",
                "media_type": 0,
            }
            for i in range(20)
        ]
        repo.append_rows(rows)

        all_results = []

        # Fetch all pages
        cursor_dt = None
        cursor_id = None
        while True:
            page = list(repo.read_geometry_only(
                limit=5,
                cursor_dt=cursor_dt,
                cursor_id=cursor_id,
            ))
            if not page:
                break
            all_results.extend(page)
            cursor_dt = page[-1]["dt"]
            cursor_id = page[-1]["id"]

        assert len(all_results) == 20

        # Verify descending order
        dates = [row["dt"] for row in all_results]
        assert dates == sorted(dates, reverse=True), "Results should be in descending date order"

    def test_pagination_with_filter(self, tmp_path: Path) -> None:
        """Test that pagination works correctly with filters applied."""
        repo = get_global_repository(tmp_path)

        # Create test files
        for i in range(20):
            (tmp_path / f"photo_{i:02d}.jpg").write_bytes(b"fake")
            (tmp_path / f"video_{i:02d}.mp4").write_bytes(b"fake")

        # Insert mixed image and video rows
        rows = []
        for i in range(20):
            rows.append({
                "rel": f"photo_{i:02d}.jpg",
                "id": f"img_{i}",
                "bytes": 100,
                "dt": f"2024-01-{i+1:02d}T12:00:00Z",
                "media_type": 0,  # Image
            })
            rows.append({
                "rel": f"video_{i:02d}.mp4",
                "id": f"vid_{i}",
                "bytes": 200,
                "dt": f"2024-01-{i+1:02d}T13:00:00Z",
                "media_type": 1,  # Video
            })
        repo.append_rows(rows)

        # Paginate with video filter
        all_videos = []
        cursor_dt = None
        cursor_id = None
        while True:
            page = list(repo.read_geometry_only(
                filter_params={"filter_mode": "videos"},
                limit=5,
                cursor_dt=cursor_dt,
                cursor_id=cursor_id,
            ))
            if not page:
                break
            all_videos.extend(page)
            cursor_dt = page[-1]["dt"]
            cursor_id = page[-1]["id"]

        # Should only get videos
        assert len(all_videos) == 20
        assert all(row["media_type"] == 1 for row in all_videos)

    def test_pagination_with_album_filter(self, tmp_path: Path) -> None:
        """Test that pagination works correctly with album filtering."""
        repo = get_global_repository(tmp_path)

        album_a = tmp_path / "AlbumA"
        album_b = tmp_path / "AlbumB"
        album_a.mkdir()
        album_b.mkdir()

        # Create test files
        for i in range(10):
            (album_a / f"photo_{i:02d}.jpg").write_bytes(b"fake")
            (album_b / f"photo_{i:02d}.jpg").write_bytes(b"fake")

        # Insert rows for two albums
        rows = []
        for i in range(10):
            rows.append({
                "rel": f"AlbumA/photo_{i:02d}.jpg",
                "id": f"a_{i}",
                "bytes": 100,
                "dt": f"2024-01-{i+1:02d}T12:00:00Z",
                "media_type": 0,
            })
            rows.append({
                "rel": f"AlbumB/photo_{i:02d}.jpg",
                "id": f"b_{i}",
                "bytes": 100,
                "dt": f"2024-01-{i+1:02d}T13:00:00Z",
                "media_type": 0,
            })
        repo.append_rows(rows)

        # Paginate AlbumA only
        album_a_results = []
        cursor_dt = None
        cursor_id = None
        while True:
            page = list(repo.read_geometry_only(
                album_path="AlbumA",
                include_subalbums=False,
                limit=3,
                cursor_dt=cursor_dt,
                cursor_id=cursor_id,
            ))
            if not page:
                break
            album_a_results.extend(page)
            cursor_dt = page[-1]["dt"]
            cursor_id = page[-1]["id"]

        assert len(album_a_results) == 10
        assert all(row["rel"].startswith("AlbumA/") for row in album_a_results)

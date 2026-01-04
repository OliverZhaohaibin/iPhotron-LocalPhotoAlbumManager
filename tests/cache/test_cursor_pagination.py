"""Tests for cursor-based pagination and K-Way merge functionality.

These tests verify:
1. PaginationCursor encoding/decoding
2. CursorPage structure and fetch_by_cursor API
3. KWayMergeProvider for multi-source aggregation
4. AssetIterator lazy loading
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.iPhoto.cache.index_store import (
    AssetIterator,
    CursorPage,
    IndexStore,
    KWayMergeProvider,
    PaginationCursor,
    create_all_photos_provider,
)


@pytest.fixture
def store(tmp_path: Path) -> IndexStore:
    """Create a fresh IndexStore for testing."""
    return IndexStore(tmp_path)


class TestPaginationCursor:
    """Tests for PaginationCursor encoding/decoding."""

    def test_encode_decode_roundtrip(self) -> None:
        """Verify that encoding and decoding produces the original cursor."""
        original = PaginationCursor(dt="2023-01-15T10:30:00Z", id="abc123")
        encoded = original.encode()
        decoded = PaginationCursor.decode(encoded)
        
        assert decoded.dt == original.dt
        assert decoded.id == original.id

    def test_encode_produces_url_safe_string(self) -> None:
        """Verify that encoded cursor is URL-safe."""
        cursor = PaginationCursor(dt="2023-01-15T10:30:00Z", id="test/path/file.jpg")
        encoded = cursor.encode()
        
        # URL-safe base64 uses only alphanumeric chars, '-', '_', and '='
        assert all(c.isalnum() or c in "-_=" for c in encoded)

    def test_decode_invalid_string_raises(self) -> None:
        """Verify that decoding invalid strings raises ValueError."""
        with pytest.raises(ValueError, match="Invalid cursor format"):
            PaginationCursor.decode("not-valid-base64!!!")

    def test_decode_missing_fields_raises(self) -> None:
        """Verify that decoding cursor without required fields raises ValueError."""
        import base64
        import json
        
        # Missing 'id' field
        bad_payload = base64.urlsafe_b64encode(
            json.dumps({"dt": "2023-01-01"}).encode()
        ).decode()
        
        with pytest.raises(ValueError, match="missing required fields"):
            PaginationCursor.decode(bad_payload)


class TestFetchByCursor:
    """Tests for fetch_by_cursor API."""

    def test_first_page_returns_cursor_page(self, store: IndexStore) -> None:
        """Verify first page returns a CursorPage with correct structure."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        page = store.fetch_by_cursor(limit=2)
        
        assert isinstance(page, CursorPage)
        assert len(page.items) == 2
        assert page.has_more is True
        assert page.next_cursor is not None

    def test_fetch_by_cursor_pagination(self, store: IndexStore) -> None:
        """Verify pagination works correctly through multiple pages."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
            {"rel": "d.jpg", "id": "4", "dt": "2023-01-04T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        # First page
        page1 = store.fetch_by_cursor(limit=2)
        assert len(page1.items) == 2
        assert page1.items[0]["rel"] == "d.jpg"  # Newest first
        assert page1.items[1]["rel"] == "c.jpg"
        assert page1.has_more is True
        
        # Second page using cursor
        page2 = store.fetch_by_cursor(cursor=page1.next_cursor, limit=2)
        assert len(page2.items) == 2
        assert page2.items[0]["rel"] == "b.jpg"
        assert page2.items[1]["rel"] == "a.jpg"
        assert page2.has_more is False
        assert page2.next_cursor is None

    def test_fetch_by_cursor_last_page(self, store: IndexStore) -> None:
        """Verify last page has has_more=False and no next_cursor."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        page = store.fetch_by_cursor(limit=10)
        
        assert len(page.items) == 1
        assert page.has_more is False
        assert page.next_cursor is None

    def test_fetch_by_cursor_empty_result(self, store: IndexStore) -> None:
        """Verify empty result when no data matches."""
        page = store.fetch_by_cursor(limit=10)
        
        assert len(page.items) == 0
        assert page.has_more is False
        assert page.next_cursor is None

    def test_fetch_by_cursor_with_album_filter(self, store: IndexStore) -> None:
        """Verify album filtering works with cursor pagination."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album1/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "Album2/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        page = store.fetch_by_cursor(album_path="Album1", limit=10)
        
        assert len(page.items) == 2
        rels = {item["rel"] for item in page.items}
        assert "Album1/a.jpg" in rels
        assert "Album1/b.jpg" in rels
        assert "Album2/c.jpg" not in rels

    def test_fetch_by_cursor_invalid_cursor_starts_fresh(self, store: IndexStore) -> None:
        """Verify invalid cursor causes start from beginning instead of error."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        # Should not raise, should start from beginning
        page = store.fetch_by_cursor(cursor="invalid-cursor", limit=10)
        
        assert len(page.items) == 1
        assert page.items[0]["rel"] == "a.jpg"


class TestFetchFirstViewport:
    """Tests for fetch_first_viewport API."""

    def test_returns_items_count_and_cursor(self, store: IndexStore) -> None:
        """Verify fetch_first_viewport returns correct tuple structure."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        items, total, cursor = store.fetch_first_viewport(limit=2)
        
        assert len(items) == 2
        assert total == 3
        assert cursor is not None

    def test_returns_lightweight_columns(self, store: IndexStore) -> None:
        """Verify fetch_first_viewport returns minimal columns for fast rendering."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z", "w": 1920, "h": 1080},
        ]
        store.write_rows(rows)
        
        items, _, _ = store.fetch_first_viewport(limit=10)
        
        assert len(items) == 1
        item = items[0]
        # Should include essential columns
        assert "id" in item
        assert "rel" in item
        assert "dt" in item
        assert "w" in item
        assert "h" in item

    def test_cursor_works_with_fetch_by_cursor(self, store: IndexStore) -> None:
        """Verify cursor from fetch_first_viewport works with fetch_by_cursor."""
        rows = [
            {"rel": f"photo{i}.jpg", "id": str(i), "dt": f"2023-01-{i:02d}T10:00:00Z"}
            for i in range(1, 6)
        ]
        store.write_rows(rows)
        
        # Get first viewport
        items, total, cursor = store.fetch_first_viewport(limit=2)
        assert len(items) == 2
        assert cursor is not None
        
        # Use cursor to get next page
        next_page = store.fetch_by_cursor(cursor=cursor, limit=10)
        assert len(next_page.items) == 3  # Remaining items

    def test_no_cursor_when_all_items_fit(self, store: IndexStore) -> None:
        """Verify no cursor when all items fit in the viewport."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        items, total, cursor = store.fetch_first_viewport(limit=10)
        
        assert len(items) == 1
        assert total == 1
        assert cursor is None

    def test_with_album_filter(self, store: IndexStore) -> None:
        """Verify album filtering works with fetch_first_viewport."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album1/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "Album2/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        items, total, _ = store.fetch_first_viewport(album_path="Album1", limit=10)
        
        assert len(items) == 2
        assert total == 2


class TestAssetIterator:
    """Tests for AssetIterator lazy loading."""

    def test_iterator_yields_all_items(self, store: IndexStore) -> None:
        """Verify iterator yields all items in order."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(
            album_path=None,
            repository=store,
            page_size=2,  # Small page size to test pagination
        )
        
        items = list(iterator)
        assert len(items) == 3
        # Should be in descending date order
        assert items[0]["rel"] == "c.jpg"
        assert items[1]["rel"] == "b.jpg"
        assert items[2]["rel"] == "a.jpg"

    def test_iterator_peek_does_not_consume(self, store: IndexStore) -> None:
        """Verify peek returns item without consuming it."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path=None, repository=store)
        
        # Peek should return item
        peeked = iterator.peek()
        assert peeked is not None
        assert peeked["rel"] == "a.jpg"
        
        # Next should return same item
        item = next(iterator)
        assert item["rel"] == "a.jpg"

    def test_iterator_reset(self, store: IndexStore) -> None:
        """Verify reset allows re-iteration."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path=None, repository=store)
        
        # Consume the iterator
        items1 = list(iterator)
        assert len(items1) == 1
        
        # Reset and iterate again
        iterator.reset()
        items2 = list(iterator)
        assert len(items2) == 1
        assert items2[0]["rel"] == items1[0]["rel"]


class TestKWayMergeProvider:
    """Tests for KWayMergeProvider multi-source aggregation."""

    def test_merge_single_source(self, store: IndexStore) -> None:
        """Verify merge works with single source."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album1/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path="Album1", repository=store)
        provider = KWayMergeProvider([iterator])
        
        items = list(provider)
        assert len(items) == 2
        assert items[0]["rel"] == "Album1/b.jpg"  # Newest first
        assert items[1]["rel"] == "Album1/a.jpg"

    def test_merge_multiple_sources_sorted(self, store: IndexStore) -> None:
        """Verify merge combines sources maintaining global sort order."""
        rows = [
            # Album1 has dates 1 and 3
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album1/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
            # Album2 has dates 2 and 4
            {"rel": "Album2/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "Album2/d.jpg", "id": "4", "dt": "2023-01-04T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iter1 = AssetIterator(album_path="Album1", repository=store)
        iter2 = AssetIterator(album_path="Album2", repository=store)
        provider = KWayMergeProvider([iter1, iter2])
        
        items = list(provider)
        assert len(items) == 4
        
        # Should be sorted by date descending across all sources
        assert items[0]["rel"] == "Album2/d.jpg"  # Jan 4
        assert items[1]["rel"] == "Album1/c.jpg"  # Jan 3
        assert items[2]["rel"] == "Album2/b.jpg"  # Jan 2
        assert items[3]["rel"] == "Album1/a.jpg"  # Jan 1

    def test_merge_with_empty_source(self, store: IndexStore) -> None:
        """Verify merge handles empty sources correctly."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iter1 = AssetIterator(album_path="Album1", repository=store)
        iter2 = AssetIterator(album_path="EmptyAlbum", repository=store)
        provider = KWayMergeProvider([iter1, iter2])
        
        items = list(provider)
        assert len(items) == 1
        assert items[0]["rel"] == "Album1/a.jpg"

    def test_merge_all_empty_sources(self, store: IndexStore) -> None:
        """Verify merge handles all empty sources."""
        iter1 = AssetIterator(album_path="Empty1", repository=store)
        iter2 = AssetIterator(album_path="Empty2", repository=store)
        provider = KWayMergeProvider([iter1, iter2])
        
        items = list(provider)
        assert len(items) == 0

    def test_fetch_page(self, store: IndexStore) -> None:
        """Verify fetch_page returns correct batch size."""
        rows = [
            {"rel": f"Album/photo{i}.jpg", "id": str(i), "dt": f"2023-01-{i:02d}T10:00:00Z"}
            for i in range(1, 11)
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path="Album", repository=store)
        provider = KWayMergeProvider([iterator], page_size=3)
        
        page1 = provider.fetch_page()
        assert len(page1) == 3
        
        page2 = provider.fetch_page()
        assert len(page2) == 3
        
        page3 = provider.fetch_page()
        assert len(page3) == 3
        
        page4 = provider.fetch_page()
        assert len(page4) == 1  # Only 1 remaining
        
        page5 = provider.fetch_page()
        assert len(page5) == 0  # Exhausted

    def test_has_more(self, store: IndexStore) -> None:
        """Verify has_more correctly indicates remaining items."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path=None, repository=store)
        provider = KWayMergeProvider([iterator])
        
        assert provider.has_more() is True
        
        next(provider)
        assert provider.has_more() is False

    def test_reset(self, store: IndexStore) -> None:
        """Verify reset allows re-iteration."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        iterator = AssetIterator(album_path=None, repository=store)
        provider = KWayMergeProvider([iterator])
        
        items1 = list(provider)
        assert len(items1) == 1
        
        provider.reset()
        items2 = list(provider)
        assert len(items2) == 1


class TestCreateAllPhotosProvider:
    """Tests for create_all_photos_provider factory function."""

    def test_create_with_no_albums(self, store: IndexStore) -> None:
        """Verify factory works with no album paths."""
        rows = [
            {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        provider = create_all_photos_provider(store, album_paths=[])
        items = list(provider)
        
        # When no albums specified, should return all
        assert len(items) == 1

    def test_create_with_specific_albums(self, store: IndexStore) -> None:
        """Verify factory creates providers for specified albums."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album2/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
            {"rel": "Album3/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        # Only request Album1 and Album2
        provider = create_all_photos_provider(
            store,
            album_paths=["Album1", "Album2"],
        )
        items = list(provider)
        
        assert len(items) == 2
        rels = {item["rel"] for item in items}
        assert "Album1/a.jpg" in rels
        assert "Album2/b.jpg" in rels
        assert "Album3/c.jpg" not in rels

    def test_create_discovers_albums_automatically(self, store: IndexStore) -> None:
        """Verify factory discovers all albums when album_paths is None."""
        rows = [
            {"rel": "Album1/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
            {"rel": "Album2/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
        ]
        store.write_rows(rows)
        
        provider = create_all_photos_provider(store, album_paths=None)
        items = list(provider)
        
        assert len(items) == 2

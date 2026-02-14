"""Tests for PaginatedAssetLoader — pure Python paginated data loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.application.services.paginated_loader import (
    DEFAULT_PAGE_SIZE,
    PageResult,
    PaginatedAssetLoader,
)
from iPhoto.domain.models.core import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(name: str) -> Asset:
    return Asset(
        id=name,
        album_id="album-1",
        path=Path(f"photos/{name}.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
    )


def _make_finder(total: int, page_size: int = DEFAULT_PAGE_SIZE):
    """Create a mock AssetFinder that returns *total* assets page by page."""
    all_assets = [_make_asset(f"asset-{i}") for i in range(total)]
    finder = Mock()
    finder.count_assets = Mock(return_value=total)

    def _find(query: AssetQuery):
        offset = query.offset
        limit = query.limit or page_size
        return all_assets[offset : offset + limit]

    finder.find_assets = Mock(side_effect=_find)
    return finder, all_assets


# ---------------------------------------------------------------------------
# PageResult
# ---------------------------------------------------------------------------


class TestPageResult:
    def test_has_more_true(self):
        r = PageResult(items=[], page=1, page_size=10, total_count=25)
        assert r.has_more is True

    def test_has_more_false_at_last_page(self):
        r = PageResult(items=[], page=3, page_size=10, total_count=25)
        assert r.has_more is False

    def test_has_more_false_when_exact(self):
        r = PageResult(items=[], page=2, page_size=10, total_count=20)
        assert r.has_more is False

    def test_total_pages(self):
        r = PageResult(items=[], page=1, page_size=10, total_count=25)
        assert r.total_pages == 3

    def test_total_pages_exact(self):
        r = PageResult(items=[], page=1, page_size=10, total_count=20)
        assert r.total_pages == 2

    def test_total_pages_zero(self):
        r = PageResult(items=[], page=1, page_size=10, total_count=0)
        assert r.total_pages == 0

    def test_defaults(self):
        r = PageResult()
        assert r.items == []
        assert r.page == 1
        assert r.page_size == DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# PaginatedAssetLoader — basic
# ---------------------------------------------------------------------------


class TestPaginatedAssetLoaderBasic:
    def test_default_page_size(self):
        finder = Mock()
        loader = PaginatedAssetLoader(finder)
        assert loader.page_size == DEFAULT_PAGE_SIZE

    def test_custom_page_size(self):
        finder = Mock()
        loader = PaginatedAssetLoader(finder, page_size=50)
        assert loader.page_size == 50

    def test_initial_state(self):
        finder = Mock()
        loader = PaginatedAssetLoader(finder)
        assert loader.items == []
        assert loader.current_page == 0
        assert loader.total_count == 0
        assert loader.has_more is False
        assert loader.total_pages == 0


# ---------------------------------------------------------------------------
# PaginatedAssetLoader — reset
# ---------------------------------------------------------------------------


class TestPaginatedAssetLoaderReset:
    def test_reset_loads_first_page(self):
        finder, all_assets = _make_finder(total=10, page_size=5)
        loader = PaginatedAssetLoader(finder, page_size=5)

        result = loader.reset(AssetQuery(album_id="album-1"))

        assert result.page == 1
        assert len(result.items) == 5
        assert loader.current_page == 1
        assert loader.total_count == 10
        assert loader.has_more is True
        assert loader.total_pages == 2

    def test_reset_with_empty_results(self):
        finder, _ = _make_finder(total=0)
        loader = PaginatedAssetLoader(finder, page_size=10)

        result = loader.reset(AssetQuery(album_id="album-1"))

        assert result.page == 1
        assert result.items == []
        assert loader.items == []
        assert loader.total_count == 0
        assert loader.has_more is False

    def test_reset_clears_previous_state(self):
        finder, _ = _make_finder(total=20, page_size=10)
        loader = PaginatedAssetLoader(finder, page_size=10)

        loader.reset(AssetQuery(album_id="album-1"))
        assert len(loader.items) == 10

        # Reset with new query
        finder.count_assets.return_value = 5
        finder.find_assets.side_effect = lambda q: [_make_asset(f"new-{i}") for i in range(5)]

        result = loader.reset(AssetQuery(album_id="album-2"))
        assert len(loader.items) == 5
        assert loader.current_page == 1

    def test_reset_all_fit_one_page(self):
        finder, _ = _make_finder(total=3, page_size=10)
        loader = PaginatedAssetLoader(finder, page_size=10)

        result = loader.reset(AssetQuery(album_id="album-1"))

        assert result.page == 1
        assert len(result.items) == 3
        assert loader.has_more is False
        assert loader.total_pages == 1


# ---------------------------------------------------------------------------
# PaginatedAssetLoader — load_next_page
# ---------------------------------------------------------------------------


class TestPaginatedAssetLoaderNextPage:
    def test_load_next_page_appends(self):
        finder, all_assets = _make_finder(total=15, page_size=5)
        loader = PaginatedAssetLoader(finder, page_size=5)

        loader.reset(AssetQuery(album_id="a"))
        assert len(loader.items) == 5

        result = loader.load_next_page()
        assert result.page == 2
        assert len(result.items) == 5
        assert len(loader.items) == 10  # accumulated

    def test_load_all_pages(self):
        finder, all_assets = _make_finder(total=12, page_size=5)
        loader = PaginatedAssetLoader(finder, page_size=5)

        loader.reset(AssetQuery(album_id="a"))
        while loader.has_more:
            loader.load_next_page()

        assert len(loader.items) == 12
        assert loader.current_page == 3
        assert loader.has_more is False

    def test_load_next_page_noop_at_end(self):
        finder, _ = _make_finder(total=5, page_size=10)
        loader = PaginatedAssetLoader(finder, page_size=10)

        loader.reset(AssetQuery(album_id="a"))
        result = loader.load_next_page()

        # Should be a no-op since all data fit in page 1
        assert result.items == []
        assert loader.current_page == 1

    def test_load_next_page_without_reset(self):
        finder = Mock()
        loader = PaginatedAssetLoader(finder)

        result = loader.load_next_page()
        assert result.items == []


# ---------------------------------------------------------------------------
# PaginatedAssetLoader — load_page
# ---------------------------------------------------------------------------


class TestPaginatedAssetLoaderPage:
    def test_load_specific_page(self):
        finder, all_assets = _make_finder(total=20, page_size=5)
        loader = PaginatedAssetLoader(finder, page_size=5)

        loader.reset(AssetQuery(album_id="a"))
        result = loader.load_page(3)

        assert result.page == 3
        assert len(result.items) == 5
        # Accumulated from page 1 + page 3 (skipping page 2)
        assert len(loader.items) == 10

    def test_query_uses_correct_offset(self):
        finder = Mock()
        finder.count_assets = Mock(return_value=100)
        finder.find_assets = Mock(return_value=[_make_asset("x")])

        loader = PaginatedAssetLoader(finder, page_size=20)
        loader.reset(AssetQuery(album_id="a"))

        # Check page 1 call
        first_call_query = finder.find_assets.call_args_list[0][0][0]
        assert first_call_query.offset == 0
        assert first_call_query.limit == 20

        # Load page 3
        loader.load_page(3)
        second_call_query = finder.find_assets.call_args_list[1][0][0]
        assert second_call_query.offset == 40  # (3-1) * 20
        assert second_call_query.limit == 20


# ---------------------------------------------------------------------------
# Integration: count query stripping
# ---------------------------------------------------------------------------


class TestCountQuery:
    def test_count_query_has_no_pagination(self):
        finder = Mock()
        finder.count_assets = Mock(return_value=0)
        finder.find_assets = Mock(return_value=[])

        loader = PaginatedAssetLoader(finder, page_size=10)
        query = AssetQuery(album_id="a", limit=999, offset=50)
        loader.reset(query)

        count_query = finder.count_assets.call_args[0][0]
        assert count_query.limit is None
        assert count_query.offset == 0
        # Original query fields preserved
        assert count_query.album_id == "a"

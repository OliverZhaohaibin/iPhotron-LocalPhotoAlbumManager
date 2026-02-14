"""Tests for PureAssetListViewModel — paginated loading path."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.application.services.paginated_loader import (
    PaginatedAssetLoader,
    PageResult,
)
from iPhoto.domain.models.core import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanCompletedEvent, AssetImportedEvent

# Direct file-level import to bypass the iPhoto.gui package chain which
# triggers PySide6/Qt imports that are unavailable in headless CI.
# PureAssetListViewModel itself is pure Python with no Qt dependency.
_SRC = Path(__file__).resolve().parents[1] / "src" / "iPhoto" / "gui" / "viewmodels"

def _load_module(name: str, filepath: Path):
    spec = importlib.util.spec_from_file_location(name, str(filepath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_signal_mod = _load_module("signal_mod", _SRC / "signal.py")
_base_mod = _load_module("base_mod", _SRC / "base.py")

# Patch sys.modules so pure_asset_list_viewmodel can resolve its relative imports
import sys
sys.modules.setdefault("iPhoto.gui.viewmodels.signal", _signal_mod)
sys.modules.setdefault("iPhoto.gui.viewmodels.base", _base_mod)

_vm_mod = _load_module("pure_asset_list_viewmodel", _SRC / "pure_asset_list_viewmodel.py")
PureAssetListViewModel = _vm_mod.PureAssetListViewModel


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


def _make_finder(total: int, page_size: int = 5):
    """Create a mock finder returning *total* assets page by page.

    *page_size* is used as a fallback limit when the query has no limit set.
    """
    all_assets = [_make_asset(f"a{i}") for i in range(total)]
    finder = Mock()
    finder.count_assets = Mock(return_value=total)

    def _find(query: AssetQuery):
        offset = query.offset
        limit = query.limit or page_size
        return all_assets[offset : offset + limit]

    finder.find_assets = Mock(side_effect=_find)
    return finder, all_assets


def _make_paginated_vm(total: int = 12, page_size: int = 5):
    """Build a VM wired to a PaginatedAssetLoader with a mock finder."""
    finder, all_assets = _make_finder(total, page_size)
    loader = PaginatedAssetLoader(finder, page_size=page_size)
    ds = Mock()  # not used in paginated mode
    tc = Mock()
    tc.get = Mock(return_value=b"thumb")
    bus = EventBus()
    vm = PureAssetListViewModel(
        data_source=ds,
        thumbnail_cache=tc,
        event_bus=bus,
        paginated_loader=loader,
    )
    return vm, finder, bus, all_assets


def _make_legacy_vm(assets=None):
    """Build a VM without a paginated loader (legacy path)."""
    ds = Mock()
    ds.load_assets = Mock(return_value=assets or [])
    tc = Mock()
    tc.get = Mock(return_value=b"thumb")
    bus = EventBus()
    vm = PureAssetListViewModel(
        data_source=ds,
        thumbnail_cache=tc,
        event_bus=bus,
    )
    return vm, ds, bus


# ---------------------------------------------------------------------------
# Legacy (non-paginated) path — backward compatibility
# ---------------------------------------------------------------------------


class TestLegacyPath:
    def test_load_album_still_works_without_loader(self):
        vm, ds, _ = _make_legacy_vm(["x", "y"])
        vm.load_album("album-1")

        assert vm.assets.value == ["x", "y"]
        assert vm.total_count.value == 2
        assert vm.current_page.value == 0
        assert vm.has_more_pages.value is False

    def test_load_next_page_is_noop(self):
        vm, ds, _ = _make_legacy_vm(["x"])
        vm.load_album("album-1")
        vm.load_next_page()
        assert vm.assets.value == ["x"]


# ---------------------------------------------------------------------------
# Paginated path — first page
# ---------------------------------------------------------------------------


class TestPaginatedFirstPage:
    def test_load_album_loads_first_page(self):
        vm, finder, _, all_assets = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")

        assert vm.current_page.value == 1
        assert len(vm.assets.value) == 5
        assert vm.total_count.value == 12
        assert vm.has_more_pages.value is True
        assert vm.total_pages.value == 3

    def test_load_album_small_album_no_more(self):
        vm, finder, _, all_assets = _make_paginated_vm(total=3, page_size=5)

        vm.load_album("album-1")

        assert vm.current_page.value == 1
        assert len(vm.assets.value) == 3
        assert vm.has_more_pages.value is False
        assert vm.total_pages.value == 1

    def test_load_album_empty(self):
        vm, finder, _, _ = _make_paginated_vm(total=0, page_size=5)

        vm.load_album("album-1")

        assert vm.assets.value == []
        assert vm.total_count.value == 0
        assert vm.has_more_pages.value is False

    def test_selection_cleared_on_load(self):
        vm, _, _, _ = _make_paginated_vm()
        vm.select(0)
        vm.load_album("album-1")
        assert vm.selected_indices.value == []


# ---------------------------------------------------------------------------
# Paginated path — load_next_page
# ---------------------------------------------------------------------------


class TestPaginatedNextPage:
    def test_load_next_page_accumulates(self):
        vm, _, _, _ = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")
        assert len(vm.assets.value) == 5

        vm.load_next_page()
        assert len(vm.assets.value) == 10
        assert vm.current_page.value == 2
        assert vm.has_more_pages.value is True

    def test_load_all_pages(self):
        vm, _, _, all_assets = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")
        while vm.has_more_pages.value:
            vm.load_next_page()

        assert len(vm.assets.value) == 12
        assert vm.has_more_pages.value is False
        assert vm.current_page.value == 3

    def test_load_next_page_noop_when_no_more(self):
        vm, _, _, _ = _make_paginated_vm(total=3, page_size=5)

        vm.load_album("album-1")
        assert vm.has_more_pages.value is False

        vm.load_next_page()
        assert len(vm.assets.value) == 3
        assert vm.current_page.value == 1

    def test_page_loaded_signal_emitted(self):
        vm, _, _, _ = _make_paginated_vm(total=10, page_size=5)
        pages_received = []
        vm.page_loaded.connect(lambda pg, items: pages_received.append((pg, len(items))))

        vm.load_album("album-1")
        vm.load_next_page()

        assert len(pages_received) == 1
        assert pages_received[0] == (2, 5)


# ---------------------------------------------------------------------------
# Paginated path — events
# ---------------------------------------------------------------------------


class TestPaginatedEvents:
    def test_scan_completed_reloads_from_page_1(self):
        vm, finder, bus, _ = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")
        vm.load_next_page()
        assert len(vm.assets.value) == 10

        # Simulate scan completed — should reload from page 1
        bus.publish(ScanCompletedEvent(album_id="album-1", asset_count=12))

        assert vm.current_page.value == 1
        assert len(vm.assets.value) == 5

    def test_scan_completed_different_album_ignored(self):
        vm, finder, bus, _ = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")
        prev_count = len(vm.assets.value)

        bus.publish(ScanCompletedEvent(album_id="other-album", asset_count=99))

        assert len(vm.assets.value) == prev_count

    def test_asset_imported_reloads(self):
        vm, finder, bus, _ = _make_paginated_vm(total=12, page_size=5)

        vm.load_album("album-1")
        bus.publish(AssetImportedEvent(album_id="album-1", asset_ids=["new"]))

        # Should have reloaded from page 1
        assert vm.current_page.value == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestPaginatedErrors:
    def test_error_on_load_album(self):
        finder = Mock()
        finder.count_assets = Mock(side_effect=RuntimeError("db error"))
        loader = PaginatedAssetLoader(finder, page_size=5)
        bus = EventBus()
        vm = PureAssetListViewModel(
            data_source=Mock(),
            thumbnail_cache=Mock(),
            event_bus=bus,
            paginated_loader=loader,
        )

        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))
        vm.load_album("album-1")

        assert len(errors) == 1
        assert "db error" in errors[0]
        assert vm.loading.value is False

    def test_error_on_load_next_page(self):
        vm, finder, _, _ = _make_paginated_vm(total=10, page_size=5)

        vm.load_album("album-1")
        # Make the next find_assets call fail
        finder.find_assets.side_effect = RuntimeError("page error")

        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))
        vm.load_next_page()

        assert len(errors) == 1
        assert "page error" in errors[0]
        assert vm.loading.value is False

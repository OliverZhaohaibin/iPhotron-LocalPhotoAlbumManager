"""Tests for incremental loading performance optimization.

This module tests the key components of the incremental loading solution:
1. Library model cache preservation when switching to physical albums
2. First screen fast loading via cursor-based pagination
3. Scroll-triggered lazy loading
4. Dual-proxy persistence architecture
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.facade import AppFacade
from src.iPhoto.gui.ui.controllers.data_manager import DataManager
from src.iPhoto.gui.ui.models.asset_model import AssetModel
from src.iPhoto.library.manager import LibraryManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Create a QApplication for testing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _write_manifest(path: Path, title: str) -> None:
    """Write a minimal album manifest file."""
    payload = {"schema": "iPhoto/album@1", "title": title, "filters": {}}
    (path / ".iphoto.album.json").write_text(json.dumps(payload), encoding="utf-8")


class TestLibraryModelCachePreservation:
    """Tests for Phase 1: Library Model Cache Preservation."""

    def test_library_model_preserved_on_album_switch(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that library model data is preserved when switching to physical album."""
        # Setup: Create library structure
        library_root = tmp_path / "Library"
        physical_album = library_root / "Album1"
        physical_album.mkdir(parents=True)
        _write_manifest(library_root, "Library")
        _write_manifest(physical_album, "Album1")

        # Create manager and facade
        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        # 1. Open library root (All Photos) - simulates loading aggregate view
        facade.open_album(library_root)
        qapp.processEvents()
        
        # Verify library model is active
        assert facade._active_model is facade._library_list_model
        library_model = facade._library_list_model
        
        # Simulate that library model has data
        # In real usage, this would be populated by the loader
        library_model._album_root = library_root

        # 2. Switch to physical album
        facade.open_album(physical_album)
        qapp.processEvents()
        
        # Verify album model is now active
        assert facade._active_model is facade._album_list_model
        
        # KEY TEST: Library model should retain its album root (cache preserved)
        assert library_model._album_root == library_root
        assert library_model.is_background_cache()

    def test_library_cache_cleared_on_reactivation(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that background cache flag is cleared when library model becomes active."""
        library_root = tmp_path / "Library"
        physical_album = library_root / "Album1"
        physical_album.mkdir(parents=True)
        _write_manifest(library_root, "Library")
        _write_manifest(physical_album, "Album1")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        # 1. Open library, switch to album (marks as cache), then back to library
        facade.open_album(library_root)
        qapp.processEvents()
        
        facade.open_album(physical_album)
        qapp.processEvents()
        assert facade._library_list_model.is_background_cache()
        
        facade.open_album(library_root)
        qapp.processEvents()
        
        # Background cache flag should be cleared
        assert not facade._library_list_model.is_background_cache()


class TestLazyLoadingController:
    """Tests for Phase 2 & 3: Lazy Loading Controller."""

    def test_lazy_loading_enabled_by_default(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that lazy loading is enabled by default."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        # Controller should have lazy loading enabled
        controller = facade.asset_list_model._controller
        assert controller._lazy_mode_enabled is True

    def test_lazy_loading_can_be_disabled(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that lazy loading can be disabled via configuration."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Disable lazy loading
        controller.enable_lazy_loading(False)
        assert controller._lazy_mode_enabled is False
        
        # Re-enable
        controller.enable_lazy_loading(True)
        assert controller._lazy_mode_enabled is True

    def test_should_use_lazy_loading_requires_album_root(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that should_use_lazy_loading returns False without album root."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        controller = facade.asset_list_model._controller
        
        # Without album root set
        controller._album_root = None
        assert controller.should_use_lazy_loading() is False


class TestBackgroundPrefetch:
    """Tests for background prefetch functionality."""

    def test_prefetch_timer_created_after_initial_page(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that prefetch timer is scheduled after initial page loads."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Simulate initial page loading completed
        controller._initial_page_loaded = True
        controller._all_data_loaded = False
        controller._schedule_background_prefetch()
        
        # Timer should be created
        assert controller._prefetch_timer is not None

    def test_prefetch_skipped_when_all_data_loaded(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that prefetch is skipped when all data is already loaded."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Mark all data as loaded
        controller._all_data_loaded = True
        controller._schedule_background_prefetch()
        
        # Timer should NOT be created when all data is loaded
        assert controller._prefetch_timer is None


class TestAssetListModelCacheState:
    """Tests for AssetListModel background cache state management."""

    def test_mark_as_background_cache(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test marking model as background cache."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        model = facade._library_list_model
        
        # Initially not a background cache
        assert not model.is_background_cache()
        
        # Mark as background cache
        model.mark_as_background_cache()
        assert model.is_background_cache()
        
        # Clear the state
        model.clear_background_cache_state()
        assert not model.is_background_cache()

    def test_is_valid_returns_true_with_album_root(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that is_valid returns True when album root is set."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        model = facade._library_list_model
        
        # Set album root
        model._album_root = library_root
        assert model.is_valid() is True
        
        # Clear album root
        model._album_root = None
        assert model.is_valid() is False


class TestPaginationState:
    """Tests for pagination state management."""

    def test_can_load_more_with_pending_data(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test can_load_more returns True when more data is available."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Initially can load more (DB not exhausted)
        controller._all_data_loaded = False
        controller._is_loading_page = False
        controller._album_root = library_root
        assert controller.can_load_more() is True

    def test_can_load_more_false_when_exhausted(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test can_load_more returns False when all data is loaded."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Mark as exhausted
        controller._all_data_loaded = True
        controller._k_way_stream.reset()  # Empty stream
        assert controller.can_load_more() is False

    def test_can_load_more_false_while_loading(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test can_load_more returns False while a page is loading."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)

        facade.open_album(library_root)
        qapp.processEvents()

        controller = facade.asset_list_model._controller
        
        # Simulate page loading in progress
        controller._all_data_loaded = False
        controller._is_loading_page = True
        controller._album_root = library_root
        assert controller.can_load_more() is False


class TestDualProxyArchitecture:
    """Tests for Solution 1: Dual-Proxy Persistence Architecture."""

    def test_data_manager_creates_separate_proxies(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that DataManager creates separate proxy instances for library and album."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)
        
        # Create a mock window
        mock_window = MagicMock()
        
        data_manager = DataManager(mock_window, facade)
        
        # Verify separate proxy instances exist
        assert data_manager._library_proxy is not None
        assert data_manager._album_proxy is not None
        assert data_manager._library_proxy is not data_manager._album_proxy
        
        # Verify library proxy is attached to library model
        assert data_manager._library_proxy.source_model() is facade._library_list_model
        
        # Verify album proxy is attached to album model
        assert data_manager._album_proxy.source_model() is facade._album_list_model

    def test_active_proxy_switches_on_context_change(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that active proxy switches when activeModelChanged is emitted."""
        library_root = tmp_path / "Library"
        physical_album = library_root / "Album1"
        physical_album.mkdir(parents=True)
        _write_manifest(library_root, "Library")
        _write_manifest(physical_album, "Album1")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)
        
        mock_window = MagicMock()
        data_manager = DataManager(mock_window, facade)
        
        # Initially should be library proxy
        assert data_manager._active_proxy is data_manager._library_proxy
        
        # Simulate switching to album model
        data_manager._on_active_model_changed(facade._album_list_model)
        assert data_manager._active_proxy is data_manager._album_proxy
        
        # Simulate switching back to library model
        data_manager._on_active_model_changed(facade._library_list_model)
        assert data_manager._active_proxy is data_manager._library_proxy

    def test_proxy_switch_is_no_op_for_same_context(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that switching to the same context is a no-op."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)
        
        mock_window = MagicMock()
        data_manager = DataManager(mock_window, facade)
        
        original_proxy = data_manager._active_proxy
        
        # Switching to same context should be no-op
        data_manager._on_active_model_changed(facade._library_list_model)
        assert data_manager._active_proxy is original_proxy

    def test_asset_model_disables_dynamic_sort_filter(
        self, tmp_path: Path, qapp: QApplication
    ) -> None:
        """Test that AssetModel disables dynamic sort filter for performance."""
        library_root = tmp_path / "Library"
        library_root.mkdir(parents=True)
        _write_manifest(library_root, "Library")

        manager = LibraryManager()
        manager.bind_path(library_root)

        facade = AppFacade()
        facade.bind_library(manager)
        
        proxy = AssetModel(facade)
        
        # Dynamic sort filter should be disabled for performance
        assert proxy.dynamicSortFilter() is False

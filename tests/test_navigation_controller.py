"""Regression tests for :mod:`iPhoto.gui.ui.controllers.navigation_controller`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

import os
import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for navigation controller tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets not available",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication, QStackedWidget, QStatusBar, QWidget, QMainWindow

from src.iPhoto.gui.ui.controllers.navigation_controller import NavigationController
from src.iPhoto.gui.ui.controllers.view_controller import ViewController


@pytest.fixture
def qapp() -> QApplication:
    """Return the shared :class:`QApplication` instance for the test suite."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _SpyViewController(ViewController):
    """Spy variant that records gallery/detail transitions for assertions."""

    def __init__(self) -> None:
        self._stack = QStackedWidget()
        self._gallery = QWidget()
        self._detail = QWidget()
        self._edit = QWidget()
        self._stack.addWidget(self._gallery)
        self._stack.addWidget(self._detail)
        self._stack.addWidget(self._edit)
        self.gallery_calls = 0
        self.detail_calls = 0
        super().__init__(self._stack, self._gallery, self._detail, self._edit)

    def show_gallery_view(self) -> None:  # type: ignore[override]
        self.gallery_calls += 1
        super().show_gallery_view()

    def show_detail_view(self) -> None:  # type: ignore[override]
        self.detail_calls += 1
        super().show_detail_view()


class _StubFacade:
    """Minimal facade exposing the bits required by :class:`NavigationController`."""

    def __init__(self) -> None:
        self.current_album: Optional[SimpleNamespace] = None
        self.open_requests: list[Path] = []
        # Track whether the library model has cached data for optimization tests
        self._has_cached_data: bool = False
        # Track switch_to_library_model calls
        self.library_model_switch_calls: list[tuple[Path, str, Optional[str]]] = []

    def open_album(self, root: Path) -> SimpleNamespace:
        self.open_requests.append(root)
        album = SimpleNamespace(root=root.resolve(), manifest={"title": root.name})
        self.current_album = album
        return album

    def library_model_has_cached_data(self) -> bool:
        """Return ``True`` when the library model has valid cached data."""
        return self._has_cached_data

    def switch_to_library_model_for_static_collection(
        self,
        library_root: Path,
        title: str,
        filter_mode: Optional[str] = None,
    ) -> bool:
        """Switch to library model without reloading data."""
        self.library_model_switch_calls.append((library_root, title, filter_mode))
        if self._has_cached_data:
            # Simulate successful switch - update current_album
            album = SimpleNamespace(root=library_root.resolve(), manifest={"title": title})
            self.current_album = album
            return True
        return False


class _StubAssetModel:
    """Track calls to ``set_filter_mode`` for sanity checks in tests."""

    def __init__(self) -> None:
        self.filter_mode = object()
        self.sort_calls = 0

    def set_filter_mode(self, mode: Optional[str]) -> None:
        self.filter_mode = mode

    def rowCount(self) -> int:  # pragma: no cover - unused but required by controller
        return 0

    def ensure_chronological_order(self) -> None:
        self.sort_calls += 1


class _StubSidebar:
    """Sidebar stand-in that optionally calls back when selection changes."""

    def __init__(self) -> None:
        self._current_path: Optional[Path] = None
        self._callback: Optional[Callable[[Path], None]] = None

    def set_callback(self, callback: Callable[[Path], None]) -> None:
        self._callback = callback

    def select_path(self, path: Path) -> None:
        already_selected = self._current_path == path
        self._current_path = path
        if not already_selected and self._callback is not None:
            self._callback(path)

    def select_static_node(self, _title: str) -> None:
        # The refresh logic is exercised through ``select_path`` in the tests,
        # so there is nothing extra to do for static nodes here.
        return


class _StubContext:
    """Capture the last album remembered by :class:`NavigationController`."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = library_root
        self.facade = None
        self.library = SimpleNamespace(
            root=lambda: self._library_root,
            ensure_deleted_directory=lambda: None,
            deleted_directory=lambda: None,
            cleanup_deleted_index=lambda: None,
        )
        self.remembered: Optional[Path] = None

    def remember_album(self, root: Path) -> None:
        self.remembered = root


class _StubDialog:
    """Placeholder dialog controller used to satisfy the constructor."""

    def bind_library_dialog(self) -> None:  # pragma: no cover - not exercised
        return


def test_open_album_skips_gallery_on_refresh(tmp_path: Path, qapp: QApplication) -> None:
    """Reopening the active album via sidebar sync must not reset the gallery."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    album_path = tmp_path / "album"
    album_path.mkdir()

    # Simulate the user selecting the album for the first time.  This should
    # present the gallery view so the model can populate cleanly.
    controller.open_album(album_path)
    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is False
    assert facade.open_requests == [album_path]

    # ``handle_album_opened`` drives the sidebar selection update in the real
    # application.  Wire the stub to mimic the sidebar re-emitting
    # ``albumSelected`` so ``open_album`` receives a second call while the sync
    # flag is active.
    sidebar.set_callback(controller.open_album)
    controller.handle_album_opened(album_path)

    # The refresh triggered by the sidebar must not reset the gallery view and
    # should advertise itself via ``consume_last_open_refresh``.  Crucially, the
    # facade must not be asked to reload the already-open album again.
    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is True
    assert facade.open_requests == [album_path]


def test_open_album_refresh_detected_without_sidebar_sync(
    tmp_path: Path, qapp: QApplication
) -> None:
    """A second ``open_album`` call for the same path must be treated as a refresh."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    album_path = tmp_path / "album"
    album_path.mkdir()

    # First call represents a genuine navigation and should reset the gallery.
    controller.open_album(album_path)
    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is False
    assert facade.open_requests == [album_path]

    # The follow-up call mimics a filesystem watcher re-selecting the already
    # open album without going through ``handle_album_opened``.  The controller
    # should classify it as a refresh so the UI stays on the detail page.
    controller.open_album(album_path)

    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is True
    assert facade.open_requests == [album_path]


def test_open_all_photos_applies_chronological_sort(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Switching to "All Photos" must enforce chronological ordering."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    tmp_path.mkdir(exist_ok=True)
    controller.open_all_photos()

    assert asset_model.sort_calls == 1
    assert asset_model.filter_mode is None
    assert facade.open_requests == [tmp_path]

def test_open_static_collection_refresh_skips_gallery(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Reopening a static collection must be treated as a refresh."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    tmp_path.mkdir(exist_ok=True)

    # First open "All Photos". Should reset to gallery.
    controller.open_all_photos()
    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is False
    assert len(facade.open_requests) == 1

    # Re-open "All Photos". Should be treated as refresh and NOT reset gallery.
    controller.open_all_photos()
    assert view_controller.gallery_calls == 1  # Should NOT increment
    assert controller.consume_last_open_refresh() is True
    assert len(facade.open_requests) == 1


def test_switch_static_collection_uses_optimized_path(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Switching between static collections on same root must skip album reload."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    tmp_path.mkdir(exist_ok=True)

    # 1. Initial Load: "All Photos"
    # This must trigger open_album as no album is currently open
    controller.open_all_photos()
    assert len(facade.open_requests) == 1
    assert controller.static_selection() == "All Photos"
    assert asset_model.filter_mode is None

    # 2. Optimized Switch: "Videos"
    # This should reuse the existing root and skip open_album
    controller.open_static_node("Videos")

    # Assertions
    assert len(facade.open_requests) == 1  # Still 1, meaning no new load
    assert controller.static_selection() == "Videos"
    assert asset_model.filter_mode == "videos"


def test_open_album_from_dashboard_force_navigation(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Navigating to the current album from the dashboard must switch views."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    album_path = tmp_path / "album"
    album_path.mkdir()

    # 1. Open album first
    controller.open_album(album_path)
    assert view_controller.gallery_calls == 1

    # 2. Go to Dashboard
    controller.open_albums_dashboard()
    assert controller.static_selection() == "Albums"

    # 3. Open same album again (simulate card click)
    controller.open_album(album_path)

    # Expectation: It should NOT be treated as refresh, and Gallery should be shown again.
    assert view_controller.gallery_calls == 2
    assert controller.consume_last_open_refresh() is False
    assert controller.static_selection() is None


def test_open_recently_deleted_refresh_skips_gallery(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Reopening 'Recently Deleted' must be treated as a refresh."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    # Mock ensure_deleted_directory
    deleted_dir = tmp_path / "deleted"
    deleted_dir.mkdir()
    context.library.ensure_deleted_directory = lambda: deleted_dir
    context.library.deleted_directory = lambda: deleted_dir
    context.library.cleanup_deleted_index = lambda: None

    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    tmp_path.mkdir(exist_ok=True)

    # First open.
    controller.open_recently_deleted()
    assert view_controller.gallery_calls == 1
    assert controller.consume_last_open_refresh() is False
    assert len(facade.open_requests) == 1
    assert facade.open_requests[0] == deleted_dir

    # Refresh.
    controller.open_recently_deleted()
    assert view_controller.gallery_calls == 1  # Should NOT increment
    assert controller.consume_last_open_refresh() is True
    assert len(facade.open_requests) == 1


def test_physical_album_to_all_photos_uses_cached_data_optimization(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Switching from physical album to All Photos uses optimized path when cached."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    album_path = tmp_path / "album"
    album_path.mkdir()

    # 1. Open a physical album
    controller.open_album(album_path)
    assert len(facade.open_requests) == 1
    assert facade.current_album.root == album_path.resolve()

    # 2. Simulate that library model has cached data
    facade._has_cached_data = True

    # 3. Switch to "All Photos"
    # This should use the optimized path (switch_to_library_model_for_static_collection)
    # instead of open_album
    controller.open_all_photos()

    # Assertions: open_album should NOT be called again (still 1 request)
    # switch_to_library_model_for_static_collection should have been called
    assert len(facade.open_requests) == 1  # No new open_album call
    assert len(facade.library_model_switch_calls) == 1
    assert facade.library_model_switch_calls[0][0] == tmp_path  # library root
    assert facade.library_model_switch_calls[0][1] == "All Photos"
    assert facade.library_model_switch_calls[0][2] is None  # filter_mode for All Photos
    assert controller.static_selection() == "All Photos"
    # Note: filter_mode on asset_model is NOT set directly now - the facade handles it
    # So we only verify the facade was called with the correct filter_mode


def test_physical_album_to_all_photos_fallback_without_cached_data(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Switching from physical album to All Photos uses standard path when no cache."""

    facade = _StubFacade()
    context = _StubContext(tmp_path)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    album_path = tmp_path / "album"
    album_path.mkdir()

    # 1. Open a physical album
    controller.open_album(album_path)
    assert len(facade.open_requests) == 1
    assert facade.current_album.root == album_path.resolve()

    # 2. Library model does NOT have cached data (default)
    assert facade._has_cached_data is False

    # 3. Switch to "All Photos"
    # This should use the standard path (open_album) because no cached data
    controller.open_all_photos()

    # Assertions: open_album SHOULD be called (total 2 requests)
    assert len(facade.open_requests) == 2
    assert facade.open_requests[1] == tmp_path  # library root
    assert controller.static_selection() == "All Photos"

def test_rebind_library_refreshes_all_photos(tmp_path: Path, qapp: QApplication) -> None:
    """Rebinding the library root must force a refresh even if on All Photos."""

    facade = _StubFacade()
    root_a = tmp_path / "root_a"
    root_a.mkdir()
    root_b = tmp_path / "root_b"
    root_b.mkdir()

    context = _StubContext(root_a)
    context.facade = facade
    asset_model = _StubAssetModel()
    sidebar = _StubSidebar()
    status_bar = QStatusBar()
    dialog = _StubDialog()
    view_controller = _SpyViewController()

    controller = NavigationController(
        context,
        facade,
        asset_model,
        sidebar,
        status_bar,
        dialog,  # type: ignore[arg-type]
        view_controller,
        QMainWindow(),
    )

    # 1. Open "All Photos" with Root A
    controller.open_all_photos()
    assert len(facade.open_requests) == 1
    assert facade.open_requests[0] == root_a
    assert controller.static_selection() == "All Photos"

    # 2. Simulate rebind to Root B
    # Updating the private attribute works because the stub uses a lambda closure
    context._library_root = root_b

    # 3. Trigger open_all_photos again (simulating sidebar re-selection)
    controller.open_all_photos()

    # Assert Root B was opened
    assert len(facade.open_requests) == 2
    assert facade.open_requests[1] == root_b

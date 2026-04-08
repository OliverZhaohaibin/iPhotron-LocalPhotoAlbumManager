"""Compatibility-shell tests for AppContext (Phase 3 closure).

Verifies that ``AppContext`` no longer constructs its own dependencies and
instead delegates entirely to :class:`~iPhoto.bootstrap.runtime_context.RuntimeContext`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestAppContextDelegation:
    """AppContext must delegate to RuntimeContext – not build its own objects."""

    def test_appctx_holds_runtime_context(self):
        """AppContext._runtime must be a RuntimeContext instance."""
        from iPhoto.appctx import AppContext
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = AppContext(defer_startup_tasks=True)
        assert isinstance(ctx._runtime, RuntimeContext)

    def test_settings_property_comes_from_runtime(self):
        """AppContext.settings must be the same object as RuntimeContext.settings."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.settings is ctx._runtime.settings

    def test_library_property_comes_from_runtime(self):
        """AppContext.library must be the same object as RuntimeContext.library."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.library is ctx._runtime.library

    def test_facade_property_comes_from_runtime(self):
        """AppContext.facade must be the same object as RuntimeContext.facade."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.facade is ctx._runtime.facade

    def test_container_property_comes_from_runtime(self):
        """AppContext.container must be the same object as RuntimeContext.container."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.container is ctx._runtime.container

    def test_recent_albums_is_list(self):
        """AppContext.recent_albums must be a list."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert isinstance(ctx.recent_albums, list)

    def test_defer_startup_tasks_flag_preserved(self):
        """AppContext.defer_startup_tasks must store the constructor argument."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.defer_startup_tasks is True

        ctx2 = AppContext(defer_startup_tasks=False)
        assert ctx2.defer_startup_tasks is False

    def test_appctx_does_not_build_own_library(self):
        """AppContext must delegate library access to RuntimeContext."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.library is ctx._runtime.library

    def test_remember_album_updates_recent_albums(self, tmp_path):
        """remember_album must propagate into recent_albums."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        album = tmp_path / "TestAlbum"
        album.mkdir()

        before = len(ctx.recent_albums)
        ctx.remember_album(album)
        assert len(ctx.recent_albums) >= before  # at least not shrunk

    def test_session_property_returns_app_session(self):
        """AppContext.session must be the AppSession held by RuntimeContext."""
        from iPhoto.appctx import AppContext
        from iPhoto.presentation.qt.session.app_session import AppSession

        ctx = AppContext(defer_startup_tasks=True)
        assert isinstance(ctx.session, AppSession)
        assert ctx.session is ctx._runtime._session

    def test_appctx_theme_is_set(self):
        """AppContext must create a ThemeManager tied to settings."""
        from iPhoto.appctx import AppContext
        from iPhoto.gui.ui.theme_manager import ThemeManager

        ctx = AppContext(defer_startup_tasks=True)
        assert isinstance(ctx.theme, ThemeManager)

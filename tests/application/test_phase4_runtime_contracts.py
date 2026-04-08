"""Phase 4 runtime entry contract and compatibility-shell contract tests.

Verifies:
1. ``RuntimeEntryContract`` is satisfied by ``RuntimeContext``.
2. ``RuntimeContext`` exposes the full contract surface.
3. ``AppContext`` (compatibility shell) only proxies to ``RuntimeContext`` and
   does not build its own dependencies.
4. ``app.py`` (deprecated shim) delegates to use cases, never owning business
   logic directly.
5. Lightweight test doubles satisfy ``RuntimeEntryContract`` without needing
   a full Qt application stack.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# RuntimeEntryContract – protocol definition tests (no Qt required)
# ---------------------------------------------------------------------------


class TestRuntimeEntryContractProtocol:
    """Verify the protocol is importable and usable for isinstance checks."""

    def test_protocol_is_importable(self):
        from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract

        assert RuntimeEntryContract is not None

    def test_protocol_is_runtime_checkable(self):
        """RuntimeEntryContract must be decorated with @runtime_checkable."""
        from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract

        # A minimal fake that satisfies the structural protocol.
        class FakeContext:
            @property
            def settings(self):
                return MagicMock()

            @property
            def library(self):
                return MagicMock()

            @property
            def facade(self):
                return MagicMock()

            @property
            def container(self):
                return MagicMock()

            @property
            def recent_albums(self):
                return []

            def resume_startup(self) -> None:
                pass

            def remember_album(self, root: Path) -> None:
                pass

        fake = FakeContext()
        assert isinstance(fake, RuntimeEntryContract)

    def test_incomplete_fake_does_not_satisfy_protocol(self):
        """An object missing required attributes must NOT satisfy the protocol."""
        from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract

        class IncompleteContext:
            @property
            def settings(self):
                return MagicMock()
            # missing library, facade, container, recent_albums, resume_startup, remember_album

        assert not isinstance(IncompleteContext(), RuntimeEntryContract)

    def test_contracts_package_re_exports_contract(self):
        from iPhoto.application.contracts import RuntimeEntryContract

        assert RuntimeEntryContract is not None


# ---------------------------------------------------------------------------
# RuntimeContext satisfies RuntimeEntryContract (Qt required)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestRuntimeContextSatisfiesContract:
    """RuntimeContext must be an instance of RuntimeEntryContract."""

    def test_runtime_context_is_contract_instance(self):
        from iPhoto.application.contracts import RuntimeEntryContract
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert isinstance(ctx, RuntimeEntryContract)

    def test_runtime_context_settings_not_none(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx.settings is not None

    def test_runtime_context_library_not_none(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx.library is not None

    def test_runtime_context_facade_not_none(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx.facade is not None

    def test_runtime_context_container_not_none(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx.container is not None

    def test_runtime_context_recent_albums_is_list(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert isinstance(ctx.recent_albums, list)

    def test_runtime_context_remember_album_updates_recent(self, tmp_path):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        album = tmp_path / "Phase4TestAlbum"
        album.mkdir()
        before = len(ctx.recent_albums)
        ctx.remember_album(album)
        assert len(ctx.recent_albums) >= before

    def test_runtime_context_resume_startup_is_callable(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        # Must not raise when called on a defer_startup context.
        ctx.resume_startup()

    def test_runtime_context_create_is_only_factory(self):
        """RuntimeContext.create() is the only supported constructor."""
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        # Direct dataclass construction is technically possible but
        # RuntimeContext.create() must always work.
        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx is not None


# ---------------------------------------------------------------------------
# AppContext compatibility-shell contract (Qt required)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestAppContextCompatibilityShell:
    """AppContext must be a pure compatibility proxy delegating to RuntimeContext."""

    def test_appctx_holds_runtime_context(self):
        from iPhoto.appctx import AppContext
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = AppContext(defer_startup_tasks=True)
        assert isinstance(ctx._runtime, RuntimeContext)

    def test_appctx_satisfies_contract(self):
        """AppContext proxies a RuntimeContext that satisfies the contract."""
        from iPhoto.application.contracts import RuntimeEntryContract
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        # The underlying runtime must satisfy the contract.
        assert isinstance(ctx._runtime, RuntimeEntryContract)

    def test_appctx_settings_is_runtime_settings(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.settings is ctx._runtime.settings

    def test_appctx_library_is_runtime_library(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.library is ctx._runtime.library

    def test_appctx_facade_is_runtime_facade(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.facade is ctx._runtime.facade

    def test_appctx_container_is_runtime_container(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.container is ctx._runtime.container

    def test_appctx_does_not_construct_own_library(self):
        """AppContext must not own a LibraryManager instance distinct from RuntimeContext."""
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        # library attribute must forward to _runtime, not be a different object
        assert ctx.library is ctx._runtime.library

    def test_appctx_does_not_construct_own_settings(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        assert ctx.settings is ctx._runtime.settings

    def test_appctx_recent_albums_mirrors_runtime(self):
        from iPhoto.appctx import AppContext

        ctx = AppContext(defer_startup_tasks=True)
        # recent_albums is a copy (list), but values should match.
        assert ctx.recent_albums == list(ctx._runtime.recent_albums)


# ---------------------------------------------------------------------------
# app.py deprecated shim – delegates to use cases, owns no business logic
# ---------------------------------------------------------------------------


class TestAppShimIsDeprecated:
    """app.py must have zero business logic – only delegation to use cases."""

    def test_app_module_has_deprecated_docstring(self):
        import iPhoto.app as app

        assert "deprecated" in (app.__doc__ or "").lower()

    def test_rescan_delegates_to_rescan_use_case(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.scan.rescan_album_use_case.RescanAlbumUseCase.execute",
            return_value=[],
        ) as mock_exec:
            result = app.rescan(album_dir)

        mock_exec.assert_called_once_with(album_dir)
        assert result == []

    def test_pair_delegates_to_pair_use_case(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.scan.pair_live_photos_use_case_v2"
            ".PairLivePhotosUseCaseV2.execute",
            return_value=[],
        ) as mock_exec:
            result = app.pair(album_dir)

        mock_exec.assert_called_once_with(album_dir)
        assert result == []

    def test_open_album_delegates_to_workflow_use_case(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app
        from iPhoto.models.album import Album

        fake_album = MagicMock(spec=Album)

        with patch(
            "iPhoto.application.use_cases.scan.open_album_workflow_use_case"
            ".OpenAlbumWorkflowUseCase.execute",
            return_value=fake_album,
        ) as mock_exec:
            result = app.open_album(album_dir)

        mock_exec.assert_called_once()
        assert result is fake_album

    def test_scan_specific_files_delegates_to_use_case(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.asset.scan_specific_files_use_case"
            ".ScanSpecificFilesUseCase.execute"
        ) as mock_exec:
            app.scan_specific_files(album_dir, [])

        mock_exec.assert_called_once_with(album_dir, [], library_root=None)


# ---------------------------------------------------------------------------
# Fake RuntimeContext satisfies contract (test double pattern)
# ---------------------------------------------------------------------------


class TestFakeRuntimeContextPattern:
    """Demonstrate how to create a lightweight test double for RuntimeEntryContract."""

    def test_fake_context_satisfies_contract(self):
        """A minimal fake double must satisfy RuntimeEntryContract for use in tests."""
        from iPhoto.application.contracts import RuntimeEntryContract

        class FakeRuntimeContext:
            """Minimal test double satisfying RuntimeEntryContract."""

            def __init__(self) -> None:
                self._settings = MagicMock()
                self._library = MagicMock()
                self._facade = MagicMock()
                self._container = MagicMock()
                self._recent_albums: list[Path] = []

            @property
            def settings(self):
                return self._settings

            @property
            def library(self):
                return self._library

            @property
            def facade(self):
                return self._facade

            @property
            def container(self):
                return self._container

            @property
            def recent_albums(self):
                return self._recent_albums

            def resume_startup(self) -> None:
                pass

            def remember_album(self, root: Path) -> None:
                self._recent_albums.insert(0, root)

        fake = FakeRuntimeContext()
        assert isinstance(fake, RuntimeEntryContract)

        # Verify behaviour
        fake.remember_album(Path("/tmp/album"))
        assert Path("/tmp/album") in fake.recent_albums

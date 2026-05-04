from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.bootstrap.library_session import LibrarySession


def test_library_session_binds_runtime_and_exposes_ports(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    state = Mock()

    session = LibrarySession(
        tmp_path,
        asset_runtime=runtime,
        state_repository=state,
    )

    runtime.bind_library_root.assert_called_once_with(tmp_path)
    assert session.assets is runtime.assets
    assert session.thumbnails is runtime.thumbnail_service
    assert session.state is state
    assert session.asset_state is not None
    assert session.album_metadata is not None
    assert session.album_metadata.library_root == tmp_path
    assert session.asset_queries is not None
    assert session.asset_queries.library_root == tmp_path
    assert session.scans is not None
    assert session.scans.library_root == tmp_path
    assert session.asset_lifecycle is not None
    assert session.asset_lifecycle.library_root == tmp_path
    assert session.asset_operations is not None
    assert session.asset_operations.library_root == tmp_path
    assert session.asset_operations.lifecycle_service is session.asset_lifecycle
    assert session.people is not None
    assert session.people.library_root() == tmp_path
    assert session.edit is not None
    runtime.bind_edit_service.assert_called_once_with(session.edit)


def test_library_session_shutdown_delegates_to_asset_runtime(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, state_repository=Mock())

    session.shutdown()

    runtime.bind_edit_service.assert_any_call(None)
    runtime.shutdown.assert_called_once()

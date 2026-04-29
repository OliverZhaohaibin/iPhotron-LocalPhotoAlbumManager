from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.bootstrap.library_session import LibrarySession


def test_library_session_binds_runtime_and_exposes_ports(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    state = Mock()

    session = LibrarySession(
        tmp_path,
        asset_runtime=runtime,
        state_repository=state,
    )

    runtime.bind_library_root.assert_called_once_with(tmp_path)
    assert session.assets is runtime.repository
    assert session.thumbnails is runtime.thumbnail_service
    assert session.state is state
    assert session.scans is not None
    assert session.scans.library_root == tmp_path
    assert session.asset_lifecycle is not None
    assert session.asset_lifecycle.library_root == tmp_path


def test_library_session_shutdown_delegates_to_asset_runtime(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, state_repository=Mock())

    session.shutdown()

    runtime.shutdown.assert_called_once()

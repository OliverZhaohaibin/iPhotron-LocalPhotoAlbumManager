from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.recognition import mutation_coordinator as mutation_module
from iPhoto.recognition.operation_journal import RecognitionOperationKind


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

    assert session.__dict__["edit"] is not None
    runtime.bind_edit_service.assert_called_once_with(session.__dict__["edit"])
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


def test_library_session_injects_one_recognition_mutation_owner(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, state_repository=Mock())

    mutations = session.recognition_mutations

    assert session.people._mutation_coordinator is mutations
    assert session.pets._mutation_coordinator is mutations
    assert session.recognition_merges._journal is mutations
    assert session.people.coordinator is not None
    assert session.pets.coordinator is not None
    assert {kind.value for kind in RecognitionOperationKind}.issubset(mutations._handlers)


def test_same_root_sessions_release_recognition_lease_by_reference_count(
    tmp_path: Path,
) -> None:
    def make_runtime():
        runtime = Mock()
        runtime.assets = object()
        runtime.repository = object()
        runtime.thumbnail_service = object()
        return runtime

    first = LibrarySession(tmp_path, asset_runtime=make_runtime(), state_repository=Mock())
    second = LibrarySession(tmp_path, asset_runtime=make_runtime(), state_repository=Mock())
    first_mutations = first.recognition_mutations
    second_mutations = second.recognition_mutations
    first_people = first.people
    first_pets = first.pets
    resolved = tmp_path.resolve()

    assert first_mutations.execution_lock is second_mutations.execution_lock
    assert mutation_module._EXECUTION_LOCKS[resolved].references == 2

    first.shutdown()

    assert mutation_module._EXECUTION_LOCKS[resolved].references == 1
    assert second_mutations.unfinished() == ()
    assert first_people.coordinator is None
    assert first_pets.coordinator is None
    with pytest.raises(RuntimeError, match="closed"):
        first_mutations.unfinished()
    with pytest.raises(RuntimeError, match="shut down"):
        _ = first.people

    second.shutdown()

    assert resolved not in mutation_module._EXECUTION_LOCKS

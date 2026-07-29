from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.bootstrap.runtime_context import RuntimeContext
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.events.bus import EventBus


@pytest.fixture(autouse=True)
def _reset_global_repository() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


class _FakeAssetRuntime:
    def __init__(self) -> None:
        self.bound_roots: list[Path] = []
        self.bound_edit_services: list[object | None] = []

    def bind_library_root(self, root: Path) -> None:
        self.bound_roots.append(root)
        work_dir = root / ".iPhoto"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "global_index.db").touch()

    def bind_edit_service(self, edit_service: object | None) -> None:
        self.bound_edit_services.append(edit_service)

    def shutdown(self) -> None:
        return None


class _FakeFacade:
    def __init__(self) -> None:
        self.scan_requests: list[tuple[Path, list[str], list[str], bool]] = []

    def scan_root_async(
        self,
        root: Path,
        *,
        include,
        exclude,
        startup: bool = False,
    ) -> None:
        self.scan_requests.append((Path(root), list(include), list(exclude), bool(startup)))


class _FakeLibrary:
    def __init__(self) -> None:
        self._root: Path | None = None
        self.library_session = None
        self.scan_requests: list[tuple[Path, list[str], list[str]]] = []
        self.bound_scan_services: list[object | None] = []
        self.bound_asset_query_services: list[object | None] = []
        self.bound_state_repositories: list[object | None] = []
        self.bound_asset_state_services: list[object | None] = []
        self.bound_album_metadata_services: list[object | None] = []
        self.bound_edit_services: list[object | None] = []
        self.bound_asset_lifecycle_services: list[object | None] = []
        self.bound_asset_operation_services: list[object | None] = []
        self.bound_people_services: list[object | None] = []
        self.bound_pet_services: list[object | None] = []
        self.bound_map_runtimes: list[object | None] = []
        self.bound_map_interaction_services: list[object | None] = []
        self.bound_location_services: list[object | None] = []
        self.asset_query_service_during_bind: object | None = None
        self.state_repository_during_bind: object | None = None

    def bind_path(self, root: Path) -> None:
        self.asset_query_service_during_bind = (
            self.bound_asset_query_services[-1]
            if self.bound_asset_query_services
            else None
        )
        self.state_repository_during_bind = (
            self.bound_state_repositories[-1]
            if self.bound_state_repositories
            else None
        )
        self._root = root

    def root(self) -> Path | None:
        return self._root

    def bind_library_session(self, library_session: object | None) -> None:
        self.library_session = library_session
        if library_session is None:
            self.bind_location_service(None)
            self.bind_edit_service(None)
            self.bind_map_interaction_service(None)
            self.bind_map_runtime(None)
            self.bind_people_service(None)
            self.bind_pet_service(None)
            self.bind_asset_operation_service(None)
            self.bind_asset_lifecycle_service(None)
            self.bind_album_metadata_service(None)
            self.bind_asset_state_service(None)
            self.bind_state_repository(None)
            self.bind_asset_query_service(None)
            self.bind_scan_service(None)
            return

        self.bind_asset_query_service(library_session.asset_queries)
        self.bind_state_repository(library_session.state)
        self.bind_asset_state_service(library_session.asset_state)
        self.bind_album_metadata_service(library_session.album_metadata)
        self.bind_location_service(library_session.locations)
        self.bind_edit_service(library_session.edit)
        self.bind_scan_service(library_session.scans)
        self.bind_asset_lifecycle_service(library_session.asset_lifecycle)
        self.bind_asset_operation_service(library_session.asset_operations)
        self.bind_people_service(library_session.people)
        self.bind_pet_service(library_session.pets)
        self.bind_map_runtime(library_session.maps)
        self.bind_map_interaction_service(library_session.map_interactions)

    def is_scanning_path(self, _root: Path) -> bool:
        return False

    def start_scanning(
        self,
        root: Path,
        include: list[str],
        exclude: list[str],
    ) -> None:
        self.scan_requests.append((root, list(include), list(exclude)))

    def bind_scan_service(self, scan_service: object | None) -> None:
        self.bound_scan_services.append(scan_service)

    @property
    def scan_service(self) -> object | None:
        if not self.bound_scan_services:
            return None
        return self.bound_scan_services[-1]

    def bind_asset_query_service(self, asset_query_service: object | None) -> None:
        self.bound_asset_query_services.append(asset_query_service)

    def bind_state_repository(self, state_repository: object | None) -> None:
        self.bound_state_repositories.append(state_repository)

    def bind_asset_state_service(self, asset_state_service: object | None) -> None:
        self.bound_asset_state_services.append(asset_state_service)

    def bind_album_metadata_service(
        self,
        album_metadata_service: object | None,
    ) -> None:
        self.bound_album_metadata_services.append(album_metadata_service)

    def bind_asset_lifecycle_service(
        self,
        asset_lifecycle_service: object | None,
    ) -> None:
        self.bound_asset_lifecycle_services.append(asset_lifecycle_service)

    def bind_edit_service(self, edit_service: object | None) -> None:
        self.bound_edit_services.append(edit_service)

    def bind_asset_operation_service(
        self,
        asset_operation_service: object | None,
    ) -> None:
        self.bound_asset_operation_services.append(asset_operation_service)

    def bind_people_service(self, people_service: object | None) -> None:
        self.bound_people_services.append(people_service)

    def bind_pet_service(self, pet_service: object | None) -> None:
        self.bound_pet_services.append(pet_service)

    def bind_map_runtime(self, map_runtime: object | None) -> None:
        self.bound_map_runtimes.append(map_runtime)

    def bind_map_interaction_service(
        self,
        map_interaction_service: object | None,
    ) -> None:
        self.bound_map_interaction_services.append(map_interaction_service)

    def bind_location_service(self, location_service: object | None) -> None:
        self.bound_location_services.append(location_service)


def _runtime_context(root: Path) -> tuple[RuntimeContext, _FakeLibrary, _FakeAssetRuntime]:
    context = RuntimeContext.__new__(RuntimeContext)
    library = _FakeLibrary()
    asset_runtime = _FakeAssetRuntime()
    context.library = library
    context.facade = _FakeFacade()
    context.event_bus = EventBus(__import__("logging").getLogger("EventBus"))
    context.asset_runtime = asset_runtime
    context._container = None
    context._pending_basic_library_path = root
    context._library_epoch = 0
    return context, library, asset_runtime


def test_resume_startup_tasks_scans_when_work_dir_exists_without_index(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    (library_root / ".iPhoto" / "cache" / "shaders").mkdir(parents=True)
    context, library, asset_runtime = _runtime_context(library_root)

    context.resume_startup_tasks()

    assert asset_runtime.bound_roots == [library_root]
    assert asset_runtime.bound_edit_services[-1] is not None
    assert (library_root / ".iPhoto" / "global_index.db").exists()
    assert library.asset_query_service_during_bind is not None
    assert library.state_repository_during_bind is not None
    assert library.bound_scan_services[-1] is not None
    assert library.bound_asset_query_services[-1] is not None
    assert library.bound_state_repositories[-1] is not None
    assert library.bound_asset_state_services[-1] is not None
    assert library.bound_album_metadata_services[-1] is not None
    assert library.bound_edit_services[-1] is not None
    assert library.bound_asset_lifecycle_services[-1] is not None
    assert library.bound_asset_operation_services[-1] is not None
    assert library.bound_people_services[-1] is not None
    assert library.bound_map_runtimes[-1] is not None
    assert library.bound_map_interaction_services[-1] is not None
    assert library.bound_location_services[-1] is not None
    assert context.facade.scan_requests == [
        (library_root, list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE), True)
    ]


def test_resume_startup_tasks_scans_when_index_preexists_without_completed_job(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    work_dir = library_root / ".iPhoto"
    work_dir.mkdir(parents=True)
    (work_dir / "global_index.db").touch()
    context, library, asset_runtime = _runtime_context(library_root)

    context.resume_startup_tasks()

    assert asset_runtime.bound_roots == [library_root]
    assert asset_runtime.bound_edit_services[-1] is not None
    assert library.bound_scan_services[-1] is not None
    assert library.bound_asset_query_services[-1] is not None
    assert library.bound_state_repositories[-1] is not None
    assert library.bound_asset_state_services[-1] is not None
    assert library.bound_album_metadata_services[-1] is not None
    assert library.bound_edit_services[-1] is not None
    assert library.bound_asset_lifecycle_services[-1] is not None
    assert library.bound_asset_operation_services[-1] is not None
    assert library.bound_people_services[-1] is not None
    assert library.bound_map_runtimes[-1] is not None
    assert library.bound_map_interaction_services[-1] is not None
    assert library.bound_location_services[-1] is not None
    assert context.facade.scan_requests == [
        (library_root, list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE), True)
    ]


def test_resume_startup_tasks_can_defer_scan_until_gallery_opens(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    context, library, asset_runtime = _runtime_context(library_root)

    context.resume_startup_tasks(defer_scan=True)

    assert asset_runtime.bound_roots == [library_root]
    assert library.bound_scan_services[-1] is not None
    assert context.facade.scan_requests == []

    context.start_deferred_startup_scan()
    context.start_deferred_startup_scan()

    assert context.facade.scan_requests == [
        (library_root, list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE), True)
    ]


def test_resume_startup_tasks_skips_scan_when_scope_complete(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    repo = get_global_repository(library_root)
    repo.create_scan_job(
        job_id="scan_complete",
        root=library_root.as_posix(),
        scope="library",
    )
    repo.update_scan_job_stage("scan_complete", status="completed", finished=True)
    reset_global_repository()
    context, library, asset_runtime = _runtime_context(library_root)

    context.resume_startup_tasks()

    assert asset_runtime.bound_roots == [library_root]
    assert library.bound_scan_services[-1] is not None
    assert context.facade.scan_requests == []


def test_close_library_unbinds_map_interaction_service(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    context, library, _asset_runtime = _runtime_context(library_root)

    context.open_library(library_root)
    assert library.bound_map_interaction_services[-1] is not None

    context.close_library()

    assert library.bound_map_interaction_services[-1] is None


def test_library_binding_token_changes_for_open_and_close(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    context, _library, _asset_runtime = _runtime_context(first_root)

    assert context.library_binding_token.epoch == 0
    assert context.library_binding_token.root is None

    context.open_library(first_root)
    first = context.library_binding_token
    context.open_library(second_root)
    second = context.library_binding_token
    context.close_library()
    closed = context.library_binding_token

    assert first.root == first_root
    assert second.root == second_root
    assert first.epoch < second.epoch < closed.epoch
    assert closed.root is None


def test_new_library_epoch_is_visible_before_session_is_published(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    context, library, _asset_runtime = _runtime_context(library_root)
    observed = []
    original_bind = library.bind_library_session

    def _observe_bind(session: object | None) -> None:
        if session is not None:
            observed.append(context.library_binding_token)
        original_bind(session)

    library.bind_library_session = _observe_bind  # type: ignore[method-assign]

    context.open_library(library_root)

    assert observed == [context.library_binding_token]
    assert observed[0].epoch == 1
    assert observed[0].root == library_root


def test_closed_library_epoch_is_visible_before_session_is_unbound(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    context, library, _asset_runtime = _runtime_context(library_root)
    context.open_library(library_root)
    observed = []
    original_bind = library.bind_library_session

    def _observe_unbind(session: object | None) -> None:
        if session is None:
            observed.append(context.library_binding_token)
        original_bind(session)

    library.bind_library_session = _observe_unbind  # type: ignore[method-assign]

    context.close_library()

    assert observed == [context.library_binding_token]
    assert observed[0].epoch == 2
    assert observed[0].root is None

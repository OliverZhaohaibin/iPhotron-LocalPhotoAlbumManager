from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.runtime_context import RuntimeContext
from iPhoto.events.bus import EventBus


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
        self.scan_requests: list[tuple[Path, list[str], list[str]]] = []

    def scan_root_async(
        self,
        root: Path,
        *,
        include,
        exclude,
    ) -> None:
        self.scan_requests.append((Path(root), list(include), list(exclude)))


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
    assert [request[0] for request in context.facade.scan_requests] == [library_root]


def test_resume_startup_tasks_skips_scan_when_index_preexists(tmp_path: Path) -> None:
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
    assert context.facade.scan_requests == []


def test_close_library_unbinds_map_interaction_service(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    context, library, _asset_runtime = _runtime_context(library_root)

    context.open_library(library_root)
    assert library.bound_map_interaction_services[-1] is not None

    context.close_library()

    assert library.bound_map_interaction_services[-1] is None

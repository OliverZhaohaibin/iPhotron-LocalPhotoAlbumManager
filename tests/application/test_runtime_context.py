from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.runtime_context import RuntimeContext


class _FakeAssetRuntime:
    def __init__(self) -> None:
        self.bound_roots: list[Path] = []

    def bind_library_root(self, root: Path) -> None:
        self.bound_roots.append(root)
        work_dir = root / ".iPhoto"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "global_index.db").touch()


class _FakeLibrary:
    def __init__(self) -> None:
        self._root: Path | None = None
        self.scan_requests: list[tuple[Path, list[str], list[str]]] = []
        self.bound_scan_services: list[object | None] = []
        self.bound_asset_query_services: list[object | None] = []
        self.bound_state_repositories: list[object | None] = []
        self.bound_asset_lifecycle_services: list[object | None] = []
        self.bound_people_services: list[object | None] = []
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

    def bind_asset_lifecycle_service(
        self,
        asset_lifecycle_service: object | None,
    ) -> None:
        self.bound_asset_lifecycle_services.append(asset_lifecycle_service)

    def bind_people_service(self, people_service: object | None) -> None:
        self.bound_people_services.append(people_service)


def _runtime_context(root: Path) -> tuple[RuntimeContext, _FakeLibrary, _FakeAssetRuntime]:
    context = RuntimeContext.__new__(RuntimeContext)
    library = _FakeLibrary()
    asset_runtime = _FakeAssetRuntime()
    context.library = library
    context.asset_runtime = asset_runtime
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
    assert (library_root / ".iPhoto" / "global_index.db").exists()
    assert library.asset_query_service_during_bind is not None
    assert library.state_repository_during_bind is not None
    assert library.bound_scan_services[-1] is not None
    assert library.bound_asset_query_services[-1] is not None
    assert library.bound_state_repositories[-1] is not None
    assert library.bound_asset_lifecycle_services[-1] is not None
    assert library.bound_people_services[-1] is not None
    assert [request[0] for request in library.scan_requests] == [library_root]


def test_resume_startup_tasks_skips_scan_when_index_preexists(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    work_dir = library_root / ".iPhoto"
    work_dir.mkdir(parents=True)
    (work_dir / "global_index.db").touch()
    context, library, asset_runtime = _runtime_context(library_root)

    context.resume_startup_tasks()

    assert asset_runtime.bound_roots == [library_root]
    assert library.bound_scan_services[-1] is not None
    assert library.bound_asset_query_services[-1] is not None
    assert library.bound_state_repositories[-1] is not None
    assert library.bound_asset_lifecycle_services[-1] is not None
    assert library.bound_people_services[-1] is not None
    assert library.scan_requests == []

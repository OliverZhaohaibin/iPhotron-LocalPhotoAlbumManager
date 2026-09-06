"""Library-scoped runtime session for vNext application boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..application.ports import (
    AssetRepositoryPort,
    AssetStateServicePort,
    EditServicePort,
    LibraryStateRepositoryPort,
    LocationAssetServicePort,
    MapInteractionServicePort,
    MapRuntimePort,
)
from ..application.services.map_interaction_service import LibraryMapInteractionService
from ..infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)
from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
from ..infrastructure.services.map_runtime_service import SessionMapRuntimeService
from .library_album_metadata_service import LibraryAlbumMetadataService
from .library_asset_lifecycle_service import LibraryAssetLifecycleService
from .library_asset_operation_service import LibraryAssetOperationService
from .library_asset_query_service import LibraryAssetQueryService
from .library_asset_state_service import LibraryAssetStateService
from .library_edit_service import LibraryEditService
from .library_location_service import LibraryLocationService
from .library_scan_service import LibraryScanService

if TYPE_CHECKING:
    from ..application.services.recognition_edit_service import RecognitionEditService
    from ..application.services.recognition_merge_service import RecognitionMergeService
    from ..application.services.recognition_query_service import RecognitionQueryService
    from ..people.service import PeopleService
    from ..pets.service import PetService
    from ..recognition.mutation_coordinator import RecognitionMutationCoordinator
    from .library_probe import PreparedLibrary, ValidatedPreparedLibrary


@dataclass
class LibrarySession:
    """Own library-scoped adapters and expose the application-facing surface."""

    library_root: Path
    asset_runtime: LibraryAssetRuntime | None = None
    state_repository: LibraryStateRepositoryPort | None = None
    asset_state: AssetStateServicePort | None = None
    album_metadata: LibraryAlbumMetadataService | None = None
    asset_queries: LibraryAssetQueryService | None = None
    scans: LibraryScanService | None = None
    asset_lifecycle: LibraryAssetLifecycleService | None = None
    asset_operations: LibraryAssetOperationService | None = None
    recognition_mutations: RecognitionMutationCoordinator | None = None
    people: PeopleService | None = None
    pets: PetService | None = None
    recognition_queries: RecognitionQueryService | None = None
    recognition_merges: RecognitionMergeService | None = None
    recognition_edits: RecognitionEditService | None = None
    maps: MapRuntimePort | None = None
    map_interactions: MapInteractionServicePort | None = None
    edit: EditServicePort | None = None
    locations: LocationAssetServicePort | None = None
    bind_asset_runtime: bool = True
    _shutdown: bool = field(default=False, init=False, repr=False)

    _LAZY_SERVICE_NAMES = frozenset(
        {
            "people",
            "pets",
            "recognition_mutations",
            "recognition_queries",
            "recognition_merges",
            "recognition_edits",
            "maps",
            "map_interactions",
            "locations",
        }
    )

    def __getattribute__(self, name: str):
        value = object.__getattribute__(self, name)
        if name not in object.__getattribute__(self, "_LAZY_SERVICE_NAMES"):
            return value
        if object.__getattribute__(self, "_shutdown"):
            raise RuntimeError("LibrarySession is shut down.")
        if value is not None:
            return value
        factory = object.__getattribute__(self, "_create_lazy_service")
        value = factory(name)
        object.__setattr__(self, name, value)
        return value

    def __post_init__(self) -> None:
        self.library_root = Path(self.library_root)
        if self.asset_runtime is None:
            self.asset_runtime = LibraryAssetRuntime(self.library_root)
            self.bind_asset_runtime = False
        if self.bind_asset_runtime:
            self.asset_runtime.bind_library_root(self.library_root)
        if self.state_repository is None:
            self.state_repository = IndexStoreLibraryStateRepository(self.library_root)
        if self.asset_queries is None:
            self.asset_queries = LibraryAssetQueryService(self.library_root)
        if self.asset_state is None:
            self.asset_state = LibraryAssetStateService(
                self.library_root,
                state_repository=self.state_repository,
                favorite_query=self.asset_queries,
            )
        if self.album_metadata is None:
            self.album_metadata = LibraryAlbumMetadataService(
                self.library_root,
                state_repository=self.state_repository,
            )
        if self.scans is None:
            self.scans = LibraryScanService(self.library_root)
        if self.asset_lifecycle is None:
            self.asset_lifecycle = LibraryAssetLifecycleService(
                self.library_root,
                scan_service=self.scans,
            )
        if self.asset_operations is None:
            self.asset_operations = LibraryAssetOperationService(
                self.library_root,
                lifecycle_service=self.asset_lifecycle,
            )
        # Detail playback consults edit sidecars for every still image.  Keep
        # this lightweight service on the core Gallery/Detail path so the
        # first image click never becomes a service-construction boundary.
        if self.edit is None:
            self.edit = LibraryEditService(
                self.library_root,
                thumbnail_state_service=self.asset_queries,
            )
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(self.edit)
        bind_thumbnail_state = getattr(
            self.asset_runtime,
            "bind_thumbnail_state_service",
            None,
        )
        if callable(bind_thumbnail_state):
            bind_thumbnail_state(self.asset_queries)

        # People, Pets, Map and Location remain feature-scoped.

    def _create_lazy_service(self, name: str):
        if name == "recognition_mutations":
            from ..recognition.mutation_coordinator import (
                RecognitionMutationCoordinator,
            )

            return RecognitionMutationCoordinator(self.library_root)
        if name == "people":
            from .library_people_service import create_people_service

            return create_people_service(
                self.library_root,
                mutation_coordinator=self.recognition_mutations,
            )
        if name == "pets":
            from .library_pet_service import create_pet_service

            return create_pet_service(
                self.library_root,
                mutation_coordinator=self.recognition_mutations,
            )
        if name == "recognition_queries":
            from ..application.services.recognition_query_service import (
                RecognitionQueryService,
            )

            return RecognitionQueryService(
                self.library_root,
                people_service=self.people,
                pet_service=self.pets,
            )
        if name == "recognition_merges":
            from ..application.services.recognition_merge_service import (
                RecognitionMergeService,
            )

            return RecognitionMergeService(
                self.people,
                self.pets,
                mutation_coordinator=self.recognition_mutations,
            )
        if name == "recognition_edits":
            from ..application.services.recognition_edit_service import RecognitionEditService

            return RecognitionEditService(
                people_service=self.people,
                pet_service=self.pets,
                merge_service=self.recognition_merges,
                mutation_coordinator=self.recognition_mutations,
            )
        if name == "maps":
            return SessionMapRuntimeService()
        if name == "map_interactions":
            return LibraryMapInteractionService()
        if name == "locations":
            return LibraryLocationService(
                self.library_root,
                query_service=self.asset_queries,
            )
        raise AttributeError(name)

    @property
    def assets(self) -> AssetRepositoryPort:
        return self.asset_runtime.assets

    @property
    def thumbnails(self):
        return self.asset_runtime.thumbnail_service

    @property
    def state(self) -> LibraryStateRepositoryPort:
        assert self.state_repository is not None
        return self.state_repository

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        people = object.__getattribute__(self, "people")
        pets = object.__getattribute__(self, "pets")
        mutations = object.__getattribute__(self, "recognition_mutations")
        if people is not None:
            people.shutdown()
        if pets is not None:
            pets.shutdown()
        if mutations is not None:
            mutations.close()
        shutdown_queries = getattr(self.asset_queries, "shutdown", None)
        if callable(shutdown_queries):
            shutdown_queries()
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(None)
        bind_thumbnail_state = getattr(
            self.asset_runtime,
            "bind_thumbnail_state_service",
            None,
        )
        if callable(bind_thumbnail_state):
            bind_thumbnail_state(None)
        self.asset_runtime.shutdown()

    @classmethod
    def from_prepared(
        cls,
        prepared: "PreparedLibrary",
        *,
        asset_runtime: LibraryAssetRuntime,
        bind_asset_runtime: bool = False,
    ) -> "LibrarySession":
        """Compose services after an out-of-process library probe succeeded."""

        from ..cache.index_store import mark_repository_prepared

        if prepared.credential is None:
            raise ValueError("prepared library is missing its credential")
        mark_repository_prepared(prepared.root, prepared.credential)
        return cls(
            prepared.root,
            asset_runtime=asset_runtime,
            bind_asset_runtime=bind_asset_runtime,
        )

    @classmethod
    def from_validated(
        cls,
        validated: "ValidatedPreparedLibrary",
        *,
        asset_runtime: LibraryAssetRuntime,
        bind_asset_runtime: bool = False,
    ) -> "LibrarySession":
        """Consume a validated capability and compose one library session."""

        return cls.from_prepared(
            validated.consume(),
            asset_runtime=asset_runtime,
            bind_asset_runtime=bind_asset_runtime,
        )


def create_headless_library_session(root: Path) -> LibrarySession:
    """Create a library session for non-GUI entry points such as the CLI."""

    library_root = Path(root)
    return LibrarySession(
        library_root,
        asset_runtime=LibraryAssetRuntime(library_root),
        bind_asset_runtime=False,
    )


def create_library_state_repository(root: Path) -> LibraryStateRepositoryPort:
    """Create the current state adapter for compatibility entry points."""

    return IndexStoreLibraryStateRepository(Path(root))


__all__ = [
    "LibrarySession",
    "create_headless_library_session",
    "create_library_state_repository",
]

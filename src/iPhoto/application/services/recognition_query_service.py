"""Library-scoped, read-only People/Pets query surface."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from iPhoto.people.repository import AssetFaceAnnotation, PeopleGroupSummary, PersonSummary
from iPhoto.people.service import PeopleService
from iPhoto.pets.records import AssetPetAnnotation, PetSummary
from iPhoto.pets.service import PetService


@dataclass(frozen=True, slots=True)
class RecognitionIdentityCandidate:
    identity_key: str
    name: str
    thumbnail_path: Path | None
    count: int


@dataclass(frozen=True, slots=True)
class RecognitionDashboardSnapshot:
    library_root: Path
    revision: int
    include_hidden: bool
    people: tuple[PersonSummary, ...]
    groups: tuple[PeopleGroupSummary, ...]
    pets: tuple[PetSummary, ...]
    pending_people: int
    pending_pets: int


@dataclass(frozen=True, slots=True)
class RecognitionOverlaySnapshot:
    library_root: Path
    revision: int
    asset_id: str
    faces: tuple[AssetFaceAnnotation, ...]
    pets: tuple[AssetPetAnnotation, ...]
    candidates: tuple[RecognitionIdentityCandidate, ...]


@dataclass(frozen=True, slots=True)
class RecognitionAssetAnnotations:
    library_root: Path
    revision: int
    asset_id: str
    faces: tuple[AssetFaceAnnotation, ...]
    pets: tuple[AssetPetAnnotation, ...]


class RecognitionQueryService:
    """Compose recognition reads and cache them by library revision.

    Concurrent callers share one cache-miss load per revision. SQLite reads
    happen outside the cache lock so GUI-thread invalidation never waits for a
    background dashboard or overlay query to finish.
    """

    def __init__(
        self,
        library_root: Path,
        *,
        people_service: PeopleService,
        pet_service: PetService,
    ) -> None:
        self._library_root = Path(library_root)
        self._people_service = people_service
        self._pet_service = pet_service
        self._revision = 0
        self._dashboard_cache: dict[tuple[bool, int], RecognitionDashboardSnapshot] = {}
        self._candidate_cache: dict[tuple[bool, int], tuple[RecognitionIdentityCandidate, ...]] = {}
        self._annotation_cache: dict[str, RecognitionAssetAnnotations] = {}
        self._lock = threading.RLock()
        self._cache_changed = threading.Condition(self._lock)
        self._dashboard_loads: set[tuple[bool, int]] = set()

    @property
    def library_root(self) -> Path:
        return self._library_root

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def load_dashboard(self, include_hidden: bool = False) -> RecognitionDashboardSnapshot:
        include_hidden = bool(include_hidden)
        while True:
            with self._cache_changed:
                revision = self._revision
                key = (include_hidden, revision)
                cached = self._dashboard_cache.get(key)
                if cached is not None:
                    return cached
                if key in self._dashboard_loads:
                    self._cache_changed.wait()
                    continue
                self._dashboard_loads.add(key)

            try:
                snapshot, candidates = self._read_dashboard(
                    include_hidden=include_hidden,
                    revision=revision,
                )
            except Exception:
                with self._cache_changed:
                    self._dashboard_loads.discard(key)
                    self._cache_changed.notify_all()
                raise

            with self._cache_changed:
                self._dashboard_loads.discard(key)
                if revision == self._revision:
                    self._dashboard_cache[key] = snapshot
                    self._candidate_cache[key] = candidates
                    self._cache_changed.notify_all()
                    return snapshot
                self._cache_changed.notify_all()

    def load_overlay(
        self,
        asset_id: str,
        include_hidden: bool = False,
    ) -> RecognitionOverlaySnapshot:
        annotations = self.load_asset_annotations(asset_id)
        candidates = self.load_identity_candidates(include_hidden=include_hidden)
        return RecognitionOverlaySnapshot(
            library_root=self._library_root,
            revision=max(annotations.revision, self.revision),
            asset_id=annotations.asset_id,
            faces=annotations.faces,
            pets=annotations.pets,
            candidates=candidates,
        )

    def load_asset_annotations(self, asset_id: str) -> RecognitionAssetAnnotations:
        """Read only face/pet boxes for one asset, without dashboard queries."""

        normalized_id = str(asset_id or "")
        with self._lock:
            cached = self._annotation_cache.get(normalized_id)
            if cached is not None:
                return cached
            revision = self._revision
        faces = tuple(self._people_service.list_asset_face_annotations(normalized_id))
        pets = tuple(self._pet_service.list_asset_pet_annotations(normalized_id))
        snapshot = RecognitionAssetAnnotations(
            library_root=self._library_root,
            revision=revision,
            asset_id=normalized_id,
            faces=faces,
            pets=pets,
        )
        with self._lock:
            # A targeted invalidation removes this key; do not resurrect a
            # result that began before a global revision change.
            if revision == self._revision:
                self._annotation_cache[normalized_id] = snapshot
                return snapshot
        return self.load_asset_annotations(normalized_id)

    def load_identity_candidates(
        self,
        include_hidden: bool = False,
    ) -> tuple[RecognitionIdentityCandidate, ...]:
        """Load naming candidates lazily for rename/manual annotation UI."""

        include_hidden = bool(include_hidden)
        while True:
            with self._lock:
                revision = self._revision
                key = (include_hidden, revision)
                candidates = self._candidate_cache.get(key)
            if candidates is None:
                dashboard = self.load_dashboard(include_hidden=include_hidden)
                revision = dashboard.revision
                key = (dashboard.include_hidden, revision)
                with self._lock:
                    if revision != self._revision:
                        continue
                    candidates = self._candidate_cache.get(key)
                if candidates is None:
                    continue
            with self._lock:
                if revision != self._revision:
                    continue
                return candidates

    def invalidate(self, changed_asset_ids=None) -> int:
        with self._cache_changed:
            self._revision += 1
            self._dashboard_cache.clear()
            self._candidate_cache.clear()
            normalized_ids = {
                str(value)
                for value in (changed_asset_ids or ())
                if str(value)
            }
            if normalized_ids:
                for asset_id in normalized_ids:
                    self._annotation_cache.pop(asset_id, None)
            else:
                self._annotation_cache.clear()
            self._cache_changed.notify_all()
            return self._revision

    def _read_dashboard(
        self,
        *,
        include_hidden: bool,
        revision: int,
    ) -> tuple[
        RecognitionDashboardSnapshot,
        tuple[RecognitionIdentityCandidate, ...],
    ]:
        # Pets are calculated once, then reused by mixed People/Pets groups.
        all_pets, pending_pets = self._pet_service.load_dashboard(include_hidden=True)
        pets = (
            list(all_pets)
            if include_hidden
            else [summary for summary in all_pets if not summary.is_hidden]
        )
        people, groups, pending_people = self._people_service.load_dashboard(
            include_hidden=include_hidden,
            pet_summaries=list(all_pets),
        )
        snapshot = RecognitionDashboardSnapshot(
            library_root=self._library_root,
            revision=revision,
            include_hidden=include_hidden,
            people=tuple(people),
            groups=tuple(groups),
            pets=tuple(pets),
            pending_people=int(pending_people),
            pending_pets=int(pending_pets),
        )
        return snapshot, self._candidates(people, pets)

    @staticmethod
    def _candidates(
        people: list[PersonSummary],
        pets: list[PetSummary],
    ) -> tuple[RecognitionIdentityCandidate, ...]:
        values = [
            RecognitionIdentityCandidate(
                identity_key=f"person:{summary.person_id}",
                name=summary.name.strip(),
                thumbnail_path=summary.thumbnail_path,
                count=int(summary.face_count or 0),
            )
            for summary in people
            if isinstance(summary.name, str) and summary.name.strip()
        ]
        values.extend(
            RecognitionIdentityCandidate(
                identity_key=f"pet:{summary.pet_id}",
                name=summary.name.strip(),
                thumbnail_path=summary.thumbnail_path,
                count=int(summary.detection_count or 0),
            )
            for summary in pets
            if isinstance(summary.name, str) and summary.name.strip()
        )
        return tuple(values)


__all__ = [
    "RecognitionAssetAnnotations",
    "RecognitionDashboardSnapshot",
    "RecognitionIdentityCandidate",
    "RecognitionOverlaySnapshot",
    "RecognitionQueryService",
]

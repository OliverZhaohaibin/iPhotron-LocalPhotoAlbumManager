"""Session-scoped staging for incremental face scanning."""

from __future__ import annotations

from typing import Iterable

from .pipeline import (
    DetectedAssetFaces,
    build_person_records_from_faces,
    canonicalize_cluster_identities,
    cluster_face_records,
)
from .repository import FaceRecord, FaceRepository, PersonRecord


class FaceScanSession:
    """Accumulate per-asset face updates and commit one runtime snapshot."""

    def __init__(self) -> None:
        self._faces_by_asset_id: dict[str, list[FaceRecord]] = {}
        self._asset_rel_by_asset_id: dict[str, str] = {}

    def has_staged_changes(self) -> bool:
        return bool(self._faces_by_asset_id)

    def clear(self) -> None:
        self._faces_by_asset_id.clear()
        self._asset_rel_by_asset_id.clear()

    def stage_detection_results(
        self,
        detected_results: Iterable[DetectedAssetFaces],
    ) -> tuple[list[str], list[str]]:
        """Stage successful detections and split done vs retry asset ids."""

        done_ids: list[str] = []
        retry_ids: list[str] = []
        for item in detected_results:
            asset_id = str(item.asset_id or "")
            if not asset_id:
                continue
            if item.error:
                retry_ids.append(asset_id)
                continue
            self._faces_by_asset_id[asset_id] = list(item.faces)
            self._asset_rel_by_asset_id[asset_id] = str(item.asset_rel or "")
            done_ids.append(asset_id)
        return done_ids, retry_ids

    def build_runtime_snapshot(
        self,
        repository: FaceRepository,
        *,
        distance_threshold: float,
        min_samples: int,
        existing_faces: list[FaceRecord] | None = None,
    ) -> tuple[list[FaceRecord], list[PersonRecord]]:
        """Build the final runtime snapshot for this scan session.

        Args:
            repository: The face repository to use for clustering and state lookup.
            distance_threshold: Clustering distance threshold.
            min_samples: Minimum cluster samples parameter.
            existing_faces: Pre-fetched list of all persisted faces.  When
                provided, no extra DB read is performed; callers that already
                hold this data (e.g. ``commit()``) should pass it in to avoid
                a redundant round-trip.
        """

        staged_asset_ids = set(self._faces_by_asset_id)
        staged_asset_rels = {
            asset_rel for asset_rel in self._asset_rel_by_asset_id.values() if asset_rel
        }

        if existing_faces is None:
            existing_faces = repository.get_all_faces()

        state_repository = repository.state_repository
        auto_faces = [
            face
            for face in existing_faces
            if (
                not face.is_manual
                and face.asset_id not in staged_asset_ids
                and face.asset_rel not in staged_asset_rels
            )
        ]
        for faces in self._faces_by_asset_id.values():
            auto_faces.extend(faces)

        if not auto_faces:
            return [], []

        clustered_auto_faces, auto_persons = cluster_face_records(
            auto_faces,
            distance_threshold=distance_threshold,
            min_samples=min_samples,
        )
        if state_repository is not None:
            clustered_auto_faces, auto_persons = canonicalize_cluster_identities(
                clustered_auto_faces,
                auto_persons,
                state_repository,
                distance_threshold=distance_threshold,
            )

        existing_persons_by_id = {
            person.person_id: person for person in repository.get_all_person_records()
        }
        names_by_person_id = {
            person_id: person.name
            for person_id, person in existing_persons_by_id.items()
        }
        created_at_by_person_id = {
            person_id: person.created_at
            for person_id, person in existing_persons_by_id.items()
        }
        if state_repository is not None:
            for profile in state_repository.get_profiles():
                names_by_person_id[profile.person_id] = profile.name
                created_at_by_person_id[profile.person_id] = profile.created_at
        persons = build_person_records_from_faces(
            clustered_auto_faces,
            names_by_person_id=names_by_person_id,
            created_at_by_person_id=created_at_by_person_id,
        )
        return clustered_auto_faces, persons

    def commit(
        self,
        repository: FaceRepository,
        *,
        distance_threshold: float,
        min_samples: int,
        previous_faces: list[FaceRecord] | None = None,
        previous_persons: list[PersonRecord] | None = None,
        clustered_faces: list[FaceRecord] | None = None,
        persons: list[PersonRecord] | None = None,
    ) -> bool:
        """Commit one unified People snapshot for this scan session."""

        if not self.has_staged_changes():
            return False

        # Fetch the current state once so it can be passed to
        # build_runtime_snapshot() (avoiding a second get_all_faces() call
        # inside that method) and kept for rollback if the commit fails.
        if previous_faces is None:
            previous_faces = repository.get_all_faces()
        if previous_persons is None:
            previous_persons = repository.get_all_person_records()

        if clustered_faces is None or persons is None:
            clustered_faces, persons = self.build_runtime_snapshot(
                repository,
                distance_threshold=distance_threshold,
                min_samples=min_samples,
                existing_faces=previous_faces,
            )
        repository.replace_all(clustered_faces, persons, sync_runtime_state=False)
        state_repository = repository.state_repository
        try:
            repository.sync_runtime_state()
            if state_repository is not None and clustered_faces:
                state_repository.sync_scan_results(persons, clustered_faces)
        except Exception:
            repository.replace_all(previous_faces, previous_persons, sync_runtime_state=False)
            repository.sync_runtime_state()
            raise
        self.clear()
        return True

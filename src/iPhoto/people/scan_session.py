"""Session-scoped staging for incremental face scanning."""

from __future__ import annotations

from typing import Iterable

from .pipeline import (
    DetectedAssetFaces,
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
    ) -> tuple[list[FaceRecord], list[PersonRecord]]:
        """Build the final runtime snapshot for this scan session."""

        staged_asset_ids = set(self._faces_by_asset_id)
        staged_asset_rels = {
            asset_rel for asset_rel in self._asset_rel_by_asset_id.values() if asset_rel
        }

        existing_faces = [
            face
            for face in repository.get_all_faces()
            if face.asset_id not in staged_asset_ids and face.asset_rel not in staged_asset_rels
        ]
        all_faces = list(existing_faces)
        for faces in self._faces_by_asset_id.values():
            all_faces.extend(faces)

        if not all_faces:
            return [], []

        clustered_faces, persons = cluster_face_records(
            all_faces,
            distance_threshold=distance_threshold,
            min_samples=min_samples,
        )
        state_repository = repository.state_repository
        if state_repository is not None:
            clustered_faces, persons = canonicalize_cluster_identities(
                clustered_faces,
                persons,
                state_repository,
                distance_threshold=distance_threshold,
            )
        return clustered_faces, persons

    def commit(
        self,
        repository: FaceRepository,
        *,
        distance_threshold: float,
        min_samples: int,
    ) -> bool:
        """Commit one unified People snapshot for this scan session."""

        if not self.has_staged_changes():
            return False

        clustered_faces, persons = self.build_runtime_snapshot(
            repository,
            distance_threshold=distance_threshold,
            min_samples=min_samples,
        )
        repository.replace_all(clustered_faces, persons)
        state_repository = repository.state_repository
        if state_repository is not None and clustered_faces:
            state_repository.sync_scan_results(persons, clustered_faces)
        self.clear()
        return True

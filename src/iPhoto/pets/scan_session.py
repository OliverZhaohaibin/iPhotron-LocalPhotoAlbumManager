"""In-memory staging for pet scan batches."""

from __future__ import annotations

from .pipeline import (
    DetectedAssetPets,
    canonicalize_pet_identities,
    cluster_pet_records,
)
from .records import PetDetectionRecord, PetRecord
from .repository import PetRepository


class PetScanSession:
    def __init__(self) -> None:
        self._staged_results: list[DetectedAssetPets] = []

    def stage_detection_results(
        self,
        detected_results: list[DetectedAssetPets],
    ) -> tuple[list[str], list[str]]:
        done_ids: list[str] = []
        retry_ids: list[str] = []
        for result in detected_results:
            if result.error:
                if result.asset_id:
                    retry_ids.append(result.asset_id)
                continue
            if result.asset_id:
                done_ids.append(result.asset_id)
            self._staged_results.append(result)
        return done_ids, retry_ids

    def build_runtime_snapshot(
        self,
        repository: PetRepository,
        *,
        distance_threshold: float,
        min_samples: int,
        existing_detections: list[PetDetectionRecord],
    ) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
        done_asset_ids = {result.asset_id for result in self._staged_results if result.asset_id}
        done_asset_rels = {
            str(result.asset_rel or "") for result in self._staged_results if result.asset_rel
        }
        staged_detections = [
            detection
            for result in self._staged_results
            for detection in result.detections
        ]
        retained = [
            detection
            for detection in existing_detections
            if detection.asset_id not in done_asset_ids
            and detection.asset_rel not in done_asset_rels
        ]
        all_detections = retained + staged_detections
        state_repository = repository.state_repository
        if state_repository is not None:
            rejected_keys = state_repository.get_rejected_pet_keys(
                detection.pet_key for detection in all_detections
            )
            all_detections = [
                detection for detection in all_detections if detection.pet_key not in rejected_keys
            ]
        clustered_detections, pets = cluster_pet_records(
            all_detections,
            distance_threshold=distance_threshold,
            min_samples=min_samples,
        )
        if state_repository is not None:
            clustered_detections, pets = canonicalize_pet_identities(
                clustered_detections,
                pets,
                state_repository,
                distance_threshold=distance_threshold,
            )
        return clustered_detections, pets

    def commit(
        self,
        repository: PetRepository,
        *,
        detections: list[PetDetectionRecord],
        pets: list[PetRecord],
    ) -> None:
        previous_detections = repository.get_all_detections()
        previous_pets = repository.get_all_pet_records()

        repository.replace_all(detections, pets, sync_runtime_state=False)
        try:
            repository.sync_runtime_state()
        except Exception:
            repository.replace_all(
                previous_detections,
                previous_pets,
                sync_runtime_state=False,
            )
            repository.sync_runtime_state()
            raise

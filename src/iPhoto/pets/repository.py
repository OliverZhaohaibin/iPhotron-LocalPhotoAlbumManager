"""SQLite pet index repository for Pets clusters."""

# ruff: noqa: S608

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from iPhoto.people.face_repository import FaceRepository
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.recognition.promotion import (
    PROMOTION_CANDIDATE,
    PROMOTION_CONFIRMED,
    PROMOTION_LEGACY_VISIBLE,
)
from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite

from .records import (
    AssetPetAnnotation,
    PetDetectionRecord,
    PetMutationFailure,
    PetProfile,
    PetRecord,
    PetSummary,
)
from .repository_utils import (
    cosine_distance,
    deserialize_embedding,
    normalize_name,
    normalize_vector,
    profile_state_for_sample_count,
    serialize_embedding,
    utc_now_iso,
)
from .state_repository import PetStateRepository, _IncrementalStateSnapshot

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PetMutationResult:
    changed_asset_ids: tuple[str, ...] = ()
    changed_pet_ids: tuple[str, ...] = ()
    pet_redirects: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingContract:
    pipeline_version: str
    dimension: int
    generation_id: int

    @classmethod
    def from_detection(cls, detection: PetDetectionRecord) -> EmbeddingContract:
        return cls(
            pipeline_version=str(detection.embedding_pipeline_version or ""),
            dimension=int(detection.embedding_dim),
            generation_id=int(detection.generation_id),
        )

    @classmethod
    def from_pet(cls, pet: PetRecord) -> EmbeddingContract:
        return cls.from_profile(pet)

    @classmethod
    def from_profile(cls, profile: PetProfile | PetRecord) -> EmbeddingContract:
        return cls(
            pipeline_version=str(profile.embedding_pipeline_version or ""),
            dimension=int(profile.embedding_dim),
            generation_id=int(profile.generation_id),
        )


@dataclass(frozen=True)
class PetIncrementalCommitResult:
    changed_asset_ids: tuple[str, ...] = ()
    retired_asset_ids: tuple[str, ...] = ()
    previous_thumbnail_paths: tuple[str, ...] = ()
    added_pet_ids: tuple[str, ...] = ()
    updated_pet_ids: tuple[str, ...] = ()
    removed_pet_ids: tuple[str, ...] = ()

    @property
    def changed_pet_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(self.added_pet_ids + self.updated_pet_ids + self.removed_pet_ids)
        )


@dataclass(frozen=True)
class PetClusteringConsolidationResult:
    processed_seed_count: int = 0
    changed_asset_ids: tuple[str, ...] = ()
    added_pet_ids: tuple[str, ...] = ()
    updated_pet_ids: tuple[str, ...] = ()
    removed_pet_ids: tuple[str, ...] = ()

    @property
    def changed_pet_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(self.added_pet_ids + self.updated_pet_ids + self.removed_pet_ids)
        )

    @property
    def changed(self) -> bool:
        return bool(self.changed_asset_ids or self.changed_pet_ids)


class PetClusteringConsolidationCancelledError(RuntimeError):
    """Raised before a consolidation transaction commits."""


@dataclass
class _ProfileMatchContext:
    profiles: dict[str, PetRecord]
    centers: dict[str, np.ndarray]
    species: dict[str, str | None]
    all_centers: dict[str, np.ndarray]
    all_species: dict[str, str | None]
    member_samples: dict[str, tuple[tuple[str, np.ndarray], ...]]
    migration_candidates_by_asset: dict[str, tuple[str, ...]]
    candidate_index: _ProfileCandidateIndex
    consolidation_candidate_index: _ProfileCandidateIndex


@dataclass(frozen=True)
class _IncrementalPetAssignment:
    detections: tuple[PetDetectionRecord, ...] = ()
    contract_replacement_pet_ids: tuple[str, ...] = ()


class PetRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = PetStateRepository(state_db_path) if state_db_path is not None else None
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._mutation_lock = threading.RLock()
        self._match_contexts: dict[EmbeddingContract, _ProfileMatchContext] = {}
        self._last_mutation_failure: PetMutationFailure | None = None
        self._same_asset_manual_conflicts = 0

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> PetStateRepository | None:
        return self._state_repo

    @property
    def last_mutation_failure(self) -> PetMutationFailure | None:
        return self._last_mutation_failure

    @property
    def same_asset_manual_conflicts(self) -> int:
        return self._same_asset_manual_conflicts

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                self._create_schema(conn)
            if self._state_repo is not None:
                self._state_repo.initialize()
            self._migrate_pet_keys_v2()
            self._initialized = True
            self.recover_pending_runtime_state_syncs()

    def _migrate_pet_keys_v2(self) -> None:
        from .pipeline import PET_EMBEDDING_PIPELINE_VERSION, build_pet_key

        migrated: list[tuple[str, str]] = []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT detection_id, pet_key, asset_id, box_x, box_y, box_w, box_h,
                       image_width, image_height, species_label, pet_id
                FROM pet_detections
                WHERE pet_key_version != 'v2' OR pet_key NOT LIKE 'v2:%'
                """
            ).fetchall()
            conn.execute(
                """
                UPDATE pet_detections
                SET embedding_pipeline_version = ?
                WHERE embedding_pipeline_version = ''
                """,
                (PET_EMBEDDING_PIPELINE_VERSION,),
            )
            conn.execute(
                """
                UPDATE pets
                SET embedding_pipeline_version = ?
                WHERE embedding_pipeline_version = ''
                """,
                (PET_EMBEDDING_PIPELINE_VERSION,),
            )
            for row in rows:
                new_key = build_pet_key(
                    asset_id=str(row["asset_id"]),
                    bbox=(
                        int(row["box_x"]),
                        int(row["box_y"]),
                        int(row["box_w"]),
                        int(row["box_h"]),
                    ),
                    image_width=int(row["image_width"]),
                    image_height=int(row["image_height"]),
                    species_label=_normalize_species_label(row["species_label"]),
                )
                conn.execute(
                    """
                    UPDATE pet_detections
                    SET pet_key = ?, pet_key_version = 'v2'
                    WHERE detection_id = ?
                    """,
                    (new_key, str(row["detection_id"])),
                )
                if row["pet_id"]:
                    migrated.append((new_key, str(row["pet_id"])))
            conn.commit()
        if self._state_repo is not None:
            self._state_repo.migrate_pet_keys(migrated)

    def replace_all(
        self,
        detections: list[PetDetectionRecord],
        pets: list[PetRecord],
        *,
        sync_runtime_state: bool = True,
        operation_id: str | None = None,
        operation_kind: str = "pet_replace_all",
    ) -> tuple[str, ...]:
        self.initialize()
        if self._state_repo is not None and detections:
            rejected = self._state_repo.get_rejected_pet_keys(
                detection.pet_key for detection in detections
            )
            if rejected:
                detections = [
                    detection for detection in detections if detection.pet_key not in rejected
                ]
                retained_pet_ids = {
                    str(detection.pet_id) for detection in detections if detection.pet_id
                }
                pets = [pet for pet in pets if pet.pet_id in retained_pet_ids]
        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        with closing(self._connect()) as conn:
            previous_pet_ids = {
                str(row["pet_id"])
                for row in conn.execute("SELECT pet_id FROM pets").fetchall()
                if row["pet_id"]
            }
            previous_thumbnail_paths = tuple(
                str(row["thumbnail_path"])
                for row in conn.execute(
                    """
                    SELECT thumbnail_path
                    FROM pet_detections
                    WHERE thumbnail_path IS NOT NULL
                    """
                ).fetchall()
                if row["thumbnail_path"]
            )
            conn.execute("DELETE FROM pets")
            conn.execute("DELETE FROM pet_detections")
            conn.execute("DELETE FROM pet_contract_migration_assets")
            conn.executemany(
                """
                INSERT INTO pet_detections (
                    detection_id, pet_key, asset_id, asset_rel,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, species_label, quality_score,
                    pet_key_version, embedding_pipeline_version, generation_id,
                    is_stale, stale_reason, source_generation_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [self._detection_to_row(detection) for detection in detections],
            )
            conn.executemany(
                """
                INSERT INTO pets (
                    pet_id, name, key_detection_id, detection_count,
                    center_embedding, embedding_dim, created_at, updated_at,
                    sample_count, profile_state, species_label,
                    embedding_pipeline_version, generation_id,
                    boundary_embeddings, boundary_sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._pet_to_row(pet) for pet in pets],
            )
            conn.execute(
                """
                INSERT INTO scan_metadata (key, value)
                VALUES ('updated_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (utc_now_iso(),),
            )
            current_pet_ids = {pet.pet_id for pet in pets if pet.pet_id}
            self._write_runtime_commit(
                conn,
                effective_operation_id,
                {
                    "operation_kind": operation_kind,
                    "asset_ids": list(
                        dict.fromkeys(
                            detection.asset_id for detection in detections if detection.asset_id
                        )
                    ),
                    "affected_pet_ids": sorted(previous_pet_ids | current_pet_ids),
                    "changed_asset_ids": list(
                        dict.fromkeys(
                            detection.asset_id for detection in detections if detection.asset_id
                        )
                    ),
                    "changed_pet_ids": sorted(previous_pet_ids | current_pet_ids),
                    "previous_thumbnail_paths": list(previous_thumbnail_paths),
                },
            )
            conn.commit()
        if sync_runtime_state:
            self.complete_runtime_state_sync(effective_operation_id)
            self.prune_unreferenced_thumbnails(previous_thumbnail_paths)
        self._invalidate_profile_indexes()
        return previous_thumbnail_paths

    def replace_assets_incrementally(
        self,
        asset_ids: Iterable[str],
        detections: list[PetDetectionRecord],
        *,
        retry_asset_ids: Iterable[str] = (),
        stale_reason: str = "asset_scan_failed_in_current_generation",
        distance_threshold: float,
        operation_id: str | None = None,
        operation_kind: str = "pet_scan_commit",
        clustering_pipeline_target: str | None = None,
    ) -> PetIncrementalCommitResult:
        """Replace detections for a bounded asset set without rewriting the index."""

        self.initialize()
        replaced_asset_ids = tuple(dict.fromkeys(str(value) for value in asset_ids if value))
        retry_ids = tuple(
            value
            for value in dict.fromkeys(str(value) for value in retry_asset_ids if value)
            if value not in set(replaced_asset_ids)
        )
        requested_changed_asset_ids = tuple(dict.fromkeys((*replaced_asset_ids, *retry_ids)))
        if not requested_changed_asset_ids:
            return PetIncrementalCommitResult()

        staged = list(detections)
        state_snapshot: _IncrementalStateSnapshot | None = None
        if self._state_repo is not None and staged:
            state_snapshot = self._state_repo._load_incremental_state(
                detection.pet_key for detection in staged
            )
            staged = [
                detection
                for detection in staged
                if detection.pet_key not in state_snapshot.rejected_keys
            ]

        contracts = {
            (
                detection.embedding_pipeline_version,
                int(detection.embedding_dim),
                int(detection.generation_id),
            )
            for detection in staged
        }
        if len(contracts) > 1:
            raise ValueError("A Pet scan commit cannot mix embedding generations.")
        contract = next(iter(contracts), None)
        embedding_contract = (
            EmbeddingContract(str(contract[0]), int(contract[1]), int(contract[2]))
            if contract is not None
            else None
        )
        if embedding_contract is None:
            existing_pets = {pet.pet_id: pet for pet in self.get_all_pet_records()}
            candidate_index = None
            match_context = None
        else:
            match_context = self._profiles_for_contract(embedding_contract)
            existing_pets = match_context.profiles
            candidate_index = match_context.candidate_index
        assignment = self._assign_incremental_pet_ids(
            staged,
            existing_pets=existing_pets,
            candidate_index=candidate_index,
            match_context=match_context,
            state_snapshot=state_snapshot,
            excluded_asset_ids=set(replaced_asset_ids),
            distance_threshold=distance_threshold,
        )
        assigned = list(assignment.detections)
        contract_replacement_pet_ids = assignment.contract_replacement_pet_ids

        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        consumed_migration_candidate = False
        with closing(self._connect()) as conn:
            replaced_rows = self._select_detections_by_asset_ids(
                conn,
                replaced_asset_ids,
            )
            retired_rows: list[sqlite3.Row] = []
            if embedding_contract is not None and contract_replacement_pet_ids:
                candidate_rows = self._select_detections_by_pet_ids(
                    conn,
                    contract_replacement_pet_ids,
                )
                retired_rows = [
                    row
                    for row in candidate_rows
                    if EmbeddingContract(
                        pipeline_version=str(row["embedding_pipeline_version"] or ""),
                        dimension=int(row["embedding_dim"] or 0),
                        generation_id=int(row["generation_id"] or 0),
                    )
                    != embedding_contract
                ]
            previous_rows_by_id = {
                str(row["detection_id"]): row for row in (*replaced_rows, *retired_rows)
            }
            previous_rows = list(previous_rows_by_id.values())
            replaced_asset_id_set = set(replaced_asset_ids)
            retired_asset_ids = tuple(
                dict.fromkeys(
                    str(row["asset_id"])
                    for row in retired_rows
                    if row["asset_id"] and str(row["asset_id"]) not in replaced_asset_id_set
                )
            )
            for chunk in _chunked(replaced_asset_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                consumed_migration_candidate = bool(
                    consumed_migration_candidate
                    or conn.execute(
                        f"""
                        SELECT 1
                        FROM pet_contract_migration_assets
                        WHERE asset_id IN ({placeholders})
                        LIMIT 1
                        """,
                        chunk,
                    ).fetchone()
                )
                conn.execute(
                    f"""
                    DELETE FROM pet_contract_migration_assets
                    WHERE asset_id IN ({placeholders})
                    """,
                    chunk,
                )
            if embedding_contract is not None and retired_rows:
                replacement_ids = tuple(contract_replacement_pet_ids)
                for chunk in _chunked(replacement_ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    conn.execute(
                        f"""
                        INSERT OR IGNORE INTO pet_contract_migration_assets (
                            pet_id, asset_id, embedding_pipeline_version,
                            embedding_dimension, generation_id
                        )
                        SELECT pet_id, asset_id, ?, ?, ?
                        FROM pet_contract_migration_assets
                        WHERE pet_id IN ({placeholders})
                        """,
                        (
                            embedding_contract.pipeline_version,
                            embedding_contract.dimension,
                            embedding_contract.generation_id,
                            *chunk,
                        ),
                    )
                    conn.execute(
                        f"""
                        DELETE FROM pet_contract_migration_assets
                        WHERE pet_id IN ({placeholders})
                          AND NOT (
                              embedding_pipeline_version = ?
                              AND embedding_dimension = ?
                              AND generation_id = ?
                          )
                        """,
                        (
                            *chunk,
                            embedding_contract.pipeline_version,
                            embedding_contract.dimension,
                            embedding_contract.generation_id,
                        ),
                    )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO pet_contract_migration_assets (
                        pet_id, asset_id, embedding_pipeline_version,
                        embedding_dimension, generation_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(row["pet_id"]),
                            str(row["asset_id"]),
                            embedding_contract.pipeline_version,
                            embedding_contract.dimension,
                            embedding_contract.generation_id,
                        )
                        for row in retired_rows
                        if row["pet_id"]
                        and row["asset_id"]
                        and str(row["asset_id"]) not in replaced_asset_id_set
                    ],
                )
            previous_detections = [self._detection_from_row(row) for row in previous_rows]
            changed_asset_ids = tuple(
                dict.fromkeys(
                    (
                        *requested_changed_asset_ids,
                        *(
                            detection.asset_id
                            for detection in previous_detections
                            if detection.asset_id
                        ),
                    )
                )
            )
            previous_thumbnail_paths = tuple(
                str(detection.thumbnail_path)
                for detection in previous_detections
                if detection.thumbnail_path
            )
            old_pet_ids = {
                str(detection.pet_id) for detection in previous_detections if detection.pet_id
            }
            new_pet_ids = {str(detection.pet_id) for detection in assigned if detection.pet_id}
            stale_pet_ids: set[str] = set()
            if retry_ids:
                retry_rows = self._select_detections_by_asset_ids(conn, retry_ids)
                stale_pet_ids.update(str(row["pet_id"]) for row in retry_rows if row["pet_id"])
                for chunk in _chunked(retry_ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    conn.execute(
                        f"""
                        UPDATE pet_detections
                        SET is_stale = 1,
                            stale_reason = ?,
                            source_generation_id = generation_id
                        WHERE asset_id IN ({placeholders})
                        """,
                        (str(stale_reason), *chunk),
                    )
            affected_pet_ids = tuple(sorted(old_pet_ids | new_pet_ids | stale_pet_ids))
            if clustering_pipeline_target and affected_pet_ids:
                queue_generation_id = (
                    int(contract[2])
                    if contract is not None
                    else next(
                        (
                            int(detection.generation_id)
                            for detection in previous_detections
                            if detection.pet_id
                        ),
                        int(
                            (
                                conn.execute(
                                    "SELECT value FROM scan_metadata WHERE key = ?",
                                    ("active_generation_id",),
                                ).fetchone()
                                or {"value": 0}
                            )["value"]
                            or 0
                        ),
                    )
                )
                self._queue_pet_ids_for_clustering_in_connection(
                    conn,
                    affected_pet_ids,
                    target_version=clustering_pipeline_target,
                    generation_id=queue_generation_id,
                )

            for chunk in _chunked(replaced_asset_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM pet_detections WHERE asset_id IN ({placeholders})",
                    chunk,
                )
            retired_detection_ids = tuple(
                dict.fromkeys(str(row["detection_id"]) for row in retired_rows)
            )
            for chunk in _chunked(retired_detection_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM pet_detections WHERE detection_id IN ({placeholders})",
                    chunk,
                )
            if assigned:
                conn.executemany(
                    """
                    INSERT INTO pet_detections (
                        detection_id, pet_key, asset_id, asset_rel,
                        box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                        embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                        image_width, image_height, species_label, quality_score,
                        pet_key_version, embedding_pipeline_version, generation_id,
                        is_stale, stale_reason, source_generation_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [self._detection_to_row(detection) for detection in assigned],
                )

            affected_detections = self._select_detections_by_pet_ids(conn, affected_pet_ids)
            runtime_detections = [self._detection_from_row(row) for row in affected_detections]
            names = {
                pet_id: existing_pets[pet_id].name
                for pet_id in affected_pet_ids
                if pet_id in existing_pets
            }
            created_at = {
                pet_id: existing_pets[pet_id].created_at
                for pet_id in affected_pet_ids
                if pet_id in existing_pets
            }
            if self._state_repo is not None:
                durable_profiles = self._state_repo.get_profiles_by_ids(affected_pet_ids)
                names.update((pet_id, profile.name) for pet_id, profile in durable_profiles.items())
                created_at.update(
                    (pet_id, profile.created_at) for pet_id, profile in durable_profiles.items()
                )
            from .pipeline import build_pet_records_from_detections

            rebuilt_pets = build_pet_records_from_detections(
                runtime_detections,
                names_by_pet_id=names,
                created_at_by_pet_id=created_at,
            )
            for chunk in _chunked(affected_pet_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM pets WHERE pet_id IN ({placeholders})", chunk)
            if rebuilt_pets:
                conn.executemany(
                    """
                    INSERT INTO pets (
                        pet_id, name, key_detection_id, detection_count,
                        center_embedding, embedding_dim, created_at, updated_at,
                        sample_count, profile_state, species_label,
                        embedding_pipeline_version, generation_id,
                        boundary_embeddings, boundary_sample_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._pet_to_row(pet) for pet in rebuilt_pets],
                )
            conn.execute(
                """
                INSERT INTO scan_metadata (key, value)
                VALUES ('updated_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (utc_now_iso(),),
            )
            surviving_pet_ids = {pet.pet_id for pet in rebuilt_pets}
            added = tuple(
                sorted(
                    pet_id
                    for pet_id in new_pet_ids
                    if pet_id not in existing_pets
                    and pet_id not in old_pet_ids
                    and pet_id not in contract_replacement_pet_ids
                )
            )
            removed = tuple(sorted(old_pet_ids - surviving_pet_ids))
            updated = tuple(
                sorted((old_pet_ids | new_pet_ids | stale_pet_ids) - set(added) - set(removed))
            )
            commit_payload = {
                "operation_kind": operation_kind,
                "asset_ids": list(changed_asset_ids),
                "done_asset_ids": list(replaced_asset_ids),
                "retry_asset_ids": list(retry_ids),
                "changed_asset_ids": list(changed_asset_ids),
                "retired_asset_ids": list(retired_asset_ids),
                "affected_pet_ids": list(affected_pet_ids),
                "changed_pet_ids": list(affected_pet_ids),
                "previous_thumbnail_paths": list(previous_thumbnail_paths),
                "added_pet_ids": list(added),
                "updated_pet_ids": list(updated),
                "removed_pet_ids": list(removed),
                "embedding_pipeline_version": contract[0] if contract else "",
                "embedding_dimension": int(contract[1]) if contract else 0,
                "generation_id": int(contract[2]) if contract else 0,
                "clustering_pipeline_target": str(clustering_pipeline_target or ""),
            }
            self._write_runtime_commit(conn, effective_operation_id, commit_payload)
            conn.commit()
            if contract_replacement_pet_ids or consumed_migration_candidate:
                self._invalidate_profile_indexes()
            elif embedding_contract is not None:
                self._update_profile_indexes(
                    embedding_contract,
                    affected_pet_ids=affected_pet_ids,
                    rebuilt_pets=rebuilt_pets,
                )
            # The runtime commit is durable before state sync begins.  Reuse
            # the already-open runtime connection for the terminal marker and
            # pruning, while preserving the unsynced marker if state I/O fails.
            self._sync_runtime_state_payload(
                commit_payload,
                rebuilt_pets,
                runtime_detections,
            )
            self._mark_runtime_state_synced(conn, effective_operation_id)
            if str(effective_operation_id).startswith("internal-"):
                self._prune_runtime_commits_in_connection(conn)
            conn.commit()
        return PetIncrementalCommitResult(
            changed_asset_ids=changed_asset_ids,
            retired_asset_ids=retired_asset_ids,
            previous_thumbnail_paths=previous_thumbnail_paths,
            added_pet_ids=added,
            updated_pet_ids=updated,
            removed_pet_ids=removed,
        )

    def delete_detections_transactionally(
        self,
        detection_ids: Iterable[str],
        *,
        operation_id: str,
        operation_kind: str,
    ) -> PetIncrementalCommitResult:
        """Delete exact detections without reassigning retained embedding generations."""

        ids = tuple(dict.fromkeys(str(value) for value in detection_ids if value))
        if not ids or not operation_id:
            return PetIncrementalCommitResult()
        self.initialize()
        with closing(self._connect()) as conn:
            rows: list[sqlite3.Row] = []
            for chunk in _chunked(ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT detection_id, asset_id, pet_id, thumbnail_path
                        FROM pet_detections
                        WHERE detection_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
            if not rows:
                return PetIncrementalCommitResult()
            changed_asset_ids = tuple(
                dict.fromkeys(str(row["asset_id"]) for row in rows if row["asset_id"])
            )
            affected_pet_ids = tuple(
                dict.fromkeys(str(row["pet_id"]) for row in rows if row["pet_id"])
            )
            previous_thumbnail_paths = tuple(
                str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]
            )
            for chunk in _chunked(ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM pet_detections WHERE detection_id IN ({placeholders})",
                    chunk,
                )
            _detections, rebuilt_pets = self._rebuild_pet_records_in_connection(conn)
            surviving = {pet.pet_id for pet in rebuilt_pets}
            removed = tuple(sorted(set(affected_pet_ids) - surviving))
            updated = tuple(sorted(set(affected_pet_ids) & surviving))
            self._write_runtime_commit(
                conn,
                operation_id,
                {
                    "operation_kind": operation_kind,
                    "detection_ids": list(ids),
                    "affected_pet_ids": list(affected_pet_ids),
                    "changed_pet_ids": list(affected_pet_ids),
                    "changed_asset_ids": list(changed_asset_ids),
                    "previous_thumbnail_paths": list(previous_thumbnail_paths),
                    "added_pet_ids": [],
                    "updated_pet_ids": list(updated),
                    "removed_pet_ids": list(removed),
                },
            )
            conn.commit()
        self.complete_runtime_state_sync(operation_id)
        self._refresh_people_group_assets_for_pets(affected_pet_ids)
        return PetIncrementalCommitResult(
            previous_thumbnail_paths=previous_thumbnail_paths,
            updated_pet_ids=updated,
            removed_pet_ids=removed,
        )

    @staticmethod
    def _write_runtime_commit(
        conn: sqlite3.Connection,
        operation_id: str,
        payload: dict[str, object],
    ) -> None:
        timestamp = utc_now_iso()
        conn.execute(
            """
            INSERT INTO pet_runtime_commits (
                operation_id, payload_json, state_synced, created_at, updated_at
            ) VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                str(operation_id),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                timestamp,
                timestamp,
            ),
        )

    def recover_pending_runtime_state_syncs(self) -> tuple[str, ...]:
        """Finish orphaned runtime commits before accepting another mutation."""

        if not self._initialized:
            return ()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT operation_id
                FROM pet_runtime_commits
                WHERE state_synced = 0
                ORDER BY rowid ASC
                """
            ).fetchall()
        recovered: list[str] = []
        for row in rows:
            operation_id = str(row["operation_id"])
            self.complete_runtime_state_sync(operation_id)
            recovered.append(operation_id)
        return tuple(recovered)

    def get_runtime_commit(self, operation_id: str) -> dict[str, object] | None:
        if not operation_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT payload_json, state_synced
                FROM pet_runtime_commits
                WHERE operation_id = ?
                """,
                (str(operation_id),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"] or "{}"))
        payload["state_synced"] = bool(row["state_synced"])
        return payload

    def complete_runtime_state_sync(self, operation_id: str) -> dict[str, object] | None:
        """Idempotently mirror one committed runtime transaction into durable state."""

        payload = self.get_runtime_commit(operation_id)
        if payload is None or bool(payload.get("state_synced")):
            return payload
        affected_pet_ids = tuple(
            str(value) for value in payload.get("affected_pet_ids", ()) if value
        )
        with closing(self._connect()) as conn:
            pet_rows = self._select_pets_by_ids(conn, affected_pet_ids)
            detection_rows = self._select_detections_by_pet_ids(conn, affected_pet_ids)
        pets = [self._pet_from_row(row) for row in pet_rows]
        detections = [self._detection_from_row(row) for row in detection_rows]
        self._sync_runtime_state_payload(payload, pets, detections)
        with closing(self._connect()) as conn:
            self._mark_runtime_state_synced(conn, operation_id)
            if str(operation_id).startswith("internal-"):
                self._prune_runtime_commits_in_connection(conn)
            conn.commit()
        payload["state_synced"] = True
        return payload

    def _sync_runtime_state_payload(
        self,
        payload: dict[str, object],
        pets: list[PetRecord],
        detections: list[PetDetectionRecord],
    ) -> None:
        """Apply one committed runtime payload without re-reading its rows."""

        affected_pet_ids = tuple(
            str(value) for value in payload.get("affected_pet_ids", ()) if value
        )
        if self._state_repo is not None:
            operation_kind = str(payload.get("operation_kind") or "")
            if operation_kind == "pet_delete_detection":
                pet_key = str(payload.get("pet_key") or "")
                detection_id = str(payload.get("detection_id") or "")
                if pet_key:
                    self._state_repo.add_rejected_pet_key(pet_key)
                if detection_id:
                    self._state_repo.clear_cover_for_detection(detection_id)
            self._state_repo.sync_scan_results(
                pets,
                detections,
                replaced_pet_ids=affected_pet_ids,
            )
            if operation_kind == "pet_move_detection_new":
                new_pet_id = str(payload.get("new_pet_id") or "")
                new_name = payload.get("new_name")
                if new_pet_id and new_name:
                    self._state_repo.rename_pet(new_pet_id, str(new_name))
                    with closing(self._connect()) as conn:
                        conn.execute(
                            "UPDATE pets SET name = ?, updated_at = ? WHERE pet_id = ?",
                            (normalize_name(str(new_name)), utc_now_iso(), new_pet_id),
                        )
                        conn.commit()
                if new_pet_id:
                    self._state_repo.confirm_pet(new_pet_id)
            elif operation_kind == "pet_move_detection":
                target_pet_id = str(payload.get("target_pet_id") or "")
                if target_pet_id:
                    self._state_repo.confirm_pet(target_pet_id)
        if str(payload.get("operation_kind") or "") == "pet_delete_detection":
            face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
            face_index_path = self._db_path.parent.parent / "faces" / "face_index.db"
            detection_id = str(payload.get("detection_id") or "")
            if face_state_path.exists() and detection_id:
                FaceStateRepository(face_state_path).clear_annotation_identity_assignment(
                    "pet", detection_id
                )
                if face_index_path.exists():
                    FaceRepository(
                        face_index_path,
                        face_state_path,
                    ).refresh_all_group_assets()

    @staticmethod
    def _mark_runtime_state_synced(
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> None:
        conn.execute(
            """
            UPDATE pet_runtime_commits
            SET state_synced = 1, updated_at = ?
            WHERE operation_id = ?
            """,
            (utc_now_iso(), str(operation_id)),
        )

    def prune_runtime_commits(
        self,
        *,
        protected_operation_ids: Iterable[str] = (),
        high_watermark: int = 1200,
        retain: int = 1000,
    ) -> int:
        """Bound synced commit history while retaining unfinished journal owners."""

        protected = tuple(dict.fromkeys(str(value) for value in protected_operation_ids if value))
        with closing(self._connect()) as conn:
            deleted = self._prune_runtime_commits_in_connection(
                conn,
                protected_operation_ids=protected,
                high_watermark=high_watermark,
                retain=retain,
            )
            conn.commit()
            return deleted

    @staticmethod
    def _prune_runtime_commits_in_connection(
        conn: sqlite3.Connection,
        *,
        protected_operation_ids: Iterable[str] = (),
        high_watermark: int = 1200,
        retain: int = 1000,
    ) -> int:
        protected = tuple(dict.fromkeys(str(value) for value in protected_operation_ids if value))
        where = "state_synced = 1"
        parameters: tuple[object, ...] = ()
        if protected:
            placeholders = ", ".join("?" for _ in protected)
            where += f" AND operation_id NOT IN ({placeholders})"
            parameters = protected
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM pet_runtime_commits WHERE {where}",
            parameters,
        ).fetchone()
        if row is None or int(row["count"] or 0) <= high_watermark:
            return 0
        deleted = 0
        while True:
            stale = conn.execute(
                f"""
                SELECT operation_id
                FROM pet_runtime_commits
                WHERE {where}
                ORDER BY rowid DESC
                LIMIT 500 OFFSET ?
                """,
                (*parameters, retain),
            ).fetchall()
            if not stale:
                break
            stale_ids = tuple(str(item["operation_id"]) for item in stale)
            placeholders = ", ".join("?" for _ in stale_ids)
            cursor = conn.execute(
                f"DELETE FROM pet_runtime_commits WHERE operation_id IN ({placeholders})",
                stale_ids,
            )
            deleted += int(cursor.rowcount or 0)
            if len(stale_ids) < 500:
                break
        return deleted

    def _assign_incremental_pet_ids(
        self,
        detections: list[PetDetectionRecord],
        *,
        existing_pets: dict[str, PetRecord],
        candidate_index: _ProfileCandidateIndex | None = None,
        match_context: _ProfileMatchContext | None = None,
        state_snapshot: _IncrementalStateSnapshot | None = None,
        excluded_asset_ids: set[str],
        distance_threshold: float,
    ) -> _IncrementalPetAssignment:
        if not detections:
            return _IncrementalPetAssignment()
        redirects = state_snapshot.redirects if state_snapshot is not None else {}
        key_map = state_snapshot.key_map if state_snapshot is not None else {}
        durable_profiles = state_snapshot.durable_profiles if state_snapshot is not None else {}
        stable_profiles = {
            pet_id: pet
            for pet_id, pet in existing_pets.items()
            if str(pet.profile_state or "unstable") == "stable"
        }
        centers = (
            match_context.centers
            if match_context is not None
            else {
                pet_id: normalize_vector(pet.center_embedding)
                for pet_id, pet in stable_profiles.items()
            }
        )
        species = (
            match_context.species
            if match_context is not None
            else {
                pet_id: _normalize_species_label(pet.species_label)
                for pet_id, pet in stable_profiles.items()
            }
        )
        member_samples = match_context.member_samples if match_context is not None else {}
        candidate_index = candidate_index or _ProfileCandidateIndex(centers, species)
        staged_samples: dict[str, list[tuple[str, np.ndarray]]] = {}
        assigned_by_detection_id: dict[str, PetDetectionRecord] = {}
        contract_replacement_pet_ids: set[str] = set()
        staged_candidate_species: dict[str, str | None] = {}
        unmatched: list[PetDetectionRecord] = []

        # Resolve durable/manual keys before embedding matches. Sorting makes the
        # incremental result independent of the caller's detection order.
        ordered = sorted(
            detections,
            key=lambda value: (value.asset_id, value.pet_key, value.detection_id),
        )
        for detection in ordered:
            detection_species = _normalize_species_label(detection.species_label)
            mapped_id = key_map.get(detection.pet_key, "")
            candidate_id = redirects.get(mapped_id, mapped_id)
            durable_profile = durable_profiles.get(candidate_id)
            known_species = (
                species.get(candidate_id)
                if candidate_id in species
                else _normalize_species_label(
                    existing_pets[candidate_id].species_label
                    if candidate_id in existing_pets
                    else durable_profile.species_label
                    if durable_profile is not None
                    else None
                )
            )
            if candidate_id and not _species_compatible(detection_species, known_species):
                candidate_id = ""
            elif candidate_id and candidate_id not in existing_pets and durable_profile is None:
                candidate_id = ""
            elif candidate_id and any(
                asset_id == detection.asset_id
                for asset_id, _sample in staged_samples.get(candidate_id, ())
            ):
                candidate_id = ""
            if candidate_id:
                candidate_profile = existing_pets.get(candidate_id) or durable_profile
                if candidate_profile is not None and EmbeddingContract.from_profile(
                    candidate_profile
                ) != EmbeddingContract.from_detection(detection):
                    contract_replacement_pet_ids.add(candidate_id)
                    if str(candidate_profile.profile_state or "unstable") == "stable":
                        staged_candidate_species[candidate_id] = known_species
                assigned_by_detection_id[detection.detection_id] = replace(
                    detection, pet_id=candidate_id
                )
                staged_samples.setdefault(candidate_id, []).append(
                    (detection.asset_id, normalize_vector(detection.embedding))
                )
            else:
                unmatched.append(detection)

        still_unmatched: list[PetDetectionRecord] = []
        for detection in unmatched:
            migration_profiles = {
                pet_id: existing_pets[pet_id]
                for pet_id in (
                    match_context.migration_candidates_by_asset.get(detection.asset_id, ())
                    if match_context is not None
                    else ()
                )
                if pet_id in existing_pets
            }
            candidate_id = self._nearest_compatible_pet_id(
                detection,
                member_samples=member_samples,
                staged_samples=staged_samples,
                excluded_asset_ids=excluded_asset_ids,
                candidate_index=candidate_index,
                staged_candidate_species=staged_candidate_species,
                migration_profiles=migration_profiles,
                distance_threshold=distance_threshold,
            )
            if candidate_id:
                assigned_by_detection_id[detection.detection_id] = replace(
                    detection, pet_id=candidate_id
                )
                staged_samples.setdefault(candidate_id, []).append(
                    (detection.asset_id, normalize_vector(detection.embedding))
                )
            else:
                still_unmatched.append(detection)

        if still_unmatched:
            from .pipeline import cluster_pet_records

            clustered, _ = cluster_pet_records(
                still_unmatched,
                distance_threshold=distance_threshold,
            )
            assigned_by_detection_id.update(
                {detection.detection_id: detection for detection in clustered}
            )
        return _IncrementalPetAssignment(
            detections=tuple(
                assigned_by_detection_id[detection.detection_id] for detection in detections
            ),
            contract_replacement_pet_ids=tuple(sorted(contract_replacement_pet_ids)),
        )

    def _profiles_for_contract(
        self,
        contract: EmbeddingContract,
    ) -> _ProfileMatchContext:
        with self._mutation_lock:
            context = self._match_contexts.get(contract)
            if context is None:
                profiles = {
                    pet.pet_id: pet
                    for pet in self.get_all_pet_records()
                    if EmbeddingContract.from_pet(pet) == contract
                }
                stable_profiles = {
                    pet_id: pet
                    for pet_id, pet in profiles.items()
                    if str(pet.profile_state or "unstable") == "stable"
                }
                centers = {
                    pet_id: normalize_vector(pet.center_embedding)
                    for pet_id, pet in stable_profiles.items()
                }
                species = {
                    pet_id: _normalize_species_label(pet.species_label)
                    for pet_id, pet in stable_profiles.items()
                }
                all_centers = {
                    pet_id: normalize_vector(pet.center_embedding)
                    for pet_id, pet in profiles.items()
                }
                all_species = {
                    pet_id: _normalize_species_label(pet.species_label)
                    for pet_id, pet in profiles.items()
                }
                migration_candidates_by_asset = self._migration_candidates_for_contract(contract)
                context = _ProfileMatchContext(
                    profiles=profiles,
                    centers=centers,
                    species=species,
                    all_centers=all_centers,
                    all_species=all_species,
                    member_samples={},
                    migration_candidates_by_asset=migration_candidates_by_asset,
                    candidate_index=_ProfileCandidateIndex(centers, species),
                    consolidation_candidate_index=_ProfileCandidateIndex(
                        all_centers,
                        all_species,
                    ),
                )
                self._match_contexts[contract] = context
            return context

    def _update_profile_indexes(
        self,
        contract: EmbeddingContract,
        *,
        affected_pet_ids: tuple[str, ...],
        rebuilt_pets: list[PetRecord],
    ) -> None:
        with self._mutation_lock:
            context = self._match_contexts.get(contract)
            if context is None:
                return
            for pet_id in affected_pet_ids:
                context.candidate_index.remove(pet_id)
                context.consolidation_candidate_index.remove(pet_id)
                context.profiles.pop(pet_id, None)
                context.member_samples.pop(pet_id, None)
                context.centers.pop(pet_id, None)
                context.species.pop(pet_id, None)
                context.all_centers.pop(pet_id, None)
                context.all_species.pop(pet_id, None)
            for pet in rebuilt_pets:
                pet_contract = EmbeddingContract.from_pet(pet)
                if pet_contract != contract:
                    continue
                context.profiles[pet.pet_id] = pet
                context.all_centers[pet.pet_id] = normalize_vector(pet.center_embedding)
                context.all_species[pet.pet_id] = _normalize_species_label(pet.species_label)
                if str(pet.profile_state or "unstable") == "stable":
                    context.centers[pet.pet_id] = normalize_vector(pet.center_embedding)
                    context.species[pet.pet_id] = _normalize_species_label(pet.species_label)
            context.candidate_index.upsert_many(
                (
                    pet.pet_id,
                    normalize_vector(pet.center_embedding),
                    _normalize_species_label(pet.species_label),
                )
                for pet in rebuilt_pets
                if EmbeddingContract.from_pet(pet) == contract
                and str(pet.profile_state or "unstable") == "stable"
            )
            context.consolidation_candidate_index.upsert_many(
                (
                    pet.pet_id,
                    normalize_vector(pet.center_embedding),
                    _normalize_species_label(pet.species_label),
                )
                for pet in rebuilt_pets
                if EmbeddingContract.from_pet(pet) == contract
            )

    def _invalidate_profile_indexes(self) -> None:
        with self._mutation_lock:
            self._match_contexts.clear()

    def _nearest_compatible_pet_id(
        self,
        detection: PetDetectionRecord,
        *,
        member_samples: dict[str, tuple[tuple[str, np.ndarray], ...]],
        staged_samples: dict[str, list[tuple[str, np.ndarray]]],
        excluded_asset_ids: set[str],
        candidate_index: _ProfileCandidateIndex,
        staged_candidate_species: dict[str, str | None],
        distance_threshold: float,
        migration_profiles: dict[str, PetRecord] | None = None,
    ) -> str:
        from .pipeline import PET_CLUSTER_DIAMETER_MULTIPLIER

        migration_profiles = migration_profiles or {}
        detection_species = _normalize_species_label(detection.species_label)
        diameter_threshold = distance_threshold * PET_CLUSTER_DIAMETER_MULTIPLIER
        limit = 8
        seen: set[str] = set()
        while True:
            indexed_candidates = candidate_index.search(
                detection.embedding,
                species_label=detection_species,
                limit=limit,
            )
            staged_candidates = [
                (
                    cosine_distance(
                        detection.embedding,
                        normalize_vector(
                            np.mean([sample for _asset_id, sample in samples], axis=0)
                        ),
                    ),
                    pet_id,
                )
                for pet_id, samples in staged_samples.items()
                if pet_id in staged_candidate_species
                and samples
                and _species_compatible(
                    detection_species,
                    staged_candidate_species.get(pet_id),
                )
            ]
            migration_candidates = [
                (
                    cosine_distance(detection.embedding, profile.center_embedding),
                    pet_id,
                )
                for pet_id, profile in migration_profiles.items()
                if profile.center_embedding.size
                and profile.center_embedding.shape == detection.embedding.shape
                and _species_compatible(
                    detection_species,
                    _normalize_species_label(profile.species_label),
                )
            ]
            candidates = sorted((*indexed_candidates, *staged_candidates, *migration_candidates))
            within_threshold: list[tuple[float, str]] = []
            for center_distance, pet_id in candidates:
                if pet_id in seen:
                    continue
                seen.add(pet_id)
                if center_distance > diameter_threshold:
                    break
                within_threshold.append((center_distance, pet_id))

            missing = tuple(
                pet_id
                for _center_distance, pet_id in within_threshold
                if pet_id not in member_samples and pet_id not in staged_candidate_species
            )
            if missing:
                member_samples.update(self._load_cluster_member_samples_for_pets(missing))
            compatible_candidates: list[tuple[float, float, str]] = []
            for center_distance, pet_id in within_threshold:
                retained_samples = tuple(
                    (asset_id, sample)
                    for asset_id, sample in member_samples.get(pet_id, ())
                    if pet_id not in staged_candidate_species
                    and asset_id not in excluded_asset_ids
                )
                current_staged_samples = tuple(staged_samples.get(pet_id, ()))
                if any(
                    asset_id == detection.asset_id
                    for asset_id, _sample in (*retained_samples, *current_staged_samples)
                ):
                    continue
                samples = tuple(
                    sample
                    for _asset_id, sample in (*retained_samples, *current_staged_samples)
                )
                if not samples:
                    if center_distance <= distance_threshold:
                        compatible_candidates.append((center_distance, center_distance, pet_id))
                    continue
                member_distances = tuple(
                    cosine_distance(detection.embedding, sample) for sample in samples
                )
                nearest_member_distance = min(member_distances)
                if nearest_member_distance > distance_threshold:
                    continue
                if max(member_distances) > diameter_threshold:
                    continue
                compatible_candidates.append((nearest_member_distance, center_distance, pet_id))
            if compatible_candidates:
                return min(compatible_candidates)[2]

            indexed_threshold_exhausted = bool(
                indexed_candidates and indexed_candidates[-1][0] > diameter_threshold
            )
            if indexed_threshold_exhausted or len(indexed_candidates) < limit:
                return ""
            limit *= 2

    def _migration_candidates_for_contract(
        self,
        contract: EmbeddingContract,
    ) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT asset_id, pet_id
                FROM pet_contract_migration_assets
                WHERE embedding_pipeline_version = ?
                  AND embedding_dimension = ?
                  AND generation_id = ?
                ORDER BY asset_id, pet_id
                """,
                (contract.pipeline_version, contract.dimension, contract.generation_id),
            ).fetchall()
        for row in rows:
            grouped.setdefault(str(row["asset_id"]), []).append(str(row["pet_id"]))
        return {asset_id: tuple(dict.fromkeys(pet_ids)) for asset_id, pet_ids in grouped.items()}

    def _load_cluster_member_samples_for_pets(
        self,
        pet_ids: tuple[str, ...],
    ) -> dict[str, tuple[tuple[str, np.ndarray], ...]]:
        grouped: dict[str, list[tuple[str, str, np.ndarray]]] = {pet_id: [] for pet_id in pet_ids}
        with closing(self._connect()) as conn:
            rows = self._select_detections_by_pet_ids(conn, pet_ids)
        for row in rows:
            pet_id = str(row["pet_id"] or "")
            if pet_id not in grouped:
                continue
            grouped[pet_id].append(
                (
                    str(row["detection_id"]),
                    str(row["asset_id"]),
                    normalize_vector(
                        deserialize_embedding(row["embedding"], int(row["embedding_dim"] or 0))
                    ),
                )
            )
        return {
            pet_id: tuple(
                (asset_id, sample)
                for _, asset_id, sample in sorted(samples, key=lambda item: item[0])
            )
            for pet_id, samples in grouped.items()
        }

    def _select_detections_by_asset_ids(
        self, conn: sqlite3.Connection, asset_ids: tuple[str, ...]
    ) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for chunk in _chunked(asset_ids, 500):
            placeholders = ", ".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"SELECT * FROM pet_detections WHERE asset_id IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        return rows

    def _select_detections_by_pet_ids(
        self, conn: sqlite3.Connection, pet_ids: tuple[str, ...]
    ) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for chunk in _chunked(pet_ids, 500):
            placeholders = ", ".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"SELECT * FROM pet_detections WHERE pet_id IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        return rows

    def _select_pets_by_ids(
        self, conn: sqlite3.Connection, pet_ids: tuple[str, ...]
    ) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for chunk in _chunked(pet_ids, 500):
            placeholders = ", ".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"SELECT * FROM pets WHERE pet_id IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        return rows

    def get_scan_metadata(self, key: str) -> str | None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                (normalized_key,),
            ).fetchone()
        if row is None:
            return None
        value = row["value"]
        return str(value) if value is not None else None

    def assign_embedding_generation(
        self,
        detections: Iterable[PetDetectionRecord],
    ) -> tuple[list[PetDetectionRecord], int]:
        staged = list(detections)
        if not staged:
            active = self.get_scan_metadata("active_generation_id")
            return [], int(active or 0)
        contracts = {
            (detection.embedding_pipeline_version, int(detection.embedding_dim))
            for detection in staged
        }
        if len(contracts) != 1:
            raise ValueError("A Pet batch must use one embedding version and dimension.")
        version, dimension = next(iter(contracts))
        self.initialize()
        with self._mutation_lock, closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT generation_id
                FROM embedding_generations
                WHERE pipeline_version = ? AND embedding_dimension = ?
                """,
                (version, dimension),
            ).fetchone()
            if row is not None:
                generation_id = int(row["generation_id"])
            else:
                maximum = conn.execute(
                    """
                    SELECT MAX(generation_id) AS generation_id
                    FROM (
                        SELECT generation_id FROM pet_detections
                        UNION ALL SELECT generation_id FROM pets
                        UNION ALL SELECT generation_id FROM embedding_generations
                    )
                    """
                ).fetchone()
                has_active = conn.execute(
                    "SELECT 1 FROM embedding_generations WHERE status = 'active' LIMIT 1"
                ).fetchone()
                generation_id = (
                    0
                    if maximum["generation_id"] is None and has_active is None
                    else int(maximum["generation_id"] or 0) + 1
                )
                conn.execute(
                    """
                    INSERT INTO embedding_generations (
                        generation_id, pipeline_version, embedding_dimension,
                        status, created_at
                    ) VALUES (?, ?, ?, 'staged', ?)
                    """,
                    (generation_id, version, dimension, utc_now_iso()),
                )
                conn.commit()
        return [replace(item, generation_id=generation_id) for item in staged], generation_id

    def activate_embedding_generation(
        self,
        *,
        generation_id: int,
        embedding_pipeline_version: str,
        embedding_dimension: int,
        detector_pipeline_version: str | None = None,
    ) -> None:
        self.initialize()
        metadata = {
            "active_generation_id": str(int(generation_id)),
            "active_embedding_pipeline_version": str(embedding_pipeline_version),
            "active_embedding_dimension": str(int(embedding_dimension)),
        }
        if detector_pipeline_version:
            metadata["detector_pipeline_version"] = str(detector_pipeline_version)
        with self._mutation_lock, closing(self._connect()) as conn:
            conn.execute(
                "UPDATE embedding_generations SET status = 'readable' WHERE status = 'active'"
            )
            conn.execute(
                """
                INSERT INTO embedding_generations (
                    generation_id, pipeline_version, embedding_dimension,
                    status, created_at, activated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                ON CONFLICT(generation_id) DO UPDATE SET
                    pipeline_version = excluded.pipeline_version,
                    embedding_dimension = excluded.embedding_dimension,
                    status = 'active',
                    activated_at = excluded.activated_at
                """,
                (
                    int(generation_id),
                    str(embedding_pipeline_version),
                    int(embedding_dimension),
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            conn.executemany(
                """
                INSERT INTO scan_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                list(metadata.items()),
            )
            conn.commit()

    def mark_asset_detections_stale(
        self,
        asset_ids: Iterable[str],
        *,
        reason: str,
    ) -> tuple[str, ...]:
        ids = tuple(dict.fromkeys(str(value) for value in asset_ids if value))
        if not ids:
            return ()
        self.initialize()
        pet_ids: set[str] = set()
        with closing(self._connect()) as conn:
            for chunk in _chunked(ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT pet_id
                    FROM pet_detections
                    WHERE asset_id IN ({placeholders}) AND pet_id IS NOT NULL
                    """,
                    chunk,
                ).fetchall()
                pet_ids.update(str(row["pet_id"]) for row in rows if row["pet_id"])
                conn.execute(
                    f"""
                    UPDATE pet_detections
                    SET is_stale = 1,
                        stale_reason = ?,
                        source_generation_id = generation_id
                    WHERE asset_id IN ({placeholders})
                    """,
                    (str(reason), *chunk),
                )
            conn.commit()
        return tuple(sorted(pet_ids))

    def set_scan_metadata(self, key: str, value: str) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO scan_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (normalized_key, str(value)),
            )
            conn.commit()

    def set_scan_metadata_many(self, values: dict[str, object]) -> None:
        """Persist related scan metadata in one SQLite transaction."""

        normalized = [
            (str(key).strip(), str(value)) for key, value in values.items() if str(key).strip()
        ]
        if not normalized:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO scan_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                normalized,
            )
            conn.commit()

    @staticmethod
    def _set_scan_metadata_in_connection(
        conn: sqlite3.Connection,
        values: dict[str, object],
    ) -> None:
        normalized = [
            (str(key).strip(), str(value)) for key, value in values.items() if str(key).strip()
        ]
        if not normalized:
            return
        conn.executemany(
            """
            INSERT INTO scan_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            normalized,
        )

    @classmethod
    def _queue_pet_ids_for_clustering_in_connection(
        cls,
        conn: sqlite3.Connection,
        pet_ids: Iterable[str],
        *,
        target_version: str,
        generation_id: int,
    ) -> int:
        ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        target = str(target_version or "").strip()
        if not ids or not target:
            return 0
        timestamp = utc_now_iso()
        before = int(conn.total_changes)
        conn.executemany(
            """
            INSERT OR IGNORE INTO pet_clustering_consolidation_queue (
                target_version, generation_id, pet_id, queued_at
            ) VALUES (?, ?, ?, ?)
            """,
            [(target, int(generation_id), pet_id, timestamp) for pet_id in ids],
        )
        cls._set_scan_metadata_in_connection(
            conn,
            {
                "clustering_pipeline_target": target,
                "clustering_consolidation_state": "pending",
            },
        )
        return max(0, int(conn.total_changes) - before)

    def queue_pet_ids_for_clustering(
        self,
        pet_ids: Iterable[str],
        *,
        target_version: str,
        generation_id: int | None = None,
    ) -> int:
        self.initialize()
        with self._mutation_lock, closing(self._connect()) as conn:
            effective_generation = generation_id
            if effective_generation is None:
                row = conn.execute(
                    "SELECT value FROM scan_metadata WHERE key = ?",
                    ("active_generation_id",),
                ).fetchone()
                effective_generation = int(row["value"] or 0) if row is not None else 0
            queued = self._queue_pet_ids_for_clustering_in_connection(
                conn,
                pet_ids,
                target_version=target_version,
                generation_id=int(effective_generation),
            )
            conn.commit()
        return queued

    def prepare_clustering_pipeline(self, *, target_version: str) -> int:
        """Durably seed a version upgrade without reclustering the full library."""

        target = str(target_version or "").strip()
        if not target:
            return 0
        self.initialize()
        with self._mutation_lock, closing(self._connect()) as conn:
            generation_row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                ("active_generation_id",),
            ).fetchone()
            generation_id = int(generation_row["value"] or 0) if generation_row else 0
            version_row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                ("clustering_pipeline_version",),
            ).fetchone()
            state_row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                ("clustering_consolidation_state",),
            ).fetchone()
            completed_version = str(version_row["value"] or "") if version_row else ""
            state = str(state_row["value"] or "") if state_row else ""
            pending_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM pet_clustering_consolidation_queue
                WHERE target_version = ? AND generation_id = ?
                """,
                (target, generation_id),
            ).fetchone()
            pending_count = int(pending_row["count"] or 0) if pending_row else 0
            if completed_version == target:
                if pending_count == 0:
                    if state != "clean":
                        self._set_scan_metadata_in_connection(
                            conn,
                            {
                                "clustering_pipeline_target": target,
                                "clustering_consolidation_state": "clean",
                            },
                        )
                        conn.commit()
                    return 0
                # A normal v3 batch or an interrupted v3 consolidation already
                # populated the exact affected set. Preserve it across worker
                # restarts instead of accidentally promoting the recovery into
                # a full-library migration.
                if state != "pending":
                    self._set_scan_metadata_in_connection(
                        conn,
                        {
                            "clustering_pipeline_target": target,
                            "clustering_consolidation_state": "pending",
                        },
                    )
                    conn.commit()
                return pending_count

            conn.execute(
                """
                DELETE FROM pet_clustering_consolidation_queue
                WHERE target_version != ? OR generation_id != ?
                """,
                (target, generation_id),
            )
            pet_rows = conn.execute(
                """
                SELECT DISTINCT pet_id
                FROM pet_detections
                WHERE generation_id = ? AND pet_id IS NOT NULL AND pet_id != ''
                ORDER BY pet_id
                """,
                (generation_id,),
            ).fetchall()
            pet_ids = tuple(str(row["pet_id"]) for row in pet_rows if row["pet_id"])
            if pet_ids:
                self._queue_pet_ids_for_clustering_in_connection(
                    conn,
                    pet_ids,
                    target_version=target,
                    generation_id=generation_id,
                )
            else:
                self._set_scan_metadata_in_connection(
                    conn,
                    {
                        "clustering_pipeline_target": target,
                        "clustering_pipeline_version": target,
                        "clustering_consolidation_state": "clean",
                    },
                )
            conn.commit()
        return len(pet_ids)

    def has_pending_clustering_consolidation(self, *, target_version: str) -> bool:
        target = str(target_version or "").strip()
        if not target:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            generation_row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                ("active_generation_id",),
            ).fetchone()
            generation_id = int(generation_row["value"] or 0) if generation_row else 0
            row = conn.execute(
                """
                SELECT 1
                FROM pet_clustering_consolidation_queue
                WHERE target_version = ? AND generation_id = ?
                LIMIT 1
                """,
                (target, generation_id),
            ).fetchone()
            state_row = conn.execute(
                "SELECT value FROM scan_metadata WHERE key = ?",
                ("clustering_consolidation_state",),
            ).fetchone()
        state = str(state_row["value"] or "") if state_row else ""
        return row is not None or state in {"pending", "running"}

    def set_clustering_consolidation_state(
        self,
        state: str,
        *,
        target_version: str,
    ) -> None:
        normalized = str(state or "").strip().lower()
        if normalized not in {"clean", "pending", "running"}:
            raise ValueError(f"Unsupported Pet clustering consolidation state: {state}")
        self.set_scan_metadata_many(
            {
                "clustering_pipeline_target": target_version,
                "clustering_consolidation_state": normalized,
            }
        )

    def sync_runtime_state(self) -> None:
        if self._state_repo is None:
            return
        self._state_repo.sync_scan_results(
            self.get_all_pet_records(),
            self.get_all_detections(),
            replaced_pet_ids=(
                profile.pet_id for profile in self._state_repo.get_identity_profiles()
            ),
        )

    def prune_unreferenced_thumbnails(self, candidates: Iterable[str | Path]) -> int:
        """Delete candidate thumbnails no longer referenced by index or state."""

        thumbnail_dir = (self._db_path.parent / "thumbnails").resolve()
        if not thumbnail_dir.is_dir():
            return 0

        referenced_paths = {
            (self._db_path.parent / str(detection.thumbnail_path)).resolve()
            for detection in self.get_all_detections()
            if detection.thumbnail_path
        }
        if self._state_repo is not None:
            referenced_paths.update(
                (self._db_path.parent / thumbnail_path).resolve()
                for thumbnail_path in self._state_repo.get_cover_thumbnail_paths()
            )

        removed = 0
        for stored_path in dict.fromkeys(str(path) for path in candidates if path):
            candidate = (self._db_path.parent / stored_path).resolve()
            if (
                candidate.parent != thumbnail_dir
                or not candidate.is_file()
                or candidate in referenced_paths
            ):
                continue
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                LOGGER.warning(
                    "Failed to remove unreferenced pet thumbnail %s",
                    candidate,
                    exc_info=True,
                )
        return removed

    @staticmethod
    def _candidate_pet_ids_within_distance(
        embedding: np.ndarray,
        *,
        species_label: str | None,
        candidate_index: _ProfileCandidateIndex,
        maximum_distance: float,
    ) -> tuple[str, ...]:
        limit = 8
        selected: dict[str, float] = {}
        while True:
            candidates = candidate_index.search(
                embedding,
                species_label=species_label,
                limit=limit,
            )
            for distance, pet_id in candidates:
                if distance <= maximum_distance:
                    selected[pet_id] = min(distance, selected.get(pet_id, float("inf")))
            if not candidates or len(candidates) < limit or candidates[-1][0] > maximum_distance:
                break
            limit *= 2
        return tuple(
            pet_id
            for pet_id, _ in sorted(
                selected.items(),
                key=lambda item: (item[1], item[0]),
            )
        )

    def consolidate_pending_clustering(
        self,
        *,
        target_version: str,
        distance_threshold: float,
        operation_id: str,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PetClusteringConsolidationResult:
        """Recluster only components reachable from durable pending Pet seeds."""

        from .pipeline import (
            PET_CLUSTER_DIAMETER_MULTIPLIER,
            build_pet_records_from_detections,
            canonicalize_pet_identities,
            cluster_pet_records,
        )

        target = str(target_version or "").strip()
        if not target or not operation_id:
            return PetClusteringConsolidationResult()
        cancelled = is_cancelled or (lambda: False)
        self.initialize()
        with self._mutation_lock:
            if cancelled():
                raise PetClusteringConsolidationCancelledError()
            generation_id = int(self.get_scan_metadata("active_generation_id") or 0)
            embedding_version = str(
                self.get_scan_metadata("active_embedding_pipeline_version") or ""
            )
            embedding_dimension = int(self.get_scan_metadata("active_embedding_dimension") or 0)
            with closing(self._connect()) as conn:
                seed_rows = conn.execute(
                    """
                    SELECT pet_id
                    FROM pet_clustering_consolidation_queue
                    WHERE target_version = ? AND generation_id = ?
                    ORDER BY pet_id
                    """,
                    (target, generation_id),
                ).fetchall()
                if not embedding_version or embedding_dimension <= 0:
                    contract_rows = conn.execute(
                        """
                        SELECT DISTINCT embedding_pipeline_version, embedding_dim
                        FROM pet_detections
                        WHERE generation_id = ?
                        """,
                        (generation_id,),
                    ).fetchall()
                    if len(contract_rows) == 1:
                        embedding_version = str(
                            contract_rows[0]["embedding_pipeline_version"] or ""
                        )
                        embedding_dimension = int(contract_rows[0]["embedding_dim"] or 0)
            seed_ids = tuple(str(row["pet_id"]) for row in seed_rows if row["pet_id"])
            contract = EmbeddingContract(
                pipeline_version=embedding_version,
                dimension=embedding_dimension,
                generation_id=generation_id,
            )
            context = self._profiles_for_contract(contract)
            profiles = context.profiles
            member_cache: dict[str, tuple[PetDetectionRecord, ...]] = {}

            def members_for(pet_id: str) -> tuple[PetDetectionRecord, ...]:
                cached = member_cache.get(pet_id)
                if cached is not None:
                    return cached
                with closing(self._connect()) as member_conn:
                    rows = self._select_detections_by_pet_ids(member_conn, (pet_id,))
                members = tuple(
                    detection
                    for detection in (self._detection_from_row(row) for row in rows)
                    if EmbeddingContract.from_detection(detection) == contract
                )
                member_cache[pet_id] = members
                return members

            processed_pet_ids: set[str] = set()
            changed_old_pet_ids: set[str] = set()
            changed_new_pet_ids: set[str] = set()
            changed_detections: list[PetDetectionRecord] = []
            rebuilt_pets: list[PetRecord] = []
            changed_asset_ids: list[str] = []
            diameter_threshold = distance_threshold * PET_CLUSTER_DIAMETER_MULTIPLIER

            for seed_id in seed_ids:
                if seed_id in processed_pet_ids:
                    continue
                if cancelled():
                    raise PetClusteringConsolidationCancelledError()
                seed_members = members_for(seed_id)
                if not seed_members:
                    processed_pet_ids.add(seed_id)
                    continue
                component = {seed_id}
                frontier = [seed_id]
                while frontier:
                    if cancelled():
                        raise PetClusteringConsolidationCancelledError()
                    current_id = frontier.pop()
                    current_members = members_for(current_id)
                    current_species = _normalize_species_label(
                        profiles.get(current_id).species_label
                        if current_id in profiles
                        else current_members[0].species_label
                        if current_members
                        else None
                    )
                    candidate_ids: set[str] = set()
                    for detection in current_members:
                        candidate_ids.update(
                            self._candidate_pet_ids_within_distance(
                                detection.embedding,
                                species_label=current_species,
                                candidate_index=context.consolidation_candidate_index,
                                maximum_distance=diameter_threshold,
                            )
                        )
                    for candidate_id in sorted(candidate_ids):
                        if candidate_id in component or candidate_id not in profiles:
                            continue
                        candidate_members = members_for(candidate_id)
                        if not candidate_members:
                            continue
                        if any(
                            cosine_distance(left.embedding, right.embedding) <= distance_threshold
                            for left in current_members
                            for right in candidate_members
                        ):
                            component.add(candidate_id)
                            frontier.append(candidate_id)

                processed_pet_ids.update(component)
                component_members = sorted(
                    (detection for pet_id in component for detection in members_for(pet_id)),
                    key=lambda detection: detection.detection_id,
                )
                if not component_members:
                    continue
                original_ids = {
                    detection.detection_id: str(detection.pet_id or "")
                    for detection in component_members
                }
                if len(component_members) == 1:
                    consolidated = component_members
                    component_pets = build_pet_records_from_detections(component_members)
                else:
                    consolidated, component_pets = cluster_pet_records(
                        component_members,
                        distance_threshold=distance_threshold,
                    )
                    if self._state_repo is not None:
                        consolidated, component_pets = canonicalize_pet_identities(
                            consolidated,
                            component_pets,
                            self._state_repo,
                            distance_threshold=distance_threshold,
                        )
                    else:
                        grouped: dict[str, list[PetDetectionRecord]] = {}
                        for detection in consolidated:
                            grouped.setdefault(str(detection.pet_id or ""), []).append(detection)
                        replacements: dict[str, str] = {}
                        used_ids: set[str] = set()
                        for raw_id, members in sorted(
                            grouped.items(),
                            key=lambda item: min(member.detection_id for member in item[1]),
                        ):
                            votes: dict[str, int] = {}
                            for member in members:
                                original_id = original_ids.get(member.detection_id, "")
                                if original_id and original_id not in used_ids:
                                    votes[original_id] = votes.get(original_id, 0) + 1
                            selected_id = (
                                min(votes, key=lambda pet_id: (-votes[pet_id], pet_id))
                                if votes
                                else raw_id
                            )
                            replacements[raw_id] = selected_id
                            used_ids.add(selected_id)
                        consolidated = [
                            replace(
                                detection,
                                pet_id=replacements.get(
                                    str(detection.pet_id or ""),
                                    detection.pet_id,
                                ),
                            )
                            for detection in consolidated
                        ]
                        names = {
                            pet_id: profiles[pet_id].name
                            for pet_id in component
                            if pet_id in profiles
                        }
                        created = {
                            pet_id: profiles[pet_id].created_at
                            for pet_id in component
                            if pet_id in profiles
                        }
                        component_pets = build_pet_records_from_detections(
                            consolidated,
                            names_by_pet_id=names,
                            created_at_by_pet_id=created,
                        )

                new_ids = {
                    detection.detection_id: str(detection.pet_id or "")
                    for detection in consolidated
                }
                if new_ids == original_ids:
                    continue
                changed_old_pet_ids.update(value for value in original_ids.values() if value)
                changed_new_pet_ids.update(value for value in new_ids.values() if value)
                changed_detections.extend(consolidated)
                rebuilt_pets.extend(component_pets)
                changed_asset_ids.extend(
                    detection.asset_id for detection in consolidated if detection.asset_id
                )

            if cancelled():
                raise PetClusteringConsolidationCancelledError()

            affected_pet_ids = tuple(sorted(changed_old_pet_ids | changed_new_pet_ids))
            deduplicated_pets = {
                pet.pet_id: pet for pet in rebuilt_pets if pet.pet_id in changed_new_pet_ids
            }
            rebuilt_pets = [deduplicated_pets[pet_id] for pet_id in sorted(deduplicated_pets)]
            changed_asset_ids_tuple = tuple(dict.fromkeys(changed_asset_ids))
            added = tuple(sorted(changed_new_pet_ids - changed_old_pet_ids))
            removed = tuple(sorted(changed_old_pet_ids - changed_new_pet_ids))
            updated = tuple(sorted(changed_old_pet_ids & changed_new_pet_ids))
            commit_payload = {
                "operation_kind": "pet_cluster_consolidate",
                "clustering_pipeline_target": target,
                "generation_id": generation_id,
                "processed_seed_count": len(seed_ids),
                "affected_pet_ids": list(affected_pet_ids),
                "changed_pet_ids": list(affected_pet_ids),
                "changed_asset_ids": list(changed_asset_ids_tuple),
                "added_pet_ids": list(added),
                "updated_pet_ids": list(updated),
                "removed_pet_ids": list(removed),
            }
            with closing(self._connect()) as conn:
                if changed_detections:
                    conn.executemany(
                        "UPDATE pet_detections SET pet_id = ? WHERE detection_id = ?",
                        [
                            (str(detection.pet_id or ""), detection.detection_id)
                            for detection in changed_detections
                        ],
                    )
                for chunk in _chunked(affected_pet_ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    conn.execute(f"DELETE FROM pets WHERE pet_id IN ({placeholders})", chunk)
                if rebuilt_pets:
                    conn.executemany(
                        """
                        INSERT INTO pets (
                            pet_id, name, key_detection_id, detection_count,
                            center_embedding, embedding_dim, created_at, updated_at,
                            sample_count, profile_state, species_label,
                            embedding_pipeline_version, generation_id,
                            boundary_embeddings, boundary_sample_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [self._pet_to_row(pet) for pet in rebuilt_pets],
                    )
                conn.execute(
                    """
                    DELETE FROM pet_clustering_consolidation_queue
                    WHERE target_version = ? AND generation_id = ?
                    """,
                    (target, generation_id),
                )
                self._set_scan_metadata_in_connection(
                    conn,
                    {
                        "clustering_pipeline_target": target,
                        "clustering_pipeline_version": target,
                        "clustering_consolidation_state": "clean",
                        "updated_at": utc_now_iso(),
                    },
                )
                self._write_runtime_commit(conn, operation_id, commit_payload)
                conn.commit()
                self._sync_runtime_state_payload(
                    commit_payload,
                    rebuilt_pets,
                    changed_detections,
                )
                self._mark_runtime_state_synced(conn, operation_id)
                conn.commit()
            self._invalidate_profile_indexes()
            return PetClusteringConsolidationResult(
                processed_seed_count=len(seed_ids),
                changed_asset_ids=changed_asset_ids_tuple,
                added_pet_ids=added,
                updated_pet_ids=updated,
                removed_pet_ids=removed,
            )

    def get_all_detections(self) -> list[PetDetectionRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    detection_id, pet_key, asset_id, asset_rel,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, species_label, quality_score,
                    pet_key_version, embedding_pipeline_version, generation_id,
                    is_stale, stale_reason, source_generation_id
                FROM pet_detections
                ORDER BY detected_at ASC, detection_id ASC
                """
            ).fetchall()
        rejected: set[str] = set()
        if self._state_repo is not None:
            rejected = self._state_repo.get_rejected_pet_keys(
                row["pet_key"] for row in rows if row["pet_key"]
            )
        return [self._detection_from_row(row) for row in rows if row["pet_key"] not in rejected]

    def get_detections_by_asset_ids(
        self,
        asset_ids: Iterable[str],
    ) -> list[PetDetectionRecord]:
        ids = tuple(dict.fromkeys(str(value) for value in asset_ids if value))
        if not ids:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = self._select_detections_by_asset_ids(conn, ids)
        rejected = (
            self._state_repo.get_rejected_pet_keys(row["pet_key"] for row in rows)
            if self._state_repo is not None
            else set()
        )
        return [self._detection_from_row(row) for row in rows if row["pet_key"] not in rejected]

    def get_all_pet_records(self) -> list[PetRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    pet_id, name, key_detection_id, detection_count,
                    center_embedding, embedding_dim, created_at, updated_at,
                    sample_count, profile_state, species_label,
                    embedding_pipeline_version, generation_id,
                    boundary_embeddings, boundary_sample_count
                FROM pets
                ORDER BY detection_count DESC, created_at ASC, pet_id ASC
                """
            ).fetchall()
        return [self._pet_from_row(row) for row in rows]

    def get_pet_summaries(self, *, include_hidden: bool = False) -> list[PetSummary]:
        self.initialize()
        merge_redirects = (
            self._state_repo.get_merge_redirect_map() if self._state_repo is not None else {}
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    pets.pet_id,
                    pets.name,
                    pets.key_detection_id,
                    pets.detection_count,
                    pets.created_at,
                    pets.profile_state,
                    pets.species_label,
                    pet_detections.thumbnail_path
                FROM pets
                LEFT JOIN pet_detections
                    ON pet_detections.detection_id = pets.key_detection_id
                ORDER BY pets.detection_count DESC, pets.created_at ASC
                """
            ).fetchall()
            asset_rows = conn.execute(
                """
                SELECT pet_id, asset_id
                FROM pet_detections
                WHERE pet_id IS NOT NULL
                """
            ).fetchall()
        hidden_map: dict[str, bool] = {}
        cover_paths: dict[str, str] = {}
        profile_names: dict[str, str | None] = {}
        promotion_map = {}
        if self._state_repo is not None:
            hidden_map, cover_paths, profile_names = self._state_repo.get_summary_state_maps()
            promotion_map = self._state_repo.get_promotion_records()
        effective_asset_sets: dict[str, set[str]] = {}
        for asset_row in asset_rows:
            runtime_pet_id = str(asset_row["pet_id"])
            canonical_pet_id = merge_redirects.get(runtime_pet_id, runtime_pet_id)
            if asset_row["asset_id"]:
                effective_asset_sets.setdefault(canonical_pet_id, set()).add(
                    str(asset_row["asset_id"])
                )
        effective_assets: dict[str, list[str]] = {
            pet_id: sorted(asset_ids) for pet_id, asset_ids in effective_asset_sets.items()
        }
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        if face_state_path.exists():
            effective_assets = self.get_asset_ids_by_pets(
                str(row["pet_id"])
                for row in rows
                if row["pet_id"] and str(row["pet_id"]) not in merge_redirects
            )

        summaries: list[PetSummary] = []
        for row in rows:
            pet_id = str(row["pet_id"])
            if pet_id in merge_redirects:
                continue
            thumbnail_path = cover_paths.get(pet_id) or row["thumbnail_path"]
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / str(thumbnail_path)).resolve()
            name = row["name"] if row["name"] is not None else profile_names.get(pet_id)
            promotion = promotion_map.get(pet_id)
            evidence_asset_count = (
                promotion.evidence_asset_count
                if promotion is not None
                else len(effective_assets.get(pet_id, ()))
            )
            summaries.append(
                PetSummary(
                    pet_id=pet_id,
                    name=name,
                    key_detection_id=str(row["key_detection_id"] or ""),
                    detection_count=int(row["detection_count"] or 0),
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(row["created_at"] or ""),
                    is_hidden=bool(hidden_map.get(pet_id, False)),
                    asset_count=len(effective_assets.get(pet_id, ())),
                    profile_state=profile_state_for_sample_count(evidence_asset_count),
                    species_label=_normalize_species_label(row["species_label"]),
                    evidence_asset_count=evidence_asset_count,
                    promotion_state=(
                        promotion.promotion_state
                        if promotion is not None
                        else PROMOTION_LEGACY_VISIBLE
                    ),
                )
            )
        if not include_hidden:
            summaries = [summary for summary in summaries if not summary.is_hidden]
        return summaries

    def get_asset_ids_by_pet(self, pet_id: str) -> list[str]:
        if not pet_id:
            return []
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        if face_state_path.exists():
            face_index_path = self._db_path.parent.parent / "faces" / "face_index.db"
            return (
                FaceRepository(
                    face_index_path,
                    face_state_path,
                )
                .get_asset_ids_by_pets_effective((pet_id,))
                .get(pet_id, [])
            )
        self.initialize()
        runtime_pet_ids = [pet_id]
        if self._state_repo is not None:
            redirects = self._state_repo.get_merge_redirect_map()
            runtime_pet_ids.extend(
                source_id for source_id, target_id in redirects.items() if target_id == pet_id
            )
        runtime_pet_ids = list(dict.fromkeys(runtime_pet_ids))
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(runtime_pet_ids), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT DISTINCT asset_id
                        FROM pet_detections
                        WHERE pet_id IN ({placeholders})
                        ORDER BY asset_id ASC
                        """,
                        chunk,
                    ).fetchall()
                )
        return list(dict.fromkeys(str(row["asset_id"]) for row in rows if row["asset_id"]))

    def get_asset_ids_by_pets(
        self,
        pet_ids: Iterable[str],
    ) -> dict[str, list[str]]:
        """Return assets for all requested Pets using one SQLite connection."""

        ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        result: dict[str, list[str]] = {pet_id: [] for pet_id in ids}
        if not ids:
            return result
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        if face_state_path.exists():
            face_index_path = self._db_path.parent.parent / "faces" / "face_index.db"
            return FaceRepository(
                face_index_path,
                face_state_path,
            ).get_asset_ids_by_pets_effective(ids)
        self.initialize()
        redirects = (
            self._state_repo.get_merge_redirect_map() if self._state_repo is not None else {}
        )
        runtime_to_requested: dict[str, str] = {pet_id: pet_id for pet_id in ids}
        for source_id, target_id in redirects.items():
            if target_id in result:
                runtime_to_requested[source_id] = target_id
        runtime_ids = tuple(runtime_to_requested)
        with closing(self._connect()) as conn:
            for start in range(0, len(runtime_ids), 900):
                chunk = runtime_ids[start : start + 900]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT pet_id, asset_id
                    FROM pet_detections
                    WHERE pet_id IN ({placeholders})
                    ORDER BY pet_id ASC, asset_id ASC
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    if row["pet_id"] and row["asset_id"]:
                        result[runtime_to_requested[str(row["pet_id"])]].append(
                            str(row["asset_id"])
                        )
        for requested_id, asset_ids in result.items():
            result[requested_id] = list(dict.fromkeys(asset_ids))
        return result

    def list_asset_pet_annotations(self, asset_id: str) -> list[AssetPetAnnotation]:
        if not asset_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    detection_id, pet_id, box_x, box_y, box_w, box_h,
                    image_width, image_height, thumbnail_path,
                    is_stale, stale_reason, source_generation_id
                FROM pet_detections
                WHERE asset_id = ?
                ORDER BY box_x ASC, box_y ASC, detection_id ASC
                """,
                (asset_id,),
            ).fetchall()
        names = {}
        redirects: dict[str, str] = {}
        if self._state_repo is not None:
            redirects = self._state_repo.get_merge_redirect_map()
            names = self._state_repo.get_profile_name_map(
                redirects.get(str(row["pet_id"]), str(row["pet_id"]))
                for row in rows
                if row["pet_id"]
            )
            promotion_map = self._state_repo.get_promotion_records(
                redirects.get(str(row["pet_id"]), str(row["pet_id"]))
                for row in rows
                if row["pet_id"]
            )
        else:
            promotion_map = {}
        canonical_identities = self._canonical_annotation_identities(
            str(row["pet_id"]) for row in rows if row["pet_id"]
        )
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        assignments: dict[tuple[str, str], tuple[str, str]] = {}
        if face_state_path.exists():
            assignments = FaceStateRepository(face_state_path).get_annotation_identity_assignments(
                ("pet", str(row["detection_id"])) for row in rows if row["detection_id"]
            )
        assigned_canonical = self._canonical_identity_refs(assignments.values())
        annotations: list[AssetPetAnnotation] = []
        for row in rows:
            thumbnail_path = row["thumbnail_path"]
            runtime_pet_id = str(row["pet_id"]) if row["pet_id"] else None
            canonical_pet_id = (
                redirects.get(runtime_pet_id, runtime_pet_id) if runtime_pet_id else None
            )
            annotations.append(
                AssetPetAnnotation(
                    detection_id=str(row["detection_id"]),
                    pet_id=runtime_pet_id,
                    display_name=names.get(canonical_pet_id) if canonical_pet_id else None,
                    box_x=int(row["box_x"] or 0),
                    box_y=int(row["box_y"] or 0),
                    box_w=int(row["box_w"] or 0),
                    box_h=int(row["box_h"] or 0),
                    image_width=int(row["image_width"] or 0),
                    image_height=int(row["image_height"] or 0),
                    thumbnail_path=(
                        (self._db_path.parent / str(thumbnail_path)).resolve()
                        if thumbnail_path
                        else None
                    ),
                    source_identity_id=runtime_pet_id,
                    canonical_identity_kind=(
                        assigned_canonical[assignments[("pet", str(row["detection_id"]))]][0]
                        if ("pet", str(row["detection_id"])) in assignments
                        else canonical_identities[runtime_pet_id][0]
                        if runtime_pet_id
                        else "pet"
                    ),
                    canonical_identity_id=(
                        assigned_canonical[assignments[("pet", str(row["detection_id"]))]][1]
                        if ("pet", str(row["detection_id"])) in assignments
                        else canonical_identities[runtime_pet_id][1]
                        if runtime_pet_id
                        else None
                    ),
                    canonical_display_name=(
                        assigned_canonical[assignments[("pet", str(row["detection_id"]))]][2]
                        if ("pet", str(row["detection_id"])) in assignments
                        else canonical_identities[runtime_pet_id][2]
                        if runtime_pet_id
                        else None
                    ),
                    is_stale=bool(row["is_stale"]),
                    stale_reason=row["stale_reason"],
                    source_generation_id=(
                        int(row["source_generation_id"])
                        if row["source_generation_id"] is not None
                        else None
                    ),
                    promotion_state=(
                        PROMOTION_CONFIRMED
                        if ("pet", str(row["detection_id"])) in assignments
                        else promotion_map[canonical_pet_id].promotion_state
                        if canonical_pet_id and canonical_pet_id in promotion_map
                        else PROMOTION_CANDIDATE
                    ),
                )
            )
        return annotations

    def _canonical_annotation_identities(
        self,
        pet_ids: Iterable[str],
    ) -> dict[str, tuple[str, str, str | None]]:
        source_ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        if not source_ids:
            return {}
        resolved = self._canonical_identity_refs(("pet", source_id) for source_id in source_ids)
        return {source_id: resolved[("pet", source_id)] for source_id in source_ids}

    def _canonical_identity_refs(
        self,
        refs: Iterable[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[str, str, str | None]]:
        source_refs = tuple(
            dict.fromkeys(
                (str(kind), str(entity_id))
                for kind, entity_id in refs
                if kind in {"person", "pet"} and entity_id
            )
        )
        if not source_refs:
            return {}
        pet_redirects = (
            self._state_repo.get_merge_redirect_map() if self._state_repo is not None else {}
        )
        redirect_map: dict[tuple[str, str], tuple[str, str]] = {
            ("pet", source_id): ("pet", target_id) for source_id, target_id in pet_redirects.items()
        }
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        face_state = None
        if face_state_path.exists():
            face_state = FaceStateRepository(face_state_path)
            redirect_map.update(
                {
                    (redirect.source_kind, redirect.source_id): (
                        redirect.target_kind,
                        redirect.target_id,
                    )
                    for redirect in face_state.get_identity_redirects()
                }
            )
        resolved = {
            source_ref: _resolve_identity_redirect(*source_ref, redirect_map)
            for source_ref in source_refs
        }
        pet_targets = [entity_id for kind, entity_id in resolved.values() if kind == "pet"]
        pet_names = (
            self._state_repo.get_profile_name_map(pet_targets)
            if self._state_repo is not None
            else {}
        )
        person_targets = [entity_id for kind, entity_id in resolved.values() if kind == "person"]
        person_names: dict[str, str | None] = {}
        if face_state is not None and person_targets:
            person_names = face_state.get_profile_name_map(person_targets)
            missing_targets = [
                person_id for person_id in person_targets if person_id not in person_names
            ]
            if missing_targets:
                face_runtime_path = face_state_path.with_name("face_index.db")
                if face_runtime_path.exists():
                    person_names.update(
                        FaceRepository(
                            face_runtime_path,
                            face_state_path,
                        ).get_person_name_map(missing_targets)
                    )
        return {
            source_ref: (
                kind,
                entity_id,
                pet_names.get(entity_id) if kind == "pet" else person_names.get(entity_id),
            )
            for source_ref, (kind, entity_id) in resolved.items()
        }

    def get_detection(self, detection_id: str) -> PetDetectionRecord | None:
        if not detection_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    detection_id, pet_key, asset_id, asset_rel,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, species_label, quality_score,
                    pet_key_version, embedding_pipeline_version, generation_id,
                    is_stale, stale_reason, source_generation_id
                FROM pet_detections
                WHERE detection_id = ?
                """,
                (detection_id,),
            ).fetchone()
        return self._detection_from_row(row) if row is not None else None

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> bool:
        if self._state_repo is None or not pet_id:
            return False
        canonical_pet_id = self._canonical_existing_pet_id(pet_id)
        if canonical_pet_id is None:
            return False
        self._state_repo.rename_pet(canonical_pet_id, name_or_none)
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE pets SET name = ?, updated_at = ? WHERE pet_id = ?",
                (normalize_name(name_or_none), utc_now_iso(), canonical_pet_id),
            )
            conn.commit()
        return (
            int(cursor.rowcount or 0) > 0
            or self._state_repo.get_profile(canonical_pet_id) is not None
        )

    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool:
        if self._state_repo is None:
            return False
        canonical_pet_id = self._canonical_existing_pet_id(pet_id)
        if canonical_pet_id is None:
            return False
        return self._state_repo.set_pet_hidden(canonical_pet_id, hidden)

    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool:
        if self._state_repo is None:
            return False
        detection = self.get_detection(detection_id)
        canonical_pet_id = self._canonical_existing_pet_id(pet_id)
        if detection is None or canonical_pet_id is None or not detection.pet_id:
            return False
        redirects = self._state_repo.get_merge_redirect_map()
        detection_pet_id = redirects.get(detection.pet_id, detection.pet_id)
        if detection_pet_id != canonical_pet_id:
            return False
        return self._state_repo.set_pet_cover(canonical_pet_id, detection)

    def _canonical_existing_pet_id(self, pet_id: str) -> str | None:
        if self._state_repo is None or not pet_id:
            return None
        redirects = self._state_repo.get_merge_redirect_map()
        canonical = redirects.get(pet_id, pet_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM pets WHERE pet_id = ?",
                (canonical,),
            ).fetchone()
        if row is not None or self._state_repo.get_profile(canonical) is not None:
            return canonical
        return None

    def merge_pets(
        self,
        source_pet_id: str,
        target_pet_id: str,
        *,
        operation_id: str | None = None,
    ) -> PetMutationResult | None:
        self._last_mutation_failure = None
        if self._state_repo is None:
            return None
        self.initialize()
        runtime_pets = [
            pet
            for pet in self.get_all_pet_records()
            if pet.pet_id in {source_pet_id, target_pet_id}
        ]
        contracts = {EmbeddingContract.from_pet(pet) for pet in runtime_pets}
        if len(contracts) > 1:
            return None
        runtime_detections = [
            detection
            for detection in self.get_all_detections()
            if detection.pet_id in {source_pet_id, target_pet_id}
        ]
        source_asset_ids = {
            detection.asset_id
            for detection in runtime_detections
            if detection.pet_id == source_pet_id and detection.asset_id
        }
        target_asset_ids = {
            detection.asset_id
            for detection in runtime_detections
            if detection.pet_id == target_pet_id and detection.asset_id
        }
        if source_asset_ids & target_asset_ids:
            self._last_mutation_failure = PetMutationFailure.SAME_ASSET_CONFLICT
            self._same_asset_manual_conflicts += 1
            LOGGER.warning(
                "Rejected pet merge due to same-asset cannot-link: "
                "same_asset_manual_conflicts=1 source=%s target=%s",
                source_pet_id,
                target_pet_id,
            )
            return None
        durable_profiles = self._state_repo.get_profiles_by_ids((source_pet_id, target_pet_id))
        species_labels = {
            label
            for value in (
                *(detection.species_label for detection in runtime_detections),
                *(pet.species_label for pet in runtime_pets),
                *(profile.species_label for profile in durable_profiles.values()),
            )
            if (label := _normalize_species_label(value)) is not None
        }
        if len(species_labels) > 1:
            return None
        self._state_repo.ensure_runtime_candidates(runtime_pets, runtime_detections)
        durable_merged = self._state_repo.merge_pets(source_pet_id, target_pet_id)
        if not durable_merged:
            redirects = self._state_repo.get_merge_redirect_map()
            if redirects.get(source_pet_id) != target_pet_id:
                return None
        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        with closing(self._connect()) as conn:
            asset_rows = conn.execute(
                """
                SELECT DISTINCT asset_id
                FROM pet_detections
                WHERE pet_id IN (?, ?)
                """,
                (source_pet_id, target_pet_id),
            ).fetchall()
            conn.execute(
                "UPDATE pet_detections SET pet_id = ? WHERE pet_id = ?",
                (target_pet_id, source_pet_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO pet_contract_migration_assets (
                    pet_id, asset_id, embedding_pipeline_version,
                    embedding_dimension, generation_id
                )
                SELECT ?, asset_id, embedding_pipeline_version,
                       embedding_dimension, generation_id
                FROM pet_contract_migration_assets
                WHERE pet_id = ?
                """,
                (target_pet_id, source_pet_id),
            )
            conn.execute(
                "DELETE FROM pet_contract_migration_assets WHERE pet_id = ?",
                (source_pet_id,),
            )
            self._rebuild_pet_records_in_connection(conn)
            changed_asset_ids = tuple(str(row["asset_id"]) for row in asset_rows if row["asset_id"])
            self._write_runtime_commit(
                conn,
                effective_operation_id,
                {
                    "operation_kind": "pet_merge",
                    "affected_pet_ids": [source_pet_id, target_pet_id],
                    "changed_pet_ids": [source_pet_id, target_pet_id],
                    "changed_asset_ids": list(changed_asset_ids),
                    "pet_redirects": {source_pet_id: target_pet_id},
                    "source_pet_id": source_pet_id,
                    "target_pet_id": target_pet_id,
                },
            )
            conn.commit()
        self._invalidate_profile_indexes()
        self.complete_runtime_state_sync(effective_operation_id)
        self._remap_people_groups_for_pet_merge(source_pet_id, target_pet_id)
        return PetMutationResult(
            changed_asset_ids=changed_asset_ids,
            changed_pet_ids=(source_pet_id, target_pet_id),
            pet_redirects={source_pet_id: target_pet_id},
        )

    def _remap_people_groups_for_pet_merge(self, source_pet_id: str, target_pet_id: str) -> None:
        face_state_db_path = self._db_path.parent.parent / "faces" / "face_state.db"
        face_index_db_path = self._db_path.parent.parent / "faces" / "face_index.db"
        if not face_state_db_path.exists():
            return
        face_state_repository = FaceStateRepository(face_state_db_path)
        face_state_repository.remap_pet_in_groups(source_pet_id, target_pet_id)
        face_state_repository.remap_identity_redirect_targets(
            target_kind="pet",
            source_target_id=source_pet_id,
            target_target_id=target_pet_id,
        )
        if face_index_db_path.exists():
            FaceRepository(face_index_db_path, face_state_db_path).refresh_all_group_assets()

    def recover_pet_merge_people_groups(
        self,
        source_pet_id: str,
        target_pet_id: str,
    ) -> None:
        """Idempotently finish the People group side of a committed Pet merge."""

        self._remap_people_groups_for_pet_merge(source_pet_id, target_pet_id)

    def _refresh_people_group_assets_for_pets(self, pet_ids: Iterable[str]) -> None:
        unique_pet_ids = tuple(dict.fromkeys(str(pet_id) for pet_id in pet_ids if pet_id))
        if not unique_pet_ids:
            return
        face_state_db_path = self._db_path.parent.parent / "faces" / "face_state.db"
        face_index_db_path = self._db_path.parent.parent / "faces" / "face_index.db"
        if not face_state_db_path.exists() or not face_index_db_path.exists():
            return
        state_repository = FaceStateRepository(face_state_db_path)
        group_ids = state_repository.list_group_ids_for_pets(unique_pet_ids)
        if not group_ids:
            return
        face_repository = FaceRepository(face_index_db_path, face_state_db_path)
        for group_id in group_ids:
            face_repository.refresh_group_assets(group_id)

    def refresh_people_group_assets_for_pets(self, pet_ids: Iterable[str]) -> None:
        """Refresh cross-kind group caches after a committed Pet mutation."""

        self._refresh_people_group_assets_for_pets(pet_ids)

    def delete_detection(
        self,
        detection_id: str,
        *,
        operation_id: str | None = None,
    ) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None:
            return None
        if self._state_repo is not None:
            self._state_repo.add_rejected_pet_key(detection.pet_key)
            self._state_repo.clear_cover_for_detection(detection_id)
        face_state_path = self._db_path.parent.parent / "faces" / "face_state.db"
        if face_state_path.exists():
            FaceStateRepository(face_state_path).clear_annotation_identity_assignment(
                "pet", detection_id
            )
        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM pet_detections WHERE detection_id = ?", (detection_id,))
            self._rebuild_pet_records_in_connection(conn)
            changed_pet_ids = (detection.pet_id,) if detection.pet_id else ()
            self._write_runtime_commit(
                conn,
                effective_operation_id,
                {
                    "operation_kind": "pet_delete_detection",
                    "affected_pet_ids": list(changed_pet_ids),
                    "changed_pet_ids": list(changed_pet_ids),
                    "changed_asset_ids": [detection.asset_id],
                    "detection_id": detection_id,
                    "pet_key": detection.pet_key,
                    "previous_thumbnail_paths": (
                        [detection.thumbnail_path] if detection.thumbnail_path else []
                    ),
                },
            )
            conn.commit()
        self.complete_runtime_state_sync(effective_operation_id)
        if detection.thumbnail_path:
            self.prune_unreferenced_thumbnails((detection.thumbnail_path,))
        self._refresh_people_group_assets_for_pets((detection.pet_id,))
        return PetMutationResult(
            changed_asset_ids=(detection.asset_id,),
            changed_pet_ids=(detection.pet_id,) if detection.pet_id else (),
        )

    def move_detection_to_pet(
        self,
        detection_id: str,
        target_pet_id: str,
        *,
        operation_id: str | None = None,
    ) -> PetMutationResult | None:
        self._last_mutation_failure = None
        detection = self.get_detection(detection_id)
        if detection is None or not target_pet_id:
            return None
        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        with closing(self._connect()) as conn:
            target = conn.execute(
                """
                SELECT pet_id, species_label,
                       embedding_pipeline_version, embedding_dim, generation_id
                FROM pets WHERE pet_id = ?
                """,
                (target_pet_id,),
            ).fetchone()
            if target is None:
                return None
            target_contract = EmbeddingContract(
                pipeline_version=str(target["embedding_pipeline_version"] or ""),
                dimension=int(target["embedding_dim"] or 0),
                generation_id=int(target["generation_id"] or 0),
            )
            if EmbeddingContract.from_detection(detection) != target_contract:
                return None
            if _normalize_species_label(detection.species_label) != _normalize_species_label(
                target["species_label"]
            ):
                return None
            same_asset = conn.execute(
                """
                SELECT 1
                FROM pet_detections
                WHERE pet_id = ? AND asset_id = ? AND detection_id != ?
                LIMIT 1
                """,
                (target_pet_id, detection.asset_id, detection_id),
            ).fetchone()
            if same_asset is not None:
                self._last_mutation_failure = PetMutationFailure.SAME_ASSET_CONFLICT
                self._same_asset_manual_conflicts += 1
                LOGGER.warning(
                    "Rejected pet detection move due to same-asset cannot-link: "
                    "same_asset_manual_conflicts=1 detection=%s target=%s asset=%s",
                    detection_id,
                    target_pet_id,
                    detection.asset_id,
                )
                return None
            conn.execute(
                "UPDATE pet_detections SET pet_id = ? WHERE detection_id = ?",
                (target_pet_id, detection_id),
            )
            self._rebuild_pet_records_in_connection(conn)
            changed_pet_ids = tuple(
                pet_id for pet_id in (detection.pet_id, target_pet_id) if pet_id
            )
            self._write_runtime_commit(
                conn,
                effective_operation_id,
                {
                    "operation_kind": "pet_move_detection",
                    "affected_pet_ids": list(changed_pet_ids),
                    "changed_pet_ids": list(changed_pet_ids),
                    "changed_asset_ids": [detection.asset_id],
                    "detection_id": detection_id,
                    "source_pet_id": detection.pet_id or "",
                    "target_pet_id": target_pet_id,
                },
            )
            conn.commit()
        self.complete_runtime_state_sync(effective_operation_id)
        if self._state_repo is not None:
            self._state_repo.confirm_pet(target_pet_id)
        self._refresh_people_group_assets_for_pets((detection.pet_id, target_pet_id))
        return PetMutationResult(
            changed_asset_ids=(detection.asset_id,),
            changed_pet_ids=changed_pet_ids,
        )

    def move_detection_to_new_pet(
        self,
        detection_id: str,
        new_pet_id: str,
        new_name: str | None,
        *,
        operation_id: str | None = None,
    ) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None or not new_pet_id:
            return None
        effective_operation_id = operation_id or f"internal-{uuid.uuid4().hex}"
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pet_detections SET pet_id = ? WHERE detection_id = ?",
                (new_pet_id, detection_id),
            )
            self._rebuild_pet_records_in_connection(conn)
            changed_pet_ids = tuple(pet_id for pet_id in (detection.pet_id, new_pet_id) if pet_id)
            self._write_runtime_commit(
                conn,
                effective_operation_id,
                {
                    "operation_kind": "pet_move_detection_new",
                    "affected_pet_ids": list(changed_pet_ids),
                    "changed_pet_ids": list(changed_pet_ids),
                    "changed_asset_ids": [detection.asset_id],
                    "detection_id": detection_id,
                    "source_pet_id": detection.pet_id or "",
                    "new_pet_id": new_pet_id,
                    "new_name": new_name,
                },
            )
            conn.commit()
        self.complete_runtime_state_sync(effective_operation_id)
        if new_name:
            self.rename_pet(new_pet_id, new_name)
        self._refresh_people_group_assets_for_pets((detection.pet_id, new_pet_id))
        return PetMutationResult(
            changed_asset_ids=(detection.asset_id,),
            changed_pet_ids=changed_pet_ids,
        )

    def _rebuild_pet_records_in_connection(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
        """Rebuild the runtime pet rows without leaving the active transaction."""

        from .pipeline import build_pet_records_from_detections

        rows = conn.execute(
            """
            SELECT
                detection_id, pet_key, asset_id, asset_rel,
                box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                image_width, image_height, species_label, quality_score,
                pet_key_version, embedding_pipeline_version, generation_id,
                is_stale, stale_reason, source_generation_id
            FROM pet_detections
            ORDER BY detected_at ASC, detection_id ASC
            """
        ).fetchall()
        rejected: set[str] = set()
        if self._state_repo is not None:
            rejected = self._state_repo.get_rejected_pet_keys(
                row["pet_key"] for row in rows if row["pet_key"]
            )
        detections = [
            self._detection_from_row(row) for row in rows if row["pet_key"] not in rejected
        ]
        names: dict[str, str | None] = {}
        created_at: dict[str, str] = {}
        if self._state_repo is not None:
            profiles = {profile.pet_id: profile for profile in self._state_repo.get_profiles()}
            names = {pet_id: profile.name for pet_id, profile in profiles.items()}
            created_at = {pet_id: profile.created_at for pet_id, profile in profiles.items()}
        pets = build_pet_records_from_detections(
            detections,
            names_by_pet_id=names,
            created_at_by_pet_id=created_at,
        )
        conn.execute("DELETE FROM pets")
        if pets:
            conn.executemany(
                """
                INSERT INTO pets (
                    pet_id, name, key_detection_id, detection_count,
                    center_embedding, embedding_dim, created_at, updated_at,
                    sample_count, profile_state, species_label,
                    embedding_pipeline_version, generation_id,
                    boundary_embeddings, boundary_sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._pet_to_row(pet) for pet in pets],
            )
        return detections, pets

    def _rebuild_pet_records_from_detections(self) -> None:
        from .pipeline import build_pet_records_from_detections

        detections = self.get_all_detections()
        names = {}
        created_at = {}
        if self._state_repo is not None:
            profiles = {profile.pet_id: profile for profile in self._state_repo.get_profiles()}
            names = {pet_id: profile.name for pet_id, profile in profiles.items()}
            created_at = {pet_id: profile.created_at for pet_id, profile in profiles.items()}
        pets = build_pet_records_from_detections(
            detections,
            names_by_pet_id=names,
            created_at_by_pet_id=created_at,
        )
        self.replace_all(detections, pets)

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self._db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn, self._db_path, wal=True)
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_detections (
                detection_id TEXT PRIMARY KEY,
                pet_key TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                box_x INTEGER NOT NULL,
                box_y INTEGER NOT NULL,
                box_w INTEGER NOT NULL,
                box_h INTEGER NOT NULL,
                confidence REAL NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                detector_model TEXT NOT NULL,
                thumbnail_path TEXT,
                pet_id TEXT,
                detected_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL,
                species_label TEXT,
                quality_score REAL,
                pet_key_version TEXT NOT NULL DEFAULT 'v1',
                embedding_pipeline_version TEXT NOT NULL DEFAULT '',
                generation_id INTEGER NOT NULL DEFAULT 0,
                is_stale INTEGER NOT NULL DEFAULT 0,
                stale_reason TEXT,
                source_generation_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pets (
                pet_id TEXT PRIMARY KEY,
                name TEXT,
                key_detection_id TEXT NOT NULL,
                detection_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                profile_state TEXT DEFAULT 'unstable',
                species_label TEXT,
                embedding_pipeline_version TEXT NOT NULL DEFAULT '',
                generation_id INTEGER NOT NULL DEFAULT 0,
                boundary_embeddings BLOB,
                boundary_sample_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_generations (
                generation_id INTEGER PRIMARY KEY,
                pipeline_version TEXT NOT NULL,
                embedding_dimension INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                UNIQUE (pipeline_version, embedding_dimension)
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_embedding_generations_active
            ON embedding_generations(status)
            WHERE status = 'active'
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_runtime_commits (
                operation_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                state_synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_contract_migration_assets (
                pet_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                embedding_pipeline_version TEXT NOT NULL,
                embedding_dimension INTEGER NOT NULL,
                generation_id INTEGER NOT NULL,
                PRIMARY KEY (
                    pet_id, asset_id, embedding_pipeline_version,
                    embedding_dimension, generation_id
                )
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_clustering_consolidation_queue (
                target_version TEXT NOT NULL,
                generation_id INTEGER NOT NULL,
                pet_id TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                PRIMARY KEY (target_version, generation_id, pet_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pet_clustering_queue_generation
            ON pet_clustering_consolidation_queue (
                generation_id, target_version, pet_id
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pet_contract_migration_asset
            ON pet_contract_migration_assets (
                asset_id, embedding_pipeline_version,
                embedding_dimension, generation_id
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_detections_pet_id ON pet_detections (pet_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_detections_asset_id ON pet_detections (asset_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_detections_pet_key ON pet_detections (pet_key)"
        )
        _ensure_column(conn, "pet_detections", "pet_key_version", "TEXT NOT NULL DEFAULT 'v1'")
        _ensure_column(
            conn,
            "pet_detections",
            "embedding_pipeline_version",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(conn, "pet_detections", "generation_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "pet_detections", "is_stale", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "pet_detections", "stale_reason", "TEXT")
        _ensure_column(conn, "pet_detections", "source_generation_id", "INTEGER")
        _ensure_column(conn, "pets", "embedding_pipeline_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "pets", "generation_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "pets", "boundary_embeddings", "BLOB")
        _ensure_column(conn, "pets", "boundary_sample_count", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    def _detection_to_row(self, detection: PetDetectionRecord) -> tuple:
        return (
            detection.detection_id,
            detection.pet_key,
            detection.asset_id,
            detection.asset_rel,
            detection.box_x,
            detection.box_y,
            detection.box_w,
            detection.box_h,
            detection.confidence,
            serialize_embedding(detection.embedding),
            detection.embedding_dim,
            detection.embedding_model,
            detection.detector_model,
            detection.thumbnail_path,
            detection.pet_id,
            detection.detected_at,
            detection.image_width,
            detection.image_height,
            _normalize_species_label(detection.species_label),
            detection.quality_score,
            detection.pet_key_version,
            detection.embedding_pipeline_version,
            detection.generation_id,
            int(detection.is_stale),
            detection.stale_reason,
            detection.source_generation_id,
        )

    def _pet_to_row(self, pet: PetRecord) -> tuple:
        sample_count = max(int(pet.sample_count), int(pet.detection_count))
        return (
            pet.pet_id,
            normalize_name(pet.name),
            pet.key_detection_id,
            pet.detection_count,
            serialize_embedding(pet.center_embedding),
            pet.embedding_dim,
            pet.created_at,
            pet.updated_at,
            sample_count,
            pet.profile_state,
            _normalize_species_label(pet.species_label),
            pet.embedding_pipeline_version,
            pet.generation_id,
            _serialize_boundary_embeddings(pet.boundary_embeddings, pet.embedding_dim),
            min(len(pet.boundary_embeddings), 8),
        )

    def _detection_from_row(self, row: sqlite3.Row) -> PetDetectionRecord:
        return PetDetectionRecord(
            detection_id=str(row["detection_id"]),
            pet_key=str(row["pet_key"]),
            asset_id=str(row["asset_id"]),
            asset_rel=str(row["asset_rel"]),
            box_x=int(row["box_x"]),
            box_y=int(row["box_y"]),
            box_w=int(row["box_w"]),
            box_h=int(row["box_h"]),
            confidence=float(row["confidence"]),
            embedding=deserialize_embedding(row["embedding"], int(row["embedding_dim"])),
            embedding_dim=int(row["embedding_dim"]),
            embedding_model=str(row["embedding_model"]),
            detector_model=str(row["detector_model"]),
            thumbnail_path=row["thumbnail_path"],
            pet_id=row["pet_id"],
            detected_at=str(row["detected_at"]),
            image_width=int(row["image_width"]),
            image_height=int(row["image_height"]),
            species_label=_normalize_species_label(row["species_label"]),
            quality_score=(
                float(row["quality_score"]) if row["quality_score"] is not None else None
            ),
            pet_key_version=str(_row_value(row, "pet_key_version", "v1") or "v1"),
            embedding_pipeline_version=str(_row_value(row, "embedding_pipeline_version", "") or ""),
            generation_id=int(_row_value(row, "generation_id", 0) or 0),
            is_stale=bool(_row_value(row, "is_stale", 0)),
            stale_reason=_row_value(row, "stale_reason", None),
            source_generation_id=(
                int(value)
                if (value := _row_value(row, "source_generation_id", None)) is not None
                else None
            ),
        )

    def _pet_from_row(self, row: sqlite3.Row) -> PetRecord:
        return PetRecord(
            pet_id=str(row["pet_id"]),
            name=row["name"],
            key_detection_id=str(row["key_detection_id"]),
            detection_count=int(row["detection_count"]),
            center_embedding=deserialize_embedding(
                row["center_embedding"],
                int(row["embedding_dim"] or 0),
            ),
            embedding_dim=int(row["embedding_dim"] or 0),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            sample_count=int(row["sample_count"] or 0),
            profile_state=str(row["profile_state"] or "unstable"),
            species_label=_normalize_species_label(row["species_label"]),
            embedding_pipeline_version=str(_row_value(row, "embedding_pipeline_version", "") or ""),
            generation_id=int(_row_value(row, "generation_id", 0) or 0),
            boundary_embeddings=_deserialize_boundary_embeddings(
                _row_value(row, "boundary_embeddings", None),
                embedding_dim=int(row["embedding_dim"] or 0),
                sample_count=int(_row_value(row, "boundary_sample_count", 0) or 0),
            ),
        )


def _normalize_species_label(value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip().lower()
    return label or None


def _species_compatible(left: str | None, right: str | None) -> bool:
    return left == right


def _chunked(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _row_value(row: sqlite3.Row, key: str, default: object) -> object:
    return row[key] if key in row.keys() else default


def _serialize_boundary_embeddings(
    embeddings: tuple[np.ndarray, ...],
    embedding_dim: int,
) -> sqlite3.Binary | None:
    selected = [
        normalize_vector(embedding)
        for embedding in embeddings[:8]
        if int(np.asarray(embedding).size) == int(embedding_dim)
    ]
    if not selected:
        return None
    return sqlite3.Binary(np.stack(selected, axis=0).astype(np.float32).tobytes())


def _deserialize_boundary_embeddings(
    blob: object,
    *,
    embedding_dim: int,
    sample_count: int,
) -> tuple[np.ndarray, ...]:
    count = max(0, min(int(sample_count), 8))
    if not blob or embedding_dim <= 0 or count <= 0:
        return ()
    matrix = np.frombuffer(blob, dtype=np.float32, count=count * embedding_dim).reshape(
        count,
        embedding_dim,
    )
    return tuple(row.copy() for row in matrix)


class _ProfileCandidateIndex:
    """USearch-backed profile candidate lookup with a dependency-free fallback."""

    def __init__(
        self,
        centers: dict[str, np.ndarray],
        species: dict[str, str | None],
    ) -> None:
        self._centers = centers
        self._species = species
        self._indexes: dict[
            tuple[int, str | None],
            tuple[object, dict[int, str], dict[str, int]],
        ] = {}
        self._next_key = 0
        try:
            from usearch.index import Index  # noqa: F401
        except ImportError:
            return
        self.upsert_many(
            (pet_id, center, species.get(pet_id))
            for pet_id, center in sorted(centers.items())
            if center.size
        )

    def upsert(
        self,
        pet_id: str,
        center: np.ndarray,
        species_label: str | None,
    ) -> None:
        self.upsert_many(((pet_id, center, species_label),))

    def upsert_many(
        self,
        profiles: Iterable[tuple[str, np.ndarray, str | None]],
    ) -> None:
        staged: list[tuple[str, np.ndarray, str | None]] = []
        for pet_id, center, species_label in profiles:
            vector = normalize_vector(center)
            if not pet_id or not vector.size:
                continue
            old_species = self._species.get(pet_id)
            old_center = self._centers.get(pet_id)
            old_group = (
                (int(old_center.size), old_species)
                if old_center is not None and old_center.size
                else None
            )
            if old_group is not None:
                self._remove_from_group(old_group, pet_id)
            self._centers[pet_id] = vector
            self._species[pet_id] = species_label
            staged.append((pet_id, vector, species_label))
        if not staged:
            return
        try:
            from usearch.index import Index
        except ImportError:
            return
        grouped: dict[
            tuple[int, str | None],
            list[tuple[str, np.ndarray]],
        ] = {}
        for pet_id, vector, species_label in staged:
            grouped.setdefault((int(vector.size), species_label), []).append((pet_id, vector))
        for group, members in grouped.items():
            entry = self._indexes.get(group)
            if entry is None:
                entry = (Index(ndim=group[0], metric="cos", dtype="f32"), {}, {})
                self._indexes[group] = entry
            index, key_to_pet, pet_to_key = entry
            keys = np.arange(
                self._next_key,
                self._next_key + len(members),
                dtype=np.uint64,
            )
            self._next_key += len(members)
            try:
                index.add(
                    keys,
                    np.stack([vector for _, vector in members], axis=0),
                )
            except Exception:  # noqa: BLE001
                self._rebuild_group(group)
                continue
            for key, (pet_id, _) in zip(keys.tolist(), members, strict=True):
                key_to_pet[int(key)] = pet_id
                pet_to_key[pet_id] = int(key)

    def remove(self, pet_id: str) -> None:
        center = self._centers.pop(pet_id, None)
        species_label = self._species.pop(pet_id, None)
        if center is not None and center.size:
            self._remove_from_group((int(center.size), species_label), pet_id)

    def _remove_from_group(self, group: tuple[int, str | None], pet_id: str) -> None:
        entry = self._indexes.get(group)
        if entry is None:
            return
        index, key_to_pet, pet_to_key = entry
        key = pet_to_key.pop(pet_id, None)
        if key is None:
            return
        key_to_pet.pop(key, None)
        try:
            index.remove(key)
        except Exception:  # noqa: BLE001
            self._rebuild_group(group)

    def _rebuild_group(self, group: tuple[int, str | None]) -> None:
        try:
            from usearch.index import Index
        except ImportError:
            self._indexes.pop(group, None)
            return
        index = Index(ndim=group[0], metric="cos", dtype="f32")
        key_to_pet: dict[int, str] = {}
        pet_to_key: dict[str, int] = {}
        members = sorted(
            (
                (pet_id, center)
                for pet_id, center in self._centers.items()
                if center.size == group[0] and self._species.get(pet_id) == group[1]
            ),
            key=lambda item: item[0],
        )
        if members:
            keys = np.arange(
                self._next_key,
                self._next_key + len(members),
                dtype=np.uint64,
            )
            self._next_key += len(members)
            index.add(
                keys,
                np.stack([center for _, center in members], axis=0),
            )
            for key, (pet_id, _) in zip(keys.tolist(), members, strict=True):
                key_to_pet[int(key)] = pet_id
                pet_to_key[pet_id] = int(key)
        self._indexes[group] = (index, key_to_pet, pet_to_key)

    def search(
        self,
        embedding: np.ndarray,
        *,
        species_label: str | None,
        limit: int,
    ) -> list[tuple[float, str]]:
        vector = normalize_vector(embedding)
        if not self._indexes:
            return sorted(
                (
                    (cosine_distance(vector, center), pet_id)
                    for pet_id, center in self._centers.items()
                    if center.size == vector.size
                    and _species_compatible(species_label, self._species.get(pet_id))
                ),
                key=lambda item: (item[0], item[1]),
            )[:limit]
        labels = {species_label}
        matches: list[tuple[float, str]] = []
        for label in labels:
            entry = self._indexes.get((int(vector.size), label))
            if entry is None:
                continue
            index, key_to_pet, _pet_to_key = entry
            if not key_to_pet:
                continue
            result = index.search(vector, min(limit, len(key_to_pet)))
            matches.extend(
                (float(distance), key_to_pet[int(key)])
                for key, distance in zip(result.keys, result.distances, strict=True)
                if int(key) in key_to_pet
                and _species_compatible(
                    species_label,
                    self._species.get(key_to_pet[int(key)]),
                )
            )
        return sorted(matches, key=lambda item: (item[0], item[1]))[:limit]


def _resolve_identity_redirect(
    source_kind: str,
    source_id: str,
    redirects: dict[tuple[str, str], tuple[str, str]],
) -> tuple[str, str]:
    cursor = (source_kind, source_id)
    visited: set[tuple[str, str]] = set()
    while cursor in redirects and cursor not in visited:
        visited.add(cursor)
        cursor = redirects[cursor]
    return cursor

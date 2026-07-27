"""SQLite pet index repository for Pets clusters."""

# ruff: noqa: S608

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from iPhoto.people.face_repository import FaceRepository
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite

from .records import AssetPetAnnotation, PetDetectionRecord, PetRecord, PetSummary
from .repository_utils import (
    cosine_distance,
    deserialize_embedding,
    normalize_name,
    normalize_vector,
    profile_state_for_sample_count,
    serialize_embedding,
    utc_now_iso,
)
from .state_repository import PetStateRepository

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PetMutationResult:
    changed_asset_ids: tuple[str, ...] = ()
    changed_pet_ids: tuple[str, ...] = ()
    pet_redirects: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PetIncrementalCommitResult:
    previous_thumbnail_paths: tuple[str, ...] = ()
    added_pet_ids: tuple[str, ...] = ()
    updated_pet_ids: tuple[str, ...] = ()
    removed_pet_ids: tuple[str, ...] = ()

    @property
    def changed_pet_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                self.added_pet_ids + self.updated_pet_ids + self.removed_pet_ids
            )
        )


class PetRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = PetStateRepository(state_db_path) if state_db_path is not None else None
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> PetStateRepository | None:
        return self._state_repo

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
        with closing(self._connect()) as conn:
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
            conn.commit()
        if sync_runtime_state:
            self.sync_runtime_state()
            self.prune_unreferenced_thumbnails(previous_thumbnail_paths)
        return previous_thumbnail_paths

    def replace_assets_incrementally(
        self,
        asset_ids: Iterable[str],
        detections: list[PetDetectionRecord],
        *,
        distance_threshold: float,
    ) -> PetIncrementalCommitResult:
        """Replace detections for a bounded asset set without rewriting the index."""

        self.initialize()
        changed_asset_ids = tuple(dict.fromkeys(str(value) for value in asset_ids if value))
        if not changed_asset_ids:
            return PetIncrementalCommitResult()

        staged = list(detections)
        if self._state_repo is not None and staged:
            rejected = self._state_repo.get_rejected_pet_keys(
                detection.pet_key for detection in staged
            )
            staged = [detection for detection in staged if detection.pet_key not in rejected]

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
        existing_pets = {
            pet.pet_id: pet
            for pet in self.get_all_pet_records()
            if contract is None
            or (
                pet.embedding_pipeline_version,
                int(pet.embedding_dim),
                int(pet.generation_id),
            )
            == contract
        }
        assigned = self._assign_incremental_pet_ids(
            staged,
            existing_pets=existing_pets,
            distance_threshold=distance_threshold,
        )

        with closing(self._connect()) as conn:
            previous_rows = self._select_detections_by_asset_ids(conn, changed_asset_ids)
            previous_detections = [self._detection_from_row(row) for row in previous_rows]
            previous_thumbnail_paths = tuple(
                str(detection.thumbnail_path)
                for detection in previous_detections
                if detection.thumbnail_path
            )
            old_pet_ids = {
                str(detection.pet_id)
                for detection in previous_detections
                if detection.pet_id
            }
            new_pet_ids = {
                str(detection.pet_id) for detection in assigned if detection.pet_id
            }
            affected_pet_ids = tuple(sorted(old_pet_ids | new_pet_ids))

            for chunk in _chunked(changed_asset_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM pet_detections WHERE asset_id IN ({placeholders})",
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
            names = {pet_id: pet.name for pet_id, pet in existing_pets.items()}
            created_at = {pet_id: pet.created_at for pet_id, pet in existing_pets.items()}
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
            conn.commit()

        if self._state_repo is not None:
            self._state_repo.sync_scan_results(
                rebuilt_pets,
                runtime_detections,
                replaced_pet_ids=affected_pet_ids,
            )

        surviving_pet_ids = {pet.pet_id for pet in rebuilt_pets}
        added = tuple(sorted(new_pet_ids - set(existing_pets)))
        removed = tuple(sorted(old_pet_ids - surviving_pet_ids))
        updated = tuple(sorted((old_pet_ids | new_pet_ids) - set(added) - set(removed)))
        return PetIncrementalCommitResult(
            previous_thumbnail_paths=previous_thumbnail_paths,
            added_pet_ids=added,
            updated_pet_ids=updated,
            removed_pet_ids=removed,
        )

    def _assign_incremental_pet_ids(
        self,
        detections: list[PetDetectionRecord],
        *,
        existing_pets: dict[str, PetRecord],
        distance_threshold: float,
    ) -> list[PetDetectionRecord]:
        if not detections:
            return []
        redirects = (
            self._state_repo.get_merge_redirect_map() if self._state_repo is not None else {}
        )
        key_map = (
            self._state_repo.get_pet_key_map(detection.pet_key for detection in detections)
            if self._state_repo is not None
            else {}
        )
        centers = {
            pet_id: normalize_vector(pet.center_embedding)
            for pet_id, pet in existing_pets.items()
        }
        sample_counts = {
            pet_id: max(int(pet.sample_count), int(pet.detection_count), 1)
            for pet_id, pet in existing_pets.items()
        }
        species = {
            pet_id: _normalize_species_label(pet.species_label)
            for pet_id, pet in existing_pets.items()
        }
        candidate_index = _ProfileCandidateIndex(centers, species)
        new_pet_ids: set[str] = set()
        boundary_samples: dict[str, tuple[np.ndarray, ...]] = {}
        assigned: list[PetDetectionRecord] = []
        for detection in detections:
            detection_species = _normalize_species_label(detection.species_label)
            mapped_id = key_map.get(detection.pet_key, "")
            candidate_id = redirects.get(mapped_id, mapped_id)
            if candidate_id not in centers or not _species_compatible(
                detection_species,
                species.get(candidate_id),
            ):
                candidate_id = ""
            if not candidate_id:
                candidate_id = self._nearest_compatible_pet_id(
                    detection,
                    centers=centers,
                    species=species,
                    boundary_samples=boundary_samples,
                    candidate_index=candidate_index,
                    new_pet_ids=new_pet_ids,
                    distance_threshold=distance_threshold,
                )
            if not candidate_id:
                candidate_id = str(uuid.uuid4())
                centers[candidate_id] = normalize_vector(detection.embedding)
                sample_counts[candidate_id] = 0
                species[candidate_id] = detection_species
                boundary_samples[candidate_id] = ()
                new_pet_ids.add(candidate_id)
            count = sample_counts.get(candidate_id, 0)
            center = centers.get(candidate_id, normalize_vector(detection.embedding))
            centers[candidate_id] = normalize_vector(
                (center * float(count) + normalize_vector(detection.embedding))
                / float(count + 1)
            )
            sample_counts[candidate_id] = count + 1
            samples = boundary_samples.get(candidate_id, ())
            boundary_samples[candidate_id] = (
                *samples,
                normalize_vector(detection.embedding),
            )[-8:]
            assigned.append(replace(detection, pet_id=candidate_id))
        return assigned

    def _nearest_compatible_pet_id(
        self,
        detection: PetDetectionRecord,
        *,
        centers: dict[str, np.ndarray],
        species: dict[str, str | None],
        boundary_samples: dict[str, tuple[np.ndarray, ...]],
        candidate_index: _ProfileCandidateIndex,
        new_pet_ids: set[str],
        distance_threshold: float,
    ) -> str:
        detection_species = _normalize_species_label(detection.species_label)
        candidates = candidate_index.search(
            detection.embedding,
            species_label=detection_species,
            limit=8,
        )
        candidates.extend(
            (cosine_distance(detection.embedding, centers[pet_id]), pet_id)
            for pet_id in new_pet_ids
            if centers[pet_id].size == detection.embedding_dim
            and _species_compatible(detection_species, species.get(pet_id))
        )
        for center_distance, pet_id in sorted(candidates)[:8]:
            if center_distance > distance_threshold:
                continue
            samples = boundary_samples.get(pet_id)
            if samples is None:
                samples = self._load_boundary_samples(pet_id, limit=8)
                boundary_samples[pet_id] = samples
            if samples and max(
                cosine_distance(detection.embedding, sample) for sample in samples
            ) > distance_threshold:
                continue
            return pet_id
        return ""

    def _load_boundary_samples(self, pet_id: str, *, limit: int) -> tuple[np.ndarray, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT embedding, embedding_dim
                FROM pet_detections
                WHERE pet_id = ?
                ORDER BY COALESCE(quality_score, confidence) ASC, detection_id ASC
                LIMIT ?
                """,
                (pet_id, max(1, min(int(limit), 8))),
            ).fetchall()
        return tuple(
            deserialize_embedding(row["embedding"], int(row["embedding_dim"] or 0))
            for row in rows
        )

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
        active_version = self.get_scan_metadata("active_embedding_pipeline_version")
        active_dimension = self.get_scan_metadata("active_embedding_dimension")
        active_generation = int(self.get_scan_metadata("active_generation_id") or 0)
        if active_version is None:
            generation_id = 0
        elif active_version == version and int(active_dimension or 0) == dimension:
            generation_id = active_generation
        else:
            self.initialize()
            with closing(self._connect()) as conn:
                row = conn.execute(
                    """
                    SELECT MAX(generation_id) AS generation_id
                    FROM (
                        SELECT generation_id FROM pet_detections
                        UNION ALL SELECT generation_id FROM pets
                    )
                    """
                ).fetchone()
            generation_id = int(row["generation_id"] or 0) + 1
        return [replace(item, generation_id=generation_id) for item in staged], generation_id

    def activate_embedding_generation(
        self,
        *,
        generation_id: int,
        embedding_pipeline_version: str,
        embedding_dimension: int,
    ) -> None:
        self.set_scan_metadata("active_generation_id", str(int(generation_id)))
        self.set_scan_metadata(
            "active_embedding_pipeline_version",
            embedding_pipeline_version,
        )
        self.set_scan_metadata("active_embedding_dimension", str(int(embedding_dimension)))

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

    def recluster_detections(
        self,
        *,
        distance_threshold: float,
    ) -> int:
        from .pipeline import canonicalize_pet_identities, cluster_pet_records

        detections = self.get_all_detections()
        if not detections:
            return 0
        clustered_detections, pets = cluster_pet_records(
            detections,
            distance_threshold=distance_threshold,
        )
        if self._state_repo is not None:
            clustered_detections, pets = canonicalize_pet_identities(
                clustered_detections,
                pets,
                self._state_repo,
                distance_threshold=distance_threshold,
            )
        self.replace_all(clustered_detections, pets)
        return len(clustered_detections)

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
        return [
            self._detection_from_row(row)
            for row in rows
            if row["pet_key"] not in rejected
        ]

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
        asset_ids_by_pet_id: dict[str, set[str]] = {}
        for asset_row in asset_rows:
            if not asset_row["pet_id"] or not asset_row["asset_id"]:
                continue
            runtime_pet_id = str(asset_row["pet_id"])
            canonical_pet_id = merge_redirects.get(runtime_pet_id, runtime_pet_id)
            pet_asset_ids = asset_ids_by_pet_id.setdefault(canonical_pet_id, set())
            pet_asset_ids.add(str(asset_row["asset_id"]))
        pet_ids = [str(row["pet_id"]) for row in rows if row["pet_id"]]
        hidden_map: dict[str, bool] = {}
        cover_paths: dict[str, str] = {}
        profile_names: dict[str, str | None] = {}
        if self._state_repo is not None:
            hidden_map = self._state_repo.get_pet_hidden_map(pet_ids)
            cover_paths = self._state_repo.get_pet_cover_thumbnail_map(pet_ids)
            profile_names = self._state_repo.get_profile_name_map(pet_ids)

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
            summaries.append(
                PetSummary(
                    pet_id=pet_id,
                    name=name,
                    key_detection_id=str(row["key_detection_id"] or ""),
                    detection_count=int(row["detection_count"] or 0),
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(row["created_at"] or ""),
                    is_hidden=bool(hidden_map.get(pet_id, False)),
                    asset_count=len(asset_ids_by_pet_id.get(pet_id, set())),
                )
            )
        if not include_hidden:
            summaries = [summary for summary in summaries if not summary.is_hidden]
        return summaries

    def get_asset_ids_by_pet(self, pet_id: str) -> list[str]:
        if not pet_id:
            return []
        self.initialize()
        runtime_pet_ids = [pet_id]
        if self._state_repo is not None:
            redirects = self._state_repo.get_merge_redirect_map()
            runtime_pet_ids.extend(
                source_id for source_id, target_id in redirects.items() if target_id == pet_id
            )
        runtime_pet_ids = list(dict.fromkeys(runtime_pet_ids))
        placeholders = ", ".join("?" for _ in runtime_pet_ids)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT asset_id
                FROM pet_detections
                WHERE pet_id IN ({placeholders})
                ORDER BY asset_id ASC
                """,
                runtime_pet_ids,
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def get_asset_ids_by_pets(
        self,
        pet_ids: Iterable[str],
    ) -> dict[str, list[str]]:
        """Return assets for all requested Pets using one SQLite connection."""

        ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        result: dict[str, list[str]] = {pet_id: [] for pet_id in ids}
        if not ids:
            return result
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
        canonical_identities = self._canonical_annotation_identities(
            str(row["pet_id"]) for row in rows if row["pet_id"]
        )
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
                        canonical_identities[runtime_pet_id][0]
                        if runtime_pet_id
                        else "pet"
                    ),
                    canonical_identity_id=(
                        canonical_identities[runtime_pet_id][1]
                        if runtime_pet_id
                        else None
                    ),
                    canonical_display_name=(
                        canonical_identities[runtime_pet_id][2]
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
        pet_redirects = (
            self._state_repo.get_merge_redirect_map() if self._state_repo is not None else {}
        )
        redirect_map: dict[tuple[str, str], tuple[str, str]] = {
            ("pet", source_id): ("pet", target_id)
            for source_id, target_id in pet_redirects.items()
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
            source_id: _resolve_identity_redirect("pet", source_id, redirect_map)
            for source_id in source_ids
        }
        pet_targets = [entity_id for kind, entity_id in resolved.values() if kind == "pet"]
        pet_names = (
            self._state_repo.get_profile_name_map(pet_targets)
            if self._state_repo is not None
            else {}
        )
        person_targets = [
            entity_id for kind, entity_id in resolved.values() if kind == "person"
        ]
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
            source_id: (
                kind,
                entity_id,
                pet_names.get(entity_id) if kind == "pet" else person_names.get(entity_id),
            )
            for source_id, (kind, entity_id) in resolved.items()
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
        return int(cursor.rowcount or 0) > 0 or self._state_repo.get_profile(
            canonical_pet_id
        ) is not None

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

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> PetMutationResult | None:
        if self._state_repo is None:
            return None
        self.initialize()
        runtime_pets = [
            pet
            for pet in self.get_all_pet_records()
            if pet.pet_id in {source_pet_id, target_pet_id}
        ]
        runtime_detections = [
            detection
            for detection in self.get_all_detections()
            if detection.pet_id in {source_pet_id, target_pet_id}
        ]
        self._state_repo.ensure_runtime_candidates(runtime_pets, runtime_detections)
        durable_merged = self._state_repo.merge_pets(source_pet_id, target_pet_id)
        if not durable_merged:
            redirects = self._state_repo.get_merge_redirect_map()
            if redirects.get(source_pet_id) != target_pet_id:
                return None
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
            conn.execute("DELETE FROM pets WHERE pet_id = ?", (source_pet_id,))
            conn.commit()
        self._remap_people_groups_for_pet_merge(source_pet_id, target_pet_id)
        self._rebuild_pet_records_from_detections()
        return PetMutationResult(
            changed_asset_ids=tuple(str(row["asset_id"]) for row in asset_rows if row["asset_id"]),
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

    def delete_detection(self, detection_id: str) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None:
            return None
        if self._state_repo is not None:
            self._state_repo.add_rejected_pet_key(detection.pet_key)
            self._state_repo.clear_cover_for_detection(detection_id)
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM pet_detections WHERE detection_id = ?", (detection_id,))
            conn.commit()
        self._rebuild_pet_records_from_detections()
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
    ) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None or not target_pet_id:
            return None
        with closing(self._connect()) as conn:
            target = conn.execute(
                "SELECT pet_id FROM pets WHERE pet_id = ?",
                (target_pet_id,),
            ).fetchone()
            if target is None:
                return None
            conn.execute(
                "UPDATE pet_detections SET pet_id = ? WHERE detection_id = ?",
                (target_pet_id, detection_id),
            )
            conn.commit()
        self._rebuild_pet_records_from_detections()
        self._refresh_people_group_assets_for_pets((detection.pet_id, target_pet_id))
        return PetMutationResult(
            changed_asset_ids=(detection.asset_id,),
            changed_pet_ids=tuple(
                pet_id for pet_id in (detection.pet_id, target_pet_id) if pet_id
            ),
        )

    def move_detection_to_new_pet(
        self,
        detection_id: str,
        new_pet_id: str,
        new_name: str | None,
    ) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None or not new_pet_id:
            return None
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pet_detections SET pet_id = ? WHERE detection_id = ?",
                (new_pet_id, detection_id),
            )
            conn.commit()
        self._rebuild_pet_records_from_detections()
        if new_name:
            self.rename_pet(new_pet_id, new_name)
        self._refresh_people_group_assets_for_pets((detection.pet_id, new_pet_id))
        return PetMutationResult(
            changed_asset_ids=(detection.asset_id,),
            changed_pet_ids=tuple(pet_id for pet_id in (detection.pet_id, new_pet_id) if pet_id),
        )

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
            profile_state_for_sample_count(sample_count),
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
            embedding_pipeline_version=str(
                _row_value(row, "embedding_pipeline_version", "") or ""
            ),
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
            embedding_pipeline_version=str(
                _row_value(row, "embedding_pipeline_version", "") or ""
            ),
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
    return left is None or right is None or left == right


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
        self._indexes: dict[tuple[int, str | None], tuple[object, tuple[str, ...]]] = {}
        try:
            from usearch.index import Index
        except ImportError:
            return
        grouped: dict[tuple[int, str | None], list[tuple[str, np.ndarray]]] = {}
        for pet_id, center in centers.items():
            if center.size:
                grouped.setdefault((int(center.size), species.get(pet_id)), []).append(
                    (pet_id, center)
                )
        for group_key, members in grouped.items():
            ordered = tuple(sorted(members, key=lambda item: item[0]))
            index = Index(ndim=group_key[0], metric="cos", dtype="f32")
            index.add(
                np.arange(len(ordered), dtype=np.uint64),
                np.stack([center for _, center in ordered], axis=0),
            )
            self._indexes[group_key] = (index, tuple(pet_id for pet_id, _ in ordered))

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
        labels = (
            {label for dim, label in self._indexes if dim == vector.size}
            if species_label is None
            else {species_label, None}
        )
        matches: list[tuple[float, str]] = []
        for label in labels:
            entry = self._indexes.get((int(vector.size), label))
            if entry is None:
                continue
            index, pet_ids = entry
            result = index.search(vector, min(limit, len(pet_ids)))
            matches.extend(
                (float(distance), pet_ids[int(key)])
                for key, distance in zip(result.keys, result.distances, strict=True)
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

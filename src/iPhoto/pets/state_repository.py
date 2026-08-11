"""SQLite state repository for persisted pet user decisions."""

# ruff: noqa: S608

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite
from iPhoto.recognition.promotion import (
    PROMOTION_CONFIRMED,
    PROMOTION_LEGACY_VISIBLE,
    IdentityPromotionRecord,
    automatic_promotion_state,
    merged_promotion_state,
    normalize_promotion_state,
)

from .records import PetDetectionRecord, PetProfile, PetRecord
from .repository_utils import (
    deserialize_embedding,
    normalize_name,
    profile_state_for_sample_count,
    serialize_embedding,
    utc_now_iso,
)

_PET_PROMOTION_MIN_ASSETS = 2


@dataclass(frozen=True)
class PetCoverRecord:
    pet_id: str
    detection_id: str | None
    pet_key: str | None
    asset_id: str | None
    thumbnail_path: str | None
    is_custom: bool


@dataclass(frozen=True)
class _IncrementalStateSnapshot:
    rejected_keys: frozenset[str]
    key_map: dict[str, str]
    redirects: dict[str, str]
    durable_profiles: dict[str, PetProfile]


class PetStateRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                self._create_schema(conn)
            self._initialized = True

    def get_profiles(self, *, include_redirected: bool = False) -> list[PetProfile]:
        self.initialize()
        with closing(self._connect()) as conn:
            redirected_ids = {
                str(row["source_pet_id"])
                for row in conn.execute(
                    "SELECT source_pet_id FROM merge_redirects"
                ).fetchall()
                if row["source_pet_id"]
            }
            inferred_counts = {
                str(row["pet_id"]): int(row["sample_count"] or 0)
                for row in conn.execute(
                    """
                    SELECT pet_id, COUNT(*) AS sample_count
                    FROM pet_keys
                    GROUP BY pet_id
                    """
                ).fetchall()
            }
            rows = conn.execute(
                """
                SELECT
                    pet_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state, species_label,
                    embedding_pipeline_version, generation_id,
                    boundary_embeddings, boundary_sample_count
                FROM pet_profiles
                """
            ).fetchall()
            promotion_rows = conn.execute(
                "SELECT pet_id, evidence_asset_count, promotion_state "
                "FROM pet_identity_promotions"
            ).fetchall()
        promotions = {
            str(row["pet_id"]): IdentityPromotionRecord(
                identity_id=str(row["pet_id"]),
                evidence_asset_count=int(row["evidence_asset_count"] or 0),
                promotion_state=normalize_promotion_state(row["promotion_state"]),
            )
            for row in promotion_rows
        }
        profiles: list[PetProfile] = []
        for row in rows:
            pet_id = str(row["pet_id"])
            if not include_redirected and pet_id in redirected_ids:
                continue
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                sample_count = inferred_counts.get(pet_id, 0)
            promotion = promotions.get(pet_id)
            evidence_asset_count = (
                promotion.evidence_asset_count if promotion is not None else sample_count
            )
            profiles.append(
                PetProfile(
                    pet_id=pet_id,
                    name=row["name"],
                    center_embedding=deserialize_embedding(
                        row["center_embedding"],
                        int(row["embedding_dim"] or 0),
                    ),
                    embedding_dim=int(row["embedding_dim"] or 0),
                    created_at=str(row["created_at"] or ""),
                    updated_at=str(row["updated_at"] or ""),
                    sample_count=sample_count,
                    profile_state=profile_state_for_sample_count(evidence_asset_count),
                    species_label=_normalize_species_label(row["species_label"]),
                    embedding_pipeline_version=str(
                        row["embedding_pipeline_version"] or ""
                    ),
                    generation_id=int(row["generation_id"] or 0),
                    boundary_embeddings=_deserialize_boundary_embeddings(
                        row["boundary_embeddings"],
                        embedding_dim=int(row["embedding_dim"] or 0),
                        sample_count=int(row["boundary_sample_count"] or 0),
                    ),
                    evidence_asset_count=evidence_asset_count,
                    promotion_state=(
                        promotion.promotion_state
                        if promotion is not None
                        else PROMOTION_LEGACY_VISIBLE
                    ),
                )
            )
        return profiles

    def get_identity_profiles(self) -> list[PetProfile]:
        """Return active profiles and redirected profiles used as aliases."""

        return self.get_profiles(include_redirected=True)

    def get_profile(self, pet_id: str) -> PetProfile | None:
        if not pet_id:
            return None
        return self.get_profiles_by_ids((pet_id,)).get(pet_id)

    def get_profiles_by_ids(self, pet_ids: Iterable[str]) -> dict[str, PetProfile]:
        unique_ids = tuple(str(value) for value in dict.fromkeys(pet_ids) if value)
        if not unique_ids:
            return {}
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(unique_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT
                            pet_profiles.pet_id, pet_profiles.name,
                            pet_profiles.center_embedding, pet_profiles.embedding_dim,
                            pet_profiles.created_at, pet_profiles.updated_at,
                            pet_profiles.sample_count, pet_profiles.profile_state,
                            pet_profiles.species_label,
                            pet_profiles.embedding_pipeline_version,
                            pet_profiles.generation_id,
                            pet_profiles.boundary_embeddings,
                            pet_profiles.boundary_sample_count,
                            pet_identity_promotions.evidence_asset_count,
                            pet_identity_promotions.promotion_state
                        FROM pet_profiles
                        LEFT JOIN pet_identity_promotions
                          ON pet_identity_promotions.pet_id = pet_profiles.pet_id
                        WHERE pet_profiles.pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        return {
            str(row["pet_id"]): _profile_from_row(row)
            for row in rows
            if row["pet_id"]
        }

    def get_promotion_records(
        self,
        pet_ids: Iterable[str] = (),
    ) -> dict[str, IdentityPromotionRecord]:
        self.initialize()
        ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        with closing(self._connect()) as conn:
            if ids:
                rows: list[sqlite3.Row] = []
                for chunk in _chunked(ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    rows.extend(
                        conn.execute(
                            "SELECT pet_id, evidence_asset_count, promotion_state "
                            f"FROM pet_identity_promotions WHERE pet_id IN ({placeholders})",
                            chunk,
                        ).fetchall()
                    )
            else:
                rows = conn.execute(
                    "SELECT pet_id, evidence_asset_count, promotion_state "
                    "FROM pet_identity_promotions"
                ).fetchall()
        return {
            str(row["pet_id"]): IdentityPromotionRecord(
                identity_id=str(row["pet_id"]),
                evidence_asset_count=int(row["evidence_asset_count"] or 0),
                promotion_state=normalize_promotion_state(row["promotion_state"]),
            )
            for row in rows
        }

    def confirm_pet(self, pet_id: str) -> None:
        if not pet_id:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO pet_identity_promotions (
                    pet_id, evidence_asset_count, promotion_state, updated_at
                ) VALUES (?, 0, ?, ?)
                ON CONFLICT(pet_id) DO UPDATE SET
                    promotion_state = excluded.promotion_state,
                    updated_at = excluded.updated_at
                """,
                (pet_id, PROMOTION_CONFIRMED, utc_now_iso()),
            )
            conn.commit()

    def get_profile_name_map(self, pet_ids: Iterable[str]) -> dict[str, str | None]:
        unique_ids = [str(pet_id) for pet_id in dict.fromkeys(pet_ids) if pet_id]
        if not unique_ids:
            return {}
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(unique_ids), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT pet_id, name
                        FROM pet_profiles
                        WHERE pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        return {str(row["pet_id"]): row["name"] for row in rows if row["pet_id"]}

    def get_pet_key_map(self, pet_keys: Iterable[str]) -> dict[str, str]:
        unique_keys = [str(key) for key in dict.fromkeys(pet_keys) if key]
        if not unique_keys:
            return {}
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(unique_keys), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT pet_key, pet_id
                        FROM pet_keys
                        WHERE pet_key IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        return {str(row["pet_key"]): str(row["pet_id"]) for row in rows if row["pet_key"]}

    def get_rejected_pet_keys(self, pet_keys: Iterable[str]) -> set[str]:
        unique_keys = [str(key) for key in dict.fromkeys(pet_keys) if key]
        if not unique_keys:
            return set()
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(unique_keys), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT pet_key
                        FROM rejected_pet_keys
                        WHERE pet_key IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        return {str(row["pet_key"]) for row in rows if row["pet_key"]}

    def _load_incremental_state(
        self,
        pet_keys: Iterable[str],
    ) -> _IncrementalStateSnapshot:
        """Read all durable assignment inputs from one state-DB snapshot."""

        unique_keys = tuple(str(key) for key in dict.fromkeys(pet_keys) if key)
        if not unique_keys:
            return _IncrementalStateSnapshot(frozenset(), {}, {}, {})
        self.initialize()
        with closing(self._connect()) as conn:
            # Python's sqlite3 driver does not open a transaction for SELECTs.
            # Pin all assignment inputs to one WAL snapshot explicitly.
            conn.execute("BEGIN")
            rejected_rows: list[sqlite3.Row] = []
            key_rows: list[sqlite3.Row] = []
            for chunk in _chunked(unique_keys, 500):
                placeholders = ", ".join("?" for _ in chunk)
                rejected_rows.extend(
                    conn.execute(
                        f"SELECT pet_key FROM rejected_pet_keys "
                        f"WHERE pet_key IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
                key_rows.extend(
                    conn.execute(
                        f"SELECT pet_key, pet_id FROM pet_keys "
                        f"WHERE pet_key IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
            redirect_rows = conn.execute(
                "SELECT source_pet_id, target_pet_id FROM merge_redirects"
            ).fetchall()
            redirects = _canonical_redirect_map(
                {
                    str(row["source_pet_id"]): str(row["target_pet_id"])
                    for row in redirect_rows
                    if row["source_pet_id"] and row["target_pet_id"]
                }
            )
            key_map = {
                str(row["pet_key"]): str(row["pet_id"])
                for row in key_rows
                if row["pet_key"] and row["pet_id"]
            }
            mapped_ids = tuple(
                dict.fromkeys(
                    redirects.get(pet_id, pet_id)
                    for pet_id in key_map.values()
                    if pet_id
                )
            )
            profile_rows: list[sqlite3.Row] = []
            for chunk in _chunked(mapped_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                profile_rows.extend(
                    conn.execute(
                        f"""
                        SELECT
                            pet_id, name, center_embedding, embedding_dim,
                            created_at, updated_at, sample_count, profile_state,
                            species_label, embedding_pipeline_version, generation_id,
                            boundary_embeddings, boundary_sample_count
                        FROM pet_profiles
                        WHERE pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
            conn.rollback()
        return _IncrementalStateSnapshot(
            rejected_keys=frozenset(
                str(row["pet_key"]) for row in rejected_rows if row["pet_key"]
            ),
            key_map=key_map,
            redirects=redirects,
            durable_profiles={
                str(row["pet_id"]): _profile_from_row(row)
                for row in profile_rows
                if row["pet_id"]
            },
        )

    def add_rejected_pet_key(self, pet_key: str) -> None:
        if not pet_key:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO rejected_pet_keys (pet_key, rejected_at)
                VALUES (?, ?)
                """,
                (pet_key, utc_now_iso()),
            )
            conn.commit()

    def migrate_pet_keys(self, mappings: Iterable[tuple[str, str]]) -> None:
        """Add replacement keys without copying any rejection decisions."""

        normalized = tuple(
            (str(pet_key), str(pet_id))
            for pet_key, pet_id in mappings
            if pet_key and pet_id
        )
        if not normalized:
            return
        self.initialize()
        timestamp = utc_now_iso()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO pet_keys (pet_key, pet_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(pet_key) DO UPDATE SET
                    pet_id = excluded.pet_id,
                    updated_at = excluded.updated_at
                """,
                [(pet_key, pet_id, timestamp) for pet_key, pet_id in normalized],
            )
            conn.commit()

    def sync_scan_results(
        self,
        pets: list[PetRecord],
        detections: list[PetDetectionRecord],
        *,
        replaced_pet_ids: Iterable[str] = (),
    ) -> None:
        self.initialize()
        timestamp = utc_now_iso()
        detection_by_id = {detection.detection_id: detection for detection in detections}
        with closing(self._connect()) as conn:
            unique_pet_ids = tuple(
                dict.fromkeys(pet.pet_id for pet in pets if pet.pet_id)
            )
            name_rows: list[sqlite3.Row] = []
            for chunk in _chunked(unique_pet_ids, 500):
                placeholders = ", ".join("?" for _ in chunk)
                name_rows.extend(
                    conn.execute(
                        f"SELECT pet_id, name FROM pet_profiles "
                        f"WHERE pet_id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
            names = {
                str(row["pet_id"]): row["name"]
                for row in name_rows
                if row["pet_id"]
            }
            redirect_rows = conn.execute(
                "SELECT source_pet_id, target_pet_id FROM merge_redirects"
            ).fetchall()
            redirects = _canonical_redirect_map(
                {
                    str(row["source_pet_id"]): str(row["target_pet_id"])
                    for row in redirect_rows
                    if row["source_pet_id"] and row["target_pet_id"]
                }
            )
            evidence_assets_by_pet: dict[str, set[str]] = {}
            for detection in detections:
                if not detection.pet_id or not detection.asset_id:
                    continue
                canonical_pet_id = redirects.get(detection.pet_id, detection.pet_id)
                evidence_assets_by_pet.setdefault(canonical_pet_id, set()).add(
                    detection.asset_id
                )
            for pet in pets:
                if pet.pet_id in redirects:
                    continue
                sample_count = max(int(pet.sample_count), int(pet.detection_count))
                evidence_asset_count = len(evidence_assets_by_pet.get(pet.pet_id, set()))
                existing = conn.execute(
                    "SELECT created_at, name FROM pet_profiles WHERE pet_id = ?",
                    (pet.pet_id,),
                ).fetchone()
                created_at = (
                    str(existing["created_at"])
                    if existing is not None and existing["created_at"]
                    else pet.created_at
                )
                name = names.get(pet.pet_id)
                if name is None and existing is not None:
                    name = existing["name"]
                if name is None:
                    name = pet.name
                conn.execute(
                    """
                    INSERT INTO pet_profiles (
                        pet_id, name, center_embedding, embedding_dim,
                        created_at, updated_at, sample_count, profile_state, species_label,
                        embedding_pipeline_version, generation_id,
                        boundary_embeddings, boundary_sample_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        name = COALESCE(pet_profiles.name, excluded.name),
                        center_embedding = excluded.center_embedding,
                        embedding_dim = excluded.embedding_dim,
                        updated_at = excluded.updated_at,
                        sample_count = excluded.sample_count,
                        profile_state = excluded.profile_state,
                        species_label = excluded.species_label,
                        embedding_pipeline_version = excluded.embedding_pipeline_version,
                        generation_id = excluded.generation_id
                        , boundary_embeddings = excluded.boundary_embeddings
                        , boundary_sample_count = excluded.boundary_sample_count
                    """,
                    (
                        pet.pet_id,
                        normalize_name(name),
                        serialize_embedding(pet.center_embedding),
                        pet.embedding_dim,
                        created_at,
                        timestamp,
                        sample_count,
                        profile_state_for_sample_count(evidence_asset_count),
                        _normalize_species_label(pet.species_label),
                        pet.embedding_pipeline_version,
                        pet.generation_id,
                        _serialize_boundary_embeddings(
                            pet.boundary_embeddings,
                            pet.embedding_dim,
                        ),
                        min(len(pet.boundary_embeddings), 8),
                    ),
                )
                state = automatic_promotion_state(
                    evidence_asset_count,
                    minimum_evidence=_PET_PROMOTION_MIN_ASSETS,
                )
                conn.execute(
                    """
                    INSERT INTO pet_identity_promotions (
                        pet_id, evidence_asset_count, promotion_state, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        evidence_asset_count = excluded.evidence_asset_count,
                        promotion_state = CASE
                            WHEN pet_identity_promotions.promotion_state IN (
                                'confirmed', 'legacy_visible'
                            ) THEN pet_identity_promotions.promotion_state
                            ELSE excluded.promotion_state
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (pet.pet_id, evidence_asset_count, state, timestamp),
                )

            for detection in detections:
                if not detection.pet_id:
                    continue
                canonical_pet_id = redirects.get(detection.pet_id, detection.pet_id)
                existing_key = conn.execute(
                    "SELECT pet_id FROM pet_keys WHERE pet_key = ?",
                    (detection.pet_key,),
                ).fetchone()
                durable_pet_id = canonical_pet_id
                if existing_key is not None and existing_key["pet_id"]:
                    raw_pet_id = str(existing_key["pet_id"])
                    if redirects.get(raw_pet_id, raw_pet_id) == canonical_pet_id:
                        durable_pet_id = raw_pet_id
                conn.execute(
                    """
                    INSERT INTO pet_keys (pet_key, pet_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(pet_key) DO UPDATE SET
                        pet_id = excluded.pet_id,
                        updated_at = excluded.updated_at
                    """,
                    (detection.pet_key, durable_pet_id, timestamp),
                )

            for pet in pets:
                key_detection = detection_by_id.get(pet.key_detection_id)
                if key_detection is None:
                    continue
                custom = conn.execute(
                    "SELECT is_custom, pet_key FROM pet_covers WHERE pet_id = ?",
                    (pet.pet_id,),
                ).fetchone()
                if custom is not None and int(custom["is_custom"] or 0) == 1:
                    custom_key = str(custom["pet_key"] or "")
                    replacement = next(
                        (
                            detection
                            for detection in detections
                            if detection.pet_id == pet.pet_id
                            and detection.pet_key == custom_key
                        ),
                        None,
                    )
                    if replacement is not None:
                        conn.execute(
                            """
                            UPDATE pet_covers
                            SET detection_id = ?, asset_id = ?, thumbnail_path = ?,
                                updated_at = ?
                            WHERE pet_id = ?
                            """,
                            (
                                replacement.detection_id,
                                replacement.asset_id,
                                replacement.thumbnail_path,
                                timestamp,
                                pet.pet_id,
                            ),
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO pet_covers (
                        pet_id, detection_id, pet_key, asset_id, thumbnail_path,
                        is_custom, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        detection_id = excluded.detection_id,
                        pet_key = excluded.pet_key,
                        asset_id = excluded.asset_id,
                        thumbnail_path = excluded.thumbnail_path,
                        updated_at = excluded.updated_at
                    """,
                    (
                        pet.pet_id,
                        key_detection.detection_id,
                        key_detection.pet_key,
                        key_detection.asset_id,
                        key_detection.thumbnail_path,
                        timestamp,
                    ),
                )
            current_pet_ids = {pet.pet_id for pet in pets if pet.pet_id}
            replaced_ids = {str(pet_id) for pet_id in replaced_pet_ids if pet_id}
            stale_automatic_cover_ids = sorted(replaced_ids - current_pet_ids)
            conn.executemany(
                "DELETE FROM pet_covers WHERE pet_id = ? AND is_custom = 0",
                [(pet_id,) for pet_id in stale_automatic_cover_ids],
            )
            conn.commit()

    def ensure_runtime_candidates(
        self,
        pets: Iterable[PetRecord],
        detections: Iterable[PetDetectionRecord],
    ) -> None:
        """Backfill missing durable rows from a legacy runtime snapshot.

        This is deliberately insert-only: names, covers, hidden state and
        existing key assignments are user state and must never be overwritten
        by compatibility repair.
        """

        self.initialize()
        timestamp = utc_now_iso()
        with closing(self._connect()) as conn:
            redirect_rows = conn.execute(
                "SELECT source_pet_id, target_pet_id FROM merge_redirects"
            ).fetchall()
            redirects = _canonical_redirect_map(
                {
                    str(row["source_pet_id"]): str(row["target_pet_id"])
                    for row in redirect_rows
                    if row["source_pet_id"] and row["target_pet_id"]
                }
            )
            for pet in pets:
                if pet.pet_id in redirects:
                    continue
                sample_count = max(int(pet.sample_count), int(pet.detection_count))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pet_profiles (
                        pet_id, name, center_embedding, embedding_dim,
                        created_at, updated_at, sample_count, profile_state, species_label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pet.pet_id,
                        normalize_name(pet.name),
                        serialize_embedding(pet.center_embedding),
                        pet.embedding_dim,
                        pet.created_at,
                        timestamp,
                        sample_count,
                        profile_state_for_sample_count(sample_count),
                        _normalize_species_label(pet.species_label),
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pet_identity_promotions (
                        pet_id, evidence_asset_count, promotion_state, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        pet.pet_id,
                        max(0, int(pet.evidence_asset_count or sample_count)),
                        PROMOTION_LEGACY_VISIBLE,
                        timestamp,
                    ),
                )
            for detection in detections:
                if not detection.pet_id:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pet_keys (pet_key, pet_id, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (detection.pet_key, detection.pet_id, timestamp),
                )
            conn.commit()

    def get_merge_redirect_map(self) -> dict[str, str]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT source_pet_id, target_pet_id
                FROM merge_redirects
                ORDER BY updated_at ASC, source_pet_id ASC
                """
            ).fetchall()
        redirects = {
            str(row["source_pet_id"]): str(row["target_pet_id"])
            for row in rows
            if row["source_pet_id"] and row["target_pet_id"]
        }
        return _canonical_redirect_map(redirects)

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> None:
        if not pet_id:
            return
        self.initialize()
        timestamp = utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE pet_profiles
                SET name = ?, updated_at = ?
                WHERE pet_id = ?
                """,
                (normalize_name(name_or_none), timestamp, pet_id),
            )
            if normalize_name(name_or_none) is not None:
                conn.execute(
                    """
                    INSERT INTO pet_identity_promotions (
                        pet_id, evidence_asset_count, promotion_state, updated_at
                    ) VALUES (?, 0, ?, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        promotion_state = excluded.promotion_state,
                        updated_at = excluded.updated_at
                    """,
                    (pet_id, PROMOTION_CONFIRMED, timestamp),
                )
            conn.commit()

    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool:
        if not pet_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            previous = conn.execute(
                "SELECT 1 FROM hidden_pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if hidden:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO hidden_pets (pet_id, hidden_at)
                    VALUES (?, ?)
                    """,
                    (pet_id, utc_now_iso()),
                )
            else:
                conn.execute("DELETE FROM hidden_pets WHERE pet_id = ?", (pet_id,))
            conn.commit()
        return bool(previous) != bool(hidden)

    def is_pet_hidden(self, pet_id: str) -> bool:
        if not pet_id:
            return False
        return bool(self.get_pet_hidden_map([pet_id]).get(pet_id, False))

    def get_pet_hidden_map(self, pet_ids: Iterable[str]) -> dict[str, bool]:
        unique_ids = [str(pet_id) for pet_id in dict.fromkeys(pet_ids) if pet_id]
        if not unique_ids:
            return {}
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(unique_ids), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT pet_id
                        FROM hidden_pets
                        WHERE pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        hidden = {str(row["pet_id"]) for row in rows if row["pet_id"]}
        return {pet_id: pet_id in hidden for pet_id in unique_ids}

    def set_pet_cover(self, pet_id: str, detection: PetDetectionRecord) -> bool:
        if not pet_id or not detection.detection_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO pet_covers (
                    pet_id, detection_id, pet_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(pet_id) DO UPDATE SET
                    detection_id = excluded.detection_id,
                    pet_key = excluded.pet_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    is_custom = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    pet_id,
                    detection.detection_id,
                    detection.pet_key,
                    detection.asset_id,
                    detection.thumbnail_path,
                    utc_now_iso(),
                ),
            )
            conn.commit()
        return True

    def get_pet_cover_thumbnail_map(self, pet_ids: Iterable[str]) -> dict[str, str]:
        unique_ids = [str(pet_id) for pet_id in dict.fromkeys(pet_ids) if pet_id]
        if not unique_ids:
            return {}
        self.initialize()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as conn:
            for chunk in _chunked(tuple(unique_ids), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT pet_id, thumbnail_path
                        FROM pet_covers
                        WHERE pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        return {
            str(row["pet_id"]): str(row["thumbnail_path"])
            for row in rows
            if row["pet_id"] and row["thumbnail_path"]
        }

    def get_cover_thumbnail_paths(self) -> set[str]:
        """Return thumbnail paths still retained by persisted cover choices."""

        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT thumbnail_path FROM pet_covers WHERE thumbnail_path IS NOT NULL"
            ).fetchall()
        return {str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]}

    def get_cover(self, pet_id: str) -> PetCoverRecord | None:
        if not pet_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM pet_covers WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
        if row is None:
            return None
        return PetCoverRecord(
            pet_id=str(row["pet_id"]),
            detection_id=str(row["detection_id"]) if row["detection_id"] else None,
            pet_key=str(row["pet_key"]) if row["pet_key"] else None,
            asset_id=str(row["asset_id"]) if row["asset_id"] else None,
            thumbnail_path=(
                str(row["thumbnail_path"]) if row["thumbnail_path"] else None
            ),
            is_custom=bool(row["is_custom"]),
        )

    def get_summary_state_maps(
        self,
    ) -> tuple[dict[str, bool], dict[str, str], dict[str, str | None]]:
        """Load dashboard state with a fixed query budget independent of pet count."""

        self.initialize()
        with closing(self._connect()) as conn:
            hidden_rows = conn.execute("SELECT pet_id FROM hidden_pets").fetchall()
            cover_rows = conn.execute(
                "SELECT pet_id, thumbnail_path FROM pet_covers"
            ).fetchall()
            profile_rows = conn.execute(
                "SELECT pet_id, name FROM pet_profiles"
            ).fetchall()
        hidden = {
            str(row["pet_id"]): True for row in hidden_rows if row["pet_id"]
        }
        covers = {
            str(row["pet_id"]): str(row["thumbnail_path"])
            for row in cover_rows
            if row["pet_id"] and row["thumbnail_path"]
        }
        names = {
            str(row["pet_id"]): row["name"]
            for row in profile_rows
            if row["pet_id"]
        }
        return hidden, covers, names

    def clear_cover_for_detection(self, detection_id: str) -> bool:
        """Remove a cover choice that points at a detection being deleted."""

        if not detection_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM pet_covers WHERE detection_id = ?",
                (detection_id,),
            )
            conn.commit()
        return int(cursor.rowcount or 0) > 0

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool:
        if not source_pet_id or not target_pet_id or source_pet_id == target_pet_id:
            return False
        self.initialize()
        timestamp = utc_now_iso()
        with closing(self._connect()) as conn:
            source = conn.execute(
                "SELECT pet_id, name FROM pet_profiles WHERE pet_id = ?",
                (source_pet_id,),
            ).fetchone()
            target = conn.execute(
                "SELECT pet_id, name FROM pet_profiles WHERE pet_id = ?",
                (target_pet_id,),
            ).fetchone()
            if source is None or target is None:
                return False
            redirects = {
                str(row["source_pet_id"]): str(row["target_pet_id"])
                for row in conn.execute(
                    "SELECT source_pet_id, target_pet_id FROM merge_redirects"
                ).fetchall()
            }
            if source_pet_id in redirects:
                return False
            cursor = target_pet_id
            visited = {source_pet_id}
            while cursor in redirects:
                if cursor in visited:
                    return False
                visited.add(cursor)
                cursor = redirects[cursor]
            if cursor != target_pet_id:
                return False
            hidden_map = self.get_pet_hidden_map((source_pet_id, target_pet_id))
            if bool(hidden_map.get(source_pet_id, False)) != bool(
                hidden_map.get(target_pet_id, False)
            ):
                return False
            if target["name"] is None and source["name"] is not None:
                conn.execute(
                    "UPDATE pet_profiles SET name = ?, updated_at = ? WHERE pet_id = ?",
                    (source["name"], timestamp, target_pet_id),
                )
            source_cover = conn.execute(
                "SELECT * FROM pet_covers WHERE pet_id = ?",
                (source_pet_id,),
            ).fetchone()
            target_cover = conn.execute(
                "SELECT is_custom FROM pet_covers WHERE pet_id = ?",
                (target_pet_id,),
            ).fetchone()
            if (
                source_cover is not None
                and int(source_cover["is_custom"] or 0) == 1
                and (target_cover is None or int(target_cover["is_custom"] or 0) == 0)
            ):
                conn.execute(
                    """
                    INSERT INTO pet_covers (
                        pet_id, detection_id, pet_key, asset_id, thumbnail_path,
                        is_custom, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        detection_id = excluded.detection_id,
                        pet_key = excluded.pet_key,
                        asset_id = excluded.asset_id,
                        thumbnail_path = excluded.thumbnail_path,
                        is_custom = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        target_pet_id,
                        source_cover["detection_id"],
                        source_cover["pet_key"],
                        source_cover["asset_id"],
                        source_cover["thumbnail_path"],
                        timestamp,
                    ),
                )
            conn.execute(
                """
                INSERT INTO merge_redirects (source_pet_id, target_pet_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_pet_id) DO UPDATE SET
                    target_pet_id = excluded.target_pet_id,
                    updated_at = excluded.updated_at
                """,
                (source_pet_id, target_pet_id, timestamp),
            )
            conn.execute(
                """
                UPDATE merge_redirects
                SET target_pet_id = ?, updated_at = ?
                WHERE target_pet_id = ? AND source_pet_id != ?
                """,
                (target_pet_id, timestamp, source_pet_id, source_pet_id),
            )
            conn.execute("DELETE FROM pet_covers WHERE pet_id = ?", (source_pet_id,))
            conn.execute("DELETE FROM hidden_pets WHERE pet_id = ?", (source_pet_id,))
            promotion_rows = conn.execute(
                """
                SELECT pet_id, evidence_asset_count, promotion_state
                FROM pet_identity_promotions
                WHERE pet_id IN (?, ?)
                """,
                (source_pet_id, target_pet_id),
            ).fetchall()
            promotions = {str(row["pet_id"]): row for row in promotion_rows}
            evidence_asset_count = max(
                (
                    int(row["evidence_asset_count"] or 0)
                    for row in promotion_rows
                ),
                default=0,
            )
            merged_state = merged_promotion_state(
                promotions.get(source_pet_id)["promotion_state"]
                if source_pet_id in promotions
                else None,
                promotions.get(target_pet_id)["promotion_state"]
                if target_pet_id in promotions
                else None,
                evidence_asset_count=evidence_asset_count,
                minimum_evidence=_PET_PROMOTION_MIN_ASSETS,
            )
            conn.execute(
                """
                INSERT INTO pet_identity_promotions (
                    pet_id, evidence_asset_count, promotion_state, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(pet_id) DO UPDATE SET
                    evidence_asset_count = excluded.evidence_asset_count,
                    promotion_state = excluded.promotion_state,
                    updated_at = excluded.updated_at
                """,
                (target_pet_id, evidence_asset_count, merged_state, timestamp),
            )
            conn.execute(
                "DELETE FROM pet_identity_promotions WHERE pet_id = ?",
                (source_pet_id,),
            )
            conn.commit()
        return True

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self._db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn, self._db_path, wal=True)
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        promotion_table_existed = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'pet_identity_promotions'"
            ).fetchone()
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_profiles (
                pet_id TEXT PRIMARY KEY,
                name TEXT,
                center_embedding BLOB,
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
            CREATE TABLE IF NOT EXISTS pet_keys (
                pet_key TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_identity_promotions (
                pet_id TEXT PRIMARY KEY,
                evidence_asset_count INTEGER NOT NULL DEFAULT 0,
                promotion_state TEXT NOT NULL DEFAULT 'candidate',
                updated_at TEXT NOT NULL
            )
            """
        )
        if not promotion_table_existed:
            conn.execute(
                """
                INSERT OR IGNORE INTO pet_identity_promotions (
                    pet_id, evidence_asset_count, promotion_state, updated_at
                )
                SELECT pet_id, sample_count, 'legacy_visible', updated_at
                FROM pet_profiles
                """
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_covers (
                pet_id TEXT PRIMARY KEY,
                detection_id TEXT,
                pet_key TEXT,
                asset_id TEXT,
                thumbnail_path TEXT,
                is_custom INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hidden_pets (
                pet_id TEXT PRIMARY KEY,
                hidden_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rejected_pet_keys (
                pet_key TEXT PRIMARY KEY,
                rejected_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merge_redirects (
                source_pet_id TEXT PRIMARY KEY,
                target_pet_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pet_keys_pet_id ON pet_keys (pet_id)")
        _ensure_column(
            conn,
            "pet_profiles",
            "embedding_pipeline_version",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn,
            "pet_profiles",
            "generation_id",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(conn, "pet_profiles", "boundary_embeddings", "BLOB")
        _ensure_column(
            conn,
            "pet_profiles",
            "boundary_sample_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        conn.commit()


def _normalize_species_label(value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip().lower()
    return label or None


def _canonical_redirect_map(redirects: dict[str, str]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for source in redirects:
        cursor = source
        visited: set[str] = set()
        while cursor in redirects:
            if cursor in visited:
                break
            visited.add(cursor)
            cursor = redirects[cursor]
        if cursor not in visited and cursor != source:
            canonical[source] = cursor
    return canonical


def _chunked(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _profile_from_row(row: sqlite3.Row) -> PetProfile:
    sample_count = int(row["sample_count"] or 0)
    evidence_asset_count = int(
        row["evidence_asset_count"] or 0
        if "evidence_asset_count" in row.keys()
        else sample_count
    )
    return PetProfile(
        pet_id=str(row["pet_id"]),
        name=row["name"],
        center_embedding=deserialize_embedding(
            row["center_embedding"], int(row["embedding_dim"] or 0)
        ),
        embedding_dim=int(row["embedding_dim"] or 0),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        sample_count=sample_count,
        profile_state=profile_state_for_sample_count(evidence_asset_count),
        species_label=_normalize_species_label(row["species_label"]),
        embedding_pipeline_version=str(row["embedding_pipeline_version"] or ""),
        generation_id=int(row["generation_id"] or 0),
        boundary_embeddings=_deserialize_boundary_embeddings(
            row["boundary_embeddings"],
            embedding_dim=int(row["embedding_dim"] or 0),
            sample_count=int(row["boundary_sample_count"] or 0),
        ),
        evidence_asset_count=evidence_asset_count,
        promotion_state=(
            normalize_promotion_state(row["promotion_state"])
            if "promotion_state" in row.keys() and row["promotion_state"] is not None
            else PROMOTION_LEGACY_VISIBLE
        ),
    )


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _serialize_boundary_embeddings(
    embeddings: tuple[np.ndarray, ...],
    embedding_dim: int,
) -> sqlite3.Binary | None:
    selected = [
        np.asarray(embedding, dtype=np.float32).reshape(-1)
        for embedding in embeddings[:8]
        if int(np.asarray(embedding).size) == int(embedding_dim)
    ]
    if not selected:
        return None
    return sqlite3.Binary(np.stack(selected, axis=0).tobytes())


def _deserialize_boundary_embeddings(
    blob: bytes | None,
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

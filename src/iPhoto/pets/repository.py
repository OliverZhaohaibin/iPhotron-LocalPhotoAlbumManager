"""SQLite pet index repository for Pets clusters."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .records import AssetPetAnnotation, PetDetectionRecord, PetRecord, PetSummary
from .repository_utils import (
    deserialize_embedding,
    normalize_name,
    profile_state_for_sample_count,
    serialize_embedding,
    utc_now_iso,
)
from .state_repository import PetStateRepository


@dataclass(frozen=True)
class PetMutationResult:
    changed_asset_ids: tuple[str, ...] = ()
    changed_pet_ids: tuple[str, ...] = ()
    pet_redirects: dict[str, str] = field(default_factory=dict)


class PetRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = PetStateRepository(state_db_path) if state_db_path is not None else None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> PetStateRepository | None:
        return self._state_repo

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)
        if self._state_repo is not None:
            self._state_repo.initialize()

    def replace_all(
        self,
        detections: list[PetDetectionRecord],
        pets: list[PetRecord],
        *,
        sync_runtime_state: bool = True,
    ) -> None:
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
            conn.execute("DELETE FROM pets")
            conn.execute("DELETE FROM pet_detections")
            conn.executemany(
                """
                INSERT INTO pet_detections (
                    detection_id, pet_key, asset_id, asset_rel, species_label,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, quality_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._detection_to_row(detection) for detection in detections],
            )
            conn.executemany(
                """
                INSERT INTO pets (
                    pet_id, name, species_label, key_detection_id, detection_count,
                    center_embedding, embedding_dim, created_at, updated_at,
                    sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        pet.pet_id,
                        normalize_name(pet.name),
                        pet.species_label,
                        pet.key_detection_id,
                        pet.detection_count,
                        serialize_embedding(pet.center_embedding),
                        pet.embedding_dim,
                        pet.created_at,
                        pet.updated_at,
                        max(int(pet.sample_count), int(pet.detection_count)),
                        profile_state_for_sample_count(
                            max(int(pet.sample_count), int(pet.detection_count))
                        ),
                    )
                    for pet in pets
                ],
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

    def sync_runtime_state(self) -> None:
        if self._state_repo is None:
            return
        self._state_repo.sync_scan_results(self.get_all_pet_records(), self.get_all_detections())

    def get_all_detections(self) -> list[PetDetectionRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    detection_id, pet_key, asset_id, asset_rel, species_label,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, quality_score
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

    def get_all_pet_records(self) -> list[PetRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    pet_id, name, species_label, key_detection_id, detection_count,
                    center_embedding, embedding_dim, created_at, updated_at,
                    sample_count, profile_state
                FROM pets
                ORDER BY detection_count DESC, created_at ASC, pet_id ASC
                """
            ).fetchall()
        return [self._pet_from_row(row) for row in rows]

    def get_pet_summaries(self, *, include_hidden: bool = False) -> list[PetSummary]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    pets.pet_id,
                    pets.name,
                    pets.species_label,
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
            thumbnail_path = cover_paths.get(pet_id) or row["thumbnail_path"]
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / str(thumbnail_path)).resolve()
            name = row["name"] if row["name"] is not None else profile_names.get(pet_id)
            summaries.append(
                PetSummary(
                    pet_id=pet_id,
                    name=name,
                    species_label=str(row["species_label"] or ""),
                    key_detection_id=str(row["key_detection_id"] or ""),
                    detection_count=int(row["detection_count"] or 0),
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(row["created_at"] or ""),
                    is_hidden=bool(hidden_map.get(pet_id, False)),
                )
            )
        if not include_hidden:
            summaries = [summary for summary in summaries if not summary.is_hidden]
        return summaries

    def get_asset_ids_by_pet(self, pet_id: str) -> list[str]:
        if not pet_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT asset_id
                FROM pet_detections
                WHERE pet_id = ?
                ORDER BY asset_id ASC
                """,
                (pet_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def list_asset_pet_annotations(self, asset_id: str) -> list[AssetPetAnnotation]:
        if not asset_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    detection_id, pet_id, species_label, box_x, box_y, box_w, box_h,
                    image_width, image_height, thumbnail_path
                FROM pet_detections
                WHERE asset_id = ?
                ORDER BY box_x ASC, box_y ASC, detection_id ASC
                """,
                (asset_id,),
            ).fetchall()
        names = {}
        if self._state_repo is not None:
            names = self._state_repo.get_profile_name_map(
                str(row["pet_id"]) for row in rows if row["pet_id"]
            )
        annotations: list[AssetPetAnnotation] = []
        for row in rows:
            thumbnail_path = row["thumbnail_path"]
            annotations.append(
                AssetPetAnnotation(
                    detection_id=str(row["detection_id"]),
                    pet_id=str(row["pet_id"]) if row["pet_id"] else None,
                    display_name=names.get(str(row["pet_id"])) if row["pet_id"] else None,
                    species_label=str(row["species_label"] or ""),
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
                )
            )
        return annotations

    def get_detection(self, detection_id: str) -> PetDetectionRecord | None:
        if not detection_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    detection_id, pet_key, asset_id, asset_rel, species_label,
                    box_x, box_y, box_w, box_h, confidence, embedding, embedding_dim,
                    embedding_model, detector_model, thumbnail_path, pet_id, detected_at,
                    image_width, image_height, quality_score
                FROM pet_detections
                WHERE detection_id = ?
                """,
                (detection_id,),
            ).fetchone()
        return self._detection_from_row(row) if row is not None else None

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> bool:
        if self._state_repo is None or not pet_id:
            return False
        self._state_repo.rename_pet(pet_id, name_or_none)
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pets SET name = ?, updated_at = ? WHERE pet_id = ?",
                (normalize_name(name_or_none), utc_now_iso(), pet_id),
            )
            conn.commit()
        return True

    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool:
        if self._state_repo is None:
            return False
        return self._state_repo.set_pet_hidden(pet_id, hidden)

    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool:
        if self._state_repo is None:
            return False
        detection = self.get_detection(detection_id)
        if detection is None:
            return False
        return self._state_repo.set_pet_cover(pet_id, detection)

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> PetMutationResult | None:
        if self._state_repo is None:
            return None
        if not self._state_repo.merge_pets(source_pet_id, target_pet_id):
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
        self._rebuild_pet_records_from_detections()
        return PetMutationResult(
            changed_asset_ids=tuple(str(row["asset_id"]) for row in asset_rows if row["asset_id"]),
            changed_pet_ids=(source_pet_id, target_pet_id),
            pet_redirects={source_pet_id: target_pet_id},
        )

    def delete_detection(self, detection_id: str) -> PetMutationResult | None:
        detection = self.get_detection(detection_id)
        if detection is None:
            return None
        if self._state_repo is not None:
            self._state_repo.add_rejected_pet_key(detection.pet_key)
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM pet_detections WHERE detection_id = ?", (detection_id,))
            conn.commit()
        self._rebuild_pet_records_from_detections()
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
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_detections (
                detection_id TEXT PRIMARY KEY,
                pet_key TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                species_label TEXT NOT NULL,
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
                quality_score REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pets (
                pet_id TEXT PRIMARY KEY,
                name TEXT,
                species_label TEXT NOT NULL,
                key_detection_id TEXT NOT NULL,
                detection_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                profile_state TEXT DEFAULT 'unstable'
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
        conn.commit()

    def _detection_to_row(self, detection: PetDetectionRecord) -> tuple:
        return (
            detection.detection_id,
            detection.pet_key,
            detection.asset_id,
            detection.asset_rel,
            detection.species_label,
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
            detection.quality_score,
        )

    def _detection_from_row(self, row: sqlite3.Row) -> PetDetectionRecord:
        return PetDetectionRecord(
            detection_id=str(row["detection_id"]),
            pet_key=str(row["pet_key"]),
            asset_id=str(row["asset_id"]),
            asset_rel=str(row["asset_rel"]),
            species_label=str(row["species_label"]),
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
            quality_score=(
                float(row["quality_score"]) if row["quality_score"] is not None else None
            ),
        )

    def _pet_from_row(self, row: sqlite3.Row) -> PetRecord:
        return PetRecord(
            pet_id=str(row["pet_id"]),
            name=row["name"],
            species_label=str(row["species_label"]),
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
        )

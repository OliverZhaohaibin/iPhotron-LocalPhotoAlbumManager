"""SQLite state repository for persisted pet user decisions."""

# ruff: noqa: S608

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite

from .records import PetDetectionRecord, PetProfile, PetRecord
from .repository_utils import (
    deserialize_embedding,
    normalize_name,
    profile_state_for_sample_count,
    serialize_embedding,
    utc_now_iso,
)


@dataclass(frozen=True)
class PetCoverRecord:
    pet_id: str
    detection_id: str | None
    pet_key: str | None
    asset_id: str | None
    thumbnail_path: str | None
    is_custom: bool


class PetStateRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)

    def get_profiles(self) -> list[PetProfile]:
        self.initialize()
        with closing(self._connect()) as conn:
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
                    pet_id, name, species_label, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                FROM pet_profiles
                """
            ).fetchall()
        profiles: list[PetProfile] = []
        for row in rows:
            pet_id = str(row["pet_id"])
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                sample_count = inferred_counts.get(pet_id, 0)
            profiles.append(
                PetProfile(
                    pet_id=pet_id,
                    name=row["name"],
                    species_label=str(row["species_label"] or ""),
                    center_embedding=deserialize_embedding(
                        row["center_embedding"],
                        int(row["embedding_dim"] or 0),
                    ),
                    embedding_dim=int(row["embedding_dim"] or 0),
                    created_at=str(row["created_at"] or ""),
                    updated_at=str(row["updated_at"] or ""),
                    sample_count=sample_count,
                    profile_state=profile_state_for_sample_count(sample_count),
                )
            )
        return profiles

    def get_profile(self, pet_id: str) -> PetProfile | None:
        if not pet_id:
            return None
        profiles = [profile for profile in self.get_profiles() if profile.pet_id == pet_id]
        return profiles[0] if profiles else None

    def get_profile_name_map(self, pet_ids: Iterable[str]) -> dict[str, str | None]:
        unique_ids = [str(pet_id) for pet_id in dict.fromkeys(pet_ids) if pet_id]
        if not unique_ids:
            return {}
        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT pet_id, name
                FROM pet_profiles
                WHERE pet_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {str(row["pet_id"]): row["name"] for row in rows if row["pet_id"]}

    def get_pet_key_map(self, pet_keys: Iterable[str]) -> dict[str, str]:
        unique_keys = [str(key) for key in dict.fromkeys(pet_keys) if key]
        if not unique_keys:
            return {}
        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_keys))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT pet_key, pet_id
                FROM pet_keys
                WHERE pet_key IN ({placeholders})
                """,
                unique_keys,
            ).fetchall()
        return {str(row["pet_key"]): str(row["pet_id"]) for row in rows if row["pet_key"]}

    def get_rejected_pet_keys(self, pet_keys: Iterable[str]) -> set[str]:
        unique_keys = [str(key) for key in dict.fromkeys(pet_keys) if key]
        if not unique_keys:
            return set()
        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_keys))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT pet_key
                FROM rejected_pet_keys
                WHERE pet_key IN ({placeholders})
                """,
                unique_keys,
            ).fetchall()
        return {str(row["pet_key"]) for row in rows if row["pet_key"]}

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

    def sync_scan_results(
        self,
        pets: list[PetRecord],
        detections: list[PetDetectionRecord],
    ) -> None:
        self.initialize()
        names = self.get_profile_name_map(pet.pet_id for pet in pets)
        timestamp = utc_now_iso()
        detection_by_id = {detection.detection_id: detection for detection in detections}
        with closing(self._connect()) as conn:
            for pet in pets:
                sample_count = max(int(pet.sample_count), int(pet.detection_count))
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
                        pet_id, name, species_label, center_embedding, embedding_dim,
                        created_at, updated_at, sample_count, profile_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pet_id) DO UPDATE SET
                        name = COALESCE(pet_profiles.name, excluded.name),
                        species_label = excluded.species_label,
                        center_embedding = excluded.center_embedding,
                        embedding_dim = excluded.embedding_dim,
                        updated_at = excluded.updated_at,
                        sample_count = excluded.sample_count,
                        profile_state = excluded.profile_state
                    """,
                    (
                        pet.pet_id,
                        normalize_name(name),
                        pet.species_label,
                        serialize_embedding(pet.center_embedding),
                        pet.embedding_dim,
                        created_at,
                        timestamp,
                        sample_count,
                        profile_state_for_sample_count(sample_count),
                    ),
                )

            for detection in detections:
                if not detection.pet_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO pet_keys (pet_key, pet_id, species_label, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(pet_key) DO UPDATE SET
                        pet_id = excluded.pet_id,
                        species_label = excluded.species_label,
                        updated_at = excluded.updated_at
                    """,
                    (detection.pet_key, detection.pet_id, detection.species_label, timestamp),
                )

            for pet in pets:
                key_detection = detection_by_id.get(pet.key_detection_id)
                if key_detection is None:
                    continue
                custom = conn.execute(
                    "SELECT is_custom FROM pet_covers WHERE pet_id = ?",
                    (pet.pet_id,),
                ).fetchone()
                if custom is not None and int(custom["is_custom"] or 0) == 1:
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
            conn.commit()

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
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT pet_id
                FROM hidden_pets
                WHERE pet_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
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
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT pet_id, thumbnail_path
                FROM pet_covers
                WHERE pet_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {
            str(row["pet_id"]): str(row["thumbnail_path"])
            for row in rows
            if row["pet_id"] and row["thumbnail_path"]
        }

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool:
        if not source_pet_id or not target_pet_id or source_pet_id == target_pet_id:
            return False
        self.initialize()
        timestamp = utc_now_iso()
        with closing(self._connect()) as conn:
            source = conn.execute(
                "SELECT pet_id FROM pet_profiles WHERE pet_id = ?",
                (source_pet_id,),
            ).fetchone()
            target = conn.execute(
                "SELECT pet_id FROM pet_profiles WHERE pet_id = ?",
                (target_pet_id,),
            ).fetchone()
            if source is None or target is None:
                return False
            conn.execute(
                "UPDATE pet_keys SET pet_id = ?, updated_at = ? WHERE pet_id = ?",
                (target_pet_id, timestamp, source_pet_id),
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
            conn.execute("DELETE FROM pet_profiles WHERE pet_id = ?", (source_pet_id,))
            conn.execute("DELETE FROM pet_covers WHERE pet_id = ?", (source_pet_id,))
            conn.execute("DELETE FROM hidden_pets WHERE pet_id = ?", (source_pet_id,))
            conn.commit()
        return True

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self._db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn, self._db_path, wal=True)
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_profiles (
                pet_id TEXT PRIMARY KEY,
                name TEXT,
                species_label TEXT NOT NULL,
                center_embedding BLOB,
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
            CREATE TABLE IF NOT EXISTS pet_keys (
                pet_key TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                species_label TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
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
        conn.commit()

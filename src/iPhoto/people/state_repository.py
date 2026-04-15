"""SQLite state repository for persisted People user state."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

import numpy as np

from .records import FaceRecord, PeopleGroupRecord, PersonProfile, PersonRecord
from .repository_utils import (
    _deserialize_embedding,
    _group_id_for_member_key,
    _group_member_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
)


class FaceStateRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)

    def get_profiles(self) -> list[PersonProfile]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT person_id, name, center_embedding, embedding_dim, created_at, updated_at
                FROM person_profiles
                """).fetchall()
        return [
            PersonProfile(
                person_id=row["person_id"],
                name=row["name"],
                center_embedding=_deserialize_embedding(
                    row["center_embedding"],
                    int(row["embedding_dim"]),
                ),
                embedding_dim=int(row["embedding_dim"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_face_key_map(self, face_keys: Iterable[str]) -> dict[str, str]:
        unique_face_keys = [face_key for face_key in dict.fromkeys(face_keys) if face_key]
        if not unique_face_keys:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_face_keys))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT face_key, person_id
                FROM face_keys
                WHERE face_key IN ({placeholders})
                """,
                unique_face_keys,
            ).fetchall()
        return {row["face_key"]: row["person_id"] for row in rows}

    def sync_scan_results(self, persons: list[PersonRecord], faces: list[FaceRecord]) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = COALESCE(excluded.name, person_profiles.name),
                    center_embedding = excluded.center_embedding,
                    embedding_dim = excluded.embedding_dim,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        person.person_id,
                        _normalize_name(person.name),
                        _serialize_embedding(person.center_embedding),
                        int(person.center_embedding.shape[0]),
                        person.created_at,
                        person.updated_at,
                    )
                    for person in persons
                ],
            )
            timestamp = _utc_now_iso()
            conn.executemany(
                """
                INSERT INTO face_keys (
                    face_key, person_id, asset_id, asset_rel, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(face_key) DO UPDATE SET
                    person_id = excluded.person_id,
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        face.face_key,
                        face.person_id,
                        face.asset_id,
                        face.asset_rel,
                        timestamp,
                    )
                    for face in faces
                    if face.face_key and face.person_id
                ],
            )
            conn.commit()

    def rename_person(self, person_id: str, name_or_none: str | None) -> None:
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (
                    person_id,
                    _normalize_name(name_or_none),
                    sqlite3.Binary(b""),
                    0,
                    updated_at,
                    updated_at,
                ),
            )
            conn.commit()

    def merge_persons(
        self,
        source_person_id: str,
        target_person_id: str,
        *,
        center_embedding: np.ndarray,
        target_name: str | None,
        target_created_at: str,
    ) -> None:
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = COALESCE(excluded.name, person_profiles.name),
                    center_embedding = excluded.center_embedding,
                    embedding_dim = excluded.embedding_dim,
                    updated_at = excluded.updated_at
                """,
                (
                    target_person_id,
                    _normalize_name(target_name),
                    _serialize_embedding(center_embedding),
                    int(center_embedding.shape[0]),
                    target_created_at,
                    updated_at,
                ),
            )
            conn.execute(
                "UPDATE face_keys SET person_id = ?, updated_at = ? WHERE person_id = ?",
                (target_person_id, updated_at, source_person_id),
            )
            conn.execute("DELETE FROM person_profiles WHERE person_id = ?", (source_person_id,))
            conn.commit()

    def create_group(self, member_person_ids: Iterable[str]) -> PeopleGroupRecord | None:
        members = _unique_person_ids(member_person_ids)
        if len(members) < 2:
            return None

        self.initialize()
        member_key = _group_member_key(members)
        timestamp = _utc_now_iso()
        group_id = _group_id_for_member_key(member_key)
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT group_id FROM people_groups WHERE member_key = ?",
                (member_key,),
            ).fetchone()
            if existing is not None:
                return self._group_from_id(conn, str(existing["group_id"]))

            conn.execute(
                """
                INSERT INTO people_groups (group_id, member_key, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (group_id, member_key, timestamp, timestamp),
            )
            conn.executemany(
                """
                INSERT INTO people_group_members (group_id, person_id, position)
                VALUES (?, ?, ?)
                """,
                [(group_id, person_id, index) for index, person_id in enumerate(members)],
            )
            conn.commit()
            return self._group_from_id(conn, group_id)

    def list_groups(self) -> list[PeopleGroupRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT group_id
                FROM people_groups
                ORDER BY created_at ASC, group_id ASC
                """).fetchall()
            groups: list[PeopleGroupRecord] = []
            for row in rows:
                group = self._group_from_id(conn, str(row["group_id"]))
                if group is not None:
                    groups.append(group)
            return groups

    def get_group(self, group_id: str) -> PeopleGroupRecord | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            return self._group_from_id(conn, group_id)

    def has_group_asset_cache(self, group_id: str) -> bool:
        if not group_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM people_group_asset_cache WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return row is not None

    def get_group_asset_ids(self, group_id: str) -> list[str]:
        if not group_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT asset_id
                FROM people_group_assets
                WHERE group_id = ?
                ORDER BY position ASC, asset_id ASC
                """,
                (group_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def replace_group_assets(
        self,
        group_id: str,
        asset_rows: Iterable[tuple[str, str]],
    ) -> None:
        if not group_id:
            return

        rows = [
            (str(asset_id), str(last_detected_at), index)
            for index, (asset_id, last_detected_at) in enumerate(asset_rows)
            if asset_id
        ]
        self.initialize()
        timestamp = _utc_now_iso()
        cover_asset_id = rows[0][0] if rows else None
        with closing(self._connect()) as conn:
            group_exists = conn.execute(
                "SELECT 1 FROM people_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if group_exists is None:
                return

            conn.execute("DELETE FROM people_group_assets WHERE group_id = ?", (group_id,))
            conn.executemany(
                """
                INSERT INTO people_group_assets (
                    group_id, asset_id, position, last_detected_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (group_id, asset_id, position, last_detected_at)
                    for asset_id, last_detected_at, position in rows
                ],
            )
            conn.execute(
                """
                INSERT INTO people_group_asset_cache (
                    group_id, asset_count, cover_asset_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    asset_count = excluded.asset_count,
                    cover_asset_id = excluded.cover_asset_id,
                    updated_at = excluded.updated_at
                """,
                (group_id, len(rows), cover_asset_id, timestamp),
            )
            conn.commit()

    @staticmethod
    def _group_from_id(
        conn: sqlite3.Connection,
        group_id: str,
    ) -> PeopleGroupRecord | None:
        row = conn.execute(
            """
            SELECT group_id, member_key, created_at, updated_at
            FROM people_groups
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()
        if row is None:
            return None

        members = conn.execute(
            """
            SELECT person_id
            FROM people_group_members
            WHERE group_id = ?
            ORDER BY position ASC, person_id ASC
            """,
            (group_id,),
        ).fetchall()
        member_person_ids = tuple(str(member["person_id"]) for member in members)
        if len(member_person_ids) < 2:
            return None
        return PeopleGroupRecord(
            group_id=str(row["group_id"]),
            member_person_ids=member_person_ids,
            member_key=str(row["member_key"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                center_embedding BLOB,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS face_keys (
                face_key TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_person_id ON face_keys(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_asset_id ON face_keys(asset_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_groups (
                group_id TEXT PRIMARY KEY,
                member_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_members (
                group_id TEXT NOT NULL REFERENCES people_groups(group_id) ON DELETE CASCADE,
                person_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (group_id, person_id)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_members_group_id "
            "ON people_group_members(group_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_members_person_id "
            "ON people_group_members(person_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_asset_cache (
                group_id TEXT PRIMARY KEY REFERENCES people_groups(group_id) ON DELETE CASCADE,
                asset_count INTEGER NOT NULL,
                cover_asset_id TEXT,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_assets (
                group_id TEXT NOT NULL REFERENCES people_groups(group_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                last_detected_at TEXT NOT NULL,
                PRIMARY KEY (group_id, asset_id)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_assets_group_id "
            "ON people_group_assets(group_id)"
        )

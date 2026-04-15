"""SQLite face index repository for People clusters."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

import numpy as np

from .records import FaceRecord, PeopleGroupRecord, PersonRecord, PersonSummary
from .repository_utils import (
    _deserialize_embedding,
    _key_face_sort_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    compute_cluster_center,
)
from .state_repository import FaceStateRepository


class FaceRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = FaceStateRepository(state_db_path) if state_db_path is not None else None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> FaceStateRepository | None:
        return self._state_repo

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)
        if self._state_repo is not None:
            self._state_repo.initialize()

    def replace_all(self, faces: list[FaceRecord], persons: list[PersonRecord]) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM persons")
            conn.execute("DELETE FROM faces")
            conn.executemany(
                """
                INSERT INTO faces (
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        face.face_id,
                        face.face_key,
                        face.asset_id,
                        face.asset_rel,
                        face.box_x,
                        face.box_y,
                        face.box_w,
                        face.box_h,
                        face.confidence,
                        _serialize_embedding(face.embedding),
                        face.embedding_dim,
                        face.thumbnail_path,
                        face.person_id,
                        face.detected_at,
                        face.image_width,
                        face.image_height,
                    )
                    for face in faces
                ],
            )
            conn.executemany(
                """
                INSERT INTO persons (
                    person_id, name, key_face_id, face_count, center_embedding, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        person.person_id,
                        _normalize_name(person.name),
                        person.key_face_id,
                        person.face_count,
                        _serialize_embedding(person.center_embedding),
                        person.created_at,
                        person.updated_at,
                    )
                    for person in persons
                ],
            )
            conn.commit()

    def get_all_faces(self) -> list[FaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                ORDER BY detected_at ASC, face_id ASC
                """).fetchall()
        return [self._face_from_row(row) for row in rows]

    def remove_faces_for_assets(
        self,
        asset_ids: Iterable[str],
        asset_rels: Iterable[str] = (),
    ) -> None:
        self.initialize()
        ids_list = [str(value) for value in asset_ids if value]
        rels_list = [str(value) for value in asset_rels if value]
        clauses: list[str] = []
        params: list[str] = []
        if ids_list:
            placeholders = ", ".join(["?"] * len(ids_list))
            clauses.append(f"asset_id IN ({placeholders})")
            params.extend(ids_list)
        if rels_list:
            placeholders = ", ".join(["?"] * len(rels_list))
            clauses.append(f"asset_rel IN ({placeholders})")
            params.extend(rels_list)
        if not clauses:
            return

        with closing(self._connect()) as conn:
            matched_faces = conn.execute(
                f"""
                SELECT face_id, person_id
                FROM faces
                WHERE {' OR '.join(clauses)}
                """,
                params,
            ).fetchall()
            if not matched_faces:
                return

            affected_person_ids = [
                str(row["person_id"]) for row in matched_faces if row["person_id"]
            ]
            if affected_person_ids:
                placeholders = ", ".join(["?"] * len(affected_person_ids))
                # Runtime person rows are fully rebuilt after rescans, so remove
                # affected clusters first to avoid dangling key_face_id references.
                conn.execute(
                    f"DELETE FROM persons WHERE person_id IN ({placeholders})",
                    affected_person_ids,
                )

            face_ids = [str(row["face_id"]) for row in matched_faces if row["face_id"]]
            placeholders = ", ".join(["?"] * len(face_ids))
            conn.execute(
                f"DELETE FROM faces WHERE face_id IN ({placeholders})",
                face_ids,
            )
            orphaned = {row[0] for row in conn.execute("""
                    SELECT person_id
                    FROM persons
                    WHERE person_id NOT IN (
                        SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL
                    )
                    """).fetchall()}
            if orphaned:
                placeholders = ", ".join(["?"] * len(orphaned))
                conn.execute(
                    f"DELETE FROM persons WHERE person_id IN ({placeholders})", list(orphaned)
                )
            conn.commit()

    def get_person_summaries(self) -> list[PersonSummary]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    persons.person_id,
                    persons.name,
                    persons.key_face_id,
                    persons.face_count,
                    persons.created_at,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.face_count DESC, persons.created_at ASC
                """).fetchall()
        summaries: list[PersonSummary] = []
        for row in rows:
            thumbnail_path = row["thumbnail_path"]
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / thumbnail_path).resolve()
            summaries.append(
                PersonSummary(
                    person_id=row["person_id"],
                    name=row["name"],
                    key_face_id=row["key_face_id"],
                    face_count=int(row["face_count"]),
                    thumbnail_path=resolved_thumbnail,
                    created_at=row["created_at"],
                )
            )
        return summaries

    def get_asset_ids_by_person(self, person_id: str) -> list[str]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT asset_id, MAX(detected_at) AS last_detected_at
                FROM faces
                WHERE person_id = ?
                GROUP BY asset_id
                ORDER BY last_detected_at DESC, asset_id ASC
                """,
                (person_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def rename_person(self, person_id: str, name_or_none: str | None) -> None:
        self.initialize()
        normalized_name = _normalize_name(name_or_none)
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE persons SET name = ?, updated_at = ? WHERE person_id = ?",
                (normalized_name, updated_at, person_id),
            )
            conn.commit()
        if self._state_repo is not None:
            self._state_repo.rename_person(person_id, normalized_name)

    def merge_persons(self, source_person_id: str, target_person_id: str) -> bool:
        if not source_person_id or not target_person_id or source_person_id == target_person_id:
            return False

        self.initialize()
        with closing(self._connect()) as conn:
            faces = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE person_id IN (?, ?)
                ORDER BY detected_at ASC, face_id ASC
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            if not faces:
                return False

            source_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == source_person_id
            ]
            target_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == target_person_id
            ]
            if not source_faces or not target_faces:
                return False

            person_rows = conn.execute(
                """
                SELECT person_id, name, created_at
                FROM persons WHERE person_id IN (?, ?)
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            person_map = {row["person_id"]: row for row in person_rows}
            target_person = person_map.get(target_person_id)
            source_person = person_map.get(source_person_id)
            target_name = None
            target_created_at = _utc_now_iso()
            if target_person is not None:
                target_name = target_person["name"]
                target_created_at = target_person["created_at"]
            elif source_person is not None:
                target_name = source_person["name"]
                target_created_at = source_person["created_at"]

            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )

            merged_faces = [
                FaceRecord(**{**face.__dict__, "person_id": target_person_id})
                for face in (target_faces + source_faces)
            ]
            key_face = max(merged_faces, key=_key_face_sort_key)
            center_embedding = compute_cluster_center(
                np.stack([face.embedding for face in merged_faces], axis=0)
            )
            updated_at = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO persons (
                    person_id, name, key_face_id, face_count, center_embedding, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = excluded.name,
                    key_face_id = excluded.key_face_id,
                    face_count = excluded.face_count,
                    center_embedding = excluded.center_embedding,
                    updated_at = excluded.updated_at
                """,
                (
                    target_person_id,
                    _normalize_name(target_name),
                    key_face.face_id,
                    len(merged_faces),
                    _serialize_embedding(center_embedding),
                    target_created_at,
                    updated_at,
                ),
            )
            conn.execute("DELETE FROM persons WHERE person_id = ?", (source_person_id,))
            conn.commit()

        if self._state_repo is not None:
            self._state_repo.merge_persons(
                source_person_id,
                target_person_id,
                center_embedding=center_embedding,
                target_name=target_name,
                target_created_at=target_created_at,
            )
        return True

    def create_group(self, member_person_ids: Iterable[str]) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        return self._state_repo.create_group(member_person_ids)

    def list_groups(self) -> list[PeopleGroupRecord]:
        if self._state_repo is None:
            return []
        self.initialize()
        return self._state_repo.list_groups()

    def get_group(self, group_id: str) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        return self._state_repo.get_group(group_id)

    def get_common_asset_ids_for_persons(self, member_person_ids: Iterable[str]) -> list[str]:
        members = _unique_person_ids(member_person_ids)
        if len(members) < 2:
            return []

        self.initialize()
        placeholders = ", ".join(["?"] * len(members))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT asset_id, MAX(detected_at) AS last_detected_at
                FROM faces
                WHERE person_id IN ({placeholders})
                GROUP BY asset_id
                HAVING COUNT(DISTINCT person_id) = ?
                ORDER BY last_detected_at DESC, asset_id ASC
                """,
                [*members, len(members)],
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def get_common_asset_ids_for_group(self, group_id: str) -> list[str]:
        group = self.get_group(group_id)
        if group is None:
            return []
        return self.get_common_asset_ids_for_persons(group.member_person_ids)

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
            CREATE TABLE IF NOT EXISTS faces (
                face_id TEXT PRIMARY KEY,
                face_key TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                box_x INTEGER NOT NULL,
                box_y INTEGER NOT NULL,
                box_w INTEGER NOT NULL,
                box_h INTEGER NOT NULL,
                confidence REAL NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                thumbnail_path TEXT,
                person_id TEXT,
                detected_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                key_face_id TEXT NOT NULL REFERENCES faces(face_id),
                face_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_face_key ON faces(face_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_asset_id ON faces(asset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_asset_rel ON faces(asset_rel)")

    @staticmethod
    def _face_from_row(row: sqlite3.Row) -> FaceRecord:
        return FaceRecord(
            face_id=row["face_id"],
            face_key=row["face_key"],
            asset_id=row["asset_id"],
            asset_rel=row["asset_rel"],
            box_x=int(row["box_x"]),
            box_y=int(row["box_y"]),
            box_w=int(row["box_w"]),
            box_h=int(row["box_h"]),
            confidence=float(row["confidence"]),
            embedding=_deserialize_embedding(row["embedding"], int(row["embedding_dim"])),
            embedding_dim=int(row["embedding_dim"]),
            thumbnail_path=row["thumbnail_path"],
            person_id=row["person_id"],
            detected_at=row["detected_at"],
            image_width=int(row["image_width"]),
            image_height=int(row["image_height"]),
        )

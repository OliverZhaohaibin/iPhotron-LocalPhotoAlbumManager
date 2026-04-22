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
    profile_state_for_sample_count,
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
            inferred_sample_counts = {
                str(row["person_id"]): int(row["sample_count"] or 0)
                for row in conn.execute(
                    """
                    SELECT person_id, COUNT(*) AS sample_count
                    FROM face_keys
                    GROUP BY person_id
                    """
                ).fetchall()
            }
            rows = conn.execute("""
                SELECT
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at,
                    sample_count,
                    profile_state
                FROM person_profiles
                """).fetchall()
        profiles: list[PersonProfile] = []
        for row in rows:
            person_id = str(row["person_id"])
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                sample_count = inferred_sample_counts.get(person_id, 0)
            profiles.append(
                PersonProfile(
                    person_id=person_id,
                    name=row["name"],
                    center_embedding=_deserialize_embedding(
                        row["center_embedding"],
                        int(row["embedding_dim"]),
                    ),
                    embedding_dim=int(row["embedding_dim"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    sample_count=sample_count,
                    profile_state=profile_state_for_sample_count(sample_count),
                )
            )
        return profiles

    def get_manual_faces(self) -> list[FaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height, is_manual
                FROM manual_faces
                ORDER BY detected_at ASC, face_id ASC
                """
            ).fetchall()
        return [self._manual_face_from_row(row) for row in rows]

    def upsert_manual_face(self, face: FaceRecord) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO manual_faces (
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height, is_manual
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(face_id) DO UPDATE SET
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    box_x = excluded.box_x,
                    box_y = excluded.box_y,
                    box_w = excluded.box_w,
                    box_h = excluded.box_h,
                    confidence = excluded.confidence,
                    embedding = excluded.embedding,
                    embedding_dim = excluded.embedding_dim,
                    thumbnail_path = excluded.thumbnail_path,
                    person_id = excluded.person_id,
                    detected_at = excluded.detected_at,
                    image_width = excluded.image_width,
                    image_height = excluded.image_height,
                    is_manual = excluded.is_manual
                """,
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
                    1 if face.is_manual else 0,
                ),
            )
            conn.commit()

    def delete_manual_face(self, face_id: str) -> None:
        if not face_id:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM manual_faces WHERE face_id = ?", (face_id,))
            conn.commit()

    def get_person_order_map(self, person_ids: Iterable[str]) -> dict[str, int]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id, sort_order
                FROM person_card_orders
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {
            str(row["person_id"]): int(row["sort_order"])
            for row in rows
            if row["person_id"] is not None and row["sort_order"] is not None
        }

    def set_person_order(self, person_ids: Iterable[str]) -> None:
        ordered_ids = _unique_person_ids(person_ids)
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                [
                    (person_id, index, updated_at)
                    for index, person_id in enumerate(ordered_ids)
                ],
            )
            if ordered_ids:
                placeholders = ", ".join(["?"] * len(ordered_ids))
                conn.execute(
                    f"DELETE FROM person_card_orders WHERE person_id NOT IN ({placeholders})",
                    ordered_ids,
                )
            else:
                conn.execute("DELETE FROM person_card_orders")
            conn.commit()

    def get_person_hidden_map(self, person_ids: Iterable[str]) -> dict[str, bool]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id
                FROM hidden_people
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {str(row["person_id"]): True for row in rows if row["person_id"] is not None}

    def is_person_hidden(self, person_id: str) -> bool:
        if not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM hidden_people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return row is not None

    def set_person_hidden(self, person_id: str, hidden: bool) -> None:
        if not person_id:
            return
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            if hidden:
                conn.execute(
                    """
                    INSERT INTO hidden_people (person_id, updated_at)
                    VALUES (?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                    """,
                    (person_id, updated_at),
                )
            else:
                conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (person_id,))
            conn.commit()

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
        person_rows = []
        for person in persons:
            sample_count = max(int(person.sample_count), int(person.face_count))
            person_rows.append(
                (
                    person.person_id,
                    _normalize_name(person.name),
                    _serialize_embedding(person.center_embedding),
                    int(person.center_embedding.shape[0]),
                    person.created_at,
                    person.updated_at,
                    sample_count,
                    profile_state_for_sample_count(sample_count),
                )
            )
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = COALESCE(excluded.name, person_profiles.name),
                    center_embedding = excluded.center_embedding,
                    embedding_dim = excluded.embedding_dim,
                    updated_at = excluded.updated_at,
                    sample_count = excluded.sample_count,
                    profile_state = excluded.profile_state
                """,
                person_rows,
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
            ordered_ids = _unique_person_ids(person.person_id for person in persons if person.person_id)
            if ordered_ids:
                placeholders = ", ".join(["?"] * len(ordered_ids))
                rows = conn.execute(
                    f"""
                    SELECT person_id, sort_order
                    FROM person_card_orders
                    WHERE person_id IN ({placeholders})
                    """,
                    ordered_ids,
                ).fetchall()
                existing_order = {
                    str(row["person_id"]): int(row["sort_order"])
                    for row in rows
                    if row["person_id"] is not None and row["sort_order"] is not None
                }
                next_ids = [person_id for person_id, _order in sorted(existing_order.items(), key=lambda item: item[1])]
                next_ids.extend(person_id for person_id in ordered_ids if person_id not in existing_order)
                conn.executemany(
                    """
                    INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (person_id, index, timestamp)
                        for index, person_id in enumerate(next_ids)
                    ],
                )
                conn.execute(
                    f"DELETE FROM person_card_orders WHERE person_id NOT IN ({placeholders})",
                    ordered_ids,
                )
            else:
                conn.execute("DELETE FROM person_card_orders")
            conn.commit()

    def rename_person(self, person_id: str, name_or_none: str | None) -> None:
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    0,
                    "unstable",
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
        sample_count: int,
        hidden_state: bool,
    ) -> dict[str, str | None]:
        self.initialize()
        updated_at = _utc_now_iso()
        group_redirects: dict[str, str | None] = {}
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = COALESCE(excluded.name, person_profiles.name),
                    center_embedding = excluded.center_embedding,
                    embedding_dim = excluded.embedding_dim,
                    updated_at = excluded.updated_at,
                    sample_count = excluded.sample_count,
                    profile_state = excluded.profile_state
                """,
                (
                    target_person_id,
                    _normalize_name(target_name),
                    _serialize_embedding(center_embedding),
                    int(center_embedding.shape[0]),
                    target_created_at,
                    updated_at,
                    int(sample_count),
                    profile_state_for_sample_count(sample_count),
                ),
            )
            conn.execute(
                "UPDATE face_keys SET person_id = ?, updated_at = ? WHERE person_id = ?",
                (target_person_id, updated_at, source_person_id),
            )
            conn.execute(
                "UPDATE manual_faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )
            source_cover = conn.execute(
                """
                SELECT face_id, face_key, asset_id, thumbnail_path, is_custom
                FROM person_covers
                WHERE person_id = ?
                """,
                (source_person_id,),
            ).fetchone()
            target_cover = conn.execute(
                """
                SELECT is_custom
                FROM person_covers
                WHERE person_id = ?
                """,
                (target_person_id,),
            ).fetchone()
            if (
                source_cover is not None
                and int(source_cover["is_custom"]) == 1
                and (target_cover is None or int(target_cover["is_custom"]) == 0)
            ):
                conn.execute(
                    """
                    INSERT INTO person_covers (
                        person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        face_id = excluded.face_id,
                        face_key = excluded.face_key,
                        asset_id = excluded.asset_id,
                        thumbnail_path = excluded.thumbnail_path,
                        is_custom = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        target_person_id,
                        source_cover["face_id"],
                        source_cover["face_key"],
                        source_cover["asset_id"],
                        source_cover["thumbnail_path"],
                        updated_at,
                    ),
                )
            conn.execute("DELETE FROM person_covers WHERE person_id = ?", (source_person_id,))
            source_order = conn.execute(
                """
                SELECT sort_order
                FROM person_card_orders
                WHERE person_id = ?
                """,
                (source_person_id,),
            ).fetchone()
            target_order = conn.execute(
                """
                SELECT sort_order
                FROM person_card_orders
                WHERE person_id = ?
                """,
                (target_person_id,),
            ).fetchone()
            merged_order = None
            if target_order is not None:
                merged_order = int(target_order["sort_order"])
            elif source_order is not None:
                merged_order = int(source_order["sort_order"])
            if merged_order is not None:
                conn.execute(
                    """
                    INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    (target_person_id, merged_order, updated_at),
                )
            conn.execute("DELETE FROM person_card_orders WHERE person_id = ?", (source_person_id,))
            if hidden_state:
                conn.execute(
                    """
                    INSERT INTO hidden_people (person_id, updated_at)
                    VALUES (?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                    """,
                    (target_person_id, updated_at),
                )
            else:
                conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (target_person_id,))
            conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (source_person_id,))
            conn.execute("DELETE FROM person_profiles WHERE person_id = ?", (source_person_id,))
            group_redirects = self._remap_groups_for_merged_person(
                conn,
                source_person_id=source_person_id,
                target_person_id=target_person_id,
                updated_at=updated_at,
            )
            conn.commit()
        return group_redirects

    def get_person_cover_thumbnail_map(self, person_ids: Iterable[str]) -> dict[str, str]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id, thumbnail_path
                FROM person_covers
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {
            str(row["person_id"]): str(row["thumbnail_path"])
            for row in rows
            if row["person_id"] and row["thumbnail_path"]
        }

    def sync_person_cover_defaults(
        self,
        cover_rows: Iterable[tuple[str, str | None, str | None, str | None, str | None]],
    ) -> None:
        rows = [
            (
                str(person_id),
                str(face_id) if face_id else None,
                str(face_key) if face_key else None,
                str(asset_id) if asset_id else None,
                str(thumbnail_path) if thumbnail_path else None,
                _utc_now_iso(),
            )
            for person_id, face_id, face_key, asset_id, thumbnail_path in cover_rows
            if person_id
        ]
        if not rows:
            return

        self.initialize()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_covers (
                    person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    face_id = excluded.face_id,
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    updated_at = excluded.updated_at
                WHERE person_covers.is_custom = 0
                    AND (
                        COALESCE(person_covers.face_id, '') != COALESCE(excluded.face_id, '')
                        OR COALESCE(person_covers.face_key, '') != COALESCE(excluded.face_key, '')
                        OR COALESCE(person_covers.asset_id, '') != COALESCE(excluded.asset_id, '')
                        OR COALESCE(person_covers.thumbnail_path, '')
                            != COALESCE(excluded.thumbnail_path, '')
                    )
                """,
                rows,
            )
            conn.commit()

    def set_person_cover(
        self,
        person_id: str,
        *,
        face_id: str | None,
        face_key: str | None,
        asset_id: str | None,
        thumbnail_path: str | None,
    ) -> None:
        if not person_id:
            return

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_covers (
                    person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    face_id = excluded.face_id,
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    is_custom = 1,
                    updated_at = excluded.updated_at
                """,
                (person_id, face_id, face_key, asset_id, thumbnail_path, updated_at),
            )
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

    def delete_group(self, group_id: str) -> PeopleGroupRecord | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            group = self._group_from_id(conn, group_id)
            if group is None:
                return None
            conn.execute("DELETE FROM people_groups WHERE group_id = ?", (group_id,))
            conn.commit()
            return group

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
        asset_ids = {asset_id for asset_id, _last_detected_at, _position in rows}
        fallback_cover_asset_id = rows[0][0] if rows else None
        with closing(self._connect()) as conn:
            group_exists = conn.execute(
                "SELECT 1 FROM people_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if group_exists is None:
                return
            existing_cache = conn.execute(
                """
                SELECT cover_asset_id, cover_is_custom
                FROM people_group_asset_cache
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            cover_asset_id = fallback_cover_asset_id
            cover_is_custom = 0
            if existing_cache is not None and int(existing_cache["cover_is_custom"]) == 1:
                existing_cover_asset_id = str(existing_cache["cover_asset_id"] or "")
                if existing_cover_asset_id in asset_ids:
                    cover_asset_id = existing_cover_asset_id
                    cover_is_custom = 1

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
                    group_id, asset_count, cover_asset_id, cover_is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    asset_count = excluded.asset_count,
                    cover_asset_id = excluded.cover_asset_id,
                    cover_is_custom = excluded.cover_is_custom,
                    updated_at = excluded.updated_at
                """,
                (group_id, len(rows), cover_asset_id, cover_is_custom, timestamp),
            )
            conn.commit()

    def get_group_cover_asset_id(self, group_id: str) -> str | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT cover_asset_id
                FROM people_group_asset_cache
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
        if row is None or not row["cover_asset_id"]:
            return None
        return str(row["cover_asset_id"])

    def set_group_cover_asset(self, group_id: str, asset_id: str) -> bool:
        if not group_id or not asset_id:
            return False

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            asset_exists = conn.execute(
                """
                SELECT 1
                FROM people_group_assets
                WHERE group_id = ? AND asset_id = ?
                """,
                (group_id, asset_id),
            ).fetchone()
            if asset_exists is None:
                return False
            count_row = conn.execute(
                """
                SELECT COUNT(*) AS asset_count
                FROM people_group_assets
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            asset_count = int(count_row["asset_count"]) if count_row is not None else 0
            conn.execute(
                """
                INSERT INTO people_group_asset_cache (
                    group_id, asset_count, cover_asset_id, cover_is_custom, updated_at
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    asset_count = excluded.asset_count,
                    cover_asset_id = excluded.cover_asset_id,
                    cover_is_custom = 1,
                    updated_at = excluded.updated_at
                """,
                (group_id, asset_count, asset_id, updated_at),
            )
            conn.commit()
        return True

    def _remap_groups_for_merged_person(
        self,
        conn: sqlite3.Connection,
        *,
        source_person_id: str,
        target_person_id: str,
        updated_at: str,
    ) -> dict[str, str | None]:
        affected = conn.execute(
            """
            SELECT DISTINCT group_id
            FROM people_group_members
            WHERE person_id IN (?, ?)
            ORDER BY group_id ASC
            """,
            (source_person_id, target_person_id),
        ).fetchall()
        if not affected:
            return {}

        redirects: dict[str, str | None] = {}
        for row in affected:
            group_id = str(row["group_id"])
            group = self._group_from_id(conn, group_id)
            if group is None:
                continue
            next_members = _unique_person_ids(
                target_person_id if person_id == source_person_id else person_id
                for person_id in group.member_person_ids
            )
            if len(next_members) < 2:
                conn.execute("DELETE FROM people_groups WHERE group_id = ?", (group_id,))
                redirects[group_id] = None
                continue

            next_member_key = _group_member_key(next_members)
            if next_member_key == group.member_key:
                continue

            duplicate = conn.execute(
                """
                SELECT group_id
                FROM people_groups
                WHERE member_key = ? AND group_id != ?
                """,
                (next_member_key, group_id),
            ).fetchone()
            if duplicate is not None:
                target_group_id = str(duplicate["group_id"])
                self._transfer_group_cover_if_needed(
                    conn,
                    source_group_id=group_id,
                    target_group_id=target_group_id,
                    updated_at=updated_at,
                )
                conn.execute("DELETE FROM people_groups WHERE group_id = ?", (group_id,))
                redirects[group_id] = target_group_id
                continue

            conn.execute(
                """
                UPDATE people_groups
                SET member_key = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (next_member_key, updated_at, group_id),
            )
            conn.execute("DELETE FROM people_group_members WHERE group_id = ?", (group_id,))
            conn.executemany(
                """
                INSERT INTO people_group_members (group_id, person_id, position)
                VALUES (?, ?, ?)
                """,
                [(group_id, person_id, index) for index, person_id in enumerate(next_members)],
            )
        return redirects

    def _transfer_group_cover_if_needed(
        self,
        conn: sqlite3.Connection,
        *,
        source_group_id: str,
        target_group_id: str,
        updated_at: str,
    ) -> None:
        source_cache = conn.execute(
            """
            SELECT asset_count, cover_asset_id, cover_is_custom
            FROM people_group_asset_cache
            WHERE group_id = ?
            """,
            (source_group_id,),
        ).fetchone()
        if source_cache is None or int(source_cache["cover_is_custom"] or 0) != 1:
            return

        target_cache = conn.execute(
            """
            SELECT asset_count, cover_is_custom
            FROM people_group_asset_cache
            WHERE group_id = ?
            """,
            (target_group_id,),
        ).fetchone()
        if target_cache is not None and int(target_cache["cover_is_custom"] or 0) == 1:
            return

        asset_count = int(target_cache["asset_count"]) if target_cache is not None else 0
        conn.execute(
            """
            INSERT INTO people_group_asset_cache (
                group_id, asset_count, cover_asset_id, cover_is_custom, updated_at
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                asset_count = excluded.asset_count,
                cover_asset_id = excluded.cover_asset_id,
                cover_is_custom = 1,
                updated_at = excluded.updated_at
            """,
            (
                target_group_id,
                asset_count,
                source_cache["cover_asset_id"],
                updated_at,
            ),
        )

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
                updated_at TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                profile_state TEXT NOT NULL DEFAULT 'unstable'
            )
            """)
        FaceStateRepository._ensure_column(
            conn,
            "person_profiles",
            "sample_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        FaceStateRepository._ensure_column(
            conn,
            "person_profiles",
            "profile_state",
            "TEXT NOT NULL DEFAULT 'unstable'",
        )
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
            CREATE TABLE IF NOT EXISTS person_covers (
                person_id TEXT PRIMARY KEY,
                face_id TEXT,
                face_key TEXT,
                asset_id TEXT,
                thumbnail_path TEXT,
                is_custom INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_covers_face_key " "ON person_covers(face_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_covers_asset_id " "ON person_covers(asset_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_card_orders (
                person_id TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_card_orders_sort_order "
            "ON person_card_orders(sort_order)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hidden_people (
                person_id TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_faces (
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
                person_id TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL,
                is_manual INTEGER NOT NULL DEFAULT 1
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_faces_asset_id "
            "ON manual_faces(asset_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_faces_person_id "
            "ON manual_faces(person_id)"
        )
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
                cover_is_custom INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """)
        FaceStateRepository._ensure_column(
            conn,
            "people_group_asset_cache",
            "cover_is_custom",
            "INTEGER NOT NULL DEFAULT 0",
        )
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

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    @staticmethod
    def _manual_face_from_row(row: sqlite3.Row) -> FaceRecord:
        return FaceRecord(
            face_id=str(row["face_id"]),
            face_key=str(row["face_key"]),
            asset_id=str(row["asset_id"]),
            asset_rel=str(row["asset_rel"]),
            box_x=int(row["box_x"]),
            box_y=int(row["box_y"]),
            box_w=int(row["box_w"]),
            box_h=int(row["box_h"]),
            confidence=float(row["confidence"]),
            embedding=_deserialize_embedding(row["embedding"], int(row["embedding_dim"])),
            embedding_dim=int(row["embedding_dim"]),
            thumbnail_path=str(row["thumbnail_path"]) if row["thumbnail_path"] else None,
            person_id=str(row["person_id"]) if row["person_id"] else None,
            detected_at=str(row["detected_at"]),
            image_width=int(row["image_width"]),
            image_height=int(row["image_height"]),
            is_manual=bool(int(row["is_manual"] or 0)),
        )

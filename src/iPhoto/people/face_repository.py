"""SQLite face index repository for People clusters."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Iterable

import numpy as np

from .records import (
    AssetFaceAnnotation,
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PersonRecord,
    PersonSummary,
)
from .repository_utils import (
    _deserialize_embedding,
    _key_face_sort_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    compute_cluster_center,
    profile_state_for_sample_count,
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

    def replace_all(
        self,
        faces: list[FaceRecord],
        persons: list[PersonRecord],
        *,
        sync_runtime_state: bool = True,
    ) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM persons")
            conn.execute("DELETE FROM faces")
            person_rows = []
            for person in persons:
                sample_count = max(int(person.sample_count), int(person.face_count))
                person_rows.append(
                    (
                        person.person_id,
                        _normalize_name(person.name),
                        person.key_face_id,
                        person.face_count,
                        _serialize_embedding(person.center_embedding),
                        person.created_at,
                        person.updated_at,
                        sample_count,
                        profile_state_for_sample_count(sample_count),
                    )
                )
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
                    person_id, name, key_face_id, face_count, center_embedding,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                person_rows,
            )
            conn.commit()
        if sync_runtime_state:
            self.sync_runtime_state()

    def sync_runtime_state(self) -> None:
        if self._state_repo is None:
            return
        self._sync_person_cover_defaults()
        self.refresh_all_group_assets()

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

    def get_faces_by_asset_id(self, asset_id: str) -> list[FaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE asset_id = ?
                ORDER BY detected_at ASC, face_id ASC
                """,
                (asset_id,),
            ).fetchall()
        return [self._face_from_row(row) for row in rows]

    def get_all_person_records(self) -> list[PersonRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    person_id, name, key_face_id, face_count, center_embedding,
                    created_at, updated_at, sample_count, profile_state
                FROM persons
                ORDER BY created_at ASC, person_id ASC
                """
            ).fetchall()
        return [self._person_from_row(row) for row in rows]

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
        if self._state_repo is not None:
            self._sync_person_cover_defaults()
            self.refresh_all_group_assets()

    def get_person_summaries(self, *, include_hidden: bool = False) -> list[PersonSummary]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    persons.person_id,
                    persons.name,
                    persons.key_face_id,
                    persons.face_count,
                    persons.created_at,
                    faces.face_id,
                    faces.face_key,
                    faces.asset_id,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.face_count DESC, persons.created_at ASC
                """).fetchall()
        auto_rows_by_person_id = {str(row["person_id"]): row for row in rows if row["person_id"]}
        manual_faces_by_person_id: dict[str, list[ManualFaceRecord]] = defaultdict(list)
        profile_map = {}
        order_map: dict[str, int] = {}
        hidden_map: dict[str, bool] = {}
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces():
                manual_faces_by_person_id[face.person_id].append(face)
            profile_map = {profile.person_id: profile for profile in self._state_repo.get_profiles()}
        person_ids = set(auto_rows_by_person_id) | set(manual_faces_by_person_id)
        cover_paths: dict[str, str] = {}
        if self._state_repo is not None and person_ids:
            cover_paths = self._state_repo.get_person_cover_thumbnail_map(
                person_ids
            )
            order_map = self._state_repo.get_person_order_map(person_ids)
            hidden_map = self._state_repo.get_person_hidden_map(person_ids)
        summaries: list[PersonSummary] = []
        for person_id in person_ids:
            row = auto_rows_by_person_id.get(person_id)
            manual_faces = manual_faces_by_person_id.get(person_id, [])
            profile = profile_map.get(person_id)
            auto_count = int(row["face_count"]) if row is not None else 0
            face_count = auto_count + len(manual_faces)
            if face_count <= 0:
                continue
            key_face_id = (
                str(row["key_face_id"])
                if row is not None and row["key_face_id"]
                else manual_faces[0].face_id
            )
            name = row["name"] if row is not None else None
            if name is None and profile is not None:
                name = profile.name
            created_at = row["created_at"] if row is not None else None
            if created_at is None and profile is not None:
                created_at = profile.created_at
            if created_at is None:
                created_at = min((face.created_at for face in manual_faces), default=_utc_now_iso())
            thumbnail_path = cover_paths.get(person_id)
            if not thumbnail_path and row is not None:
                thumbnail_path = row["thumbnail_path"]
            if not thumbnail_path and manual_faces:
                thumbnail_path = manual_faces[0].thumbnail_path
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / thumbnail_path).resolve()
            summaries.append(
                PersonSummary(
                    person_id=person_id,
                    name=name,
                    key_face_id=key_face_id,
                    face_count=face_count,
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(created_at),
                    is_hidden=bool(hidden_map.get(person_id, False)),
                )
            )
        summaries.sort(key=lambda summary: (-summary.face_count, summary.created_at, summary.person_id))
        if order_map:
            fallback_order = {summary.person_id: index for index, summary in enumerate(summaries)}
            summaries.sort(
                key=lambda summary: (
                    order_map.get(summary.person_id, len(order_map) + fallback_order[summary.person_id]),
                    fallback_order[summary.person_id],
                )
            )
        if not include_hidden:
            summaries = [summary for summary in summaries if not summary.is_hidden]
        return summaries

    def is_person_hidden(self, person_id: str) -> bool:
        if self._state_repo is None:
            return False
        return self._state_repo.is_person_hidden(person_id)

    def set_person_hidden(self, person_id: str, hidden: bool) -> bool:
        if self._state_repo is None or not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM persons WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        if row is None and not self._state_repo.get_manual_faces_for_persons([person_id]):
            return False
        self._state_repo.set_person_hidden(person_id, hidden)
        return True

    def get_asset_ids_by_person(self, person_id: str) -> list[str]:
        if not person_id:
            return []
        self.initialize()
        asset_dates: dict[str, str] = {}
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
        for row in rows:
            if row["asset_id"]:
                asset_dates[str(row["asset_id"])] = str(row["last_detected_at"])
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces_for_persons([person_id]):
                previous = asset_dates.get(face.asset_id)
                if previous is None or face.created_at > previous:
                    asset_dates[face.asset_id] = face.created_at
        ordered = sorted(asset_dates.items(), key=lambda item: item[0])
        ordered = sorted(ordered, key=lambda item: item[1], reverse=True)
        return [asset_id for asset_id, _last_seen in ordered]

    def get_person_ids_for_asset_ids(self, asset_ids: Iterable[str]) -> list[str]:
        ids = [str(asset_id) for asset_id in asset_ids if asset_id]
        if not ids:
            return []
        self.initialize()
        # Use a set to deduplicate person IDs across chunks before sorting.
        chunk_size = 900
        person_ids: set[str] = set()
        with closing(self._connect()) as conn:
            for start in range(0, len(ids), chunk_size):
                chunk = ids[start : start + chunk_size]
                placeholders = ", ".join(["?"] * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT person_id
                    FROM faces
                    WHERE asset_id IN ({placeholders}) AND person_id IS NOT NULL
                    """,
                    chunk,
                ).fetchall()
                person_ids.update(str(row["person_id"]) for row in rows if row["person_id"])
        if self._state_repo is not None:
            person_ids.update(self._state_repo.get_manual_person_ids_for_asset_ids(ids))
        return sorted(person_ids)

    def list_asset_face_annotations(self, asset_id: str) -> list[AssetFaceAnnotation]:
        if not asset_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    faces.face_id,
                    faces.person_id,
                    persons.name,
                    faces.box_x,
                    faces.box_y,
                    faces.box_w,
                    faces.box_h,
                    faces.image_width,
                    faces.image_height,
                    faces.thumbnail_path
                FROM faces
                LEFT JOIN persons ON persons.person_id = faces.person_id
                WHERE faces.asset_id = ?
                ORDER BY faces.box_x ASC, faces.box_y ASC, faces.face_id ASC
                """,
                (asset_id,),
            ).fetchall()
        annotations = [
            AssetFaceAnnotation(
                face_id=str(row["face_id"]),
                person_id=str(row["person_id"]) if row["person_id"] else None,
                display_name=row["name"],
                box_x=int(row["box_x"]),
                box_y=int(row["box_y"]),
                box_w=int(row["box_w"]),
                box_h=int(row["box_h"]),
                image_width=int(row["image_width"]),
                image_height=int(row["image_height"]),
                thumbnail_path=(
                    (self._db_path.parent / str(row["thumbnail_path"])).resolve()
                    if row["thumbnail_path"]
                    else None
                ),
                is_manual=False,
            )
            for row in rows
            if row["face_id"]
        ]
        if self._state_repo is not None:
            manual_faces = self._state_repo.get_manual_faces_for_asset(asset_id)
            names = self._state_repo.get_profile_name_map(
                face.person_id for face in manual_faces
            )
            missing_name_ids = [
                face.person_id
                for face in manual_faces
                if face.person_id not in names or names[face.person_id] is None
            ]
            if missing_name_ids:
                placeholders = ", ".join(["?"] * len(set(missing_name_ids)))
                with closing(self._connect()) as conn:
                    name_rows = conn.execute(
                        f"""
                        SELECT person_id, name
                        FROM persons
                        WHERE person_id IN ({placeholders})
                        """,
                        list(dict.fromkeys(missing_name_ids)),
                    ).fetchall()
                names.update(
                    {
                        str(row["person_id"]): row["name"]
                        for row in name_rows
                        if row["person_id"] and row["name"] is not None
                    }
                )
            annotations.extend(
                AssetFaceAnnotation(
                    face_id=face.face_id,
                    person_id=face.person_id,
                    display_name=names.get(face.person_id),
                    box_x=face.box_x,
                    box_y=face.box_y,
                    box_w=face.box_w,
                    box_h=face.box_h,
                    image_width=face.image_width,
                    image_height=face.image_height,
                    thumbnail_path=(
                        (self._db_path.parent / face.thumbnail_path).resolve()
                        if face.thumbnail_path
                        else None
                    ),
                    is_manual=True,
                )
                for face in manual_faces
            )
        annotations.sort(key=lambda face: (face.box_x, face.box_y, face.face_id))
        return annotations

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

    def set_person_cover(self, person_id: str, face_id: str) -> bool:
        if self._state_repo is None or not person_id or not face_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT face_id, face_key, asset_id, thumbnail_path
                FROM faces
                WHERE person_id = ? AND face_id = ?
                """,
                (person_id, face_id),
            ).fetchone()
        if row is None:
            manual_face = self._state_repo.get_manual_face(face_id)
            if manual_face is None or manual_face.person_id != person_id:
                return False
            self._state_repo.set_person_cover(
                person_id,
                face_id=manual_face.face_id,
                face_key=None,
                asset_id=manual_face.asset_id,
                thumbnail_path=manual_face.thumbnail_path,
            )
            return True
        self._state_repo.set_person_cover(
            person_id,
            face_id=row["face_id"],
            face_key=row["face_key"],
            asset_id=row["asset_id"],
            thumbnail_path=row["thumbnail_path"],
        )
        return True

    def set_person_order(self, person_ids: Iterable[str]) -> None:
        if self._state_repo is None:
            return
        self._state_repo.set_person_order(person_ids)

    def merge_persons(self, source_person_id: str, target_person_id: str) -> bool:
        merged, _group_redirects = self.merge_persons_with_redirects(
            source_person_id,
            target_person_id,
        )
        return merged

    def merge_persons_with_redirects(
        self,
        source_person_id: str,
        target_person_id: str,
    ) -> tuple[bool, dict[str, str | None]]:
        if not source_person_id or not target_person_id or source_person_id == target_person_id:
            return False, {}
        source_hidden = False
        target_hidden = False
        if self._state_repo is not None:
            hidden_map = self._state_repo.get_person_hidden_map((source_person_id, target_person_id))
            source_hidden = bool(hidden_map.get(source_person_id, False))
            target_hidden = bool(hidden_map.get(target_person_id, False))
            if source_hidden != target_hidden:
                return False, {}

        self.initialize()
        group_redirects: dict[str, str | None] = {}
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
            source_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == source_person_id
            ]
            target_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == target_person_id
            ]
            manual_source_faces: list[ManualFaceRecord] = []
            manual_target_faces: list[ManualFaceRecord] = []
            profile_map = {}
            if self._state_repo is not None:
                manual_faces = self._state_repo.get_manual_faces_for_persons(
                    (source_person_id, target_person_id)
                )
                manual_source_faces = [
                    face for face in manual_faces if face.person_id == source_person_id
                ]
                manual_target_faces = [
                    face for face in manual_faces if face.person_id == target_person_id
                ]
                profile_map = {
                    profile.person_id: profile
                    for profile in self._state_repo.get_profiles()
                    if profile.person_id in {source_person_id, target_person_id}
                }
            if not (source_faces or manual_source_faces) or not (
                target_faces or manual_target_faces
            ):
                return False, {}

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
            target_profile = profile_map.get(target_person_id)
            source_profile = profile_map.get(source_person_id)
            target_name = None
            target_created_at = _utc_now_iso()
            if target_person is not None:
                target_name = target_person["name"]
                target_created_at = target_person["created_at"]
            elif target_profile is not None:
                target_name = target_profile.name
                target_created_at = target_profile.created_at
            elif manual_target_faces:
                target_created_at = min(face.created_at for face in manual_target_faces)
            elif source_person is not None:
                target_name = source_person["name"]
                target_created_at = source_person["created_at"]
            elif source_profile is not None:
                target_name = source_profile.name
                target_created_at = source_profile.created_at
            elif manual_source_faces:
                target_created_at = min(face.created_at for face in manual_source_faces)

            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )

            merged_faces = [
                FaceRecord(**{**face.__dict__, "person_id": target_person_id})
                for face in (target_faces + source_faces)
            ]
            center_embedding = np.empty((0,), dtype=np.float32)
            updated_at = _utc_now_iso()
            if merged_faces:
                key_face = max(merged_faces, key=_key_face_sort_key)
                center_embedding = compute_cluster_center(
                    np.stack([face.embedding for face in merged_faces], axis=0)
                )
                conn.execute(
                    """
                    INSERT INTO persons (
                        person_id, name, key_face_id, face_count, center_embedding,
                        created_at, updated_at, sample_count, profile_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        name = excluded.name,
                        key_face_id = excluded.key_face_id,
                        face_count = excluded.face_count,
                        center_embedding = excluded.center_embedding,
                        updated_at = excluded.updated_at,
                        sample_count = excluded.sample_count,
                        profile_state = excluded.profile_state
                    """,
                    (
                        target_person_id,
                        _normalize_name(target_name),
                        key_face.face_id,
                        len(merged_faces),
                        _serialize_embedding(center_embedding),
                        target_created_at,
                        updated_at,
                        len(merged_faces),
                        profile_state_for_sample_count(len(merged_faces)),
                    ),
                )
            else:
                conn.execute("DELETE FROM persons WHERE person_id = ?", (target_person_id,))
            conn.execute("DELETE FROM persons WHERE person_id = ?", (source_person_id,))
            conn.commit()

        if self._state_repo is not None:
            group_redirects = self._state_repo.merge_persons(
                source_person_id,
                target_person_id,
                center_embedding=center_embedding,
                target_name=target_name,
                target_created_at=target_created_at,
                sample_count=len(merged_faces),
                hidden_state=source_hidden,
            )
            self._sync_person_cover_defaults()
            self.refresh_all_group_assets()
        return True, group_redirects

    def create_group(self, member_person_ids: Iterable[str]) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        group = self._state_repo.create_group(member_person_ids)
        if group is not None:
            self.refresh_group_assets(group.group_id)
        return group

    def list_groups(self) -> list[PeopleGroupRecord]:
        if self._state_repo is None:
            return []
        self.initialize()
        return self._state_repo.list_groups()

    def delete_group(self, group_id: str) -> tuple[bool, PeopleGroupRecord | None, list[str]]:
        if self._state_repo is None or not group_id:
            return False, None, []
        self.initialize()
        group = self._state_repo.get_group(group_id)
        if group is None:
            return False, None, []
        asset_ids = self.get_common_asset_ids_for_group(group_id)
        deleted_group = self._state_repo.delete_group(group_id)
        if deleted_group is None:
            return False, None, []
        return True, deleted_group, asset_ids

    def get_group(self, group_id: str) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        return self._state_repo.get_group(group_id)

    def get_common_asset_ids_for_persons(self, member_person_ids: Iterable[str]) -> list[str]:
        return [
            asset_id
            for asset_id, _last_detected_at in self._common_asset_rows_for_persons(
                member_person_ids
            )
        ]

    def _common_asset_rows_for_persons(
        self,
        member_person_ids: Iterable[str],
    ) -> list[tuple[str, str]]:
        members = _unique_person_ids(member_person_ids)
        if len(members) < 2:
            return []

        self.initialize()
        hits_by_asset_id: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
        placeholders = ", ".join(["?"] * len(members))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    person_id,
                    asset_id,
                    MAX(detected_at) AS last_detected_at,
                    MAX(rowid) AS last_face_rowid
                FROM faces
                WHERE person_id IN ({placeholders})
                GROUP BY person_id, asset_id
                """,
                members,
            ).fetchall()
        for row in rows:
            if row["person_id"] and row["asset_id"]:
                hits_by_asset_id[str(row["asset_id"])][str(row["person_id"])] = (
                    str(row["last_detected_at"]),
                    int(row["last_face_rowid"] or 0),
                )
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces_for_persons(members):
                person_hits = hits_by_asset_id[face.asset_id]
                previous = person_hits.get(face.person_id)
                manual_hit = (face.created_at, 0)
                if previous is None or manual_hit > previous:
                    person_hits[face.person_id] = manual_hit

        member_set = set(members)
        common_rows = [
            (asset_id, *max(person_hits.values()))
            for asset_id, person_hits in hits_by_asset_id.items()
            if member_set.issubset(person_hits)
        ]
        common_rows = sorted(common_rows, key=lambda item: item[0])
        common_rows = sorted(common_rows, key=lambda item: (item[1], item[2]), reverse=True)
        return [(asset_id, last_detected_at) for asset_id, last_detected_at, _rowid in common_rows]

    def get_common_asset_ids_for_group(self, group_id: str) -> list[str]:
        if self._state_repo is None:
            return []
        self.initialize()
        if self._state_repo.has_group_asset_cache(group_id):
            return self._state_repo.get_group_asset_ids(group_id)
        return self.refresh_group_assets(group_id)

    def get_group_cover_asset_id(self, group_id: str) -> str | None:
        if self._state_repo is None:
            return None
        return self._state_repo.get_group_cover_asset_id(group_id)

    def set_group_cover_asset(self, group_id: str, asset_id: str) -> bool:
        if self._state_repo is None:
            return False
        self.initialize()
        if not self._state_repo.has_group_asset_cache(group_id):
            self.refresh_group_assets(group_id)
        return self._state_repo.set_group_cover_asset(group_id, asset_id)

    def refresh_group_assets(self, group_id: str) -> list[str]:
        if self._state_repo is None:
            return []
        group = self.get_group(group_id)
        if group is None:
            return []
        asset_rows = self._common_asset_rows_for_persons(group.member_person_ids)
        self._state_repo.replace_group_assets(group.group_id, asset_rows)
        return [asset_id for asset_id, _last_detected_at in asset_rows]

    def refresh_all_group_assets(self) -> None:
        if self._state_repo is None:
            return
        self.initialize()
        for group in self._state_repo.list_groups():
            self.refresh_group_assets(group.group_id)

    def _sync_person_cover_defaults(self) -> None:
        if self._state_repo is None:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    persons.person_id,
                    faces.face_id,
                    faces.face_key,
                    faces.asset_id,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.created_at ASC, persons.person_id ASC
                """).fetchall()
        self._state_repo.sync_person_cover_defaults(
            (
                (
                    str(row["person_id"]),
                    row["face_id"],
                    row["face_key"],
                    row["asset_id"],
                    row["thumbnail_path"],
                )
                for row in rows
            )
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
                updated_at TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                profile_state TEXT NOT NULL DEFAULT 'unstable'
            )
            """)
        FaceRepository._ensure_column(
            conn,
            "persons",
            "sample_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        FaceRepository._ensure_column(
            conn,
            "persons",
            "profile_state",
            "TEXT NOT NULL DEFAULT 'unstable'",
        )
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

    @staticmethod
    def _person_from_row(row: sqlite3.Row) -> PersonRecord:
        center_blob = row["center_embedding"]
        embedding_dim = int(len(center_blob) / 4) if center_blob else 0
        return PersonRecord(
            person_id=str(row["person_id"]),
            name=row["name"],
            key_face_id=str(row["key_face_id"]),
            face_count=int(row["face_count"]),
            center_embedding=_deserialize_embedding(center_blob, embedding_dim),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            sample_count=int(row["sample_count"] or row["face_count"] or 0),
            profile_state=str(
                row["profile_state"]
                or profile_state_for_sample_count(int(row["sample_count"] or row["face_count"] or 0))
            ),
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

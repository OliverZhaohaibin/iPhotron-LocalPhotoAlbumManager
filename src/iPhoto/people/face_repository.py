"""SQLite face index repository for People clusters."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from iPhoto.recognition.promotion import (
    PROMOTION_CANDIDATE,
    PROMOTION_CONFIRMED,
    PROMOTION_LEGACY_VISIBLE,
)
from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite

from .records import (
    AssetFaceAnnotation,
    FaceRecord,
    IdentityGroupMember,
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
    _unique_group_members,
    _unique_person_ids,
    _utc_now_iso,
    compute_cluster_center,
    profile_state_for_sample_count,
)
from .state_repository import FaceStateRepository


@dataclass(frozen=True)
class FaceMutationResult:
    changed_asset_ids: tuple[str, ...] = ()
    changed_person_ids: tuple[str, ...] = ()
    changed_group_ids: tuple[str, ...] = ()
    person_redirects: dict[str, str] = field(default_factory=dict)
    group_redirects: dict[str, str | None] = field(default_factory=dict)


class FaceRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = FaceStateRepository(state_db_path) if state_db_path is not None else None
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> FaceStateRepository | None:
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
            self._initialized = True

    def replace_all(
        self,
        faces: list[FaceRecord],
        persons: list[PersonRecord],
        *,
        sync_runtime_state: bool = True,
        operation_id: str | None = None,
        operation_kind: str = "people_scan_commit",
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
                        person.profile_state,
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
            if operation_id is not None:
                self._write_runtime_commit(
                    conn,
                    operation_id,
                    {
                        "operation_kind": operation_kind,
                        "changed_asset_ids": sorted(
                            {face.asset_id for face in faces if face.asset_id}
                        ),
                        "changed_person_ids": sorted(
                            {person.person_id for person in persons if person.person_id}
                        ),
                    },
                )
            conn.commit()
        if sync_runtime_state:
            if operation_id is not None:
                self.complete_runtime_state_sync(operation_id)
            else:
                self.sync_runtime_state()

    def sync_runtime_state(self) -> None:
        if self._state_repo is None:
            return
        self._sync_person_cover_defaults()
        self.refresh_all_group_assets()

    def record_runtime_commit(
        self,
        operation_id: str,
        payload: dict[str, object],
        *,
        state_synced: bool = False,
    ) -> None:
        if not operation_id:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            self._write_runtime_commit(
                conn,
                operation_id,
                payload,
                state_synced=state_synced,
            )
            conn.commit()

    def add_manual_face(
        self,
        face: ManualFaceRecord,
        *,
        person_name: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        if self._state_repo is None:
            raise RuntimeError("Manual faces require a People state repository.")
        if operation_id is None:
            self._state_repo.add_manual_face(face, person_name=person_name)
            self.sync_runtime_state()
            return
        self.record_runtime_commit(
            operation_id,
            {
                "operation_kind": "people_add_manual_face",
                "manual_face": {
                    "face_id": face.face_id,
                    "asset_id": face.asset_id,
                    "asset_rel": face.asset_rel,
                    "box_x": face.box_x,
                    "box_y": face.box_y,
                    "box_w": face.box_w,
                    "box_h": face.box_h,
                    "thumbnail_path": face.thumbnail_path,
                    "person_id": face.person_id,
                    "created_at": face.created_at,
                    "image_width": face.image_width,
                    "image_height": face.image_height,
                },
                "person_name": person_name,
                "changed_asset_ids": [face.asset_id],
                "changed_person_ids": [face.person_id],
            },
        )
        self.complete_runtime_state_sync(operation_id)

    @staticmethod
    def _write_runtime_commit(
        conn: sqlite3.Connection,
        operation_id: str,
        payload: dict[str, object],
        *,
        state_synced: bool = False,
    ) -> None:
        conn.execute(
            """
            INSERT INTO people_runtime_commits (
                operation_id, payload_json, state_synced, created_at, updated_at
            ) VALUES (?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(operation_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                state_synced = MAX(people_runtime_commits.state_synced, excluded.state_synced),
                updated_at = datetime('now')
            """,
            (
                operation_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                int(state_synced),
            ),
        )

    def get_runtime_commit(self, operation_id: str) -> dict[str, object] | None:
        if not operation_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT payload_json, state_synced
                FROM people_runtime_commits WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"] or "{}"))
        payload["state_synced"] = bool(row["state_synced"])
        return payload

    def complete_runtime_state_sync(self, operation_id: str) -> dict[str, object] | None:
        payload = self.get_runtime_commit(operation_id)
        if payload is None or bool(payload.get("state_synced")):
            return payload
        operation_kind = str(payload.get("operation_kind") or "")
        if self._state_repo is not None:
            if operation_kind == "people_add_manual_face":
                manual_payload = payload.get("manual_face")
                if isinstance(manual_payload, dict):
                    manual_face = ManualFaceRecord(**manual_payload)
                    self._state_repo.add_manual_face(
                        manual_face,
                        person_name=(
                            str(payload["person_name"])
                            if payload.get("person_name") is not None
                            else None
                        ),
                    )
                    payload["changed_group_ids"] = (
                        self._state_repo.list_group_ids_for_people(
                            (manual_face.person_id,)
                        )
                    )
            if operation_kind == "people_delete_face":
                face_key = str(payload.get("face_key") or "")
                face_id = str(payload.get("face_id") or "")
                if not face_key and face_id:
                    self._state_repo.delete_manual_face(face_id)
            elif operation_kind == "people_move_face":
                face_id = str(payload.get("face_id") or "")
                target_person_id = str(payload.get("target_person_id") or "")
                if face_id and target_person_id:
                    manual_face = self._state_repo.get_manual_face(face_id)
                    if manual_face is not None:
                        self._state_repo.move_manual_face(face_id, target_person_id)
            elif operation_kind == "people_move_face_new":
                new_person_id = str(payload.get("new_person_id") or "")
                new_name = payload.get("new_name")
                face_id = str(payload.get("face_id") or "")
                if face_id and new_person_id:
                    manual_face = self._state_repo.get_manual_face(face_id)
                    if manual_face is not None:
                        self._state_repo.move_manual_face(face_id, new_person_id)
            if operation_kind in {
                "people_delete_face",
                "people_move_face",
                "people_move_face_new",
            }:
                mutation = self._finalize_face_mutation(
                    changed_asset_ids=(
                        str(value)
                        for value in payload.get("changed_asset_ids", ())
                        if value
                    ),
                    changed_person_ids=(
                        str(value)
                        for value in payload.get("changed_person_ids", ())
                        if value
                    ),
                )
                payload["changed_asset_ids"] = list(mutation.changed_asset_ids)
                payload["changed_person_ids"] = list(mutation.changed_person_ids)
                payload["changed_group_ids"] = list(mutation.changed_group_ids)
                payload["person_redirects"] = mutation.person_redirects
                payload["group_redirects"] = mutation.group_redirects

            persons = self.get_all_person_records()
            faces = self.get_all_faces()
            if operation_kind in {
                "people_scan_commit",
                "people_delete_face",
                "people_move_face",
                "people_move_face_new",
            }:
                self._state_repo.sync_scan_results(persons, faces)
            if operation_kind == "people_delete_face":
                face_key = str(payload.get("face_key") or "")
                if face_key:
                    self._state_repo.reject_face_key(
                        face_key,
                        asset_id=str(payload.get("asset_id") or "") or None,
                        asset_rel=str(payload.get("asset_rel") or "") or None,
                    )
                face_id = str(payload.get("face_id") or "")
                if face_id:
                    self._state_repo.clear_annotation_identity_assignment("person", face_id)
            elif operation_kind == "people_move_face_new":
                new_person_id = str(payload.get("new_person_id") or "")
                new_name = payload.get("new_name")
                if new_person_id and new_name:
                    self._state_repo.rename_person(new_person_id, str(new_name))
                    self._state_repo.confirm_person(new_person_id)
            elif operation_kind == "people_move_face":
                target_person_id = str(payload.get("target_person_id") or "")
                if target_person_id:
                    self._state_repo.confirm_person(target_person_id)
            elif operation_kind == "people_rename":
                person_id = str(payload.get("person_id") or "")
                if person_id:
                    self._state_repo.rename_person(person_id, payload.get("name"))
            elif operation_kind == "people_merge":
                payload["group_redirects"] = self._complete_merge_state_sync(
                    payload,
                    persons,
                )
            if operation_kind in {
                "people_delete_face",
                "people_move_face",
                "people_move_face_new",
            }:
                active_ids = {person.person_id for person in persons if person.person_id}
                for person_id in (
                    str(value)
                    for value in payload.get("changed_person_ids", ())
                    if value
                ):
                    if person_id not in active_ids:
                        self._state_repo.remove_person_from_groups(person_id)
                        self._state_repo.delete_person_state(person_id)
            if operation_kind == "people_create_group":
                group = self._state_repo.create_group(payload.get("members", ()))
                if group is not None:
                    payload["changed_group_ids"] = [group.group_id]
                    payload["changed_person_ids"] = list(group.member_person_ids)
                    payload["changed_asset_ids"] = (
                        self.get_common_asset_ids_for_group(group.group_id)
                    )
            elif operation_kind == "people_delete_group":
                self._state_repo.delete_group(str(payload.get("group_id") or ""))
        self.sync_runtime_state()
        persisted_payload = {
            key: value for key, value in payload.items() if key != "state_synced"
        }
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE people_runtime_commits
                SET payload_json = ?, state_synced = 1, updated_at = datetime('now')
                WHERE operation_id = ?
                """,
                (
                    json.dumps(
                        persisted_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    operation_id,
                ),
            )
            conn.commit()
        payload["state_synced"] = True
        return payload

    def _complete_merge_state_sync(
        self,
        payload: dict[str, object],
        persons: list[PersonRecord],
    ) -> dict[str, str | None]:
        if self._state_repo is None:
            return {}
        source_person_id = str(payload.get("source_person_id") or "")
        target_person_id = str(payload.get("target_person_id") or "")
        target = next(
            (person for person in persons if person.person_id == target_person_id),
            None,
        )
        if not source_person_id or not target_person_id:
            return {}
        center_embedding = (
            target.center_embedding
            if target is not None
            else np.empty((0,), dtype=np.float32)
        )
        return self._state_repo.merge_persons(
            source_person_id,
            target_person_id,
            center_embedding=center_embedding,
            target_name=(
                target.name if target is not None else payload.get("target_name")
            ),
            target_created_at=(
                target.created_at
                if target is not None
                else str(payload.get("target_created_at") or _utc_now_iso())
            ),
            sample_count=(
                max(int(target.sample_count), int(target.face_count))
                if target is not None
                else int(payload.get("sample_count") or 0)
            ),
            evidence_asset_count=(
                int(target.evidence_asset_count)
                if target is not None and target.evidence_asset_count > 0
                else int(payload.get("evidence_asset_count") or 0)
            ),
            hidden_state=bool(payload.get("hidden_state", False)),
        )

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
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        return [
            self._face_from_row(row)
            for row in rows
            if row["face_key"] not in rejected_face_keys
        ]

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
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        return [
            self._face_from_row(row)
            for row in rows
            if row["face_key"] not in rejected_face_keys
        ]

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
            evidence_rows = conn.execute(
                """
                SELECT person_id, COUNT(DISTINCT asset_id) AS evidence_asset_count
                FROM faces
                WHERE person_id IS NOT NULL
                GROUP BY person_id
                """
            ).fetchall()
        evidence_by_person_id = {
            str(row["person_id"]): int(row["evidence_asset_count"] or 0)
            for row in evidence_rows
            if row["person_id"]
        }
        return [
            PersonRecord(
                **{
                    **self._person_from_row(row).__dict__,
                    "evidence_asset_count": evidence_by_person_id.get(
                        str(row["person_id"]), 0
                    ),
                }
            )
            for row in rows
        ]

    def get_person_name_map(
        self,
        person_ids: Iterable[str],
    ) -> dict[str, str | None]:
        """Return runtime display names for a bounded set of canonical people."""

        unique_ids = tuple(dict.fromkeys(str(value) for value in person_ids if value))
        if not unique_ids:
            return {}
        self.initialize()
        result: dict[str, str | None] = {}
        with closing(self._connect()) as conn:
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT person_id, name FROM persons "
                    f"WHERE person_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                result.update(
                    {
                        str(row["person_id"]): row["name"]
                        for row in rows
                        if row["person_id"]
                    }
                )
        return result

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
            asset_rows = conn.execute(
                """
                SELECT person_id, asset_id
                FROM faces
                WHERE person_id IS NOT NULL
                """
            ).fetchall()
        auto_asset_ids_by_person_id: dict[str, set[str]] = defaultdict(set)
        for asset_row in asset_rows:
            if asset_row["person_id"] and asset_row["asset_id"]:
                auto_asset_ids_by_person_id[str(asset_row["person_id"])].add(
                    str(asset_row["asset_id"])
                )
        auto_rows_by_person_id = {str(row["person_id"]): row for row in rows if row["person_id"]}
        manual_faces_by_person_id: dict[str, list[ManualFaceRecord]] = defaultdict(list)
        profile_map = {}
        order_map: dict[str, int] = {}
        hidden_map: dict[str, bool] = {}
        promotion_map = {}
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
            promotion_map = self._state_repo.get_promotion_records()
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
            asset_ids = set(auto_asset_ids_by_person_id.get(person_id, set()))
            asset_ids.update(face.asset_id for face in manual_faces if face.asset_id)
            promotion = promotion_map.get(person_id)
            evidence_asset_count = (
                promotion.evidence_asset_count if promotion is not None else len(asset_ids)
            )
            promotion_state = (
                promotion.promotion_state
                if promotion is not None
                else PROMOTION_LEGACY_VISIBLE
            )
            summaries.append(
                PersonSummary(
                    person_id=person_id,
                    name=name,
                    key_face_id=key_face_id,
                    face_count=face_count,
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(created_at),
                    is_hidden=bool(hidden_map.get(person_id, False)),
                    asset_count=len(asset_ids),
                    profile_state=profile_state_for_sample_count(evidence_asset_count),
                    evidence_asset_count=evidence_asset_count,
                    promotion_state=promotion_state,
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
        asset_dates = self._person_asset_rows(person_id)
        ordered = sorted(asset_dates.items(), key=lambda item: item[0])
        ordered = sorted(ordered, key=lambda item: item[1], reverse=True)
        return [asset_id for asset_id, _last_seen in ordered]

    def get_asset_ids_by_people(
        self,
        person_ids: Iterable[str],
    ) -> dict[str, list[str]]:
        """Return effective identity assets for dashboard cards in one batch."""
        target_ids = tuple(dict.fromkeys(str(value) for value in person_ids if value))
        if not target_ids:
            return {}
        rows = self._effective_identity_asset_rows(
            IdentityGroupMember("person", person_id) for person_id in target_ids
        )
        return {
            person_id: sorted(rows.get(("person", person_id), {}))
            for person_id in target_ids
        }

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
                    faces.face_key,
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
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        canonical = self._canonical_annotation_identities(
            str(row["person_id"]) for row in rows if row["person_id"]
        )
        promotion_map = (
            self._state_repo.get_promotion_records(
                str(row["person_id"]) for row in rows if row["person_id"]
            )
            if self._state_repo is not None
            else {}
        )
        assignments = (
            self._state_repo.get_annotation_identity_assignments(
                ("person", str(row["face_id"])) for row in rows if row["face_id"]
            )
            if self._state_repo is not None
            else {}
        )
        assigned_canonical = self._canonical_identity_refs(assignments.values())
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
                source_identity_id=(
                    str(row["person_id"]) if row["person_id"] else None
                ),
                canonical_identity_kind=(
                    assigned_canonical[assignments[("person", str(row["face_id"]))]][0]
                    if ("person", str(row["face_id"])) in assignments
                    else canonical[str(row["person_id"])][0]
                    if row["person_id"] else "person"
                ),
                canonical_identity_id=(
                    assigned_canonical[assignments[("person", str(row["face_id"]))]][1]
                    if ("person", str(row["face_id"])) in assignments
                    else canonical[str(row["person_id"])][1]
                    if row["person_id"] else None
                ),
                canonical_display_name=(
                    assigned_canonical[assignments[("person", str(row["face_id"]))]][2]
                    if ("person", str(row["face_id"])) in assignments
                    else canonical[str(row["person_id"])][2]
                    if row["person_id"] else None
                ),
                promotion_state=(
                    PROMOTION_CONFIRMED
                    if ("person", str(row["face_id"])) in assignments
                    else promotion_map[str(row["person_id"])].promotion_state
                    if row["person_id"] and str(row["person_id"]) in promotion_map
                    else PROMOTION_CANDIDATE
                ),
            )
            for row in rows
            if row["face_id"] and row["face_key"] not in rejected_face_keys
        ]
        if self._state_repo is not None:
            manual_faces = self._state_repo.get_manual_faces_for_asset(asset_id)
            manual_promotions = self._state_repo.get_promotion_records(
                face.person_id for face in manual_faces
            )
            manual_canonical = self._canonical_annotation_identities(
                face.person_id for face in manual_faces
            )
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
                    source_identity_id=face.person_id,
                    canonical_identity_kind=manual_canonical[face.person_id][0],
                    canonical_identity_id=manual_canonical[face.person_id][1],
                    canonical_display_name=manual_canonical[face.person_id][2],
                    promotion_state=(
                        manual_promotions[face.person_id].promotion_state
                        if face.person_id in manual_promotions
                        else PROMOTION_CONFIRMED
                    ),
                )
                for face in manual_faces
            )
        annotations.sort(key=lambda face: (face.box_x, face.box_y, face.face_id))
        return annotations

    def _canonical_annotation_identities(
        self,
        person_ids: Iterable[str],
    ) -> dict[str, tuple[str, str, str | None]]:
        source_ids = tuple(dict.fromkeys(str(value) for value in person_ids if value))
        if not source_ids:
            return {}
        resolved = self._canonical_identity_refs(
            ("person", source_id) for source_id in source_ids
        )
        return {
            source_id: resolved[("person", source_id)] for source_id in source_ids
        }

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
        redirect_map: dict[tuple[str, str], tuple[str, str]] = {}
        if self._state_repo is not None:
            redirect_map = {
                (redirect.source_kind, redirect.source_id): (
                    redirect.target_kind,
                    redirect.target_id,
                )
                for redirect in self._state_repo.get_identity_redirects()
            }
        resolved = {
            source_ref: _resolve_identity_redirect(*source_ref, redirect_map)
            for source_ref in source_refs
        }
        person_targets = [
            entity_id for kind, entity_id in resolved.values() if kind == "person"
        ]
        person_names = (
            self._state_repo.get_profile_name_map(person_targets)
            if self._state_repo is not None
            else {}
        )
        pet_names: dict[str, str | None] = {}
        pet_targets = [entity_id for kind, entity_id in resolved.values() if kind == "pet"]
        pet_state_path = self._db_path.parent.parent / "pets" / "pet_state.db"
        if pet_targets and pet_state_path.exists():
            from iPhoto.pets.state_repository import PetStateRepository

            pet_names = PetStateRepository(pet_state_path).get_profile_name_map(pet_targets)
        return {
            source_ref: (
                kind,
                entity_id,
                person_names.get(entity_id) if kind == "person" else pet_names.get(entity_id),
            )
            for source_ref, (kind, entity_id) in resolved.items()
        }

    def rename_person(
        self,
        person_id: str,
        name_or_none: str | None,
        *,
        operation_id: str | None = None,
    ) -> bool:
        if not person_id:
            return False
        self.initialize()
        normalized_name = _normalize_name(name_or_none)
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            exists = conn.execute(
                "SELECT 1 FROM persons WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None and (
                self._state_repo is None
                or self._state_repo.get_profile(person_id) is None
            ):
                return False
            conn.execute(
                "UPDATE persons SET name = ?, updated_at = ? WHERE person_id = ?",
                (normalized_name, updated_at, person_id),
            )
            if operation_id is not None:
                self._write_runtime_commit(
                    conn,
                    operation_id,
                    {
                        "operation_kind": "people_rename",
                        "person_id": person_id,
                        "name": normalized_name,
                        "changed_asset_ids": self.get_asset_ids_by_person(person_id),
                        "changed_person_ids": [person_id],
                    },
                )
            conn.commit()
        if operation_id is not None:
            self.complete_runtime_state_sync(operation_id)
        elif self._state_repo is not None:
            self._state_repo.rename_person(person_id, normalized_name)
        return True

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

    def set_group_order(self, group_ids: Iterable[str]) -> None:
        if self._state_repo is None:
            return
        self._state_repo.set_group_order(group_ids)

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
        *,
        operation_id: str | None = None,
    ) -> tuple[bool, dict[str, str | None]]:
        if not source_person_id or not target_person_id or source_person_id == target_person_id:
            return False, {}
        target_hidden = False
        if self._state_repo is not None:
            hidden_map = self._state_repo.get_person_hidden_map((source_person_id, target_person_id))
            target_hidden = bool(hidden_map.get(target_person_id, False))
        merged_hidden = target_hidden

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
            target_name = next(
                (
                    str(value)
                    for value in (
                        target_person["name"] if target_person is not None else None,
                        target_profile.name if target_profile is not None else None,
                        source_person["name"] if source_person is not None else None,
                        source_profile.name if source_profile is not None else None,
                    )
                    if value is not None and str(value).strip()
                ),
                None,
            )
            target_created_at = _utc_now_iso()
            if target_person is not None:
                target_created_at = target_person["created_at"]
            elif target_profile is not None:
                target_created_at = target_profile.created_at
            elif manual_target_faces:
                target_created_at = min(face.created_at for face in manual_target_faces)
            elif source_person is not None:
                target_created_at = source_person["created_at"]
            elif source_profile is not None:
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
            evidence_asset_count = len(
                {
                    *(face.asset_id for face in merged_faces if face.asset_id),
                    *(
                        face.asset_id
                        for face in (*manual_source_faces, *manual_target_faces)
                        if face.asset_id
                    ),
                }
            )
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
                        profile_state_for_sample_count(evidence_asset_count),
                    ),
                )
            else:
                conn.execute("DELETE FROM persons WHERE person_id = ?", (target_person_id,))
            conn.execute("DELETE FROM persons WHERE person_id = ?", (source_person_id,))
            if operation_id is not None:
                self._write_runtime_commit(
                    conn,
                    operation_id,
                    {
                        "operation_kind": "people_merge",
                        "source_person_id": source_person_id,
                        "target_person_id": target_person_id,
                        "target_name": target_name,
                        "target_created_at": target_created_at,
                        "sample_count": len(merged_faces),
                        "evidence_asset_count": evidence_asset_count,
                        "hidden_state": merged_hidden,
                        "changed_asset_ids": sorted(
                            {face.asset_id for face in merged_faces if face.asset_id}
                        ),
                        "changed_person_ids": [source_person_id, target_person_id],
                    },
                )
            conn.commit()

        if operation_id is not None:
            runtime_commit = self.complete_runtime_state_sync(operation_id)
            if runtime_commit is not None:
                group_redirects = {
                    str(key): (str(value) if value is not None else None)
                    for key, value in dict(
                        runtime_commit.get("group_redirects", {})
                    ).items()
                }
        elif self._state_repo is not None:
            group_redirects = self._state_repo.merge_persons(
                source_person_id,
                target_person_id,
                center_embedding=center_embedding,
                target_name=target_name,
                target_created_at=target_created_at,
                sample_count=len(merged_faces),
                evidence_asset_count=evidence_asset_count,
                hidden_state=merged_hidden,
            )
            self._sync_person_cover_defaults()
            self.refresh_all_group_assets()
        return True, group_redirects

    def delete_face(
        self,
        face_id: str,
        *,
        operation_id: str | None = None,
    ) -> FaceMutationResult | None:
        if not face_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                conn.execute("UPDATE faces SET person_id = NULL WHERE face_id = ?", (face_id,))
                if operation_id is not None:
                    self._write_runtime_commit(
                        conn,
                        operation_id,
                        {
                            "operation_kind": "people_delete_face",
                            "face_id": face.face_id,
                            "face_key": face.face_key,
                            "asset_id": face.asset_id,
                            "asset_rel": face.asset_rel,
                            "changed_asset_ids": [face.asset_id],
                            "changed_person_ids": (
                                [face.person_id] if face.person_id else []
                            ),
                        },
                    )
                conn.commit()
                if operation_id is not None:
                    runtime_commit = self.complete_runtime_state_sync(operation_id)
                    return self._mutation_result_from_commit(runtime_commit)
                elif self._state_repo is not None:
                    self._state_repo.clear_annotation_identity_assignment(
                        "person", face_id
                    )
                    self._state_repo.reject_face_key(
                        face.face_key,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                result = self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=((face.person_id,) if face.person_id else ()),
                )
                self.refresh_all_group_assets()
                return result

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None:
            return None
        if operation_id is not None:
            self.record_runtime_commit(
                operation_id,
                {
                    "operation_kind": "people_delete_face",
                    "face_id": face_id,
                    "asset_id": manual_face.asset_id,
                    "changed_asset_ids": [manual_face.asset_id],
                    "changed_person_ids": [manual_face.person_id],
                },
            )
            runtime_commit = self.complete_runtime_state_sync(operation_id)
            return self._mutation_result_from_commit(runtime_commit)
        else:
            self._state_repo.clear_annotation_identity_assignment("person", face_id)
            self._state_repo.delete_manual_face(face_id)
        result = self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id,),
        )
        self.refresh_all_group_assets()
        return result

    def has_face(self, face_id: str) -> bool:
        if not face_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM faces WHERE face_id = ?",
                (face_id,),
            ).fetchone()
        return bool(
            row is not None
            or (
                self._state_repo is not None
                and self._state_repo.get_manual_face(face_id) is not None
            )
        )

    def move_face_to_person(
        self,
        face_id: str,
        target_person_id: str,
        *,
        operation_id: str | None = None,
    ) -> FaceMutationResult | None:
        if not face_id or not target_person_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                if face.person_id == target_person_id:
                    return None
                conn.execute(
                    "UPDATE faces SET person_id = ? WHERE face_id = ?",
                    (target_person_id, face_id),
                )
                if operation_id is not None:
                    self._write_runtime_commit(
                        conn,
                        operation_id,
                        {
                            "operation_kind": "people_move_face",
                            "face_id": face.face_id,
                            "asset_id": face.asset_id,
                            "target_person_id": target_person_id,
                            "changed_asset_ids": [face.asset_id],
                            "changed_person_ids": [
                                value for value in (face.person_id, target_person_id) if value
                            ],
                        },
                    )
                conn.commit()
                if operation_id is not None:
                    runtime_commit = self.complete_runtime_state_sync(operation_id)
                    return self._mutation_result_from_commit(runtime_commit)
                elif self._state_repo is not None:
                    self._state_repo.assign_face_key(
                        face.face_key,
                        target_person_id,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                    self._state_repo.confirm_person(target_person_id)
                return self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=(
                        value for value in (face.person_id, target_person_id) if value
                    ),
                )

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None or manual_face.person_id == target_person_id:
            return None
        if operation_id is not None:
            self.record_runtime_commit(
                operation_id,
                {
                    "operation_kind": "people_move_face",
                    "face_id": face_id,
                    "asset_id": manual_face.asset_id,
                    "target_person_id": target_person_id,
                    "changed_asset_ids": [manual_face.asset_id],
                    "changed_person_ids": [manual_face.person_id, target_person_id],
                },
            )
            runtime_commit = self.complete_runtime_state_sync(operation_id)
            return self._mutation_result_from_commit(runtime_commit)
        elif not self._state_repo.move_manual_face(face_id, target_person_id):
            return None
        return self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id, target_person_id),
        )

    def move_face_to_new_person(
        self,
        face_id: str,
        new_person_id: str,
        new_name: str,
        *,
        operation_id: str | None = None,
    ) -> FaceMutationResult | None:
        normalized_name = _normalize_name(new_name)
        if not face_id or not new_person_id or not normalized_name:
            return None

        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                if face.person_id == new_person_id:
                    return None
                conn.execute(
                    "UPDATE faces SET person_id = ? WHERE face_id = ?",
                    (new_person_id, face_id),
                )
                if operation_id is not None:
                    self._write_runtime_commit(
                        conn,
                        operation_id,
                        {
                            "operation_kind": "people_move_face_new",
                            "face_id": face.face_id,
                            "asset_id": face.asset_id,
                            "new_person_id": new_person_id,
                            "new_name": normalized_name,
                            "changed_asset_ids": [face.asset_id],
                            "changed_person_ids": [
                                value for value in (face.person_id, new_person_id) if value
                            ],
                        },
                    )
                conn.commit()
                if operation_id is not None:
                    runtime_commit = self.complete_runtime_state_sync(operation_id)
                    return self._mutation_result_from_commit(runtime_commit)
                elif self._state_repo is not None:
                    self._state_repo.assign_face_key(
                        face.face_key,
                        new_person_id,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                    self._state_repo.upsert_person_profile(
                        new_person_id,
                        name_or_none=normalized_name,
                        created_at=face.detected_at,
                        center_embedding=face.embedding,
                        sample_count=1,
                        evidence_asset_count=1,
                    )
                    self._state_repo.confirm_person(new_person_id)
                return self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=(
                        value for value in (face.person_id, new_person_id) if value
                    ),
                )

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None or manual_face.person_id == new_person_id:
            return None
        if operation_id is not None:
            self.record_runtime_commit(
                operation_id,
                {
                    "operation_kind": "people_move_face_new",
                    "face_id": face_id,
                    "asset_id": manual_face.asset_id,
                    "new_person_id": new_person_id,
                    "new_name": normalized_name,
                    "created_at": manual_face.created_at,
                    "changed_asset_ids": [manual_face.asset_id],
                    "changed_person_ids": [manual_face.person_id, new_person_id],
                },
            )
            runtime_commit = self.complete_runtime_state_sync(operation_id)
            return self._mutation_result_from_commit(runtime_commit)
        else:
            self._state_repo.upsert_person_profile(
                new_person_id,
                name_or_none=normalized_name,
                created_at=manual_face.created_at,
            )
            if not self._state_repo.move_manual_face(face_id, new_person_id):
                return None
        return self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id, new_person_id),
        )

    def create_group(
        self,
        member_person_ids: Iterable[object],
        *,
        operation_id: str | None = None,
    ) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        members = tuple(member_person_ids)
        if operation_id is not None:
            self.record_runtime_commit(
                operation_id,
                {
                    "operation_kind": "people_create_group",
                    "members": [
                        member.key if hasattr(member, "key") else str(member)
                        for member in members
                        if member
                    ],
                    "changed_asset_ids": [],
                    "changed_person_ids": [],
                },
            )
            self.complete_runtime_state_sync(operation_id)
        group = self._state_repo.create_group(members)
        if group is not None:
            self.refresh_group_assets(group.group_id)
        return group

    def list_groups(self) -> list[PeopleGroupRecord]:
        if self._state_repo is None:
            return []
        self.initialize()
        return self._state_repo.list_groups()

    def delete_group(
        self,
        group_id: str,
        *,
        operation_id: str | None = None,
    ) -> tuple[bool, PeopleGroupRecord | None, list[str]]:
        if self._state_repo is None or not group_id:
            return False, None, []
        self.initialize()
        group = self._state_repo.get_group(group_id)
        if group is None:
            return False, None, []
        asset_ids = self.get_common_asset_ids_for_group(group_id)
        if operation_id is not None:
            self.record_runtime_commit(
                operation_id,
                {
                    "operation_kind": "people_delete_group",
                    "group_id": group_id,
                    "changed_asset_ids": list(asset_ids),
                    "changed_person_ids": list(group.member_person_ids),
                    "changed_group_ids": [group_id],
                },
            )
            self.complete_runtime_state_sync(operation_id)
            deleted_group = group if self._state_repo.get_group(group_id) is None else None
        else:
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

    def _common_asset_rows_for_group_members(
        self,
        members: Iterable[object],
    ) -> list[tuple[str, str]]:
        group_members = _unique_group_members(members)
        if len(group_members) < 2:
            return []
        effective_rows = self._effective_identity_asset_rows(group_members)
        per_member_assets = [
            effective_rows.get((member.kind, member.entity_id), {})
            for member in group_members
            if member.kind in {"person", "pet"}
        ]
        if not per_member_assets or any(not assets for assets in per_member_assets):
            return []
        common_ids = set(per_member_assets[0])
        for assets in per_member_assets[1:]:
            common_ids.intersection_update(assets)
        rows = [
            (asset_id, max(assets[asset_id] for assets in per_member_assets))
            for asset_id in common_ids
        ]
        rows.sort(key=lambda item: item[0])
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows

    def _person_asset_rows(self, person_id: str) -> dict[str, str]:
        if not person_id:
            return {}
        return self._effective_identity_asset_rows(
            (IdentityGroupMember("person", person_id),)
        ).get(("person", person_id), {})

    def _direct_person_asset_rows(self, person_id: str) -> dict[str, str]:
        if not person_id:
            return {}
        self.initialize()
        assets: dict[str, str] = {}
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
                assets[str(row["asset_id"])] = str(row["last_detected_at"])
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces_for_persons((person_id,)):
                previous = assets.get(face.asset_id)
                if previous is None or face.created_at > previous:
                    assets[face.asset_id] = face.created_at
        return assets

    def _pet_asset_rows(self, pet_id: str) -> dict[str, str]:
        if not pet_id:
            return {}
        return self._effective_identity_asset_rows(
            (IdentityGroupMember("pet", pet_id),)
        ).get(("pet", pet_id), {})

    def get_asset_ids_by_pets_effective(
        self, pet_ids: Iterable[str]
    ) -> dict[str, list[str]]:
        target_ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        rows = self._effective_identity_asset_rows(
            IdentityGroupMember("pet", pet_id) for pet_id in target_ids
        )
        return {
            pet_id: sorted(rows.get(("pet", pet_id), {}))
            for pet_id in target_ids
        }

    def _effective_identity_asset_rows(
        self, members: Iterable[IdentityGroupMember]
    ) -> dict[tuple[str, str], dict[str, str]]:
        """Aggregate assets by effective identity, honoring assignment before redirect."""

        requested = tuple(
            dict.fromkeys(
                (member.kind, member.entity_id)
                for member in members
                if member.kind in {"person", "pet"} and member.entity_id
            )
        )
        result = {identity: {} for identity in requested}
        if not requested:
            return result
        self.initialize()
        redirects = self._state_repo.get_identity_redirects() if self._state_repo else []
        redirected_sources = {
            (redirect.source_kind, redirect.source_id) for redirect in redirects
        }
        raw_to_target = {
            identity: identity
            for identity in requested
            if identity not in redirected_sources
        }
        for redirect in redirects:
            target = (redirect.target_kind, redirect.target_id)
            if target in result:
                raw_to_target[(redirect.source_kind, redirect.source_id)] = target

        direct_rows: dict[tuple[str, str], tuple[str, str]] = {}
        person_ids = tuple(identity_id for kind, identity_id in raw_to_target if kind == "person")
        if person_ids:
            with closing(self._connect()) as conn:
                for start in range(0, len(person_ids), 900):
                    chunk = person_ids[start : start + 900]
                    placeholders = ", ".join("?" for _ in chunk)
                    for row in conn.execute(
                        f"""
                        SELECT face_id, person_id, asset_id, detected_at
                        FROM faces WHERE person_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall():
                        if row["face_id"] and row["person_id"] and row["asset_id"]:
                            direct_rows[("person", str(row["face_id"]))] = (
                                str(row["asset_id"]), str(row["detected_at"] or "")
                            )
            if self._state_repo is not None:
                for face in self._state_repo.get_manual_faces_for_persons(person_ids):
                    direct_rows[("person", face.face_id)] = (face.asset_id, face.created_at)

        pet_ids = tuple(identity_id for kind, identity_id in raw_to_target if kind == "pet")
        pet_db_path = self._db_path.parent.parent / "pets" / "pet_index.db"
        if pet_ids and pet_db_path.exists():
            with closing(connect_sqlite(pet_db_path, check_same_thread=False)) as conn:
                conn.row_factory = sqlite3.Row
                configure_sqlite_connection(conn, pet_db_path, foreign_keys=True, wal=True)
                for start in range(0, len(pet_ids), 900):
                    chunk = pet_ids[start : start + 900]
                    placeholders = ", ".join("?" for _ in chunk)
                    for row in conn.execute(
                        f"""
                        SELECT detection_id, pet_id, asset_id, detected_at
                        FROM pet_detections WHERE pet_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall():
                        if row["detection_id"] and row["pet_id"] and row["asset_id"]:
                            direct_rows[("pet", str(row["detection_id"]))] = (
                                str(row["asset_id"]), str(row["detected_at"] or "")
                            )

        assignments = (
            self._state_repo.get_annotation_identity_assignments(direct_rows)
            if self._state_repo is not None else {}
        )
        source_identity_by_ref: dict[tuple[str, str], tuple[str, str]] = {}
        if direct_rows:
            face_ref_ids = {ref[1] for ref in direct_rows if ref[0] == "person"}
            pet_ref_ids = {ref[1] for ref in direct_rows if ref[0] == "pet"}
            if face_ref_ids:
                with closing(self._connect()) as conn:
                    for start in range(0, len(face_ref_ids), 900):
                        chunk = tuple(face_ref_ids)[start : start + 900]
                        placeholders = ", ".join("?" for _ in chunk)
                        for row in conn.execute(
                            f"""
                            SELECT face_id, person_id
                            FROM faces WHERE face_id IN ({placeholders})
                            """,
                            chunk,
                        ).fetchall():
                            if row["person_id"]:
                                source_identity_by_ref[("person", str(row["face_id"]))] = (
                                    "person", str(row["person_id"])
                                )
                if self._state_repo is not None:
                    for face in self._state_repo.get_manual_faces():
                        if face.face_id in face_ref_ids:
                            source_identity_by_ref[("person", face.face_id)] = (
                                "person", face.person_id
                            )
            if pet_ref_ids and pet_db_path.exists():
                with closing(connect_sqlite(pet_db_path, check_same_thread=False)) as conn:
                    conn.row_factory = sqlite3.Row
                    configure_sqlite_connection(conn, pet_db_path, foreign_keys=True, wal=True)
                    for start in range(0, len(pet_ref_ids), 900):
                        chunk = tuple(pet_ref_ids)[start : start + 900]
                        placeholders = ", ".join("?" for _ in chunk)
                        for row in conn.execute(
                            f"""
                            SELECT detection_id, pet_id
                            FROM pet_detections
                            WHERE detection_id IN ({placeholders})
                            """,
                            chunk,
                        ).fetchall():
                            if row["pet_id"]:
                                source_identity_by_ref[("pet", str(row["detection_id"]))] = (
                                    "pet", str(row["pet_id"])
                                )

        for ref, (asset_id, last_seen) in direct_rows.items():
            if ref in assignments:
                continue
            target = raw_to_target.get(source_identity_by_ref.get(ref, ("", "")))
            if target in result:
                previous = result[target].get(asset_id)
                if previous is None or last_seen > previous:
                    result[target][asset_id] = last_seen

        targeted = (
            self._state_repo.get_annotation_identity_assignments_for_targets(requested)
            if self._state_repo is not None else {}
        )
        missing_refs = tuple(ref for ref in targeted if ref not in direct_rows)
        if missing_refs:
            direct_rows.update(self._annotation_asset_rows(missing_refs, pet_db_path))
        for ref, target in targeted.items():
            row = direct_rows.get(ref)
            if row is None or target not in result:
                continue
            asset_id, last_seen = row
            previous = result[target].get(asset_id)
            if previous is None or last_seen > previous:
                result[target][asset_id] = last_seen
        return result

    def _annotation_asset_rows(
        self,
        refs: Iterable[tuple[str, str]],
        pet_db_path: Path,
    ) -> dict[tuple[str, str], tuple[str, str]]:
        requested = tuple(dict.fromkeys(refs))
        result: dict[tuple[str, str], tuple[str, str]] = {}
        face_ids = tuple(annotation_id for kind, annotation_id in requested if kind == "person")
        if face_ids:
            with closing(self._connect()) as conn:
                for start in range(0, len(face_ids), 900):
                    chunk = face_ids[start : start + 900]
                    placeholders = ", ".join("?" for _ in chunk)
                    for row in conn.execute(
                        f"""
                        SELECT face_id, asset_id, detected_at
                        FROM faces WHERE face_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall():
                        result[("person", str(row["face_id"]))] = (
                            str(row["asset_id"]), str(row["detected_at"] or "")
                        )
            if self._state_repo is not None:
                face_id_set = set(face_ids)
                for face in self._state_repo.get_manual_faces():
                    if face.face_id in face_id_set:
                        result[("person", face.face_id)] = (face.asset_id, face.created_at)
        detection_ids = tuple(annotation_id for kind, annotation_id in requested if kind == "pet")
        if detection_ids and pet_db_path.exists():
            with closing(connect_sqlite(pet_db_path, check_same_thread=False)) as conn:
                conn.row_factory = sqlite3.Row
                configure_sqlite_connection(conn, pet_db_path, foreign_keys=True, wal=True)
                for start in range(0, len(detection_ids), 900):
                    chunk = detection_ids[start : start + 900]
                    placeholders = ", ".join("?" for _ in chunk)
                    for row in conn.execute(
                        f"""
                        SELECT detection_id, asset_id, detected_at
                        FROM pet_detections WHERE detection_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall():
                        result[("pet", str(row["detection_id"]))] = (
                            str(row["asset_id"]), str(row["detected_at"] or "")
                        )
        return result

    def _direct_pet_asset_rows(self, pet_id: str) -> dict[str, str]:
        if not pet_id:
            return {}
        pet_db_path = self._db_path.parent.parent / "pets" / "pet_index.db"
        if not pet_db_path.exists():
            return {}
        with closing(connect_sqlite(pet_db_path, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            configure_sqlite_connection(conn, pet_db_path, foreign_keys=True, wal=True)
            rows = conn.execute(
                """
                SELECT asset_id, MAX(detected_at) AS last_detected_at
                FROM pet_detections
                WHERE pet_id = ?
                GROUP BY asset_id
                ORDER BY last_detected_at DESC, asset_id ASC
                """,
                (pet_id,),
            ).fetchall()
        return {
            str(row["asset_id"]): str(row["last_detected_at"])
            for row in rows
            if row["asset_id"]
        }

    def _direct_pet_asset_rows_by_ids(
        self,
        pet_ids: Iterable[str],
    ) -> dict[str, dict[str, str]]:
        ids = tuple(dict.fromkeys(str(value) for value in pet_ids if value))
        result = {pet_id: {} for pet_id in ids}
        pet_db_path = self._db_path.parent.parent / "pets" / "pet_index.db"
        if not ids or not pet_db_path.exists():
            return result
        with closing(connect_sqlite(pet_db_path, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            configure_sqlite_connection(conn, pet_db_path, foreign_keys=True, wal=True)
            for start in range(0, len(ids), 900):
                chunk = ids[start : start + 900]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT pet_id, asset_id, MAX(detected_at) AS last_detected_at
                    FROM pet_detections
                    WHERE pet_id IN ({placeholders})
                    GROUP BY pet_id, asset_id
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    if row["pet_id"] and row["asset_id"]:
                        result[str(row["pet_id"])][str(row["asset_id"])] = str(
                            row["last_detected_at"] or ""
                        )
        return result

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
        asset_rows = self._common_asset_rows_for_group_members(group.member_entities)
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

    def _finalize_face_mutation(
        self,
        *,
        changed_asset_ids: Iterable[str],
        changed_person_ids: Iterable[str],
    ) -> FaceMutationResult:
        person_ids = tuple(dict.fromkeys(person_id for person_id in changed_person_ids if person_id))
        asset_ids = set(asset_id for asset_id in changed_asset_ids if asset_id)
        group_redirects: dict[str, str | None] = {}
        changed_group_ids: set[str] = set()
        active_person_ids: list[str] = []

        if self._state_repo is not None and person_ids:
            changed_group_ids.update(self._state_repo.list_group_ids_for_people(person_ids))

        for person_id in person_ids:
            if self._rebuild_runtime_person(person_id):
                active_person_ids.append(person_id)
                continue
            if self._state_repo is not None:
                group_redirects.update(self._state_repo.remove_person_from_groups(person_id))
                self._state_repo.delete_person_state(person_id)

        if self._state_repo is not None:
            for person_id in active_person_ids:
                self._repair_person_cover(person_id)
            self._sync_person_cover_defaults()
            remaining_group_ids = set(self._state_repo.list_group_ids_for_people(active_person_ids))
            changed_group_ids.update(remaining_group_ids)
            changed_group_ids.update(group_redirects)
            changed_group_ids.update(group_id for group_id in group_redirects.values() if group_id)
            for group_id in changed_group_ids:
                self.refresh_group_assets(group_id)

        for person_id in active_person_ids:
            asset_ids.update(self.get_asset_ids_by_person(person_id))

        return FaceMutationResult(
            changed_asset_ids=tuple(sorted(asset_ids)),
            changed_person_ids=person_ids,
            changed_group_ids=tuple(sorted(group_id for group_id in changed_group_ids if group_id)),
            group_redirects=group_redirects,
        )

    @staticmethod
    def _mutation_result_from_commit(
        payload: dict[str, object] | None,
    ) -> FaceMutationResult:
        values = payload or {}
        return FaceMutationResult(
            changed_asset_ids=tuple(
                str(value) for value in values.get("changed_asset_ids", ()) if value
            ),
            changed_person_ids=tuple(
                str(value) for value in values.get("changed_person_ids", ()) if value
            ),
            changed_group_ids=tuple(
                str(value) for value in values.get("changed_group_ids", ()) if value
            ),
            person_redirects={
                str(key): str(value)
                for key, value in dict(values.get("person_redirects", {})).items()
                if value is not None
            },
            group_redirects={
                str(key): (str(value) if value is not None else None)
                for key, value in dict(values.get("group_redirects", {})).items()
            },
        )

    def _rebuild_runtime_person(self, person_id: str) -> bool:
        if not person_id:
            return False

        self.initialize()
        profile = None
        manual_faces: list[ManualFaceRecord] = []
        if self._state_repo is not None:
            manual_faces = self._state_repo.get_manual_faces_for_persons((person_id,))
            profile = self._state_repo.get_profile(person_id)

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE person_id = ?
                ORDER BY detected_at ASC, face_id ASC
                """,
                (person_id,),
            ).fetchall()
            auto_faces = [self._face_from_row(row) for row in rows]
            existing_person = conn.execute(
                """
                SELECT person_id, name, created_at
                FROM persons
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()

            if not auto_faces:
                conn.execute("DELETE FROM persons WHERE person_id = ?", (person_id,))
                conn.commit()
                return bool(manual_faces)

            name = existing_person["name"] if existing_person is not None else None
            if name is None and profile is not None:
                name = profile.name
            created_at = existing_person["created_at"] if existing_person is not None else None
            if created_at is None and profile is not None:
                created_at = profile.created_at
            if created_at is None and manual_faces:
                created_at = min(face.created_at for face in manual_faces)
            if created_at is None:
                created_at = min(face.detected_at for face in auto_faces)

            key_face = max(auto_faces, key=_key_face_sort_key)
            center_embedding = compute_cluster_center(
                np.stack([face.embedding for face in auto_faces], axis=0)
            )
            sample_count = len(auto_faces)
            evidence_asset_count = len(
                {
                    *(face.asset_id for face in auto_faces if face.asset_id),
                    *(face.asset_id for face in manual_faces if face.asset_id),
                }
            )
            updated_at = _utc_now_iso()
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
                    person_id,
                    _normalize_name(name),
                    key_face.face_id,
                    sample_count,
                    _serialize_embedding(center_embedding),
                    created_at,
                    updated_at,
                    sample_count,
                    profile_state_for_sample_count(evidence_asset_count),
                ),
            )
            conn.commit()

        if self._state_repo is not None:
            self._state_repo.upsert_person_profile(
                person_id,
                name_or_none=name,
                created_at=created_at,
                center_embedding=center_embedding,
                sample_count=sample_count,
                evidence_asset_count=evidence_asset_count,
            )
        return True

    def _repair_person_cover(self, person_id: str) -> None:
        if self._state_repo is None or not person_id:
            return

        manual_faces = self._state_repo.get_manual_faces_for_persons((person_id,))
        has_auto_faces = self._has_auto_faces(person_id)
        cover = self._state_repo.get_person_cover(person_id)
        if cover is None:
            if manual_faces and not has_auto_faces:
                first_face = manual_faces[0]
                self._state_repo.set_person_cover(
                    person_id,
                    face_id=first_face.face_id,
                    face_key=None,
                    asset_id=first_face.asset_id,
                    thumbnail_path=first_face.thumbnail_path,
                )
            return

        valid_auto_faces = {face.face_id: face for face in self.get_faces_by_asset_id(cover.asset_id or "")}
        valid_manual_faces = {face.face_id: face for face in manual_faces}
        auto_face = valid_auto_faces.get(cover.face_id or "")
        if auto_face is not None and auto_face.person_id == person_id:
            return
        if cover.face_id in valid_manual_faces:
            return

        if valid_manual_faces and not has_auto_faces:
            fallback = next(iter(valid_manual_faces.values()))
            self._state_repo.set_person_cover(
                person_id,
                face_id=fallback.face_id,
                face_key=None,
                asset_id=fallback.asset_id,
                thumbnail_path=fallback.thumbnail_path,
            )
            return
        self._state_repo.delete_person_cover(person_id)

    def _has_auto_faces(self, person_id: str) -> bool:
        if not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM faces WHERE person_id = ? LIMIT 1",
                (person_id,),
            ).fetchone()
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn, self._db_path, foreign_keys=True, wal=True)
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS people_runtime_commits (
                operation_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                state_synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

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

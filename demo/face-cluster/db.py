from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class FaceRecord:
    face_id: str
    asset_rel: str
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    confidence: float
    embedding: np.ndarray
    embedding_dim: int
    thumbnail_path: str | None
    person_id: str | None
    detected_at: str
    image_width: int
    image_height: int


@dataclass
class PersonRecord:
    person_id: str
    key_face_id: str
    face_count: int
    center_embedding: np.ndarray
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PersonSummary:
    person_id: str
    key_face_id: str
    face_count: int
    thumbnail_path: Path | None


@dataclass(frozen=True)
class RuntimeWorkspace:
    root_path: Path
    db_path: Path
    thumbnail_dir: Path


def prepare_runtime_workspace(
    source_dir: Path,
    runtime_root: Path | None = None,
) -> RuntimeWorkspace:
    runtime_root = (runtime_root or Path(__file__).resolve().parent / "runtime").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)

    folder_hash = hashlib.sha1(
        str(source_dir.resolve()).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]
    workspace_root = (runtime_root / folder_hash).resolve()
    _ensure_within(workspace_root, runtime_root)

    if workspace_root.exists():
        shutil.rmtree(workspace_root)

    thumbnail_dir = workspace_root / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeWorkspace(
        root_path=workspace_root,
        db_path=workspace_root / "face_index.db",
        thumbnail_dir=thumbnail_dir,
    )


class FaceClusterRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)

    def replace_all(self, faces: list[FaceRecord], persons: list[PersonRecord]) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM persons")
            conn.execute("DELETE FROM faces")
            conn.executemany(
                """
                INSERT INTO faces (
                    face_id,
                    asset_rel,
                    box_x,
                    box_y,
                    box_w,
                    box_h,
                    confidence,
                    embedding,
                    embedding_dim,
                    thumbnail_path,
                    person_id,
                    detected_at,
                    image_width,
                    image_height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        face.face_id,
                        face.asset_rel,
                        face.box_x,
                        face.box_y,
                        face.box_w,
                        face.box_h,
                        face.confidence,
                        face.embedding.astype(np.float32).tobytes(),
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
                    person_id,
                    key_face_id,
                    face_count,
                    center_embedding,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        person.person_id,
                        person.key_face_id,
                        person.face_count,
                        person.center_embedding.astype(np.float32).tobytes(),
                        person.created_at,
                        person.updated_at,
                    )
                    for person in persons
                ],
            )
            conn.commit()

    def get_person_summaries(self) -> list[PersonSummary]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    persons.person_id,
                    persons.key_face_id,
                    persons.face_count,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.face_count DESC, persons.created_at ASC
                """
            ).fetchall()

        summaries: list[PersonSummary] = []
        for row in rows:
            thumbnail_path = row["thumbnail_path"]
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / thumbnail_path).resolve()
            summaries.append(
                PersonSummary(
                    person_id=row["person_id"],
                    key_face_id=row["key_face_id"],
                    face_count=int(row["face_count"]),
                    thumbnail_path=resolved_thumbnail,
                )
            )
        return summaries

    def get_faces_by_person(self, person_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id,
                    asset_rel,
                    box_x,
                    box_y,
                    box_w,
                    box_h,
                    confidence,
                    embedding,
                    embedding_dim,
                    thumbnail_path,
                    person_id,
                    detected_at,
                    image_width,
                    image_height
                FROM faces
                WHERE person_id = ?
                ORDER BY confidence DESC, (box_w * box_h) DESC, detected_at ASC
                """,
                (person_id,),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            display_row = dict(row)
            embedding_blob = row["embedding"] or b""
            display_row["embedding_bytes"] = len(embedding_blob)
            display_row["embedding"] = f"<float32[{row['embedding_dim']}]>"
            results.append(display_row)
        return results

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                face_id TEXT PRIMARY KEY,
                asset_rel TEXT NOT NULL,
                box_x INTEGER NOT NULL,
                box_y INTEGER NOT NULL,
                box_w INTEGER NOT NULL,
                box_h INTEGER NOT NULL,
                confidence REAL NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                thumbnail_path TEXT,
                -- person_id is a denormalized back-reference; no FK declared here
                -- to avoid a circular dependency with persons.key_face_id REFERENCES faces.
                person_id TEXT,
                detected_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                key_face_id TEXT NOT NULL REFERENCES faces(face_id),
                face_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_faces_asset_rel ON faces(asset_rel)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_persons_face_count ON persons(face_count DESC)"
        )


def _ensure_within(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Workspace path escaped runtime root: {candidate}") from exc

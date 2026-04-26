from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
    face_key: str = ""


@dataclass
class PersonRecord:
    person_id: str
    key_face_id: str
    face_count: int
    center_embedding: np.ndarray
    created_at: str
    updated_at: str
    name: str | None = None


@dataclass(frozen=True)
class PersonSummary:
    person_id: str
    name: str | None
    key_face_id: str
    face_count: int
    thumbnail_path: Path | None
    created_at: str


@dataclass(frozen=True)
class PersonProfile:
    person_id: str
    name: str | None
    center_embedding: np.ndarray
    embedding_dim: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RuntimeWorkspace:
    root_path: Path
    db_path: Path
    state_db_path: Path
    thumbnail_dir: Path


def prepare_runtime_workspace(
    source_dir: Path,
    runtime_root: Path | None = None,
    state_root: Path | None = None,
) -> RuntimeWorkspace:
    base_dir = Path(__file__).resolve().parent
    runtime_root = (runtime_root or base_dir / "runtime").resolve()
    state_root = (state_root or base_dir / "state").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)

    folder_hash = compute_workspace_hash(source_dir)
    workspace_root = (runtime_root / folder_hash).resolve()
    state_dir = (state_root / folder_hash).resolve()
    _ensure_within(workspace_root, runtime_root)
    _ensure_within(state_dir, state_root)

    if workspace_root.exists():
        shutil.rmtree(workspace_root)

    state_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_dir = workspace_root / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeWorkspace(
        root_path=workspace_root,
        db_path=workspace_root / "face_index.db",
        state_db_path=state_dir / "face_cluster_state.db",
        thumbnail_dir=thumbnail_dir,
    )


def compute_workspace_hash(source_dir: Path) -> str:
    return hashlib.sha1(
        str(source_dir.resolve()).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]


class FaceClusterRepository:
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = (
            FaceClusterStateRepository(state_db_path) if state_db_path is not None else None
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

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
                    face_id,
                    face_key,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        face.face_id,
                        face.face_key or face.face_id,
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
                    person_id,
                    name,
                    key_face_id,
                    face_count,
                    center_embedding,
                    created_at,
                    updated_at
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

    def get_person_summaries(self) -> list[PersonSummary]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
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
                    name=row["name"],
                    key_face_id=row["key_face_id"],
                    face_count=int(row["face_count"]),
                    thumbnail_path=resolved_thumbnail,
                    created_at=row["created_at"],
                )
            )
        return summaries

    def get_faces_by_person(self, person_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id,
                    face_key,
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

    def merge_persons(self, source_person_id: str, target_person_id: str) -> None:
        if source_person_id == target_person_id:
            return

        self.initialize()
        with closing(self._connect()) as conn:
            person_rows = conn.execute(
                """
                SELECT person_id, name, created_at
                FROM persons
                WHERE person_id IN (?, ?)
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            if len(person_rows) != 2:
                raise ValueError("Could not locate both source and target persons for merge.")

            target_row = next(row for row in person_rows if row["person_id"] == target_person_id)
            face_rows = conn.execute(
                """
                SELECT
                    face_id,
                    confidence,
                    box_w,
                    box_h,
                    embedding,
                    embedding_dim
                FROM faces
                WHERE person_id IN (?, ?)
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            if not face_rows:
                raise ValueError("Could not locate any faces for the requested merge.")

            key_face_row = max(
                face_rows,
                key=lambda row: (float(row["confidence"]), int(row["box_w"]) * int(row["box_h"])),
            )
            center_embedding = compute_cluster_center(
                np.stack(
                    [
                        _deserialize_embedding(row["embedding"], int(row["embedding_dim"]))
                        for row in face_rows
                    ],
                    axis=0,
                )
            )
            updated_at = _utc_now_iso()

            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )
            conn.execute(
                """
                UPDATE persons
                SET key_face_id = ?, face_count = ?, center_embedding = ?, updated_at = ?
                WHERE person_id = ?
                """,
                (
                    key_face_row["face_id"],
                    len(face_rows),
                    _serialize_embedding(center_embedding),
                    updated_at,
                    target_person_id,
                ),
            )
            conn.execute("DELETE FROM persons WHERE person_id = ?", (source_person_id,))
            conn.commit()

        if self._state_repo is not None:
            self._state_repo.merge_persons(
                source_person_id,
                target_person_id,
                center_embedding=center_embedding,
                target_name=target_row["name"],
                target_created_at=target_row["created_at"],
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                face_id TEXT PRIMARY KEY,
                face_key TEXT NOT NULL,
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
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                key_face_id TEXT NOT NULL REFERENCES faces(face_id),
                face_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_face_key ON faces(face_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_asset_rel ON faces(asset_rel)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_persons_face_count ON persons(face_count DESC)"
        )


class FaceClusterStateRepository:
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
            rows = conn.execute(
                """
                SELECT
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at
                FROM person_profiles
                """
            ).fetchall()

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
        if not persons and not faces:
            return

        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_profiles (
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at
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
                    face_key,
                    person_id,
                    asset_rel,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(face_key) DO UPDATE SET
                    person_id = excluded.person_id,
                    asset_rel = excluded.asset_rel,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        face.face_key,
                        face.person_id,
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
        normalized_name = _normalize_name(name_or_none)
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (
                    person_id,
                    normalized_name,
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
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at
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
            CREATE TABLE IF NOT EXISTS person_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                center_embedding BLOB,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_keys (
                face_key TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_profiles_name ON person_profiles(name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_person_id ON face_keys(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_asset_rel ON face_keys(asset_rel)")


def compute_cluster_center(embeddings: np.ndarray) -> np.ndarray:
    if embeddings.size == 0:
        return np.empty((0,), dtype=np.float32)
    center = embeddings.mean(axis=0).astype(np.float32)
    return normalize_vector(center)


def normalize_vector(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).flatten()
    if vector.size == 0:
        return vector
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector
    return (vector / norm).astype(np.float32)


def _serialize_embedding(embedding: np.ndarray) -> sqlite3.Binary:
    vector = normalize_vector(embedding)
    return sqlite3.Binary(vector.astype(np.float32).tobytes())


def _deserialize_embedding(blob: bytes | None, embedding_dim: int) -> np.ndarray:
    if not blob or embedding_dim <= 0:
        return np.empty((0,), dtype=np.float32)
    return np.frombuffer(blob, dtype=np.float32, count=embedding_dim).copy()


def _normalize_name(name_or_none: str | None) -> str | None:
    if name_or_none is None:
        return None
    normalized = str(name_or_none).strip()
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_within(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Workspace path escaped root: {candidate}") from exc

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from iPhoto.people.pipeline import DetectedAssetFaces
from iPhoto.people.repository import FaceRecord, FaceRepository, FaceStateRepository, PersonRecord
from iPhoto.people.scan_session import FaceScanSession


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _face_record(
    *,
    face_id: str,
    asset_id: str,
    asset_rel: str,
    person_id: str | None,
    thumbnail_path: str | None = None,
    embedding: np.ndarray | None = None,
    face_key: str | None = None,
    is_manual: bool = False,
) -> FaceRecord:
    if embedding is None:
        embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    return FaceRecord(
        face_id=face_id,
        face_key=face_key or f"key-{face_id}",
        asset_id=asset_id,
        asset_rel=asset_rel,
        box_x=10,
        box_y=12,
        box_w=80,
        box_h=80,
        confidence=0.99,
        embedding=embedding,
        embedding_dim=int(embedding.shape[0]),
        thumbnail_path=thumbnail_path,
        person_id=person_id,
        detected_at=_now_iso(),
        image_width=400,
        image_height=300,
        is_manual=is_manual,
    )


def _person_record(
    *,
    person_id: str,
    key_face_id: str,
    face_count: int,
    name: str | None = "Alice",
    embedding: np.ndarray | None = None,
) -> PersonRecord:
    if embedding is None:
        embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    timestamp = _now_iso()
    sample_count = int(face_count)
    return PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=sample_count,
        profile_state="stable" if sample_count >= 3 else "unstable",
    )


def test_remove_faces_for_assets_deletes_affected_person_rows_first(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db")
    face_a = _face_record(
        face_id="face-a",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-a",
    )
    face_b = _face_record(
        face_id="face-b",
        asset_id="asset-b",
        asset_rel="album/b.jpg",
        person_id="person-a",
    )
    person = _person_record(person_id="person-a", key_face_id="face-a", face_count=2)
    repository.replace_all([face_a, face_b], [person])

    repository.remove_faces_for_assets(["asset-a"], ["album/a.jpg"])

    remaining_faces = repository.get_all_faces()
    assert [face.face_id for face in remaining_faces] == ["face-b"]
    assert repository.get_person_summaries() == []


def test_person_cover_persists_and_custom_cover_survives_rescan(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-default",
            asset_id="asset-default",
            asset_rel="album/default.jpg",
            person_id="person-a",
            thumbnail_path="thumbnails/default.jpg",
        ),
        _face_record(
            face_id="face-custom",
            asset_id="asset-custom",
            asset_rel="album/custom.jpg",
            person_id="person-a",
            thumbnail_path="thumbnails/custom.jpg",
        ),
    ]
    person = _person_record(
        person_id="person-a",
        key_face_id="face-default",
        face_count=2,
        name="Alice",
    )
    repository.replace_all(faces, [person])

    summaries = repository.get_person_summaries()
    assert summaries[0].thumbnail_path == (tmp_path / "thumbnails/default.jpg").resolve()
    assert repository.state_repository is not None
    assert repository.state_repository.get_person_cover_thumbnail_map(["person-a"]) == {
        "person-a": "thumbnails/default.jpg"
    }

    assert repository.set_person_cover("person-a", "face-custom") is True
    assert (
        repository.get_person_summaries()[0].thumbnail_path
        == (tmp_path / "thumbnails/custom.jpg").resolve()
    )


def test_set_person_cover_from_asset_uses_matching_face_on_asset(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            thumbnail_path="thumbnails/a.jpg",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            thumbnail_path="thumbnails/b.jpg",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
    ]
    repository.replace_all(faces, persons)

    assert repository.set_person_cover_from_asset("person-b", "asset-shared") is True
    assert repository.state_repository is not None
    assert repository.state_repository.get_person_cover_thumbnail_map(["person-b"]) == {
        "person-b": "thumbnails/b.jpg"
    }
    assert repository.set_person_cover_from_asset("person-a", "asset-missing") is False


def test_hidden_person_ids_persist_and_follow_merge(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
    ]
    repository.replace_all(faces, persons)

    assert repository.set_person_hidden("person-a", True) is True
    assert repository.get_hidden_person_ids(["person-a", "person-b"]) == {"person-a"}

    merged, _group_redirects = repository.merge_persons_with_redirects("person-a", "person-b")

    assert merged is True
    assert repository.get_hidden_person_ids(["person-a", "person-b"]) == {"person-b"}


def test_person_card_order_persists_across_reload(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
    ]
    repository.replace_all(faces, persons)

    repository.set_person_order(["person-b", "person-a"])

    ordered = repository.get_person_summaries()
    assert [summary.person_id for summary in ordered] == ["person-b", "person-a"]

    repository.replace_all(faces, persons)
    persisted = repository.get_person_summaries()
    assert [summary.person_id for summary in persisted] == ["person-b", "person-a"]


def test_people_groups_persist_and_query_common_assets(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a-shared-1",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-a-shared-2",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-a-only",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-only",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared-1",
            face_count=3,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=2,
            name="Bob",
        ),
    ]
    repository.replace_all(faces, persons)

    group = repository.create_group(["person-a", "person-b", "person-a"])
    assert group is not None
    assert group.member_person_ids == ("person-a", "person-b")
    assert repository.state_repository is not None
    assert repository.state_repository.has_group_asset_cache(group.group_id) is True
    assert repository.state_repository.get_group_asset_ids(group.group_id) == ["asset-shared"]

    duplicate = repository.create_group(["person-b", "person-a"])
    assert duplicate is not None
    assert duplicate.group_id == group.group_id
    assert duplicate.member_person_ids == ("person-a", "person-b")

    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-shared"]
    assert repository.get_group_cover_asset_id(group.group_id) == "asset-shared"

    updated_faces = [
        _face_record(
            face_id="face-a-updated-shared",
            asset_id="asset-updated-shared",
            asset_rel="album/updated-shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-updated-shared",
            asset_id="asset-updated-shared",
            asset_rel="album/updated-shared.jpg",
            person_id="person-b",
        ),
    ]
    updated_persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-updated-shared",
            face_count=1,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-updated-shared",
            face_count=1,
            name="Bob",
        ),
    ]
    repository.replace_all(updated_faces, updated_persons)
    persisted = repository.list_groups()
    assert len(persisted) == 1
    assert persisted[0].group_id == group.group_id
    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-updated-shared"]


def test_group_cover_can_be_customized_without_rescan_overwrite(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a-older",
            asset_id="asset-older",
            asset_rel="album/older.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-older",
            asset_id="asset-older",
            asset_rel="album/older.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-a-newer",
            asset_id="asset-newer",
            asset_rel="album/newer.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-newer",
            asset_id="asset-newer",
            asset_rel="album/newer.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-newer",
            face_count=2,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-newer",
            face_count=2,
            name="Bob",
        ),
    ]
    repository.replace_all(faces, persons)

    group = repository.create_group(["person-a", "person-b"])
    assert group is not None
    assert repository.get_common_asset_ids_for_group(group.group_id) == [
        "asset-newer",
        "asset-older",
    ]
    assert repository.get_group_cover_asset_id(group.group_id) == "asset-newer"

    assert repository.set_group_cover_asset(group.group_id, "asset-older") is True
    assert repository.get_group_cover_asset_id(group.group_id) == "asset-older"

    repository.replace_all(faces, persons)
    assert repository.get_group_cover_asset_id(group.group_id) == "asset-older"


def test_delete_group_removes_group_and_cached_assets(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
    ]
    repository.replace_all(faces, persons)

    group = repository.create_group(["person-a", "person-b"])
    assert group is not None
    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-shared"]

    assert repository.delete_group(group.group_id) is True
    assert repository.get_group(group.group_id) is None
    assert repository.list_groups() == []
    assert repository.get_common_asset_ids_for_group(group.group_id) == []


def test_merge_persons_rewrites_group_memberships_and_deduplicates_groups(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a-ab",
            asset_id="asset-ab",
            asset_rel="album/ab.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-ab",
            asset_id="asset-ab",
            asset_rel="album/ab.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-b-bc",
            asset_id="asset-bc",
            asset_rel="album/bc.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-c-bc",
            asset_id="asset-bc",
            asset_rel="album/bc.jpg",
            person_id="person-c",
            embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a-ab", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b-ab", face_count=2, name="Bob"),
        _person_record(
            person_id="person-c",
            key_face_id="face-c-bc",
            face_count=1,
            name="Carol",
            embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        ),
    ]
    repository.replace_all(faces, persons)

    group_ab = repository.create_group(["person-a", "person-b"])
    group_bc = repository.create_group(["person-b", "person-c"])
    assert group_ab is not None
    assert group_bc is not None

    merged, group_redirects = repository.merge_persons_with_redirects("person-a", "person-c")

    assert merged is True
    assert group_redirects[group_ab.group_id] == group_bc.group_id
    groups = repository.list_groups()
    assert len(groups) == 1
    assert groups[0].group_id == group_bc.group_id
    assert groups[0].member_person_ids == ("person-b", "person-c")
    assert repository.get_common_asset_ids_for_group(group_bc.group_id) == [
        "asset-bc",
        "asset-ab",
    ]


def test_list_asset_face_annotations_returns_only_matching_asset(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name=None),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
    ]
    repository.replace_all(faces, persons)

    annotations = repository.list_asset_face_annotations("asset-a")

    assert len(annotations) == 1
    assert annotations[0].face_id == "face-a"
    assert annotations[0].person_id == "person-a"
    assert annotations[0].display_name is None
    assert annotations[0].box_w == 80
    assert annotations[0].image_width == 400


def test_face_scan_session_leaves_runtime_snapshot_unchanged_until_commit(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
            face_key="face-key-a-shared",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
            face_key="face-key-b-shared",
        ),
    ]
    initial_persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)

    group = repository.create_group(["person-a", "person-b"])
    assert group is not None

    session = FaceScanSession()
    done_ids, retry_ids = session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-new-shared",
                asset_rel="album/new-shared.jpg",
                faces=[
                    _face_record(
                        face_id="face-a-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.98, 0.02, 0.0], dtype=np.float32),
                        face_key="face-key-a-new-shared",
                    ),
                    _face_record(
                        face_id="face-b-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.02, 0.98, 0.0], dtype=np.float32),
                        face_key="face-key-b-new-shared",
                    ),
                ],
            )
        ]
    )

    assert done_ids == ["asset-new-shared"]
    assert retry_ids == []
    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-shared"]

    session.commit(repository, distance_threshold=0.6, min_samples=2)

    summaries = repository.get_person_summaries()
    assert {summary.person_id for summary in summaries} == {"person-a", "person-b"}
    assert repository.get_common_asset_ids_for_group(group.group_id) == [
        "asset-new-shared",
        "asset-shared",
    ]


def test_face_scan_session_preserves_group_id_and_custom_cover_after_commit(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
            face_key="face-key-a-shared",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
            face_key="face-key-b-shared",
        ),
    ]
    initial_persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)

    group = repository.create_group(["person-a", "person-b"])
    assert group is not None
    assert repository.set_group_cover_asset(group.group_id, "asset-shared") is True

    session = FaceScanSession()
    session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-new-shared",
                asset_rel="album/new-shared.jpg",
                faces=[
                    _face_record(
                        face_id="face-a-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.96, 0.04, 0.0], dtype=np.float32),
                        face_key="face-key-a-new-shared",
                    ),
                    _face_record(
                        face_id="face-b-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.04, 0.96, 0.0], dtype=np.float32),
                        face_key="face-key-b-new-shared",
                    ),
                ],
            )
        ]
    )

    session.commit(repository, distance_threshold=0.6, min_samples=2)

    groups = repository.list_groups()
    assert len(groups) == 1
    assert groups[0].group_id == group.group_id
    assert repository.get_group_cover_asset_id(group.group_id) == "asset-shared"


def test_face_scan_session_rolls_back_runtime_snapshot_when_runtime_state_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
            face_key="face-key-a-shared",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
            face_key="face-key-b-shared",
        ),
    ]
    initial_persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)
    group = repository.create_group(["person-a", "person-b"])
    assert group is not None

    session = FaceScanSession()
    session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-new-shared",
                asset_rel="album/new-shared.jpg",
                faces=[
                    _face_record(
                        face_id="face-a-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.97, 0.03, 0.0], dtype=np.float32),
                        face_key="face-key-a-new-shared",
                    ),
                    _face_record(
                        face_id="face-b-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.03, 0.97, 0.0], dtype=np.float32),
                        face_key="face-key-b-new-shared",
                    ),
                ],
            )
        ]
    )

    original_sync_runtime_state = repository.sync_runtime_state
    call_count = {"value": 0}

    def fail_once() -> None:
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("cache refresh failed")
        original_sync_runtime_state()

    monkeypatch.setattr(repository, "sync_runtime_state", fail_once)

    with pytest.raises(RuntimeError, match="cache refresh failed"):
        session.commit(repository, distance_threshold=0.6, min_samples=2)

    assert [summary.person_id for summary in repository.get_person_summaries()] == [
        "person-a",
        "person-b",
    ]
    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-shared"]


def test_face_scan_session_rolls_back_runtime_snapshot_when_profile_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
            face_key="face-key-a-shared",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
            face_key="face-key-b-shared",
        ),
    ]
    initial_persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)
    group = repository.create_group(["person-a", "person-b"])
    assert group is not None

    session = FaceScanSession()
    session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-new-shared",
                asset_rel="album/new-shared.jpg",
                faces=[
                    _face_record(
                        face_id="face-a-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.96, 0.04, 0.0], dtype=np.float32),
                        face_key="face-key-a-new-shared",
                    ),
                    _face_record(
                        face_id="face-b-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.04, 0.96, 0.0], dtype=np.float32),
                        face_key="face-key-b-new-shared",
                    ),
                ],
            )
        ]
    )

    original_sync_scan_results = repository.state_repository.sync_scan_results
    call_count = {"value": 0}

    def fail_once(persons: list[PersonRecord], faces: list[FaceRecord]) -> None:
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("profile sync failed")
        original_sync_scan_results(persons, faces)

    monkeypatch.setattr(repository.state_repository, "sync_scan_results", fail_once)

    with pytest.raises(RuntimeError, match="profile sync failed"):
        session.commit(repository, distance_threshold=0.6, min_samples=2)

    assert [summary.person_id for summary in repository.get_person_summaries()] == [
        "person-a",
        "person-b",
    ]
    assert repository.get_common_asset_ids_for_group(group.group_id) == ["asset-shared"]


def test_state_repository_backfills_profile_stability_from_face_keys_for_legacy_rows(
    tmp_path: Path,
) -> None:
    state_repository = FaceStateRepository(tmp_path / "face_state.db")
    state_repository.initialize()
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    timestamp = _now_iso()

    with sqlite3.connect(state_repository.db_path) as conn:
        conn.execute(
            """
            INSERT INTO person_profiles (
                person_id, name, center_embedding, embedding_dim,
                created_at, updated_at, sample_count, profile_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "person-a",
                "Alice",
                sqlite3.Binary(embedding.tobytes()),
                int(embedding.shape[0]),
                timestamp,
                timestamp,
                0,
                "unstable",
            ),
        )
        conn.executemany(
            """
            INSERT INTO face_keys (face_key, person_id, asset_id, asset_rel, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (f"face-key-{index}", "person-a", f"asset-{index}", f"album/{index}.jpg", timestamp)
                for index in range(3)
            ],
        )
        conn.commit()

    profiles = state_repository.get_profiles()

    assert len(profiles) == 1
    assert profiles[0].person_id == "person-a"
    assert profiles[0].sample_count == 3
    assert profiles[0].profile_state == "stable"


def test_face_scan_session_preserves_manual_faces_for_rescanned_asset(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db", tmp_path / "face_state.db")
    auto_face = _face_record(
        face_id="face-auto",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-a",
        face_key="face-key-auto",
    )
    manual_face = _face_record(
        face_id="face-manual",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-b",
        face_key="face-key-manual",
        thumbnail_path="thumbnails/face-manual.png",
        is_manual=True,
    )

    repository.replace_all(
        [auto_face],
        [_person_record(person_id="person-a", key_face_id="face-auto", face_count=1, name="Alice")],
    )
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(
        [_person_record(person_id="person-a", key_face_id="face-auto", face_count=1, name="Alice")],
        [auto_face],
    )
    repository.state_repository.upsert_manual_face(manual_face)

    session = FaceScanSession()
    session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                faces=[],
            )
        ]
    )

    session.commit(repository, distance_threshold=0.6, min_samples=1)

    faces = {face.face_id: face for face in repository.get_all_faces()}
    assert set(faces) == {"face-manual"}
    assert faces["face-manual"].is_manual is True
    assert faces["face-manual"].asset_id == "asset-a"


def test_get_person_ids_for_asset_ids_chunks_large_sqlite_in_queries(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db")
    face_count = 1005
    faces = [
        _face_record(
            face_id=f"face-{index}",
            asset_id=f"asset-{index:04d}",
            asset_rel=f"album/{index:04d}.jpg",
            person_id=f"person-{index:04d}",
        )
        for index in range(face_count)
    ]
    persons = [
        _person_record(
            person_id=f"person-{index:04d}",
            key_face_id=f"face-{index}",
            face_count=1,
            name=f"Person {index:04d}",
        )
        for index in range(face_count)
    ]
    repository.replace_all(faces, persons)

    person_ids = repository.get_person_ids_for_asset_ids(
        [f"asset-{index:04d}" for index in range(face_count)]
    )

    assert person_ids == [f"person-{index:04d}" for index in range(face_count)]

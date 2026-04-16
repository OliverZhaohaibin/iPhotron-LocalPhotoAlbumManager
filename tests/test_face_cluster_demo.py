from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo" / "face-cluster"
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

db = importlib.import_module("db")
pipeline = importlib.import_module("pipeline")
ui = importlib.import_module("ui")


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _vector(*values: float) -> np.ndarray:
    return db.normalize_vector(np.asarray(values, dtype=np.float32))


def _workspace(tmp_path: Path, name: str):
    source_dir = tmp_path / name
    source_dir.mkdir(parents=True, exist_ok=True)
    return db.prepare_runtime_workspace(
        source_dir,
        runtime_root=tmp_path / "runtime",
        state_root=tmp_path / "state",
    )


def _face(
    *,
    face_id: str,
    face_key: str,
    person_id: str | None,
    embedding: np.ndarray,
    asset_rel: str = "photo.jpg",
    confidence: float = 0.9,
) -> db.FaceRecord:
    return db.FaceRecord(
        face_id=face_id,
        face_key=face_key,
        asset_rel=asset_rel,
        box_x=10,
        box_y=20,
        box_w=80,
        box_h=80,
        confidence=confidence,
        embedding=embedding,
        embedding_dim=int(embedding.shape[0]),
        thumbnail_path=None,
        person_id=person_id,
        detected_at="2026-04-11T00:00:00+00:00",
        image_width=1024,
        image_height=768,
    )


def _person(
    *,
    person_id: str,
    face_id: str,
    embedding: np.ndarray,
    face_count: int = 1,
    name: str | None = None,
    created_at: str = "2026-04-11T00:00:00+00:00",
) -> db.PersonRecord:
    return db.PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=created_at,
        updated_at=created_at,
    )


def _seed_repository(
    workspace,
    *,
    faces: list[db.FaceRecord],
    persons: list[db.PersonRecord],
) -> tuple[db.FaceClusterRepository, db.FaceClusterStateRepository]:
    repository = db.FaceClusterRepository(workspace.db_path, workspace.state_db_path)
    state_repository = db.FaceClusterStateRepository(workspace.state_db_path)
    repository.replace_all(faces, persons)
    state_repository.sync_scan_results(persons, faces)
    return repository, state_repository


def test_rename_persists_across_rescans(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "library-a")
    embedding = _vector(1.0, 0.0, 0.0)
    face = _face(face_id="face-1", face_key="face-key-1", person_id="person-a", embedding=embedding)
    person = _person(person_id="person-a", face_id="face-1", embedding=embedding)

    repository, _ = _seed_repository(workspace, faces=[face], persons=[person])
    repository.rename_person("person-a", "Alice")

    rescan_workspace = _workspace(tmp_path, "library-a")
    rescan_state = db.FaceClusterStateRepository(rescan_workspace.state_db_path)
    temp_face = _face(
        face_id="face-2",
        face_key="face-key-1",
        person_id="temp-person",
        embedding=embedding,
    )
    temp_person = _person(person_id="temp-person", face_id="face-2", embedding=embedding)

    canonical_faces, canonical_persons = pipeline.canonicalize_cluster_identities(
        [temp_face],
        [temp_person],
        rescan_state,
        distance_threshold=0.6,
    )

    assert canonical_faces[0].person_id == "person-a"
    assert canonical_persons[0].person_id == "person-a"
    assert canonical_persons[0].name == "Alice"


def test_state_is_isolated_per_folder(tmp_path: Path) -> None:
    embedding = _vector(1.0, 0.0, 0.0)

    workspace_a = _workspace(tmp_path, "library-a")
    repository_a, _ = _seed_repository(
        workspace_a,
        faces=[_face(face_id="a-face", face_key="shared-key", person_id="person-a", embedding=embedding)],
        persons=[_person(person_id="person-a", face_id="a-face", embedding=embedding)],
    )
    repository_a.rename_person("person-a", "Alice")

    workspace_b = _workspace(tmp_path, "library-b")
    state_b = db.FaceClusterStateRepository(workspace_b.state_db_path)
    temp_face = _face(
        face_id="b-face",
        face_key="shared-key",
        person_id="temp-person",
        embedding=embedding,
    )
    temp_person = _person(person_id="temp-person", face_id="b-face", embedding=embedding)

    _, canonical_persons = pipeline.canonicalize_cluster_identities(
        [temp_face],
        [temp_person],
        state_b,
        distance_threshold=0.6,
    )

    assert canonical_persons[0].person_id != "person-a"
    assert canonical_persons[0].name is None


def test_merge_persons_updates_runtime_and_state(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "library-a")
    source_embedding = _vector(1.0, 0.0, 0.0)
    target_embedding = _vector(0.0, 1.0, 0.0)
    faces = [
        _face(face_id="source-face", face_key="source-key", person_id="source", embedding=source_embedding),
        _face(face_id="target-face", face_key="target-key", person_id="target", embedding=target_embedding),
    ]
    persons = [
        _person(person_id="source", face_id="source-face", embedding=source_embedding, name="来源人物"),
        _person(person_id="target", face_id="target-face", embedding=target_embedding, name="目标人物"),
    ]

    repository, state_repository = _seed_repository(workspace, faces=faces, persons=persons)
    repository.merge_persons("source", "target")

    summaries = repository.get_person_summaries()
    assert [summary.person_id for summary in summaries] == ["target"]
    assert summaries[0].face_count == 2
    assert len(repository.get_faces_by_person("target")) == 2
    assert repository.get_faces_by_person("source") == []

    face_key_map = state_repository.get_face_key_map(["source-key", "target-key"])
    assert face_key_map == {"source-key": "target", "target-key": "target"}
    assert {profile.person_id for profile in state_repository.get_profiles()} == {"target"}


def test_rescan_reuses_merged_target_person(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "library-a")
    source_embedding = _vector(1.0, 0.0, 0.0)
    target_embedding = _vector(0.0, 1.0, 0.0)
    faces = [
        _face(face_id="source-face", face_key="source-key", person_id="source", embedding=source_embedding),
        _face(face_id="target-face", face_key="target-key", person_id="target", embedding=target_embedding),
    ]
    persons = [
        _person(person_id="source", face_id="source-face", embedding=source_embedding),
        _person(person_id="target", face_id="target-face", embedding=target_embedding, name="目标人物"),
    ]

    repository, state_repository = _seed_repository(workspace, faces=faces, persons=persons)
    repository.merge_persons("source", "target")

    temp_faces = [
        _face(face_id="rescan-a", face_key="source-key", person_id="temp-a", embedding=source_embedding),
        _face(face_id="rescan-b", face_key="target-key", person_id="temp-b", embedding=target_embedding),
    ]
    temp_persons = [
        _person(person_id="temp-a", face_id="rescan-a", embedding=source_embedding),
        _person(person_id="temp-b", face_id="rescan-b", embedding=target_embedding),
    ]

    canonical_faces, canonical_persons = pipeline.canonicalize_cluster_identities(
        temp_faces,
        temp_persons,
        state_repository,
        distance_threshold=0.6,
    )

    assert {face.person_id for face in canonical_faces} == {"target"}
    assert len(canonical_persons) == 1
    assert canonical_persons[0].person_id == "target"


def test_embedding_match_falls_back_to_existing_profile(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "library-a")
    state_repository = db.FaceClusterStateRepository(workspace.state_db_path)
    existing_person = _person(
        person_id="profile-a",
        face_id="seed-face",
        embedding=_vector(0.9, 0.1, 0.0),
        name="已命名人物",
    )
    state_repository.sync_scan_results([existing_person], [])

    temp_face = _face(
        face_id="new-face",
        face_key="new-face-key",
        person_id="temp-person",
        embedding=_vector(0.88, 0.12, 0.0),
    )
    temp_person = _person(
        person_id="temp-person",
        face_id="new-face",
        embedding=_vector(0.88, 0.12, 0.0),
    )

    _, canonical_persons = pipeline.canonicalize_cluster_identities(
        [temp_face],
        [temp_person],
        state_repository,
        distance_threshold=0.6,
    )

    assert canonical_persons[0].person_id == "profile-a"
    assert canonical_persons[0].name == "已命名人物"


def test_sync_scan_results_does_not_call_nested_person_order_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "library-a")
    state_repository = db.FaceClusterStateRepository(workspace.state_db_path)
    embedding = _vector(1.0, 0.0, 0.0)
    person = _person(person_id="profile-a", face_id="seed-face", embedding=embedding)
    face = _face(face_id="seed-face", face_key="seed-key", person_id="profile-a", embedding=embedding)

    def _forbidden_nested_query(_person_ids):
        raise AssertionError("sync_scan_results should not call get_person_order_map")

    monkeypatch.setattr(state_repository, "get_person_order_map", _forbidden_nested_query)
    state_repository.sync_scan_results([person], [face])

    verify_repository = db.FaceClusterStateRepository(workspace.state_db_path)
    assert verify_repository.get_person_order_map(["profile-a"]) == {"profile-a": 0}


def test_editable_name_label_emits_trimmed_text(qapp: QApplication) -> None:
    widget = ui.EditableNameLabel("人物1")
    widget.show()
    qapp.processEvents()

    captured: list[str] = []
    widget.rename_submitted.connect(captured.append)

    QTest.mouseClick(widget._display_label, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    widget._editor.setText("  Alice  ")
    QTest.keyClick(widget._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert captured == ["Alice"]


def test_clearing_name_restores_fallback_label(tmp_path: Path, qapp: QApplication) -> None:
    workspace = _workspace(tmp_path, "library-a")
    embedding = _vector(1.0, 0.0, 0.0)
    repository, _ = _seed_repository(
        workspace,
        faces=[_face(face_id="face-1", face_key="key-1", person_id="person-a", embedding=embedding)],
        persons=[_person(person_id="person-a", face_id="face-1", embedding=embedding, name="Alice")],
    )

    window = ui.FaceClusterWindow()
    window._repository = repository
    window._refresh_from_repository("person-a")
    window.show()
    qapp.processEvents()

    card = window._cards["person-a"]
    QTest.mouseClick(card._title_label._display_label, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    card._title_label._editor.setText("   ")
    QTest.keyClick(card._title_label._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert window._cards["person-a"]._title_label.text() == "人物1"


def test_context_menu_and_merge_flow(tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(tmp_path, "library-a")
    source_embedding = _vector(1.0, 0.0, 0.0)
    target_embedding = _vector(0.0, 1.0, 0.0)
    repository, _ = _seed_repository(
        workspace,
        faces=[
            _face(face_id="source-face", face_key="source-key", person_id="source", embedding=source_embedding),
            _face(face_id="target-face", face_key="target-key", person_id="target", embedding=target_embedding),
        ],
        persons=[
            _person(person_id="source", face_id="source-face", embedding=source_embedding, name="来源人物"),
            _person(person_id="target", face_id="target-face", embedding=target_embedding, name="目标人物"),
        ],
    )

    window = ui.FaceClusterWindow()
    window._repository = repository
    window._refresh_from_repository("source")
    window.show()
    qapp.processEvents()

    menu = window._cards["source"].build_context_menu(has_merge_targets=True, parent=window)
    assert [action.text() for action in menu.actions()] == ["合并到..."]

    merge_choices = window._build_merge_choices("source")
    assert merge_choices == [("目标人物（1 张人脸）", "target")]

    monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *args, **kwargs: ("目标人物（1 张人脸）", True))
    window._prompt_merge_target("source")
    qapp.processEvents()

    assert list(window._cards) == ["target"]
    assert "目标人物" in window._detail_hint.text()

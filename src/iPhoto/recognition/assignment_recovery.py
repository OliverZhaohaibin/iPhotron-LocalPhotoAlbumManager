"""Idempotent recovery for cross-kind detection assignments."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from iPhoto.people.repository import FaceRepository
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.utils.pathutils import ensure_work_dir


def apply_detection_assignment_with_group_refresh(
    library_root: Path,
    payload: Mapping[str, object],
) -> bool:
    """Apply an assignment and rebuild every dependent group cache before success."""

    faces_root = ensure_work_dir(Path(library_root)) / "faces"
    state_db_path = faces_root / "face_state.db"
    face_state_repository = FaceStateRepository(state_db_path)
    succeeded = face_state_repository.set_annotation_identity_assignment(
        source_kind=str(payload.get("source_kind") or ""),
        source_annotation_id=str(payload.get("source_annotation_id") or ""),
        target_kind=str(payload.get("target_kind") or ""),
        target_id=str(payload.get("target_id") or ""),
    )
    if not succeeded:
        return False
    target_kind = str(payload.get("target_kind") or "")
    target_id = str(payload.get("target_id") or "")
    if target_kind == "person":
        face_state_repository.confirm_person(target_id)
    elif target_kind == "pet":
        from iPhoto.pets.state_repository import PetStateRepository

        pets_root = ensure_work_dir(Path(library_root)) / "pets"
        PetStateRepository(pets_root / "pet_state.db").confirm_pet(target_id)
    FaceRepository(
        faces_root / "face_index.db",
        state_db_path,
    ).refresh_all_group_assets()
    return True


__all__ = ["apply_detection_assignment_with_group_refresh"]

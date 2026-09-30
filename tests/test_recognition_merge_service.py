from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from iPhoto.application.services.recognition_merge_service import (
    IdentityMergeFailure,
    IdentityMergeRefreshPolicy,
    IdentityRef,
    RecognitionMergeService,
)
from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.pets.records import PetDetectionRecord, PetMergeOutcome, PetMutationFailure, PetRecord
from iPhoto.pets.repository_utils import utc_now_iso
from iPhoto.recognition.mutation_coordinator import RecognitionMutationCoordinator
from iPhoto.recognition.operation_journal import RecognitionOperationJournal
from iPhoto.utils.pathutils import ensure_work_dir


def _services(*, person_hidden: bool = False, pet_hidden: bool = False):
    people = SimpleNamespace(
        list_clusters=lambda *, include_hidden: [
            SimpleNamespace(person_id="same", is_hidden=person_hidden),
            SimpleNamespace(person_id="other-person", is_hidden=person_hidden),
        ],
        cluster_asset_ids=lambda person_id: [f"asset-{person_id}"],
        merge_clusters=Mock(return_value=True),
        merge_identities=Mock(
            return_value=SimpleNamespace(merged=True, group_redirects={"group-a": "group-b"})
        ),
    )
    pets = SimpleNamespace(
        list_pets=lambda *, include_hidden: [
            SimpleNamespace(pet_id="same", is_hidden=pet_hidden),
            SimpleNamespace(pet_id="other-pet", is_hidden=pet_hidden),
        ],
        pet_asset_ids=lambda pet_id: [f"asset-{pet_id}"],
        merge_pets=Mock(return_value=True),
    )
    return people, pets


@pytest.mark.parametrize(
    ("source", "target", "called_method", "called_args"),
    [
        ("person:same", "person:other-person", "merge_clusters", ("same", "other-person")),
        ("pet:same", "pet:other-pet", "merge_pets", ("same", "other-pet")),
        (
            "person:same",
            "pet:same",
            "merge_identities",
            ("person:same", "pet:same"),
        ),
        (
            "pet:same",
            "person:same",
            "merge_identities",
            ("pet:same", "person:same"),
        ),
    ],
)
def test_routes_all_directions_with_typed_identity(
    source: str,
    target: str,
    called_method: str,
    called_args: tuple[str, str],
) -> None:
    people, pets = _services()
    service = RecognitionMergeService(people, pets)

    outcome = service.merge(source, target)

    assert outcome.merged is True
    assert outcome.refresh_policy is (
        IdentityMergeRefreshPolicy.SNAPSHOT
        if source.split(":", 1)[0] == target.split(":", 1)[0]
        else IdentityMergeRefreshPolicy.IMMEDIATE
    )
    owner = pets if called_method == "merge_pets" else people
    getattr(owner, called_method).assert_called_once_with(*called_args)


def test_manual_merge_allows_hidden_state_mismatch() -> None:
    people, pets = _services(person_hidden=False, pet_hidden=True)
    service = RecognitionMergeService(people, pets)

    outcome = service.merge(IdentityRef("person", "same"), IdentityRef("pet", "same"))

    assert outcome.merged is True
    assert outcome.failure is None
    people.merge_identities.assert_called_once_with("person:same", "pet:same")


def test_merge_holds_library_lease_from_hidden_check_through_mutation(tmp_path) -> None:
    hidden = {"source": False, "target": False}
    merge_entered = threading.Event()
    allow_merge = threading.Event()
    hidden_started = threading.Event()
    hidden_finished = threading.Event()

    def merge_clusters(source: str, target: str) -> bool:
        assert (source, target) == ("source", "target")
        merge_entered.set()
        assert allow_merge.wait(timeout=2.0)
        return True

    people = SimpleNamespace(
        list_clusters=lambda *, include_hidden: [
            SimpleNamespace(person_id=person_id, is_hidden=is_hidden)
            for person_id, is_hidden in hidden.items()
        ],
        cluster_asset_ids=lambda person_id: [f"asset-{person_id}"],
        merge_clusters=merge_clusters,
    )
    pets = SimpleNamespace(library_root=lambda: tmp_path)
    merge_owner = RecognitionMutationCoordinator(tmp_path)
    hidden_owner = RecognitionMutationCoordinator(tmp_path)
    service = RecognitionMergeService(
        people,
        pets,
        mutation_coordinator=merge_owner,
    )

    def hide_target() -> None:
        hidden_started.set()
        with hidden_owner.mutation_scope():
            hidden["target"] = True
        hidden_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        merge = executor.submit(service.merge, "person:source", "person:target")
        assert merge_entered.wait(timeout=2.0)
        hide = executor.submit(hide_target)
        assert hidden_started.wait(timeout=2.0)
        assert not hidden_finished.wait(timeout=0.2)

        allow_merge.set()
        assert merge.result(timeout=2.0).merged is True
        hide.result(timeout=2.0)

    assert hidden["target"] is True


def test_untyped_raw_id_is_rejected_without_guessing_kind() -> None:
    people, pets = _services()
    service = RecognitionMergeService(people, pets)

    outcome = service.merge("same", "pet:same")

    assert outcome.merged is False
    assert outcome.failure is IdentityMergeFailure.INVALID_IDENTITY


def test_pet_merge_recovery_pending_is_not_reported_as_rejected() -> None:
    people, pets = _services()
    pets.merge_pets.return_value = PetMergeOutcome(
        False,
        PetMutationFailure.RECOVERY_PENDING,
    )
    service = RecognitionMergeService(people, pets)

    outcome = service.merge("pet:same", "pet:other-pet")

    assert outcome.merged is False
    assert outcome.failure is IdentityMergeFailure.RECOVERY_PENDING


def test_pet_same_asset_conflict_is_preserved_by_typed_merge_boundary() -> None:
    people, pets = _services()
    pets.merge_pets.return_value = PetMergeOutcome(
        False,
        PetMutationFailure.SAME_ASSET_CONFLICT,
    )
    service = RecognitionMergeService(people, pets)

    outcome = service.merge("pet:same", "pet:other-pet")

    assert outcome.merged is False
    assert outcome.failure is IdentityMergeFailure.SAME_ASSET_CONFLICT


def test_pending_assignment_blocks_person_merge_before_people_mutation(tmp_path) -> None:
    people, pets = _services()
    pets.library_root = lambda: tmp_path
    journal = RecognitionOperationJournal(
        ensure_work_dir(tmp_path) / "recognition" / "operations.db"
    )
    operation_id = journal.prepare(
        "recognition_detection_assignment",
        {
            "source_kind": "pet",
            "source_annotation_id": "det-a",
            "target_kind": "person",
            "target_id": "same",
        },
    )
    journal.transition(operation_id, "applying")
    service = RecognitionMergeService(people, pets)

    outcome = service.merge("person:same", "person:other-person")

    assert outcome.failure is IdentityMergeFailure.RECOVERY_PENDING
    people.merge_clusters.assert_not_called()


def test_manual_pet_merge_ignores_species_and_embedding_contracts(tmp_path) -> None:
    pets = create_pet_service(tmp_path)
    repository = pets.repository()
    assert repository is not None
    timestamp = utc_now_iso()
    detections: list[PetDetectionRecord] = []
    records: list[PetRecord] = []
    for index, pet_id in enumerate(("pet-a", "pet-b")):
        embedding = np.asarray(
            [1.0, 0.0] if index == 0 else [0.0, 1.0, 0.0],
            dtype=np.float32,
        )
        pipeline_version = f"test-pipeline-v{index + 1}"
        detection = PetDetectionRecord(
            detection_id=f"detection-{pet_id}",
            pet_key=f"v2:{pet_id}",
            asset_id=f"asset-{pet_id}",
            asset_rel=f"album/{pet_id}.jpg",
            box_x=0,
            box_y=0,
            box_w=100,
            box_h=100,
            confidence=0.9,
            embedding=embedding,
            embedding_dim=int(embedding.shape[0]),
            embedding_model="test-model",
            detector_model="test-detector",
            thumbnail_path=None,
            pet_id=pet_id,
            detected_at=timestamp,
            image_width=100,
            image_height=100,
            species_label="cat" if index == 0 else "dog",
            embedding_pipeline_version=pipeline_version,
            generation_id=index,
        )
        detections.append(detection)
        records.append(
            PetRecord(
                pet_id=pet_id,
                name=None,
                key_detection_id=detection.detection_id,
                detection_count=1,
                center_embedding=embedding,
                embedding_dim=int(embedding.shape[0]),
                created_at=timestamp,
                updated_at=timestamp,
                sample_count=1,
                species_label=detection.species_label,
                embedding_pipeline_version=pipeline_version,
                generation_id=index,
            )
        )
    repository.replace_all(detections, records)
    service = RecognitionMergeService(SimpleNamespace(), pets)

    outcome = service.merge("pet:pet-a", "pet:pet-b")

    assert outcome.merged is True
    merged_pets = repository.get_all_pet_records()
    assert [pet.pet_id for pet in merged_pets] == ["pet-b"]
    assert merged_pets[0].detection_count == 2
    assert merged_pets[0].embedding_dim == 3
    assert {detection.pet_id for detection in repository.get_all_detections()} == {"pet-b"}
    assert repository.state_repository is not None
    assert repository.state_repository.get_merge_redirect_map()["pet-a"] == "pet-b"
    assert service._journal is not None
    assert service._journal.unfinished() == ()

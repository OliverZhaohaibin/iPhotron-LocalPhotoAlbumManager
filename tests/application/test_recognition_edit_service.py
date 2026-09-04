"""Scope and concurrency contracts for recognition edits, independent of Qt."""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3

import pytest

from iPhoto.application.services.recognition_edit_service import (
    RecognitionEditService,
    annotation_edit_context,
    current_identity_display_name,
)
from iPhoto.domain.recognition_edits import (
    AnnotationEditContext,
    IdentityRef,
    IdentityRenameRequest,
    IdentitySelectionRequest,
    InlineSelectionScope,
    RecognitionEditStatus,
)
from iPhoto.people.records import AssetFaceAnnotation
from iPhoto.pets.records import AssetPetAnnotation, PetMutationFailure, PetMutationOutcome


def annotation(kind="person", **changes):
    fields = dict(
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=40,
        box_h=40,
        image_width=100,
        image_height=100,
        promotion_state="confirmed",
    )
    if kind == "person":
        fields.update(face_id="face-a", person_id="a")
        fields.update(changes)
        return AssetFaceAnnotation(**fields)
    fields.update(detection_id="det-a", pet_id="a")
    fields.update(changes)
    return AssetPetAnnotation(**fields)


def service_for(record):
    people = Mock()
    pets = Mock()
    people.list_asset_face_annotations.return_value = [record]
    pets.list_asset_pet_annotations.return_value = [record]
    people.has_cluster.return_value = pets.has_pet.return_value = True
    people.move_face_to_person.return_value = True
    people.reassign_detection_identity.return_value = True
    pets.move_detection_to_pet_with_outcome.return_value = PetMutationOutcome(True)
    merges = Mock()
    merges.merge.return_value = SimpleNamespace(merged=True)
    mutations = Mock()
    mutations.mutation_scope.side_effect = nullcontext
    mutations.recover_pending.return_value = True
    service = RecognitionEditService(
        people_service=people,
        pet_service=pets,
        merge_service=merges,
        mutation_coordinator=mutations,
    )
    return service, people, pets, merges, mutations


@pytest.mark.parametrize("kind", ["person", "pet"])
@pytest.mark.parametrize(
    "state", ["candidate", "eligible", "confirmed", "legacy_visible", "unknown"]
)
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("assigned", [False, True])
def test_inline_scope_matrix(kind, state, manual, assigned):
    identity = IdentityRef(kind, "a") if assigned else None
    context = AnnotationEditContext("photo", kind, "detection", identity, identity, manual, state)
    expected = (
        InlineSelectionScope.CANDIDATE_IDENTITY
        if assigned and not manual and state == "candidate"
        else InlineSelectionScope.ANNOTATION
    )
    assert context.selection_scope == expected


def test_redirected_candidate_is_not_a_merge_source():
    context = AnnotationEditContext(
        "photo",
        "person",
        "face-a",
        IdentityRef("person", "a"),
        IdentityRef("pet", "b"),
        False,
        "candidate",
    )
    assert context.selection_scope == InlineSelectionScope.ANNOTATION


@pytest.mark.parametrize(
    "kind,target_kind", [("person", "person"), ("pet", "pet"), ("person", "pet"), ("pet", "person")]
)
def test_reassignment_uses_original_detection_and_never_merges(kind, target_kind):
    record = annotation(kind)
    service, people, pets, merges, _ = service_for(record)
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record), IdentityRef(target_kind, "b")
    )
    assert service.reassign_annotation(request).status == RecognitionEditStatus.CHANGED
    if kind != target_kind:
        people.reassign_detection_identity.assert_called_once_with(
            source_kind=kind,
            source_annotation_id=request.context.annotation_id,
            target_identity=f"{target_kind}:b",
        )
        people.move_face_to_person.assert_not_called()
        pets.move_detection_to_pet_with_outcome.assert_not_called()
    elif kind == "person":
        people.move_face_to_person.assert_called_once_with("face-a", "b")
    else:
        pets.move_detection_to_pet_with_outcome.assert_called_once_with("det-a", "b")
    merges.merge.assert_not_called()


@pytest.mark.parametrize("kind", ["person", "pet"])
def test_only_independent_pending_candidates_can_merge(kind):
    record = annotation(kind, promotion_state="candidate")
    service, people, pets, merges, _ = service_for(record)
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record), IdentityRef("person", "b")
    )
    assert service.merge_candidate_identity(request).status == RecognitionEditStatus.CHANGED
    merges.merge.assert_called_once_with(IdentityRef(kind, "a"), IdentityRef("person", "b"))
    people.move_face_to_person.assert_not_called()
    people.reassign_detection_identity.assert_not_called()
    pets.move_detection_to_pet_with_outcome.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [{"is_manual": True}, {"promotion_state": "eligible"}, {"canonical_identity_id": "other"}],
)
def test_merge_command_cannot_expand_non_candidate_scope(changes):
    record = replace(annotation(promotion_state="candidate"), **changes)
    service, _, _, merges, _ = service_for(record)
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record), IdentityRef("person", "b")
    )
    assert service.merge_candidate_identity(request).failure == "context_changed"
    merges.merge.assert_not_called()


@pytest.mark.parametrize(
    "change", ["deleted", "confirmed", "reassigned", "target_deleted", "recovery"]
)
def test_stale_or_unavailable_edits_are_rejected_without_writes(change):
    record = annotation(promotion_state="candidate")
    service, people, pets, merges, mutations = service_for(record)
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record), IdentityRef("person", "b")
    )
    if change == "deleted":
        people.list_asset_face_annotations.return_value = []
    elif change == "confirmed":
        people.list_asset_face_annotations.return_value = [
            replace(record, promotion_state="confirmed")
        ]
    elif change == "reassigned":
        people.list_asset_face_annotations.return_value = [
            replace(record, canonical_identity_id="c")
        ]
    elif change == "target_deleted":
        people.has_cluster.return_value = False
    else:
        mutations.recover_pending.return_value = False
    assert service.merge_candidate_identity(request).status == RecognitionEditStatus.REJECTED
    merges.merge.assert_not_called()
    people.move_face_to_person.assert_not_called()
    people.reassign_detection_identity.assert_not_called()
    pets.move_detection_to_pet_with_outcome.assert_not_called()


@pytest.mark.parametrize("operation", ["reassign_annotation", "merge_candidate_identity"])
def test_current_identity_is_noop_even_for_pending_candidate(operation):
    record = annotation(promotion_state="candidate")
    service, people, pets, merges, _ = service_for(record)
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record), IdentityRef("person", "a")
    )
    assert getattr(service, operation)(request).status == RecognitionEditStatus.UNCHANGED
    merges.merge.assert_not_called()
    people.move_face_to_person.assert_not_called()
    people.rename_cluster.assert_not_called()


@pytest.mark.parametrize("kind", ["person", "pet"])
def test_plain_text_renames_even_when_matching_an_existing_name(kind):
    record = annotation(kind)
    service, people, pets, merges, _ = service_for(record)
    request = IdentityRenameRequest(
        annotation_edit_context("photo", record),
        "  Bob  ",
        current_identity_display_name(record),
    )
    assert service.rename_identity(request).status == RecognitionEditStatus.CHANGED
    (people.rename_cluster if kind == "person" else pets.rename_pet).assert_called_once_with(
        "a", "Bob"
    )
    merges.merge.assert_not_called()
    people.reassign_detection_identity.assert_not_called()


@pytest.mark.parametrize("failure", ["rejected", "io", "pet_conflict"])
def test_move_failure_never_returns_success(failure):
    record = annotation("pet" if failure == "pet_conflict" else "person")
    service, people, pets, _, _ = service_for(record)
    if failure == "io":
        people.move_face_to_person.side_effect = sqlite3.OperationalError("injected")
    elif failure == "rejected":
        people.move_face_to_person.return_value = False
    else:
        pets.move_detection_to_pet_with_outcome.return_value = PetMutationOutcome(
            False, PetMutationFailure.SAME_ASSET_CONFLICT
        )
    request = IdentitySelectionRequest(
        annotation_edit_context("photo", record),
        IdentityRef("pet" if failure == "pet_conflict" else "person", "b"),
    )
    result = service.reassign_annotation(request)
    assert result.status == RecognitionEditStatus.REJECTED
    assert (
        result.failure
        == {"rejected": "rejected", "io": "io_error", "pet_conflict": "same_asset_conflict"}[
            failure
        ]
    )


@pytest.mark.parametrize("source_kind", ["person", "pet"])
def test_unnamed_cross_kind_identity_does_not_borrow_source_name(source_kind):
    target_kind = "pet" if source_kind == "person" else "person"
    record = annotation(
        source_kind,
        canonical_identity_kind=target_kind,
        canonical_identity_id="target",
        canonical_display_name=None,
        display_name="Alice",
    )
    service, people, pets, merges, _ = service_for(record)
    request = IdentityRenameRequest(annotation_edit_context("photo", record), "Alice", None)
    assert current_identity_display_name(record) is None
    assert service.rename_identity(request).status == RecognitionEditStatus.CHANGED
    rename = pets.rename_pet if target_kind == "pet" else people.rename_cluster
    rename.assert_called_once_with("target", "Alice")
    merges.merge.assert_not_called()


def test_stale_confirmed_rename_is_compare_and_set_rejected():
    record = annotation(
        promotion_state="confirmed",
        canonical_display_name="Alice",
        display_name="Alice",
    )
    service, people, pets, merges, _ = service_for(record)
    request = IdentityRenameRequest(annotation_edit_context("photo", record), "Bob", "Alice")
    people.list_asset_face_annotations.return_value = [
        replace(record, canonical_display_name="Carol", display_name="Carol")
    ]
    outcome = service.rename_identity(request)
    assert outcome.status == RecognitionEditStatus.REJECTED
    assert outcome.failure == "context_changed"
    people.rename_cluster.assert_not_called()
    pets.rename_pet.assert_not_called()
    merges.merge.assert_not_called()


def test_rename_with_matching_expected_and_new_name_is_noop():
    record = annotation(canonical_display_name="Alice", display_name="Alice")
    service, people, pets, _, _ = service_for(record)
    request = IdentityRenameRequest(annotation_edit_context("photo", record), "Alice", "Alice")
    assert service.rename_identity(request).status == RecognitionEditStatus.UNCHANGED
    people.rename_cluster.assert_not_called()
    pets.rename_pet.assert_not_called()

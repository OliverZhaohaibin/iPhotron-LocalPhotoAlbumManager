from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iPhoto.application.services.recognition_merge_service import (
    IdentityMergeFailure,
    IdentityMergeRefreshPolicy,
    IdentityRef,
    RecognitionMergeService,
)


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


def test_hidden_state_mismatch_is_structured_and_does_not_mutate() -> None:
    people, pets = _services(person_hidden=False, pet_hidden=True)
    service = RecognitionMergeService(people, pets)

    outcome = service.merge(IdentityRef("person", "same"), IdentityRef("pet", "same"))

    assert outcome.merged is False
    assert outcome.failure is IdentityMergeFailure.HIDDEN_STATE_MISMATCH
    people.merge_identities.assert_not_called()


def test_untyped_raw_id_is_rejected_without_guessing_kind() -> None:
    people, pets = _services()
    service = RecognitionMergeService(people, pets)

    outcome = service.merge("same", "pet:same")

    assert outcome.merged is False
    assert outcome.failure is IdentityMergeFailure.INVALID_IDENTITY

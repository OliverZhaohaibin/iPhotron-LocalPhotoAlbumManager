from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from iPhoto.application.services.recognition_query_service import RecognitionQueryService


def _person(person_id: str, name: str = "Alice") -> SimpleNamespace:
    return SimpleNamespace(
        person_id=person_id,
        name=name,
        thumbnail_path=None,
        face_count=3,
    )


def _pet(pet_id: str, *, hidden: bool = False, name: str = "Milo") -> SimpleNamespace:
    return SimpleNamespace(
        pet_id=pet_id,
        name=name,
        thumbnail_path=None,
        detection_count=4,
        is_hidden=hidden,
    )


def test_dashboard_cache_reuses_single_pet_summary_read() -> None:
    root = Path("/library")
    visible_pet = _pet("pet-visible")
    hidden_pet = _pet("pet-hidden", hidden=True)
    pet_service = Mock()
    pet_service.load_dashboard.return_value = ([visible_pet, hidden_pet], 2)
    people_service = Mock()
    people_service.load_dashboard.return_value = ([_person("person-a")], [object()], 1)
    service = RecognitionQueryService(
        root,
        people_service=people_service,
        pet_service=pet_service,
    )

    first = service.load_dashboard(False)
    second = service.load_dashboard(False)

    assert second is first
    assert first.pets == (visible_pet,)
    pet_service.load_dashboard.assert_called_once_with(include_hidden=True)
    people_service.load_dashboard.assert_called_once_with(
        include_hidden=False,
        pet_summaries=[visible_pet, hidden_pet],
    )


def test_overlay_reuses_revision_candidates_and_invalidation_reloads() -> None:
    root = Path("/library")
    pet_service = Mock()
    pet_service.load_dashboard.return_value = ([_pet("pet-a")], 0)
    pet_service.list_asset_pet_annotations.return_value = ["pet-box"]
    people_service = Mock()
    people_service.load_dashboard.return_value = ([_person("person-a")], [], 0)
    people_service.list_asset_face_annotations.return_value = ["face-box"]
    service = RecognitionQueryService(
        root,
        people_service=people_service,
        pet_service=pet_service,
    )

    service.load_dashboard(False)
    overlay = service.load_overlay("asset-a", False)

    assert overlay.faces == ("face-box",)
    assert overlay.pets == ("pet-box",)
    assert {value.identity_key for value in overlay.candidates} == {
        "person:person-a",
        "pet:pet-a",
    }
    assert people_service.load_dashboard.call_count == 1
    assert pet_service.load_dashboard.call_count == 1

    service.invalidate(["asset-a"])
    refreshed = service.load_dashboard(False)
    assert refreshed.revision == 1
    assert people_service.load_dashboard.call_count == 2
    assert pet_service.load_dashboard.call_count == 2


def test_asset_annotations_never_load_identity_dashboards() -> None:
    pet_service = Mock()
    pet_service.list_asset_pet_annotations.return_value = ["pet-box"]
    people_service = Mock()
    people_service.list_asset_face_annotations.return_value = ["face-box"]
    service = RecognitionQueryService(
        Path("/library"),
        people_service=people_service,
        pet_service=pet_service,
    )

    first = service.load_asset_annotations("asset-a")
    second = service.load_asset_annotations("asset-a")

    assert second is first
    assert first.faces == ("face-box",)
    assert first.pets == ("pet-box",)
    people_service.load_dashboard.assert_not_called()
    pet_service.load_dashboard.assert_not_called()
    people_service.list_asset_face_annotations.assert_called_once_with("asset-a")
    pet_service.list_asset_pet_annotations.assert_called_once_with("asset-a")


def test_invalidation_does_not_wait_for_inflight_dashboard_read() -> None:
    first_read_started = threading.Event()
    release_first_read = threading.Event()
    invalidation_done = threading.Event()
    pet_calls = 0

    def _load_pets(*, include_hidden: bool):
        nonlocal pet_calls
        assert include_hidden is True
        pet_calls += 1
        if pet_calls == 1:
            first_read_started.set()
            release_first_read.wait(timeout=2.0)
        return ([_pet("pet-a")], 0)

    pet_service = Mock()
    pet_service.load_dashboard.side_effect = _load_pets
    people_service = Mock()
    people_service.load_dashboard.return_value = ([_person("person-a")], [], 0)
    service = RecognitionQueryService(
        Path("/library"),
        people_service=people_service,
        pet_service=pet_service,
    )
    snapshots = []
    errors = []

    def _load_dashboard() -> None:
        try:
            snapshots.append(service.load_dashboard(False))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    loader = threading.Thread(target=_load_dashboard)
    loader.start()
    assert first_read_started.wait(timeout=1.0)

    invalidator = threading.Thread(
        target=lambda: (service.invalidate(), invalidation_done.set())
    )
    invalidator.start()
    try:
        assert invalidation_done.wait(timeout=1.0)
    finally:
        release_first_read.set()

    invalidator.join(timeout=1.0)
    loader.join(timeout=2.0)

    assert not invalidator.is_alive()
    assert not loader.is_alive()
    assert errors == []
    assert [snapshot.revision for snapshot in snapshots] == [1]
    assert pet_calls == 2

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.people.records import PersonSummary
from iPhoto.people.service import PeopleService
from iPhoto.pets.records import PetSummary
from iPhoto.pets.service import PetService


class _AssetRepository:
    def __init__(self) -> None:
        self.calls = 0

    def get_rows_by_ids(self, asset_ids):
        self.calls += 1
        return {asset_id: {"id": asset_id} for asset_id in asset_ids}


def test_people_asset_validation_is_constant_for_100_identities() -> None:
    assets = _AssetRepository()
    repository = Mock()
    repository.get_asset_ids_by_people.return_value = {
        f"person-{index}": [f"asset-{index}"] for index in range(100)
    }
    summaries = [
        PersonSummary(
            person_id=f"person-{index}",
            name=None,
            key_face_id=f"face-{index}",
            face_count=1,
            thumbnail_path=None,
            created_at="",
        )
        for index in range(100)
    ]
    service = PeopleService(Path("/library"), asset_repository=assets)

    result = service._with_valid_person_asset_counts(summaries, repository)

    repository.get_asset_ids_by_people.assert_called_once()
    assert assets.calls == 1
    assert all(summary.asset_count == 1 for summary in result)


def test_pet_asset_validation_is_constant_for_100_identities() -> None:
    assets = _AssetRepository()
    repository = Mock()
    repository.get_asset_ids_by_pets.side_effect = lambda pet_ids: {
        pet_id: [f"asset-{pet_id}"] for pet_id in pet_ids
    }
    summaries = [
        PetSummary(
            pet_id=f"pet-{index}",
            name=None,
            key_detection_id=f"detection-{index}",
            detection_count=1,
            thumbnail_path=None,
            created_at="",
        )
        for index in range(100)
    ]
    service = PetService(Path("/library"), asset_repository=assets)
    service._identity_redirects = Mock(return_value=[])
    service._face_repository = Mock(return_value=None)

    result = service._with_valid_pet_asset_counts(summaries, repository)

    assert repository.get_asset_ids_by_pets.call_count == 1
    assert assets.calls == 1
    assert all(summary.asset_count == 1 for summary in result)


def test_pet_dashboard_reuses_one_request_scoped_redirect_context() -> None:
    assets = _AssetRepository()
    repository = Mock()
    summary = PetSummary(
        pet_id="pet-a",
        name=None,
        key_detection_id="detection-a",
        detection_count=1,
        thumbnail_path=None,
        created_at="",
    )
    repository.get_pet_summaries.return_value = [summary]
    repository.get_asset_ids_by_pets.return_value = {"pet-a": ["asset-pet-a"]}
    service = PetService(Path("/library"), asset_repository=assets)
    service._repository = repository
    service._identity_redirects = Mock(return_value=[])
    service._face_repository = Mock(return_value=Mock())
    service.pet_status_counts = Mock(return_value={"pending": 2, "retry": 1})

    summaries, pending = service.load_dashboard(include_hidden=True)

    assert summaries[0].asset_count == 1
    assert pending == 3
    service._identity_redirects.assert_called_once_with()
    service._face_repository.assert_called_once_with()

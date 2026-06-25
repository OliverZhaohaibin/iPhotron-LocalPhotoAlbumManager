from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.ports import LocationWriteJobRecord
from iPhoto.application.services.location_assignment_service import LocationAssignmentService
from iPhoto.events.asset_events import AssetMetadataUpdated
from iPhoto.events.bus import EventBus


def test_location_assignment_persists_local_state_creates_job_and_publishes_event(
    tmp_path: Path,
) -> None:
    job = LocationWriteJobRecord(
        job_id="job-1",
        asset_rel="clip.mov",
        asset_path=tmp_path / "clip.mov",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        media_kind="video",
        status="queued",
    )
    assignment_repository = Mock(assign_location=Mock(return_value=job))
    event_bus = EventBus()
    events: list[AssetMetadataUpdated] = []
    event_bus.subscribe(AssetMetadataUpdated, events.append)

    service = LocationAssignmentService(assignment_repository, event_bus)
    result = service.assign(
        asset_path=tmp_path / "clip.mov",
        asset_rel="clip.mov",
        display_name=" Munich ",
        latitude=48.137154,
        longitude=11.576124,
        is_video=True,
        existing_metadata={"codec": "hevc"},
    )

    assert result.write_job is job
    assert result.metadata["codec"] == "hevc"
    assert result.metadata["gps"] == {"lat": 48.137154, "lon": 11.576124}
    assert result.metadata["location"] == "Munich"
    assert result.metadata["location_name"] == "Munich"
    assignment_repository.assign_location.assert_called_once_with(
        asset_rel="clip.mov",
        asset_path=tmp_path / "clip.mov",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        is_video=True,
        metadata_updates=result.metadata,
    )
    assert len(events) == 1
    assert events[0].asset_path == tmp_path / "clip.mov"
    assert events[0].location == "Munich"
    assert events[0].metadata_delta["gps"] == {"lat": 48.137154, "lon": 11.576124}

    event_bus.shutdown()

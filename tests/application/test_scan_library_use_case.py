from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.use_cases.scan_library import (
    ScanLibraryRequest,
    ScanLibraryUseCase,
)


class _Scanner:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def scan(self, *_args, **_kwargs):
        yield from self.rows


def test_scan_library_use_case_merges_chunks_and_emits_ready_batches() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = lambda rows: [
        {**row, "merged": True} for row in rows
    ]
    batches = []
    use_case = ScanLibraryUseCase(
        scanner=_Scanner(
            [
                {"rel": "a.jpg", "thumbnail_state": "ready", "thumb_cache_key": "thumb-a"},
                {"rel": "b.jpg", "thumbnail_state": "ready", "thumb_cache_key": "thumb-b"},
                {"rel": "c.jpg", "thumbnail_state": "ready", "thumb_cache_key": "thumb-c"},
            ]
        ),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            chunk_size=2,
            scan_batch_callback=batches.append,
            scan_job_id="scan_1",
        )
    )

    assert [row["rel"] for row in result.rows] == ["a.jpg", "b.jpg", "c.jpg"]
    assert result.failed_count == 0
    assert [[row["rel"] for row in batch.rows] for batch in batches] == [
        ["a.jpg", "b.jpg"],
        ["c.jpg"],
    ]


def test_scan_library_use_case_splits_ready_ui_batches_after_large_db_chunk() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = lambda rows: list(rows)
    rows = [
        {
            "rel": f"{index}.jpg",
            "thumbnail_state": "ready",
            "thumb_cache_key": f"thumb-{index}",
        }
        for index in range(501)
    ]
    batches = []
    use_case = ScanLibraryUseCase(
        scanner=_Scanner(rows),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            chunk_size=500,
            visible_publish_size=100,
            scan_batch_callback=batches.append,
            scan_job_id="scan_1",
        )
    )

    assert len(result.rows) == 501
    assert [len(call.args[0]) for call in repository.merge_scan_rows.call_args_list] == [
        500,
        1,
    ]
    assert [batch.ready_count for batch in batches] == [100, 100, 100, 100, 100, 1]


def test_scan_library_use_case_tracks_failed_batches() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = RuntimeError("db unavailable")
    failed_batches: list[int] = []
    use_case = ScanLibraryUseCase(
        scanner=_Scanner([{"rel": "a.jpg"}, {"rel": "b.jpg"}]),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            chunk_size=1,
            batch_failed_callback=failed_batches.append,
        )
    )

    assert result.failed_count == 2
    assert failed_batches == [1, 1]


def test_scan_library_use_case_can_collect_without_persisting_chunks() -> None:
    repository = Mock()
    emitted_batches = []
    failed_batches: list[int] = []
    use_case = ScanLibraryUseCase(
        scanner=_Scanner(
            [
                {"rel": "a.jpg", "thumbnail_state": "ready", "thumb_cache_key": "thumb-a"},
                {"rel": "b.jpg", "thumbnail_state": "ready", "thumb_cache_key": "thumb-b"},
            ]
        ),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            chunk_size=1,
            scan_batch_callback=emitted_batches.append,
            batch_failed_callback=failed_batches.append,
            persist_chunks=False,
            scan_job_id="scan_1",
        )
    )

    assert [row["rel"] for row in result.rows] == ["a.jpg", "b.jpg"]
    assert result.failed_count == 0
    repository.merge_scan_rows.assert_not_called()
    assert emitted_batches == []
    assert failed_batches == []

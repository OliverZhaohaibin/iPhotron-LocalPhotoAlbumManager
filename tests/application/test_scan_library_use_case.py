from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.use_cases.scan_library import (
    ScanLibraryRequest,
    ScanLibraryUseCase,
)
from iPhoto.application.use_cases.scan_models import ScanMode


class _Scanner:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def scan(self, *_args, **_kwargs):
        yield from self.rows


class _LegacyScanner:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def scan(
        self,
        _root,
        _include,
        _exclude,
        *,
        existing_index=None,
        progress_callback=None,
    ):
        if progress_callback is not None:
            progress_callback(0, len(self.rows))
        yield from self.rows


def test_scan_library_use_case_merges_chunks_and_emits_persisted_rows() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = lambda rows, **_kwargs: [
        {**row, "merged": True} for row in rows
    ]
    chunks: list[list[dict]] = []
    use_case = ScanLibraryUseCase(
        scanner=_Scanner([{"rel": "a.jpg"}, {"rel": "b.jpg"}, {"rel": "c.jpg"}]),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            chunk_size=2,
            chunk_callback=chunks.append,
        )
    )

    assert result.rows == [{"rel": "a.jpg"}, {"rel": "b.jpg"}, {"rel": "c.jpg"}]
    assert result.failed_count == 0
    assert chunks == [
        [{"rel": "a.jpg", "merged": True}, {"rel": "b.jpg", "merged": True}],
        [{"rel": "c.jpg", "merged": True}],
    ]


def test_scan_library_use_case_forwards_scan_id_to_chunk_merges() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = lambda rows, **_kwargs: list(rows)
    use_case = ScanLibraryUseCase(
        scanner=_Scanner([{"rel": "a.jpg"}]),
        asset_repository=repository,
    )

    use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            scan_id="scan-123",
        )
    )

    repository.merge_scan_rows.assert_called_once_with(
        [{"rel": "a.jpg"}],
        scan_id="scan-123",
    )


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
    emitted_chunks: list[list[dict]] = []
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
            chunk_callback=emitted_chunks.append,
            batch_failed_callback=failed_batches.append,
            persist_chunks=False,
        )
    )

    assert result.rows == [{"rel": "a.jpg"}, {"rel": "b.jpg"}]
    assert result.failed_count == 0
    repository.merge_scan_rows.assert_not_called()
    assert emitted_chunks == []
    assert failed_batches == []


def test_scan_library_use_case_background_mode_does_not_collect_rows() -> None:
    repository = Mock()
    repository.merge_scan_rows.side_effect = lambda rows, **_kwargs: list(rows)
    use_case = ScanLibraryUseCase(
        scanner=_Scanner([{"rel": "a.jpg"}, {"rel": "b.jpg"}]),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            mode=ScanMode.BACKGROUND,
            persist_chunks=True,
            collect_rows=False,
        )
    )

    assert result.rows == []
    assert result.processed_count == 2
    assert result.failed_count == 0


def test_scan_library_use_case_supports_legacy_scanner_signatures() -> None:
    repository = Mock()
    progress_updates: list[tuple[int, int]] = []
    use_case = ScanLibraryUseCase(
        scanner=_LegacyScanner([{"rel": "a.jpg"}]),
        asset_repository=repository,
    )

    result = use_case.execute(
        ScanLibraryRequest(
            root=Path("/library"),
            include=["*.jpg"],
            exclude=[],
            existing_index={"a.jpg": {"rel": "a.jpg"}},
            progress_callback=lambda current, total: progress_updates.append((current, total)),
            persist_chunks=False,
        )
    )

    assert result.rows == [{"rel": "a.jpg"}]
    assert progress_updates == [(0, 1)]
    repository.merge_scan_rows.assert_not_called()

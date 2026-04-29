from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto import app as backend
from iPhoto.cache.index_store import get_global_repository, reset_global_repository


@pytest.fixture(autouse=True)
def clean_global_repository():
    reset_global_repository()
    yield
    reset_global_repository()


def test_rescan_does_not_persist_partial_rows_when_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    store = get_global_repository(album_root)
    store.write_rows([{"rel": "existing.jpg", "id": "existing"}])

    def failing_scan_album(*_args, **_kwargs):
        for index in range(60):
            yield {
                "rel": f"new_{index}.jpg",
                "id": f"new-{index}",
                "mime": "image/jpeg",
            }
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(
        "iPhoto.infrastructure.services.filesystem_media_scanner.scan_album",
        failing_scan_album,
    )

    with pytest.raises(RuntimeError, match="scanner exploded"):
        backend.rescan(album_root)

    assert {row["rel"] for row in store.read_all(filter_hidden=False)} == {
        "existing.jpg"
    }

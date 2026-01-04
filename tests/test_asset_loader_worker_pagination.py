import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.iPhoto.gui.ui.tasks.asset_loader_worker import AssetLoaderWorker


class _StubSignal:
    def __init__(self) -> None:
        self.calls = []

    def emit(self, *args) -> None:
        self.calls.append(args)


class _StubSignals:
    def __init__(self) -> None:
        self.progressUpdated = _StubSignal()
        self.chunkReady = _StubSignal()
        self.finished = _StubSignal()
        self.error = _StubSignal()


class _StubStore:
    def __init__(self, *_args, **_kwargs) -> None:
        self._pages = []
        self.calls = []

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def set_pages(self, pages):
        self._pages = list(pages)

    def get_assets_page(
        self,
        cursor_dt=None,
        cursor_id=None,
        limit=0,
        album_path=None,
        include_subalbums=True,
        filter_hidden=True,
        filter_params=None,
    ):
        self.calls.append((cursor_dt, cursor_id, limit))
        if not self._pages:
            return []
        return self._pages.pop(0)

    def count(self, *_, **__):
        return sum(1 for _ in self.calls)


@pytest.fixture
def stub_signals():
    return _StubSignals()


def test_asset_loader_worker_uses_cursor_pagination(monkeypatch, tmp_path: Path, stub_signals):
    root = tmp_path / "Library"
    root.mkdir(parents=True, exist_ok=True)
    first_file = root / "a.jpg"
    second_file = root / "b.jpg"
    for path in (first_file, second_file):
        path.write_bytes(b"\x00")

    stub_store = _StubStore()
    stub_store.set_pages(
        [
            [
                {
                    "id": 2,
                    "rel": os.path.relpath(first_file, root),
                    "dt": "2024-01-02T00:00:00Z",
                    "ts": 1,
                    "media_type": 0,
                    "bytes": 2 * 1024 * 1024,
                    "w": 2,
                    "h": 1,
                }
            ],
            [
                {
                    "id": 1,
                    "rel": os.path.relpath(second_file, root),
                    "dt": "2024-01-01T00:00:00Z",
                    "ts": 1,
                    "media_type": 0,
                    "bytes": 2 * 1024 * 1024,
                    "w": 2,
                    "h": 1,
                }
            ],
        ]
    )

    monkeypatch.setattr(
        "src.iPhoto.gui.ui.tasks.asset_loader_worker.IndexStore", lambda _root: stub_store
    )

    worker = AssetLoaderWorker(
        root,
        featured=[],
        signals=stub_signals,
        filter_params={},
        library_root=root,
    )

    chunks = list(worker._build_payload_chunks())
    # Expect both items yielded while paging twice
    assert len(chunks) == 1
    assert len(chunks[0]) == 2
    assert stub_store.calls[0][0] is None and stub_store.calls[0][1] is None
    # Second call must carry the cursor from the first page
    assert stub_store.calls[1][0] == "2024-01-02T00:00:00Z"
    assert stub_store.calls[1][1] == 2

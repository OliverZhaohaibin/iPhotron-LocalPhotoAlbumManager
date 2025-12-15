from pathlib import Path

from src.iPhoto.gui.ui.tasks.asset_loader_worker import compute_asset_rows


def test_compute_asset_rows_skips_missing_files(monkeypatch, tmp_path: Path) -> None:
    existing = tmp_path / "keep.jpg"
    existing.write_bytes(b"data")

    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def read_geometry_only(self, *args, **kwargs):
            return [
                {
                    "rel": "keep.jpg",
                    "w": 1,
                    "h": 1,
                    "bytes": 4,
                    "ts": 1,
                    "is_favorite": False,
                },
                {
                    "rel": "missing.jpg",
                    "w": 1,
                    "h": 1,
                    "bytes": 4,
                    "ts": 1,
                    "is_favorite": False,
                },
            ]

        def count(self, **kwargs):
            return 2

    monkeypatch.setattr(
        "src.iPhoto.gui.ui.tasks.asset_loader_worker.IndexStore",
        lambda root: FakeStore(root),
    )

    entries, total = compute_asset_rows(tmp_path, [])

    assert total == 1
    assert [entry["rel"] for entry in entries] == ["keep.jpg"]

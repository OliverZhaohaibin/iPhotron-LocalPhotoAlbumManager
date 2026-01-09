from pathlib import Path
import sqlite3

from iPhoto import app


def test_open_album_skips_hydration_when_disabled(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()

    calls: dict[str, int] = {
        "read_all": 0,
        "read_album_assets": 0,
        "count": 0,
        "sync_favorites": 0,
    }

    class DummyStore:
        def __init__(self, root: Path):
            self.root = root

        def read_all(self):
            calls["read_all"] += 1
            raise AssertionError("read_all should not be called when hydration is disabled")

        def read_album_assets(self, *_args, **_kwargs):
            calls["read_album_assets"] += 1
            raise AssertionError("read_album_assets should not be called when hydration is disabled")

        def count(self, **_kwargs):
            calls["count"] += 1
            return 5

        def write_rows(self, _rows):
            raise AssertionError("write_rows should not run in the lazy path")

        def sync_favorites(self, _featured):
            calls["sync_favorites"] += 1
            return None

    def _fail_ensure_links(*_args, **_kwargs):
        raise AssertionError("_ensure_links should not be invoked without hydration")

    monkeypatch.setattr(app, "IndexStore", DummyStore)
    monkeypatch.setattr(app, "_ensure_links", _fail_ensure_links)

    album = app.open_album(album_dir, autoscan=False, hydrate_index=False)

    assert album.root == album_dir
    assert calls["count"] == 1
    assert calls["read_all"] == 0
    assert calls["read_album_assets"] == 0
    assert calls["sync_favorites"] == 1


def test_open_album_scans_when_empty_autoscan_enabled(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()

    calls: dict[str, int] = {
        "count": 0,
        "write_rows": 0,
        "sync_favorites": 0,
        "ensure_links": 0,
        "scan_album": 0,
    }
    captured_rows: list[list[dict]] = []

    class DummyStore:
        def __init__(self, root: Path):
            self.root = root

        def count(self, **_kwargs):
            calls["count"] += 1
            return 0

        def write_rows(self, rows):
            calls["write_rows"] += 1
            captured_rows.append(rows)

        def sync_favorites(self, _featured):
            calls["sync_favorites"] += 1

        def read_all(self):
            raise AssertionError("read_all should not run in scan path")

        def read_album_assets(self, *_args, **_kwargs):
            raise AssertionError("read_album_assets should not run in scan path")

    def fake_scan_album(root, *_args, **_kwargs):
        calls["scan_album"] += 1
        return [{"rel": "a.jpg"}, {"rel": "b.jpg"}]

    def record_ensure_links(_root, rows, *_args, **_kwargs):
        calls["ensure_links"] += 1
        captured_rows.append(rows)

    monkeypatch.setattr(app, "IndexStore", DummyStore)
    monkeypatch.setattr("iPhoto.io.scanner.scan_album", fake_scan_album)
    monkeypatch.setattr(app, "_ensure_links", record_ensure_links)

    album = app.open_album(album_dir, autoscan=True, hydrate_index=False)

    assert album.root == album_dir
    assert calls["count"] == 1
    assert calls["scan_album"] == 1
    assert calls["write_rows"] == 1
    assert calls["ensure_links"] == 1
    assert calls["sync_favorites"] == 1
    # Ensure the scanned rows were persisted and passed to ensure_links.
    assert captured_rows[0] == [{"rel": "a.jpg"}, {"rel": "b.jpg"}]
    assert captured_rows[1] == [{"rel": "a.jpg"}, {"rel": "b.jpg"}]


def test_open_album_sets_empty_rows_when_no_autoscan(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()

    calls: dict[str, int] = {
        "count": 0,
        "write_rows": 0,
        "sync_favorites": 0,
        "ensure_links": 0,
    }
    captured_rows: list[list[dict]] = []

    class DummyStore:
        def __init__(self, root: Path):
            self.root = root

        def count(self, **_kwargs):
            calls["count"] += 1
            return 0

        def write_rows(self, _rows):
            calls["write_rows"] += 1

        def sync_favorites(self, _featured):
            calls["sync_favorites"] += 1

    def record_ensure_links(_root, rows, *_args, **_kwargs):
        calls["ensure_links"] += 1
        captured_rows.append(rows)

    monkeypatch.setattr(app, "IndexStore", DummyStore)
    monkeypatch.setattr(app, "_ensure_links", record_ensure_links)

    album = app.open_album(album_dir, autoscan=False, hydrate_index=False)

    assert album.root == album_dir
    assert calls["count"] == 1
    assert calls["write_rows"] == 0
    assert calls["ensure_links"] == 1
    assert captured_rows[0] == []
    assert calls["sync_favorites"] == 1


def test_open_album_recovers_on_recoverable_errors(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()

    calls: dict[str, int] = {"count": 0, "sync_favorites": 0}

    class DummyStore:
        def __init__(self, root: Path):
            self.root = root

        def count(self, **_kwargs):
            calls["count"] += 1
            raise sqlite3.Error("db locked")

        def sync_favorites(self, _featured):
            calls["sync_favorites"] += 1
            raise sqlite3.Error("db locked")

    def noop_ensure_links(_root, _rows, *_args, **_kwargs):
        return None

    monkeypatch.setattr(app, "IndexStore", DummyStore)
    monkeypatch.setattr(app, "_ensure_links", noop_ensure_links)

    album = app.open_album(album_dir, autoscan=False, hydrate_index=False)

    assert album.root == album_dir
    assert calls["count"] == 1
    assert calls["sync_favorites"] == 1

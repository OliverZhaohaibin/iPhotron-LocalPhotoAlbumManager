from pathlib import Path
from types import SimpleNamespace

from iPhoto.legacy import app
from iPhoto.config import ALBUM_MANIFEST_NAMES


class FakeScanService:
    def __init__(self) -> None:
        self.prepared: list[dict] = []
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []
        self.synced: list[Path] = []
        self.specific: list[tuple[Path, list[Path]]] = []
        self.paired: list[Path] = []

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepared.append({"root": root, **kwargs})
        return SimpleNamespace(asset_count=5)

    def scan_album(self, root: Path, *, progress_callback=None, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        if progress_callback is not None:
            progress_callback(1, 1)
        return SimpleNamespace(rows=[{"rel": "a.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))

    def sync_manifest_favorites(self, root: Path) -> None:
        self.synced.append(root)

    def scan_specific_files(self, root: Path, files):
        self.specific.append((root, [Path(path) for path in files]))

    def pair_album(self, root: Path):
        self.paired.append(root)
        return ["pair"]


class FakeLifecycleService:
    def __init__(self) -> None:
        self.reconciled: list[tuple[Path, list[dict]]] = []

    def reconcile_missing_scan_rows(self, root: Path, rows: list[dict]) -> int:
        self.reconciled.append((root, rows))
        return 0


def test_open_album_forwards_lazy_open_to_session_scan_service(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    service = FakeScanService()

    monkeypatch.setattr(app, "_scan_service", lambda root, library_root=None: service)

    album = app.open_album(album_dir, autoscan=False, hydrate_index=False)

    assert album.root == album_dir
    assert service.prepared == [
        {
            "root": album_dir,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": True,
        }
    ]


def test_open_album_disables_manifest_favorite_sync_for_library_db(
    monkeypatch,
    tmp_path,
):
    library_root = tmp_path / "library"
    album_dir = library_root / "album"
    album_dir.mkdir(parents=True)
    service = FakeScanService()

    monkeypatch.setattr(app, "_scan_service", lambda root, library_root=None: service)

    app.open_album(album_dir, autoscan=False, library_root=library_root, hydrate_index=False)

    assert service.prepared[0]["sync_manifest_favorites"] is False


def test_rescan_wrapper_uses_session_scan_and_finalize(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    service = FakeScanService()
    lifecycle = FakeLifecycleService()
    progress: list[tuple[int, int]] = []

    monkeypatch.setattr(app, "_scan_service", lambda root, library_root=None: service)
    monkeypatch.setattr(
        app,
        "_lifecycle_service",
        lambda root, library_root=None, scan_service=None: lifecycle,
    )

    rows = app.rescan(
        album_dir,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert rows == [{"rel": "a.jpg"}]
    assert service.scanned == [(album_dir, False)]
    assert service.finalized == [(album_dir, [{"rel": "a.jpg"}])]
    assert lifecycle.reconciled == [(album_dir, [{"rel": "a.jpg"}])]
    assert service.synced == [album_dir]
    assert progress == [(1, 1)]


def test_rescan_wrapper_opens_album_to_create_manifest(tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()

    app.rescan(album_dir)

    assert (album_dir / ALBUM_MANIFEST_NAMES[0]).exists()


def test_scan_specific_files_and_pair_are_compatibility_forwarders(monkeypatch, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    asset = album_dir / "a.jpg"
    service = FakeScanService()

    monkeypatch.setattr(app, "_scan_service", lambda root, library_root=None: service)

    app.scan_specific_files(album_dir, [asset])
    pairs = app.pair(album_dir)

    assert service.specific == [(album_dir, [asset])]
    assert service.paired == [album_dir]
    assert pairs == ["pair"]

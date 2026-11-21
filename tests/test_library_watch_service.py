from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unicodedata import normalize
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.library.tree import AlbumNode
from iPhoto.library.watch_service import (
    LibraryWatchRequest,
    LibraryWatchResult,
    _LibraryWatchWorker,
)
from iPhoto.utils.hashutils import compute_file_id


class _FakeWatcher:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def directories(self) -> list[str]:
        return list(self.paths)

    def removePaths(self, paths: list[str]) -> None:  # noqa: N802
        removed = set(paths)
        self.paths = [path for path in self.paths if path not in removed]

    def addPaths(self, paths: list[str]) -> None:  # noqa: N802
        self.paths.extend(paths)


class _FakeTimer:
    def __init__(self) -> None:
        self.started = False

    def start(self, *_args) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


def test_configure_completes_a_truncated_prepared_tree(qapp, tmp_path: Path) -> None:
    root = tmp_path / "library"
    first = root / "A"
    second = root / "B"
    first.mkdir(parents=True)
    second.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)

    worker.configure(LibraryWatchRequest(1, root, (root, first), False))

    assert len(results) == 1
    assert {node.path for node in results[0].albums} == {first, second}
    assert set(worker._request.paths) == {root, first, second}


def test_tree_snapshot_ignores_dangling_symlinks(qapp, tmp_path: Path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    try:
        (tmp_path / "missing-album").symlink_to(tmp_path / "missing", target_is_directory=True)
        (album / "missing-child").symlink_to(tmp_path / "missing", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)

    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), False))

    assert results[-1].warning is None
    assert [node.path for node in results[-1].albums] == [album]


def test_poll_reports_the_existing_album_whose_signature_changed(qapp) -> None:
    root = Path("/library")
    album = root / "Album"
    worker = _LibraryWatchWorker()
    worker._request = LibraryWatchRequest(4, root, (root, album), True)
    worker._poll_signature = (
        (str(root), 1, 0),
        (str(album), 1, 0),
    )
    worker._signature = lambda _paths: (  # type: ignore[method-assign]
        (str(root), 1, 0),
        (str(album), 2, 0),
    )
    changed: list[tuple[Path, ...]] = []
    worker._emit_snapshot = changed.append  # type: ignore[method-assign]

    worker._poll()

    assert changed == [(album,)]


@pytest.mark.parametrize("polling", [False, True])
def test_root_media_changes_queue_scans_but_internal_writes_do_not(
    qapp, tmp_path: Path, polling: bool
) -> None:
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    controller = LibraryRuntimeController()
    controller._root = tmp_path
    controller._background_watch_generation = 1
    controller._queue_watcher_scan_roots = queue_scans = Mock()
    worker.resultReady.connect(controller._on_background_watch_result)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), polling))

    def notify_change() -> None:
        if polling:
            worker._poll()
        else:
            worker._on_directory_changed(str(tmp_path))

    (tmp_path / "links.json").touch()
    notify_change()
    queue_scans.assert_not_called()

    photo = tmp_path / "photo.jpg"
    photo.touch()
    notify_change()
    queue_scans.assert_called_once_with([tmp_path], root_only=True)

    queue_scans.reset_mock()
    (tmp_path / "photo.ipo").touch()
    notify_change()
    queue_scans.assert_not_called()

    photo.unlink()
    notify_change()
    queue_scans.assert_called_once_with([tmp_path], root_only=True)


@pytest.mark.parametrize("polling", [False, True])
def test_watcher_ignores_artifacts_during_commits_and_queues_real_root_change(
    qapp, tmp_path: Path, polling: bool,
) -> None:
    root = tmp_path.resolve()
    album = root / "Album"
    album.mkdir()
    for index, color in enumerate(("red", "green", "blue")):
        Image.new("RGB", (32, 24), color).save(album / f"photo-{index}.jpg")
    scan_service = LibraryScanService(root)
    controller = LibraryRuntimeController()
    controller._root = root
    controller._background_watch_generation = 1
    controller.bind_scan_service(scan_service)
    controller._live_scan_root = root
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    # Establish the watched album tree before the scan starts.
    worker.configure(LibraryWatchRequest(1, root, (root, album), polling))
    controller._nodes = {node.path: node for node in worker._albums}
    worker.resultReady.connect(controller._on_background_watch_result)

    def notify() -> None:
        if polling:
            worker._poll()
        else:
            worker._on_directory_changed(str(root))

    batches = []

    def on_commit(batch) -> None:
        batches.append(batch)
        notify()
        assert not controller._watch_scan_queue

    try:
        with patch.object(controller, "start_scanning") as dispatch:
            controller._current_scanner_worker = Mock()
            scan_service.scan_album(
                root, include=["*.jpg"], chunk_size=1, persist_chunks=True, scan_batch_callback=on_commit,
            )
            assert len(batches) == 3
            assert get_global_repository(root).count() == 3
            dispatch.assert_not_called()
            Image.new("RGB", (32, 24), "black").save(root / "external.jpg")
            notify()
            notify()
            assert controller._watch_scan_queue == [(root, True)]
            controller._current_scanner_worker = None
            controller._on_watcher_scan_finished(root, True)
            dispatch.assert_called_once()
            assert dispatch.call_args.args[0] == root
            assert dispatch.call_args.kwargs == {"root_only": True}
    finally:
        controller._current_scanner_worker = None


@pytest.mark.parametrize("polling", [False, True])
def test_poll_detects_root_media_overwrite_without_a_directory_change(
    qapp, tmp_path: Path, polling: bool
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"before")
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._poll_timer = timer = _FakeTimer()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), polling))
    assert timer.started
    worker._signature = Mock(return_value=worker._poll_signature)

    photo.write_bytes(b"after overwrite")
    worker._poll()

    assert len(results) == 2
    assert results[-1].root_media_changed
    worker._poll()
    assert len(results) == 2
    if not polling:
        worker._signature.assert_not_called()


def test_root_directory_notification_accepts_equivalent_path(qapp, tmp_path: Path) -> None:
    root = tmp_path / normalize("NFD", "Café")
    root.mkdir()
    alternate_path = normalize("NFC", str(root))
    try:
        if not Path(alternate_path).samefile(root):
            pytest.skip("filesystem distinguishes Unicode spellings")
    except OSError:
        pytest.skip("filesystem distinguishes Unicode spellings")
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, root, (root,), False))

    (root / "photo.jpg").touch()
    worker._on_directory_changed(alternate_path)

    assert results[-1].changed_paths == (root,)
    assert results[-1].root_media_changed


def test_root_watcher_uses_session_scan_filters(qapp, tmp_path: Path) -> None:
    (tmp_path / ".iphoto.album.json").write_text(
        '{"schema":"iPhoto/album@1","title":"Custom",'
        '"filters":{"include":["**/*.jpeg"],"exclude":["**/*.bak"]}}',
        encoding="utf-8",
    )
    service = LibraryScanService(tmp_path)
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), False, service.scan_filters))

    (tmp_path / "ignored.jpg").touch()
    worker._on_directory_changed(str(tmp_path))
    assert not results[-1].root_media_changed

    (tmp_path / "._slide.jpeg").touch()
    worker._on_directory_changed(str(tmp_path))
    assert results[-1].root_media_changed


def test_unreadable_root_media_preserves_signature_and_album_snapshot(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.touch()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), False))
    previous_media = worker._root_media

    original_stat = Path.stat

    def failing_stat(path: Path, *args, **kwargs):
        if path == photo:
            raise PermissionError("Temporarily unreadable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    album = tmp_path / "Album"
    album.mkdir()
    worker._on_directory_changed(str(tmp_path))

    assert results[-1].warning is None
    assert [node.path for node in results[-1].albums] == [album]
    assert not results[-1].root_media_changed
    assert worker._root_media == previous_media


def test_unreadable_child_album_does_not_block_root_scan(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    controller = LibraryRuntimeController()
    controller._root = tmp_path
    controller._background_watch_generation = 1
    controller._queue_watcher_scan_roots = queue_scans = Mock()
    worker.resultReady.connect(controller._on_background_watch_result)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path, album), False))
    queue_scans.reset_mock()
    original_iterdir = Path.iterdir

    def failing_iterdir(path: Path):
        if path == album:
            raise PermissionError("Temporarily unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    (tmp_path / "photo.jpg").touch()
    worker._on_directory_changed(str(tmp_path))
    worker._poll()

    queue_scans.assert_called_once_with([tmp_path], root_only=True)
    assert [node.path for node in controller.list_albums()] == [album]
    controller.shutdown()


@pytest.mark.parametrize("polling", [False, True])
def test_tree_refresh_retries_after_a_temporary_read_failure(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, polling: bool
) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path, album), polling))
    new_album = tmp_path / "New Album"
    new_album.mkdir()
    (album / "new.jpg").touch()
    (tmp_path / "root.jpg").touch()
    original_iterdir = Path.iterdir

    def failing_iterdir(path: Path):
        if path == album:
            raise PermissionError("Temporarily unreadable")
        return original_iterdir(path)

    with monkeypatch.context() as failing:
        failing.setattr(Path, "iterdir", failing_iterdir)
        if polling:
            worker._poll()
        else:
            worker._on_directory_changed(str(album))
            worker._on_directory_changed(str(tmp_path))
        assert results[-1].warning is not None
        assert results[-1].root_media_changed

    worker._poll()

    assert results[-1].warning is None
    assert {node.path for node in results[-1].albums} == {album, new_album}
    assert album in results[-1].changed_paths
    assert set(worker._request.paths) == {tmp_path, album, new_album}
    count = len(results)
    worker._poll()
    assert len(results) == count


@pytest.mark.parametrize("polling", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_rename_during_tree_enumeration_retries_before_pruning_state(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, polling: bool, nested: bool
) -> None:
    root = tmp_path.resolve()
    parent = root / "Parent" if nested else root
    old = parent / "Old"
    old.mkdir(parents=True)
    Image.new("RGB", (16, 16), "blue").save(old / "photo.jpg")
    source_rel = (old / "photo.jpg").relative_to(root).as_posix()
    new = parent / "New"
    destination_rel = (new / "photo.jpg").relative_to(root).as_posix()
    gps = {"lat": 52.5, "lon": 13.4}
    controller = LibraryRuntimeController()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        repository.write_rows([{
            "rel": source_rel, "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "gps": gps, "location": "Manual location",
        }])
        controller._background_watch_generation = 1
        results = []
        worker.resultReady.connect(results.append)
        worker.resultReady.connect(controller._on_background_watch_result)
        worker.configure(LibraryWatchRequest(
            1, root, (root, *controller._nodes), polling,
        ))
        previous_albums = worker._albums
        original_iterdir = Path.iterdir
        renamed = False

        def rename_after_enumeration(path: Path):
            nonlocal renamed
            entries = list(original_iterdir(path))
            if path == parent and not renamed:
                old.rename(new)
                renamed = True
            return iter(entries)

        with patch.object(controller._scan_thread_pool, "start", side_effect=lambda task: task.run()):
            with monkeypatch.context() as racing:
                racing.setattr(Path, "iterdir", rename_after_enumeration)
                worker._on_directory_changed(str(parent))

            assert renamed
            assert results[-1].warning is not None
            assert worker._albums == previous_albums
            assert repository.get_rows_by_rels([source_rel])[source_rel]["is_favorite"]
            # Native notifications need not arrive again: the pending snapshot
            # must also retry on the ordinary poll.
            worker._poll()

        assert results[-1].warning is None
        assert not repository.get_rows_by_rels([source_rel])
        row = repository.get_rows_by_rels([destination_rel])[destination_rel]
        assert row["is_favorite"]
        assert row["gps"] == gps
        assert row["location"] == "Manual location"
        assert row["thumbnail_state"] == "ready"
    finally:
        controller.shutdown()


def test_root_watcher_scan_preserves_child_album_assets(qapp, tmp_path: Path) -> None:
    root = tmp_path.resolve()
    album = root / "Custom"
    album.mkdir()
    (album / ".iphoto.album.json").write_text(
        '{"schema":"iPhoto/album@1","title":"Custom","filters":{"include":["**/*.tiff"]}}',
        encoding="utf-8",
    )
    source = Image.new("RGB", (16, 16), "red")
    source.save(album / "saved.tiff")
    source.save(album / "excluded.jpg")
    source.save(root / "root.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        controller._background_watch_generation = 1
        scan_service = controller.scan_service
        assert scan_service is not None
        scan_service.rescan_album(album, pair_live=False)
        repository = get_global_repository(root)
        repository.set_favorite_status("Custom/saved.tiff", True)
        snapshot = LibraryWatchResult(
            1, (root,), tuple(controller.list_albums()), root_media_changed=True
        )
        finished = []
        controller.scanFinished.connect(lambda path, ok: finished.append((path, ok)))

        with (
            patch.object(
                controller._scan_thread_pool, "start", side_effect=lambda task: task.run()
            ),
            patch.object(repository, "read_all", wraps=repository.read_all) as read_all,
            patch.object(repository, "apply_live_role_updates") as replace_all_roles,
        ):
            controller._on_background_watch_result(snapshot)
            assert repository.get_rows_by_rels(["root.jpg"])
            (root / "root.jpg").unlink()
            controller._on_background_watch_result(snapshot)

        assert finished == [(root, True), (root, True)]
        read_all.assert_not_called()
        replace_all_roles.assert_not_called()
        rows = repository.get_rows_by_rels(["root.jpg", "Custom/saved.tiff", "Custom/excluded.jpg"])
        assert set(rows) == {"Custom/saved.tiff"}
        assert rows["Custom/saved.tiff"]["is_favorite"]
        assert not scan_service.is_scan_scope_complete(root)
    finally:
        controller.shutdown()


@pytest.mark.parametrize("rename", [False, True], ids=["deleted", "renamed"])
def test_background_snapshot_reconciles_disappeared_album_without_scanning_siblings(
    qapp, tmp_path: Path, rename: bool
) -> None:
    root = tmp_path.resolve()
    old_album = root / "Old"
    nested = old_album / "Nested"
    nested.mkdir(parents=True)
    sibling = root / "Custom"
    sibling.mkdir()
    (sibling / ".iphoto.album.json").write_text(
        '{"schema":"iPhoto/album@1","title":"Custom","filters":{"include":["**/*.tiff"]}}',
        encoding="utf-8",
    )
    image = Image.new("RGB", (16, 16), "red")
    image.save(old_album / "photo.jpg")
    image.save(nested / "photo.jpg")
    image.save(sibling / "kept.tiff")
    image.save(sibling / "excluded.jpg")
    controller = LibraryRuntimeController()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        repository.write_rows([
            {"rel": rel, "id": f"as_{compute_file_id(root / rel)}", "is_favorite": True,
             "thumbnail_state": "ready", "thumb_cache_key": rel}
            for rel in ("Old/photo.jpg", "Old/Nested/photo.jpg", "Custom/kept.tiff")
        ])
        gps = {"lat": 52.5, "lon": 13.4}
        for rel in ("Old/photo.jpg", "Old/Nested/photo.jpg"):
            repository.update_asset_geodata(rel, gps=gps, location=f"Manual: {rel}")
        preserved = repository.get_rows_by_rels(["Custom/kept.tiff"])
        controller._background_watch_generation = 1
        worker.resultReady.connect(controller._on_background_watch_result)
        worker.configure(LibraryWatchRequest(1, root, (root, old_album, nested, sibling), False))
        if rename:
            old_album.rename(root / "New")
        else:
            shutil.rmtree(old_album)

        with (
            patch.object(controller._scan_thread_pool, "start", side_effect=lambda task: task.run()),
            patch.object(controller, "start_scanning", wraps=controller.start_scanning) as scans,
            patch.object(repository, "read_all", wraps=repository.read_all) as read_all,
        ):
            worker._on_directory_changed(str(root))

        assert not old_album.exists()
        assert not repository.get_rows_by_rels(["Old/photo.jpg", "Old/Nested/photo.jpg"])
        assert repository.get_rows_by_rels(["Custom/kept.tiff"]) == preserved
        assert not repository.get_rows_by_rels(["Custom/excluded.jpg"])
        assert all(call.args[0] != root for call in scans.call_args_list)
        assert sum(call.args[0] == old_album for call in scans.call_args_list) == 1
        # A renamed destination may use the existing scan snapshot helper; the
        # missing old scope itself never requires a whole-library read.
        if not rename:
            read_all.assert_not_called()
        if rename:
            expected = {"New/photo.jpg", "New/Nested/photo.jpg"}
            renamed_rows = repository.get_rows_by_rels(expected)
            assert set(renamed_rows) == expected
            for rel, row in renamed_rows.items():
                assert row["is_favorite"]
                assert row["gps"] == gps
                assert row["has_gps"]
                assert row["location"] == f"Manual: {rel.replace('New/', 'Old/', 1)}"
                assert row["thumbnail_state"] == "ready"
                assert row["thumb_cache_key"]
            # A second scan must retain the migrated local state as well.
            controller.scan_service.rescan_album(root / "New")
            assert all(row["is_favorite"] and row["gps"] == gps
                       for row in repository.get_rows_by_rels(expected).values())
    finally:
        controller.shutdown()


def test_removed_album_reconciliation_waits_for_its_active_scan(qapp, tmp_path: Path) -> None:
    album = tmp_path / "Removed"
    album.mkdir()
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(tmp_path)
        repository = get_global_repository(tmp_path)
        repository.write_rows([{"rel": "Removed/photo.jpg", "id": "old"}])
        controller._background_watch_generation = 1
        active_worker = SimpleNamespace(cancelled=False, failed=True, root_only=False)
        controller._current_scanner_worker = active_worker
        controller._live_scan_root = album
        album.rmdir()

        with patch.object(controller._scan_thread_pool, "start", side_effect=lambda task: task.run()):
            controller._on_background_watch_result(LibraryWatchResult(1, (tmp_path,), ()))
            assert controller._watch_scan_queue == [(album, False)]
            assert repository.get_rows_by_rels(["Removed/photo.jpg"])
            # A scan whose directory vanished may fail before finalization.
            controller._on_scan_finished(active_worker, album, [])

        assert not repository.get_rows_by_rels(["Removed/photo.jpg"])
        assert not album.exists()
    finally:
        controller.shutdown()


def test_album_rename_preserves_state_when_an_older_scan_has_not_started(qapp, tmp_path: Path) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    old.mkdir()
    Image.new("RGB", (16, 16), "red").save(old / "photo.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        repository.write_rows([{
            "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "location": "Local place",
        }])
        controller._background_watch_generation = 1
        pending = []
        with patch.object(controller._scan_thread_pool, "start", side_effect=pending.append):
            controller.start_scanning(old, ["*.jpg"], [])
            earlier_scan = controller._current_scanner_worker
            new = root / "New"
            old.rename(new)
            controller._on_background_watch_result(
                LibraryWatchResult(1, (root,), (AlbumNode(new, 1, "New", False),))
            )
            while pending:
                pending.pop(0).run()

        assert earlier_scan.failed
        assert not old.exists()
        assert not repository.get_rows_by_rels(["Old/photo.jpg"])
        row = repository.get_rows_by_rels(["New/photo.jpg"])["New/photo.jpg"]
        assert row["is_favorite"]
        assert row["location"] == "Local place"
    finally:
        controller.shutdown()


def test_album_move_into_new_parent_preserves_local_state(qapp, tmp_path: Path) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    old.mkdir()
    Image.new("RGB", (16, 16), "red").save(old / "photo.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        gps = {"lat": 52.5, "lon": 13.4}
        repository.write_rows([{
            "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "gps": gps, "location": "Local place",
        }])
        controller._background_watch_generation = 1
        parent = root / "Parent"
        parent.mkdir()
        destination = parent / "Old"
        old.rename(destination)

        with patch.object(controller._scan_thread_pool, "start", side_effect=lambda task: task.run()):
            controller._on_background_watch_result(LibraryWatchResult(
                1, (root,), (
                    AlbumNode(parent, 1, "Parent", False),
                    AlbumNode(destination, 2, "Old", False),
                ),
            ))

        assert not repository.get_rows_by_rels(["Old/photo.jpg"])
        row = repository.get_rows_by_rels(["Parent/Old/photo.jpg"])["Parent/Old/photo.jpg"]
        assert row["is_favorite"]
        assert row["gps"] == gps
        assert row["location"] == "Local place"
        assert row["thumbnail_state"] == "ready"
        assert row["thumb_cache_key"]
    finally:
        controller.shutdown()


@pytest.mark.parametrize("scan_library", [True, False], ids=["library", "parent"])
@pytest.mark.parametrize("notification", ["before-scan", "after-scan", "starts-scan"])
def test_recursive_scan_leaves_missing_watched_album_for_relocation(
    qapp, tmp_path: Path, scan_library: bool, notification: str,
) -> None:
    root = tmp_path.resolve()
    parent = root / "Parent"
    old, new, kept = (parent / name for name in ("Old", "New", "Kept"))
    old.mkdir(parents=True)
    kept.mkdir()
    photo = old / "photo.jpg"
    Image.new("RGB", (16, 16), "blue").save(photo)
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        store = get_global_repository(root)
        gps = {"lat": 51.1, "lon": 13.1}
        store.write_rows([
            {"rel": "Parent/Old/photo.jpg", "id": f"as_{compute_file_id(photo)}",
             "gps": gps, "location": "My place", "is_favorite": True},
            {"rel": "Parent/Kept/removed.jpg", "id": "removed"},
        ])
        controller._background_watch_generation = 1
        snapshot = LibraryWatchResult(1, (parent,), (
            AlbumNode(parent, 1, "Parent", False), AlbumNode(kept, 2, "Kept", False),
            AlbumNode(new, 2, "New", False),
        ))
        pending = []
        scan_root = root if scan_library else parent

        def scan_from_updated_tree() -> None:
            controller.treeUpdated.disconnect(scan_from_updated_tree)
            controller.start_scanning(scan_root, ["**/*.jpg"], [])

        with patch.object(controller._scan_thread_pool, "start", side_effect=pending.append):
            if notification == "starts-scan":
                controller.treeUpdated.connect(scan_from_updated_tree)
            else:
                controller.start_scanning(scan_root, ["**/*.jpg"], [])
            old.rename(new)
            if notification != "after-scan":
                controller._on_background_watch_result(snapshot)
            pending.pop(0).run()
            assert store.get_rows_by_rels(["Parent/Old/photo.jpg"])
            assert not store.get_rows_by_rels(["Parent/Kept/removed.jpg"])
            if notification == "after-scan":
                controller._on_background_watch_result(snapshot)
            while pending:
                pending.pop(0).run()

        rows = store.get_rows_by_rels(["Parent/Old/photo.jpg", "Parent/New/photo.jpg"])
        assert set(rows) == {"Parent/New/photo.jpg"}
        current = rows["Parent/New/photo.jpg"]
        assert current["is_favorite"] and current["gps"] == gps
        assert current["location"] == "My place"
    finally:
        controller.shutdown()


@pytest.mark.parametrize("nested_destination", [False, True])
def test_moved_child_album_preserves_state_when_its_empty_parent_is_removed(
    qapp, tmp_path: Path, nested_destination: bool
) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    child = old / "Child"
    child.mkdir(parents=True)
    Image.new("RGB", (16, 16), "blue").save(child / "photo.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        gps = {"lat": 52.5, "lon": 13.4}
        repository.write_rows([{
            "rel": "Old/Child/photo.jpg", "id": f"as_{compute_file_id(child / 'photo.jpg')}",
            "is_favorite": True, "gps": gps, "location": "Manual location",
        }])
        controller._background_watch_generation = 1
        parent = root / "Parent" if nested_destination else root
        parent.mkdir(exist_ok=True)
        destination = parent / "New"
        child.rename(destination)
        shutil.rmtree(old)
        nodes = [AlbumNode(destination, 2 if nested_destination else 1, "New", False)]
        if nested_destination:
            nodes.append(AlbumNode(parent, 1, "Parent", False))

        with patch.object(controller._scan_thread_pool, "start", side_effect=lambda task: task.run()):
            controller._on_background_watch_result(LibraryWatchResult(1, (root,), tuple(nodes)))

        assert not repository.get_rows_by_rels(["Old/Child/photo.jpg"])
        destination_rel = (destination / "photo.jpg").relative_to(root).as_posix()
        row = repository.get_rows_by_rels([destination_rel])[destination_rel]
        assert row["is_favorite"]
        assert row["gps"] == gps
        assert row["location"] == "Manual location"
        assert row["thumbnail_state"] == "ready"
        assert row["thumb_cache_key"]
    finally:
        controller.shutdown()


@pytest.mark.parametrize("active_album", ["Old", "Other", None])
@pytest.mark.parametrize("nested_destination", [False, True])
def test_queued_album_reconciliation_follows_successive_renames(
    qapp, tmp_path: Path, active_album: str | None, nested_destination: bool
) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    old.mkdir()
    other = root / "Other"
    other.mkdir()
    Image.new("RGB", (16, 16), "red").save(old / "photo.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        gps = {"lat": 52.5, "lon": 13.4}
        repository.write_rows([{
            "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "gps": gps, "location": "Local place",
        }])
        controller._background_watch_generation = 1
        other_node = AlbumNode(other, 1, "Other", False)
        pending = []
        with patch.object(controller._scan_thread_pool, "start", side_effect=pending.append):
            if active_album is not None:
                controller.start_scanning(root / active_album, ["*.jpg"], [])
            temporary = root / "Temporary"
            old.rename(temporary)
            controller._on_background_watch_result(LibraryWatchResult(
                1, (root,), (other_node, AlbumNode(temporary, 1, "Temporary", False)),
            ))
            parent = root / "Parent" if nested_destination else root
            parent.mkdir(exist_ok=True)
            destination = parent / "Final"
            temporary.rename(destination)
            nodes = [
                other_node,
                AlbumNode(destination, 2 if nested_destination else 1, "Final", False),
            ]
            if nested_destination:
                nodes.append(AlbumNode(parent, 1, "Parent", False))
            controller._on_background_watch_result(LibraryWatchResult(1, (root,), tuple(nodes)))
            while pending:
                pending.pop(0).run()

        assert not old.exists()
        assert not temporary.exists()
        assert not repository.get_rows_by_rels(["Old/photo.jpg", "Temporary/photo.jpg"])
        destination_rel = (destination / "photo.jpg").relative_to(root).as_posix()
        row = repository.get_rows_by_rels([destination_rel])[destination_rel]
        assert row["is_favorite"]
        assert row["gps"] == gps
        assert row["location"] == "Local place"
        assert row["thumbnail_state"] == "ready"
        assert row["thumb_cache_key"]
    finally:
        controller.shutdown()


@pytest.mark.parametrize("root_only", [False, True])
def test_queued_root_scan_does_not_recreate_an_unavailable_library(
    qapp, tmp_path: Path, root_only: bool
) -> None:
    root = tmp_path / "Disconnected"
    controller = LibraryRuntimeController()
    try:
        controller._root = root
        controller._watch_scan_queue = [(root, root_only)]
        with patch.object(controller, "start_scanning") as scans:
            controller._start_next_watcher_scan()
        scans.assert_not_called()
        assert not root.exists()
    finally:
        controller.shutdown()


@pytest.mark.parametrize("nested_destination", [False, True])
@pytest.mark.parametrize("delete_destination", [False, True])
def test_album_reconciliation_waits_for_a_late_successive_rename_snapshot(
    qapp, tmp_path: Path, nested_destination: bool, delete_destination: bool
) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    old.mkdir()
    Image.new("RGB", (16, 16), "red").save(old / "photo.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        gps = {"lat": 52.5, "lon": 13.4}
        repository.write_rows([{
            "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "gps": gps, "location": "Manual location",
        }])
        before = repository.get_rows_by_rels(["Old/photo.jpg"])
        controller._background_watch_generation = 1
        pending = []
        with (
            patch.object(controller._scan_thread_pool, "start", side_effect=pending.append),
            patch.object(controller._watch_service, "refresh") as refresh,
        ):
            temporary = root / "Temporary"
            old.rename(temporary)
            controller._on_background_watch_result(LibraryWatchResult(
                1, (root,), (AlbumNode(temporary, 1, "Temporary", False),),
            ))
            parent = root / "Parent" if nested_destination else root
            parent.mkdir(exist_ok=True)
            destination = parent / "Final"
            if delete_destination:
                shutil.rmtree(temporary)
            else:
                temporary.rename(destination)
            # The first worker runs before the next watcher snapshot arrives.
            worker = pending.pop(0)
            worker.run()
            assert worker.reconciliation_deferred
            assert not worker.failed
            assert repository.get_rows_by_rels(before) == before
            refresh.assert_called_once_with()

            nodes = []
            if not delete_destination:
                nodes.append(AlbumNode(destination, 2 if nested_destination else 1, "Final", False))
            if nested_destination:
                nodes.append(AlbumNode(parent, 1, "Parent", False))
            controller._on_background_watch_result(LibraryWatchResult(1, (root,), tuple(nodes)))
            while pending:
                pending.pop(0).run()

        assert not repository.get_rows_by_rels(["Old/photo.jpg", "Temporary/photo.jpg"])
        assert not controller._watch_scan_relocation_roots
        if not delete_destination:
            rel = (destination / "photo.jpg").relative_to(root).as_posix()
            row = repository.get_rows_by_rels([rel])[rel]
            assert row["is_favorite"]
            assert row["gps"] == gps
            assert row["location"] == "Manual location"
            assert row["thumbnail_state"] == "ready"
            assert row["thumb_cache_key"]
    finally:
        controller.shutdown()


def test_deferred_reconciliation_refreshes_without_another_native_notification(
    qapp, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    old = root / "Old"
    old.mkdir()
    Image.new("RGB", (16, 16), "red").save(old / "photo.jpg")
    controller = LibraryRuntimeController()
    watcher = _LibraryWatchWorker()
    watcher._watcher = _FakeWatcher()  # type: ignore[assignment]
    try:
        controller.bind_path(root)
        repository = get_global_repository(root)
        repository.write_rows([{
            "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(old / 'photo.jpg')}",
            "is_favorite": True, "location": "Manual location",
        }])
        controller._background_watch_generation = 1
        watcher.resultReady.connect(controller._on_background_watch_result)
        watcher.configure(LibraryWatchRequest(1, root, (root, old), False))
        pending = []
        with (
            patch.object(controller._scan_thread_pool, "start", side_effect=pending.append),
            patch.object(controller._watch_service, "refresh", side_effect=watcher.refresh) as refresh,
        ):
            temporary = root / "Temporary"
            old.rename(temporary)
            watcher._on_directory_changed(str(root))
            temporary.rename(root / "Final")
            while pending:
                pending.pop(0).run()

        refresh.assert_called_once_with()
        assert not repository.get_rows_by_rels(["Old/photo.jpg", "Temporary/photo.jpg"])
        row = repository.get_rows_by_rels(["Final/photo.jpg"])["Final/photo.jpg"]
        assert row["is_favorite"]
        assert row["location"] == "Manual location"
        assert row["thumbnail_state"] == "ready"
        assert not controller._watch_scan_relocation_roots
    finally:
        controller.shutdown()

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

if os.environ.get("IPHOTO_RUN_STRESS") != "1":
    pytest.skip(
        "Set IPHOTO_RUN_STRESS=1 to run testbase stress workflows.",
        allow_module_level=True,
    )

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for move worker stress tests.",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtCore",
    reason="QtCore is required for move worker stress tests.",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="QtWidgets is required for move worker stress tests.",
    exc_type=ImportError,
)

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
from iPhoto.bootstrap.library_asset_operation_service import LibraryAssetOperationService
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.core.export import export_asset
from iPhoto.gui.ui.tasks.move_worker import MoveSignals, MoveWorker
from iPhoto.media_classifier import ALL_IMAGE_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


DEFAULT_SAMPLE_LIMIT = 120
DEFAULT_EXPORT_LIMIT = 40

DEFAULT_MAX_SCAN_SECONDS = 300.0
DEFAULT_MAX_MOVE_SECONDS = 120.0
DEFAULT_MAX_DELETE_RESTORE_SECONDS = 180.0
DEFAULT_MAX_EXPORT_SECONDS = 120.0

RAW_SUFFIXES = ALL_IMAGE_EXTENSIONS - IMAGE_EXTENSIONS
SUPPORTED_SUFFIXES = ALL_IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
EXPORT_SUFFIXES = (IMAGE_EXTENSIONS | VIDEO_EXTENSIONS) - RAW_SUFFIXES
IGNORED_PARTS = {".iPhoto", ".iphoto", RECENTLY_DELETED_DIR_NAME}


class _LightweightFilesystemScanner:
    def scan(
        self,
        root: Path,
        _include: Iterable[str],
        _exclude: Iterable[str],
        *,
        existing_index: dict[str, dict[str, Any]] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        del existing_index
        paths = list(_iter_materialized_media(root))
        total = len(paths)
        if progress_callback is not None:
            progress_callback(0, total)
        for index, path in enumerate(paths, start=1):
            yield _build_lightweight_row(root, path)
            if progress_callback is not None:
                progress_callback(index, total)


@pytest.fixture(autouse=True)
def _reset_global_index() -> Iterator[None]:
    reset_global_repository()
    yield
    reset_global_repository()


@pytest.fixture()
def qapp() -> QCoreApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _testbase_root() -> Path:
    configured = os.environ.get("IPHOTO_STRESS_TESTBASE")
    root = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[2] / "tools" / "testbase"
    )
    if not root.exists() or not root.is_dir():
        pytest.skip(f"Stress testbase is unavailable: {root}")
    return root.resolve()


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        pytest.fail(f"{name} must be an integer, got {value!r}")
    return max(1, parsed)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        pytest.fail(f"{name} must be a number, got {value!r}")
    return max(0.0, parsed)


def _sample_limit() -> int:
    return _env_int("IPHOTO_STRESS_SAMPLE_LIMIT", DEFAULT_SAMPLE_LIMIT)


def _export_limit() -> int:
    return _env_int("IPHOTO_STRESS_EXPORT_LIMIT", DEFAULT_EXPORT_LIMIT)


def _supported_testbase_media(root: Path) -> list[Path]:
    paths = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and not any(part in IGNORED_PARTS for part in path.parts)
        and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    paths.sort(key=lambda path: path.relative_to(root).as_posix())
    return paths


def _sample_testbase_media(
    *,
    suffixes: set[str] | frozenset[str] = SUPPORTED_SUFFIXES,
    limit: int | None = None,
) -> tuple[Path, list[Path]]:
    root = _testbase_root()
    selected = [
        path
        for path in _supported_testbase_media(root)
        if path.suffix.lower() in suffixes
    ]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        pytest.skip(f"No supported media found under {root}")
    return root, selected


def _materialize_library(
    tmp_path: Path,
    sources: Iterable[Path],
    *,
    source_root: Path,
) -> tuple[Path, Path, Path, list[Path]]:
    library_root = tmp_path / "Library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_a.mkdir(parents=True)
    album_b.mkdir()
    trash_root.mkdir()
    _write_album_manifest(album_a)

    materialized: list[Path] = []
    for source in sources:
        relative = source.relative_to(source_root)
        destination = album_a / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(source, destination)
        materialized.append(destination)

    return library_root, album_b, trash_root, materialized


def _write_album_manifest(album_root: Path) -> None:
    (album_root / ".iphoto.album.json").write_text(
        (
            '{"schema":"iPhoto/album@1","id":"stress-album-a",'
            '"title":"AlbumA","filters":{}}'
        ),
        encoding="utf-8",
    )


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _make_operation_services(
    library_root: Path,
) -> tuple[LibraryScanService, LibraryAssetOperationService]:
    scan_service = LibraryScanService(
        library_root,
        scanner=_LightweightFilesystemScanner(),
    )
    lifecycle_service = LibraryAssetLifecycleService(
        library_root,
        scan_service=scan_service,
    )
    operation_service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle_service,
    )
    return scan_service, operation_service


def _run_move_plan(plan, *, is_restore: bool = False) -> list[tuple[Path, Path]]:
    assert plan.accepted is True
    assert plan.source_root is not None
    assert plan.destination_root is not None

    signals = MoveSignals()
    errors: list[str] = []
    finished: list[tuple[Path, Path, list, bool, bool]] = []
    signals.error.connect(errors.append)
    signals.finished.connect(
        lambda source, dest, moved, source_ok, dest_ok: finished.append(
            (source, dest, moved, source_ok, dest_ok)
        )
    )
    worker = MoveWorker(
        plan.sources,
        plan.source_root,
        plan.destination_root,
        signals,
        library_root=plan.library_root,
        trash_root=plan.trash_root,
        is_restore=is_restore,
        asset_lifecycle_service=plan.asset_lifecycle_service,
    )
    worker.run()

    assert errors == []
    assert len(finished) == 1
    _source, _dest, moved, source_ok, dest_ok = finished[0]
    assert source_ok is True
    assert dest_ok is True
    return [(Path(original), Path(target)) for original, target in moved]


def _iter_materialized_media(root: Path) -> Iterator[Path]:
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.name == ".DS_Store":
            continue
        if any(part in IGNORED_PARTS for part in candidate.parts):
            continue
        if candidate.suffix.lower() in SUPPORTED_SUFFIXES:
            yield candidate


def _build_lightweight_row(root: Path, asset_path: Path) -> dict[str, Any]:
    stat = asset_path.stat()
    rel = asset_path.resolve().relative_to(root.resolve()).as_posix()
    is_video = asset_path.suffix.lower() in VIDEO_EXTENSIONS
    return {
        "rel": rel,
        "id": f"asset:{rel}",
        "dt": "2024-01-01T00:00:00Z",
        "ts": int(stat.st_mtime * 1_000_000),
        "bytes": stat.st_size,
        "mime": "video/quicktime" if is_video else "image/jpeg",
        "media_type": 1 if is_video else 0,
        "live_role": 0,
    }


def _assert_under(elapsed: float, env_name: str, default: float, label: str) -> None:
    limit = _env_float(env_name, default)
    assert elapsed <= limit, f"{label} took {elapsed:.2f}s; limit is {limit:.2f}s"
    print(f"{label}: {elapsed:.2f}s")


def _indexed_rels(library_root: Path) -> set[str]:
    return {
        str(row["rel"])
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
        if row.get("rel") is not None
    }


def test_scan_real_testbase_sample_stress(tmp_path: Path) -> None:
    source_root, sources = _sample_testbase_media(limit=_sample_limit())
    library_root, _album_b, _trash_root, _materialized = _materialize_library(
        tmp_path,
        sources,
        source_root=source_root,
    )
    service = LibraryScanService(library_root)

    started = time.perf_counter()
    rows = service.rescan_album(library_root)
    elapsed = time.perf_counter() - started

    assert len(rows) == len(sources)
    assert get_global_repository(library_root).count(filter_hidden=False) == len(sources)
    _assert_under(
        elapsed,
        "IPHOTO_STRESS_MAX_SCAN_SECONDS",
        DEFAULT_MAX_SCAN_SECONDS,
        f"scan stress ({len(sources)} files)",
    )


def test_move_real_testbase_sample_stress(tmp_path: Path, qapp: QCoreApplication) -> None:
    source_root, sources = _sample_testbase_media(limit=_sample_limit())
    library_root, album_b, _trash_root, materialized = _materialize_library(
        tmp_path,
        sources,
        source_root=source_root,
    )
    scan_service, operation_service = _make_operation_services(library_root)
    scan_service.rescan_album(library_root, pair_live=False)

    started = time.perf_counter()
    plan = operation_service.plan_move_request(
        materialized,
        album_b,
        current_album_root=library_root / "AlbumA",
    )
    moved = _run_move_plan(plan)
    elapsed = time.perf_counter() - started

    assert len(moved) == len(materialized)
    assert all(not original.exists() for original, _target in moved)
    assert all(target.exists() for _original, target in moved)
    assert len(
        {
            rel
            for rel in _indexed_rels(library_root)
            if rel.startswith("AlbumB/")
        }
    ) == len(materialized)
    _assert_under(
        elapsed,
        "IPHOTO_STRESS_MAX_MOVE_SECONDS",
        DEFAULT_MAX_MOVE_SECONDS,
        f"move stress ({len(materialized)} files)",
    )


def test_delete_restore_real_testbase_sample_stress(
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    source_root, sources = _sample_testbase_media(limit=_sample_limit())
    library_root, _album_b, trash_root, materialized = _materialize_library(
        tmp_path,
        sources,
        source_root=source_root,
    )
    scan_service, operation_service = _make_operation_services(library_root)
    scan_service.rescan_album(library_root, pair_live=False)

    started = time.perf_counter()
    delete_plan = operation_service.plan_delete_request(
        materialized,
        trash_root=trash_root,
    )
    deleted = _run_move_plan(delete_plan)
    assert len(deleted) == len(materialized)

    trash_rels = {
        rel
        for rel in _indexed_rels(library_root)
        if rel.startswith(f"{RECENTLY_DELETED_DIR_NAME}/")
    }
    assert len(trash_rels) == len(materialized)
    trash_rows = [
        row
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
        if str(row.get("rel", "")).startswith(f"{RECENTLY_DELETED_DIR_NAME}/")
    ]
    assert all(row.get("original_rel_path") for row in trash_rows)

    restore_plan = operation_service.plan_restore_request(
        [target for _original, target in deleted],
        trash_root=trash_root,
    )
    assert restore_plan.errors == []
    restored_count = 0
    for batch in restore_plan.batches:
        restored_count += len(_run_move_plan(batch, is_restore=True))
    elapsed = time.perf_counter() - started

    assert restored_count == len(materialized)
    assert not any(target.exists() for _original, target in deleted)
    assert len(
        {
            rel
            for rel in _indexed_rels(library_root)
            if rel.startswith("AlbumA/")
        }
    ) == len(materialized)
    _assert_under(
        elapsed,
        "IPHOTO_STRESS_MAX_DELETE_RESTORE_SECONDS",
        DEFAULT_MAX_DELETE_RESTORE_SECONDS,
        f"delete/restore stress ({len(materialized)} files)",
    )


def test_export_real_testbase_sample_stress(tmp_path: Path) -> None:
    source_root, sources = _sample_testbase_media(
        suffixes=EXPORT_SUFFIXES,
        limit=_export_limit(),
    )
    library_root, _album_b, _trash_root, materialized = _materialize_library(
        tmp_path,
        sources,
        source_root=source_root,
    )
    export_root = library_root / "exported"

    started = time.perf_counter()
    successes = [
        export_asset(path, export_root, library_root)
        for path in materialized
    ]
    elapsed = time.perf_counter() - started

    exported_files = [
        path
        for path in export_root.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    ]
    assert all(successes)
    assert len(exported_files) == len(materialized)
    _assert_under(
        elapsed,
        "IPHOTO_STRESS_MAX_EXPORT_SECONDS",
        DEFAULT_MAX_EXPORT_SECONDS,
        f"export stress ({len(materialized)} files)",
    )

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for the benchmark harness")

from PySide6.QtCore import QObject

from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui import detail_benchmark_harness as harness_module
from iPhoto.gui.detail_benchmark_harness import (
    _RAPID_SWITCH_INTERVAL_MS,
    PackagedDetailBenchmarkHarness,
    _BenchmarkItem,
)


def _snapshot(path: Path, generation: int) -> object:
    return SimpleNamespace(
        transaction=SimpleNamespace(
            generation=generation,
            source_identity=SimpleNamespace(path=path),
        )
    )


def test_rapid_switch_ignores_initial_presentation_until_final_transaction() -> None:
    initial = Path("/benchmark/a.jpg")
    middle = Path("/benchmark/b.jpg")
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._active = _BenchmarkItem(
        path=initial,
        category="jpeg-hot",
        scenario="rapid-switch",
        switch_paths=(middle, initial),
    )
    harness._active_final_transaction = None
    harness._timeout = Mock()
    harness._run_post_present_scenario = Mock(return_value=0)

    with patch.object(harness_module.QTimer, "singleShot") as single_shot:
        # The initial A may be GPU-resident and present before B→A is dispatched.
        harness._on_presented(_snapshot(initial, 10))
        assert not harness._timeout.stop.called  # noqa: S101
        single_shot.assert_not_called()

        harness._active_final_transaction = (initial, 12)
        harness._on_presented(_snapshot(middle, 11))
        harness._on_presented(_snapshot(initial, 10))
        assert not harness._timeout.stop.called  # noqa: S101
        single_shot.assert_not_called()

        harness._on_presented(_snapshot(initial, 12))
        harness._timeout.stop.assert_called_once_with()
        harness._run_post_present_scenario.assert_not_called()
        assert single_shot.call_count == 1  # noqa: S101
        delay, start_scenario = single_shot.call_args.args
        assert delay == 0  # noqa: S101

        start_scenario()

        harness._run_post_present_scenario.assert_called_once_with(harness._active)
        assert single_shot.call_count == 2  # noqa: S101
        completion_delay, completion = single_shot.call_args.args
        assert completion_delay == 0  # noqa: S101
        assert callable(completion)  # noqa: S101


def test_start_opens_and_scans_fresh_disposable_library(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    sample = library / "image.jpg"
    sample.write_bytes(b"image")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "library_root": str(library),
                "samples": [{"path": sample.name, "category": "jpeg"}],
                "repetitions": 1,
            }
        ),
        encoding="utf-8",
    )
    runtime = QObject()
    runtime.open_album_from_path = Mock()
    runtime._gallery_vm = SimpleNamespace(rescan_current=Mock())
    scan_finished = SimpleNamespace(connect=Mock())
    runtime._facade = SimpleNamespace(scanFinished=scan_finished)
    lifecycle = SimpleNamespace(
        stateChanged=SimpleNamespace(connect=Mock()),
        presented=SimpleNamespace(connect=Mock()),
        failed=SimpleNamespace(connect=Mock()),
        cancelled=SimpleNamespace(connect=Mock()),
    )
    runtime._playback = SimpleNamespace(_detail_render_lifecycle=lifecycle)
    app = SimpleNamespace(exit=Mock())
    harness = PackagedDetailBenchmarkHarness(app, runtime, plan, tmp_path / "runtime.json")

    with (
        patch.object(harness_module.QAbstractEventDispatcher, "instance", return_value=None),
        patch.object(harness_module.QTimer, "singleShot"),
    ):
        harness.start()

    runtime.open_album_from_path.assert_called_once_with(library)
    runtime._gallery_vm.rescan_current.assert_called_once_with()
    scan_finished.connect.assert_called_once_with(harness._on_initial_scan_finished)


def test_rapid_switch_interval_exceeds_production_playback_debounce() -> None:
    assert _RAPID_SWITCH_INTERVAL_MS > PLAY_ASSET_DEBOUNCE_MS  # noqa: S101


def test_gui_task_measurement_starts_with_a_fresh_dispatch_timestamp() -> None:
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._measure_gui_tasks = False
    harness._gui_task_started_at = 1.0

    with patch.object(harness_module.time, "perf_counter", return_value=42.0):
        harness._start_gui_task_measurement()

    assert harness._measure_gui_tasks is True  # noqa: S101
    assert harness._gui_task_started_at == 42.0  # noqa: S101

    harness._stop_gui_task_measurement()

    assert harness._measure_gui_tasks is False  # noqa: S101
    assert harness._gui_task_started_at is None  # noqa: S101


def test_dispatcher_awake_does_not_arm_unmeasured_post_present_work() -> None:
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._measure_gui_tasks = False
    harness._gui_task_started_at = 1.0

    harness._on_dispatcher_awake()

    assert harness._gui_task_started_at is None  # noqa: S101


def test_final_transaction_is_bound_from_real_lifecycle_generation() -> None:
    expected = Path("/benchmark/a.jpg")
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._pending_final_source = expected
    harness._pending_final_category = "jpeg-rapid-switch"
    harness._active_final_transaction = None

    with patch.object(harness_module, "emit_detail_event") as emit_event:
        harness._bind_pending_final_transaction(_snapshot(Path("/benchmark/b.jpg"), 11))
        assert harness._active_final_transaction is None  # noqa: S101

        harness._bind_pending_final_transaction(_snapshot(expected, 12))

    assert harness._active_final_transaction == (expected, 12)  # noqa: S101
    assert harness._pending_final_source is None  # noqa: S101
    emit_event.assert_called_once_with(
        "benchmark_sample_started",
        generation=12,
        category="jpeg-rapid-switch",
    )


def test_initial_scan_completion_defers_dispatch_until_store_refresh(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    alias = tmp_path / "library-alias"
    alias.symlink_to(library, target_is_directory=True)
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._library_root = library.absolute()
    harness._scan_ready = False
    harness._gallery_ready = True
    harness._dispatch_scheduled = False
    harness._finish = Mock()
    harness._dispatch_next = Mock()

    with patch.object(harness_module.QTimer, "singleShot") as single_shot:
        harness._on_initial_scan_finished(alias, True)
        harness._on_initial_scan_finished(library, True)

    assert harness._scan_ready is True  # noqa: S101
    assert harness._dispatch_scheduled is True  # noqa: S101
    single_shot.assert_called_once_with(100, harness._dispatch_next)
    harness._finish.assert_not_called()


def test_exact_row_rejects_stale_cached_position() -> None:
    expected = Path("/benchmark/a.jpg")
    store = Mock(
        row_for_path=Mock(return_value=4),
        ensure_row_loaded=Mock(return_value=True),
        asset_at=Mock(return_value=SimpleNamespace(abs_path=Path("/benchmark/b.jpg"))),
    )
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._runtime = SimpleNamespace(_gallery_store=store)

    assert harness._exact_row_for_path(expected) is None  # noqa: S101


def test_exact_row_accepts_symlink_alias_for_same_asset(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    asset = library / "a.jpg"
    asset.write_bytes(b"image")
    alias = tmp_path / "library-alias"
    alias.symlink_to(library, target_is_directory=True)
    store = Mock(
        row_for_path=Mock(return_value=0),
        ensure_row_loaded=Mock(return_value=True),
        asset_at=Mock(return_value=SimpleNamespace(abs_path=asset)),
    )
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._runtime = SimpleNamespace(_gallery_store=store)

    assert harness._exact_row_for_path(alias / asset.name) == 0  # noqa: S101


def test_live_motion_sample_opens_the_hidden_companions_still_row(tmp_path) -> None:
    library = tmp_path / "library"
    live_dir = library / "live photo"
    motion = live_dir / "IMG_3789.MOV"
    still = live_dir / "IMG_3789.HEIC"
    dto = SimpleNamespace(
        abs_path=still,
        is_live=True,
        metadata={"live_partner_rel": "live photo/IMG_3789.MOV"},
    )
    store = Mock()
    store.row_for_path.side_effect = lambda path: 7 if Path(path) == still else None
    store.ensure_row_loaded.return_value = True
    store.asset_at.return_value = dto
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._library_root = library
    harness._runtime = SimpleNamespace(_gallery_store=store)

    assert harness._indexed_target_for_path(motion) == (7, still)  # noqa: S101


def test_initial_dispatch_waits_for_gallery_first_tile(tmp_path) -> None:
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._scan_ready = True
    harness._gallery_ready = False
    harness._dispatch_scheduled = False
    harness._dispatch_next = Mock()

    with patch.object(harness_module.QTimer, "singleShot") as single_shot:
        harness._schedule_initial_dispatch()
        single_shot.assert_not_called()
        harness._on_initial_gallery_ready()
        harness._on_initial_gallery_ready()

    single_shot.assert_called_once_with(100, harness._dispatch_next)


def test_cold_cache_cleanup_accepts_macos_private_var_alias(tmp_path) -> None:
    library = tmp_path / "library"
    cache_root = library / ".iPhoto" / "cache" / "detail-surfaces" / "v2"
    cache_root.mkdir(parents=True)
    (cache_root / "entry.ipsurface").write_bytes(b"cached")
    alias = tmp_path / "library-alias"
    alias.symlink_to(library, target_is_directory=True)
    player = SimpleNamespace(
        clear_frame_cache=Mock(),
        _decode_backend=SimpleNamespace(
            store=SimpleNamespace(root=alias / ".iPhoto" / "cache" / "detail-surfaces" / "v2")
        ),
    )
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._library_root = library
    harness._runtime = SimpleNamespace(
        _playback=SimpleNamespace(_player_view=player),
    )

    harness._prepare_cache_group(
        _BenchmarkItem(
            path=library / "photo.jpg",
            category="jpeg-cold",
            cache_group="cold",
        )
    )

    player.clear_frame_cache.assert_called_once_with()
    assert not cache_root.exists()  # noqa: S101

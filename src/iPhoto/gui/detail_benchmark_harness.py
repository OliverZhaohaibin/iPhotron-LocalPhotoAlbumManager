"""Opt-in packaged Detail benchmark driver using production Gallery actions."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractEventDispatcher, QObject, QTimer, qVersion

from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui.detail_profile import emit_detail_event

_RAPID_SWITCH_INTERVAL_MS = PLAY_ASSET_DEBOUNCE_MS + 15


def _canonical_path(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


@dataclass(frozen=True, slots=True)
class _BenchmarkItem:
    path: Path
    category: str
    cache_group: str = "preserve"
    scenario: str = "open"
    switch_paths: tuple[Path, ...] = ()


class PackagedDetailBenchmarkHarness(QObject):
    """Drive indexed rows and exit after every requested transaction terminates."""

    def __init__(self, app, runtime, plan_path: Path, metadata_path: Path) -> None:
        super().__init__(runtime)
        self._app = app
        self._runtime = runtime
        self._plan_path = plan_path
        self._metadata_path = metadata_path
        self._queue: list[_BenchmarkItem] = []
        self._active: _BenchmarkItem | None = None
        self._active_final_transaction: tuple[Path, int] | None = None
        self._pending_final_source: Path | None = None
        self._pending_final_category: str | None = None
        self._pending_final_is_warmup = False
        self._disk_warmup_item: _BenchmarkItem | None = None
        self._disk_warmup_key: object | None = None
        self._disk_warmup_deadline = 0.0
        self._measure_gui_tasks = False
        self._attempts = 0
        self._completed = 0
        self._failed = 0
        self._interval_ms = 50
        self._timeout_ms = 30_000
        self._library_root: Path | None = None
        self._scan_ready = False
        self._gallery_ready = False
        self._dispatch_scheduled = False
        self._gui_task_started_at: float | None = None
        self._sample_manifest: list[dict[str, str]] = []
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish("transaction_timeout", exit_code=2))

    def start(self) -> None:
        try:
            plan = json.loads(self._plan_path.read_text(encoding="utf-8"))
            library_root = Path(plan["library_root"]).expanduser().absolute()
            repetitions = max(1, int(plan.get("repetitions", 30)))
            self._interval_ms = max(0, int(plan.get("interval_ms", 50)))
            self._timeout_ms = max(1_000, int(plan.get("timeout_ms", 30_000)))
            samples = [
                _BenchmarkItem(
                    path=(library_root / str(item["path"])).absolute(),
                    category=str(item.get("category") or "unknown"),
                    cache_group=str(item.get("cache_group") or "preserve").lower(),
                    scenario=str(item.get("scenario") or "open").lower(),
                    switch_paths=tuple(
                        (library_root / str(relative)).absolute()
                        for relative in item.get("switch_paths", ())
                    ),
                )
                for item in plan["samples"]
            ]
            if not samples:
                raise ValueError("benchmark plan contains no samples")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._finish(f"invalid_plan:{exc}", exit_code=2)
            return

        self._library_root = library_root
        self._sample_manifest = [
            {
                "category": sample.category,
                "suffix": sample.path.suffix.lower(),
                "cache_group": sample.cache_group,
                "scenario": sample.scenario,
            }
            for sample in samples
        ]
        self._queue = [sample for _ in range(repetitions) for sample in samples]
        self._runtime.open_album_from_path(library_root)
        # A freshly created disposable Library has no index.  The normal
        # directory-binding UI starts this scan after opening the root; the
        # harness uses the same Gallery production entry point explicitly.
        scan_finished = getattr(self._runtime._facade, "scanFinished", None)
        if scan_finished is not None:
            scan_finished.connect(self._on_initial_scan_finished)
        asset_model = getattr(self._runtime, "_asset_list_vm", None)
        gallery_ready = getattr(asset_model, "startupGalleryReady", None)
        if gallery_ready is not None:
            gallery_ready.connect(self._on_initial_gallery_ready)
        else:
            self._gallery_ready = True
        self._runtime._gallery_vm.rescan_current()
        coordinator = self._runtime._playback._detail_render_lifecycle
        coordinator.stateChanged.connect(self._on_transaction_state_changed)
        coordinator.presented.connect(self._on_presented)
        coordinator.failed.connect(self._on_failed)
        coordinator.cancelled.connect(self._on_cancelled)
        dispatcher = QAbstractEventDispatcher.instance()
        if dispatcher is not None:
            dispatcher.awake.connect(self._on_dispatcher_awake)
            dispatcher.aboutToBlock.connect(self._on_dispatcher_about_to_block)
        if scan_finished is None:
            # Test doubles and older compatible runtimes may not expose scan
            # completion. Exact row/path validation below still prevents a
            # transaction from opening a stale row while indexing settles.
            self._scan_ready = True
            self._schedule_initial_dispatch()

    def _on_initial_gallery_ready(self) -> None:
        self._gallery_ready = True
        self._schedule_initial_dispatch()

    def _schedule_initial_dispatch(self) -> None:
        if not self._scan_ready or not self._gallery_ready or self._dispatch_scheduled:
            return
        self._dispatch_scheduled = True
        QTimer.singleShot(100, self._dispatch_next)

    def _on_initial_scan_finished(self, root: Path, success: bool) -> None:
        library_root = self._library_root
        if library_root is None:
            return
        emitted_root = _canonical_path(root)
        expected_root = _canonical_path(library_root)
        if emitted_root != expected_root:
            return
        if not success:
            self._finish("library_scan_failed", exit_code=2)
            return
        if self._scan_ready:
            return
        self._scan_ready = True
        # A real user cannot click until Gallery has produced its first usable
        # tile. Gate on both scan completion and startupGalleryReady so cold
        # Detail timing does not begin while the clicked thumbnail still owns
        # the image codec. Then give queued store refresh one event-loop turn.
        self._schedule_initial_dispatch()

    def _exact_row_for_path(self, path: Path) -> int | None:
        """Resolve *path* only when that row currently contains the same asset."""

        store = self._runtime._gallery_store
        row = store.row_for_path(path)
        if row is None or int(row) < 0:
            return None
        row = int(row)
        ensure_loaded = getattr(store, "ensure_row_loaded", None)
        if callable(ensure_loaded) and not ensure_loaded(row):
            return None
        dto = store.asset_at(row)
        if dto is None:
            return None
        try:
            actual = _canonical_path(dto.abs_path)
        except (AttributeError, TypeError):
            return None
        return row if actual == _canonical_path(path) else None

    def _indexed_target_for_path(self, path: Path) -> tuple[int, Path] | None:
        """Resolve a Gallery row and the transaction source it will present.

        A Live Photo motion companion is intentionally hidden from Gallery.
        Benchmark plans may still name that companion to request a motion test;
        in that case open its paired still row and expect the transaction to be
        identified by the still source.
        """

        row = self._exact_row_for_path(path)
        if row is not None:
            return row, path
        if path.suffix.lower() != ".mov":
            return None

        store = self._runtime._gallery_store
        library_root = self._library_root
        still_suffixes = (
            ".HEIC",
            ".heic",
            ".JPG",
            ".jpg",
            ".JPEG",
            ".jpeg",
            ".PNG",
            ".png",
        )
        for suffix in still_suffixes:
            still_path = path.with_suffix(suffix)
            still_row = self._exact_row_for_path(still_path)
            if still_row is None:
                continue
            dto = store.asset_at(still_row)
            metadata = getattr(dto, "metadata", None) or {}
            partner_raw = metadata.get("live_partner_rel")
            if not getattr(dto, "is_live", False) or not partner_raw:
                continue
            partner_path = Path(str(partner_raw))
            if not partner_path.is_absolute():
                if library_root is None:
                    continue
                partner_path = library_root / partner_path
            if _canonical_path(partner_path) == _canonical_path(path):
                return still_row, Path(dto.abs_path)
        return None

    def _on_dispatcher_awake(self) -> None:
        self._gui_task_started_at = (
            time.perf_counter() if self._measure_gui_tasks else None
        )

    def _start_gui_task_measurement(self) -> None:
        """Start a fresh click-to-present dispatcher timing window."""

        self._measure_gui_tasks = True
        self._gui_task_started_at = time.perf_counter()

    def _stop_gui_task_measurement(self) -> None:
        """Discard any dispatcher interval that crosses a terminal boundary."""

        self._measure_gui_tasks = False
        self._gui_task_started_at = None

    def _on_dispatcher_about_to_block(self) -> None:
        started = self._gui_task_started_at
        self._gui_task_started_at = None
        if started is None or self._active is None or not self._measure_gui_tasks:
            return
        generation = self._current_generation()
        if generation <= 0:
            return
        emit_detail_event(
            "gui_task",
            generation=generation,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _dispatch_next(self) -> None:
        if self._active is not None:
            return
        if not self._queue:
            if self._failed:
                self._finish("scenario_failures", exit_code=2)
            else:
                self._finish("complete", exit_code=0)
            return
        item = self._queue.pop(0)
        indexed_targets = [
            self._indexed_target_for_path(path) for path in (item.path, *item.switch_paths)
        ]
        if any(target is None or int(target[0]) < 0 for target in indexed_targets):
            self._attempts += 1
            if self._attempts > 300:
                self._finish(f"asset_not_indexed:{item.path.name}", exit_code=2)
                return
            self._queue.insert(0, item)
            QTimer.singleShot(100, self._dispatch_next)
            return
        self._attempts = 0
        self._active = item
        self._active_final_transaction = None
        self._pending_final_source = None
        self._pending_final_category = None
        self._pending_final_is_warmup = False
        try:
            self._prepare_cache_group(item)
        except OSError as exc:
            self._finish(f"cache_prepare_failed:{exc}", exit_code=2)
            return
        is_disk_warmup = item.cache_group == "disk"
        self._disk_warmup_item = item if is_disk_warmup else None
        if is_disk_warmup:
            self._stop_gui_task_measurement()
        else:
            self._start_gui_task_measurement()
        self._timeout.start(self._timeout_ms)
        self._open_benchmark_path(
            item.path,
            item.category,
            is_final=not item.switch_paths,
            is_warmup=is_disk_warmup,
        )
        for index, switch_path in enumerate(item.switch_paths, start=1):
            is_final_switch = index == len(item.switch_paths)
            QTimer.singleShot(
                index * _RAPID_SWITCH_INTERVAL_MS,
                lambda path=switch_path, is_final=is_final_switch: (
                    self._open_benchmark_path(path, item.category, is_final=is_final)
                ),
            )

    def _open_benchmark_path(
        self,
        path: Path,
        category: str,
        *,
        is_final: bool,
        is_warmup: bool = False,
    ) -> None:
        target = self._indexed_target_for_path(path)
        if target is None:
            self._finish(f"asset_not_indexed:{path.name}", exit_code=2)
            return
        row, transaction_source = target
        if is_final:
            self._pending_final_source = transaction_source
            self._pending_final_category = category
            self._pending_final_is_warmup = bool(is_warmup)
        self._runtime._gallery_vm.open_row(row)
        lifecycle = self._runtime._playback._detail_render_lifecycle
        self._bind_pending_final_transaction(getattr(lifecycle, "snapshot", None))

    def _on_transaction_state_changed(self, snapshot: object) -> None:
        self._bind_pending_final_transaction(snapshot)

    def _bind_pending_final_transaction(self, snapshot: object) -> None:
        expected_source = self._pending_final_source
        transaction = getattr(snapshot, "transaction", None)
        identity = getattr(transaction, "source_identity", None)
        source = getattr(identity, "path", None)
        generation = getattr(transaction, "generation", None)
        if expected_source is None or source is None or generation is None:
            return
        if _canonical_path(source) != _canonical_path(expected_source):
            return
        try:
            resolved_generation = int(generation)
        except (TypeError, ValueError):
            return
        if resolved_generation <= 0:
            return
        category = self._pending_final_category or "unknown"
        is_warmup = getattr(self, "_pending_final_is_warmup", False)
        self._active_final_transaction = (Path(source), resolved_generation)
        self._pending_final_source = None
        self._pending_final_category = None
        self._pending_final_is_warmup = False
        emit_detail_event(
            "benchmark_warmup_started" if is_warmup else "benchmark_sample_started",
            generation=resolved_generation,
            category=category,
        )

    def _prepare_cache_group(self, item: _BenchmarkItem) -> None:
        player = self._runtime._playback._player_view
        if item.cache_group == "cold":
            player.clear_frame_cache()
            root = player._decode_backend.store.root
            library_root = self._library_root
            canonical_root = _canonical_path(root) if root is not None else None
            canonical_library_root = (
                _canonical_path(library_root) if library_root is not None else None
            )
            if (
                canonical_root is not None
                and canonical_library_root is not None
                and canonical_root.is_relative_to(canonical_library_root)
                and canonical_root.exists()
            ):
                shutil.rmtree(canonical_root)
        elif item.cache_group == "disk":
            player.clear_frame_cache()
        elif item.cache_group == "memory":
            player.image_viewer.clear_still_residency()
        elif item.cache_group not in {"gpu", "preserve"}:
            raise OSError(f"unknown cache group: {item.cache_group}")
        if item.scenario == "sidecar-only":
            sidecar = item.path.with_suffix(".ipo")
            if not sidecar.is_file():
                raise OSError(f"sidecar-only sample has no .ipo: {item.path.name}")
            os.utime(sidecar, None)

    def _on_presented(self, snapshot: object) -> None:
        if not self._is_active_final_transaction(snapshot):
            return
        self._timeout.stop()
        self._stop_gui_task_measurement()
        item = self._active
        if item is not None and getattr(self, "_disk_warmup_item", None) is item:
            player = self._runtime._playback._player_view
            self._disk_warmup_key = getattr(player, "_last_presented_decode_key", None)
            self._disk_warmup_deadline = time.monotonic() + 5.0
            QTimer.singleShot(0, self._wait_for_disk_warmup)
            return
        QTimer.singleShot(0, lambda: self._start_post_present_scenario(item))

    def _wait_for_disk_warmup(self) -> None:
        """Start the measured open only after its production surface is on disk."""

        item = self._active
        if item is None or self._disk_warmup_item is not item:
            return
        player = self._runtime._playback._player_view
        key = self._disk_warmup_key
        persisted = getattr(player._decode_backend, "has_persisted_surface", None)
        if key is not None and callable(persisted) and persisted(key):
            player.clear_frame_cache()
            self._disk_warmup_item = None
            self._disk_warmup_key = None
            self._active_final_transaction = None
            self._pending_final_source = None
            self._pending_final_category = None
            self._pending_final_is_warmup = False
            self._start_gui_task_measurement()
            self._timeout.start(self._timeout_ms)
            self._open_benchmark_path(
                item.path,
                item.category,
                is_final=True,
            )
            return
        if time.monotonic() >= self._disk_warmup_deadline:
            self._finish(f"disk_warmup_timeout:{item.path.name}", exit_code=2)
            return
        QTimer.singleShot(10, self._wait_for_disk_warmup)

    def _start_post_present_scenario(self, item: _BenchmarkItem | None) -> None:
        """Run interactions after the QRhi presentation callback has unwound."""

        if item is None or self._active is not item:
            return
        delay = self._run_post_present_scenario(item)
        QTimer.singleShot(delay, self._complete_active)

    def _is_active_final_transaction(self, snapshot: object) -> bool:
        """Return whether a terminal signal belongs to the sample's final open."""

        expected = self._active_final_transaction
        if self._active is None or expected is None:
            return False
        transaction = getattr(snapshot, "transaction", None)
        identity = getattr(transaction, "source_identity", None)
        source = getattr(identity, "path", None)
        generation = getattr(transaction, "generation", None)
        if source is None:
            return False
        try:
            return (
                _canonical_path(source) == _canonical_path(expected[0])
                and int(generation) == expected[1]
            )
        except (TypeError, ValueError):
            return False

    def _run_post_present_scenario(self, item: _BenchmarkItem) -> int:
        scenario = item.scenario
        if scenario in {"open", "sidecar-only", "rapid-switch"}:
            return 0
        if scenario == "fullscreen":
            self._runtime._window.enter_fullscreen()
            QTimer.singleShot(100, self._runtime._window.exit_fullscreen)
            return 150
        if scenario == "lod":
            viewer = self._runtime._playback._player_view.image_viewer
            viewer.zoom_in()
            viewer.zoom_in()
            return 250
        if scenario == "memory-pressure":
            self._runtime._playback._player_view.handle_memory_pressure()
            return 50
        if scenario in {"edit-cancel", "edit-done"}:
            editor = self._runtime._ensure_edit_coordinator()
            editor.enter_edit_mode(item.path)
            if scenario == "edit-done":
                QTimer.singleShot(100, editor._handle_done_clicked)
            else:
                QTimer.singleShot(
                    100,
                    lambda: editor.leave_edit_mode(restore_reason="edit_cancel"),
                )
            return 250
        self._failed += 1
        emit_detail_event(
            "benchmark_scenario_failed",
            generation=self._current_generation(),
            scenario=scenario,
        )
        return 0

    def _complete_active(self) -> None:
        item = self._active
        if item is None:
            return
        emit_detail_event(
            "benchmark_scenario_finished",
            generation=self._current_generation(),
            scenario=item.scenario,
            cache_group=item.cache_group,
        )
        self._completed += 1
        self._active = None
        self._active_final_transaction = None
        self._pending_final_source = None
        self._pending_final_category = None
        self._pending_final_is_warmup = False
        self._disk_warmup_item = None
        self._disk_warmup_key = None
        self._stop_gui_task_measurement()
        QTimer.singleShot(self._interval_ms, self._dispatch_next)

    def _on_failed(self, snapshot: object) -> None:
        if not self._is_active_final_transaction(snapshot):
            return
        self._timeout.stop()
        self._stop_gui_task_measurement()
        self._failed += 1
        self._active = None
        self._active_final_transaction = None
        self._pending_final_source = None
        self._pending_final_category = None
        self._pending_final_is_warmup = False
        self._disk_warmup_item = None
        self._disk_warmup_key = None
        QTimer.singleShot(self._interval_ms, self._dispatch_next)

    def _current_generation(self) -> int:
        lifecycle = self._runtime._playback._detail_render_lifecycle
        return int(lifecycle.current_generation)

    def _on_cancelled(self, _snapshot: object) -> None:
        # Expected during rapid A→B→A.  Only the final transaction completes
        # the active sample; cancellation must not disarm its timeout.
        return

    def _finish(self, result: str, *, exit_code: int) -> None:
        backend = "unknown"
        device = "unknown"
        try:
            viewer = self._runtime._playback._player_view.image_viewer
            backend = viewer.render_backend_name()
            device = viewer.render_device_name()
        except (AttributeError, RuntimeError):
            pass
        qt_platform = os.environ.get("QT_QPA_PLATFORM", "")
        renderer_identity = f"{backend} {device}".lower()
        invalid_renderer = (
            qt_platform.lower() in {"offscreen", "minimal"}
            or backend == "unknown"
            or any(
                marker in renderer_identity
                for marker in (
                    "software",
                    "null",
                    "llvmpipe",
                    "softpipe",
                    "swiftshader",
                    "lavapipe",
                    "basic render",
                )
            )
        )
        if result == "complete" and invalid_renderer:
            result = "invalid_renderer"
            exit_code = 2
        try:
            app_version = version("iPhoto")
        except PackageNotFoundError:
            app_version = "unknown"
        payload: dict[str, Any] = {
            "schema": 1,
            "result": result,
            "completed": self._completed,
            "failed": self._failed,
            "platform": sys.platform,
            "platform_release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "qt": qVersion(),
            "app": "iPhoto",
            "app_version": app_version,
            "commit": os.environ.get("IPHOTO_DETAIL_BENCHMARK_COMMIT", "unknown"),
            "build_label": os.environ.get("IPHOTO_DETAIL_BENCHMARK_BUILD", "unknown"),
            "qt_platform": qt_platform or "default",
            "graphics_backend": backend,
            "graphics_device": device,
            "packaged": "__compiled__" in globals() or bool(getattr(sys, "frozen", False)),
            "samples": self._sample_manifest,
        }
        try:
            self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
            self._metadata_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        finally:
            def _close_and_exit() -> None:
                # MainWindow.closeEvent owns the production shutdown sequence;
                # bypassing it can destroy an active library scan QThread.
                self._runtime._window.close()
                self._app.exit(exit_code)

            QTimer.singleShot(0, _close_and_exit)


def maybe_start_detail_benchmark(app, runtime) -> PackagedDetailBenchmarkHarness | None:
    plan_value = os.environ.get("IPHOTO_DETAIL_BENCHMARK_PLAN", "").strip()
    metadata_value = os.environ.get("IPHOTO_DETAIL_BENCHMARK_METADATA", "").strip()
    if not plan_value:
        return None
    metadata = Path(metadata_value) if metadata_value else Path("benchmark-metadata.json")
    harness = PackagedDetailBenchmarkHarness(app, runtime, Path(plan_value), metadata)
    QTimer.singleShot(0, harness.start)
    return harness


__all__ = ["PackagedDetailBenchmarkHarness", "maybe_start_detail_benchmark"]

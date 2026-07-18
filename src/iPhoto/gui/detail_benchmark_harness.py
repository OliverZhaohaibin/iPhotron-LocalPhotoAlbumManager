"""Opt-in packaged Detail benchmark driver using production Gallery actions."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractEventDispatcher, QObject, QTimer, qVersion

from iPhoto.gui.detail_profile import emit_detail_event


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
        self._attempts = 0
        self._completed = 0
        self._failed = 0
        self._interval_ms = 50
        self._timeout_ms = 30_000
        self._library_root: Path | None = None
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
        self._runtime.gallery.open_album_from_path(library_root)
        coordinator = self._runtime._playback._render_transaction_coordinator()
        coordinator.presented.connect(self._on_presented)
        coordinator.failed.connect(self._on_failed)
        coordinator.cancelled.connect(self._on_cancelled)
        dispatcher = QAbstractEventDispatcher.instance()
        if dispatcher is not None:
            dispatcher.awake.connect(self._on_dispatcher_awake)
            dispatcher.aboutToBlock.connect(self._on_dispatcher_about_to_block)
        QTimer.singleShot(100, self._dispatch_next)

    def _on_dispatcher_awake(self) -> None:
        self._gui_task_started_at = time.perf_counter()

    def _on_dispatcher_about_to_block(self) -> None:
        started = self._gui_task_started_at
        self._gui_task_started_at = None
        if started is None or self._active is None:
            return
        generation = int(self._runtime._detail_vm._request_generation)
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
        indexed_rows = [
            self._runtime._gallery_store.row_for_path(path)
            for path in (item.path, *item.switch_paths)
        ]
        if any(row is None or int(row) < 0 for row in indexed_rows):
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
        try:
            self._prepare_cache_group(item)
        except OSError as exc:
            self._finish(f"cache_prepare_failed:{exc}", exit_code=2)
            return
        self._timeout.start(self._timeout_ms)
        self._open_benchmark_path(
            item.path,
            item.category,
            is_final=not item.switch_paths,
        )
        for index, switch_path in enumerate(item.switch_paths, start=1):
            QTimer.singleShot(
                index,
                lambda path=switch_path, is_final=index == len(item.switch_paths):
                self._open_benchmark_path(path, item.category, is_final=is_final),
            )

    def _open_benchmark_path(
        self,
        path: Path,
        category: str,
        *,
        is_final: bool,
    ) -> None:
        row = self._runtime._gallery_store.row_for_path(path)
        if row is None or int(row) < 0:
            self._finish(f"asset_not_indexed:{path.name}", exit_code=2)
            return
        self._runtime._gallery_vm.open_row(int(row))
        generation = int(self._runtime._detail_vm._request_generation)
        if is_final:
            self._active_final_transaction = (path, generation)
        emit_detail_event(
            "benchmark_sample_started",
            generation=generation,
            category=category,
        )

    def _prepare_cache_group(self, item: _BenchmarkItem) -> None:
        player = self._runtime._playback._player_view
        if item.cache_group == "cold":
            player.clear_frame_cache()
            root = player._decode_backend.store.root
            library_root = self._library_root
            if (
                root is not None
                and library_root is not None
                and root.is_relative_to(library_root)
                and root.exists()
            ):
                shutil.rmtree(root)
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
        delay = self._run_post_present_scenario(self._active)
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
                Path(source).expanduser().absolute() == expected[0]
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
            generation=int(self._runtime._detail_vm._request_generation),
            scenario=scenario,
        )
        return 0

    def _complete_active(self) -> None:
        item = self._active
        if item is None:
            return
        emit_detail_event(
            "benchmark_scenario_finished",
            generation=int(self._runtime._detail_vm._request_generation),
            scenario=item.scenario,
            cache_group=item.cache_group,
        )
        self._completed += 1
        self._active = None
        self._active_final_transaction = None
        QTimer.singleShot(self._interval_ms, self._dispatch_next)

    def _on_failed(self, snapshot: object) -> None:
        if not self._is_active_final_transaction(snapshot):
            return
        self._timeout.stop()
        self._failed += 1
        self._active = None
        self._active_final_transaction = None
        QTimer.singleShot(self._interval_ms, self._dispatch_next)

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
            QTimer.singleShot(0, lambda: self._app.exit(exit_code))


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

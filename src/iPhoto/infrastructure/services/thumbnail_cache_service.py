import shutil
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, Literal, Optional, Set

import numpy as np
from PySide6.QtCore import (
    QFile,
    QIODevice,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap, QTransform

from iPhoto.application.ports import EditServicePort
from iPhoto.core import geo_utils
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.infrastructure.services.performance_events import (
    emit_perf_event,
    monotonic_ms,
    perf_logging_enabled,
)
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    ThumbnailRuntimePolicy,
    speculative_thread_background_mode,
    windows_low_memory_resource_active,
)
from iPhoto.io import sidecar
from iPhoto.utils import image_loader

ThumbnailScrollPhase = Literal["settled", "slow", "medium", "fast"]
ThumbnailScrollIntent = Literal[
    "slow_continuous",
    "directional_dwell",
    "continuous_burst",
    "idle",
]

class ThumbnailWorkerSignals(QObject):
    """Signals emitted by thumbnail generation workers."""

    result = Signal(Path, QSize, QImage, int, object)
    failed = Signal(Path, QSize, str, int, object)


class ThumbnailRequestKind(str, Enum):
    """Resource-isolated classes of Gallery thumbnail work."""

    VISIBLE = "visible"
    GUARD = "guard"
    PREFETCH = "far_speculative"


@dataclass(frozen=True, slots=True)
class ThumbnailRequest:
    path: Path
    size: QSize
    kind: ThumbnailRequestKind
    generation: int
    l2_cache_key: str | None = None
    rank: int = 0


@dataclass(frozen=True, slots=True)
class ThumbnailPrefetchCandidate:
    path: Path
    l2_cache_key: str
    kind: Literal["guard", "far_speculative"]
    rank: int = 0
    row: int = -1


@dataclass(frozen=True, slots=True)
class ThumbnailLoadResult:
    path: Path
    size: QSize
    image: QImage
    generation: int
    kind: ThumbnailRequestKind
    promoted: bool = False


@dataclass(frozen=True, slots=True)
class ThumbnailDemandSnapshot:
    """One complete, immutable thumbnail demand for a Gallery viewport."""

    revision: int
    size: QSize
    visible_paths: tuple[Path, ...]
    guard_paths: tuple[Path, ...] = ()
    speculative_paths: tuple[Path, ...] = ()
    candidates: tuple[ThumbnailPrefetchCandidate, ...] = ()
    phase: ThumbnailScrollPhase = "settled"
    intent: ThumbnailScrollIntent = "idle"

    @property
    def prefetch_paths(self) -> tuple[Path, ...]:
        return self.guard_paths + self.speculative_paths


@dataclass(frozen=True, slots=True)
class ThumbnailMemorySnapshot:
    """Live thumbnail memory accounted by the cache scheduler."""

    budget_bytes: int
    l1_bytes: int
    staging_bytes: int
    active_reservation_bytes: int
    pixmap_pool_bytes: int = 0
    urgent_staging_bytes: int = 0
    far_staging_bytes: int = 0
    slot_count: int = 0
    slot_allocations: int = 0
    slot_reuses: int = 0
    slot_releases: int = 0

    @property
    def live_bytes(self) -> int:
        return self.l1_bytes + self.staging_bytes + self.active_reservation_bytes


class _CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.l2_outcome: str | None = None
        self.cancel_reason: str | None = None

    def cancel(self, reason: str = "cancelled") -> None:
        self.cancel_reason = reason
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()


class ThumbnailGenerationTask(QRunnable):
    """Background task to generate a thumbnail."""

    def __init__(
        self,
        renderer,
        path: Path,
        size: QSize,
        signals: ThumbnailWorkerSignals,
        generation: int,
        kind: ThumbnailRequestKind,
        platform: str,
        cancellation: _CancellationToken | None = None,
        l2_cache_key: str | None = None,
    ):
        super().__init__()
        self._renderer = renderer
        self._path = path
        self._size = size
        self._signals = signals
        self._generation = int(generation)
        self._kind = kind
        self._platform = platform
        self._cancellation = cancellation
        self._l2_cache_key = l2_cache_key

    def run(self):
        try:
            with speculative_thread_background_mode(
                self._platform if self._kind is ThumbnailRequestKind.PREFETCH else ""
            ):
                if self._cancellation is not None and self._cancellation.cancelled():
                    self._signals.failed.emit(
                        self._path,
                        self._size,
                        "cancelled",
                        self._generation,
                        self._kind,
                    )
                    return
                if self._l2_cache_key is None:
                    qimg = self._renderer(self._path, self._size, self._cancellation)
                else:
                    qimg = self._renderer(
                        self._path,
                        self._size,
                        self._cancellation,
                        self._l2_cache_key,
                    )
                if self._cancellation is not None and self._cancellation.cancelled():
                    self._signals.failed.emit(
                        self._path,
                        self._size,
                        "cancelled",
                        self._generation,
                        self._kind,
                    )
                    return
                if qimg is not None and not qimg.isNull():
                    self._signals.result.emit(
                        self._path,
                        self._size,
                        qimg,
                        self._generation,
                        self._kind,
                    )
                else:
                    self._signals.failed.emit(
                        self._path,
                        self._size,
                        "empty_render",
                        self._generation,
                        self._kind,
                    )
        except Exception:
            self._signals.failed.emit(
                self._path,
                self._size,
                "exception",
                self._generation,
                self._kind,
            )

class ThumbnailCacheService(QObject):
    """
    Manages thumbnail caching (Memory + Disk) and asynchronous generation.
    """

    thumbnailReady = Signal(Path)

    def __init__(
        self,
        disk_cache_path: Path,
        memory_limit_mb: int | None = None,
        runtime_policy: ThumbnailRuntimePolicy | None = None,
    ):
        super().__init__()
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._generator = PillowThumbnailGenerator()
        self._edit_service: EditServicePort | None = None

        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._memory_bytes: Dict[str, int] = {}
        self._memory_used_bytes = 0
        self._staging_used_bytes = 0
        self._active_decode_reservations: Dict[str, int] = {}
        self._active_decode_kinds: Dict[str, ThumbnailRequestKind] = {}
        self._runtime_policy = runtime_policy or ThumbnailRuntimePolicy.detect(
            memory_limit_mb=memory_limit_mb
        )
        self._memory_limit_bytes = (
            self._resolve_memory_limit(memory_limit_mb)
            if runtime_policy is not None and memory_limit_mb is not None
            else self._runtime_policy.memory_limit_bytes
        )
        self._pinned_keys: Set[str] = set()
        self._current_l1_demand_keys: Set[str] | None = None
        self._current_guard_keys: Set[str] = set()
        self._current_cache_size: tuple[int, int] | None = None
        self._slot_allocations = 0
        self._slot_reuses = 0
        self._slot_releases = 0
        self._pool_warm = False
        self._pool_saturated = False

        self._pending_tasks: Set[str] = set()
        self._pending_generations: Dict[str, int] = {}
        self._queued_tasks: Dict[str, ThumbnailRequest] = {}
        self._visible_queue: Deque[str] = deque()
        self._visible_queued_at: Dict[str, float] = {}
        self._active_tasks = 0
        self._max_active_jobs = self._runtime_policy.visible_workers
        self._prefetch_pending: Set[str] = set()
        self._prefetch_generations: Dict[str, int] = {}
        self._prefetch_queued: Dict[str, ThumbnailRequest] = {}
        self._prefetch_queue: Deque[str] = deque()
        self._prefetch_kinds: Dict[str, ThumbnailRequestKind] = {}
        self._prefetch_active_tokens: Dict[str, _CancellationToken] = {}
        self._prefetch_promoted_visible: Set[str] = set()
        self._prefetch_active_tasks = 0
        self._guard_active_tasks = 0
        self._far_active_tasks = 0
        self._prefetch_key_order: list[str] = []
        self._prefetch_l2_miss_until: Dict[str, float] = {}
        self._current_phase: ThumbnailScrollPhase = "settled"
        self._current_intent: ThumbnailScrollIntent = "idle"
        self._low_memory_pressure = False
        self._last_low_memory_probe_ms = 0.0
        self._publish_visible: Deque[ThumbnailLoadResult] = deque()
        self._publish_guard: Deque[ThumbnailLoadResult] = deque()
        self._publish_prefetch: Deque[ThumbnailLoadResult] = deque()
        self._publish_keys: Set[str] = set()
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.timeout.connect(self._drain_publish_queue)
        self._eviction_timer = QTimer(self)
        self._eviction_timer.setSingleShot(True)
        self._eviction_timer.timeout.connect(self._drain_l1_evictions)
        self._pending_eviction_target_bytes: int | None = None
        self._pending_stale_eviction = False
        self._failure_cooldown_seconds = 60.0
        self._failure_until: Dict[str, float] = {}
        self._is_shutting_down = False
        self._current_generation = 0
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(self._max_active_jobs)
        self._prefetch_thread_pool = QThreadPool(self)
        self._prefetch_thread_pool.setMaxThreadCount(self._runtime_policy.far_speculative_workers)
        self._prefetch_thread_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._guard_thread_pool = QThreadPool(self)
        self._guard_thread_pool.setMaxThreadCount(self._runtime_policy.prefetch_max_workers)
        emit_perf_event(
            "thumbnail_runtime_policy",
            platform=self._runtime_policy.platform,
            physical_memory_bytes=self._runtime_policy.physical_memory_bytes,
            l1_memory_limit_bytes=self._memory_limit_bytes,
            visible_workers=self._runtime_policy.visible_workers,
            prefetch_max_workers=self._runtime_policy.prefetch_max_workers,
            far_speculative_workers=self._runtime_policy.far_speculative_workers,
            guard_initial_workers=self._runtime_policy.guard_initial_workers,
            guard_max_workers=self._runtime_policy.guard_max_workers,
            guard_staging_limit=self._runtime_policy.guard_staging_limit,
            far_staging_limit=self._runtime_policy.far_staging_limit,
            windows_low_memory_target_ratio=(
                self._runtime_policy.windows_low_memory_target_ratio
            ),
            l1_replacement_threshold_ratio=(
                self._runtime_policy.l1_replacement_threshold_ratio
            ),
            l1_replacement_target_ratio=self._runtime_policy.l1_replacement_target_ratio,
            pixmap_pool_target_ratio=self._runtime_policy.pixmap_pool_target_ratio,
            urgent_pipeline_budget_ratio=(
                self._runtime_policy.urgent_pipeline_budget_ratio
            ),
            far_pipeline_budget_ratio=self._runtime_policy.far_pipeline_budget_ratio,
        )

    def shutdown(self):
        """Prevents new tasks from being submitted and clears pending logic."""
        self._is_shutting_down = True
        self._pending_tasks.clear()
        self._pending_generations.clear()
        self._queued_tasks.clear()
        self._visible_queue.clear()
        self._visible_queued_at.clear()
        self._cancel_all_prefetch("shutdown")
        self._prefetch_l2_miss_until.clear()
        self._clear_publish_queue()
        self._thread_pool.clear()
        self._prefetch_thread_pool.clear()
        self._guard_thread_pool.clear()
        self._active_tasks = 0
        self._prefetch_active_tasks = 0
        self._guard_active_tasks = 0
        self._far_active_tasks = 0
        self._active_decode_reservations.clear()
        self._active_decode_kinds.clear()
        self._staging_used_bytes = 0
        self._release_all_l1_slots("shutdown")
        self._eviction_timer.stop()
        self._pending_eviction_target_bytes = None
        self._pending_stale_eviction = False

    def set_disk_cache_path(self, disk_cache_path: Path) -> None:
        self._is_shutting_down = False
        if self._disk_cache_path == disk_cache_path:
            return
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._release_all_l1_slots("disk_cache_changed")
        self._staging_used_bytes = 0
        self._active_decode_reservations.clear()
        self._active_decode_kinds.clear()
        self._pinned_keys.clear()
        self._current_l1_demand_keys = None
        self._current_guard_keys.clear()
        self._current_cache_size = None
        self._eviction_timer.stop()
        self._pending_eviction_target_bytes = None
        self._pending_stale_eviction = False
        self._pending_tasks.clear()
        self._pending_generations.clear()
        self._queued_tasks.clear()
        self._visible_queue.clear()
        self._visible_queued_at.clear()
        self._cancel_all_prefetch("all_prefetch_cancelled")
        self._prefetch_l2_miss_until.clear()
        self._clear_publish_queue()
        self._failure_until.clear()
        self._low_memory_pressure = False
        self._last_low_memory_probe_ms = 0.0

    def set_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current edit surface used for thumbnail rendering."""

        self._edit_service = edit_service

    def peek_full_thumbnail(self, path: Path, size: QSize) -> Optional[QPixmap]:
        """Return an in-memory thumbnail without touching disk or starting work."""

        if self._is_shutting_down:
            return None

        key = self._cache_key(path, size)
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
            emit_perf_event("thumbnail_cache_hit", tier="L1", key=key)
            return self._memory_cache[key]
        return None

    def has_full_thumbnail(self, path: Path, size: QSize) -> bool:
        """Return whether the full thumbnail is resident without touching its LRU state."""

        if self._is_shutting_down:
            return False
        return self._cache_key(path, size) in self._memory_cache

    def memory_snapshot(self) -> ThumbnailMemorySnapshot:
        urgent_staging = self._urgent_staging_bytes()
        far_staging = self._far_staging_bytes()
        return ThumbnailMemorySnapshot(
            budget_bytes=self._memory_limit_bytes,
            l1_bytes=self._memory_used_bytes,
            staging_bytes=self._staging_used_bytes,
            active_reservation_bytes=sum(self._active_decode_reservations.values()),
            pixmap_pool_bytes=self._memory_used_bytes,
            urgent_staging_bytes=urgent_staging,
            far_staging_bytes=far_staging,
            slot_count=len(self._memory_cache),
            slot_allocations=self._slot_allocations,
            slot_reuses=self._slot_reuses,
            slot_releases=self._slot_releases,
        )

    def get_thumbnail(
        self,
        path: Path,
        size: QSize,
        *,
        priority: str = "normal",
    ) -> Optional[QPixmap]:
        """Compatibility API: memory-only lookup followed by asynchronous request."""

        del priority
        pixmap = self.peek_full_thumbnail(path, size)
        if pixmap is not None:
            return pixmap
        self.request_many(
            [
                ThumbnailRequest(
                    path=path,
                    size=size,
                    kind=ThumbnailRequestKind.VISIBLE,
                    generation=self._current_generation,
                )
            ],
            generation=self._current_generation,
        )
        return None

    def request_many(
        self,
        requests: Iterable[ThumbnailRequest],
        *,
        generation: int,
    ) -> None:
        """Queue deduplicated foreground requests for one viewport generation."""

        if self._is_shutting_down:
            return
        self._current_generation = max(self._current_generation, int(generation))
        for request in requests:
            path = Path(request.path)
            size = request.size
            if request.kind is not ThumbnailRequestKind.VISIBLE:
                self._queue_prefetch(request)
                continue
            key = self._cache_key(path, size)
            if key in self._memory_cache:
                continue
            if self._promote_staged_result(request):
                continue
            active_prefetch = self._prefetch_active_tokens.get(key)
            if active_prefetch is not None and not active_prefetch.cancelled():
                self._promote_active_prefetch(request)
                continue
            self._cancel_prefetch_key(key)
            if key in self._pending_tasks:
                self._pending_generations[key] = max(
                    self._pending_generations.get(key, 0),
                    int(request.generation),
                )
                queued = self._queued_tasks.get(key)
                if queued is not None:
                    self._queued_tasks[key] = ThumbnailRequest(
                        path=queued.path,
                        size=queued.size,
                        kind=ThumbnailRequestKind.VISIBLE,
                        generation=max(queued.generation, int(request.generation)),
                    )
                continue
            if self._failure_until.get(key, 0.0) > time.monotonic():
                continue
            self._queue_visible(request)

    def reconcile_demand(
        self,
        demand: ThumbnailDemandSnapshot,
    ) -> None:
        """Atomically replace visible, guard, and best-effort thumbnail demand."""

        self._current_phase = demand.phase
        self._current_intent = demand.intent
        cache_size = (int(demand.size.width()), int(demand.size.height()))
        if self._current_cache_size is not None and cache_size != self._current_cache_size:
            self._release_all_l1_slots("display_bucket_changed")
        self._current_cache_size = cache_size
        visible = list(dict.fromkeys(Path(path) for path in demand.visible_paths))
        visible_set = set(visible)
        candidate_by_path = {
            Path(candidate.path): candidate for candidate in demand.candidates
        }
        guard = [
            Path(path)
            for path in dict.fromkeys(Path(path) for path in demand.guard_paths)
            if Path(path) not in visible_set
        ]
        guard_set = set(guard)
        speculative = [
            Path(path)
            for path in dict.fromkeys(Path(path) for path in demand.speculative_paths)
            if Path(path) not in visible_set and Path(path) not in guard_set
        ]
        if self._motion_blocks_prefetch(
            self._current_phase,
            self._current_intent,
        ):
            guard = []
            speculative = []
        self._current_generation = max(self._current_generation, int(demand.revision))
        self.pin_visible(visible, demand.size)
        guard, speculative = self._admit_prefetch_paths(
            visible,
            guard,
            speculative,
            demand.size,
        )
        prefetch = guard + speculative
        desired_visible_keys = {self._cache_key(path, demand.size) for path in visible}
        desired_guard_keys = {self._cache_key(path, demand.size) for path in guard}
        desired_prefetch_keys = {
            self._cache_key(path, demand.size) for path in prefetch
        }
        self._current_guard_keys = set(desired_guard_keys)
        record_perf = perf_logging_enabled()
        pending_before = set(self._pending_tasks) if record_perf else set()
        resident = len(desired_visible_keys.intersection(self._memory_cache)) if record_perf else 0
        if record_perf:
            for path in visible:
                emit_perf_event(
                    "thumbnail_visible_entry",
                    path=path,
                    generation=demand.revision,
                    phase=demand.phase,
                    intent=self._current_intent,
                    full=self._cache_key(path, demand.size) in self._memory_cache,
                    miss_reason=self._visible_miss_reason(
                        self._cache_key(path, demand.size)
                    ),
                )
        self._prefetch_key_order = [
            self._cache_key(path, demand.size) for path in prefetch
        ]
        self._refresh_l1_for_demand(
            desired_visible_keys,
            desired_prefetch_keys,
        )
        self._apply_low_memory_pressure_if_needed()
        self._demote_stale_promotions(desired_visible_keys)

        drop_keys = set(self._queued_tasks) - desired_visible_keys
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
            self._visible_queued_at.pop(key, None)
        if drop_keys:
            self._visible_queue = deque(key for key in self._visible_queue if key not in drop_keys)

        self._replace_prefetch_demand(
            desired_prefetch_keys,
            desired_visible_keys,
            demand.revision,
        )

        self.request_many(
            (
                ThumbnailRequest(
                    path,
                    demand.size,
                    ThumbnailRequestKind.VISIBLE,
                    demand.revision,
                )
                for path in visible
            ),
            generation=demand.revision,
        )
        for rank, path in enumerate(prefetch):
            candidate = candidate_by_path.get(path)
            kind = (
                ThumbnailRequestKind.GUARD
                if path in guard
                else ThumbnailRequestKind.PREFETCH
            )
            if (
                kind is ThumbnailRequestKind.PREFETCH
                and self._low_memory_pressure
            ):
                continue
            self._queue_prefetch(
                ThumbnailRequest(
                    path,
                    demand.size,
                    kind,
                    demand.revision,
                    l2_cache_key=(candidate.l2_cache_key if candidate is not None else None),
                    rank=(candidate.rank if candidate is not None else rank),
                )
            )
        self._discard_stale_staged_results(desired_visible_keys, desired_prefetch_keys)
        if record_perf:
            emit_perf_event(
                "thumbnail_demand_reconciled",
                generation=demand.revision,
                visible=len(visible),
                guard=len(guard),
                speculative=len(speculative),
                requested=len(
                    (set(self._pending_tasks) - pending_before).intersection(desired_visible_keys)
                ),
                resident=resident,
                canceled=len(drop_keys),
                queued=len(self._queued_tasks),
                active=self._active_tasks,
                prefetch_queued=len(self._prefetch_queued),
                prefetch_active=self._prefetch_active_tasks,
                phase=demand.phase,
                intent=self._current_intent,
                guard_resident=len(desired_guard_keys.intersection(self._memory_cache)),
                guard_total=len(desired_guard_keys),
            )
        self._drain_generation_queue()

    def pin_visible(self, paths: Iterable[Path], size: QSize) -> None:
        """Keep current visible full thumbnails resident until the next viewport."""

        self._pinned_keys = {self._cache_key(path, size) for path in paths}

    def cancel_stale(self, generation: int) -> None:
        """Drop queued work older than *generation*; active workers self-discard on delivery."""

        self._current_generation = max(self._current_generation, int(generation))
        drop_keys = {
            key
            for key, request in self._queued_tasks.items()
            if request.generation < self._current_generation
        }
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
            self._visible_queued_at.pop(key, None)
        if drop_keys:
            self._visible_queue = deque(key for key in self._visible_queue if key not in drop_keys)
        self._cancel_all_prefetch("all_prefetch_cancelled")
        self._discard_stale_staged_results(set(), set())

    def cancel_pending_except(self, paths: Set[Path], size: QSize) -> None:
        """Cancel queued thumbnail work except for *paths* at *size*."""

        keep_keys = {self._cache_key(path, size) for path in paths}
        self._demote_stale_promotions(keep_keys)
        drop_keys = set(self._queued_tasks) - keep_keys
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
            self._visible_queued_at.pop(key, None)
        if drop_keys:
            self._visible_queue = deque(key for key in self._visible_queue if key not in drop_keys)
        self._replace_prefetch_demand(
            {self._cache_key(path, size) for path in paths},
            set(),
            self._current_generation,
        )

    def _queue_visible(self, request: ThumbnailRequest) -> None:
        key = self._cache_key(request.path, request.size)
        self._pending_tasks.add(key)
        self._pending_generations[key] = max(
            self._pending_generations.get(key, 0),
            int(request.generation),
        )
        self._queued_tasks[key] = request
        self._visible_queued_at.setdefault(key, monotonic_ms())
        self._visible_queue.append(key)
        self._drain_generation_queue()

    def _drain_generation_queue(self) -> None:
        while (
            not self._is_shutting_down
            and self._active_tasks < self._max_active_jobs
            and len(self._publish_visible) < self._runtime_policy.staging_limit
        ):
            next_item = self._pop_next_generation()
            if next_item is None:
                break
            key, path, size, generation = next_item
            self._active_tasks += 1
            if not self._start_generation(
                key,
                path,
                size,
                generation,
                kind=ThumbnailRequestKind.VISIBLE,
            ):
                self._active_tasks = max(0, self._active_tasks - 1)
                request = ThumbnailRequest(
                    path,
                    size,
                    ThumbnailRequestKind.VISIBLE,
                    generation,
                )
                self._queued_tasks[key] = request
                self._visible_queue.appendleft(key)
                self._visible_queued_at.setdefault(key, monotonic_ms())
                break
        self._drain_prefetch_queue()

    def _pop_next_generation(self) -> tuple[str, Path, QSize, int] | None:
        while self._visible_queue:
            key = self._visible_queue.popleft()
            spec = self._queued_tasks.pop(key, None)
            if spec is None:
                continue
            queued_at = self._visible_queued_at.pop(key, monotonic_ms())
            queue_wait_ms = max(0.0, monotonic_ms() - queued_at)
            emit_perf_event(
                "thumbnail_visible_dequeued",
                path=spec.path,
                generation=spec.generation,
                queue_wait_ms=round(queue_wait_ms, 3),
                visible_queued=len(self._queued_tasks),
                visible_active=self._active_tasks,
            )
            return key, spec.path, spec.size, spec.generation
        return None

    def _queue_prefetch(self, request: ThumbnailRequest) -> None:
        key = self._cache_key(request.path, request.size)
        miss_until = self._prefetch_l2_miss_until.get(key, 0.0)
        if miss_until and miss_until <= time.monotonic():
            self._prefetch_l2_miss_until.pop(key, None)
        if self._refresh_staged_prefetch(request):
            return
        if (
            key in self._memory_cache
            or key in self._pending_tasks
            or miss_until > time.monotonic()
            or request.generation < self._current_generation
            or self._motion_blocks_prefetch(
                self._current_phase,
                self._current_intent,
            )
        ):
            return
        if key in self._prefetch_pending:
            self._prefetch_generations[key] = max(
                self._prefetch_generations.get(key, 0),
                request.generation,
            )
            queued = self._prefetch_queued.get(key)
            if queued is not None:
                self._prefetch_kinds[key] = request.kind
                self._prefetch_queued[key] = request
            return
        self._prefetch_pending.add(key)
        self._prefetch_generations[key] = request.generation
        self._prefetch_queued[key] = request
        self._prefetch_kinds[key] = request.kind
        self._prefetch_queue.append(key)
        self._drain_prefetch_queue()

    def _drain_prefetch_queue(self) -> None:
        target = self._prefetch_concurrency_target()
        emit_perf_event(
            "thumbnail_prefetch_concurrency",
            phase=self._current_phase,
            target=target,
            active=self._prefetch_active_tasks,
            queued=len(self._prefetch_queued),
            guard_active=self._guard_active_tasks,
            far_active=self._far_active_tasks,
            intent=self._current_intent,
        )
        self._prefetch_queue = deque(
            sorted(
                self._prefetch_queue,
                key=lambda key: (
                    0
                    if self._prefetch_kinds.get(key) is ThumbnailRequestKind.GUARD
                    else 1,
                    self._prefetch_queued.get(key).rank
                    if self._prefetch_queued.get(key) is not None
                    else 0,
                ),
            )
        )
        while (
            not self._is_shutting_down
            and target > 0
            and self._prefetch_queue
        ):
            key = self._prefetch_queue.popleft()
            request = self._prefetch_queued.pop(key, None)
            if request is None or request.generation < self._current_generation:
                self._prefetch_pending.discard(key)
                self._prefetch_generations.pop(key, None)
                self._prefetch_kinds.pop(key, None)
                continue
            if (
                request.kind is ThumbnailRequestKind.GUARD
                and self._guard_active_tasks >= target
            ):
                self._prefetch_queued[key] = request
                self._prefetch_queue.appendleft(key)
                break
            if (
                request.kind is ThumbnailRequestKind.PREFETCH
                and (
                    self._far_active_tasks >= self._runtime_policy.far_speculative_workers
                    or self._guard_active_tasks > 0
                    or any(
                        queued.kind is ThumbnailRequestKind.GUARD
                        for queued in self._prefetch_queued.values()
                    )
                )
            ):
                self._prefetch_queued[key] = request
                self._prefetch_queue.appendleft(key)
                break
            token = _CancellationToken()
            self._prefetch_active_tokens[key] = token
            self._prefetch_active_tasks += 1
            if request.kind is ThumbnailRequestKind.GUARD:
                self._guard_active_tasks += 1
            else:
                self._far_active_tasks += 1
            if not self._start_generation(
                key,
                request.path,
                request.size,
                request.generation,
                kind=request.kind,
                cancellation=token,
                l2_cache_key=request.l2_cache_key,
            ):
                self._prefetch_active_tokens.pop(key, None)
                self._prefetch_active_tasks = max(0, self._prefetch_active_tasks - 1)
                if request.kind is ThumbnailRequestKind.GUARD:
                    self._guard_active_tasks = max(0, self._guard_active_tasks - 1)
                else:
                    self._far_active_tasks = max(0, self._far_active_tasks - 1)
                self._prefetch_queued[key] = request
                self._prefetch_queue.appendleft(key)
                break

    def _prefetch_concurrency_target(self) -> int:
        guard_queued = sum(
            request.kind is ThumbnailRequestKind.GUARD
            for request in self._prefetch_queued.values()
        )
        if (
            self._is_shutting_down
            or self._motion_blocks_prefetch(
                self._current_phase,
                self._current_intent,
            )
        ):
            return 0
        if (
            guard_queued
            and len(self._publish_guard) >= self._effective_guard_staging_limit()
        ):
            return 0
        if guard_queued or self._guard_active_tasks:
            if self._guard_active_tasks == 0:
                return min(
                    self._runtime_policy.guard_initial_workers,
                    self._runtime_policy.guard_max_workers,
                )
            return min(
                self._runtime_policy.guard_max_workers,
                self._runtime_policy.prefetch_max_workers,
            )
        if not self._current_guard_keys.issubset(self._memory_cache.keys()):
            return 0
        if self._low_memory_pressure:
            return 0
        if len(self._publish_prefetch) >= self._effective_far_staging_limit():
            return 0
        return self._runtime_policy.far_speculative_workers

    @staticmethod
    def _motion_blocks_prefetch(
        phase: ThumbnailScrollPhase,
        intent: ThumbnailScrollIntent,
    ) -> bool:
        return phase in ("medium", "fast") or intent == "continuous_burst"

    def _replace_prefetch_demand(
        self,
        desired_prefetch_keys: Set[str],
        desired_visible_keys: Set[str],
        generation: int,
    ) -> None:
        queued_canceled = 0
        active_canceled = 0
        for key in set(self._prefetch_queued) - desired_prefetch_keys:
            self._prefetch_queued.pop(key, None)
            self._prefetch_pending.discard(key)
            self._prefetch_generations.pop(key, None)
            self._prefetch_kinds.pop(key, None)
            queued_canceled += 1
        self._prefetch_queue = deque(
            key for key in self._prefetch_queue if key in self._prefetch_queued
        )
        desired_active_keys = desired_prefetch_keys | desired_visible_keys
        for key, token in list(self._prefetch_active_tokens.items()):
            if key not in desired_active_keys:
                token.cancel("demand_replaced")
                active_canceled += 1
        if queued_canceled or active_canceled:
            emit_perf_event(
                "thumbnail_prefetch_canceled",
                generation=generation,
                reason="demand_replaced",
                queued=queued_canceled,
                active=active_canceled,
            )

    def _visible_miss_reason(self, key: str) -> str | None:
        if key in self._memory_cache:
            return None
        if key in self._publish_keys:
            return "staging_wait"
        if key in self._prefetch_active_tokens:
            return "guard_active"
        if key in self._prefetch_queued:
            return "l2_not_started"
        if self._prefetch_l2_miss_until.get(key, 0.0) > time.monotonic():
            return "l2_miss_or_decode_error"
        return "hint_unavailable"

    def _promote_active_prefetch(self, request: ThumbnailRequest) -> None:
        key = self._cache_key(request.path, request.size)
        self._prefetch_promoted_visible.add(key)
        self._prefetch_generations[key] = max(
            self._prefetch_generations.get(key, 0),
            int(request.generation),
        )
        self._pending_tasks.add(key)
        self._pending_generations[key] = max(
            self._pending_generations.get(key, 0),
            int(request.generation),
        )
        emit_perf_event(
            "thumbnail_prefetch_promoted",
            path=request.path,
            generation=request.generation,
            foreground_active=self._active_tasks,
            foreground_pending=len(self._pending_tasks),
        )

    def _demote_stale_promotions(self, desired_visible_keys: Set[str]) -> None:
        for key in self._prefetch_promoted_visible - desired_visible_keys:
            self._prefetch_promoted_visible.discard(key)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)

    def _cancel_prefetch_key(self, key: str) -> None:
        self._prefetch_queued.pop(key, None)
        self._prefetch_pending.discard(key)
        self._prefetch_generations.pop(key, None)
        self._prefetch_kinds.pop(key, None)
        self._prefetch_key_order = [
            candidate for candidate in self._prefetch_key_order if candidate != key
        ]
        if key in self._prefetch_promoted_visible:
            self._prefetch_promoted_visible.discard(key)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
        token = self._prefetch_active_tokens.get(key)
        if token is not None:
            token.cancel("key_cancelled")

    def _cancel_all_prefetch(self, reason: str = "all_prefetch_cancelled") -> None:
        for key in self._prefetch_promoted_visible:
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
        self._prefetch_pending.clear()
        self._prefetch_generations.clear()
        self._prefetch_queued.clear()
        self._prefetch_queue.clear()
        self._prefetch_kinds.clear()
        self._prefetch_key_order.clear()
        self._prefetch_promoted_visible.clear()
        for token in self._prefetch_active_tokens.values():
            token.cancel(reason)

    def _admit_prefetch_paths(
        self,
        visible: list[Path],
        guard: list[Path],
        speculative: list[Path],
        size: QSize,
    ) -> tuple[list[Path], list[Path]]:
        if not guard and not speculative:
            return [], []
        observed_bytes = (
            sum(self._memory_bytes.values()) // len(self._memory_bytes)
            if self._memory_bytes
            else max(1, size.width() * size.height() * 4)
        )
        pool_limit = self._pixmap_pool_limit_bytes()
        capacity = max(1, pool_limit // max(1, observed_bytes))
        available = max(0, capacity - len(visible))
        admitted_guard = guard[:available]
        available -= len(admitted_guard)
        admitted_speculative = speculative[:available]
        requested = len(guard) + len(speculative)
        admitted = len(admitted_guard) + len(admitted_speculative)
        if admitted != requested:
            emit_perf_event(
                "thumbnail_prefetch_admission_limited",
                requested=requested,
                admitted=admitted,
                guard_requested=len(guard),
                guard_admitted=len(admitted_guard),
                estimated_pixmap_bytes=observed_bytes,
                l1_memory_limit_bytes=self._memory_limit_bytes,
                pixmap_pool_limit_bytes=pool_limit,
            )
        return admitted_guard, admitted_speculative

    def _promote_staged_result(self, request: ThumbnailRequest) -> bool:
        key = self._cache_key(request.path, request.size)
        matched: ThumbnailLoadResult | None = None
        was_prefetch = False
        for queue in (self._publish_visible, self._publish_guard, self._publish_prefetch):
            remaining: Deque[ThumbnailLoadResult] = deque()
            while queue:
                result = queue.popleft()
                if matched is None and self._cache_key(result.path, result.size) == key:
                    matched = result
                    was_prefetch = result.kind is not ThumbnailRequestKind.VISIBLE
                else:
                    remaining.append(result)
            queue.extend(remaining)
        if matched is None:
            return False
        self._publish_visible.append(
            ThumbnailLoadResult(
                path=matched.path,
                size=matched.size,
                image=matched.image,
                generation=max(matched.generation, request.generation),
                kind=ThumbnailRequestKind.VISIBLE,
                promoted=matched.promoted or was_prefetch,
            )
        )
        emit_perf_event(
            "thumbnail_prefetch_promoted",
            path=request.path,
            generation=request.generation,
            stage="publisher",
            from_prefetch=was_prefetch,
        )
        self._ensure_publish_timer()
        return True

    def _refresh_staged_prefetch(self, request: ThumbnailRequest) -> bool:
        key = self._cache_key(request.path, request.size)
        if key not in self._publish_keys:
            return False
        for queue in (self._publish_visible, self._publish_guard, self._publish_prefetch):
            for index, result in enumerate(queue):
                if self._cache_key(result.path, result.size) != key:
                    continue
                queue[index] = ThumbnailLoadResult(
                    path=result.path,
                    size=result.size,
                    image=result.image,
                    generation=max(result.generation, request.generation),
                    kind=result.kind,
                    promoted=result.promoted,
                )
                return True
        self._publish_keys.discard(key)
        return False

    def _stage_result(
        self,
        result: ThumbnailLoadResult,
        *,
        reservation_key: str | None = None,
    ) -> None:
        key = self._cache_key(result.path, result.size)
        reservation_key = reservation_key or key
        reserved_bytes = self._active_decode_reservations.get(reservation_key, 0)
        if key in self._publish_keys:
            if result.kind is ThumbnailRequestKind.VISIBLE:
                self._promote_staged_result(
                    ThumbnailRequest(
                        result.path,
                        result.size,
                        ThumbnailRequestKind.VISIBLE,
                        result.generation,
                    )
                )
            self._active_decode_reservations.pop(reservation_key, None)
            self._active_decode_kinds.pop(reservation_key, None)
            return
        image_bytes = self._image_bytes(result.image)
        far_staging_budget = max(
            1,
            min(32 * 1024 * 1024, self._memory_limit_bytes // 10),
        )
        while (
            result.kind is ThumbnailRequestKind.PREFETCH
            and self._far_staging_bytes() + image_bytes > min(
                far_staging_budget,
                self._far_pipeline_limit_bytes(),
            )
        ):
            queue = (
                self._publish_prefetch
                if self._publish_prefetch
                else None
            )
            if queue is None:
                break
            dropped = queue.pop()
            self._publish_keys.discard(self._cache_key(dropped.path, dropped.size))
            self._staging_used_bytes = max(
                0,
                self._staging_used_bytes - self._image_bytes(dropped.image),
            )
        if (
            result.kind is ThumbnailRequestKind.PREFETCH
            and self._far_staging_bytes() + image_bytes
            > min(far_staging_budget, self._far_pipeline_limit_bytes())
        ):
            self._active_decode_reservations.pop(reservation_key, None)
            self._active_decode_kinds.pop(reservation_key, None)
            emit_perf_event(
                "thumbnail_staging_dropped",
                path=result.path,
                reason="far_byte_budget",
                staging_bytes=self._staging_used_bytes,
                staging_budget_bytes=min(
                    far_staging_budget,
                    self._far_pipeline_limit_bytes(),
                ),
            )
            return
        projected_live = (
            self.memory_snapshot().live_bytes
            - reserved_bytes
            + image_bytes
        )
        self._evict_l1_until(
            max(
                0,
                self._memory_limit_bytes - projected_live + self._memory_used_bytes,
            ),
            protected_keys={key},
            reason_prefix="staging_admission",
            max_items=8,
            budget_ms=2.0,
        )
        projected_live = self.memory_snapshot().live_bytes - reserved_bytes + image_bytes
        if (
            projected_live > self._memory_limit_bytes
            and result.kind is ThumbnailRequestKind.PREFETCH
        ):
            self._active_decode_reservations.pop(reservation_key, None)
            self._active_decode_kinds.pop(reservation_key, None)
            emit_perf_event(
                "thumbnail_staging_dropped",
                path=result.path,
                reason="hard_budget",
                staging_bytes=self._staging_used_bytes,
                image_bytes=image_bytes,
                memory_limit_bytes=self._memory_limit_bytes,
            )
            return
        if projected_live > self._memory_limit_bytes:
            emit_perf_event(
                "thumbnail_urgent_staging_overcommit",
                path=result.path,
                kind=result.kind.value,
                projected_live_bytes=projected_live,
                memory_limit_bytes=self._memory_limit_bytes,
            )
        if result.kind is ThumbnailRequestKind.GUARD:
            guard_limit = self._effective_guard_staging_limit()
            if len(self._publish_guard) >= guard_limit:
                if self._publish_prefetch:
                    dropped = self._publish_prefetch.pop()
                    self._publish_keys.discard(self._cache_key(dropped.path, dropped.size))
                    self._staging_used_bytes = max(
                        0,
                        self._staging_used_bytes - self._image_bytes(dropped.image),
                    )
            if len(self._publish_guard) >= guard_limit:
                emit_perf_event(
                    "thumbnail_urgent_staging_limit_bypassed",
                    path=result.path,
                    reason="guard_queue_full",
                    depth=len(self._publish_guard),
                )
                # Guard work is a deadline, not best effort.  The worker admission
                # credit prevents this in normal operation; retain the result if
                # a policy override makes the item limit smaller than concurrency.
        elif result.kind is ThumbnailRequestKind.PREFETCH:
            far_limit = self._effective_far_staging_limit()
            if self._low_memory_pressure:
                self._active_decode_reservations.pop(reservation_key, None)
                self._active_decode_kinds.pop(reservation_key, None)
                emit_perf_event(
                    "thumbnail_staging_dropped",
                    path=result.path,
                    reason="far_disabled_for_memory_pressure",
                    depth=len(self._publish_prefetch),
                )
                return
            if len(self._publish_prefetch) >= far_limit:
                self._active_decode_reservations.pop(reservation_key, None)
                self._active_decode_kinds.pop(reservation_key, None)
                emit_perf_event(
                    "thumbnail_staging_dropped",
                    path=result.path,
                    reason="far_queue_full",
                    depth=len(self._publish_prefetch),
                )
                return
        nonvisible_depth = len(self._publish_guard) + len(self._publish_prefetch)
        if result.kind is not ThumbnailRequestKind.VISIBLE and (
            nonvisible_depth >= self._runtime_policy.staging_limit
        ):
            if result.kind is ThumbnailRequestKind.GUARD and self._publish_prefetch:
                dropped = self._publish_prefetch.pop()
                self._publish_keys.discard(self._cache_key(dropped.path, dropped.size))
                self._staging_used_bytes = max(
                    0,
                    self._staging_used_bytes - self._image_bytes(dropped.image),
                )
            else:
                if result.kind is not ThumbnailRequestKind.GUARD:
                    self._active_decode_reservations.pop(reservation_key, None)
                    self._active_decode_kinds.pop(reservation_key, None)
                    emit_perf_event(
                        "thumbnail_staging_dropped",
                        path=result.path,
                        reason="queue_full",
                        depth=nonvisible_depth,
                    )
                    return
        self._active_decode_reservations.pop(reservation_key, None)
        self._active_decode_kinds.pop(reservation_key, None)
        self._publish_keys.add(key)
        queue = (
            self._publish_visible
            if result.kind is ThumbnailRequestKind.VISIBLE
            else self._publish_guard
            if result.kind is ThumbnailRequestKind.GUARD
            else self._publish_prefetch
        )
        queue.append(result)
        self._staging_used_bytes += image_bytes
        emit_perf_event(
            "thumbnail_staging_depth",
            visible=len(self._publish_visible),
            guard=len(self._publish_guard),
            prefetch=len(self._publish_prefetch),
        )
        self._ensure_publish_timer()

    def _ensure_publish_timer(self) -> None:
        if not self._publish_timer.isActive():
            self._publish_timer.start(0)

    def _drain_publish_queue(self) -> None:
        started = monotonic_ms()
        processed = 0
        converted = 0
        publish_max_items = self._effective_publish_max_items()
        publish_budget_ms = self._effective_publish_budget_ms()
        while self._publish_visible or self._publish_guard or self._publish_prefetch:
            if (
                processed >= publish_max_items
                and not self._publish_visible
            ):
                break
            result = (
                self._publish_visible.popleft()
                if self._publish_visible
                else self._publish_guard.popleft()
                if self._publish_guard
                else self._publish_prefetch.popleft()
                if self._publish_prefetch
                else None
            )
            if result is None:
                break
            key = self._cache_key(result.path, result.size)
            self._publish_keys.discard(key)
            self._staging_used_bytes = max(
                0,
                self._staging_used_bytes - self._image_bytes(result.image),
            )
            visible = result.kind is ThumbnailRequestKind.VISIBLE
            fresh_visible = visible and result.generation >= self._current_generation
            current_prefetch = (
                not visible
                and self._prefetch_results_allowed()
                and key in self._prefetch_key_order
            )
            relevant = (
                not self._is_shutting_down
                and (fresh_visible or current_prefetch)
            )
            if relevant and not result.image.isNull():
                convert_started = monotonic_ms()
                stored = self._store_image_in_l1(key, result.image)
                convert_ms = max(0.0, monotonic_ms() - convert_started)
                emit_perf_event(
                    "thumbnail_pixmap_converted",
                    path=result.path,
                    kind=result.kind.value,
                    display_bucket=result.size.width(),
                    elapsed_ms=round(convert_ms, 3),
                )
                emit_perf_event(
                    "thumbnail_qimage_to_qpixmap",
                    path=result.path,
                    kind=result.kind.value,
                    elapsed_ms=round(convert_ms, 3),
                )
                if not stored and result.kind is not ThumbnailRequestKind.PREFETCH:
                    retry_queue = (
                        self._publish_visible
                        if result.kind is ThumbnailRequestKind.VISIBLE
                        else self._publish_guard
                    )
                    retry_queue.appendleft(result)
                    self._publish_keys.add(key)
                    self._staging_used_bytes += self._image_bytes(result.image)
                    emit_perf_event(
                        "thumbnail_urgent_publish_deferred",
                        path=result.path,
                        kind=result.kind.value,
                    )
                    break
                converted += int(stored)
                if visible:
                    emit_perf_event(
                        "thumbnail_generate_finished",
                        path=result.path,
                        width=result.size.width(),
                        height=result.size.height(),
                        pending=len(self._pending_tasks),
                        promoted=result.promoted,
                    )
                    if stored:
                        self.thumbnailReady.emit(result.path)
                else:
                    emit_perf_event(
                        "thumbnail_prefetch_finished",
                        path=result.path,
                        generation=result.generation,
                        foreground_active=self._active_tasks,
                        foreground_pending=len(self._pending_tasks),
                    )
            else:
                emit_perf_event(
                    "thumbnail_result_discarded_before_pixmap",
                    path=result.path,
                    kind=result.kind.value,
                    phase=self._current_phase,
                )
            processed += 1
            if monotonic_ms() - started >= publish_budget_ms:
                break
        emit_perf_event(
            "thumbnail_publish_batch",
            processed=processed,
            converted=converted,
            elapsed_ms=round(max(0.0, monotonic_ms() - started), 3),
            visible_depth=len(self._publish_visible),
            guard_depth=len(self._publish_guard),
            prefetch_depth=len(self._publish_prefetch),
            publish_backlog=(
                len(self._publish_visible)
                + len(self._publish_guard)
                + len(self._publish_prefetch)
            ),
            publish_budget_ms=publish_budget_ms,
            publish_max_items=publish_max_items,
        )
        if self._publish_visible or self._publish_guard or self._publish_prefetch:
            self._ensure_publish_timer()
        self._drain_generation_queue()

    def _discard_stale_staged_results(
        self,
        desired_visible_keys: Set[str],
        desired_prefetch_keys: Set[str],
    ) -> None:
        self._publish_visible = deque(
            result
            for result in self._publish_visible
            if self._cache_key(result.path, result.size) in desired_visible_keys
        )
        self._publish_prefetch = deque(
            result
            for result in self._publish_prefetch
            if self._cache_key(result.path, result.size) in desired_prefetch_keys
            and self._prefetch_results_allowed()
            and not self._low_memory_pressure
        )
        self._publish_guard = deque(
            result
            for result in self._publish_guard
            if self._cache_key(result.path, result.size) in desired_prefetch_keys
            and self._prefetch_results_allowed()
        )
        self._publish_keys = {
            self._cache_key(result.path, result.size)
            for result in (*self._publish_visible, *self._publish_guard, *self._publish_prefetch)
        }
        self._recompute_staging_bytes()

    def _drop_far_staged_results(self, reason: str) -> None:
        if not self._publish_prefetch:
            return
        dropped = len(self._publish_prefetch)
        for result in self._publish_prefetch:
            self._publish_keys.discard(self._cache_key(result.path, result.size))
        self._publish_prefetch.clear()
        self._recompute_staging_bytes()
        emit_perf_event("thumbnail_far_staging_dropped", reason=reason, dropped=dropped)

    def _apply_low_memory_pressure_if_needed(self) -> None:
        low_memory = self._low_memory_pressure_active()
        self._low_memory_pressure = low_memory
        if not low_memory:
            return
        self._drop_far_staged_results("low_memory")
        current_demand = set(self._current_l1_demand_keys or set())
        self._discard_stale_staged_results(
            set(self._pinned_keys),
            current_demand - self._pinned_keys,
        )
        for key, request in list(self._prefetch_queued.items()):
            if request.kind is not ThumbnailRequestKind.PREFETCH:
                continue
            self._prefetch_queued.pop(key, None)
            self._prefetch_pending.discard(key)
            self._prefetch_generations.pop(key, None)
            self._prefetch_kinds.pop(key, None)
        self._prefetch_queue = deque(
            key
            for key in self._prefetch_queue
            if self._prefetch_kinds.get(key) is not ThumbnailRequestKind.PREFETCH
        )
        for key, token in list(self._prefetch_active_tokens.items()):
            if self._prefetch_kinds.get(key) is ThumbnailRequestKind.PREFETCH:
                token.cancel("low_memory")
        live_target = int(
            self._memory_limit_bytes
            * self._runtime_policy.windows_low_memory_target_ratio
        )
        self._schedule_l1_eviction(self._available_l1_bytes(live_target))
        self._drain_l1_evictions()

    def _low_memory_pressure_active(self) -> bool:
        if self._memory_limit_bytes <= 0:
            return False
        low_watermark = int(
            self._memory_limit_bytes
            * self._runtime_policy.windows_low_memory_target_ratio
        )
        if self._runtime_policy.platform.lower().startswith("win"):
            live_bytes = self.memory_snapshot().live_bytes
            if self._low_memory_pressure and live_bytes > low_watermark:
                return True
            now_ms = monotonic_ms()
            if (
                now_ms - self._last_low_memory_probe_ms
                >= self._runtime_policy.windows_low_memory_probe_interval_ms
            ):
                self._last_low_memory_probe_ms = now_ms
                if windows_low_memory_resource_active():
                    return True
        return False

    def _clear_publish_queue(self) -> None:
        self._publish_timer.stop()
        self._publish_visible.clear()
        self._publish_guard.clear()
        self._publish_prefetch.clear()
        self._publish_keys.clear()
        self._staging_used_bytes = 0

    @staticmethod
    def _image_bytes(image: QImage) -> int:
        try:
            return max(0, int(image.sizeInBytes()))
        except (AttributeError, TypeError, ValueError):
            return max(1, int(image.width()) * int(image.height()) * 4)

    def _urgent_staging_bytes(self) -> int:
        return sum(
            self._image_bytes(result.image)
            for result in (*self._publish_visible, *self._publish_guard)
        )

    def _far_staging_bytes(self) -> int:
        return sum(self._image_bytes(result.image) for result in self._publish_prefetch)

    def _active_pipeline_bytes(self, *, urgent: bool) -> int:
        urgent_kinds = {ThumbnailRequestKind.VISIBLE, ThumbnailRequestKind.GUARD}
        return sum(
            reservation
            for key, reservation in self._active_decode_reservations.items()
            if (self._active_decode_kinds.get(key) in urgent_kinds) == urgent
        )

    def _pixmap_pool_limit_bytes(self) -> int:
        ratio = min(0.95, max(0.10, float(self._runtime_policy.pixmap_pool_target_ratio)))
        return max(1, int(self._memory_limit_bytes * ratio))

    def _urgent_pipeline_limit_bytes(self) -> int:
        requested = int(
            self._memory_limit_bytes * self._runtime_policy.urgent_pipeline_budget_ratio
        )
        return max(
            1,
            min(
                requested,
                self._memory_limit_bytes - self._pixmap_pool_limit_bytes(),
            ),
        )

    def _far_pipeline_limit_bytes(self) -> int:
        requested = int(
            self._memory_limit_bytes * self._runtime_policy.far_pipeline_budget_ratio
        )
        remaining = (
            self._memory_limit_bytes
            - self._pixmap_pool_limit_bytes()
            - self._urgent_pipeline_limit_bytes()
        )
        return max(0, min(requested, remaining))

    def _pipeline_credit_available(
        self,
        kind: ThumbnailRequestKind,
        reservation: int,
        *,
        key: str,
    ) -> bool:
        current = self._active_decode_reservations.get(key, 0)
        if kind in {ThumbnailRequestKind.VISIBLE, ThumbnailRequestKind.GUARD}:
            used = self._urgent_staging_bytes() + self._active_pipeline_bytes(urgent=True)
            limit = self._urgent_pipeline_limit_bytes()
        else:
            used = self._far_staging_bytes() + self._active_pipeline_bytes(urgent=False)
            limit = self._far_pipeline_limit_bytes()
        allowed = used - current + reservation <= limit
        if not allowed:
            emit_perf_event(
                (
                    "thumbnail_urgent_credit_wait"
                    if kind in {ThumbnailRequestKind.VISIBLE, ThumbnailRequestKind.GUARD}
                    else "thumbnail_far_credit_wait"
                ),
                key=key,
                kind=kind.value,
                used_bytes=used,
                reservation_bytes=reservation,
                limit_bytes=limit,
            )
        return allowed

    def _recompute_staging_bytes(self) -> None:
        self._staging_used_bytes = sum(
            self._image_bytes(result.image)
            for result in (
                *self._publish_visible,
                *self._publish_guard,
                *self._publish_prefetch,
            )
        )

    def _effective_guard_staging_limit(self) -> int:
        return max(1, int(self._runtime_policy.guard_staging_limit))

    def _effective_far_staging_limit(self) -> int:
        if self._low_memory_pressure:
            return 0
        return max(0, int(self._runtime_policy.far_staging_limit))

    def _effective_publish_max_items(self) -> int:
        if self._current_phase in ("settled", "slow") and self._current_intent != "continuous_burst":
            return max(1, self._runtime_policy.publish_max_items)
        return max(1, min(2, self._runtime_policy.publish_max_items))

    def _effective_publish_budget_ms(self) -> float:
        if self._current_phase in ("settled", "slow") and self._current_intent != "continuous_burst":
            return max(0.0, float(self._runtime_policy.publish_budget_ms))
        return max(0.0, min(3.0, float(self._runtime_policy.publish_budget_ms)))

    def _prefetch_results_allowed(self) -> bool:
        return not self._motion_blocks_prefetch(
            self._current_phase,
            self._current_intent,
        )

    def _start_generation(
        self,
        key: str,
        path: Path,
        size: QSize,
        generation: int,
        *,
        kind: ThumbnailRequestKind,
        cancellation: _CancellationToken | None = None,
        l2_cache_key: str | None = None,
    ) -> bool:
        reservation = max(
            1,
            int(size.width()) * int(size.height()) * 4,
        )
        pool_limit = self._pixmap_pool_limit_bytes()
        if self._memory_used_bytes > pool_limit:
            self._evict_l1_until(
                pool_limit,
                protected_keys={key},
                reason_prefix="decode_admission",
                max_items=8,
                budget_ms=2.0,
            )
        if self._memory_used_bytes > pool_limit:
            self._schedule_l1_eviction(pool_limit)
            emit_perf_event(
                "thumbnail_decode_deferred",
                key=key,
                kind=kind.value,
                live_bytes=self.memory_snapshot().live_bytes,
                reservation_bytes=reservation,
                memory_limit_bytes=self._memory_limit_bytes,
            )
            return False
        if not self._pipeline_credit_available(kind, reservation, key=key):
            return False
        self._active_decode_reservations[key] = reservation
        self._active_decode_kinds[key] = kind
        worker_signals = ThumbnailWorkerSignals()
        worker_signals.result.connect(self._handle_generation_result)
        worker_signals.failed.connect(self._handle_generation_failure)

        emit_perf_event(
            "thumbnail_generate_started",
            path=path,
            width=size.width(),
            height=size.height(),
            pending=len(self._pending_tasks),
        )
        worker = ThumbnailGenerationTask(
            (
                self._load_cached_thumbnail_only
                if kind is not ThumbnailRequestKind.VISIBLE
                else self._load_or_render_thumbnail
            ),
            path,
            size,
            worker_signals,
            generation,
            kind,
            self._runtime_policy.platform,
            cancellation,
            l2_cache_key,
        )
        if kind is ThumbnailRequestKind.GUARD:
            self._guard_thread_pool.start(worker)
        elif kind is ThumbnailRequestKind.PREFETCH:
            self._prefetch_thread_pool.start(worker)
        else:
            self._thread_pool.start(worker)
        return True

    def _handle_generation_result(
        self,
        path: Path,
        size: QSize,
        image: QImage,
        generation: int = 0,
        kind: ThumbnailRequestKind = ThumbnailRequestKind.VISIBLE,
    ):
        # Back on main thread
        if kind is not ThumbnailRequestKind.VISIBLE:
            self._handle_prefetch_result(path, size, image, generation, kind)
            return
        if not image.isNull():
            key = self._cache_key(path, size)
            self._pending_tasks.discard(key)
            desired_generation = self._pending_generations.pop(key, generation)
            self._failure_until.pop(key, None)
            self._prefetch_l2_miss_until.pop(key, None)
            self._active_tasks = max(0, self._active_tasks - 1)

            if self._is_shutting_down or desired_generation < self._current_generation:
                self._active_decode_reservations.pop(key, None)
                self._active_decode_kinds.pop(key, None)
                emit_perf_event(
                    "thumbnail_result_discarded",
                    path=path,
                    generation=desired_generation,
                    current_generation=self._current_generation,
                )
                self._drain_generation_queue()
                return

            self._stage_result(
                ThumbnailLoadResult(
                    path=path,
                    size=size,
                    image=image,
                    generation=desired_generation,
                    kind=ThumbnailRequestKind.VISIBLE,
                ),
                reservation_key=key,
            )
            self._drain_generation_queue()

    def _handle_generation_failure(
        self,
        path: Path,
        size: QSize,
        reason: str,
        generation: int = 0,
        kind: ThumbnailRequestKind = ThumbnailRequestKind.VISIBLE,
    ) -> None:
        if kind is not ThumbnailRequestKind.VISIBLE:
            self._handle_prefetch_failure(path, size, reason, generation, kind)
            return
        key = self._cache_key(path, size)
        self._active_decode_reservations.pop(key, None)
        self._active_decode_kinds.pop(key, None)
        self._pending_tasks.discard(key)
        desired_generation = self._pending_generations.pop(key, generation)
        self._queued_tasks.pop(key, None)
        self._active_tasks = max(0, self._active_tasks - 1)
        if (
            not self._is_shutting_down
            and desired_generation > generation
            and desired_generation >= self._current_generation
        ):
            self._queue_visible(
                ThumbnailRequest(
                    path,
                    size,
                    ThumbnailRequestKind.VISIBLE,
                    desired_generation,
                )
            )
            return
        self._failure_until[key] = time.monotonic() + self._failure_cooldown_seconds
        emit_perf_event(
            "thumbnail_generate_failed",
            path=path,
            width=size.width(),
            height=size.height(),
            reason=reason,
            pending=len(self._pending_tasks),
        )
        self._drain_generation_queue()

    def _handle_prefetch_result(
        self,
        path: Path,
        size: QSize,
        image: QImage,
        generation: int,
        kind: ThumbnailRequestKind = ThumbnailRequestKind.PREFETCH,
    ) -> None:
        key = self._cache_key(path, size)
        token = self._prefetch_active_tokens.pop(key, None)
        promoted = key in self._prefetch_promoted_visible
        self._prefetch_promoted_visible.discard(key)
        self._prefetch_pending.discard(key)
        self._prefetch_kinds.pop(key, None)
        desired_generation = self._prefetch_generations.pop(key, generation)
        self._prefetch_active_tasks = max(0, self._prefetch_active_tasks - 1)
        if kind is ThumbnailRequestKind.GUARD:
            self._guard_active_tasks = max(0, self._guard_active_tasks - 1)
        else:
            self._far_active_tasks = max(0, self._far_active_tasks - 1)
        if promoted:
            desired_generation = max(
                desired_generation,
                self._pending_generations.pop(key, generation),
            )
            self._pending_tasks.discard(key)
        is_visible = promoted or key in self._pinned_keys
        is_prefetch = key in self._prefetch_key_order
        stale = (
            self._is_shutting_down
            or (is_visible and desired_generation < self._current_generation)
            or token is None
            or token.cancelled()
            or not (is_visible or is_prefetch)
        )
        if stale:
            self._active_decode_reservations.pop(key, None)
            self._active_decode_kinds.pop(key, None)
            emit_perf_event(
                "thumbnail_prefetch_result_discarded",
                path=path,
                generation=generation,
                current_generation=self._current_generation,
            )
        elif not image.isNull():
            self._stage_result(
                ThumbnailLoadResult(
                    path=path,
                    size=size,
                    image=image,
                    generation=desired_generation,
                    kind=(
                        ThumbnailRequestKind.VISIBLE
                        if is_visible
                        else kind
                    ),
                    promoted=promoted,
                ),
                reservation_key=key,
            )
        self._drain_generation_queue()

    def _handle_prefetch_failure(
        self,
        path: Path,
        size: QSize,
        reason: str,
        generation: int,
        kind: ThumbnailRequestKind = ThumbnailRequestKind.PREFETCH,
    ) -> None:
        key = self._cache_key(path, size)
        self._active_decode_reservations.pop(key, None)
        self._active_decode_kinds.pop(key, None)
        token = self._prefetch_active_tokens.pop(key, None)
        promoted = key in self._prefetch_promoted_visible
        self._prefetch_promoted_visible.discard(key)
        self._prefetch_pending.discard(key)
        self._prefetch_kinds.pop(key, None)
        desired_generation = self._prefetch_generations.pop(key, generation)
        self._prefetch_active_tasks = max(0, self._prefetch_active_tasks - 1)
        if kind is ThumbnailRequestKind.GUARD:
            self._guard_active_tasks = max(0, self._guard_active_tasks - 1)
        else:
            self._far_active_tasks = max(0, self._far_active_tasks - 1)
        actual_reason = (
            token.l2_outcome
            if token is not None and token.l2_outcome
            else "miss"
            if reason == "empty_render"
            else reason
        )
        if promoted:
            desired_generation = max(
                desired_generation,
                self._pending_generations.pop(key, generation),
            )
            self._pending_tasks.discard(key)
            if not self._is_shutting_down:
                emit_perf_event(
                    "thumbnail_prefetch_promoted_fallback",
                    path=path,
                    reason=actual_reason,
                    generation=desired_generation,
                )
                self._queue_visible(
                    ThumbnailRequest(
                        path,
                        size,
                        ThumbnailRequestKind.VISIBLE,
                        desired_generation,
                    )
                )
                return
        if actual_reason == "miss":
            self._prefetch_l2_miss_until[key] = (
                time.monotonic()
                + (
                    self._runtime_policy.guard_miss_ttl_seconds
                    if kind is ThumbnailRequestKind.GUARD
                    else self._runtime_policy.prefetch_miss_ttl_seconds
                )
            )
        emit_perf_event(
            "thumbnail_prefetch_skipped",
            path=path,
            reason=actual_reason,
            generation=generation,
        )
        if (
            actual_reason == "cancelled"
            and (token is None or token.cancel_reason != "demand_replaced")
            and key in self._prefetch_key_order
            and key not in self._pending_tasks
        ):
            self._queue_prefetch(
                ThumbnailRequest(
                    path,
                    size,
                    kind,
                    self._current_generation,
                )
            )
        self._drain_generation_queue()

    def invalidate(self, path: Path, *, size: QSize | None = None):
        """Removes the thumbnail from cache to force regeneration."""
        if size is None:
            size = QSize(512, 512)
        key = self._cache_key(path, size)
        disk_key = self._disk_cache_key(path)
        memory_keys = [
            candidate for candidate in self._memory_cache if candidate.startswith(f"{disk_key}:")
        ]

        for memory_key in memory_keys:
            del self._memory_cache[memory_key]
            self._memory_used_bytes = max(
                0,
                self._memory_used_bytes - self._memory_bytes.pop(memory_key, 0),
            )
        self._failure_until.pop(key, None)
        self._prefetch_l2_miss_until.pop(key, None)
        self._pending_tasks.discard(key)
        self._pending_generations.pop(key, None)
        self._queued_tasks.pop(key, None)
        self._visible_queued_at.pop(key, None)
        self._pinned_keys.discard(key)
        self._cancel_prefetch_key(key)

        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, disk_key)
        if disk_file.exists():
            try:
                disk_file.unlink()
            except OSError:
                pass

    def remap_album_paths(
        self,
        old_root: Path,
        new_root: Path,
        *,
        size: QSize | None = None,
    ) -> None:
        """Copy cached thumbnails from an album's old path to its renamed path."""

        if size is None:
            size = QSize(512, 512)
        if not new_root.exists():
            return
        try:
            paths = [path for path in new_root.rglob("*") if path.is_file()]
        except OSError:
            return

        for new_path in paths:
            try:
                rel = new_path.relative_to(new_root)
            except ValueError:
                continue
            old_path = old_root / rel
            old_key = self._cache_key(old_path, size)
            new_key = self._cache_key(new_path, size)
            if old_key in self._memory_cache and new_key not in self._memory_cache:
                self._add_to_memory(new_key, self._memory_cache[old_key])

            old_disk_file = thumbnail_cache_file_for_key(
                self._disk_cache_path,
                self._disk_cache_key(old_path),
            )
            new_disk_file = thumbnail_cache_file_for_key(
                self._disk_cache_path,
                self._disk_cache_key(new_path),
            )
            if old_disk_file.exists() and not new_disk_file.exists():
                try:
                    shutil.copy2(old_disk_file, new_disk_file)
                except OSError:
                    pass

    def _cache_key(self, path: Path, size: QSize) -> str:
        return f"{self._disk_cache_key(path)}:{size.width()}x{size.height()}"

    @staticmethod
    def _disk_cache_key(path: Path, known_key: str | None = None) -> str:
        return known_key or thumbnail_cache_key(path, (512, 512))

    def _available_l1_bytes(self, total_budget: int | None = None) -> int:
        budget = self._memory_limit_bytes if total_budget is None else max(0, total_budget)
        return max(
            0,
            budget
            - self._staging_used_bytes
            - sum(self._active_decode_reservations.values()),
        )

    def _store_image_in_l1(self, key: str, image: QImage) -> bool:
        """Publish an image into a stable GUI-thread pixmap slot."""

        if image.isNull():
            return False
        if self._current_l1_demand_keys is not None and key not in self._l1_write_allowed_keys():
            emit_perf_event(
                "thumbnail_l1_write_discarded",
                key=key,
                reason="outside_current_demand",
            )
            return False

        existing = self._memory_cache.get(key)
        if isinstance(existing, QPixmap) and not existing.isNull():
            if self._overwrite_pixmap_slot(existing, image):
                self._memory_cache.move_to_end(key)
                return True

        image_bytes = self._image_bytes(image)
        pool_limit = self._pixmap_pool_limit_bytes()
        if self._memory_used_bytes + image_bytes <= pool_limit:
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                return False
            self._memory_cache[key] = pixmap
            self._memory_cache.move_to_end(key)
            self._memory_bytes[key] = image_bytes
            self._memory_used_bytes += image_bytes
            self._slot_allocations += 1
            emit_perf_event(
                "thumbnail_pixmap_slot_allocated",
                key=key,
                slot_count=len(self._memory_cache),
                pool_bytes=self._memory_used_bytes,
                pool_limit_bytes=pool_limit,
            )
            if not self._pool_warm and self._memory_used_bytes + image_bytes > pool_limit:
                self._pool_warm = True
                emit_perf_event(
                    "thumbnail_pixmap_pool_warm",
                    slot_count=len(self._memory_cache),
                    pool_bytes=self._memory_used_bytes,
                    pool_limit_bytes=pool_limit,
                )
            return True

        protected = {key}
        victim_key: str | None = None
        victim_reason = "protected"
        while len(protected) <= len(self._memory_cache):
            candidate, reason = self._select_l1_eviction_candidate(protected)
            if candidate is None:
                break
            candidate_pixmap = self._memory_cache.get(candidate)
            if (
                isinstance(candidate_pixmap, QPixmap)
                and not candidate_pixmap.isNull()
                and candidate_pixmap.size() == image.size()
            ):
                victim_key = candidate
                victim_reason = reason
                break
            protected.add(candidate)
        if victim_key is None:
            emit_perf_event(
                "thumbnail_pixmap_slot_unavailable",
                key=key,
                image_width=image.width(),
                image_height=image.height(),
            )
            return False

        pixmap = self._memory_cache.pop(victim_key)
        old_bytes = self._memory_bytes.pop(victim_key, image_bytes)
        if not self._overwrite_pixmap_slot(pixmap, image):
            self._memory_used_bytes = max(0, self._memory_used_bytes - old_bytes)
            self._slot_releases += 1
            return False
        self._memory_cache[key] = pixmap
        self._memory_cache.move_to_end(key)
        self._memory_bytes[key] = old_bytes
        self._slot_reuses += 1
        if not self._pool_saturated:
            self._pool_saturated = True
            emit_perf_event(
                "thumbnail_pixmap_pool_saturated",
                slot_count=len(self._memory_cache),
                pool_bytes=self._memory_used_bytes,
                pool_limit_bytes=pool_limit,
            )
        emit_perf_event(
            "thumbnail_pixmap_slot_rebound",
            old_key=victim_key,
            key=key,
            reason=victim_reason,
            slot_count=len(self._memory_cache),
            pool_bytes=self._memory_used_bytes,
        )
        return True

    @staticmethod
    def _overwrite_pixmap_slot(pixmap: QPixmap, image: QImage) -> bool:
        if pixmap.isNull() or pixmap.size() != image.size():
            return False
        painter = QPainter(pixmap)
        if not painter.isActive():
            return False
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(pixmap.rect(), image)
        finally:
            painter.end()
        return True

    def _release_all_l1_slots(self, reason: str) -> None:
        released = len(self._memory_cache)
        if not released:
            self._pool_warm = False
            self._pool_saturated = False
            return
        self._memory_cache.clear()
        self._memory_bytes.clear()
        self._memory_used_bytes = 0
        self._slot_releases += released
        self._pool_warm = False
        self._pool_saturated = False
        emit_perf_event(
            "thumbnail_pixmap_slots_released",
            reason=reason,
            released=released,
        )

    def _add_to_memory(self, key: str, pixmap: QPixmap):
        if self._current_l1_demand_keys is not None and key not in self._l1_write_allowed_keys():
            had_slot = key in self._memory_cache
            self._memory_used_bytes = max(
                0,
                self._memory_used_bytes - self._memory_bytes.pop(key, 0),
            )
            self._memory_cache.pop(key, None)
            self._slot_releases += int(had_slot)
            emit_perf_event(
                "thumbnail_l1_write_discarded",
                key=key,
                reason="outside_current_demand",
                memory_used_bytes=self._memory_used_bytes,
                memory_limit_bytes=self._memory_limit_bytes,
            )
            return

        bytes_per_pixel = max(1, (int(pixmap.depth()) + 7) // 8)
        estimated_bytes = max(
            1,
            int(pixmap.width()) * int(pixmap.height()) * bytes_per_pixel,
        )
        old_bytes = self._memory_bytes.get(key, 0)
        target_before_insert = max(
            0,
            self._available_l1_bytes() - estimated_bytes + old_bytes,
        )
        self._evict_l1_until(
            target_before_insert,
            protected_keys={key},
            reason_prefix="write_admission",
            max_items=8,
            budget_ms=2.0,
        )
        if self._memory_used_bytes - old_bytes + estimated_bytes > self._available_l1_bytes():
            emit_perf_event(
                "thumbnail_l1_write_discarded",
                key=key,
                reason="hard_budget",
                memory_used_bytes=self._memory_used_bytes,
                staging_bytes=self._staging_used_bytes,
                active_reservation_bytes=sum(self._active_decode_reservations.values()),
                memory_limit_bytes=self._memory_limit_bytes,
            )
            return
        if key in self._memory_cache:
            self._memory_cache.pop(key, None)
            self._memory_used_bytes = max(0, self._memory_used_bytes - old_bytes)
            self._memory_bytes.pop(key, None)
            self._slot_releases += 1
        # This compatibility path is used by exceptional cache remaps and tests.
        # Own a distinct native surface so one key can never mutate another key's
        # slot when the steady-state publisher later rebinds it in place.
        owned_pixmap = pixmap.copy()
        if owned_pixmap.isNull():
            return
        self._memory_cache[key] = owned_pixmap
        self._memory_cache.move_to_end(key)
        self._memory_bytes[key] = estimated_bytes
        self._memory_used_bytes += estimated_bytes
        self._slot_allocations += 1
        emit_perf_event(
            "thumbnail_l1_capacity",
            key=key,
            estimated_pixmap_bytes=estimated_bytes,
            memory_used_bytes=self._memory_used_bytes,
            memory_limit_bytes=self._memory_limit_bytes,
            capacity_tiles=max(1, self._memory_limit_bytes // max(1, estimated_bytes)),
        )
        self._schedule_l1_eviction(self._available_l1_bytes())

    def _refresh_l1_for_demand(
        self,
        desired_visible_keys: Set[str],
        desired_prefetch_keys: Set[str],
    ) -> None:
        desired_keys = desired_visible_keys | desired_prefetch_keys
        self._current_l1_demand_keys = set(desired_keys)
        if self._memory_limit_bytes <= 0:
            return
        for key in (*desired_visible_keys, *self._prefetch_key_order):
            if key in self._memory_cache:
                self._memory_cache.move_to_end(key)
        # A stable slot pool is deliberately retained across viewport changes.
        # Once warm, new thumbnails rebind cold slots instead of destroying and
        # reallocating native QPixmap storage on every wheel notch.
        target = min(self._available_l1_bytes(), self._pixmap_pool_limit_bytes())
        self._schedule_l1_eviction(target)
        if self._memory_used_bytes > target:
            self._drain_l1_evictions()

    def _schedule_l1_eviction(self, target_bytes: int) -> None:
        target = max(0, int(target_bytes))
        if self._pending_eviction_target_bytes is None:
            self._pending_eviction_target_bytes = target
        else:
            self._pending_eviction_target_bytes = min(
                self._pending_eviction_target_bytes,
                target,
            )
        needs_eviction = self._memory_used_bytes > target
        if not needs_eviction and not self._eviction_timer.isActive():
            self._pending_eviction_target_bytes = None
            return
        if needs_eviction and not self._eviction_timer.isActive():
            self._eviction_timer.start(0)

    def _drain_l1_evictions(self) -> None:
        target = self._pending_eviction_target_bytes
        if target is None:
            return
        started = monotonic_ms()
        stale_evicted = 0
        max_items = (
            self._runtime_policy.low_memory_release_max_items
            if self._low_memory_pressure
            else 8
        )
        budget_ms = (
            self._runtime_policy.low_memory_release_budget_ms
            if self._low_memory_pressure
            else 2.0
        )
        remaining_items = max(1, int(max_items))
        remaining_ms = max(0.0, float(budget_ms) - (monotonic_ms() - started))
        pressure_evicted = self._evict_l1_until(
            target,
            reason_prefix="demand_refresh",
            max_items=remaining_items,
            budget_ms=remaining_ms,
        )
        if self._memory_used_bytes > target:
            if stale_evicted + pressure_evicted == 0:
                self._pending_eviction_target_bytes = None
                self._pending_stale_eviction = False
                emit_perf_event(
                    "thumbnail_l1_eviction_blocked",
                    target_bytes=target,
                    memory_used_bytes=self._memory_used_bytes,
                    pinned=len(self._pinned_keys),
                )
                return
            if not self._eviction_timer.isActive():
                self._eviction_timer.start(0)
            return
        self._pending_eviction_target_bytes = None
        if self._queued_tasks or self._prefetch_queued:
            QTimer.singleShot(0, self._drain_generation_queue)

    def _has_stale_l1_entries(self) -> bool:
        if not self._pending_stale_eviction or self._current_l1_demand_keys is None:
            return False
        protected = self._current_l1_demand_keys | self._pinned_keys
        return any(key not in protected for key in self._memory_cache)

    def _evict_stale_l1(self, *, max_items: int, budget_ms: float) -> int:
        if not self._has_stale_l1_entries():
            return 0
        protected = (self._current_l1_demand_keys or set()) | self._pinned_keys
        started = monotonic_ms()
        evicted = 0
        for key in tuple(self._memory_cache):
            if key in protected:
                continue
            self._memory_cache.pop(key, None)
            self._slot_releases += 1
            self._memory_used_bytes = max(
                0,
                self._memory_used_bytes - self._memory_bytes.pop(key, 0),
            )
            emit_perf_event(
                "thumbnail_l1_evicted",
                key=key,
                reason="demand_refresh_old_demand",
                memory_used_bytes=self._memory_used_bytes,
                memory_limit_bytes=self._memory_limit_bytes,
            )
            evicted += 1
            if evicted >= max_items or monotonic_ms() - started >= budget_ms:
                break
        return evicted

    def _evict_l1_until(
        self,
        target_bytes: int,
        *,
        protected_keys: Set[str] | None = None,
        reason_prefix: str = "memory_pressure",
        max_items: int | None = None,
        budget_ms: float | None = None,
    ) -> int:
        protected = set(protected_keys or set()) | self._pinned_keys
        target = max(0, int(target_bytes))
        started = monotonic_ms()
        evicted = 0
        while self._memory_used_bytes > target:
            if max_items is not None and evicted >= max_items:
                break
            if budget_ms is not None and monotonic_ms() - started >= budget_ms:
                break
            evicted_key, eviction_reason = self._select_l1_eviction_candidate(protected)
            if evicted_key is None:
                break
            self._memory_cache.pop(evicted_key, None)
            self._slot_releases += 1
            self._memory_used_bytes -= self._memory_bytes.pop(evicted_key, 0)
            emit_perf_event(
                "thumbnail_l1_evicted",
                key=evicted_key,
                reason=(
                    eviction_reason
                    if reason_prefix == "memory_pressure"
                    else f"{reason_prefix}_{eviction_reason}"
                ),
                memory_used_bytes=self._memory_used_bytes,
                memory_limit_bytes=self._memory_limit_bytes,
            )
            evicted += 1
        return evicted

    def _l1_write_allowed_keys(self) -> Set[str]:
        allowed = set(self._pinned_keys)
        if self._current_l1_demand_keys is not None:
            allowed |= self._current_l1_demand_keys
        return allowed

    def _select_l1_eviction_candidate(
        self,
        protected_keys: Set[str],
    ) -> tuple[str | None, str]:
        old_demand = next(
            (
                candidate
                for candidate in self._memory_cache
                if candidate not in protected_keys
                and candidate not in self._prefetch_key_order
                and (
                    self._current_l1_demand_keys is None
                    or candidate not in self._current_l1_demand_keys
                )
            ),
            None,
        )
        if old_demand is not None:
            return old_demand, "old_demand"
        far_keys = set(self._prefetch_key_order) - self._current_guard_keys
        far_prefetch = next(
            (
                candidate
                for candidate in reversed(self._prefetch_key_order)
                if candidate in self._memory_cache
                and candidate not in protected_keys
                and candidate in far_keys
            ),
            None,
        )
        if far_prefetch is not None:
            return far_prefetch, "far_prefetch"
        guard = next(
            (
                candidate
                for candidate in reversed(self._prefetch_key_order)
                if candidate in self._memory_cache
                and candidate not in protected_keys
                and candidate in self._current_guard_keys
            ),
            None,
        )
        if guard is not None:
            return guard, "guard_distance"
        lru = next(
            (
                candidate
                for candidate in self._memory_cache
                if candidate not in protected_keys
            ),
            None,
        )
        if lru is not None:
            return lru, "lru"
        return None, "protected"

    def _load_cached_thumbnail_only(
        self,
        path: Path,
        size: QSize,
        cancellation: _CancellationToken | None = None,
        l2_cache_key: str | None = None,
    ) -> Optional[QImage]:
        """Read and decode an existing L2 thumbnail without rendering source media."""

        disk_file = thumbnail_cache_file_for_key(
            self._disk_cache_path,
            self._disk_cache_key(path, l2_cache_key),
        )
        image, outcome, _elapsed_ms = self._read_cached_thumbnail(
            disk_file,
            path=path,
            cancellation=cancellation,
            tier="L2_prefetch",
            target_size=size,
        )
        if cancellation is not None:
            cancellation.l2_outcome = outcome
        return image

    def _read_cached_thumbnail(
        self,
        disk_file: Path,
        *,
        path: Path,
        cancellation: _CancellationToken | None,
        tier: str,
        target_size: QSize | None = None,
    ) -> tuple[Optional[QImage], str, float]:
        started = monotonic_ms()
        open_finished = started
        decode_finished = started
        outcome = "miss"
        image: Optional[QImage] = None
        if cancellation is not None and cancellation.cancelled():
            outcome = "cancelled"
        else:
            handle = QFile(str(disk_file))
            if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
                open_finished = decode_finished = monotonic_ms()
                error_text = handle.errorString().lower()
                outcome = (
                    "miss"
                    if any(
                        marker in error_text
                        for marker in ("no such", "not found", "cannot find")
                    )
                    else "read_error"
                )
            else:
                open_finished = monotonic_ms()
                try:
                    if cancellation is not None and cancellation.cancelled():
                        outcome = "cancelled"
                    else:
                        reader = QImageReader(handle)
                        reader.setAutoTransform(True)
                        if target_size is not None and target_size.isValid() and not target_size.isEmpty():
                            reader.setScaledSize(target_size)
                        image = reader.read()
                        decode_finished = monotonic_ms()
                        outcome = (
                            "hit"
                            if image is not None and not image.isNull()
                            else "decode_error"
                        )
                finally:
                    handle.close()
        if cancellation is not None and cancellation.cancelled():
            outcome = "cancelled"
            image = None
        elapsed_ms = max(0.0, monotonic_ms() - started)
        if outcome == "hit":
            emit_perf_event("thumbnail_cache_hit", tier=tier, key=disk_file.stem)
        emit_perf_event(
            "thumbnail_prefetch_l2_finished" if tier == "L2_prefetch" else "thumbnail_l2_finished",
            path=path,
            outcome=outcome,
            tier=tier,
            open_ms=round(max(0.0, open_finished - started), 3),
            decode_ms=round(max(0.0, decode_finished - open_finished), 3),
            elapsed_ms=round(elapsed_ms, 3),
        )
        return image, outcome, elapsed_ms

    def _load_or_render_thumbnail(
        self,
        path: Path,
        size: QSize,
        cancellation: _CancellationToken | None = None,
        l2_cache_key: str | None = None,
    ) -> Optional[QImage]:
        """Load L2 or render/write a replacement entirely on a worker thread."""

        del cancellation
        disk_file = thumbnail_cache_file_for_key(
            self._disk_cache_path,
            self._disk_cache_key(path, l2_cache_key),
        )
        image, outcome, _elapsed_ms = self._read_cached_thumbnail(
            disk_file,
            path=path,
            cancellation=None,
            tier="L2",
            target_size=size,
        )
        if outcome == "hit":
            return image

        storage_size = QSize(512, 512)
        storage_image = self._render_thumbnail(path, storage_size)
        if storage_image is None or storage_image.isNull():
            return None
        try:
            disk_file.parent.mkdir(parents=True, exist_ok=True)
            storage_image.save(str(disk_file), "JPEG")
        except OSError:
            pass
        if size == storage_size:
            return storage_image
        return storage_image.scaled(
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _resolve_memory_limit(memory_limit_mb: int | None) -> int:
        return ThumbnailRuntimePolicy.detect(memory_limit_mb=memory_limit_mb).memory_limit_bytes

    def _render_thumbnail(self, path: Path, size: QSize) -> Optional[QImage]:
        started = monotonic_ms()
        if size.isEmpty() or not size.isValid():
            return None

        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
        is_video = path.suffix.lower() in video_exts
        qimage: Optional[QImage] = None
        if not is_video:
            qimage = image_loader.load_qimage(path, size)

        if qimage is None or qimage.isNull():
            pil_image = self._generator.generate(path, (size.width(), size.height()))
            if pil_image is None:
                return None
            qimage = image_loader.qimage_from_pil(pil_image)

        if qimage is None or qimage.isNull():
            emit_perf_event(
                "thumbnail_generate_failed",
                path=path,
                elapsed_ms=round(monotonic_ms() - started, 3),
                reason="empty_render",
            )
            return None

        if self._edit_service is not None and self._edit_service.sidecar_exists(path):
            stats = compute_color_statistics(qimage)
            state = self._edit_service.describe_adjustments(
                path,
                color_stats=stats,
            )
            adjustments = state.resolved_adjustments
        else:
            raw_adjustments = sidecar.load_adjustments(path)
            stats = compute_color_statistics(qimage) if raw_adjustments else None
            adjustments = sidecar.resolve_render_adjustments(
                raw_adjustments,
                color_stats=stats,
            )

        if adjustments:
            qimage = self._apply_geometry_and_crop(qimage, adjustments) or qimage
            qimage = apply_adjustments(qimage, adjustments, color_stats=stats)

        result = self._composite_canvas(qimage, size)
        if result is None or result.isNull():
            emit_perf_event(
                "thumbnail_generate_failed",
                path=path,
                elapsed_ms=round(monotonic_ms() - started, 3),
                reason="empty_composite",
            )
        return result

    def _apply_geometry_and_crop(
        self,
        image: QImage,
        adjustments: Dict[str, float],
    ) -> Optional[QImage]:
        rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
        flip_h = bool(adjustments.get("Crop_FlipH", False))
        straighten = float(adjustments.get("Crop_Straighten", 0.0))
        p_vert = float(adjustments.get("Perspective_Vertical", 0.0))
        p_horz = float(adjustments.get("Perspective_Horizontal", 0.0))

        tex_crop = (
            float(adjustments.get("Crop_CX", 0.5)),
            float(adjustments.get("Crop_CY", 0.5)),
            float(adjustments.get("Crop_W", 1.0)),
            float(adjustments.get("Crop_H", 1.0)),
        )

        log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(
            tex_crop,
            rotate_steps,
        )

        w, h = image.width(), image.height()

        if (
            rotate_steps == 0
            and not flip_h
            and abs(straighten) < 1e-5
            and abs(p_vert) < 1e-5
            and abs(p_horz) < 1e-5
            and log_w >= 0.999
            and log_h >= 0.999
        ):
            return image

        if rotate_steps % 2 == 1:
            logical_aspect = float(h) / float(w) if w > 0 else 1.0
        else:
            logical_aspect = float(w) / float(h) if h > 0 else 1.0

        matrix_inv = geo_utils.build_perspective_matrix(
            vertical=p_vert,
            horizontal=p_horz,
            image_aspect_ratio=logical_aspect,
            straighten_degrees=straighten,
            rotate_steps=0,
            flip_horizontal=flip_h,
        )

        try:
            matrix = np.linalg.inv(matrix_inv)
        except np.linalg.LinAlgError:
            matrix = np.identity(3)

        qt_perspective = QTransform(
            matrix[0, 0],
            matrix[1, 0],
            matrix[2, 0],
            matrix[0, 1],
            matrix[1, 1],
            matrix[2, 1],
            matrix[0, 2],
            matrix[1, 2],
            matrix[2, 2],
        )

        t_to_norm = QTransform().scale(1.0 / w, 1.0 / h)

        t_rot = QTransform()
        t_rot.translate(0.5, 0.5)
        t_rot.rotate(rotate_steps * 90)
        t_rot.translate(-0.5, -0.5)

        t_to_ndc = QTransform().translate(-1.0, -1.0).scale(2.0, 2.0)
        t_from_ndc = QTransform().translate(0.5, 0.5).scale(0.5, 0.5)

        log_w_px = h if rotate_steps % 2 else w
        log_h_px = w if rotate_steps % 2 else h
        t_to_pixels = QTransform().scale(log_w_px, log_h_px)

        transform = t_to_norm * t_rot * t_to_ndc * qt_perspective * t_from_ndc * t_to_pixels

        crop_x_px = log_cx * log_w_px - (log_w * log_w_px * 0.5)
        crop_y_px = log_cy * log_h_px - (log_h * log_h_px * 0.5)
        crop_w_px = log_w * log_w_px
        crop_h_px = log_h * log_h_px

        t_final = transform * QTransform().translate(-crop_x_px, -crop_y_px)

        out_w = max(1, int(round(crop_w_px)))
        out_h = max(1, int(round(crop_h_px)))

        result_img = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
        result_img.fill(Qt.transparent)

        painter = QPainter(result_img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.setTransform(t_final)
        painter.drawImage(0, 0, image)
        painter.end()

        return result_img

    def _composite_canvas(self, image: QImage, size: QSize) -> QImage:
        canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        scaled = image.scaled(
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        target_rect = canvas.rect()
        source_rect = scaled.rect()
        if source_rect.width() > target_rect.width():
            diff = source_rect.width() - target_rect.width()
            left = diff // 2
            right = diff - left
            source_rect.adjust(left, 0, -right, 0)
        if source_rect.height() > target_rect.height():
            diff = source_rect.height() - target_rect.height()
            top = diff // 2
            bottom = diff - top
            source_rect.adjust(0, top, 0, -bottom)
        painter.drawImage(target_rect, scaled, source_rect)
        painter.end()
        return canvas

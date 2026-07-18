"""Coordinator for the stacked player widgets used on the detail page."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

from ....application.ports import EditServicePort
from ....core.adjustment_mapping import resolve_adjustment_mapping
from ....core.color_resolver import compute_color_statistics
from ....gui.detail_decode_backend import (
    DecodeCancelledError,
    DecodedSurface,
    DefaultStillDecodeBackend,
    StillDecodeBackend,
)
from ....gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailGeometryState,
    DetailPrefetchDescriptor,
    DetailRenderRequest,
    detail_pipeline_v2_enabled,
    detail_scheduler_v3_enabled,
)
from ....gui.detail_profile import (
    emit_detail_event,
    log_detail_profile,
    shutdown_detail_profile,
)
from ....gui.detail_request_scheduler import DetailStillRequestScheduler
from ....gui.i18n import tr
from ..widgets.gl_image_viewer import GLImageViewer
from ..widgets.live_badge import LiveBadge
from ..widgets.video_area import VideoArea


class _AdjustedImageSignals(QObject):
    """Relay neutral-surface completion events back to the GUI thread."""

    started = Signal(object)
    """Emitted immediately before the runnable enters its decode path."""

    completed = Signal(object, dict)
    """Emitted with a detached DecodedSurface and shader adjustments."""

    failed = Signal(Path, str)
    """Emitted when loading or processing the image fails."""

    finished = Signal(object)
    """Emitted for success, failure and cooperative cancellation."""


class _AdjustedImageWorker(QRunnable):
    """Decode one viewport-aware neutral surface on a background thread."""

    def __init__(
        self,
        request: DetailRenderRequest,
        signals: _AdjustedImageSignals,
        backend: StillDecodeBackend | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._request = request.with_decode_level()
        self._source = self._request.source_identity.path
        self._signals = signals
        self._backend = backend or DefaultStillDecodeBackend()
        self._cancelled = False
        self._submitted_at = time.perf_counter()

    @property
    def signals(self) -> _AdjustedImageSignals:
        """Expose the scheduler-owned lifecycle relay."""

        return self._signals

    def cancel(self) -> None:
        """Suppress delivery when this request is no longer current."""

        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def update_request(self, request: DetailRenderRequest) -> None:
        """Adopt the newest same-surface render state during promotion/reuse."""

        self._request = request.with_decode_level()

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        """Perform the expensive image work outside the GUI thread."""

        self._signals.started.emit(self)
        started = time.perf_counter()
        log_detail_profile(
            "still_worker",
            "queue_wait",
            (started - self._submitted_at) * 1000.0,
            path=self._source.name,
        )
        try:
            decode_started = time.perf_counter()
            surface = self._backend.decode(self._request, self)
            log_detail_profile(
                "still_worker",
                "decode",
                (time.perf_counter() - decode_started) * 1000.0,
                path=self._source.name,
            )
        except DecodeCancelledError:
            return
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - codec-specific
            if not self._cancelled:
                self._signals.failed.emit(self._source, str(exc))
            return

        try:
            adjustments_started = time.perf_counter()
            active_request = self._request
            raw_adjustments = dict(active_request.raw_adjustments or {})
            adjustments: dict = {}
            if raw_adjustments:
                stats = compute_color_statistics(surface.image)
                adjustments = resolve_adjustment_mapping(
                    raw_adjustments,
                    stats=stats,
                    normalize_bw_for_render=True,
                )
            log_detail_profile(
                "still_worker",
                "adjustments",
                (time.perf_counter() - adjustments_started) * 1000.0,
                path=self._source.name,
                adjustments=len(adjustments),
            )
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - resolver-specific
            if not self._cancelled:
                self._signals.failed.emit(self._source, str(exc))
            return

        # Pass the raw image and adjustments to the main thread. The GL viewer
        # will apply the adjustments on the GPU.
        log_detail_profile(
            "still_worker",
            "total",
            (time.perf_counter() - started) * 1000.0,
            path=self._source.name,
            has_adjustments=bool(adjustments),
        )
        if self._cancelled:
            return
        emit_detail_event(
            "backend_selected",
            generation=active_request.generation,
            asset_id=active_request.asset_id,
            suffix=self._source.suffix.lower(),
            backend=surface.backend,
            decode_level=surface.decode_level,
        )
        if surface.fallback:
            emit_detail_event(
                "decode_fallback",
                generation=active_request.generation,
                asset_id=active_request.asset_id,
                suffix=self._source.suffix.lower(),
                fallback=surface.fallback,
            )
        emit_detail_event(
            "surface_ready",
            generation=active_request.generation,
            asset_id=active_request.asset_id,
            width=surface.decoded_size[0],
            height=surface.decoded_size[1],
            decode_level=surface.decode_level,
        )
        self._signals.completed.emit(surface, adjustments or {})


class _ScheduledAdjustedImageWorker(_AdjustedImageWorker):
    """Production runnable that always reports terminal completion."""

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        try:
            super().run()
        finally:
            self._signals.finished.emit(self)


class _AdjustmentPreparationSignals(QObject):
    ready = Signal(object, dict)
    failed = Signal(object, str)
    finished = Signal(object)


class _AdjustmentPreparationWorker(QRunnable):
    """Read raw sidecar state without touching the GUI or decode lanes."""

    def __init__(
        self,
        key: object,
        source: Path,
        signals: _AdjustmentPreparationSignals,
        edit_service: EditServicePort | None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.key = key
        self.source = source
        self.signals = signals
        self._edit_service = edit_service
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # pragma: no cover - worker-thread filesystem boundary
        try:
            adjustments = (
                dict(self._edit_service.read_adjustments(self.source) or {})
                if self._edit_service is not None
                else {}
            )
            if not self._cancelled:
                self.signals.ready.emit(self.key, adjustments)
        except Exception as exc:  # noqa: BLE001 - edit providers have varied I/O failures
            if not self._cancelled:
                self.signals.failed.emit(self.key, str(exc))
        finally:
            self.signals.finished.emit(self)


@dataclass(slots=True)
class _PreparedRequestIntent:
    asset_id: str
    source_identity: AssetSourceIdentity
    generation: int
    reason: str


@dataclass(slots=True)
class _PreparationEntry:
    worker: _AdjustmentPreparationWorker
    intents: list[_PreparedRequestIntent]
    priority: int
    result: dict | None = None


class StillImageDecodeScheduler(QThreadPool):
    """Two-lane latest-wins scheduler for non-cancellable native decoders."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # One native decoder may be stuck in an uninterruptible codec call. A
        # second lane lets the newest request bypass it; queued middle requests
        # are removed by ``tryTake`` and running stale results are discarded.
        self.setMaxThreadCount(2)
        self.setThreadPriority(QThread.Priority.HighPriority)


class PlayerViewController(QObject):
    """Control which player surface is visible and manage related UI state."""

    liveReplayRequested = Signal()
    """Re-emitted when the image viewer asks to replay a Live Photo."""

    imageLoadingFailed = Signal(Path, str)
    """Emitted when a still image fails to load or post-process."""

    stillFramePresented = Signal(object, int)
    """Emitted after the requested viewport surface is presented."""

    def __init__(
        self,
        player_stack: QStackedWidget,
        image_viewer: GLImageViewer,
        video_area: VideoArea,
        placeholder: QWidget,
        live_badge: LiveBadge,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Store references to the widgets composing the player area."""

        super().__init__(parent)
        self._player_stack = player_stack
        self._image_viewer = image_viewer
        self._video_area = video_area
        self._placeholder = placeholder
        self._placeholder_default_text = (
            placeholder.text() if isinstance(placeholder, QLabel) else None
        )
        self._uses_standard_placeholder = self._placeholder_default_text in {
            "Select a photo or video to preview.",
            tr("DetailPage", "Select a photo or video to preview."),
        }
        self._live_badge = live_badge
        self._edit_service_getter = edit_service_getter
        self._image_viewer_index = player_stack.indexOf(image_viewer)
        self._image_viewer.replayRequested.connect(self.liveReplayRequested)
        self._pool = StillImageDecodeScheduler(self)
        self._decode_backend = DefaultStillDecodeBackend()
        self._still_scheduler = DetailStillRequestScheduler(
            pool=self._pool,
            worker_factory=self._create_adjusted_image_worker,
            reuse_enabled=detail_scheduler_v3_enabled(),
            parent=self,
        )
        self._still_scheduler.ready.connect(self._on_scheduled_image_ready)
        self._still_scheduler.failed.connect(self._on_scheduled_image_failed)
        self._preparation_pool = QThreadPool(self)
        self._preparation_pool.setMaxThreadCount(1)
        self._preparation_entries: dict[object, _PreparationEntry] = {}
        self._preparation_entry_by_worker: dict[int, _PreparationEntry] = {}
        self._pending_layout_intent: tuple[_PreparedRequestIntent, dict] | None = None
        self._request_generation = 0
        self._present_generation = 0
        self._present_started_at: float | None = None
        self._present_source: Path | None = None
        self._loading_source: Path | None = None
        self._loading_started_at: float | None = None
        self._defer_still_updates = False
        self._pending_still: tuple[DecodedSurface, dict] | None = None
        self._current_full_image: QImage | None = None

        # Per-widget first-render tracking.  QRhiWidget backing textures are
        # uninitialised (transparent) until the first ``render()`` call fills
        # them.  An opaque *init cover* in the DetailPageWidget hides this
        # transparent region.  The cover must stay visible until the currently
        # shown QRhiWidget has rendered at least once; only then is it safe to
        # remove.  When switching to a widget that has *not* yet rendered we
        # re-show the cover to prevent a one-frame transparency flash.
        self._image_viewer_rendered = False
        self._video_renderer_rendered = False

        self._image_viewer.firstFrameReady.connect(self._on_image_first_render)
        self._video_area.firstFrameReady.connect(self._on_video_first_render)
        still_presented = getattr(self._image_viewer, "stillFramePresented", None)
        if still_presented is not None:
            still_presented.connect(self._on_still_frame_presented)

    # ------------------------------------------------------------------
    # High-level surface selection helpers
    # ------------------------------------------------------------------
    def _default_placeholder_text(self) -> str | None:
        if self._uses_standard_placeholder:
            return tr("DetailPage", "Select a photo or video to preview.")
        return self._placeholder_default_text

    def _on_image_first_render(self) -> None:
        """Mark image viewer as initialised; hide cover if it is visible."""
        self._image_viewer_rendered = True
        if self._player_stack.currentWidget() is self._image_viewer:
            self._hide_detail_init_cover()

    def _on_video_first_render(self) -> None:
        """Mark video renderer as initialised; hide cover if it is visible."""
        self._video_renderer_rendered = True
        if self._player_stack.currentWidget() is self._video_area:
            self._hide_detail_init_cover()

    def _hide_detail_init_cover(self) -> None:
        """Walk up to ``DetailPageWidget`` and hide the init cover."""
        from ..widgets.detail_page import DetailPageWidget

        widget = self._player_stack.parent()
        while widget is not None:
            if isinstance(widget, DetailPageWidget):
                widget.hide_rhi_init_cover()
                break
            widget = widget.parent()

    def _show_detail_init_cover(self) -> None:
        """Walk up to ``DetailPageWidget`` and re-show the init cover."""
        from ..widgets.detail_page import DetailPageWidget

        widget = self._player_stack.parent()
        while widget is not None:
            if isinstance(widget, DetailPageWidget):
                widget.show_rhi_init_cover()
                break
            widget = widget.parent()

    def show_placeholder(self, message: str | None = None) -> None:
        """Display the placeholder widget and clear any previous image."""
        if isinstance(self._placeholder, QLabel):
            self._placeholder.setText(
                self._default_placeholder_text() if message is None else message
            )
        self._video_area.hide_controls(animate=False)
        self.hide_live_badge()
        if self._player_stack.currentWidget() is not self._placeholder:
            self._player_stack.setCurrentWidget(self._placeholder)
        if not self._player_stack.isVisible():
            self._player_stack.show()
        # 不再上传“空图像”，而是显式清空纹理/图像
        self._image_viewer.set_image(None, {})

    def show_image_surface(self) -> None:
        """Reveal the still-image viewer surface."""

        # Hide lingering transport controls from the video surface so the
        # still viewer never inherits a faded overlay background.
        self._video_area.hide_controls(animate=False)

        # If the image viewer has never rendered, its QRhiWidget backing
        # texture is still uninitialised (transparent).  Re-show the opaque
        # init cover *before* switching the stack so the user never sees a
        # transparent frame.
        if not self._image_viewer_rendered:
            self._show_detail_init_cover()

        if self._player_stack.currentWidget() is not self._image_viewer:
            if self._player_stack.indexOf(self._image_viewer) != -1:
                self._player_stack.setCurrentWidget(self._image_viewer)
        if not self._player_stack.isVisible():
            self._player_stack.show()
        # Request an immediate update so the GL widget draws the latest frame as
        # soon as Qt processes the next paint cycle, mirroring the responsiveness
        # of the legacy QLabel-based viewer.
        self._image_viewer.update()

    def show_video_surface(self, *, interactive: bool) -> None:
        """Switch the stacked widget to the video surface.

        Parameters
        ----------
        interactive:
            ``True`` enables the floating playback controls (used for regular
            videos). ``False`` keeps the controls hidden so Live Photos can play
            unobstructed while still allowing the badge to trigger replays.
        """

        self._video_area.set_controls_enabled(interactive)
        if interactive:
            # Present the controls immediately so keyboard users see the
            # transport state without having to move the pointer.
            self._video_area.show_controls(animate=False)
        else:
            self._video_area.hide_controls(animate=False)

        # If the video renderer has never rendered, its QRhiWidget backing
        # texture is still uninitialised (transparent).  Re-show the opaque
        # init cover *before* switching the stack so the user never sees a
        # transparent frame.
        if not self._video_renderer_rendered:
            self._show_detail_init_cover()

        if self._player_stack.currentWidget() is not self._video_area:
            self._player_stack.setCurrentWidget(self._video_area)
        if not self._player_stack.isVisible():
            self._player_stack.show()

        # Hand focus to the graphics view so space/arrow shortcuts continue to
        # target the media surface, matching the ergonomics of the legacy
        # QWidget-based implementation.
        self._video_area.video_view().setFocus()

    # ------------------------------------------------------------------
    # Content helpers
    # ------------------------------------------------------------------
    def display_image(
        self,
        source: Path,
        *,
        asset_id: str = "",
        request_generation: int | None = None,
        source_identity: AssetSourceIdentity | None = None,
    ) -> bool:
        """Begin loading ``source`` asynchronously, returning scheduling success."""
        identity = source_identity or AssetSourceIdentity.create(source)
        source = identity.path
        if request_generation is None:
            self._request_generation += 1
        else:
            self._request_generation = int(request_generation)
        request_generation = self._request_generation
        self._loading_source = source
        self._loading_started_at = time.perf_counter()

        # V2 keeps the opaque placeholder visible until the full source image
        # has decoded and is ready for one atomic surface switch.
        if detail_pipeline_v2_enabled():
            self.show_placeholder("")
        else:
            self.show_image_surface()
            self._image_viewer.set_image(None, {})
        emit_detail_event(
            "decode_started",
            generation=request_generation,
            media_type="image",
            suffix=source.suffix.lower(),
        )
        scheduled = self._schedule_adjustment_preparation(
            _PreparedRequestIntent(
                asset_id=str(asset_id),
                source_identity=identity,
                generation=request_generation,
                reason="initial",
            )
        )
        if not scheduled:
            self._loading_source = None
            self._loading_started_at = None
        return scheduled

    def _cancel_stale_image_workers(self) -> None:
        """Compatibility wrapper for callers invalidating the active generation."""

        self._still_scheduler.cancel_foreground()

    def _create_adjusted_image_worker(
        self,
        request: DetailRenderRequest,
    ) -> _ScheduledAdjustedImageWorker:
        signals = _AdjustedImageSignals()
        worker = _ScheduledAdjustedImageWorker(
            request,
            signals,
            backend=self._decode_backend,
        )
        return worker

    def prefetch_image(
        self,
        descriptor: DetailPrefetchDescriptor | Path,
    ) -> bool:
        """Warm exactly one viewport-surface candidate at low priority."""

        if isinstance(descriptor, DetailPrefetchDescriptor):
            asset_id = descriptor.asset_id
            source = descriptor.path
            identity = descriptor.source_identity or AssetSourceIdentity.create(source)
        else:
            source = Path(descriptor)
            asset_id = ""
            identity = AssetSourceIdentity.create(source)
        if self._viewport_metrics() is None:
            return False
        return self._schedule_adjustment_preparation(
            _PreparedRequestIntent(
                asset_id=str(asset_id),
                source_identity=identity,
                generation=0,
                reason="prefetch",
            )
        )

    def prefetch_images(
        self,
        candidates: list[DetailPrefetchDescriptor | Path],
    ) -> bool:
        """Compatibility helper; Phase 1 admits only the first candidate."""

        return bool(candidates) and self.prefetch_image(candidates[0])

    def _schedule_adjustment_preparation(self, intent: _PreparedRequestIntent) -> bool:
        identity = intent.source_identity
        key = (intent.asset_id, identity.path, identity.revision)
        existing = self._preparation_entries.get(key)
        priority = 1 if intent.reason != "prefetch" else -1
        if existing is not None:
            if existing.result is not None:
                return self._dispatch_prepared_intent(intent, dict(existing.result))
            existing.intents.append(intent)
            if priority > existing.priority and self._preparation_pool.tryTake(existing.worker):
                existing.priority = priority
                self._preparation_pool.start(existing.worker, priority)
            return True

        if priority > 0:
            for other_key, entry in tuple(self._preparation_entries.items()):
                if other_key == key:
                    continue
                if self._preparation_pool.tryTake(entry.worker):
                    entry.worker.cancel()
                    self._retire_preparation_entry(entry)
                else:
                    entry.intents.clear()

        signals = _AdjustmentPreparationSignals()
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = _AdjustmentPreparationWorker(key, identity.path, signals, edit_service)
        entry = _PreparationEntry(worker=worker, intents=[intent], priority=priority)
        self._preparation_entries[key] = entry
        self._preparation_entry_by_worker[id(worker)] = entry
        signals.ready.connect(self._on_adjustment_prepared)
        signals.failed.connect(self._on_adjustment_preparation_failed)
        signals.finished.connect(self._on_adjustment_preparation_finished)
        try:
            self._preparation_pool.start(worker, priority)
        except RuntimeError:
            self._retire_preparation_entry(entry)
            return False
        return True

    def _on_adjustment_prepared(self, key: object, adjustments: dict) -> None:
        entry = self._preparation_entries.get(key)
        if entry is None:
            return
        entry.result = dict(adjustments)
        for intent in tuple(entry.intents):
            self._dispatch_prepared_intent(intent, dict(adjustments))

    def _dispatch_prepared_intent(
        self,
        intent: _PreparedRequestIntent,
        adjustments: dict,
    ) -> bool:
        metrics = self._viewport_metrics()
        if metrics is None:
            if intent.reason != "prefetch" and intent.generation == self._request_generation:
                self._pending_layout_intent = (intent, dict(adjustments))
                QTimer.singleShot(0, self._retry_pending_layout_intent)
                return True
            return False
        physical_size, dpr = metrics
        request = DetailRenderRequest(
            generation=int(intent.generation),
            asset_id=intent.asset_id,
            source_identity=intent.source_identity,
            viewport_physical_size=physical_size,
            device_pixel_ratio=dpr,
            geometry=DetailGeometryState.from_adjustments(adjustments),
            reason="prefetch" if intent.reason == "prefetch" else "initial",
            texture_limit=self._texture_limit(),
            raw_adjustments=dict(adjustments),
        ).with_decode_level()
        emit_detail_event(
            "level_selected",
            generation=request.generation,
            asset_id=request.asset_id,
            suffix=request.source_identity.path.suffix.lower(),
            decode_level=request.decode_level,
            viewport_width=physical_size[0],
            viewport_height=physical_size[1],
            reason=request.reason,
        )
        if request.decode_level == "full" and max(
            request.source_identity.width,
            request.source_identity.height,
        ) > 4096:
            emit_detail_event(
                "decode_fallback",
                generation=request.generation,
                asset_id=request.asset_id,
                suffix=request.source_identity.path.suffix.lower(),
                fallback="full_level",
            )
        if request.reason == "prefetch":
            return self._still_scheduler.prefetch(request)
        return self._still_scheduler.request(request)

    def _retry_pending_layout_intent(self) -> None:
        pending = self._pending_layout_intent
        if pending is None:
            return
        intent, adjustments = pending
        if intent.generation != self._request_generation:
            self._pending_layout_intent = None
            return
        if self._viewport_metrics() is None:
            QTimer.singleShot(16, self._retry_pending_layout_intent)
            return
        self._pending_layout_intent = None
        self._dispatch_prepared_intent(intent, adjustments)

    def _viewport_metrics(self) -> tuple[tuple[int, int], float] | None:
        width = int(self._image_viewer.width())
        height = int(self._image_viewer.height())
        if width <= 0 or height <= 0:
            return None
        dpr = max(1.0, float(self._image_viewer.devicePixelRatioF()))
        return ((max(1, round(width * dpr)), max(1, round(height * dpr))), dpr)

    def _texture_limit(self) -> int:
        getter = getattr(self._image_viewer, "maximum_texture_size", None)
        return max(1, int(getter())) if callable(getter) else 8192

    def _on_adjustment_preparation_failed(self, key: object, message: str) -> None:
        entry = self._preparation_entries.get(key)
        if entry is None:
            return
        for intent in tuple(entry.intents):
            if intent.reason != "prefetch" and intent.generation == self._request_generation:
                self._on_adjusted_image_failed(intent.source_identity.path, message)

    def _on_adjustment_preparation_finished(self, worker: object) -> None:
        entry = self._preparation_entry_by_worker.get(id(worker))
        if entry is not None:
            self._retire_preparation_entry(entry)

    def _retire_preparation_entry(self, entry: _PreparationEntry) -> None:
        key = entry.worker.key
        if self._preparation_entries.get(key) is entry:
            self._preparation_entries.pop(key, None)
        self._preparation_entry_by_worker.pop(id(entry.worker), None)
        entry.worker.signals.deleteLater()
        entry.worker.setAutoDelete(True)

    def _on_scheduled_image_ready(
        self,
        generation: int,
        surface: DecodedSurface,
        adjustments: dict,
    ) -> None:
        if generation != self._request_generation:
            return
        source = surface.decode_key.source
        self._present_generation = generation
        self._present_started_at = self._loading_started_at
        self._present_source = source
        self._on_adjusted_image_ready(
            source,
            surface,
            adjustments,
        )

    def _on_scheduled_image_failed(
        self,
        generation: int,
        source: Path,
        message: str,
    ) -> None:
        if generation != self._request_generation:
            return
        self._on_adjusted_image_failed(source, message)

    def _on_still_frame_presented(self, source: object) -> None:
        presented_path = getattr(source, "source", source)
        if presented_path != self._present_source:
            return
        generation = self._present_generation
        started_at = self._present_started_at
        if generation <= 0 or started_at is None:
            return
        log_detail_profile(
            "player_view",
            "still.presented",
            (time.perf_counter() - started_at) * 1000.0,
            path=Path(presented_path).name if presented_path is not None else "",
            generation=generation,
        )
        emit_detail_event(
            "presented",
            generation=generation,
            media_type="image",
        )
        self._present_started_at = None
        self._present_source = None
        self.stillFramePresented.emit(presented_path, generation)

    def shutdown(self, *, timeout_ms: int = 1500) -> None:
        """Cancel queued preparation/decode work and flush profiling."""

        self.cancel_pending_image_requests()
        self._preparation_pool.clear()
        self._preparation_pool.waitForDone(max(0, int(timeout_ms)))
        self._still_scheduler.shutdown(timeout_ms=timeout_ms)
        shutdown_detail_profile(timeout_ms=min(max(0, int(timeout_ms)), 1000))

    def clear_frame_cache(self) -> None:
        """Compatibility no-op until the Phase-3 neutral surface cache exists."""

    def current_full_image(self) -> QImage | None:
        """Return the retained viewport surface for compatibility consumers."""

        image = self._current_full_image
        return QImage(image) if image is not None and not image.isNull() else None

    def cancel_pending_image_requests(self) -> None:
        """Invalidate still work when Detail or the current library is left."""

        self._request_generation += 1
        self._cancel_stale_image_workers()
        self._loading_source = None
        self._loading_started_at = None
        self._present_source = None
        self._present_started_at = None
        self._pending_still = None
        self._pending_layout_intent = None
        self._current_full_image = None
        for entry in tuple(self._preparation_entries.values()):
            entry.intents.clear()
            entry.worker.cancel()
            if self._preparation_pool.tryTake(entry.worker):
                self._retire_preparation_entry(entry)

    def defer_still_updates(self, enabled: bool) -> None:
        """Control whether still frames should be applied immediately."""
        self._defer_still_updates = bool(enabled)
        if not self._defer_still_updates:
            self.apply_pending_still()

    def apply_pending_still(self) -> bool:
        """Apply any deferred still frame if available."""
        if self._pending_still is None:
            return False
        surface, adjustments = self._pending_still
        self._pending_still = None
        self._apply_still_frame(surface, adjustments)
        return True

    def clear_image(self) -> None:
        """Remove any pixmap currently shown in the image viewer."""
        # 清空而非传空图像，避免一帧“空绘制/空上传”
        self._current_full_image = None
        self._image_viewer.set_image(None, {})

    # ------------------------------------------------------------------
    # Live badge helpers
    # ------------------------------------------------------------------
    def show_live_badge(self) -> None:
        """Ensure the Live Photo badge is visible and raised above overlays."""

        self._live_badge.show()
        self._live_badge.raise_()

    def hide_live_badge(self) -> None:
        """Hide the Live Photo badge."""

        self._live_badge.hide()

    def is_live_badge_visible(self) -> bool:
        """Return ``True`` when the Live Photo badge is currently visible."""

        return self._live_badge.isVisible()

    # ------------------------------------------------------------------
    # Convenience wrappers used by the playback controller
    # ------------------------------------------------------------------
    def set_live_replay_enabled(self, enabled: bool) -> None:
        """Delegate Live Photo replay toggling to the image viewer."""

        self._image_viewer.set_live_replay_enabled(enabled)

    def is_showing_video(self) -> bool:
        """Return ``True`` when the video surface is the current widget."""

        return self._player_stack.currentWidget() is self._video_area

    def is_showing_image(self) -> bool:
        """Return ``True`` when the still-image surface is active."""

        return self._player_stack.currentWidget() is self._image_viewer

    def note_video_activity(self) -> None:
        """Forward external activity notifications to the video controls."""

        self._video_area.note_activity()

    @property
    def image_viewer(self) -> GLImageViewer:
        """Expose the image viewer for read-only integrations."""

        return self._image_viewer

    @property
    def video_area(self) -> VideoArea:
        """Expose the video area for media output bindings."""

        return self._video_area

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------
    def _on_adjusted_image_ready(
        self,
        source: Path,
        surface: DecodedSurface,
        adjustments: dict,
    ) -> None:
        """Render a neutral surface when the matching worker completes."""
        if self._loading_source != source:
            return

        image = surface.image

        if self._loading_started_at is not None:
            log_detail_profile(
                "player_view",
                "still.worker_ready",
                (time.perf_counter() - self._loading_started_at) * 1000.0,
                path=source.name,
                has_adjustments=bool(adjustments),
            )

        if image.isNull():
            if self._loading_source == source:
                self._loading_source = None
                self._loading_started_at = None
            self._image_viewer.set_image(None, {})
            self.imageLoadingFailed.emit(source, "Image decoder returned an empty frame")
            return

        if self._defer_still_updates and self._player_stack.currentWidget() is self._video_area:
            self._pending_still = (surface, adjustments)
        else:
            self._apply_still_frame(
                surface,
                adjustments,
                reset_view=True,
            )

        if self._loading_source == source:
            self._loading_source = None
            self._loading_started_at = None

    def _on_adjusted_image_failed(self, source: Path, message: str) -> None:
        """Propagate worker failures while ensuring stale results are ignored."""

        if self._loading_source != source:
            return

        if self._loading_source == source:
            self._loading_source = None
            self._loading_started_at = None
        self._image_viewer.set_image(None)
        self.imageLoadingFailed.emit(source, message)

    def _apply_still_frame(
        self,
        surface: DecodedSurface,
        adjustments: dict,
        *,
        reset_view: bool = True,
    ) -> None:
        """Render the already-normalised still surface on the GL viewer."""
        apply_started = time.perf_counter()
        source = surface.decode_key.source
        image = surface.image
        self.show_image_surface()
        self._current_full_image = QImage(image)
        self._image_viewer.set_image(
            image,
            adjustments,
            image_source=surface.decode_key,
            source_size=surface.source_size,
            reset_view=reset_view,
        )
        self._image_viewer.update()
        log_detail_profile(
            "player_view",
            "still.apply_frame",
            (time.perf_counter() - apply_started) * 1000.0,
            path=source.name,
            has_adjustments=bool(adjustments),
        )

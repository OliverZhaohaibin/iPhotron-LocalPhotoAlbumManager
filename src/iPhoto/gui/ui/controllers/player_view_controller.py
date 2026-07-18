"""Coordinator for the stacked player widgets used on the detail page."""

from __future__ import annotations

import time
from collections import OrderedDict
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
from ....gui.detail_decode_backend import (
    DecodeCancelledError,
    DecodedSurface,
    DefaultStillDecodeBackend,
    StillDecodeBackend,
)
from ....gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
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
from ....gui.detail_render_session import EditRenderState, PhotoRenderSessionHandle
from ....gui.detail_surface_cache import CachedStillDecodeBackend
from ....gui.i18n import tr
from ..widgets.gl_image_viewer import GLImageViewer
from ..widgets.live_badge import LiveBadge
from ..widgets.video_area import VideoArea


class _AdjustedImageSignals(QObject):
    """Relay neutral-surface completion events back to the GUI thread."""

    started = Signal(object)
    """Emitted immediately before the runnable enters its decode path."""

    completed = Signal(object)
    """Emitted with one detached neutral DecodedSurface."""

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
            log_detail_profile(
                "still_worker",
                "adjustments",
                (time.perf_counter() - adjustments_started) * 1000.0,
                path=self._source.name,
                adjustments=len(raw_adjustments),
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
            has_adjustments=bool(raw_adjustments),
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
        self._signals.completed.emit(surface)


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
    residency_slot: str | None = None
    window_generation: int = 0
    zoom_factor: float = 1.0


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
        library_root_getter: Callable[[], Path | None] | None = None,
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
        self._library_root_getter = library_root_getter
        self._image_viewer_index = player_stack.indexOf(image_viewer)
        self._image_viewer.replayRequested.connect(self.liveReplayRequested)
        self._pool = StillImageDecodeScheduler(self)
        self._decode_backend = CachedStillDecodeBackend(DefaultStillDecodeBackend())
        if self._library_root_getter is not None:
            self._decode_backend.bind_library(self._library_root_getter())
        self._still_scheduler = DetailStillRequestScheduler(
            pool=self._pool,
            worker_factory=self._create_adjusted_image_worker,
            reuse_enabled=detail_scheduler_v3_enabled(),
            parent=self,
        )
        self._still_scheduler.ready.connect(self._on_scheduled_image_ready)
        self._still_scheduler.warmed.connect(self._on_scheduled_surface_warmed)
        self._still_scheduler.failed.connect(self._on_scheduled_image_failed)
        self._preparation_pool = QThreadPool(self)
        self._preparation_pool.setMaxThreadCount(1)
        self._preparation_entries: dict[object, _PreparationEntry] = {}
        self._preparation_entry_by_worker: dict[int, _PreparationEntry] = {}
        self._pending_layout_intent: tuple[_PreparedRequestIntent, dict] | None = None
        self._request_generation = 0
        self._residency_window_generation = 0
        self._active_asset_id = ""
        self._active_source_identity: AssetSourceIdentity | None = None
        self._active_adjustments: dict = {}
        self._current_decode_level: int | str | None = None
        self._request_reason_by_generation: dict[int, str] = {}
        self._pending_zoom_factor = 1.0
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(80)
        self._lod_timer.timeout.connect(self._request_higher_lod)
        zoom_changed = getattr(self._image_viewer, "zoomChanged", None)
        if zoom_changed is not None:
            zoom_changed.connect(self._on_viewer_zoom_changed)
        viewport_changed = getattr(self._image_viewer, "viewportMetricsChanged", None)
        if viewport_changed is not None:
            viewport_changed.connect(self._on_viewport_metrics_changed)
        self._present_generation = 0
        self._present_started_at: float | None = None
        self._present_source: Path | None = None
        self._loading_source: Path | None = None
        self._loading_started_at: float | None = None
        self._defer_still_updates = False
        self._pending_still: tuple[DecodedSurface, dict] | None = None
        self._current_full_image: QImage | None = None
        self._render_sessions: OrderedDict[tuple, PhotoRenderSessionHandle] = OrderedDict()
        self._current_render_session: PhotoRenderSessionHandle | None = None
        self._next_render_session_id = 1
        self._render_session_interaction_depth: dict[int, int] = {}
        self._render_session_lod_pending: set[int] = set()
        self._render_session_pending_surfaces: dict[
            int,
            tuple[int, DecodedSurface],
        ] = {}

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
        # The placeholder is a separate stack page. Keep the bounded still
        # residency window intact so a hot return can activate its texture
        # without another upload.

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
        self._residency_window_generation += 1
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
        """Warm the previous/next window without occupying both decode lanes."""

        self._residency_window_generation += 1
        window_generation = self._residency_window_generation
        accepted = False
        for slot, candidate in zip(("previous", "next"), candidates[:2], strict=False):
            if isinstance(candidate, DetailPrefetchDescriptor):
                identity = candidate.source_identity or AssetSourceIdentity.create(candidate.path)
                asset_id = candidate.asset_id
            else:
                identity = AssetSourceIdentity.create(Path(candidate))
                asset_id = ""
            accepted = self._schedule_adjustment_preparation(
                _PreparedRequestIntent(
                    asset_id=str(asset_id),
                    source_identity=identity,
                    generation=0,
                    reason="prefetch",
                    residency_slot=slot,
                    window_generation=window_generation,
                )
            ) or accepted
        return accepted

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
            reason=(
                intent.reason
                if intent.reason in {"prefetch", "initial", "resize", "zoom"}
                else "initial"
            ),
            texture_limit=self._texture_limit(),
            raw_adjustments=dict(adjustments),
            zoom_factor=intent.zoom_factor,
            residency_slot=intent.residency_slot,
            window_generation=intent.window_generation,
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
        if request.reason in {"zoom", "resize"} and not self._is_higher_level(
            request.decode_level,
            self._current_decode_level,
        ):
            return False
        self._active_asset_id = request.asset_id
        self._active_source_identity = request.source_identity
        self._active_adjustments = dict(adjustments)
        self._request_reason_by_generation[request.generation] = request.reason
        session = self._session_for_request(request)
        render_adjustments = dict(adjustments)
        if session is not None:
            if dict(session.baseline_state.raw_adjustments) != dict(adjustments):
                state = EditRenderState.create(
                    adjustments,
                    color_stats=session.edit_state.color_stats,
                    revision=("index", request.source_identity.index_revision),
                )
                session.edit_state = state
                session.baseline_state = state
            render_adjustments = dict(session.edit_state.shader_adjustments)
        decode_key = DetailDecodeKey.from_request(request)
        defer_presentation = bool(
            self._defer_still_updates
            and self._player_stack.currentWidget() is self._video_area
        )
        if defer_presentation and session is not None and session.activate_surface(decode_key):
            self._current_render_session = session
            self._touch_render_session(session)
            self._current_decode_level = request.decode_level
            self._present_generation = request.generation
            self._present_started_at = self._loading_started_at
            self._present_source = request.source_identity.path
            self._pending_still = (session.current_surface, render_adjustments)
            self._loading_source = None
            self._loading_started_at = None
            return True

        activate_resident = getattr(self._image_viewer, "activate_resident_surface", None)
        if not defer_presentation and callable(activate_resident) and activate_resident(
            decode_key,
            render_adjustments,
            source_size=(request.source_identity.width, request.source_identity.height),
            reset_view=request.reason == "initial",
            generation=request.generation,
        ):
            self._present_generation = request.generation
            self._present_started_at = self._loading_started_at
            self._present_source = request.source_identity.path
            self.show_image_surface()
            self._loading_source = None
            self._loading_started_at = None
            self._current_decode_level = request.decode_level
            if session is not None:
                session.activate_surface(decode_key)
                self._current_render_session = session
                self._touch_render_session(session)
            return True
        if request.reason in {"zoom", "resize"}:
            emit_detail_event(
                "lod_upgrade_requested",
                generation=request.generation,
                asset_id=request.asset_id,
                decode_level=request.decode_level,
                reason=request.reason,
            )
        return self._still_scheduler.request(request)

    @staticmethod
    def _is_higher_level(candidate: object, current: object) -> bool:
        def rank(value: object) -> int:
            if value == "full":
                return 1 << 30
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        return rank(candidate) > rank(current)

    def _on_viewer_zoom_changed(self, factor: float) -> None:
        self._pending_zoom_factor = max(1.0, float(factor))
        if self._active_source_identity is not None:
            if not self._defer_lod_for_active_render_interaction():
                self._lod_timer.start()

    def _on_viewport_metrics_changed(self) -> None:
        if self._active_source_identity is None:
            return
        zoom_getter = getattr(self._image_viewer, "zoom_factor", None)
        self._pending_zoom_factor = max(
            1.0,
            float(zoom_getter()) if callable(zoom_getter) else self._pending_zoom_factor,
        )
        if not self._defer_lod_for_active_render_interaction():
            self._lod_timer.start()

    def _request_higher_lod(self) -> None:
        identity = self._active_source_identity
        if identity is None or self._loading_source is not None:
            return
        self._request_generation += 1
        generation = self._request_generation
        self._loading_source = identity.path
        self._loading_started_at = time.perf_counter()
        intent = _PreparedRequestIntent(
            asset_id=self._active_asset_id,
            source_identity=identity,
            generation=generation,
            reason="zoom",
            zoom_factor=self._pending_zoom_factor,
        )
        if not self._dispatch_prepared_intent(intent, dict(self._active_adjustments)):
            self._loading_source = None
            self._loading_started_at = None

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
    ) -> None:
        if generation != self._request_generation:
            return
        session = self._current_render_session
        if (
            session is not None
            and session.source == surface.decode_key.source
            and self._render_session_interaction_depth.get(session.session_id, 0) > 0
        ):
            self._render_session_pending_surfaces[session.session_id] = (
                generation,
                surface,
            )
            return
        self._present_scheduled_image(generation, surface)

    def _present_scheduled_image(
        self,
        generation: int,
        surface: DecodedSurface,
    ) -> None:
        """Install and present one current-generation decoded surface."""

        if generation != self._request_generation:
            return
        source = surface.decode_key.source
        raw_adjustments = dict(self._active_adjustments)
        session = self._upsert_render_session(surface, raw_adjustments)
        if session.edit_references > 0:
            adjustments = dict(session.edit_state.shader_adjustments)
        else:
            adjustments = dict(session.baseline_state.shader_adjustments)
        self._current_render_session = session
        self._current_decode_level = surface.decode_level
        self._present_generation = generation
        self._present_started_at = self._loading_started_at
        self._present_source = source
        self._on_adjusted_image_ready(
            source,
            surface,
            adjustments,
            reset_view=self._request_reason_by_generation.get(generation) not in {"zoom", "resize"},
        )

    def _on_scheduled_surface_warmed(
        self,
        request: DetailRenderRequest,
        surface: DecodedSurface,
    ) -> None:
        if request.window_generation != self._residency_window_generation:
            return
        self._upsert_render_session(
            surface,
            dict(request.raw_adjustments or {}),
            make_current=False,
            source_identity=request.source_identity,
        )
        warmer = getattr(self._image_viewer, "warm_still_surface", None)
        if callable(warmer):
            warmer(
                surface,
                {},
                residency_slot=request.residency_slot,
                window_generation=request.window_generation,
            )

    def _on_scheduled_image_failed(
        self,
        generation: int,
        source: Path,
        message: str,
    ) -> None:
        if generation != self._request_generation:
            return
        reason = self._request_reason_by_generation.get(generation)
        if reason in {"zoom", "resize"}:
            if self._loading_source == source:
                self._loading_source = None
                self._loading_started_at = None
            emit_detail_event(
                "lod_upgrade_failed",
                generation=generation,
                asset_id=self._active_asset_id,
                reason=reason,
                message=str(message),
            )
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
        if self._request_reason_by_generation.get(generation) in {"zoom", "resize"}:
            emit_detail_event(
                "lod_upgrade_presented",
                generation=generation,
                media_type="image",
                decode_level=self._current_decode_level,
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
        self._decode_backend.shutdown(timeout_ms=min(max(0, int(timeout_ms)), 1000))
        shutdown_detail_profile(timeout_ms=min(max(0, int(timeout_ms)), 1000))

    def clear_frame_cache(self) -> None:
        """Clear library-scoped mapped surfaces and GPU still resources."""

        root = self._library_root_getter() if self._library_root_getter is not None else None
        self._decode_backend.bind_library(root)
        clear_residency = getattr(self._image_viewer, "clear_still_residency", None)
        if callable(clear_residency):
            clear_residency()
        self._render_sessions.clear()
        self._current_render_session = None
        self._render_session_interaction_depth.clear()
        self._render_session_lod_pending.clear()
        self._render_session_pending_surfaces.clear()

    def handle_memory_pressure(self) -> None:
        """Drop speculative GPU and mapped surfaces while preserving current draw state."""

        self._decode_backend.memory_cache.clear()
        trim_residency = getattr(self._image_viewer, "trim_still_residency", None)
        if callable(trim_residency):
            trim_residency()
        current = self._current_render_session
        self._render_sessions.clear()
        if current is not None:
            self._render_sessions[self._render_session_key_for_surface(current.current_surface)] = current

    def acquire_render_session(self, source: Path) -> PhotoRenderSessionHandle | None:
        """Acquire the current still session without reading or decoding the source."""

        session = self._current_render_session
        if session is None or session.source != Path(source).expanduser().absolute():
            return None
        if self._image_viewer.current_image_source() != session.current_texture_key:
            return None
        session.edit_references += 1
        self._touch_render_session(session)
        emit_detail_event(
            "render_session_acquired",
            generation=self._request_generation,
            asset_id=session.asset_id,
            session_id=session.session_id,
        )
        return session

    def update_render_session(
        self,
        handle: PhotoRenderSessionHandle,
        raw_adjustments: dict,
    ) -> EditRenderState:
        """Replace one session's immutable edit state and update GPU uniforms."""

        self._require_current_session(handle)
        previous_geometry = DetailGeometryState.from_adjustments(
            handle.edit_state.raw_adjustments
        )
        state = handle.next_state(raw_adjustments)
        self._active_adjustments = dict(state.raw_adjustments)
        self._image_viewer.set_adjustments(state.shader_adjustments)
        emit_detail_event(
            "edit_state_updated",
            generation=self._request_generation,
            asset_id=handle.asset_id,
            session_id=handle.session_id,
            revision=state.revision[1],
        )
        current_geometry = DetailGeometryState.from_adjustments(state.raw_adjustments)
        if current_geometry != previous_geometry:
            self._queue_render_session_lod(handle)
        return state

    def begin_render_session_interaction(
        self,
        handle: PhotoRenderSessionHandle,
    ) -> None:
        """Defer LOD replacement while an edit gesture is in progress."""

        self._require_current_session(handle)
        session_id = handle.session_id
        depth = self._render_session_interaction_depth.get(session_id, 0)
        self._render_session_interaction_depth[session_id] = depth + 1
        if depth == 0:
            if self._lod_timer.isActive():
                self._render_session_lod_pending.add(session_id)
            self._lod_timer.stop()

    def end_render_session_interaction(
        self,
        handle: PhotoRenderSessionHandle,
    ) -> None:
        """Finish an edit gesture and evaluate its final geometry once."""

        self._require_current_session(handle)
        session_id = handle.session_id
        depth = self._render_session_interaction_depth.get(session_id, 0)
        if depth > 1:
            self._render_session_interaction_depth[session_id] = depth - 1
            return
        self._render_session_interaction_depth.pop(session_id, None)
        pending_surface = self._render_session_pending_surfaces.pop(session_id, None)
        if pending_surface is not None:
            generation, surface = pending_surface
            self._present_scheduled_image(generation, surface)
        if session_id in self._render_session_lod_pending:
            self._render_session_lod_pending.discard(session_id)
            self._schedule_render_session_lod()

    def _queue_render_session_lod(self, handle: PhotoRenderSessionHandle) -> None:
        """Schedule or defer a geometry-driven LOD reevaluation."""

        session_id = handle.session_id
        if self._render_session_interaction_depth.get(session_id, 0) > 0:
            self._render_session_lod_pending.add(session_id)
            self._lod_timer.stop()
            return
        self._schedule_render_session_lod()

    def _schedule_render_session_lod(self) -> None:
        """Debounce one render-session LOD reevaluation."""

        self._pending_zoom_factor = max(1.0, self._image_viewer.zoom_factor())
        self._lod_timer.start()

    def _defer_lod_for_active_render_interaction(self) -> bool:
        """Record a pending LOD check when the current session is interactive."""

        handle = self._current_render_session
        if handle is None:
            return False
        session_id = handle.session_id
        if self._render_session_interaction_depth.get(session_id, 0) <= 0:
            return False
        self._render_session_lod_pending.add(session_id)
        self._lod_timer.stop()
        return True

    def finish_render_session(
        self,
        handle: PhotoRenderSessionHandle,
        *,
        committed: bool,
    ) -> EditRenderState:
        """Commit or discard live edits without replacing the resident texture."""

        self._require_current_session(handle)
        lod_pending = handle.session_id in self._render_session_lod_pending
        pending_surface = self._render_session_pending_surfaces.pop(
            handle.session_id,
            None,
        )
        self._render_session_interaction_depth.pop(handle.session_id, None)
        self._render_session_lod_pending.discard(handle.session_id)
        state = handle.commit_current_state() if committed else handle.restore_baseline()
        handle.edit_references = max(0, handle.edit_references - 1)
        self._active_adjustments = dict(state.raw_adjustments)
        self._image_viewer.set_adjustments(state.shader_adjustments)
        emit_detail_event(
            "render_session_released",
            generation=self._request_generation,
            asset_id=handle.asset_id,
            session_id=handle.session_id,
            committed=bool(committed),
        )
        if pending_surface is not None:
            generation, surface = pending_surface
            self._present_scheduled_image(generation, surface)
        if lod_pending:
            self._schedule_render_session_lod()
        return state

    def render_session_sidebar_input(
        self,
        handle: PhotoRenderSessionHandle,
    ) -> tuple[QImage, object]:
        """Return a shared neutral snapshot and its precomputed statistics."""

        self._require_current_session(handle)
        return QImage(handle.current_surface.image), handle.edit_state.color_stats

    def _require_current_session(self, handle: PhotoRenderSessionHandle) -> None:
        if handle is not self._current_render_session:
            raise RuntimeError("Photo render session is no longer current")

    @staticmethod
    def _render_session_key_for_surface(surface: DecodedSurface) -> tuple:
        key = surface.decode_key
        return (key.asset_id, key.source, key.source_revision, key.orientation)

    def _session_for_request(
        self,
        request: DetailRenderRequest,
    ) -> PhotoRenderSessionHandle | None:
        key = DetailDecodeKey.from_request(request)
        session_key = (key.asset_id, key.source, key.source_revision, key.orientation)
        return self._render_sessions.get(session_key)

    def _upsert_render_session(
        self,
        surface: DecodedSurface,
        raw_adjustments: dict,
        *,
        make_current: bool = True,
        source_identity: AssetSourceIdentity | None = None,
    ) -> PhotoRenderSessionHandle:
        session_key = self._render_session_key_for_surface(surface)
        session = self._render_sessions.get(session_key)
        if session is None:
            identity = source_identity or self._active_source_identity
            if identity is None or identity.path != surface.decode_key.source:
                identity = AssetSourceIdentity.create(
                    surface.decode_key.source,
                    size_bytes=surface.decode_key.source_revision[1],
                    source_mtime_ns=(
                        surface.decode_key.source_revision[2]
                        if surface.decode_key.source_revision[0] == "mtime"
                        else 0
                    ),
                    width=surface.source_size[0],
                    height=surface.source_size[1],
                    orientation=surface.decode_key.orientation,
                )
            state = EditRenderState.create(
                raw_adjustments,
                color_stats=surface.color_stats,
                revision=("index", identity.index_revision),
            )
            session = PhotoRenderSessionHandle(
                session_id=self._next_render_session_id,
                asset_id=surface.decode_key.asset_id,
                source_identity=identity,
                current_surface=surface,
                edit_state=state,
                baseline_state=state,
            )
            self._next_render_session_id += 1
            emit_detail_event(
                "render_session_created",
                generation=self._request_generation if make_current else 0,
                asset_id=session.asset_id,
                session_id=session.session_id,
            )
        else:
            session.replace_surface(surface)
            if session.edit_references == 0 and dict(session.baseline_state.raw_adjustments) != dict(raw_adjustments):
                state = EditRenderState.create(
                    raw_adjustments,
                    color_stats=surface.color_stats,
                    revision=("index", session.source_identity.index_revision),
                )
                session.edit_state = state
                session.baseline_state = state
        self._render_sessions.pop(session_key, None)
        self._render_sessions[session_key] = session
        while len(self._render_sessions) > 3:
            oldest_key, oldest = next(iter(self._render_sessions.items()))
            if oldest is self._current_render_session or oldest.edit_references > 0:
                self._render_sessions.move_to_end(oldest_key)
                if all(item.edit_references > 0 or item is self._current_render_session for item in self._render_sessions.values()):
                    break
                continue
            self._render_sessions.pop(oldest_key)
        if make_current:
            self._current_render_session = session
        return session

    def _touch_render_session(self, session: PhotoRenderSessionHandle) -> None:
        key = self._render_session_key_for_surface(session.current_surface)
        if self._render_sessions.pop(key, None) is not None:
            self._render_sessions[key] = session

    def current_full_image(self) -> QImage | None:
        """Return the retained viewport surface for compatibility consumers."""

        image = self._current_full_image
        return QImage(image) if image is not None and not image.isNull() else None

    def cancel_pending_image_requests(self) -> None:
        """Invalidate still work when Detail or the current library is left."""

        self._request_generation += 1
        self._residency_window_generation += 1
        self._lod_timer.stop()
        self._cancel_stale_image_workers()
        self._loading_source = None
        self._loading_started_at = None
        self._present_source = None
        self._present_started_at = None
        self._pending_still = None
        self._pending_layout_intent = None
        self._current_full_image = None
        self._active_asset_id = ""
        self._active_source_identity = None
        self._active_adjustments = {}
        self._current_decode_level = None
        self._request_reason_by_generation.clear()
        self._render_session_interaction_depth.clear()
        self._render_session_lod_pending.clear()
        self._render_session_pending_surfaces.clear()
        for entry in tuple(self._preparation_entries.values()):
            entry.intents.clear()
            entry.worker.cancel()
            if self._preparation_pool.tryTake(entry.worker):
                self._retire_preparation_entry(entry)

    def defer_still_updates(self, enabled: bool) -> None:
        """Control whether still frames should be applied immediately."""
        self._defer_still_updates = bool(enabled)

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
        *,
        reset_view: bool = True,
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
                reset_view=reset_view,
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
        set_still_surface = getattr(self._image_viewer, "set_still_surface", None)
        if callable(set_still_surface):
            set_still_surface(surface, adjustments, reset_view=reset_view)
        else:
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

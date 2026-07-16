"""Coordinator for the stacked player widgets used on the detail page."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

from ....application.ports import EditServicePort
from ....core.color_resolver import compute_color_statistics
from ....gui.detail_pipeline import (
    DetailFrameCache,
    DetailFrameIdentity,
    detail_pipeline_v2_enabled,
)
from ....gui.detail_profile import emit_detail_event, log_detail_profile
from ....gui.i18n import tr
from ....utils import image_loader
from ..widgets.gl_image_viewer import GLImageViewer
from ..widgets.live_badge import LiveBadge
from ..widgets.video_area import VideoArea


class _AdjustedImageSignals(QObject):
    """Relay worker completion events back to the GUI thread."""

    completed = Signal(Path, QImage, dict)
    """Emitted when the adjusted image finished loading successfully."""

    failed = Signal(Path, str)
    """Emitted when loading or processing the image fails."""

    finished = Signal(object)
    """Emitted for success, failure and cooperative cancellation."""


class _AdjustedImageWorker(QRunnable):
    """Load and tone-map an image on a background thread."""

    def __init__(
        self,
        source: Path,
        signals: _AdjustedImageSignals,
        edit_service: EditServicePort | None = None,
        frame_cache: DetailFrameCache | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._source = source
        self._signals = signals
        self._edit_service = edit_service
        self._frame_cache = frame_cache
        self.frame_identity: DetailFrameIdentity | None = None
        self.cache_hit = False
        self._cancelled = False
        self._submitted_at = time.perf_counter()

    def cancel(self) -> None:
        """Suppress delivery when this request is no longer current."""

        self._cancelled = True

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        """Perform the expensive image work outside the GUI thread."""

        started = time.perf_counter()
        log_detail_profile(
            "still_worker",
            "queue_wait",
            (started - self._submitted_at) * 1000.0,
            path=self._source.name,
        )
        try:
            if self._cancelled:
                return
            self.frame_identity = DetailFrameIdentity.from_path(self._source)
            cached = (
                self._frame_cache.get(self.frame_identity)
                if self._frame_cache is not None and self.frame_identity is not None
                else None
            )
            if cached is not None:
                self.cache_hit = True
                image, adjustments = cached
                if not self._cancelled:
                    self._signals.completed.emit(self._source, image, adjustments)
                return
            decode_started = time.perf_counter()
            image = image_loader.load_qimage(self._source, None)
            log_detail_profile(
                "still_worker",
                "decode",
                (time.perf_counter() - decode_started) * 1000.0,
                path=self._source.name,
            )
        except Exception as exc:  # pragma: no cover - Qt loader errors are rare
            if not self._cancelled:
                self._signals.failed.emit(self._source, str(exc))
            return

        if image is None or image.isNull():
            if not self._cancelled:
                self._signals.failed.emit(self._source, "Image decoder returned an empty frame")
            return

        try:
            adjustments_started = time.perf_counter()
            adjustments = {}
            has_sidecar = (
                self._edit_service is not None
                and self._edit_service.sidecar_exists(self._source)
            )
            if has_sidecar and self._edit_service is not None:
                stats = compute_color_statistics(image)
                adjustments = self._edit_service.describe_adjustments(
                    self._source,
                    color_stats=stats,
                ).resolved_adjustments
            log_detail_profile(
                "still_worker",
                "adjustments",
                (time.perf_counter() - adjustments_started) * 1000.0,
                path=self._source.name,
                adjustments=len(adjustments),
            )
        except Exception as exc:  # pragma: no cover - filesystem errors are rare
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

        if not self._cancelled:
            if self._frame_cache is not None and self.frame_identity is not None:
                self._frame_cache.put(self.frame_identity, image, adjustments or {})
            self._signals.completed.emit(self._source, image, adjustments or {})


class _ScheduledAdjustedImageWorker(_AdjustedImageWorker):
    """Production runnable that always reports terminal completion."""

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        try:
            super().run()
        finally:
            self._signals.finished.emit(self)


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
    """Emitted after the requested full-resolution still texture is presented."""

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
        self._placeholder_default_text = placeholder.text() if isinstance(placeholder, QLabel) else None
        self._uses_standard_placeholder = self._placeholder_default_text in {
            "Select a photo or video to preview.",
            tr("DetailPage", "Select a photo or video to preview."),
        }
        self._live_badge = live_badge
        self._edit_service_getter = edit_service_getter
        self._image_viewer_index = player_stack.indexOf(image_viewer)
        self._image_viewer.replayRequested.connect(self.liveReplayRequested)
        self._pool = StillImageDecodeScheduler(self)
        self._frame_cache = DetailFrameCache()
        self._active_workers: set[_AdjustedImageWorker] = set()
        self._prefetch_worker: _AdjustedImageWorker | None = None
        self._prefetch_queue: list[Path] = []
        self._request_generation = 0
        self._present_generation = 0
        self._present_started_at: float | None = None
        self._present_source: Path | None = None
        self._loading_source: Path | None = None
        self._loading_started_at: float | None = None
        self._defer_still_updates = False
        self._pending_still: tuple[Path, QImage, dict] | None = None
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
        request_generation: int | None = None,
    ) -> bool:
        """Begin loading ``source`` asynchronously, returning scheduling success."""
        if request_generation is None:
            self._request_generation += 1
        else:
            self._request_generation = int(request_generation)
        request_generation = self._request_generation
        self._loading_source = source
        self._loading_started_at = time.perf_counter()
        self._cancel_stale_image_workers()

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

        signals = _AdjustedImageSignals()
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = _ScheduledAdjustedImageWorker(
            source,
            signals,
            edit_service=edit_service,
            frame_cache=self._frame_cache,
        )
        self._active_workers.add(worker)

        signals.completed.connect(
            lambda img_source, img, adjustments, generation=request_generation, active=worker:
            self._on_scheduled_image_ready(
                generation, img_source, img, adjustments, active.frame_identity
            )
        )
        signals.failed.connect(
            lambda img_source, message, generation=request_generation:
            self._on_scheduled_image_failed(generation, img_source, message)
        )
        signals.finished.connect(self._on_image_worker_finished)

        try:
            self._pool.start(worker, 1)
        except RuntimeError as exc:  # 线程池满极少见
            self._release_worker(worker)
            self._loading_source = None
            self._loading_started_at = None
            self.imageLoadingFailed.emit(source, str(exc))
            return False
        return True

    def _cancel_stale_image_workers(self) -> None:
        self._prefetch_queue.clear()
        for worker in tuple(self._active_workers):
            worker.cancel()
            if self._pool.tryTake(worker):
                self._release_worker(worker)

    def prefetch_image(self, source: Path) -> bool:
        """Warm exactly one full-source candidate at low priority."""

        return self.prefetch_images([source])

    def prefetch_images(self, sources: list[Path]) -> bool:
        """Warm unique full-source candidates sequentially at low priority."""

        previous = self._prefetch_worker
        if previous is not None:
            previous.cancel()
            if self._pool.tryTake(previous):
                self._release_worker(previous)
        self._prefetch_queue = list(dict.fromkeys(Path(value) for value in sources))
        return self._start_next_prefetch()

    def _start_next_prefetch(self) -> bool:
        if self._prefetch_worker is not None or not self._prefetch_queue:
            return False
        source = self._prefetch_queue.pop(0)
        signals = _AdjustedImageSignals()
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = _ScheduledAdjustedImageWorker(
            source,
            signals,
            edit_service=edit_service,
            frame_cache=self._frame_cache,
        )
        self._prefetch_worker = worker
        self._active_workers.add(worker)
        signals.finished.connect(self._on_image_worker_finished)
        try:
            self._pool.start(worker, -1)
        except RuntimeError:
            self._release_worker(worker)
            return False
        return True

    def _on_scheduled_image_ready(
        self,
        generation: int,
        source: Path,
        image: QImage,
        adjustments: dict,
        frame_identity: DetailFrameIdentity | None = None,
    ) -> None:
        if generation != self._request_generation:
            return
        self._present_generation = generation
        self._present_started_at = self._loading_started_at
        self._present_source = source
        self._on_adjusted_image_ready(
            source,
            image,
            adjustments,
            frame_identity=frame_identity,
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

    def _on_image_worker_finished(self, worker: object) -> None:
        if isinstance(worker, _AdjustedImageWorker):
            was_prefetch = worker is self._prefetch_worker
            self._release_worker(worker)
            signals = getattr(worker, "_signals", None)
            if signals is not None:
                signals.deleteLater()
            if was_prefetch and self._prefetch_queue:
                QTimer.singleShot(0, self._start_next_prefetch)

    def _on_still_frame_presented(self, source: object) -> None:
        presented_path = (
            source.path if isinstance(source, DetailFrameIdentity) else source
        )
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
            "image_presented",
            generation=generation,
            media_type="image",
        )
        self._present_started_at = None
        self._present_source = None
        self.stillFramePresented.emit(presented_path, generation)

    def shutdown(self, *, timeout_ms: int = 1500) -> None:
        """Cancel queued decodes and wait briefly for active full-image reads."""

        self.cancel_pending_image_requests()
        self._pool.clear()
        self._pool.waitForDone(max(0, int(timeout_ms)))
        self._frame_cache.clear()

    def clear_frame_cache(self) -> None:
        """Drop decoded frames after a library change or memory pressure."""

        self._frame_cache.clear()

    def current_full_image(self) -> QImage | None:
        """Return the retained original decode for edit/re-sampling consumers."""

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
        self._current_full_image = None

    def defer_still_updates(self, enabled: bool) -> None:
        """Control whether still frames should be applied immediately."""
        self._defer_still_updates = bool(enabled)
        if not self._defer_still_updates:
            self.apply_pending_still()

    def apply_pending_still(self) -> bool:
        """Apply any deferred still frame if available."""
        if self._pending_still is None:
            return False
        source, image, adjustments = self._pending_still
        self._pending_still = None
        self._apply_still_frame(source, image, adjustments)
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
        image: QImage,
        adjustments: dict,
        *,
        frame_identity: DetailFrameIdentity | None = None,
    ) -> None:
        """Render *image* when the matching worker completes successfully."""
        if self._loading_source != source:
            return

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
            self._pending_still = (source, image, adjustments)
        else:
            self._apply_still_frame(
                source,
                image,
                adjustments,
                reset_view=True,
                frame_identity=frame_identity,
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
        source: Path,
        image: QImage,
        adjustments: dict,
        *,
        reset_view: bool = True,
        frame_identity: DetailFrameIdentity | None = None,
    ) -> None:
        """Render the still image on the GL viewer."""
        apply_started = time.perf_counter()
        self.show_image_surface()
        self._current_full_image = QImage(image)
        display_image = image
        texture_limit_getter = getattr(self._image_viewer, "maximum_texture_size", None)
        texture_limit = int(texture_limit_getter()) if callable(texture_limit_getter) else 8192
        if image.width() > texture_limit or image.height() > texture_limit:
            display_image = image.scaled(
                texture_limit,
                texture_limit,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._image_viewer.set_image(
            display_image,
            adjustments,
            image_source=frame_identity or source,
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

    def _release_worker(self, worker: _AdjustedImageWorker) -> None:
        """Drop completed workers so the thread pool can reclaim resources."""

        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if self._prefetch_worker is worker:
            self._prefetch_worker = None
        worker.setAutoDelete(True)

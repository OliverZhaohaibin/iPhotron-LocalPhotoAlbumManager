"""Coordinator for the stacked player widgets used on the detail page."""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional, Set

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

from ....application.ports import EditServicePort
from ....core.color_resolver import ColorStats, compute_color_statistics
from ....gui.detail_decode_backend import DecodedSurface
from ....gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailRenderTransaction,
    PlaybackAsyncToken,
)
from ....gui.detail_profile import log_detail_profile
from ....gui.detail_render_session import (
    EditRenderState,
    PhotoRenderSessionHandle,
    SurfaceRetentionBudget,
)
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


class _AdjustedImageWorker(QRunnable):
    """Load and tone-map an image on a background thread."""

    def __init__(
        self,
        source: Path,
        signals: _AdjustedImageSignals,
        edit_service: EditServicePort | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._source = source
        self._signals = signals
        self._edit_service = edit_service
        self.color_stats = ColorStats()
        self.source_identity = AssetSourceIdentity.create(source)
        # The worker always decodes the original frame at full fidelity.  The
        # GUI thread performs any downscaling so zooming and full-screen views
        # can leverage every available pixel.

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        """Perform the expensive image work outside the GUI thread."""

        started = time.perf_counter()
        try:
            # Requesting ``None`` as the target size forces ``QImageReader`` to
            # decode the full-resolution frame.  The detail view later scales
            # the resulting pixmap to fit the viewport while maintaining the
            # original aspect ratio, ensuring sharp results without distortion.
            decode_started = time.perf_counter()
            image = image_loader.load_qimage(self._source, None)
            log_detail_profile(
                "still_worker",
                "decode",
                (time.perf_counter() - decode_started) * 1000.0,
                path=self._source.name,
            )
        except Exception as exc:  # pragma: no cover - Qt loader errors are rare
            self._signals.failed.emit(self._source, str(exc))
            return

        if image is None or image.isNull():
            self._signals.failed.emit(self._source, "Image decoder returned an empty frame")
            return

        try:
            adjustments_started = time.perf_counter()
            adjustments = {}
            self.color_stats = compute_color_statistics(image)
            self.source_identity = self.source_identity.repair_revision_from_stat()
            if self._edit_service is not None and self._edit_service.sidecar_exists(self._source):
                adjustments = self._edit_service.describe_adjustments(
                    self._source,
                    color_stats=self.color_stats,
                ).resolved_adjustments
            log_detail_profile(
                "still_worker",
                "adjustments",
                (time.perf_counter() - adjustments_started) * 1000.0,
                path=self._source.name,
                adjustments=len(adjustments),
            )
        except Exception as exc:  # pragma: no cover - filesystem errors are rare
            self._signals.failed.emit(self._source, str(exc))
            return

        # Pass the raw image and adjustments to the main thread. The GL viewer
        # Pass the raw image and adjustments to the main thread. The GL viewer
        # will apply the adjustments on the GPU.
        log_detail_profile(
            "still_worker",
            "total",
            (time.perf_counter() - started) * 1000.0,
            path=self._source.name,
            has_adjustments=bool(adjustments),
        )
        self._signals.completed.emit(self._source, image, adjustments or {})


class PlayerViewController(QObject):
    """Control which player surface is visible and manage related UI state."""

    liveReplayRequested = Signal()
    """Re-emitted when the image viewer asks to replay a Live Photo."""

    imageLoadingFailed = Signal(Path, str)
    """Emitted when a still image fails to load or post-process."""

    stillFramePresented = Signal(Path, int)
    """Emitted when a transaction-owned still has actually rendered."""

    videoFramePresented = Signal(int)
    """Emitted when a transaction-owned video surface renders its first frame."""

    def __init__(
        self,
        player_stack: QStackedWidget,
        image_viewer: GLImageViewer,
        video_area: VideoArea,
        placeholder: QWidget,
        live_badge: LiveBadge,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        surface_budget_bytes: int = 192 * 1024 * 1024,
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
        self._pool = QThreadPool.globalInstance()
        self._active_workers: Set[_AdjustedImageWorker] = set()
        self._loading_source: Optional[Path] = None
        self._loading_started_at: float | None = None
        self._active_transaction: DetailRenderTransaction | None = None
        self._loading_transaction: DetailRenderTransaction | None = None
        self._active_async_token: PlaybackAsyncToken | None = None
        self._current_still_source: Path | None = None
        self._current_still_generation = 0
        self._defer_still_updates = False
        self._pending_still: Optional[
            tuple[
                Path,
                QImage,
                dict,
                DetailRenderTransaction | None,
                PlaybackAsyncToken | None,
                AssetSourceIdentity,
                ColorStats,
            ]
        ] = None
        self._surface_budget = SurfaceRetentionBudget(surface_budget_bytes)
        self._render_sessions: OrderedDict[tuple, PhotoRenderSessionHandle] = OrderedDict()
        self._current_render_session: PhotoRenderSessionHandle | None = None
        self._next_render_session_id = 1

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
        still_presented = getattr(self._image_viewer, "stillFramePresented", None)
        if still_presented is not None:
            still_presented.connect(self._on_still_surface_presented)
        self._video_area.firstFrameReady.connect(self._on_video_first_render)
        video_presented = getattr(self._video_area, "framePresented", None)
        if video_presented is not None:
            video_presented.connect(self._on_video_surface_presented)

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

    def _on_still_surface_presented(self, source: object) -> None:
        if self._current_still_generation <= 0:
            return
        presented_source = Path(source)
        if self._current_still_source != presented_source:
            return
        self.stillFramePresented.emit(
            presented_source,
            self._current_still_generation,
        )

    def _on_video_first_render(self) -> None:
        """Mark video renderer as initialised; hide cover if it is visible."""
        self._video_renderer_rendered = True
        if self._player_stack.currentWidget() is self._video_area:
            self._hide_detail_init_cover()

    def _on_video_surface_presented(self, _source: Path) -> None:
        transaction = self._active_transaction
        if transaction is not None and transaction.media_kind == "live_motion":
            self.videoFramePresented.emit(transaction.generation)

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
        placeholder: Optional[QPixmap] = None,
        transaction: DetailRenderTransaction | None = None,
        async_token: PlaybackAsyncToken | None = None,
    ) -> bool:
        """Begin loading ``source`` asynchronously, returning scheduling success."""
        self._loading_source = source
        self._loading_started_at = time.perf_counter()
        self._active_transaction = transaction
        self._loading_transaction = transaction
        self._active_async_token = async_token

        # 1) 先切到 GL 视图，保证有有效的 GL 上下文
        self.show_image_surface()

        # 2) 若有占位图，先显示；否则仅清空，不上传空图像
        if placeholder is not None and not placeholder.isNull():
            self._image_viewer.set_placeholder(placeholder)
        else:
            self._image_viewer.set_image(None, {})

        signals = _AdjustedImageSignals()
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = _AdjustedImageWorker(source, signals, edit_service=edit_service)
        self._active_workers.add(worker)

        signals.completed.connect(
            lambda source, image, adjustments, candidate=worker, token=async_token: (
                self._on_adjusted_image_ready(
                    source,
                    image,
                    adjustments,
                    async_token=token,
                    source_identity=candidate.source_identity,
                    color_stats=candidate.color_stats,
                )
            )
        )
        signals.failed.connect(
            lambda source, message, token=async_token: self._on_adjusted_image_failed(
                source,
                message,
                async_token=token,
            )
        )

        def _finalize_on_completion(img_source: Path, img: QImage, adjustments: dict) -> None:
            self._release_worker(worker)
            signals.deleteLater()

        def _finalize_on_failure(img_source: Path, message: str) -> None:
            self._release_worker(worker)
            signals.deleteLater()

        signals.completed.connect(_finalize_on_completion)
        signals.failed.connect(_finalize_on_failure)

        try:
            self._pool.start(worker)
        except RuntimeError as exc:  # 线程池满极少见
            self._release_worker(worker)
            self._loading_source = None
            self._loading_started_at = None
            self._loading_transaction = None
            self.imageLoadingFailed.emit(source, str(exc))
            return False
        return True

    def defer_still_updates(self, enabled: bool) -> None:
        """Control whether still frames should be applied immediately."""
        self._defer_still_updates = bool(enabled)
        if not self._defer_still_updates:
            self.apply_pending_still()

    def apply_pending_still(self) -> bool:
        """Apply any deferred still frame if available."""
        if self._pending_still is None:
            return False
        (
            source,
            image,
            adjustments,
            transaction,
            async_token,
            source_identity,
            color_stats,
        ) = self._pending_still
        self._pending_still = None
        surface_budget = getattr(self, "_surface_budget", None)
        if surface_budget is not None:
            surface_budget.release(0)
        self._apply_still_frame(
            source,
            image,
            adjustments,
            transaction=transaction,
            async_token=async_token,
            source_identity=source_identity,
            color_stats=color_stats,
        )
        return True

    def invalidate_async_work(self) -> None:
        """Reject all late worker deliveries and release library-scoped surfaces."""

        self._loading_source = None
        self._loading_started_at = None
        self._loading_transaction = None
        self._active_transaction = None
        self._active_async_token = None
        self._pending_still = None
        self._current_still_source = None
        self._current_still_generation = 0
        for session in self._render_sessions.values():
            session.invalidate()
        self._render_sessions.clear()
        self._current_render_session = None
        self._surface_budget.clear()

    def clear_image(self) -> None:
        """Remove any pixmap currently shown in the image viewer."""
        # 清空而非传空图像，避免一帧“空绘制/空上传”
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
        async_token: PlaybackAsyncToken | None = None,
        source_identity: AssetSourceIdentity | None = None,
        color_stats: ColorStats | None = None,
    ) -> None:
        """Render *image* when the matching worker completes successfully."""
        if self._loading_source != source or (
            async_token is not None and async_token != self._active_async_token
        ):
            return
        identity = source_identity or AssetSourceIdentity.create(source)
        stats = color_stats or ColorStats()

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
                self._loading_transaction = None
            self._image_viewer.set_image(None, {})
            self.imageLoadingFailed.emit(source, "Image decoder returned an empty frame")
            return

        if self._defer_still_updates and self._player_stack.currentWidget() is self._video_area:
            reserve_pending = getattr(self, "_reserve_pending_surface", None)
            if not callable(reserve_pending) or reserve_pending(image):
                self._pending_still = (
                    source,
                    image,
                    adjustments,
                    self._loading_transaction,
                    async_token,
                    identity,
                    stats,
                )
            else:
                self._pending_still = None
        else:
            self._apply_still_frame(
                source,
                image,
                adjustments,
                transaction=self._loading_transaction,
                async_token=async_token,
                source_identity=identity,
                color_stats=stats,
            )

        if self._loading_source == source:
            self._loading_source = None
            self._loading_started_at = None
            self._loading_transaction = None

    def _on_adjusted_image_failed(
        self,
        source: Path,
        message: str,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        """Propagate worker failures while ensuring stale results are ignored."""

        if self._loading_source != source or (
            async_token is not None and async_token != self._active_async_token
        ):
            return

        if self._loading_source == source:
            self._loading_source = None
            self._loading_started_at = None
            self._loading_transaction = None
        self._image_viewer.set_image(None)
        self.imageLoadingFailed.emit(source, message)

    def _apply_still_frame(
        self,
        source: Path,
        image: QImage,
        adjustments: dict,
        *,
        transaction: DetailRenderTransaction | None = None,
        async_token: PlaybackAsyncToken | None = None,
        source_identity: AssetSourceIdentity | None = None,
        color_stats: ColorStats | None = None,
    ) -> None:
        """Render the still image on the GL viewer."""
        apply_started = time.perf_counter()
        self._active_transaction = transaction
        self._current_still_source = source
        self._current_still_generation = (
            transaction.generation if transaction is not None else 0
        )
        identity = (
            transaction.source_identity
            if transaction is not None
            else source_identity or AssetSourceIdentity.create(source)
        )
        self._retain_render_session(
            source=source,
            image=image,
            adjustments=adjustments,
            identity=identity,
            color_stats=color_stats or ColorStats(),
            asset_id=(
                transaction.asset_id
                if transaction is not None
                else async_token.asset_id if async_token is not None else source.name
            ),
        )
        self.show_image_surface()
        self._image_viewer.set_image(
            image,
            adjustments,
            image_source=source,
            reset_view=True,
        )
        self._image_viewer.update()
        log_detail_profile(
            "player_view",
            "still.apply_frame",
            (time.perf_counter() - apply_started) * 1000.0,
            path=source.name,
            has_adjustments=bool(adjustments),
        )

    @property
    def retained_surface_bytes(self) -> int:
        return self._surface_budget.retained_bytes

    @property
    def surface_budget_bytes(self) -> int:
        return self._surface_budget.max_bytes

    def acquire_render_session(self, source: Path) -> PhotoRenderSessionHandle | None:
        session = self._current_render_session
        normalized = Path(source).expanduser().absolute()
        if session is None or not session.valid or session.source != normalized:
            return None
        if session.current_surface is None:
            return None
        session.edit_references += 1
        self._touch_render_session(session)
        return session

    def update_render_session(
        self,
        handle: PhotoRenderSessionHandle,
        adjustments: dict,
    ) -> EditRenderState | None:
        if handle is not self._current_render_session or not handle.valid:
            return None
        state = handle.next_state(adjustments)
        if state is not None:
            self._image_viewer.set_adjustments(dict(state.shader_adjustments))
        return state

    def finish_render_session(
        self,
        handle: PhotoRenderSessionHandle,
        *,
        committed: bool,
    ) -> bool:
        if handle is not self._current_render_session or not handle.valid:
            return False
        handle.edit_references = max(0, handle.edit_references - 1)
        if committed:
            state = handle.next_state(handle.edit_state.raw_adjustments, kind="commit")
            if state is not None:
                handle.baseline_state = state
        else:
            handle.restore_baseline()
        return True

    def render_session_sidebar_input(
        self,
        handle: PhotoRenderSessionHandle,
    ) -> tuple[QImage, ColorStats] | None:
        if handle is not self._current_render_session or not handle.valid:
            return None
        surface = handle.current_surface
        if surface is None:
            return None
        return QImage(surface.image), handle.edit_state.color_stats

    def _retain_render_session(
        self,
        *,
        source: Path,
        image: QImage,
        adjustments: dict,
        identity: AssetSourceIdentity,
        color_stats: ColorStats,
        asset_id: str,
    ) -> PhotoRenderSessionHandle | None:
        if not identity.has_stable_revision:
            return None
        decode_key = DetailDecodeKey(
            asset_id=str(asset_id).strip() or source.name,
            source=identity.path,
            source_revision=identity.revision,
            orientation=identity.orientation,
            decode_level="full",
        )
        surface = DecodedSurface(
            image=QImage(image),
            decode_key=decode_key,
            source_size=(image.width(), image.height()),
            decoded_size=(image.width(), image.height()),
            decode_level="full",
            backend="legacy-player-worker",
            color_stats=color_stats,
        )
        session_key = (
            decode_key.asset_id,
            decode_key.source,
            decode_key.source_revision,
            decode_key.orientation,
        )
        session = self._render_sessions.get(session_key)
        if session is None:
            state = EditRenderState.create(
                adjustments,
                color_stats=color_stats,
                revision=("index", identity.index_revision),
            )
            session = PhotoRenderSessionHandle(
                session_id=self._next_render_session_id,
                asset_id=decode_key.asset_id,
                source_identity=identity,
                current_surface=surface,
                edit_state=state,
                baseline_state=state,
            )
            self._next_render_session_id += 1
        required = SurfaceRetentionBudget.surface_bytes(surface)
        if required > self._surface_budget.max_bytes:
            if session_key not in self._render_sessions:
                session.invalidate()
            return None
        while not self._surface_budget.can_replace(session.session_id, required):
            evicted = self._evict_oldest_render_session(excluding=session)
            if not evicted:
                if session_key not in self._render_sessions:
                    session.invalidate()
                return None
        if session.current_surface is not surface and not session.replace_surface(surface):
            return None
        if (
            session.edit_references == 0
            and dict(session.baseline_state.raw_adjustments) != dict(adjustments)
        ):
            state = EditRenderState.create(
                adjustments,
                color_stats=color_stats,
                revision=("index", identity.index_revision),
            )
            session.edit_state = state
            session.baseline_state = state
        if not self._surface_budget.replace(session.session_id, required):
            return None

        self._render_sessions.pop(session_key, None)
        self._render_sessions[session_key] = session
        self._current_render_session = session
        while len(self._render_sessions) > 3:
            if not self._evict_oldest_render_session(excluding=session):
                break
        return session

    def _reserve_pending_surface(self, image: QImage) -> bool:
        """Reserve one deferred still without escaping the unified budget."""

        required = max(0, int(image.sizeInBytes()))
        if required > self._surface_budget.max_bytes:
            self._surface_budget.release(0)
            return False
        while not self._surface_budget.can_replace(0, required):
            current = self._current_render_session
            if current is None or current.edit_references > 0:
                self._surface_budget.release(0)
                return False
            if not self._evict_oldest_render_session(excluding=current):
                # The current Detail surface is no longer visible while Live
                # motion plays, so it is the final safe eviction candidate.
                for key, candidate in tuple(self._render_sessions.items()):
                    if candidate is not current:
                        continue
                    self._render_sessions.pop(key, None)
                    self._surface_budget.release(candidate.session_id)
                    candidate.invalidate()
                    self._current_render_session = None
                    break
                else:
                    return False
        return self._surface_budget.replace(0, required)

    def _evict_oldest_render_session(
        self,
        *,
        excluding: PhotoRenderSessionHandle,
    ) -> bool:
        for key, candidate in tuple(self._render_sessions.items()):
            if candidate is excluding or candidate.edit_references > 0:
                continue
            self._render_sessions.pop(key, None)
            self._surface_budget.release(candidate.session_id)
            candidate.invalidate()
            if candidate is self._current_render_session:
                self._current_render_session = None
            return True
        return False

    def _touch_render_session(self, session: PhotoRenderSessionHandle) -> None:
        for key, candidate in tuple(self._render_sessions.items()):
            if candidate is session:
                self._render_sessions.move_to_end(key)
                return

    def _release_worker(self, worker: _AdjustedImageWorker) -> None:
        """Drop completed workers so the thread pool can reclaim resources."""

        if worker in self._active_workers:
            self._active_workers.remove(worker)
        worker.setAutoDelete(True)

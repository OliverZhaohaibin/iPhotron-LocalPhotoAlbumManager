"""Windows-only visual hold used while the main window enters fullscreen."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    Property,
    QPropertyAnimation,
    QRect,
    Signal,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class _WindowsFullscreenPresentationHold(QWidget):
    """Opaque top-level snapshot surface independent of the main HWND."""

    presented = Signal(int)
    zoomFinished = Signal(int)
    handoffFinished = Signal(int)

    def __init__(
        self,
        *,
        transition_id: int,
        settle_ms: int,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self._transition_id = int(transition_id)
        self._settle_ms = max(0, int(settle_ms))
        self._snapshot = QPixmap()
        self._snapshot_rect = QRect()
        self._fallback_color = QColor("#000000")
        self._first_paint_seen = False
        self._presented_emitted = False
        self._cancelled = False
        self._zoom_finished = False
        self._handoff_finished = False
        self._zoom_animation: QPropertyAnimation | None = None
        self._handoff_animation: QPropertyAnimation | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def transition_id(self) -> int:
        return self._transition_id

    @property
    def zoom_finished(self) -> bool:
        return self._zoom_finished

    def present(
        self,
        *,
        screen_geometry: QRect,
        source_rect_global: QRect,
        snapshot: QPixmap | None,
        fallback_color: QColor,
    ) -> None:
        """Show an opaque screen-sized hold with the snapshot at its source rect."""

        if screen_geometry.isEmpty() or source_rect_global.isEmpty():
            raise ValueError("fullscreen hold geometry must be non-empty")
        self._cancelled = False
        self._first_paint_seen = False
        self._presented_emitted = False
        self._zoom_finished = False
        self._handoff_finished = False
        self._fallback_color = QColor(fallback_color)
        self._fallback_color.setAlpha(255)
        self._snapshot = QPixmap(snapshot) if snapshot is not None else QPixmap()
        local_rect = QRect(source_rect_global)
        local_rect.translate(-screen_geometry.x(), -screen_geometry.y())
        self._snapshot_rect = local_rect
        self.setWindowOpacity(1.0)
        self.setGeometry(screen_geometry)
        self.show()
        self.raise_()
        self.update()

    def snapshot_rect(self) -> QRect:
        return QRect(self._snapshot_rect)

    def set_snapshot_rect(self, rect: QRect) -> None:
        target = QRect(rect)
        if target == self._snapshot_rect:
            return
        self._snapshot_rect = target
        self.update()

    snapshotRect = Property(  # noqa: N815 - Qt property naming convention
        QRect,
        snapshot_rect,
        set_snapshot_rect,
    )

    def animate_to(self, target_rect_global: QRect, *, duration_ms: int) -> None:
        target = self._to_local_rect(target_rect_global)
        self._stop_animation("_zoom_animation")
        self._zoom_finished = False
        if duration_ms <= 0 or target == self._snapshot_rect:
            self.set_snapshot_rect(target)
            self._finish_zoom()
            return
        animation = QPropertyAnimation(self, b"snapshotRect", self)
        animation.setDuration(max(1, int(duration_ms)))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(self.snapshot_rect())
        animation.setEndValue(target)
        animation.finished.connect(self._finish_zoom)
        self._zoom_animation = animation
        animation.start()

    def retarget(self, target_rect_global: QRect) -> None:
        """Correct the animation endpoint after native fullscreen layout settles."""

        target = self._to_local_rect(target_rect_global)
        animation = self._zoom_animation
        if animation is None:
            self.set_snapshot_rect(target)
            return
        remaining_ms = max(1, animation.duration() - animation.currentTime())
        self._stop_animation("_zoom_animation")
        replacement = QPropertyAnimation(self, b"snapshotRect", self)
        replacement.setDuration(remaining_ms)
        replacement.setEasingCurve(QEasingCurve.Type.OutCubic)
        replacement.setStartValue(self.snapshot_rect())
        replacement.setEndValue(target)
        replacement.finished.connect(self._finish_zoom)
        self._zoom_animation = replacement
        replacement.start()

    def finish_handoff(self, *, duration_ms: int) -> None:
        """Fade the independent hold only after the main media surface is stable."""

        if self._cancelled or self._handoff_finished:
            return
        self._stop_animation("_handoff_animation")
        if duration_ms <= 0:
            self.setWindowOpacity(0.0)
            self._finish_handoff()
            return
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(max(1, int(duration_ms)))
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.finished.connect(self._finish_handoff)
        self._handoff_animation = animation
        animation.start()

    def cancel(self) -> None:
        """Stop all callbacks and synchronously remove the native hold window."""

        if self._cancelled:
            return
        self._cancelled = True
        self._stop_animation("_zoom_animation")
        self._stop_animation("_handoff_animation")
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if not self._snapshot_rect.isEmpty():
            if self._snapshot.isNull():
                painter.fillRect(self._snapshot_rect, self._fallback_color)
            else:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(
                    self._snapshot_rect,
                    self._snapshot,
                    self._snapshot.rect(),
                )
        painter.end()
        if not self._first_paint_seen:
            self._first_paint_seen = True
            QTimer.singleShot(self._settle_ms, self._emit_presented)

    def _to_local_rect(self, global_rect: QRect) -> QRect:
        target = QRect(global_rect)
        target.translate(-self.geometry().x(), -self.geometry().y())
        return target

    def _emit_presented(self) -> None:
        if self._cancelled or self._presented_emitted or not self.isVisible():
            return
        self._presented_emitted = True
        self.presented.emit(self._transition_id)

    def _finish_zoom(self) -> None:
        if self._cancelled or self._zoom_finished:
            return
        self._zoom_finished = True
        self._stop_animation("_zoom_animation")
        self.zoomFinished.emit(self._transition_id)

    def _finish_handoff(self) -> None:
        if self._cancelled or self._handoff_finished:
            return
        self._handoff_finished = True
        self._stop_animation("_handoff_animation")
        self.hide()
        self.handoffFinished.emit(self._transition_id)

    def _stop_animation(self, attribute: str) -> None:
        animation = getattr(self, attribute)
        setattr(self, attribute, None)
        if animation is None:
            return
        try:
            animation.stop()
            animation.deleteLater()
        except RuntimeError:
            pass


__all__ = ["_WindowsFullscreenPresentationHold"]

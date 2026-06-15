"""Low-latency wheel handling and viewport state for the Gallery grid."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from iPhoto.gui.gallery_demand import (
    FAST_SCROLL_SCREENS_PER_SECOND,
    SCROLL_BURST_INTERVAL_MS,
    SCROLL_DIRECTION_RETENTION_MS,
    SCROLL_DIRECTIONAL_DWELL_MS,
    SCROLL_SETTLED_TIMEOUT_MS,
    SCROLL_VELOCITY_EWMA_SECONDS,
    GalleryViewportDemand,
    build_viewport_demand,
    resolve_display_thumbnail_bucket,
)
from iPhoto.infrastructure.services.performance_events import emit_perf_event


class GalleryScrollController(QObject):
    """Keep wheel input responsive and publish at most one viewport state per event-loop turn."""

    def __init__(self, view, publish: Callable[[], None]) -> None:
        super().__init__(view)
        self._view = view
        self._publish = publish
        self._pending_pixel_delta = 0.0
        self._generation = 0
        self._last_value = int(self._view.verticalScrollBar().value())
        self._last_value_at = time.monotonic()
        self._direction = 0
        self._screens_per_second = 0.0
        self._input_kind = "none"
        self._intent = "idle"
        self._last_input_at = 0.0
        self._angle_intervals_ms: deque[float] = deque(maxlen=4)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(0)
        self._apply_timer.timeout.connect(self._apply_pending_scroll)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(SCROLL_SETTLED_TIMEOUT_MS)
        self._idle_timer.timeout.connect(self._publish_idle_state)

        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._publish_directional_dwell)

        self._direction_expiry_timer = QTimer(self)
        self._direction_expiry_timer.setSingleShot(True)
        self._direction_expiry_timer.setInterval(SCROLL_DIRECTION_RETENTION_MS)
        self._direction_expiry_timer.timeout.connect(self._publish_expired_direction)

        self._view.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)

    def handle_wheel(self, event) -> bool:
        """Accumulate one wheel event without introducing inertial drag."""

        now = time.monotonic()
        pixel_delta = event.pixelDelta()
        pixel_y = pixel_delta.y() if not pixel_delta.isNull() else 0
        if pixel_y:
            # Trackpads already provide a precise physical-pixel stream.
            delta = -float(pixel_y)
            self._input_kind = "pixel"
            self._intent = (
                "continuous_burst"
                if self._screens_per_second >= 2.0
                else "slow_continuous"
            )
            dwell_delay = SCROLL_SETTLED_TIMEOUT_MS
        else:
            angle_delta = event.angleDelta()
            angle = angle_delta.y() or angle_delta.x()
            if not angle:
                return False

            steps = float(angle) / 120.0
            wheel_lines = max(0, QApplication.wheelScrollLines())
            row_height = max(1, self._view.gridSize().height() or self._view.iconSize().height())
            delta = -steps * row_height * wheel_lines
            self._input_kind = "angle"
            interval_ms = (
                (now - self._last_input_at) * 1000.0
                if self._last_input_at > 0.0
                else None
            )
            if interval_ms is not None:
                self._angle_intervals_ms.append(interval_ms)
            self._intent = (
                "continuous_burst"
                if (
                    self._screens_per_second >= FAST_SCROLL_SCREENS_PER_SECOND
                    or (
                        interval_ms is not None
                        and interval_ms <= SCROLL_BURST_INTERVAL_MS
                    )
                )
                else "slow_continuous"
            )
            dwell_delay = (
                SCROLL_SETTLED_TIMEOUT_MS
                if self._intent == "continuous_burst"
                else SCROLL_DIRECTIONAL_DWELL_MS
            )

        self._last_input_at = now
        self._dwell_timer.start(dwell_delay)
        self._direction_expiry_timer.start()
        self._pending_pixel_delta += delta
        if not self._apply_timer.isActive():
            self._apply_timer.start()
        event.accept()
        return True

    def schedule_publish(self) -> None:
        self._publish()

    def viewport_state(self, row_count: int) -> GalleryViewportDemand | None:
        if row_count <= 0:
            return None
        cell_height = max(1, self._view.gridSize().height() or self._view.iconSize().height())
        cell_width = max(1, self._view.gridSize().width() or self._view.iconSize().width())
        viewport = self._view.viewport()
        columns = max(1, viewport.width() // cell_width)
        scroll_y = max(0, self._view.verticalScrollBar().value())
        first_grid_row = scroll_y // cell_height
        visible_grid_rows = max(1, math.ceil(viewport.height() / cell_height) + 1)
        first = min(row_count - 1, first_grid_row * columns)
        last = min(row_count - 1, (first_grid_row + visible_grid_rows) * columns - 1)
        dpr = max(1.0, float(viewport.devicePixelRatioF()))
        display_bucket = resolve_display_thumbnail_bucket(
            max(1, self._view.iconSize().width()) * dpr
        )
        predicted_interval = (
            sum(self._angle_intervals_ms) / len(self._angle_intervals_ms)
            if self._angle_intervals_ms
            else None
        )
        self._generation += 1
        demand = build_viewport_demand(
            generation=self._generation,
            row_count=row_count,
            visible_first=first,
            visible_last=max(first, last),
            direction=self._direction,
            screens_per_second=self._screens_per_second,
            actively_scrolling=self._intent in {"slow_continuous", "continuous_burst"},
            intent=self._intent,
            prefetch_direction=self._direction if self._intent != "idle" else 0,
            predicted_input_interval_ms=predicted_interval,
            display_bucket=display_bucket,
        )
        emit_perf_event(
            "gallery_scroll_intent",
            generation=demand.generation,
            input_kind=self._input_kind,
            intent=demand.intent,
            phase=demand.phase,
            direction=demand.prefetch_direction,
            predicted_input_interval_ms=predicted_interval,
            display_bucket=display_bucket,
        )
        return demand

    def _apply_pending_scroll(self) -> None:
        delta = self._pending_pixel_delta
        self._pending_pixel_delta = 0.0
        if not delta:
            return
        scrollbar = self._view.verticalScrollBar()
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), scrollbar.value() + round(delta)),
        )
        scrollbar.setValue(target)
        self._idle_timer.start()
        self.schedule_publish()

    def _on_scroll_value_changed(self, value: int) -> None:
        now = time.monotonic()
        elapsed = max(1e-6, now - self._last_value_at)
        distance = int(value) - self._last_value
        self._direction = 1 if distance > 0 else (-1 if distance < 0 else self._direction)
        viewport_height = max(1, self._view.viewport().height())
        instantaneous = abs(float(distance)) / elapsed / viewport_height
        alpha = 1.0 - math.exp(-elapsed / SCROLL_VELOCITY_EWMA_SECONDS)
        self._screens_per_second += alpha * (instantaneous - self._screens_per_second)
        recent_wheel_input = now - self._last_input_at <= 0.05
        if self._input_kind == "pixel" and instantaneous >= 2.0:
            self._intent = "continuous_burst"
        elif not recent_wheel_input:
            self._input_kind = "scrollbar"
            self._intent = (
                "continuous_burst" if instantaneous >= 2.0 else "slow_continuous"
            )
            self._last_input_at = now
            self._dwell_timer.start(SCROLL_SETTLED_TIMEOUT_MS)
            self._direction_expiry_timer.start()
        self._last_value = int(value)
        self._last_value_at = now
        self._idle_timer.start()
        self.schedule_publish()

    def _publish_idle_state(self) -> None:
        self._screens_per_second = 0.0
        if self._intent == "continuous_burst":
            self._intent = "directional_dwell"
        self.schedule_publish()

    def _publish_directional_dwell(self) -> None:
        if self._intent != "continuous_burst":
            self._intent = "directional_dwell"
            self._screens_per_second = 0.0
            self.schedule_publish()

    def _publish_expired_direction(self) -> None:
        self._intent = "idle"
        self._screens_per_second = 0.0
        self.schedule_publish()


__all__ = ["GalleryScrollController", "GalleryViewportDemand"]

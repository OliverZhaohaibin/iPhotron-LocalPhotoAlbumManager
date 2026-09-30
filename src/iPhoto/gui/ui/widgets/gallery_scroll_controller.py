"""Low-latency wheel handling and viewport state for the Gallery grid."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from iPhoto.gui.gallery_demand import (
    FAST_SCROLL_SCREENS_PER_SECOND,
    SCROLL_BURST_INTERVAL_MS,
    SCROLL_DIRECTION_RETENTION_MS,
    SCROLL_DIRECTIONAL_DWELL_MS,
    SCROLL_SETTLED_TIMEOUT_MS,
    SCROLL_VELOCITY_EWMA_SECONDS,
    AssetViewportDemand,
    build_viewport_demand,
    resolve_surface_thumbnail_bucket,
)
from iPhoto.infrastructure.services.performance_events import emit_perf_event


class AssetScrollController(QObject):
    """Track scroll intent and publish one surface demand per event-loop turn."""

    def __init__(
        self,
        view,
        publish: Callable[[], None],
        *,
        surface_id: str,
        axis: Literal["horizontal", "vertical"],
    ) -> None:
        super().__init__(view)
        self._view = view
        self._publish = publish
        self._surface_id = str(surface_id)
        self._axis = axis
        self._suspended = not bool(self._view.isVisible())
        self._scrollbar = (
            self._view.horizontalScrollBar()
            if axis == "horizontal"
            else self._view.verticalScrollBar()
        )
        self._pending_pixel_delta = 0.0
        self._generation = 0
        self._last_value = int(self._scrollbar.value())
        self._last_value_at = time.monotonic()
        self._direction = 0
        self._screens_per_second = 0.0
        self._input_kind = "none"
        self._intent = "idle"
        self._last_input_at = 0.0
        self._last_demand: AssetViewportDemand | None = None
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

        self._scrollbar.valueChanged.connect(self._on_scroll_value_changed)

    def handle_wheel(self, event) -> bool:
        """Accumulate one wheel event without introducing inertial drag."""

        if self._suspended:
            return False
        now = time.monotonic()
        pixel_delta = event.pixelDelta()
        pixel_y = pixel_delta.y() if not pixel_delta.isNull() else 0
        if pixel_y:
            # Trackpads already provide a precise physical-pixel stream.
            delta = -float(pixel_y)
            self._input_kind = "pixel"
            next_intent = (
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
            next_intent = (
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
                if next_intent == "continuous_burst"
                else SCROLL_DIRECTIONAL_DWELL_MS
            )

        self._set_intent(next_intent)
        self._last_input_at = now
        self._dwell_timer.start(dwell_delay)
        self._direction_expiry_timer.start()
        self._pending_pixel_delta += delta
        if not self._apply_timer.isActive():
            self._apply_timer.start()
        event.accept()
        return True

    def schedule_publish(self) -> None:
        if not self._suspended:
            self._publish()

    def suspend(self) -> None:
        """Stop delayed publications while the owning surface is hidden."""

        self._suspended = True
        self._pending_pixel_delta = 0.0
        for timer in (
            self._apply_timer,
            self._idle_timer,
            self._dwell_timer,
            self._direction_expiry_timer,
        ):
            timer.stop()

    def resume(self) -> None:
        """Resume observation from the scrollbar's current position."""

        self._suspended = False
        self._last_value = int(self._scrollbar.value())
        self._last_value_at = time.monotonic()
        self._screens_per_second = 0.0
        self._input_kind = "none"
        self._intent = "idle"
        self._clear_angle_cadence()

    def viewport_state(self, row_count: int) -> AssetViewportDemand | None:
        if self._suspended or row_count <= 0:
            return None
        resolved = self._resolve_visible_range(row_count)
        if resolved is None:
            return None
        first, last = resolved
        demand_row_count = self._demand_row_count(row_count)
        if demand_row_count <= 0:
            return None
        viewport = self._view.viewport()
        dpr = max(1.0, float(viewport.devicePixelRatioF()))
        display_bucket = resolve_surface_thumbnail_bucket(
            self._surface_id,
            self._display_edge() * dpr
        )
        predicted_interval = (
            sum(self._angle_intervals_ms) / len(self._angle_intervals_ms)
            if self._angle_intervals_ms
            else None
        )
        demand = build_viewport_demand(
            surface_id=self._surface_id,
            generation=self._generation + 1,
            row_count=demand_row_count,
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
        previous = self._last_demand
        if previous is not None and previous.scheduling_identity == demand.scheduling_identity:
            return previous
        self._generation += 1
        self._last_demand = demand
        emit_perf_event(
            "gallery_scroll_intent",
            surface_id=self._surface_id,
            generation=demand.generation,
            input_kind=self._input_kind,
            intent=demand.intent,
            phase=demand.phase,
            direction=demand.prefetch_direction,
            predicted_input_interval_ms=predicted_interval,
            display_bucket=display_bucket,
        )
        return demand

    def _demand_row_count(self, proxy_row_count: int) -> int:
        return max(0, int(proxy_row_count))

    def _resolve_visible_range(self, row_count: int) -> tuple[int, int] | None:
        """Return source-model rows visible on this controller's surface."""

        cell_height = max(1, self._view.gridSize().height() or self._view.iconSize().height())
        cell_width = max(1, self._view.gridSize().width() or self._view.iconSize().width())
        viewport = self._view.viewport()
        columns = max(1, viewport.width() // cell_width)
        scroll_y = max(0, self._view.verticalScrollBar().value())
        first_grid_row = scroll_y // cell_height
        visible_grid_rows = max(1, math.ceil(viewport.height() / cell_height) + 1)
        first = min(row_count - 1, first_grid_row * columns)
        last = min(row_count - 1, (first_grid_row + visible_grid_rows) * columns - 1)
        return first, max(first, last)

    def _display_edge(self) -> int:
        return max(1, int(self._view.iconSize().width()))

    def _apply_pending_scroll(self) -> None:
        delta = self._pending_pixel_delta
        self._pending_pixel_delta = 0.0
        if not delta:
            return
        scrollbar = self._scrollbar
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), scrollbar.value() + round(delta)),
        )
        scrollbar.setValue(target)
        self._idle_timer.start()
        self.schedule_publish()

    def _on_scroll_value_changed(self, value: int) -> None:
        if self._suspended:
            self._last_value = int(value)
            self._last_value_at = time.monotonic()
            return
        now = time.monotonic()
        elapsed = max(1e-6, now - self._last_value_at)
        distance = int(value) - self._last_value
        self._direction = 1 if distance > 0 else (-1 if distance < 0 else self._direction)
        viewport_extent = max(
            1,
            self._view.viewport().width()
            if self._axis == "horizontal"
            else self._view.viewport().height(),
        )
        instantaneous = abs(float(distance)) / elapsed / viewport_extent
        alpha = 1.0 - math.exp(-elapsed / SCROLL_VELOCITY_EWMA_SECONDS)
        self._screens_per_second += alpha * (instantaneous - self._screens_per_second)
        recent_wheel_input = now - self._last_input_at <= 0.05
        if self._input_kind == "pixel" and instantaneous >= 2.0:
            self._set_intent("continuous_burst")
        elif not recent_wheel_input:
            self._input_kind = "scrollbar"
            next_intent = "continuous_burst" if instantaneous >= 2.0 else "slow_continuous"
            self._set_intent(next_intent)
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
            self._set_intent("directional_dwell")
        self._clear_angle_cadence()
        self.schedule_publish()

    def _publish_directional_dwell(self) -> None:
        if self._intent != "continuous_burst":
            self._set_intent("directional_dwell")
            self._screens_per_second = 0.0
            self._clear_angle_cadence()
            self.schedule_publish()

    def _publish_expired_direction(self) -> None:
        self._set_intent("idle")
        self._screens_per_second = 0.0
        self._clear_angle_cadence()
        self.schedule_publish()

    def _set_intent(self, intent: str) -> None:
        self._intent = intent

    def _clear_angle_cadence(self) -> None:
        self._angle_intervals_ms.clear()


class GalleryScrollController(AssetScrollController):
    """Vertical Gallery controller with low-latency wheel accumulation."""

    def __init__(self, view, publish: Callable[[], None]) -> None:
        super().__init__(view, publish, surface_id="gallery", axis="vertical")


__all__ = ["AssetScrollController", "AssetViewportDemand", "GalleryScrollController"]

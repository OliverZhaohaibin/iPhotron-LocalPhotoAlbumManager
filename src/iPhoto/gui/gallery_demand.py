"""Shared Gallery viewport-demand policy and scheduling limits."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

GalleryScrollPhase = Literal["settled", "slow", "medium", "fast"]
GalleryScrollIntent = Literal[
    "slow_continuous",
    "directional_dwell",
    "continuous_burst",
    "idle",
]

MICRO_WARM_LIMIT = 2000
MICRO_QUERY_CHUNK = 256
MICRO_MIN_WARM_ITEMS = 300
MICRO_SLOW_SCREENS = 6
MICRO_MEDIUM_SCREENS = 24
FULL_PREFETCH_SLOW_AHEAD_SCREENS = 8
FULL_PREFETCH_SLOW_BEHIND_SCREENS = 3
FULL_PREFETCH_DWELL_AHEAD_SCREENS = 10
FULL_PREFETCH_DWELL_BEHIND_SCREENS = 4
FULL_PREFETCH_IDLE_SCREENS = 6
FULL_PREFETCH_MEDIUM_AHEAD_SCREENS = 1

SLOW_SCROLL_SCREENS_PER_SECOND = 2.0
FAST_SCROLL_SCREENS_PER_SECOND = 8.0
SCROLL_SETTLED_TIMEOUT_MS = 120
SCROLL_VELOCITY_EWMA_SECONDS = 0.12
SCROLL_DIRECTIONAL_DWELL_MS = 75
SCROLL_DIRECTION_RETENTION_MS = 600
SCROLL_BURST_INTERVAL_MS = 75
DISPLAY_THUMBNAIL_BUCKETS = (256, 384, 512)


@dataclass(frozen=True, slots=True)
class GalleryViewportDemand:
    """One immutable description of visible, full-prefetch, and micro-warm demand."""

    generation: int
    visible_first: int
    visible_last: int
    direction: int
    screens_per_second: float
    phase: GalleryScrollPhase
    intent: GalleryScrollIntent
    prefetch_direction: int
    predicted_input_interval_ms: float | None
    display_bucket: int
    full_prefetch_first: int
    full_prefetch_last: int
    warm_first: int
    warm_last: int

    @property
    def actively_scrolling(self) -> bool:
        """Compatibility helper for callers that still distinguish active input."""

        return self.phase != "settled"

    @property
    def visible_range(self) -> tuple[int, int]:
        return self.visible_first, self.visible_last

    @property
    def full_prefetch_range(self) -> tuple[int, int]:
        return self.full_prefetch_first, self.full_prefetch_last

    @property
    def warm_range(self) -> tuple[int, int]:
        return self.warm_first, self.warm_last

    def iter_full_prefetch_rows(self) -> Iterator[int]:
        """Yield nearby full-thumbnail rows, favoring the active scroll direction."""

        before = range(self.visible_first - 1, self.full_prefetch_first - 1, -1)
        after = range(self.visible_last + 1, self.full_prefetch_last + 1)
        if self.prefetch_direction:
            ahead = after if self.prefetch_direction > 0 else before
            behind = before if self.prefetch_direction > 0 else after
            if self.intent == "directional_dwell":
                ahead_iter = iter(ahead)
                for _ in range(self.visible_last - self.visible_first + 1):
                    try:
                        yield next(ahead_iter)
                    except StopIteration:
                        break
                yield from _interleave_iterators(ahead_iter, iter(behind), primary_count=3)
                return
            yield from _interleave_ranges(ahead, behind, primary_count=3)
            return
        yield from _interleave_ranges(before, after, primary_count=1)


def _interleave_ranges(
    primary: range,
    secondary: range,
    *,
    primary_count: int,
) -> Iterator[int]:
    yield from _interleave_iterators(iter(primary), iter(secondary), primary_count=primary_count)


def _interleave_iterators(
    primary_iter: Iterator[int],
    secondary_iter: Iterator[int],
    *,
    primary_count: int,
) -> Iterator[int]:
    while True:
        emitted = False
        for _ in range(max(1, primary_count)):
            try:
                yield next(primary_iter)
                emitted = True
            except StopIteration:
                break
        try:
            yield next(secondary_iter)
            emitted = True
        except StopIteration:
            pass
        if not emitted:
            return


def classify_scroll_phase(
    screens_per_second: float,
    *,
    actively_scrolling: bool,
) -> GalleryScrollPhase:
    if not actively_scrolling:
        return "settled"
    speed = max(0.0, float(screens_per_second))
    if speed < SLOW_SCROLL_SCREENS_PER_SECOND:
        return "slow"
    if speed < FAST_SCROLL_SCREENS_PER_SECOND:
        return "medium"
    return "fast"


def build_viewport_demand(
    *,
    generation: int,
    row_count: int,
    visible_first: int,
    visible_last: int,
    direction: int,
    screens_per_second: float,
    actively_scrolling: bool,
    intent: GalleryScrollIntent | None = None,
    prefetch_direction: int | None = None,
    predicted_input_interval_ms: float | None = None,
    display_bucket: int = 512,
) -> GalleryViewportDemand:
    """Build bounded visible, full-prefetch, and micro-warm ranges."""

    row_count = max(1, int(row_count))
    first = max(0, min(int(visible_first), row_count - 1))
    last = max(first, min(int(visible_last), row_count - 1))
    direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
    phase = classify_scroll_phase(screens_per_second, actively_scrolling=actively_scrolling)
    if intent is None:
        intent = (
            "continuous_burst"
            if phase in {"medium", "fast"}
            else "slow_continuous"
            if phase == "slow"
            else "idle"
        )
    if intent == "slow_continuous":
        phase = "slow"
    elif intent in {"directional_dwell", "idle"}:
        phase = "settled"
    visible_count = max(1, last - first + 1)

    before_screens, after_screens = _full_prefetch_screens(
        phase=phase,
        intent=intent,
        direction=direction,
        predicted_input_interval_ms=predicted_input_interval_ms,
    )
    full_prefetch_first, full_prefetch_last = _bounded_range(
        row_count,
        first - visible_count * before_screens,
        last + visible_count * after_screens,
    )

    if phase == "fast":
        warm_target = MICRO_WARM_LIMIT
    elif phase == "medium":
        warm_target = max(MICRO_MIN_WARM_ITEMS, visible_count * MICRO_MEDIUM_SCREENS)
    else:
        warm_target = max(MICRO_MIN_WARM_ITEMS, visible_count * MICRO_SLOW_SCREENS)
    warm_target = min(MICRO_WARM_LIMIT, row_count, warm_target)
    warm_first, warm_last = _warm_window(
        row_count=row_count,
        first=first,
        last=last,
        target=warm_target,
        direction=direction if intent != "idle" else 0,
    )

    return GalleryViewportDemand(
        generation=int(generation),
        visible_first=first,
        visible_last=last,
        direction=direction,
        screens_per_second=max(0.0, float(screens_per_second)),
        phase=phase,
        intent=intent,
        prefetch_direction=(
            (direction if intent != "idle" else 0)
            if prefetch_direction is None
            else (1 if prefetch_direction > 0 else (-1 if prefetch_direction < 0 else 0))
        ),
        predicted_input_interval_ms=(
            None
            if predicted_input_interval_ms is None
            else max(0.0, float(predicted_input_interval_ms))
        ),
        display_bucket=resolve_display_thumbnail_bucket(display_bucket),
        full_prefetch_first=full_prefetch_first,
        full_prefetch_last=full_prefetch_last,
        warm_first=warm_first,
        warm_last=warm_last,
    )


def resolve_display_thumbnail_bucket(physical_edge: int | float) -> int:
    edge = max(1, int(round(float(physical_edge))))
    return next((bucket for bucket in DISPLAY_THUMBNAIL_BUCKETS if bucket >= edge), 512)


def _bounded_range(row_count: int, first: int, last: int) -> tuple[int, int]:
    bounded_first = max(0, min(first, row_count - 1))
    bounded_last = max(bounded_first, min(last, row_count - 1))
    return bounded_first, bounded_last


def _full_prefetch_screens(
    *,
    phase: GalleryScrollPhase,
    intent: GalleryScrollIntent,
    direction: int,
    predicted_input_interval_ms: float | None,
) -> tuple[int, int]:
    """Return lookbehind/lookahead full-thumbnail screens for the current intent."""

    medium_slow_input = (
        phase == "medium"
        and predicted_input_interval_ms is not None
        and predicted_input_interval_ms > SCROLL_BURST_INTERVAL_MS
    )
    if phase == "fast" or (intent == "continuous_burst" and not medium_slow_input):
        return 0, 0

    if intent == "idle":
        return FULL_PREFETCH_IDLE_SCREENS, FULL_PREFETCH_IDLE_SCREENS

    if intent == "directional_dwell":
        ahead = FULL_PREFETCH_DWELL_AHEAD_SCREENS
        behind = FULL_PREFETCH_DWELL_BEHIND_SCREENS
    elif phase == "slow":
        ahead = FULL_PREFETCH_SLOW_AHEAD_SCREENS
        behind = FULL_PREFETCH_SLOW_BEHIND_SCREENS
    elif medium_slow_input:
        ahead = FULL_PREFETCH_MEDIUM_AHEAD_SCREENS
        behind = 0
    else:
        return 0, 0

    if direction < 0:
        return ahead, behind
    if direction > 0:
        return behind, ahead
    return behind, ahead


def _warm_window(
    *,
    row_count: int,
    first: int,
    last: int,
    target: int,
    direction: int,
) -> tuple[int, int]:
    visible_count = max(1, last - first + 1)
    extra = max(0, target - visible_count)
    if direction > 0:
        before = extra // 4
    elif direction < 0:
        before = extra - extra // 4
    else:
        before = extra // 2
    window_first = max(0, first - before)
    window_last = min(row_count - 1, window_first + target - 1)
    if window_last - window_first + 1 < target:
        window_first = max(0, window_last - target + 1)
    return window_first, window_last


__all__ = [
    "FAST_SCROLL_SCREENS_PER_SECOND",
    "DISPLAY_THUMBNAIL_BUCKETS",
    "MICRO_QUERY_CHUNK",
    "MICRO_WARM_LIMIT",
    "FULL_PREFETCH_SLOW_AHEAD_SCREENS",
    "FULL_PREFETCH_SLOW_BEHIND_SCREENS",
    "FULL_PREFETCH_DWELL_AHEAD_SCREENS",
    "FULL_PREFETCH_DWELL_BEHIND_SCREENS",
    "FULL_PREFETCH_IDLE_SCREENS",
    "FULL_PREFETCH_MEDIUM_AHEAD_SCREENS",
    "SCROLL_SETTLED_TIMEOUT_MS",
    "SCROLL_DIRECTIONAL_DWELL_MS",
    "SCROLL_DIRECTION_RETENTION_MS",
    "SCROLL_BURST_INTERVAL_MS",
    "SCROLL_VELOCITY_EWMA_SECONDS",
    "SLOW_SCROLL_SCREENS_PER_SECOND",
    "GalleryScrollIntent",
    "GalleryScrollPhase",
    "GalleryViewportDemand",
    "build_viewport_demand",
    "classify_scroll_phase",
    "resolve_display_thumbnail_bucket",
]

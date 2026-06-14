"""Shared Gallery viewport-demand policy and scheduling limits."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

GalleryScrollPhase = Literal["settled", "slow", "medium", "fast"]

MICRO_WARM_LIMIT = 2000
MICRO_QUERY_CHUNK = 256
MICRO_MIN_WARM_ITEMS = 300
MICRO_SLOW_SCREENS = 6
MICRO_MEDIUM_SCREENS = 24

SLOW_SCROLL_SCREENS_PER_SECOND = 2.0
FAST_SCROLL_SCREENS_PER_SECOND = 8.0
SCROLL_SETTLED_TIMEOUT_MS = 120
SCROLL_VELOCITY_EWMA_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class GalleryViewportDemand:
    """One immutable description of visible, full-prefetch, and micro-warm demand."""

    generation: int
    visible_first: int
    visible_last: int
    direction: int
    screens_per_second: float
    phase: GalleryScrollPhase
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
        """Yield viewport-external full-thumbnail rows nearest-first, alternating sides."""

        before = range(self.visible_first - 1, self.full_prefetch_first - 1, -1)
        after = range(self.visible_last + 1, self.full_prefetch_last + 1)
        before_iter = iter(before)
        after_iter = iter(after)
        while True:
            emitted = False
            try:
                yield next(before_iter)
                emitted = True
            except StopIteration:
                pass
            try:
                yield next(after_iter)
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
) -> GalleryViewportDemand:
    """Build bounded visible, full-prefetch, and micro-warm ranges."""

    row_count = max(1, int(row_count))
    first = max(0, min(int(visible_first), row_count - 1))
    last = max(first, min(int(visible_last), row_count - 1))
    direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
    phase = classify_scroll_phase(
        screens_per_second,
        actively_scrolling=actively_scrolling,
    )
    visible_count = max(1, last - first + 1)

    full_prefetch_screens = 2 if phase in {"settled", "slow"} else 0
    full_prefetch_first, full_prefetch_last = _bounded_range(
        row_count,
        first - visible_count * full_prefetch_screens,
        last + visible_count * full_prefetch_screens,
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
        direction=direction if phase != "settled" else 0,
    )

    return GalleryViewportDemand(
        generation=int(generation),
        visible_first=first,
        visible_last=last,
        direction=direction,
        screens_per_second=max(0.0, float(screens_per_second)),
        phase=phase,
        full_prefetch_first=full_prefetch_first,
        full_prefetch_last=full_prefetch_last,
        warm_first=warm_first,
        warm_last=warm_last,
    )


def _bounded_range(row_count: int, first: int, last: int) -> tuple[int, int]:
    bounded_first = max(0, min(first, row_count - 1))
    bounded_last = max(bounded_first, min(last, row_count - 1))
    return bounded_first, bounded_last


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
    "MICRO_QUERY_CHUNK",
    "MICRO_WARM_LIMIT",
    "SCROLL_SETTLED_TIMEOUT_MS",
    "SCROLL_VELOCITY_EWMA_SECONDS",
    "SLOW_SCROLL_SCREENS_PER_SECOND",
    "GalleryScrollPhase",
    "GalleryViewportDemand",
    "build_viewport_demand",
    "classify_scroll_phase",
]

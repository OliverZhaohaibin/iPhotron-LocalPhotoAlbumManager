"""Shared Gallery viewport-demand policy and scheduling limits."""

from __future__ import annotations

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
SLOW_FULL_AHEAD_SCREENS = 6
SLOW_FULL_BEHIND_SCREENS = 1
MEDIUM_FULL_AHEAD_SCREENS = 2
SCROLL_SETTLED_TIMEOUT_MS = 120
SCROLL_VELOCITY_EWMA_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class GalleryViewportDemand:
    """One immutable description of visible, hot, and warm Gallery demand."""

    generation: int
    visible_first: int
    visible_last: int
    direction: int
    screens_per_second: float
    phase: GalleryScrollPhase
    ahead_full_range: tuple[int, int] | None
    behind_full_range: tuple[int, int] | None
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
    def hot_range(self) -> tuple[int, int]:
        """Compatibility range spanning all requested full thumbnails."""

        ranges = [
            item
            for item in (
                self.behind_full_range,
                self.visible_range,
                self.ahead_full_range,
            )
            if item is not None
        ]
        return min(item[0] for item in ranges), max(item[1] for item in ranges)

    @property
    def hot_first(self) -> int:
        return self.hot_range[0]

    @property
    def hot_last(self) -> int:
        return self.hot_range[1]

    @property
    def warm_range(self) -> tuple[int, int]:
        return self.warm_first, self.warm_last


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
    """Build bounded visible/hot/warm ranges from current scroll behavior."""

    row_count = max(1, int(row_count))
    first = max(0, min(int(visible_first), row_count - 1))
    last = max(first, min(int(visible_last), row_count - 1))
    direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
    phase = classify_scroll_phase(
        screens_per_second,
        actively_scrolling=actively_scrolling,
    )
    visible_count = max(1, last - first + 1)

    ahead_screens, behind_screens = _full_prefetch_screens(phase)
    ahead_full_range, behind_full_range = _directional_full_ranges(
        row_count=row_count,
        first=first,
        last=last,
        visible_count=visible_count,
        direction=direction,
        ahead_screens=ahead_screens,
        behind_screens=behind_screens,
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
        ahead_full_range=ahead_full_range,
        behind_full_range=behind_full_range,
        warm_first=warm_first,
        warm_last=warm_last,
    )


def _full_prefetch_screens(phase: GalleryScrollPhase) -> tuple[int, int]:
    if phase in {"settled", "slow"}:
        return SLOW_FULL_AHEAD_SCREENS, SLOW_FULL_BEHIND_SCREENS
    if phase == "medium":
        return MEDIUM_FULL_AHEAD_SCREENS, 0
    return 0, 0


def _directional_full_ranges(
    *,
    row_count: int,
    first: int,
    last: int,
    visible_count: int,
    direction: int,
    ahead_screens: int,
    behind_screens: int,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    if direction < 0:
        ahead = _optional_range(
            row_count,
            first - visible_count * ahead_screens,
            first - 1,
        )
        behind = _optional_range(
            row_count,
            last + 1,
            last + visible_count * behind_screens,
        )
        return ahead, behind

    ahead = _optional_range(
        row_count,
        last + 1,
        last + visible_count * ahead_screens,
    )
    behind = _optional_range(
        row_count,
        first - visible_count * behind_screens,
        first - 1,
    )
    return ahead, behind


def _optional_range(row_count: int, first: int, last: int) -> tuple[int, int] | None:
    bounded_first = max(0, first)
    bounded_last = min(row_count - 1, last)
    if bounded_first > bounded_last:
        return None
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
    "MEDIUM_FULL_AHEAD_SCREENS",
    "MICRO_QUERY_CHUNK",
    "MICRO_WARM_LIMIT",
    "SCROLL_SETTLED_TIMEOUT_MS",
    "SCROLL_VELOCITY_EWMA_SECONDS",
    "SLOW_FULL_AHEAD_SCREENS",
    "SLOW_FULL_BEHIND_SCREENS",
    "SLOW_SCROLL_SCREENS_PER_SECOND",
    "GalleryScrollPhase",
    "GalleryViewportDemand",
    "build_viewport_demand",
    "classify_scroll_phase",
]

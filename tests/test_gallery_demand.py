from __future__ import annotations

from iPhoto.gui.gallery_demand import MICRO_WARM_LIMIT, build_viewport_demand


def test_fast_demand_limits_full_hot_range_and_warms_2000_micro_items() -> None:
    demand = build_viewport_demand(
        generation=4,
        row_count=100_000,
        visible_first=50_000,
        visible_last=50_039,
        direction=1,
        screens_per_second=12.0,
        actively_scrolling=True,
    )

    assert demand.phase == "fast"
    assert demand.hot_range == demand.visible_range
    assert demand.warm_last - demand.warm_first + 1 == MICRO_WARM_LIMIT
    assert demand.warm_last - demand.visible_last > demand.visible_first - demand.warm_first


def test_medium_and_slow_demand_progressively_expand_full_hot_range() -> None:
    medium = build_viewport_demand(
        generation=1,
        row_count=10_000,
        visible_first=1_000,
        visible_last=1_019,
        direction=1,
        screens_per_second=4.0,
        actively_scrolling=True,
    )
    slow = build_viewport_demand(
        generation=2,
        row_count=10_000,
        visible_first=1_000,
        visible_last=1_019,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    assert medium.phase == "medium"
    assert slow.phase == "slow"
    assert medium.hot_first == medium.visible_first
    assert medium.hot_last > medium.visible_last
    assert slow.hot_first < slow.visible_first
    assert slow.hot_last > medium.hot_last


def test_settled_warm_range_is_centered_and_bounded() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=320,
        visible_first=0,
        visible_last=19,
        direction=-1,
        screens_per_second=0.0,
        actively_scrolling=False,
    )

    assert demand.phase == "settled"
    assert demand.warm_first == 0
    assert demand.warm_last == 299

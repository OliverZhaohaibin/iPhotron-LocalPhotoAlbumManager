from __future__ import annotations

from iPhoto.gui.gallery_demand import MICRO_WARM_LIMIT, build_viewport_demand


def test_fast_demand_disables_full_prefetch_and_warms_2000_micro_items() -> None:
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
    assert demand.full_prefetch_range == demand.visible_range
    assert list(demand.iter_full_prefetch_rows()) == []
    assert demand.warm_last - demand.warm_first + 1 == MICRO_WARM_LIMIT
    assert demand.warm_last - demand.visible_last > demand.visible_first - demand.warm_first


def test_slow_demand_prefetches_two_viewports_on_both_sides() -> None:
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
    assert medium.full_prefetch_range == medium.visible_range
    assert slow.full_prefetch_range == (960, 1059)


def test_scrolling_full_prefetch_rows_favor_the_forward_direction_three_to_one() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    assert list(demand.iter_full_prefetch_rows()) == [
        103,
        104,
        105,
        99,
        106,
        107,
        108,
        98,
        97,
        96,
        95,
        94,
    ]


def test_upward_full_prefetch_rows_favor_rows_before_the_viewport() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=-1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    assert list(demand.iter_full_prefetch_rows()) == [
        99,
        98,
        97,
        103,
        96,
        95,
        94,
        104,
        105,
        106,
        107,
        108,
    ]


def test_settled_full_prefetch_rows_alternate_nearest_before_and_after() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
    )

    assert list(demand.iter_full_prefetch_rows()) == [
        99,
        103,
        98,
        104,
        97,
        105,
        96,
        106,
        95,
        107,
        94,
        108,
    ]


def test_full_prefetch_rows_are_bounded_at_collection_edges() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=8,
        visible_first=0,
        visible_last=1,
        direction=-1,
        screens_per_second=0.0,
        actively_scrolling=False,
    )

    assert demand.full_prefetch_range == (0, 5)
    assert list(demand.iter_full_prefetch_rows()) == [2, 3, 4, 5]


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

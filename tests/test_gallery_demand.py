from __future__ import annotations

from iPhoto.gui.gallery_demand import (
    MICRO_WARM_LIMIT,
    build_viewport_demand,
    resolve_display_thumbnail_bucket,
)


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


def test_slow_demand_builds_one_screen_guard_and_directional_speculation() -> None:
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
    assert slow.full_guard_range == (980, 1039)
    assert slow.full_prefetch_range == (960, 1079)


def test_scrolling_full_prefetch_rows_alternate_from_viewpoint_with_direction_tie() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    assert list(demand.iter_full_prefetch_rows())[:12] == [
        103,
        99,
        104,
        98,
        105,
        97,
        106,
        107,
        108,
        96,
        109,
        110,
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

    assert list(demand.iter_full_prefetch_rows())[:12] == [
        99,
        103,
        98,
        104,
        97,
        105,
        96,
        95,
        94,
        106,
        93,
        92,
    ]


def test_idle_full_prefetch_rows_cover_guard_then_two_speculative_screens() -> None:
    demand = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
    )

    assert demand.full_guard_range == (97, 105)
    assert demand.full_prefetch_range == (91, 111)
    assert list(demand.iter_full_prefetch_rows())[:12] == [
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

    assert demand.full_prefetch_range == (0, 7)
    assert list(demand.iter_full_prefetch_rows()) == [2, 3, 4, 5, 6, 7]


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


def test_directional_dwell_finishes_next_screen_before_far_prefetch() -> None:
    demand = build_viewport_demand(
        generation=4,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
        intent="directional_dwell",
        prefetch_direction=1,
    )

    assert demand.phase == "settled"
    assert demand.full_guard_range == (97, 105)
    assert demand.full_prefetch_range == (94, 111)
    assert list(demand.iter_full_prefetch_rows())[:4] == [103, 99, 104, 98]


def test_medium_scroll_keeps_full_work_visible_only() -> None:
    demand = build_viewport_demand(
        generation=5,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=4.0,
        actively_scrolling=True,
        predicted_input_interval_ms=100.0,
    )

    assert demand.phase == "medium"
    assert demand.full_prefetch_range == demand.visible_range
    assert list(demand.iter_full_prefetch_rows()) == []


def test_slow_demand_after_burst_needs_no_recovery_state() -> None:
    demand = build_viewport_demand(
        generation=6,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=9.0,
        actively_scrolling=True,
        intent="slow_continuous",
    )

    assert demand.phase == "slow"
    assert demand.full_guard_range == (97, 105)
    assert demand.full_prefetch_range == (94, 111)
    assert list(demand.iter_full_prefetch_rows())[:6] == [103, 99, 104, 98, 105, 97]


def test_display_thumbnail_bucket_never_requires_new_disk_sizes() -> None:
    assert resolve_display_thumbnail_bucket(192) == 256
    assert resolve_display_thumbnail_bucket(300) == 384
    assert resolve_display_thumbnail_bucket(500) == 512
    assert resolve_display_thumbnail_bucket(900) == 512

"""Tests for VirtualAssetGrid — headless virtualization logic."""

from __future__ import annotations

import importlib
import sys

import pytest

# Direct file-level import to bypass the iPhoto.gui package chain which
# triggers PySide6/Qt imports that are unavailable in headless CI.
# VirtualAssetGrid itself is pure Python with no Qt dependency.
_spec = importlib.util.spec_from_file_location(
    "virtual_grid",
    str(__import__("pathlib").Path(__file__).resolve().parents[1]
        / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "virtual_grid.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
VirtualAssetGrid = _mod.VirtualAssetGrid


class TestVirtualAssetGrid:
    def test_default_state(self):
        grid = VirtualAssetGrid()
        assert grid.total_count == 0

    def test_set_total_count(self):
        grid = VirtualAssetGrid()
        grid.set_total_count(100)
        assert grid.total_count == 100

    def test_set_negative_total_count_clamps_to_zero(self):
        grid = VirtualAssetGrid()
        grid.set_total_count(-5)
        assert grid.total_count == 0

    def test_visible_range_empty(self):
        grid = VirtualAssetGrid()
        first, last = grid.calculate_visible_range(800, 600)
        assert first == 0
        assert last == 0

    def test_visible_range_single_row(self):
        grid = VirtualAssetGrid(item_width=200, item_height=200, spacing=0)
        grid.set_total_count(4)
        # 800px wide → 4 columns, all items fit in one row
        first, last = grid.calculate_visible_range(800, 400, scroll_y=0)
        assert first == 0
        assert last == 4

    def test_visible_range_scrolled(self):
        grid = VirtualAssetGrid(item_width=100, item_height=100, spacing=0)
        grid.set_total_count(100)
        # viewport 400×200 → 4 cols; scroll past first 2 rows
        first, last = grid.calculate_visible_range(400, 200, scroll_y=200)
        assert first > 0
        assert last > first
        assert last <= 100

    def test_visible_range_does_not_exceed_total(self):
        grid = VirtualAssetGrid(item_width=100, item_height=100, spacing=0)
        grid.set_total_count(5)
        _, last = grid.calculate_visible_range(800, 800, scroll_y=0)
        assert last <= 5

    def test_content_height(self):
        grid = VirtualAssetGrid(item_width=200, item_height=200, spacing=0)
        grid.set_total_count(10)
        # 800px wide → 4 cols → ceil(10/4)=3 rows → 600px
        assert grid.content_height(800) == 600

    def test_content_height_zero(self):
        grid = VirtualAssetGrid()
        assert grid.content_height(800) == 0

    def test_item_rect_first(self):
        grid = VirtualAssetGrid(item_width=200, item_height=200, spacing=4)
        grid.set_total_count(10)
        x, y, w, h = grid.item_rect(0, 800)
        assert (x, y) == (0, 0)
        assert (w, h) == (200, 200)

    def test_item_rect_second_row(self):
        grid = VirtualAssetGrid(item_width=200, item_height=200, spacing=0)
        grid.set_total_count(10)
        # 800px wide → 4 cols; index 4 should be row 1
        x, y, w, h = grid.item_rect(4, 800)
        assert y == 200
        assert x == 0

    def test_columns_calculation(self):
        grid = VirtualAssetGrid(item_width=200, item_height=200, spacing=10)
        # 630px wide: (200+10)=210 per cell → 630//210 = 3 columns
        assert grid._columns(630) == 3

    def test_spacing_affects_layout(self):
        grid_no_space = VirtualAssetGrid(item_width=100, item_height=100, spacing=0)
        grid_spaced = VirtualAssetGrid(item_width=100, item_height=100, spacing=20)
        grid_no_space.set_total_count(8)
        grid_spaced.set_total_count(8)
        assert grid_spaced.content_height(400) > grid_no_space.content_height(400)

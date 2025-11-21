"""Tests for the gl_crop HitTester module."""

import pytest
from PySide6.QtCore import QPointF

from src.iPhoto.gui.ui.widgets.gl_crop.hit_tester import HitTester
from src.iPhoto.gui.ui.widgets.gl_crop.utils import CropHandle


@pytest.fixture
def hit_tester():
    """Create a HitTester instance with standard padding."""
    return HitTester(hit_padding=10.0)


@pytest.fixture
def crop_corners():
    """Standard crop box corners for testing."""
    return {
        "top_left": QPointF(100, 100),
        "top_right": QPointF(200, 100),
        "bottom_right": QPointF(200, 200),
        "bottom_left": QPointF(100, 200),
    }


def test_hit_corner_top_left(hit_tester, crop_corners):
    """Test hitting the top-left corner."""
    point = QPointF(102, 102)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.TOP_LEFT


def test_hit_corner_top_right(hit_tester, crop_corners):
    """Test hitting the top-right corner."""
    point = QPointF(198, 102)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.TOP_RIGHT


def test_hit_corner_bottom_right(hit_tester, crop_corners):
    """Test hitting the bottom-right corner."""
    point = QPointF(198, 198)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.BOTTOM_RIGHT


def test_hit_corner_bottom_left(hit_tester, crop_corners):
    """Test hitting the bottom-left corner."""
    point = QPointF(102, 198)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.BOTTOM_LEFT


def test_hit_edge_top(hit_tester, crop_corners):
    """Test hitting the top edge."""
    point = QPointF(150, 102)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.TOP


def test_hit_edge_right(hit_tester, crop_corners):
    """Test hitting the right edge."""
    point = QPointF(198, 150)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.RIGHT


def test_hit_edge_bottom(hit_tester, crop_corners):
    """Test hitting the bottom edge."""
    point = QPointF(150, 198)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.BOTTOM


def test_hit_edge_left(hit_tester, crop_corners):
    """Test hitting the left edge."""
    point = QPointF(102, 150)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.LEFT


def test_hit_inside(hit_tester, crop_corners):
    """Test hitting inside the crop box."""
    point = QPointF(150, 150)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.INSIDE


def test_hit_none_outside(hit_tester, crop_corners):
    """Test missing the crop box entirely."""
    point = QPointF(50, 50)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.NONE


def test_hit_none_far_from_edge(hit_tester, crop_corners):
    """Test being outside the hit padding threshold."""
    point = QPointF(150, 85)  # 15 pixels away from top edge (padding is 10)
    result = hit_tester.test(point, **crop_corners)
    assert result == CropHandle.NONE


def test_edge_hit_on_edge():
    """Test edge hit detection when point is exactly on the edge."""
    hit_tester = HitTester(hit_padding=5.0)
    # Point on the top edge
    point = QPointF(150, 100)
    corners = {
        "top_left": QPointF(100, 100),
        "top_right": QPointF(200, 100),
        "bottom_right": QPointF(200, 200),
        "bottom_left": QPointF(100, 200),
    }
    result = hit_tester.test(point, **corners)
    assert result == CropHandle.TOP


def test_edge_hit_near_edge():
    """Test edge hit detection when point is near but within padding."""
    hit_tester = HitTester(hit_padding=10.0)
    # Point 5 pixels below the top edge (within 10 pixel padding)
    point = QPointF(150, 105)
    corners = {
        "top_left": QPointF(100, 100),
        "top_right": QPointF(200, 100),
        "bottom_right": QPointF(200, 200),
        "bottom_left": QPointF(100, 200),
    }
    result = hit_tester.test(point, **corners)
    assert result == CropHandle.TOP


def test_edge_miss_beyond_padding():
    """Test edge miss when point is beyond padding distance."""
    hit_tester = HitTester(hit_padding=5.0)
    # Point 10 pixels below the top edge (beyond 5 pixel padding)
    point = QPointF(150, 110)
    corners = {
        "top_left": QPointF(100, 100),
        "top_right": QPointF(200, 100),
        "bottom_right": QPointF(200, 200),
        "bottom_left": QPointF(100, 200),
    }
    result = hit_tester.test(point, **corners)
    # Should be INSIDE since it's beyond edge padding but inside the box
    assert result == CropHandle.INSIDE

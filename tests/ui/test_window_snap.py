"""Unit tests for the edge-snap window tiling helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QRect

from iPhoto.gui.ui.window_snap import (
    EdgeSnapHelper,
    SnapZone,
    _CORNER_SIZE,
    _EDGE_THRESHOLD,
    detect_snap_zone,
    snap_geometry,
)


# ---------------------------------------------------------------------------
# Fake QScreen
# ---------------------------------------------------------------------------

class _FakeScreen:
    """Minimal stand-in for ``QScreen`` used in geometry tests."""

    def __init__(self, rect: QRect) -> None:
        self._rect = rect

    def availableGeometry(self) -> QRect:  # noqa: N802
        return QRect(self._rect)


_SCREEN = _FakeScreen(QRect(0, 0, 1920, 1080))


def _make_helper() -> EdgeSnapHelper:
    """Create an ``EdgeSnapHelper`` with a mocked overlay to avoid QWidget crashes."""
    helper = EdgeSnapHelper()
    helper._overlay = MagicMock()
    return helper


# ---------------------------------------------------------------------------
# detect_snap_zone
# ---------------------------------------------------------------------------

class TestDetectSnapZone:
    """Tests for snap-zone detection near screen edges."""

    def test_center_returns_none(self) -> None:
        assert detect_snap_zone(QPoint(960, 540), _SCREEN) is SnapZone.NONE

    def test_none_screen_returns_none(self) -> None:
        assert detect_snap_zone(QPoint(0, 0), None) is SnapZone.NONE

    def test_left_edge(self) -> None:
        pt = QPoint(_EDGE_THRESHOLD, 540)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.LEFT

    def test_right_edge(self) -> None:
        pt = QPoint(1920 - _EDGE_THRESHOLD, 540)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.RIGHT

    def test_top_edge(self) -> None:
        pt = QPoint(960, _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.TOP

    # Corner snapping (Linux platform for tests) --------------------------

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_top_left_corner_at_edge(self, _mock: MagicMock) -> None:
        pt = QPoint(_EDGE_THRESHOLD, _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.TOP_LEFT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_top_left_corner_wide_region(self, _mock: MagicMock) -> None:
        """Corner detection uses the full _CORNER_SIZE square, not just the 8px edge."""
        pt = QPoint(_CORNER_SIZE - 1, _CORNER_SIZE - 1)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.TOP_LEFT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_top_right_corner(self, _mock: MagicMock) -> None:
        pt = QPoint(1920 - _EDGE_THRESHOLD, _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.TOP_RIGHT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_bottom_left_corner(self, _mock: MagicMock) -> None:
        pt = QPoint(_EDGE_THRESHOLD, 1080 - _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.BOTTOM_LEFT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_bottom_right_corner(self, _mock: MagicMock) -> None:
        pt = QPoint(1920 - _EDGE_THRESHOLD, 1080 - _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.BOTTOM_RIGHT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="linux")
    def test_bottom_right_corner_wide_region(self, _mock: MagicMock) -> None:
        """Corner detection uses the full _CORNER_SIZE square at bottom-right."""
        pt = QPoint(1920 - _CORNER_SIZE + 1, 1080 - _CORNER_SIZE + 1)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.BOTTOM_RIGHT

    # macOS disables corner snapping --------------------------------------

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="mac")
    def test_mac_no_corner_snap_top_left(self, _mock: MagicMock) -> None:
        pt = QPoint(_EDGE_THRESHOLD, _EDGE_THRESHOLD)
        # On macOS, the left edge takes priority (no corner snap).
        zone = detect_snap_zone(pt, _SCREEN)
        assert zone is SnapZone.LEFT

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="mac")
    def test_mac_no_corner_snap_top_right(self, _mock: MagicMock) -> None:
        pt = QPoint(1920 - _EDGE_THRESHOLD, _EDGE_THRESHOLD)
        zone = detect_snap_zone(pt, _SCREEN)
        assert zone is SnapZone.RIGHT

    # Windows also supports corner snapping --------------------------------

    @patch("iPhoto.gui.ui.window_snap._platform", return_value="win")
    def test_win_top_left_corner(self, _mock: MagicMock) -> None:
        pt = QPoint(_EDGE_THRESHOLD, _EDGE_THRESHOLD)
        assert detect_snap_zone(pt, _SCREEN) is SnapZone.TOP_LEFT


# ---------------------------------------------------------------------------
# snap_geometry
# ---------------------------------------------------------------------------

class TestSnapGeometry:
    """Tests for target geometry calculation."""

    def test_none_zone_returns_empty(self) -> None:
        assert snap_geometry(SnapZone.NONE, _SCREEN).isEmpty()

    def test_none_screen_returns_empty(self) -> None:
        assert snap_geometry(SnapZone.LEFT, None).isEmpty()

    def test_left_half(self) -> None:
        rect = snap_geometry(SnapZone.LEFT, _SCREEN)
        assert rect == QRect(0, 0, 960, 1080)

    def test_right_half(self) -> None:
        rect = snap_geometry(SnapZone.RIGHT, _SCREEN)
        assert rect == QRect(960, 0, 960, 1080)

    def test_top_maximise(self) -> None:
        rect = snap_geometry(SnapZone.TOP, _SCREEN)
        assert rect == QRect(0, 0, 1920, 1080)

    def test_top_left_quarter(self) -> None:
        rect = snap_geometry(SnapZone.TOP_LEFT, _SCREEN)
        assert rect == QRect(0, 0, 960, 540)

    def test_top_right_quarter(self) -> None:
        rect = snap_geometry(SnapZone.TOP_RIGHT, _SCREEN)
        assert rect == QRect(960, 0, 960, 540)

    def test_bottom_left_quarter(self) -> None:
        rect = snap_geometry(SnapZone.BOTTOM_LEFT, _SCREEN)
        assert rect == QRect(0, 540, 960, 540)

    def test_bottom_right_quarter(self) -> None:
        rect = snap_geometry(SnapZone.BOTTOM_RIGHT, _SCREEN)
        assert rect == QRect(960, 540, 960, 540)

    def test_offset_screen(self) -> None:
        """Geometry on a screen whose origin is not (0, 0)."""
        screen = _FakeScreen(QRect(1920, 0, 1920, 1080))
        rect = snap_geometry(SnapZone.LEFT, screen)
        assert rect == QRect(1920, 0, 960, 1080)


# ---------------------------------------------------------------------------
# EdgeSnapHelper
# ---------------------------------------------------------------------------

class TestEdgeSnapHelper:
    """Integration tests for the snap helper lifecycle."""

    def test_initial_state(self) -> None:
        helper = _make_helper()
        assert not helper.is_snapped()
        assert helper.current_zone is SnapZone.NONE

    def test_snap_left_commit(self) -> None:
        helper = _make_helper()
        helper.begin_drag(QRect(100, 100, 800, 600))
        helper.update(QPoint(_EDGE_THRESHOLD, 540), _SCREEN)
        assert helper.current_zone is SnapZone.LEFT

        rect = helper.commit_with_screen(_SCREEN)
        assert rect == QRect(0, 0, 960, 1080)
        assert helper.is_snapped()

    def test_drag_away_clears_snap(self) -> None:
        helper = _make_helper()
        helper.begin_drag(QRect(100, 100, 800, 600))
        helper.update(QPoint(_EDGE_THRESHOLD, 540), _SCREEN)
        helper.commit_with_screen(_SCREEN)
        assert helper.is_snapped()

        # Start a new drag and release without snapping.
        helper.begin_drag(QRect(0, 0, 960, 1080))
        helper.update(QPoint(500, 500), _SCREEN)
        rect = helper.commit_with_screen(_SCREEN)
        assert rect.isEmpty()
        assert not helper.is_snapped()

    def test_pre_snap_geometry_preserved(self) -> None:
        original = QRect(100, 100, 800, 600)
        helper = _make_helper()
        helper.begin_drag(original)
        helper.update(QPoint(_EDGE_THRESHOLD, 540), _SCREEN)
        helper.commit_with_screen(_SCREEN)

        assert helper.pre_snap_geometry() == original

    def test_pre_snap_geometry_kept_on_re_snap(self) -> None:
        """Re-snapping preserves the original pre-snap geometry."""
        original = QRect(100, 100, 800, 600)
        helper = _make_helper()

        # First snap to left.
        helper.begin_drag(original)
        helper.update(QPoint(_EDGE_THRESHOLD, 540), _SCREEN)
        helper.commit_with_screen(_SCREEN)

        # Re-snap to right (without un-snapping first).
        helper.begin_drag(QRect(0, 0, 960, 1080))
        helper.update(QPoint(1920 - _EDGE_THRESHOLD, 540), _SCREEN)
        helper.commit_with_screen(_SCREEN)

        # The original geometry should still be the first one.
        assert helper.pre_snap_geometry() == original

    def test_cancel_resets_zone(self) -> None:
        helper = _make_helper()
        helper.begin_drag(QRect(100, 100, 800, 600))
        helper.update(QPoint(_EDGE_THRESHOLD, 540), _SCREEN)
        helper.cancel()
        assert helper.current_zone is SnapZone.NONE

    def test_commit_no_zone_returns_empty(self) -> None:
        helper = _make_helper()
        helper.begin_drag(QRect(100, 100, 800, 600))
        rect = helper.commit_with_screen(_SCREEN)
        assert rect.isEmpty()
        assert not helper.is_snapped()

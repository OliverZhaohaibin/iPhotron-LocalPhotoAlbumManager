"""
Hit testing logic for crop handles.

This module contains pure geometric functions for detecting which crop handle
(if any) is under a given point, with no dependencies on Qt events or UI state.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF

from .utils import CropHandle


class HitTester:
    """Pure-function hit tester for crop box handles."""

    def __init__(self, hit_padding: float = 12.0) -> None:
        """Initialize hit tester.

        Parameters
        ----------
        hit_padding:
            Distance threshold for detecting corner/edge hits, in viewport pixels.
        """
        self._hit_padding = float(hit_padding)

    @staticmethod
    def _distance_to_segment(point: QPointF, start: QPointF, end: QPointF) -> float:
        """Calculate distance from point to line segment."""
        px, py = point.x(), point.y()
        ax, ay = start.x(), start.y()
        bx, by = end.x(), end.y()
        vx = bx - ax
        vy = by - ay
        if abs(vx) < 1e-6 and abs(vy) < 1e-6:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)
        t = max(0.0, min(1.0, t))
        qx = ax + t * vx
        qy = ay + t * vy
        return math.hypot(px - qx, py - qy)

    def test(
        self,
        point: QPointF,
        top_left: QPointF,
        top_right: QPointF,
        bottom_right: QPointF,
        bottom_left: QPointF,
    ) -> CropHandle:
        """Determine which crop handle (if any) is under the cursor.

        This returns TEXTURE-SPACE handles, not logical handles.
        The handle names (TOP, LEFT, etc.) refer to the texture coordinate system,
        not the visual/rotated display.

        Parameters
        ----------
        point:
            The point to test in viewport coordinates.
        top_left, top_right, bottom_right, bottom_left:
            The four corners of the crop box in viewport coordinates.

        Returns
        -------
        CropHandle:
            The handle that was hit, or CropHandle.NONE if no handle was hit.
        """
        # Check corners first - these are TEXTURE-SPACE handles
        corners = [
            (CropHandle.TOP_LEFT, top_left),
            (CropHandle.TOP_RIGHT, top_right),
            (CropHandle.BOTTOM_RIGHT, bottom_right),
            (CropHandle.BOTTOM_LEFT, bottom_left),
        ]
        for handle, corner in corners:
            if math.hypot(point.x() - corner.x(), point.y() - corner.y()) <= self._hit_padding:
                return handle

        # Check edges - these are TEXTURE-SPACE handles
        edges = [
            (CropHandle.TOP, top_left, top_right),
            (CropHandle.RIGHT, top_right, bottom_right),
            (CropHandle.BOTTOM, bottom_left, bottom_right),
            (CropHandle.LEFT, top_left, bottom_left),
        ]
        for handle, start, end in edges:
            if self._distance_to_segment(point, start, end) <= self._hit_padding:
                return handle

        # Check if inside
        left = min(top_left.x(), bottom_left.x())
        right = max(top_right.x(), bottom_right.x())
        top = min(top_left.y(), top_right.y())
        bottom = max(bottom_left.y(), bottom_right.y())
        if left <= point.x() <= right and top <= point.y() <= bottom:
            return CropHandle.INSIDE

        return CropHandle.NONE

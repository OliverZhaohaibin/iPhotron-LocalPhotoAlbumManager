"""Rendering regressions for the transient checkmark notification."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from iPhoto.gui.ui.widgets.notification_toast import NotificationToast


@pytest.mark.parametrize("dpr", [1, 1.5, 2])
def test_toast_checkmark_fills_its_icon_area(qapp, dpr):
    toast = NotificationToast()
    try:
        toast.setProperty("checkmark_progress", 1.0)
        image = QImage(toast.size() * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        toast.render(image)

        # The caption is empty, so the white pixels belong to the SVG glyph.
        pixels = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).red() >= 180
        ]
        assert pixels
        columns, rows = zip(*pixels)
        width = (max(columns) - min(columns) + 1) / dpr
        height = (max(rows) - min(rows) + 1) / dpr

        # The v6.6.6 rendition was about 79 by 82 logical pixels. Rendering
        # at the SVG's intrinsic size shrinks it to about 34 by 36.
        assert width == pytest.approx(79, abs=2)
        assert height == pytest.approx(82, abs=2)
    finally:
        toast.close()

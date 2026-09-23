from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget

from tools.windows_fullscreen_probe import capture_visible_regions, evaluate_capture


def screen(rect, dpr, *, color=QColor(30, 220, 50)):
    result = Mock()
    result.geometry.return_value = rect

    def grab(hwnd, x, y, width, height):
        assert hwnd == 0
        assert QRect(0, 0, rect.width(), rect.height()).contains(QRect(x, y, width, height))
        image = QImage(round(width * dpr), round(height * dpr), QImage.Format.Format_RGBA8888)
        image.fill(color)
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap

    result.grabWindow.side_effect = grab
    return result


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.5])
@pytest.mark.parametrize("left", [0, -1920])
def test_capture_uses_screen_local_coordinates_and_pixmap_dpr(qapp, dpr, left):
    monitor = screen(QRect(left, 0, 1920, 1080), dpr)
    viewer = QRect(left + 100, 80, 80, 40)
    [(record, image)] = capture_visible_regions(viewer, [monitor])
    monitor.grabWindow.assert_called_once_with(0, 100, 80, 80, 40)
    assert record["dpr"] == dpr
    result = evaluate_capture(record, image, QRectF(viewer))
    assert result["ok"] and result["status"] == "passed"
    assert result["observed_bounds_px"] == [0, 0, 80 * dpr, 40 * dpr]


def test_mixed_dpi_spanning_window_is_captured_and_checked_per_screen(qapp):
    left = screen(QRect(-100, 0, 100, 100), 1)
    right = screen(QRect(0, 0, 100, 100), 2.5)
    viewer = QRect(-20, 10, 40, 40)
    captures = capture_visible_regions(viewer, [left, right])
    left.grabWindow.assert_called_once_with(0, 80, 10, 20, 40)
    right.grabWindow.assert_called_once_with(0, 0, 10, 20, 40)
    assert [r["pixel_size"] for r, _ in captures] == [[20, 40], [50, 100]]
    assert all(evaluate_capture(r, image, QRectF(viewer))["ok"] for r, image in captures)


def test_overscan_outside_screen_is_never_requested(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 2.5)
    captures = capture_visible_regions(QRect(0, 0, 100, 101), [monitor])
    monitor.grabWindow.assert_called_once_with(0, 0, 0, 100, 100)
    record, image = captures[0]
    assert evaluate_capture(record, image, QRectF(0, 0, 100, 101))["ok"]


def test_empty_capture_and_no_intersection_are_failures(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 1)
    monitor.grabWindow.side_effect = lambda *args: QPixmap()
    record, image = capture_visible_regions(QRect(0, 0, 20, 20), [monitor])[0]
    result = evaluate_capture(record, image, QRectF(0, 0, 20, 20))
    assert not result["ok"] and result["status"] == "capture_failed"
    assert result["reason"] == "empty_capture"
    record, image = capture_visible_regions(QRect(200, 0, 20, 20), [monitor])[0]
    assert not record["ok"] and record["status"] == "no_visible_intersection"


def test_wrong_capture_dimensions_do_not_masquerade_as_render_failure(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 1)
    monitor.grabWindow.side_effect = lambda *args: QPixmap(5, 5)
    record, image = capture_visible_regions(QRect(0, 0, 20, 20), [monitor])[0]
    result = evaluate_capture(record, image, QRectF(0, 0, 20, 20))
    assert not result["ok"] and result["reason"] == "capture_size_mismatch"


def test_black_frame_is_a_pixel_mismatch_not_a_capture_error(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 1, color=QColor("black"))
    record, image = capture_visible_regions(QRect(0, 0, 100, 100), [monitor])[0]
    result = evaluate_capture(record, image, QRectF(10, 10, 80, 80))
    assert not result["ok"] and result["status"] == "pixel_mismatch"
    assert result["reason"] == "missing_green_content"


def test_stale_rectangle_is_detected_with_desktop_pixels(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 1)
    record, image = capture_visible_regions(QRect(0, 0, 100, 100), [monitor])[0]
    result = evaluate_capture(record, image, QRectF(10, 10, 80, 80))
    assert not result["ok"] and result["bounds_error_px"] == 10


def test_unavailable_media_geometry_cannot_pass_as_a_black_backdrop(qapp):
    monitor = screen(QRect(0, 0, 100, 100), 1, color=QColor("black"))
    record, image = capture_visible_regions(QRect(0, 0, 100, 100), [monitor])[0]
    result = evaluate_capture(record, image, QRectF())
    assert not result["ok"] and result["status"] == "invalid_expected_geometry"


@pytest.mark.parametrize("translucent", [True, False])
def test_translucency_never_changes_the_capture_api_to_an_hwnd(qapp, translucent):
    host = QWidget()
    host.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, translucent)
    host.setGeometry(10, 10, 80, 40)
    viewport = QRect(host.mapToGlobal(QPoint(0, 0)), host.size())
    monitor = screen(QRect(0, 0, 200, 100), 1.5)
    [(record, image)] = capture_visible_regions(viewport, [monitor])
    assert evaluate_capture(record, image, QRectF(viewport))["ok"]
    assert monitor.grabWindow.call_args.args[0] == 0
    assert host.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) == translucent

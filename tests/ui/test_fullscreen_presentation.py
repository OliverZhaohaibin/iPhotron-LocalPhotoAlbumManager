"""Contracts for the independent Windows fullscreen presentation hold."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QWidget

from iPhoto.gui.ui.fullscreen_presentation import _WindowsFullscreenPresentationHold


def _hold(qapp, *, settle_ms: int = 0):
    owner = QWidget()
    owner.resize(640, 400)
    owner.show()
    qapp.processEvents()
    hold = _WindowsFullscreenPresentationHold(
        transition_id=7,
        settle_ms=settle_ms,
    )
    return owner, hold


def test_hold_is_opaque_nonactivating_top_level(qapp) -> None:
    owner, hold = _hold(qapp)
    flags = hold.windowFlags()

    assert hold.isWindow()
    hold.show()
    qapp.processEvents()
    assert int(hold.winId()) != int(owner.winId())
    assert bool(flags & Qt.WindowType.Tool)
    assert bool(flags & Qt.WindowType.FramelessWindowHint)
    assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
    assert bool(flags & Qt.WindowType.WindowTransparentForInput)
    assert bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert hold.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert hold.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert not hold.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    hold.cancel()
    owner.close()


def test_present_uses_screen_local_snapshot_geometry_and_emits_after_paint(qapp) -> None:
    owner, hold = _hold(qapp)
    presented = QSignalSpy(hold.presented)
    snapshot = QPixmap(320, 180)
    snapshot.fill(QColor("#cc2020"))
    screen_geometry = QRect(100, 50, 1280, 720)
    source_global = QRect(260, 140, 320, 180)

    hold.present(
        screen_geometry=screen_geometry,
        source_rect_global=source_global,
        snapshot=snapshot,
        fallback_color=QColor("#101010"),
    )
    qapp.processEvents()

    assert hold.geometry() == screen_geometry
    assert hold.snapshot_rect() == QRect(160, 90, 320, 180)
    assert presented.wait(100)
    assert presented.at(0) == [7]

    hold.cancel()
    owner.close()


def test_zoom_retarget_and_handoff_are_generation_local(qapp) -> None:
    owner, hold = _hold(qapp)
    hold.present(
        screen_geometry=QRect(0, 0, 640, 400),
        source_rect_global=QRect(100, 80, 320, 180),
        snapshot=None,
        fallback_color=QColor("#202020"),
    )
    qapp.processEvents()
    zoomed = QSignalSpy(hold.zoomFinished)
    handed_off = QSignalSpy(hold.handoffFinished)

    hold.animate_to(QRect(0, 0, 640, 400), duration_ms=20)
    hold.retarget(QRect(10, 5, 620, 390))
    assert zoomed.wait(250)

    assert hold.snapshot_rect() == QRect(10, 5, 620, 390)
    assert zoomed.at(0) == [7]

    hold.finish_handoff(duration_ms=20)
    assert handed_off.wait(250)
    assert handed_off.at(0) == [7]
    assert not hold.isVisible()

    hold.cancel()
    owner.close()


@pytest.mark.parametrize("dpr", (1.0, 1.25, 1.5))
def test_snapshot_scaling_preserves_full_logical_content_at_fractional_dpr(
    qapp,
    dpr: float,
) -> None:
    owner, hold = _hold(qapp)
    snapshot = QPixmap(round(320 * dpr), round(180 * dpr))
    snapshot.setDevicePixelRatio(dpr)
    painter = QPainter(snapshot)
    painter.fillRect(QRect(0, 0, 160, 180), QColor("#d02020"))
    painter.fillRect(QRect(160, 0, 160, 180), QColor("#2050d0"))
    painter.end()
    hold.present(
        screen_geometry=QRect(0, 0, 640, 400),
        source_rect_global=QRect(100, 80, 320, 180),
        snapshot=snapshot,
        fallback_color=QColor("#000000"),
    )
    qapp.processEvents()

    rendered = hold.grab().toImage()
    left = rendered.pixelColor(140, 170)
    right = rendered.pixelColor(380, 170)

    assert left.red() > left.blue()
    assert right.blue() > right.red()
    hold.cancel()
    owner.close()


def test_cancelled_hold_cannot_emit_late_animation_signals(qapp) -> None:
    owner, hold = _hold(qapp)
    hold.present(
        screen_geometry=QRect(0, 0, 640, 400),
        source_rect_global=QRect(100, 80, 320, 180),
        snapshot=None,
        fallback_color=QColor("#202020"),
    )
    qapp.processEvents()
    zoomed = QSignalSpy(hold.zoomFinished)
    handed_off = QSignalSpy(hold.handoffFinished)

    hold.animate_to(QRect(0, 0, 640, 400), duration_ms=100)
    hold.cancel()
    QTest.qWait(150)

    assert zoomed.count() == 0
    assert handed_off.count() == 0
    assert not hold.isVisible()
    owner.close()

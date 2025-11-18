"""Tests for perspective drag signal connections and auto-scaling."""

import pytest

# Qt widgets test - skip if not available
try:
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QSignalSpy
    from src.iPhoto.gui.ui.widgets.edit_strip import BWSlider
    from src.iPhoto.gui.ui.widgets.edit_perspective_controls import (
        _PerspectiveSliderRow,
        PerspectiveControls,
    )
    qt_available = True
except (ImportError, RuntimeError):
    qt_available = False


@pytest.mark.skipif(not qt_available, reason="Qt widgets not available")
def test_bwslider_emits_drag_signals(qtbot):
    """Test that BWSlider emits dragStarted and dragEnded signals."""
    slider = BWSlider("Test", minimum=-1.0, maximum=1.0, initial=0.0)
    qtbot.addWidget(slider)
    
    # Create signal spies
    drag_started_spy = QSignalSpy(slider.dragStarted)
    drag_ended_spy = QSignalSpy(slider.dragEnded)
    
    # Simulate mouse press
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QEvent
    
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(50, 15),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(press_event)
    
    # Check dragStarted was emitted
    assert len(drag_started_spy) == 1
    
    # Simulate mouse release
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(60, 15),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(release_event)
    
    # Check dragEnded was emitted
    assert len(drag_ended_spy) == 1


@pytest.mark.skipif(not qt_available, reason="Qt widgets not available")
def test_perspective_slider_row_forwards_drag_signals(qtbot):
    """Test that _PerspectiveSliderRow forwards drag signals."""
    row = _PerspectiveSliderRow("Test", "perspective.vertical.svg")
    qtbot.addWidget(row)
    
    # Create signal spies
    drag_started_spy = QSignalSpy(row.dragStarted)
    drag_ended_spy = QSignalSpy(row.dragEnded)
    
    # Emit drag signals from internal slider
    row._slider.dragStarted.emit()
    assert len(drag_started_spy) == 1
    
    row._slider.dragEnded.emit()
    assert len(drag_ended_spy) == 1


@pytest.mark.skipif(not qt_available, reason="Qt widgets not available")
def test_perspective_controls_aggregates_drag_signals(qtbot):
    """Test that PerspectiveControls aggregates drag signals from both sliders."""
    controls = PerspectiveControls()
    qtbot.addWidget(controls)
    
    # Create signal spies
    drag_started_spy = QSignalSpy(controls.perspectiveDragStarted)
    drag_ended_spy = QSignalSpy(controls.perspectiveDragEnded)
    
    # Emit from vertical slider
    controls._vertical_row.dragStarted.emit()
    assert len(drag_started_spy) == 1
    
    controls._vertical_row.dragEnded.emit()
    assert len(drag_ended_spy) == 1
    
    # Emit from horizontal slider
    controls._horizontal_row.dragStarted.emit()
    assert len(drag_started_spy) == 2
    
    controls._horizontal_row.dragEnded.emit()
    assert len(drag_ended_spy) == 2

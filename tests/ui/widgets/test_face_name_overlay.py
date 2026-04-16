from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for face overlay tests")

import os

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
from iPhoto.people.repository import AssetFaceAnnotation


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _wait_until(app: QApplication, condition, timeout_ms: int = 2000) -> None:
    """Poll condition, processing Qt events, until it's True or timeout elapses."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Condition not met within timeout")


class _FakeViewer(QWidget):
    viewTransformChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_image_content = True

    def image_rect_to_viewport(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QRectF:
        del image_width, image_height
        return QRectF(float(x), float(y), float(width), float(height))

    def has_image_content(self) -> bool:
        return self._has_image_content

    def set_has_image_content(self, value: bool) -> None:
        self._has_image_content = bool(value)

    def pixmap(self):
        if not self._has_image_content:
            return None
        return QPixmap.fromImage(QImage(1, 1, QImage.Format.Format_ARGB32))


def _make_overlay(qapp):
    surface = QWidget()
    surface.resize(420, 320)
    surface.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    viewer = _FakeViewer(surface)
    viewer.setGeometry(0, 0, 420, 320)
    viewer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    overlay = FaceNameOverlayWidget(surface)
    overlay.setGeometry(surface.rect())
    overlay.set_viewer(viewer)
    surface.show()
    viewer.show()
    overlay.show()
    qapp.processEvents()
    return surface, viewer, overlay


def _annotation(
    *,
    face_id: str = "face-1",
    person_id: str = "person-1",
    display_name: str | None = None,
    box_x: int = 320,
    box_y: int = 220,
    box_w: int = 120,
    box_h: int = 90,
) -> AssetFaceAnnotation:
    return AssetFaceAnnotation(
        face_id=face_id,
        person_id=person_id,
        display_name=display_name,
        box_x=box_x,
        box_y=box_y,
        box_w=box_w,
        box_h=box_h,
        image_width=420,
        image_height=320,
    )


def test_face_name_overlay_shows_fallback_and_clamps_label(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations([_annotation(display_name=None)])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    chip = overlay._states["face-1"].chip
    assert chip.text() == "unnamed"
    assert chip.cursor().shape() == Qt.CursorShape.IBeamCursor
    assert chip.geometry().right() <= viewer.geometry().right()
    assert chip.geometry().bottom() <= viewer.geometry().bottom()


def test_face_name_overlay_hover_updates_highlighted_face(qapp) -> None:
    surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [
            _annotation(face_id="face-1", box_x=40, box_y=40),
            _annotation(face_id="face-2", box_x=220, box_y=80, display_name="Julie"),
        ]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    chip = overlay._states["face-2"].chip
    QTest.mouseMove(chip, chip.rect().center())
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-2")

    QTest.mouseMove(surface, QPoint(5, 5))
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)


@pytest.mark.parametrize(
    ("entered_text", "expected_name"),
    [
        ("  Alice  ", "Alice"),
        ("   ", None),
    ],
)
def test_face_name_overlay_commits_entered_name(qapp, entered_text: str, expected_name: str | None) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations([_annotation(display_name="Bob")])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    chip = overlay._states["face-1"].chip
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText(entered_text)

    spy = QSignalSpy(overlay.renameSubmitted)
    QTest.keyClick(overlay._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert len(spy) == 1
    assert list(spy[0]) == ["person-1", expected_name]
    assert overlay._states["face-1"].chip.text() == (expected_name or "unnamed")


def test_face_name_overlay_escape_and_focus_loss_cancel_edit(qapp) -> None:
    surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations([_annotation(display_name="Bob", box_x=60, box_y=70)])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    chip = overlay._states["face-1"].chip

    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Alice")
    QTest.keyClick(overlay._editor, Qt.Key.Key_Escape)
    _wait_until(qapp, lambda: overlay._editor is None)
    assert chip.text() == "Bob"

    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Charlie")
    surface.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.qWait(10)
    _wait_until(qapp, lambda: overlay._editor is None)
    assert chip.text() == "Bob"


def test_face_name_overlay_stays_visible_even_if_viewer_is_hidden_when_activated(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    viewer.hide()
    overlay.set_annotations([_annotation(display_name="Bob")])
    overlay.set_overlay_active(True)

    assert overlay.isVisible() is True
    assert overlay._states["face-1"].chip.isVisible() is True


def test_face_name_overlay_waits_for_loaded_image_before_showing_labels(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    viewer.set_has_image_content(False)
    overlay.set_annotations([_annotation(display_name="Bob", box_x=80, box_y=60)])
    overlay.set_overlay_active(True)

    chip = overlay._states["face-1"].chip
    assert overlay.isVisible() is False
    assert chip.isVisible() is False

    viewer.set_has_image_content(True)
    viewer.viewTransformChanged.emit()

    _wait_until(qapp, lambda: overlay.isVisible())
    assert chip.isVisible() is True
    assert chip.geometry().topLeft() != QPoint(0, 0)

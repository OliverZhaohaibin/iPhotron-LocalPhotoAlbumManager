from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for face overlay tests")

import os

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QImage, QMouseEvent, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
from iPhoto.gui.ui.widgets.recognition_annotations import (
    RecognitionAnnotation,
    RecognitionIdentitySuggestion,
)
from iPhoto.people.repository import AssetFaceAnnotation


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def close_qt_widgets_after_test(qapp):
    existing_widgets = set(QApplication.topLevelWidgets())
    yield
    created_widgets = [
        widget
        for widget in QApplication.topLevelWidgets()
        if widget not in existing_widgets
    ]
    for widget in reversed(created_widgets):
        widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        widget.close()
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()
    qapp.processEvents()


def _wait_until(app: QApplication, condition, timeout_ms: int = 2000) -> None:
    """Poll condition, processing Qt events, until it's True or timeout elapses."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Condition not met within timeout")


def _spy_records(spy: QSignalSpy) -> list[list[object]]:
    count = spy.count() if hasattr(spy, "count") else len(spy)
    if hasattr(spy, "at"):
        return [list(spy.at(index)) for index in range(count)]
    return [list(spy[index]) for index in range(count)]


class _FakeViewer(QWidget):
    viewTransformChanged = Signal()
    firstFrameReady = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_image_content = True
        self.mouse_press_count = 0
        self.mouse_release_count = 0

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

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.mouse_press_count += 1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.mouse_release_count += 1
        super().mouseReleaseEvent(event)


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


def _move_viewer_mouse_to_overlay_point(
    qapp,
    viewer: QWidget,
    overlay: FaceNameOverlayWidget,
    point: QPointF,
) -> None:
    viewer_point = viewer.mapFromGlobal(overlay.mapToGlobal(point.toPoint()))
    QTest.mouseMove(viewer, viewer_point)
    qapp.processEvents()


def _move_widget_mouse_to_overlay_point(
    qapp,
    target: QWidget,
    overlay: FaceNameOverlayWidget,
    point: QPointF,
) -> None:
    target_point = target.mapFromGlobal(overlay.mapToGlobal(point.toPoint()))
    QTest.mouseMove(target, target_point)
    qapp.processEvents()


def _chip_center(overlay: FaceNameOverlayWidget, face_id: str = "face-1") -> QPointF:
    return overlay._states[face_id].layout.chip_rect.center()


def _chip_margin_point(overlay: FaceNameOverlayWidget, face_id: str = "face-1") -> QPointF:
    hover_rect = overlay._states[face_id].layout.hover_rect
    visual_rect = overlay._states[face_id].layout.chip_rect
    candidates = [
        QPointF(visual_rect.left() - 3.0, visual_rect.center().y()),
        QPointF(visual_rect.right() + 3.0, visual_rect.center().y()),
        QPointF(visual_rect.center().x(), visual_rect.top() - 3.0),
        QPointF(visual_rect.center().x(), visual_rect.bottom() + 3.0),
    ]
    for point in candidates:
        if hover_rect.contains(point) and not visual_rect.contains(point):
            return point
    return QPointF(hover_rect.left() + 1.0, hover_rect.center().y())


def _assert_ibeam_cursor() -> None:
    cursor = QApplication.overrideCursor()
    assert cursor is not None
    assert cursor.shape() == Qt.CursorShape.IBeamCursor


def _assert_cursor_restored() -> None:
    assert QApplication.overrideCursor() is None


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

    layout = overlay._states["face-1"].layout
    assert layout.label_text == "unnamed"
    assert layout.chip_rect.right() <= viewer.geometry().right()
    assert layout.chip_rect.bottom() <= viewer.geometry().bottom()


def test_face_name_overlay_labels_candidate_as_pending_confirmation(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    annotation = _annotation(display_name=None)
    annotation = AssetFaceAnnotation(
        **{**annotation.__dict__, "promotion_state": "candidate"}
    )
    overlay.set_annotations([annotation])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    assert overlay._states["face-1"].layout.label_text == "Pending confirmation"


def test_candidate_face_click_opens_editor_and_confirms_renamed_cluster(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    annotation = _annotation(display_name=None)
    annotation = AssetFaceAnnotation(
        **{**annotation.__dict__, "promotion_state": "candidate"}
    )
    overlay.set_annotations([annotation])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Alice")
    spy = QSignalSpy(overlay.renameSubmitted)

    QTest.keyClick(overlay._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert _spy_records(spy) == [["person-1", "Alice"]]
    assert overlay._states["face-1"].layout.label_text == "Alice"


def test_unassigned_pending_face_click_creates_named_identity(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    annotation = _annotation(person_id=None, display_name=None)
    annotation = AssetFaceAnnotation(
        **{**annotation.__dict__, "promotion_state": "candidate"}
    )
    overlay.set_annotations([annotation])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Alice")
    spy = QSignalSpy(overlay.unassignedRenameSubmitted)

    QTest.keyClick(overlay._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    records = _spy_records(spy)
    assert len(records) == 1
    assert records[0][0].face_id == "face-1"
    assert records[0][1] == "Alice"
    assert overlay._states["face-1"].layout.label_text == "Alice"


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

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, _chip_center(overlay, "face-2"))
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-2")
    _assert_ibeam_cursor()

    QTest.mouseMove(surface, QPoint(5, 5))
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)
    _assert_cursor_restored()


def test_face_name_overlay_chip_margin_is_real_ibeam_hit_target(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    margin_point = _chip_margin_point(overlay, "face-1")
    global_margin_point = overlay.mapToGlobal(margin_point.toPoint())

    assert overlay._editor is None

    QTest.mouseMove(viewer, viewer.mapFromGlobal(global_margin_point))

    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()
    assert overlay._editor is None


def test_face_name_overlay_face_circle_does_not_keep_chip_hover_alive(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    state = overlay._states["face-1"]
    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, state.layout.chip_rect.center())
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")

    circle_center = state.layout.circle_rect.center()
    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, circle_center)
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)
    _assert_cursor_restored()

    viewer.viewTransformChanged.emit()
    qapp.processEvents()

    assert overlay._hovered_face_id is None


def test_face_name_overlay_switches_hover_between_chips(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [
            _annotation(face_id="face-1", box_x=40, box_y=40, display_name="Alice"),
            _annotation(face_id="face-2", box_x=220, box_y=80, display_name="Julie"),
        ]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    first = overlay._states["face-1"]
    second = overlay._states["face-2"]

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, first.layout.chip_rect.center())
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")

    first_circle_center = first.layout.circle_rect.center()
    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, first_circle_center)
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, second.layout.chip_rect.center())
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-2")


def test_face_name_overlay_chip_margin_survives_chip_leave(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, _chip_center(overlay))
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")

    margin_point = _chip_margin_point(overlay)
    QCursor.setPos(overlay.mapToGlobal(margin_point.toPoint()))
    QApplication.sendEvent(viewer, QEvent(QEvent.Type.Leave))

    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()


def test_face_name_overlay_parent_mouse_move_updates_chip_margin_hover(qapp) -> None:
    surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    margin_point = _chip_margin_point(overlay)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        margin_point,
        margin_point,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(surface, move_event)

    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()


def test_face_name_overlay_transform_refresh_uses_current_cursor(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()
    qapp.processEvents()

    overlay._set_hovered_face_id(None)
    QCursor.setPos(overlay.mapToGlobal(_chip_center(overlay).toPoint()))
    viewer.viewTransformChanged.emit()

    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()


def test_face_name_overlay_poll_refreshes_hover_from_cursor(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()
    overlay._set_hovered_face_id(None)

    QCursor.setPos(overlay.mapToGlobal(_chip_center(overlay).toPoint()))

    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()


def test_face_name_overlay_deactivation_restores_cursor(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()
    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, _chip_center(overlay))
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")
    _assert_ibeam_cursor()

    overlay.set_overlay_active(False)
    qapp.processEvents()

    assert overlay._hovered_face_id is None
    _assert_cursor_restored()


def test_face_name_overlay_chip_stays_above_overlay_after_activation(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.hide()
    overlay.set_annotations([_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    assert overlay._states["face-1"].layout.chip_rect.isEmpty() is False


def test_face_name_overlay_pet_annotation_uses_same_hover_path(qapp) -> None:
    surface, viewer, overlay = _make_overlay(qapp)
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-1",
        source_identity_kind="pet",
        source_identity_id="pet-1",
        canonical_identity_kind="pet",
        canonical_identity_id="pet-1",
        canonical_display_name="Miso",
        box_x=80,
        box_y=80,
        box_w=120,
        box_h=90,
        image_width=420,
        image_height=320,
    )
    overlay.set_annotations([annotation])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    _move_viewer_mouse_to_overlay_point(
        qapp,
        viewer,
        overlay,
        overlay._states["pet:det-1"].layout.chip_rect.center(),
    )
    _wait_until(qapp, lambda: overlay._hovered_face_id == "pet:det-1")
    _assert_ibeam_cursor()

    QTest.mouseMove(surface, QPoint(5, 5))
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)
    _assert_cursor_restored()


@pytest.mark.parametrize("canonical_name", ["Milo", None])
def test_stale_pet_editor_uses_pure_canonical_name(qapp, canonical_name) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-stale",
        source_identity_kind="pet",
        source_identity_id="pet-stale",
        canonical_identity_kind="pet",
        canonical_identity_id="pet-stale",
        canonical_display_name=canonical_name,
        box_x=80,
        box_y=80,
        box_w=120,
        box_h=90,
        image_width=420,
        image_height=320,
        is_stale=True,
        stale_reason="asset_scan_failed_in_current_generation",
    )
    overlay.set_annotations([annotation])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    assert overlay._display_name(annotation).endswith("previous generation")
    overlay._start_editing("pet:det-stale")

    assert overlay._editor is not None
    assert overlay._editor.text() == (canonical_name or "")
    overlay._commit_editing()
    assert (
        overlay._states["pet:det-stale"].annotation.canonical_display_name
        == canonical_name
    )


def test_face_name_overlay_clicking_saved_circle_does_not_toggle_hover(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations([_annotation(face_id="face-1", box_x=80, box_y=80)])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    circle_center = overlay._states["face-1"].layout.circle_rect.center()
    viewer_point = QPointF(viewer.mapFromGlobal(overlay.mapToGlobal(circle_center.toPoint())))
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        viewer_point,
        viewer_point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        viewer_point,
        viewer_point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    drag_move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        viewer_point,
        viewer_point,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(viewer, press_event)
    QApplication.sendEvent(viewer, release_event)
    QApplication.sendEvent(viewer, press_event)
    QApplication.sendEvent(viewer, drag_move_event)
    QApplication.sendEvent(viewer, release_event)
    qapp.processEvents()

    assert overlay._hovered_face_id is None


def test_face_name_overlay_chip_click_starts_edit_without_pinning_hover(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, _chip_center(overlay))
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")

    press_count = viewer.mouse_press_count
    release_count = viewer.mouse_release_count
    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)

    assert overlay._hovered_face_id is None
    assert viewer.mouse_press_count == press_count
    assert viewer.mouse_release_count == release_count


def test_face_name_overlay_foreground_widget_blocks_saved_chip_hit(qapp) -> None:
    surface, _viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    overlay.refresh_view_state()

    blocker = QLabel("front", surface)
    blocker.setGeometry(
        overlay._states["face-1"].layout.chip_rect.toRect().adjusted(-4, -4, 4, 4)
    )
    blocker.raise_()
    blocker.show()
    qapp.processEvents()

    chip_center = _chip_center(overlay)
    blocker_point = blocker.mapFromGlobal(overlay.mapToGlobal(chip_center.toPoint()))

    assert QApplication.widgetAt(overlay.mapToGlobal(chip_center.toPoint())) is blocker

    QTest.mouseMove(blocker, blocker_point)
    qapp.processEvents()
    assert overlay._hovered_face_id is None
    _assert_cursor_restored()

    QTest.mouseClick(blocker, Qt.MouseButton.LeftButton, pos=blocker_point)
    qapp.processEvents()

    assert overlay._editor is None


def test_face_name_overlay_clicking_second_chip_switches_editor(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [
            _annotation(face_id="face-1", box_x=80, box_y=80, display_name="Alice"),
            _annotation(face_id="face-2", box_x=220, box_y=80, display_name="Julie"),
        ]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay, "face-1").toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editing_face_id == "face-1")

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay, "face-2").toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editing_face_id == "face-2")

    assert overlay._editor is not None
    assert overlay._editor.text() == "Julie"


def test_face_name_overlay_empty_click_reaches_viewer(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations(
        [_annotation(face_id="face-1", box_x=80, box_y=80, display_name="Julie")]
    )
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    QTest.mouseClick(viewer, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    qapp.processEvents()

    assert overlay._editor is None
    assert viewer.mouse_press_count == 1
    assert viewer.mouse_release_count == 1


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

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText(entered_text)

    spy = QSignalSpy(overlay.renameSubmitted)
    QTest.keyClick(overlay._editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert _spy_records(spy) == [["person-1", expected_name]]
    assert overlay._states["face-1"].layout.label_text == (expected_name or "unnamed")


def test_face_name_overlay_identity_suggestions_mix_people_and_pets(qapp) -> None:
    _surface, _viewer, overlay = _make_overlay(qapp)

    overlay.set_identity_suggestions(
        [
            RecognitionIdentitySuggestion("person:person-a", "Miso", None, 2),
            RecognitionIdentitySuggestion("pet:pet-a", "Miso", None, 3),
        ]
    )
    overlay.start_manual_face()
    assert overlay._manual_editor is not None

    model = overlay._manual_editor._model
    assert [model.item(row).text() for row in range(model.rowCount())] == ["Miso", "Miso"]
    assert [model.item(row).data(Qt.ItemDataRole.UserRole) for row in range(model.rowCount())] == [
        "person:person-a",
        "pet:pet-a",
    ]
    assert all("pet:" not in model.item(row).text() for row in range(model.rowCount()))
    overlay.clear_manual_face_draft()
    qapp.processEvents()


def test_manual_face_submission_preserves_selected_pet_identity(qapp) -> None:
    _surface, _viewer, overlay = _make_overlay(qapp)
    overlay.set_identity_suggestions(
        [
            RecognitionIdentitySuggestion("person:person-a", "Miso", None, 2),
            RecognitionIdentitySuggestion("pet:pet-a", "Miso", None, 3),
        ]
    )
    overlay.start_manual_face()
    assert overlay._manual_editor is not None
    overlay._manual_editor.setText("Miso")
    assert overlay._manual_editor._completer.setCurrentRow(1)
    overlay._manual_editor._handle_completion_activated("Miso")

    spy = QSignalSpy(overlay.manualFaceSubmitted)
    QTest.keyClick(overlay._manual_editor, Qt.Key.Key_Return)
    qapp.processEvents()

    records = _spy_records(spy)
    assert len(records) == 1
    payload = records[0][0]
    assert payload["identity_key"] == "pet:pet-a"
    assert payload["person_id"] is None
    assert payload["name"] == "Miso"
    overlay.set_manual_face_busy(False)
    overlay.clear_manual_face_draft()
    qapp.processEvents()


def test_manual_face_submission_leaves_ambiguous_typed_name_unlinked(qapp) -> None:
    _surface, _viewer, overlay = _make_overlay(qapp)
    overlay.set_identity_suggestions(
        [
            RecognitionIdentitySuggestion("person:person-a", "Miso", None, 2),
            RecognitionIdentitySuggestion("pet:pet-a", "Miso", None, 3),
        ]
    )
    overlay.start_manual_face()
    assert overlay._manual_editor is not None
    overlay._manual_editor.setText("Miso")

    spy = QSignalSpy(overlay.manualFaceSubmitted)
    QTest.keyClick(overlay._manual_editor, Qt.Key.Key_Return)
    qapp.processEvents()

    records = _spy_records(spy)
    assert len(records) == 1
    payload = records[0][0]
    assert payload["identity_key"] is None
    assert payload["person_id"] is None
    overlay.set_manual_face_busy(False)
    overlay.clear_manual_face_draft()
    qapp.processEvents()


def test_face_name_overlay_escape_and_focus_loss_cancel_edit(qapp) -> None:
    surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_annotations([_annotation(display_name="Bob", box_x=60, box_y=70)])
    overlay.set_overlay_active(True)
    viewer.viewTransformChanged.emit()

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Alice")
    QTest.keyClick(overlay._editor, Qt.Key.Key_Escape)
    _wait_until(qapp, lambda: overlay._editor is None)
    assert overlay._states["face-1"].layout.label_text == "Bob"

    QTest.mouseClick(
        viewer,
        Qt.MouseButton.LeftButton,
        pos=viewer.mapFromGlobal(overlay.mapToGlobal(_chip_center(overlay).toPoint())),
    )
    _wait_until(qapp, lambda: overlay._editor is not None)
    overlay._editor.setText("Charlie")
    surface.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.qWait(10)
    _wait_until(qapp, lambda: overlay._editor is None)
    assert overlay._states["face-1"].layout.label_text == "Bob"


def test_face_name_overlay_stays_visible_even_if_viewer_is_hidden_when_activated(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    viewer.hide()
    overlay.set_annotations([_annotation(display_name="Bob")])
    overlay.set_overlay_active(True)

    assert overlay.isVisible() is True
    assert overlay._states["face-1"].layout.chip_rect.isEmpty() is False


def test_face_name_overlay_waits_for_loaded_image_before_showing_labels(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    viewer.set_has_image_content(False)
    overlay.set_annotations([_annotation(display_name="Bob", box_x=80, box_y=60)])
    overlay.set_overlay_active(True)

    assert overlay.isVisible() is False or overlay._states["face-1"].layout.chip_rect.isEmpty()
    assert overlay._states["face-1"].layout.chip_rect.isEmpty()

    viewer.set_has_image_content(True)
    viewer.viewTransformChanged.emit()

    _wait_until(qapp, lambda: overlay.isVisible())
    assert overlay._states["face-1"].layout.chip_rect.isEmpty() is False
    assert overlay._states["face-1"].layout.chip_rect.topLeft() != QPointF(0, 0)


def test_face_name_overlay_first_frame_ready_refreshes_deferred_labels(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    viewer.set_has_image_content(False)
    overlay.set_annotations([_annotation(display_name="Bob", box_x=80, box_y=60)])
    overlay.set_overlay_active(True)

    assert overlay._states["face-1"].layout.chip_rect.isEmpty()

    viewer.set_has_image_content(True)
    viewer.firstFrameReady.emit()

    _wait_until(qapp, lambda: overlay.isVisible() and not overlay._states["face-1"].layout.chip_rect.isEmpty())
    assert overlay._states["face-1"].layout.chip_rect.topLeft() != QPointF(0, 0)


def test_face_name_overlay_recovers_when_startup_annotation_precedes_texture(qapp) -> None:
    surface = QWidget()
    surface.resize(420, 320)
    viewer = GLImageViewer(surface)
    viewer.setGeometry(0, 0, 420, 320)
    overlay = FaceNameOverlayWidget(surface)
    overlay.setGeometry(surface.rect())
    overlay.set_viewer(viewer)
    surface.show()
    viewer.show()
    overlay.show()
    qapp.processEvents()

    overlay.set_annotations([_annotation(display_name="Bob", box_x=80, box_y=60)])
    overlay.set_overlay_active(True)

    assert overlay._states["face-1"].layout.chip_rect.isEmpty()

    image = QImage(420, 320, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)
    viewer.set_image(image, {}, image_source="startup-still")
    qapp.processEvents()

    _wait_until(qapp, lambda: overlay.isVisible() and not overlay._states["face-1"].layout.chip_rect.isEmpty())
    assert overlay._states["face-1"].layout.face_rect.isEmpty() is False
    assert overlay._states["face-1"].layout.chip_rect.topLeft() != QPointF(0, 0)

    _move_viewer_mouse_to_overlay_point(qapp, viewer, overlay, _chip_center(overlay))
    _wait_until(qapp, lambda: overlay._hovered_face_id == "face-1")

    QTest.mouseMove(surface, QPoint(5, 5))
    _wait_until(qapp, lambda: overlay._hovered_face_id is None)


def test_manual_face_draft_drag_moves_circle_without_falling_through(qapp) -> None:
    _surface, viewer, overlay = _make_overlay(qapp)
    overlay.set_overlay_active(True)
    overlay.start_manual_face()
    qapp.processEvents()

    assert overlay._manual_draft is not None
    assert overlay._manual_editor is not None
    assert overlay._manual_editor.hasFocus() is False
    original_center = QPointF(overlay._manual_draft.center)
    circle_center = overlay._manual_circle_rect().center()
    moved_center = QPointF(circle_center.x() + 36.0, circle_center.y() + 24.0)

    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        circle_center,
        circle_center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        moved_center,
        moved_center,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        moved_center,
        moved_center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(overlay, press_event)
    QApplication.sendEvent(overlay, move_event)
    QApplication.sendEvent(overlay, release_event)
    qapp.processEvents()

    assert overlay._manual_draft.center != original_center
    assert overlay._manual_draft.center.x() > original_center.x()
    assert overlay._manual_draft.center.y() > original_center.y()
    assert overlay._drag_mode is None


def test_manual_face_press_does_not_clear_draft_when_editor_loses_focus(qapp) -> None:
    _surface, _viewer, overlay = _make_overlay(qapp)
    overlay.set_overlay_active(True)
    overlay.start_manual_face()
    qapp.processEvents()

    assert overlay._manual_editor is not None
    assert overlay._manual_draft is not None
    overlay._manual_editor.setText("Alice")
    overlay._manual_editor.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()

    circle_center = overlay._manual_circle_rect().center()
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        circle_center,
        circle_center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(overlay, press_event)
    qapp.processEvents()

    assert overlay._manual_draft is not None
    assert overlay._manual_editor is not None
    assert overlay._drag_mode == "move"

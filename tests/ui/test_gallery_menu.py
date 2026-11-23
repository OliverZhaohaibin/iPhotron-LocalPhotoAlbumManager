"""Regression checks for gallery move destination menu layout and interaction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QStyleFactory, QWidget

from iPhoto.gui.ui.menus.core import MenuContext, populate_menu
from iPhoto.gui.ui.menus.gallery_menu import GalleryMenuHandlers, gallery_action_specs
from iPhoto.gui.ui.menus.style import apply_menu_style
from iPhoto.gui.ui.window_manager import FramelessWindowManager


@pytest.fixture(params=["Windows", "Fusion", "macOS"])
def menu_style(request, qapp):
    if request.param not in QStyleFactory.keys():
        pytest.skip(f"Qt style {request.param} is unavailable")
    previous_style = qapp.style().objectName()
    qapp.setStyle(request.param)
    try:
        yield
    finally:
        qapp.setStyle(previous_style)


def _move_menu(qtbot, tmp_path: Path, *, count: int, themed: bool):
    anchor = QWidget()
    qtbot.addWidget(anchor)
    if themed:
        style_source = SimpleNamespace(
            _rounded_shell=anchor,
            _opaque_color=FramelessWindowManager._opaque_color,
        )
        stylesheet = FramelessWindowManager._build_menu_styles(style_source)[0]
        anchor.get_qmenu_stylesheet = lambda: stylesheet

    moved = Mock()
    def noop(_context: MenuContext) -> None:
        pass

    targets = [(f"Album {index:03}", tmp_path / str(index)) for index in range(count)]
    handlers = GalleryMenuHandlers(
        copy_selection=noop,
        reveal_selection=noop,
        export_selection=noop,
        delete_selection=noop,
        restore_selection=noop,
        paste_into_album=noop,
        open_current_folder=noop,
        set_as_cover=noop,
        set_as_cover_visible=lambda _context: False,
        move_targets=lambda _context: targets,
        move_to_album=moved,
    )
    menu = QMenu(anchor)
    apply_menu_style(menu, anchor)
    context = MenuContext(surface="gallery", selection_kind="assets")
    populate_menu(
        menu,
        context=context,
        action_specs=gallery_action_specs(context, handlers),
        anchor=anchor,
    )
    move_menu = next(action.menu() for action in menu.actions() if action.data() == "move_to")
    return menu, move_menu, moved, targets


@pytest.mark.parametrize("themed", [False, True])
def test_long_move_menu_keeps_all_destinations_accessible(
    menu_style,
    qapp,
    qtbot,
    tmp_path: Path,
    themed: bool,
) -> None:
    parent_menu, menu, moved, targets = _move_menu(qtbot, tmp_path, count=300, themed=themed)
    available = qapp.primaryScreen().availableGeometry()
    menu.popup(available.center())
    qapp.processEvents()
    try:
        assert len(menu.actions()) == len(targets)
        assert available.contains(menu.geometry())
        assert len({menu.actionGeometry(action).x() for action in menu.actions()}) == 1
        last = menu.actions()[-1]
        assert not menu.rect().contains(menu.actionGeometry(last))

        first = menu.actions()[0]
        before_scroll = menu.actionGeometry(first).y()
        point = menu.rect().center()
        wheel = QWheelEvent(
            point,
            menu.mapToGlobal(point),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(menu, wheel)
        qapp.processEvents()
        assert menu.actionGeometry(first).y() < before_scroll

        QTest.keyClick(menu, Qt.Key.Key_End)
        qapp.processEvents()
        assert menu.activeAction() == last
        assert menu.rect().contains(menu.actionGeometry(last))
        QTest.keyClick(menu, Qt.Key.Key_Return)
        moved.assert_called_once_with(targets[-1][1])
    finally:
        menu.hide()
        parent_menu.close()


@pytest.mark.parametrize("corner", ["top_left", "bottom_right"])
def test_move_submenu_shows_every_destination_when_it_fits(
    menu_style,
    qapp,
    qtbot,
    tmp_path: Path,
    corner: str,
) -> None:
    parent_menu, menu, _moved, targets = _move_menu(qtbot, tmp_path, count=10, themed=True)
    available = qapp.primaryScreen().availableGeometry()
    position = available.topLeft() if corner == "top_left" else available.bottomRight()
    parent_menu.popup(position)
    qapp.processEvents()
    try:
        action = menu.menuAction()
        parent_menu.setActiveAction(action)
        QTest.keyClick(parent_menu, Qt.Key.Key_Right)
        qapp.processEvents()
        assert menu.isVisible()
        assert len(menu.actions()) == len(targets)
        assert all(menu.rect().contains(menu.actionGeometry(item)) for item in menu.actions())
    finally:
        parent_menu.close()

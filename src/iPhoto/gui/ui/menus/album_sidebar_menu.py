"""Context menu helpers for the album sidebar widget."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QTreeView,
    QWidget,
)

from ..widgets import dialogs
from ....errors import LibraryError
from ....library.manager import LibraryManager
from ....library.tree import AlbumNode
from ..models.album_tree_model import AlbumTreeItem, AlbumTreeModel, NodeType


def _apply_main_window_menu_style(menu: QMenu, anchor: QWidget | None) -> None:
    """Apply the main window's rounded menu styling to ``menu``."""

    # Keep ``WA_TranslucentBackground`` enabled so the stylesheet-defined border radius can take
    # effect.  ``setAutoFillBackground`` ensures Qt still paints an opaque surface inside the
    # rounded outline that the stylesheet defines.
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setAutoFillBackground(True)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Popup
    )

    main_window = anchor.window() if anchor is not None else None
    if main_window is not None:
        # Adopt the main window palette so the popup maintains consistent colours regardless of
        # which display it appears on.  Emphasising the ``Base`` role keeps the rounded corners
        # opaque and prevents any wallpaper bleed-through.
        menu.setPalette(main_window.palette())
        menu.setBackgroundRole(QPalette.ColorRole.Base)

        accessor = getattr(main_window, "get_qmenu_stylesheet", None)
        stylesheet: str | None
        if callable(accessor):
            stylesheet = accessor()
        else:
            fallback_accessor = getattr(main_window, "menu_stylesheet", None)
            stylesheet = fallback_accessor() if callable(fallback_accessor) else None
        if isinstance(stylesheet, str) and stylesheet:
            menu.setStyleSheet(stylesheet)

    # Clear any inherited graphics effect so previous UI state cannot interfere with the rounded
    # outline or introduce unexpected blending artefacts on the popup surface.
    menu.setGraphicsEffect(None)


def _create_styled_input_dialog(
    parent: QWidget, title: str, label: str, text: str = ""
) -> tuple[str, bool]:
    """Create an input dialog for album operations.

    This helper used to enforce a light theme but now inherits the application-wide
    theme settings to support both Light and Dark modes.

    Args:
        parent: Parent widget for the dialog
        title: Dialog window title
        label: Prompt label text
        text: Default input text value

    Returns:
        Tuple of (user_input_text, accepted_bool)
    """
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)

    # Retrieve the application-wide palette to ensure the dialog respects the active theme.
    # While inheritance usually handles this, we explicitly query the global state to
    # guarantee the correct colors are applied regardless of the parent widget's styling.
    palette = QApplication.palette()
    bg = palette.color(QPalette.ColorRole.Window).name()
    text_col = palette.color(QPalette.ColorRole.WindowText).name()
    base = palette.color(QPalette.ColorRole.Base).name()
    text_input = palette.color(QPalette.ColorRole.Text).name()
    button = palette.color(QPalette.ColorRole.Button).name()
    button_text = palette.color(QPalette.ColorRole.ButtonText).name()

    stylesheet = f"""
        QInputDialog {{
            background-color: {bg};
            color: {text_col};
        }}
        QLabel {{
            color: {text_col};
        }}
        QLineEdit {{
            background-color: {base};
            color: {text_input};
            border: 1px solid {text_col};
            padding: 4px;
        }}
        QPushButton {{
            background-color: {button};
            color: {button_text};
            border: 1px solid {text_col};
            padding: 6px 16px;
            min-width: 60px;
        }}
        QPushButton:hover {{
            background-color: {base};
        }}
    """
    dialog.setStyleSheet(stylesheet)

    # Execute dialog and return result
    accepted = dialog.exec()
    return (dialog.textValue(), accepted == QInputDialog.DialogCode.Accepted)


class AlbumSidebarContextMenu(QMenu):
    """Context menu providing album management actions."""

    def __init__(
        self,
        parent: QWidget,
        tree: QTreeView,
        model: AlbumTreeModel,
        library: LibraryManager,
        item: AlbumTreeItem,
        set_pending_selection: Callable[[Path | None], None],
        on_bind_library: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._tree = tree
        self._model = model
        self._library = library
        self._item = item
        self._set_pending_selection = set_pending_selection
        self._on_bind_library = on_bind_library
        # Ensure the popup renders with rounded opaque styling by reusing the palette-aware rules
        # published by the main window whenever they are available.
        _apply_main_window_menu_style(self, parent)
        self._build_menu()

    def _build_menu(self) -> None:
        if self._item.node_type in {NodeType.HEADER, NodeType.SECTION}:
            self.addAction("New Album…", self._prompt_new_album)
        if self._item.node_type == NodeType.ALBUM:
            self.addAction(
                "New Sub-Album…",
                lambda: self._prompt_new_album(self._item),
            )
            self.addAction(
                "Rename Album…",
                lambda: self._prompt_rename_album(self._item),
            )
            self.addSeparator()
            self.addAction(
                "Show in File Manager",
                lambda: self._reveal_path(self._item.album),
            )
        if self._item.node_type == NodeType.SUBALBUM:
            self.addAction(
                "Rename Album…",
                lambda: self._prompt_rename_album(self._item),
            )
            self.addSeparator()
            self.addAction(
                "Show in File Manager",
                lambda: self._reveal_path(self._item.album),
            )
        if self._item.node_type == NodeType.ACTION:
            self.addAction("Set Basic Library…", self._on_bind_library)

    def _prompt_new_album(self, parent_item: AlbumTreeItem | None = None) -> None:
        base_item = parent_item
        if base_item is None:
            index = self._tree.currentIndex()
            base_item = self._model.item_from_index(index)

        if base_item is None:
            return

        name, ok = _create_styled_input_dialog(self.parentWidget(), "New Album", "Album name:")
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self.parentWidget(), "Album name cannot be empty.")
            return
        try:
            if base_item.node_type == NodeType.ALBUM and base_item.album is not None:
                node = self._library.create_subalbum(base_item.album, target_name)
            else:
                node = self._library.create_album(target_name)
        except LibraryError as exc:  # pragma: no cover - GUI feedback
            dialogs.show_warning(self.parentWidget(), str(exc))
            return
        self._set_pending_selection(node.path)

    def _prompt_rename_album(self, item: AlbumTreeItem) -> None:
        if item.album is None:
            return
        current_title = item.album.title
        name, ok = _create_styled_input_dialog(
            self.parentWidget(),
            "Rename Album",
            "New album name:",
            text=current_title,
        )
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self.parentWidget(), "Album name cannot be empty.")
            return
        try:
            self._library.rename_album(item.album, target_name)
        except LibraryError as exc:  # pragma: no cover - GUI feedback
            dialogs.show_warning(self.parentWidget(), str(exc))
            return
        self._set_pending_selection(item.album.path.parent / target_name)

    @staticmethod
    def _reveal_path(album: AlbumNode | None) -> None:
        if album is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(album.path)))


def show_context_menu(
    parent: QWidget,
    point: QPoint,
    tree: QTreeView,
    model: AlbumTreeModel,
    library: LibraryManager,
    set_pending_selection: Callable[[Path | None], None],
    on_bind_library: Callable[[], None],
) -> None:
    """Display the context menu for the album sidebar."""

    index = tree.indexAt(point)
    global_pos = tree.viewport().mapToGlobal(point)

    if not index.isValid():
        menu = QMenu(parent)
        _apply_main_window_menu_style(menu, parent)

        menu.addAction("Set Basic Library…", on_bind_library)
        menu.exec(global_pos)
        return

    item = model.item_from_index(index)
    if item is None:
        return

    menu = AlbumSidebarContextMenu(
        parent,
        tree,
        model,
        library,
        item,
        set_pending_selection,
        on_bind_library,
    )
    if not menu.isEmpty():
        menu.exec(global_pos)


__all__ = ["AlbumSidebarContextMenu", "show_context_menu"]

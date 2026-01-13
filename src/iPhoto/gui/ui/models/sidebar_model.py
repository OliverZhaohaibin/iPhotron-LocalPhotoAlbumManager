"""QML-compatible model for the sidebar tree view."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)

from ....library.manager import LibraryManager
from ....library.tree import AlbumNode
from ..icon import icon_path as get_icon_path


class NodeType(IntEnum):
    """Types of nodes available in the sidebar tree."""
    
    ROOT = 0
    HEADER = 1
    SECTION = 2
    STATIC = 3
    ACTION = 4
    ALBUM = 5
    SUBALBUM = 6
    SEPARATOR = 7


class SidebarRoles(IntEnum):
    """Custom roles for the sidebar model exposed to QML."""
    
    TitleRole = Qt.ItemDataRole.UserRole + 1
    NodeTypeRole = Qt.ItemDataRole.UserRole + 2
    DepthRole = Qt.ItemDataRole.UserRole + 3
    IsExpandedRole = Qt.ItemDataRole.UserRole + 4
    HasChildrenRole = Qt.ItemDataRole.UserRole + 5
    IsSelectableRole = Qt.ItemDataRole.UserRole + 6
    IconNameRole = Qt.ItemDataRole.UserRole + 7
    FilePathRole = Qt.ItemDataRole.UserRole + 8
    IconPathRole = Qt.ItemDataRole.UserRole + 9  # Full path to SVG icon file


@dataclass
class SidebarItem:
    """Internal representation of a sidebar item."""
    
    title: str
    node_type: NodeType
    depth: int = 0
    is_expanded: bool = False
    icon_name: str = ""
    album: AlbumNode | None = None
    children: list[SidebarItem] = field(default_factory=list)


# Static nodes that appear in every library
STATIC_NODES: tuple[str, ...] = (
    "All Photos",
    "Videos", 
    "Live Photos",
    "Favorites",
    "Location",
)

TRAILING_STATIC_NODES: tuple[str, ...] = ("Recently Deleted",)

# Icon mapping for static nodes
STATIC_ICON_MAP: dict[str, str] = {
    "all photos": "photo.on.rectangle",
    "videos": "video",
    "live photos": "livephoto",
    "favorites": "suit.heart",
    "location": "mappin.and.ellipse",
    "recently deleted": "trash",
}


class SidebarModel(QAbstractListModel):
    """QML-compatible list model for the sidebar tree.
    
    This model flattens the tree structure into a list for QML ListView
    while tracking expansion state to show/hide children.
    """
    
    # Qt Signals use camelCase by convention (noqa: N815)
    albumSelected = Signal(Path)  # noqa: N815
    allPhotosSelected = Signal()  # noqa: N815
    staticNodeSelected = Signal(str)  # noqa: N815
    bindLibraryRequested = Signal()  # noqa: N815
    
    def __init__(self, library: LibraryManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._items: list[SidebarItem] = []
        self._flat_items: list[SidebarItem] = []  # Flattened view of visible items
        self._expansion_state: dict[str, bool] = {}  # Track expansion by title/path
        
        # Connect to library updates
        self._library.treeUpdated.connect(self.refresh)
        
        # Initial refresh
        self.refresh()
    
    def roleNames(self) -> dict[int, bytes]:  # noqa: N802  # Qt override
        """Return the role names for QML property binding."""
        return {
            SidebarRoles.TitleRole: b"title",
            SidebarRoles.NodeTypeRole: b"nodeType",
            SidebarRoles.DepthRole: b"depth",
            SidebarRoles.IsExpandedRole: b"isExpanded",
            SidebarRoles.HasChildrenRole: b"hasChildren",
            SidebarRoles.IsSelectableRole: b"isSelectable",
            SidebarRoles.IconNameRole: b"iconName",
            SidebarRoles.FilePathRole: b"filePath",
            SidebarRoles.IconPathRole: b"iconPath",
        }
    
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802  # Qt override
        """Return the number of visible items."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._flat_items)
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Return data for the given role at the specified index."""
        row = index.row()
        if not index.isValid() or row < 0 or row >= len(self._flat_items):
            return None
        
        item = self._flat_items[row]
        
        if role == Qt.ItemDataRole.DisplayRole or role == SidebarRoles.TitleRole:
            return item.title
        elif role == SidebarRoles.NodeTypeRole:
            return int(item.node_type)
        elif role == SidebarRoles.DepthRole:
            return item.depth
        elif role == SidebarRoles.IsExpandedRole:
            return item.is_expanded
        elif role == SidebarRoles.HasChildrenRole:
            return len(item.children) > 0
        elif role == SidebarRoles.IsSelectableRole:
            return item.node_type not in {NodeType.SECTION, NodeType.SEPARATOR, NodeType.ROOT}
        elif role == SidebarRoles.IconNameRole:
            return self._icon_for_item(item)
        elif role == SidebarRoles.FilePathRole:
            if item.album is not None:
                return str(item.album.path)
            return ""
        elif role == SidebarRoles.IconPathRole:
            return self._icon_path_for_item(item)
        
        return None
    
    @Slot()
    def refresh(self) -> None:
        """Rebuild the model from the current state of the library."""
        self.beginResetModel()
        self._items = []
        self._build_tree()
        self._flatten_visible_items()
        self.endResetModel()
    
    @Slot(int)
    def select_item(self, row: int) -> None:
        """Handle selection of an item at the given row."""
        if row < 0 or row >= len(self._flat_items):
            return
        
        item = self._flat_items[row]
        node_type = item.node_type
        
        if node_type == NodeType.ACTION:
            self.bindLibraryRequested.emit()
            return
        
        if node_type == NodeType.HEADER:
            if item.title == "Albums":
                self.staticNodeSelected.emit("Albums")
            return
        
        if node_type == NodeType.STATIC:
            # Require library to be bound before selecting static nodes
            if not self._has_library():
                self.bindLibraryRequested.emit()
                return
            if item.title == "All Photos":
                self.allPhotosSelected.emit()
            else:
                self.staticNodeSelected.emit(item.title)
            return
        
        if item.album is not None:
            self.albumSelected.emit(item.album.path)
    
    def _has_library(self) -> bool:
        """Check if a library root is currently bound."""
        return self._library.root() is not None
    
    @Slot(int)
    def toggle_expansion(self, row: int) -> None:
        """Toggle the expansion state of an item."""
        if row < 0 or row >= len(self._flat_items):
            return
        
        item = self._flat_items[row]
        if not item.children:
            return
        
        # Toggle expansion
        item.is_expanded = not item.is_expanded
        
        # Store expansion state
        key = self._expansion_key(item)
        self._expansion_state[key] = item.is_expanded
        
        # Rebuild flattened list
        self.beginResetModel()
        self._flatten_visible_items()
        self.endResetModel()
    
    def _build_tree(self) -> None:
        """Build the tree structure from the library."""
        if not self._has_library():
            # Show placeholder when no library is bound
            placeholder = SidebarItem(
                title="Bind Basic Library…",
                node_type=NodeType.ACTION,
                depth=0,
                icon_name="plus.circle",
            )
            self._items.append(placeholder)
            return
        
        # Basic Library header
        header = SidebarItem(
            title="Basic Library",
            node_type=NodeType.HEADER,
            depth=0,
            is_expanded=self._get_expansion_state("header:Basic Library", True),
            icon_name="photo.on.rectangle",
        )
        
        # Add static nodes under header
        for title in STATIC_NODES:
            static_item = SidebarItem(
                title=title,
                node_type=NodeType.STATIC,
                depth=1,
                icon_name=STATIC_ICON_MAP.get(title.casefold(), ""),
            )
            header.children.append(static_item)
        
        self._items.append(header)
        
        # Separator
        self._items.append(SidebarItem(
            title="──────────",
            node_type=NodeType.SEPARATOR,
            depth=0,
        ))
        
        # Albums header
        albums_header = SidebarItem(
            title="Albums",
            node_type=NodeType.HEADER,
            depth=0,
            is_expanded=self._get_expansion_state("header:Albums", True),
            icon_name="folder",
        )
        
        # Add albums
        for album in self._library.list_albums():
            album_item = SidebarItem(
                title=album.title,
                node_type=NodeType.ALBUM,
                depth=1,
                is_expanded=self._get_expansion_state(f"album:{album.path}", False),
                icon_name="rectangle.stack",
                album=album,
            )
            
            # Add subalbums
            for child in self._library.list_children(album):
                child_item = SidebarItem(
                    title=child.title,
                    node_type=NodeType.SUBALBUM,
                    depth=2,
                    icon_name="rectangle.stack",
                    album=child,
                )
                album_item.children.append(child_item)
            
            albums_header.children.append(album_item)
        
        self._items.append(albums_header)
        
        # Trailing separator
        self._items.append(SidebarItem(
            title="──────────",
            node_type=NodeType.SEPARATOR,
            depth=0,
        ))
        
        # Trailing static nodes
        for title in TRAILING_STATIC_NODES:
            static_item = SidebarItem(
                title=title,
                node_type=NodeType.STATIC,
                depth=0,
                icon_name=STATIC_ICON_MAP.get(title.casefold(), ""),
            )
            self._items.append(static_item)
    
    def _flatten_visible_items(self) -> None:
        """Flatten the tree into a list of visible items."""
        self._flat_items = []
        
        def _add_items(items: list[SidebarItem]) -> None:
            for item in items:
                self._flat_items.append(item)
                if item.is_expanded and item.children:
                    _add_items(item.children)
        
        _add_items(self._items)
    
    def _get_expansion_state(self, key: str, default: bool = False) -> bool:
        """Get the expansion state for a given key."""
        return self._expansion_state.get(key, default)
    
    def _expansion_key(self, item: SidebarItem) -> str:
        """Generate a unique key for tracking expansion state."""
        if item.node_type == NodeType.HEADER:
            return f"header:{item.title}"
        elif item.album is not None:
            return f"album:{item.album.path}"
        return f"item:{item.title}"
    
    def _icon_for_item(self, item: SidebarItem) -> str:
        """Return the icon name for an item."""
        if item.icon_name:
            return item.icon_name
        
        if item.node_type == NodeType.ACTION:
            return "plus.circle"
        elif item.node_type == NodeType.STATIC:
            return STATIC_ICON_MAP.get(item.title.casefold(), "")
        elif item.node_type in {NodeType.ALBUM, NodeType.SUBALBUM}:
            return "rectangle.stack"
        elif item.node_type == NodeType.HEADER:
            return "photo.on.rectangle"
        
        return ""
    
    def _icon_path_for_item(self, item: SidebarItem) -> str:
        """Return the full path to the SVG icon for an item."""
        icon_name = self._icon_for_item(item)
        if not icon_name:
            return ""
        
        path = get_icon_path(icon_name)
        if path.exists():
            return str(path)
        return ""


__all__ = ["NodeType", "SidebarItem", "SidebarModel", "SidebarRoles"]

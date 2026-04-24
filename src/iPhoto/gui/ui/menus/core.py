"""Shared context and builders for popup menus."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Sequence

from PySide6.QtWidgets import QMenu, QWidget

from iPhoto.application.dtos import AssetDTO

from .style import apply_menu_style

MenuSurface = Literal[
    "gallery",
    "albums_dashboard",
    "album_sidebar",
    "people_dashboard",
    "info_panel",
]
SelectionKind = Literal["empty", "assets"]
EntityKind = Literal["album", "person", "group", None]


@dataclass(frozen=True)
class MenuContext:
    """Normalized menu state shared across UI surfaces."""

    surface: MenuSurface
    selection_kind: SelectionKind
    selected_assets: tuple[AssetDTO, ...] = ()
    gallery_section: str | None = None
    entity_kind: EntityKind = None
    entity_id: str | None = None
    active_root: Path | None = None
    is_recently_deleted: bool = False
    is_cluster_gallery: bool = False

    @property
    def selected_asset(self) -> AssetDTO | None:
        if len(self.selected_assets) != 1:
            return None
        return self.selected_assets[0]

    def with_selection(
        self,
        *,
        selection_kind: SelectionKind,
        selected_assets: Sequence[AssetDTO],
    ) -> "MenuContext":
        return MenuContext(
            surface=self.surface,
            selection_kind=selection_kind,
            selected_assets=tuple(selected_assets),
            gallery_section=self.gallery_section,
            entity_kind=self.entity_kind,
            entity_id=self.entity_id,
            active_root=self.active_root,
            is_recently_deleted=self.is_recently_deleted,
            is_cluster_gallery=self.is_cluster_gallery,
        )


_ContextPredicate = Callable[[MenuContext], bool]
_ContextHandler = Callable[[MenuContext], None]


@dataclass(frozen=True)
class MenuActionSpec:
    """Declarative menu item description."""

    action_id: str
    label: str
    on_trigger: _ContextHandler | None = None
    is_visible: _ContextPredicate = lambda _context: True
    is_enabled: _ContextPredicate = lambda _context: True
    children: tuple["MenuActionSpec", ...] = field(default_factory=tuple)
    separator_before: bool = False


def populate_menu(
    menu: QMenu,
    *,
    context: MenuContext,
    action_specs: Sequence[MenuActionSpec],
    anchor: QWidget | None = None,
) -> int:
    """Populate ``menu`` from ``action_specs`` and return the visible item count."""

    visible_count = 0
    for spec in action_specs:
        if not spec.is_visible(context):
            continue
        if spec.separator_before and visible_count > 0:
            menu.addSeparator()
        if spec.children:
            submenu = menu.addMenu(spec.label)
            apply_menu_style(submenu, anchor)
            child_count = populate_menu(
                submenu,
                context=context,
                action_specs=spec.children,
                anchor=anchor,
            )
            submenu.menuAction().setVisible(child_count > 0)
            submenu.setEnabled(child_count > 0 and spec.is_enabled(context))
            if child_count > 0:
                visible_count += 1
            continue
        action = menu.addAction(spec.label)
        action.setEnabled(spec.is_enabled(context))
        if spec.on_trigger is not None:
            action.triggered.connect(lambda _checked=False, handler=spec.on_trigger: handler(context))
        visible_count += 1
    return visible_count

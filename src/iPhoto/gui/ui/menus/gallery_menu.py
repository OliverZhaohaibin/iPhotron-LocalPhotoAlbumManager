"""Gallery-specific popup menu action registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core import MenuActionSpec, MenuContext


@dataclass(frozen=True)
class GalleryMenuHandlers:
    copy_selection: Callable[[MenuContext], None]
    reveal_selection: Callable[[MenuContext], None]
    export_selection: Callable[[MenuContext], None]
    delete_selection: Callable[[MenuContext], None]
    restore_selection: Callable[[MenuContext], None]
    paste_into_album: Callable[[MenuContext], None]
    open_current_folder: Callable[[MenuContext], None]
    set_as_cover: Callable[[MenuContext], None]
    set_as_cover_visible: Callable[[MenuContext], bool]
    move_targets: Callable[[MenuContext], list[tuple[str, Path]]]
    move_to_album: Callable[[Path], None]


def gallery_action_specs(
    context: MenuContext,
    handlers: GalleryMenuHandlers,
) -> list[MenuActionSpec]:
    """Return the action registry for the gallery surface."""

    selected_actions = [
        MenuActionSpec(
            action_id="copy",
            label="Copy",
            on_trigger=handlers.copy_selection,
            is_visible=_has_selection,
        ),
        MenuActionSpec(
            action_id="reveal",
            label="Reveal in File Manager",
            on_trigger=handlers.reveal_selection,
            is_visible=_has_selection,
        ),
        MenuActionSpec(
            action_id="export",
            label="Export",
            on_trigger=handlers.export_selection,
            is_visible=_has_selection,
        ),
        MenuActionSpec(
            action_id="set_as_cover",
            label="Set as Cover",
            on_trigger=handlers.set_as_cover,
            is_visible=handlers.set_as_cover_visible,
        ),
        MenuActionSpec(
            action_id="move_to",
            label="Move to",
            is_visible=lambda ctx: _has_selection(ctx) and not ctx.is_recently_deleted,
            children=tuple(
                MenuActionSpec(
                    action_id=f"move_to:{path}",
                    label=label,
                    on_trigger=lambda ctx, target=path: handlers.move_to_album(target),
                )
                for label, path in (
                    handlers.move_targets(context)
                    if _has_selection(context) and not context.is_recently_deleted
                    else []
                )
            ),
        ),
        MenuActionSpec(
            action_id="delete",
            label="Delete",
            on_trigger=handlers.delete_selection,
            is_visible=lambda ctx: _has_selection(ctx) and not ctx.is_recently_deleted,
        ),
        MenuActionSpec(
            action_id="restore",
            label="Restore",
            on_trigger=handlers.restore_selection,
            is_visible=lambda ctx: _has_selection(ctx) and ctx.is_recently_deleted,
        ),
    ]
    empty_actions = [
        MenuActionSpec(
            action_id="paste",
            label="Paste",
            on_trigger=handlers.paste_into_album,
            is_visible=lambda ctx: ctx.selection_kind == "empty",
        ),
        MenuActionSpec(
            action_id="open_folder_location",
            label="Open Folder Location",
            on_trigger=handlers.open_current_folder,
            is_visible=lambda ctx: ctx.selection_kind == "empty",
        ),
    ]
    return selected_actions + empty_actions


def _has_selection(context: MenuContext) -> bool:
    return context.selection_kind == "assets"

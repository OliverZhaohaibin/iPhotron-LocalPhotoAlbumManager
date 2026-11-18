# Project Popup And Menu Guardrails

This note captures a UI regression class that surfaced during the People
dashboard rollback window between commits
`f7b865980293975d22f351cd29d6c333c7b4c296` and
`1e36ab8f837dcf1253f4e7ec27b315b77efd12fc`.
Those commits explain the historical motivation; the rules below are the
current presentation contract for popups, context menus, and People pickers.

The core lesson is project-wide: warning and information popups should prefer
the application's own popup implementation and theme plumbing instead of
falling back to native/system-styled `QMessageBox` surfaces.

Treat the items below as product contracts, not optional UI polish.

---

## Project-wide popup contract

- Do not use raw `QMessageBox` for routine in-app information or warning
  popups when the project already provides a shared popup implementation.
- Use `dialogs.show_information()` / `dialogs.show_warning()` so the popup goes
  through `InformationPopup` and inherits the app theme and project styling.
- Prefer shared project popup widgets over one-off native dialogs whenever the
  popup is part of normal product UX instead of a platform-level file/system
  prompt.
- This rule also applies in Light Mode. The popup must inherit the active app
  theme even if the OS color scheme is dark.
- Popup positioning should be relative to the hosting top-level application
  window, not a nested child widget, so the popup remains visually centered in
  the main UI.

## People & Pets dashboard-specific regression contract

The regression that exposed this issue happened in the People dashboard, so the
following rules remain important concrete examples of the project-wide popup
contract.

- Use `MergeConfirmDialog` for People/Pets dashboard confirmation flows that should
  visually match the merge confirmation popup.

## People visibility contract

- Each People card must expose a right-click `Hide` / `Unhide` action.
- Each Pet card must expose the corresponding hide/unhide action.
- Hidden state must persist in the owning People or Pets state database.
- The `View` menu must expose `Show Hidden People`, backed by
  `ui.show_hidden_people`.
- When `Show Hidden People` is disabled, hidden people must be excluded from the
  People dashboard; the same dashboard filter includes/excludes hidden pets.
  Group cards must stay in sync with the filtered result.

## Merge safety contract

- People in different hidden states must never be merged.
- Pets or cross-kind person/pet identities in different hidden states must not
  be merged or redirected into one another.
- The repository/service layer must reject the merge even if the UI misses the
  guard.
- The People UI must also block the action early and show the project-styled
  warning popup.
- Keep the warning copy aligned with the historical behavior:
  - Title: `Cannot Merge People`
  - Body:
    `People in hidden and visible states cannot be merged. Please make both People cards hidden or visible first.`

## Group disband contract

- Group cards must expose a right-click `Disband Group` action.
- Disbanding a group must remove only the group card/container. It must not
  delete the underlying people or their photos.
- Pinned groups must not be disbanded.
- Attempting to disband a pinned group must show a project-styled warning popup,
  not a native/system-styled modal.
- The `Hide Person` and `Disband Group` confirmation popups must continue to use
  the same shared confirmation dialog family as merge confirmation, so the
  window styling, palette inheritance, and button treatment stay consistent.

## Context Menu Guardrails

Qt context menus must use the project menu styling instead of bare `QMenu`
instances. The main window uses translucent rounded chrome, and unstyled menus
can inherit that translucency and render with a transparent background.

- For sidebar and album-related menus, call
  `_apply_main_window_menu_style(menu, parent)` from
  `iPhoto.gui.ui.menus.album_sidebar_menu` before adding or executing actions.
- If a menu uses a local stylesheet instead, set
  `menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)` and apply
  an explicit opaque `QMenu { background-color: ... }` rule.
- Do not create and execute a naked `QMenu(self)` from widgets such as
  dashboards, cards, sidebars, or popups.
- When adding a new right-click surface, add or update a focused GUI test that
  verifies the menu is styled and that important actions are present.

### Unified Right-Click Menu Rules

The app now treats sidebar, dashboard, and gallery context menus as one shared
interaction system. When you add or change a right-click entry, keep these
rules aligned across surfaces:

- Use `MenuContext` + `populate_menu()` for declarative menus whenever the
  surface already participates in the shared menu system.
- Right-clicking an asset in gallery must first sync selection to the clicked
  row before computing menu visibility, so selection-scoped actions operate on
  the intended asset.
- Album-cover actions must resolve paths relative to the active album root
  before calling `facade.set_cover(...)`. Do not assume `AssetDTO.rel_path`
  already matches the current album root.
- `Rename…` is the canonical label for rename actions. Use the same ellipsis
  style and the same empty-name validation across sidebar and pinned-item menus.
- Pinned-item rename is a sidebar-local alias. Persist it through
  `PinnedItemsService` instead of mutating the underlying album/person/group
  entity name.
- `Pin`/`Unpin` and `Rename…` should stay adjacent on sidebar-driven menus so
  users can manage the same entity without hunting across different surfaces.
- Any regression around menu visibility or per-surface action parity needs a
  targeted test in the menu/controller/widget layer that owns that surface.

## People UI Conventions

### Reusable person-picker popup

The canonical picker for choosing one or more People cards is
`GroupPeopleDialog` in
`src/iPhoto/gui/ui/widgets/people_dashboard_dialogs.py`.

Use this dialog for People-card selection flows instead of creating ad-hoc
`QInputDialog` or combo-box popups. Current uses include:

- `New Group` from the People dashboard
- `Merge Into...` from a People card context menu
- `Choose Someone Else...` from the Info panel face actions

When reusing it:

- pass `dark_mode=` from the hosting window/theme context explicitly when the
  caller is not the People dashboard itself
- use `min_selection=1` and `max_selection=1` for single-target pickers
- customize `title_text`, `prompt_text`, and `confirm_text` per workflow
- keep multi-select behavior only for true grouping flows

## Regression tests to keep

Keep coverage for the following behaviors in
`tests/gui/widgets/test_people_dashboard_widget.py`,
`tests/test_people_repository.py`,
`tests/test_people_service.py`, and
`tests/test_information_popup.py`:

- People card menu exposes `Hide` / `Unhide`.
- Pet card menu exposes the matching visibility and management actions.
- Hidden people can be included again with `Show Hidden People`.
- Merge is blocked when hidden state differs.
- Group menu exposes `Disband Group`.
- Pinned groups cannot be disbanded and show a warning popup.
- Hide/disband confirmation flows use the shared People dialog implementation.
- `show_warning()` routes through `InformationPopup`.
- Light-theme popup rendering follows the app theme rather than the OS theme.
- The themed popup is centered on the top-level window.

When changing popup plumbing, theme detection, People/Pets filtering, identity
merges, or group actions, run these tests before merging.
Menu, cover, move, and picker changes also use the focused tests for their
surface, including `tests/ui/controllers/test_context_menu_cover.py`,
`tests/ui/controllers/test_context_menu_operations.py`, and
`tests/test_info_panel.py`.

# Project Popup Guardrails

This note captures a UI regression class that surfaced during the People
dashboard rollback window between commits
`f7b865980293975d22f351cd29d6c333c7b4c296` and
`1e36ab8f837dcf1253f4e7ec27b315b77efd12fc`.

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

## People dashboard-specific regression contract

The regression that exposed this issue happened in the People dashboard, so the
following rules remain important concrete examples of the project-wide popup
contract.

- Use `MergeConfirmDialog` for People dashboard confirmation flows that should
  visually match the merge confirmation popup.

## People visibility contract

- Each People card must expose a right-click `Hide` / `Unhide` action.
- Hidden state must persist in the People state database.
- The `View` menu must expose `Show Hidden People`, backed by
  `ui.show_hidden_people`.
- When `Show Hidden People` is disabled, hidden people must be excluded from the
  People dashboard. Group cards must stay in sync with the filtered result.

## Merge safety contract

- People in different hidden states must never be merged.
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

## Regression tests to keep

Keep coverage for the following behaviors in
`tests/gui/widgets/test_people_dashboard_widget.py`,
`tests/test_people_repository.py`,
`tests/test_people_service.py`, and
`tests/test_information_popup.py`:

- People card menu exposes `Hide` / `Unhide`.
- Hidden people can be included again with `Show Hidden People`.
- Merge is blocked when hidden state differs.
- Group menu exposes `Disband Group`.
- Pinned groups cannot be disbanded and show a warning popup.
- Hide/disband confirmation flows use the shared People dialog implementation.
- `show_warning()` routes through `InformationPopup`.
- Light-theme popup rendering follows the app theme rather than the OS theme.
- The themed popup is centered on the top-level window.

When changing popup plumbing, theme detection, People filtering, or group
actions, run these tests before merging.

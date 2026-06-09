# i18n UI Text Guardrails

This note captures the long-term internationalization contracts for GUI text.
Treat these as regression rules when adding or changing user-visible UI.

## Translation contract

- Wrap new user-visible GUI text with `iPhoto.gui.i18n.tr(context, source_text)`
  or `QCoreApplication.translate(...)` using a stable context.
- Keep source strings in English. German and Simplified Chinese translations
  live in `src/iPhoto/resources/i18n/iPhoto_de.ts` and
  `src/iPhoto/resources/i18n/iPhoto_zh_CN.ts`.
- Do not translate user data or raw technical values: file names, paths, people
  names, place-search results, camera/lens/codec values, backend names,
  environment variables, and exception details should remain original data.
- Insert dynamic values with named placeholders such as `{filename}` or
  `{count}`. Do not build translation source strings with f-strings.
- Use `iPhoto.gui.i18n.formatters` for UI-facing dates, numbers, decimals, and
  file sizes so formatting follows the effective UI locale.

## Runtime refresh contract

- Long-lived widgets that own labels, tooltips, placeholder text, menus, or
  status strings need a `retranslate_ui()` method.
- `MainWindow.retranslate_ui_tree()` is the root refresh path after
  `TranslationManager.languageChanged`; child widgets should refresh existing
  controls instead of rebuilding library/session state.
- Menu and action behavior must be driven by stable ids, callbacks, node types,
  or `QAction.data()`. Never use translated action text as a command key.
- For combo boxes and segmented controls, store stable mode ids separately from
  translated display text.

## Resource workflow

After adding or changing translatable text, update and compile resources:

```bash
bash scripts/i18n_extract.sh
bash scripts/i18n_compile.sh
```

`scripts/i18n_extract.sh` scans `src/iPhoto/gui` and `src/maps`, preserves
existing translations, and restores the previous `.ts` files if extraction
would produce an empty active message set.

## Regression checks

Run the i18n checks before merging UI text changes:

```bash
python tools/check_i18n_strings.py src/iPhoto/gui src/maps
.venv/bin/python -m pytest tests/architecture/test_i18n_string_gate.py tests/test_i18n_extract_tool.py tests/test_i18n_translation_manager.py -q
```

Also run focused widget tests for the surface you touched. The static gate only
catches high-risk direct literals; it does not prove every existing state string
refreshes correctly after a runtime language switch.

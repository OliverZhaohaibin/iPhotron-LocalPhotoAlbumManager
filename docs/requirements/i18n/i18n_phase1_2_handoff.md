# iPhotron 国际化阶段 1-3 交接文档

> 日期：2026-06-08
> 状态：阶段 1-2 已实现；阶段 3 已完成 `InfoPanel`、People Dashboard、相册导航面、gallery context menu、detail/player 控制区、detail/edit 收尾控件、share/export 首批反馈、face overlay、edit sidebar 首批控件和 edit sidebar 剩余 Apple Photos 对齐控件迁移；Python-aware 提取工具已补齐；待后续阶段继续迁移地图入口和其余业务页面
> 对应指南：`docs/requirements/i18n/i18n_multilingual_architecture_guide.md`

---

## 1. 当前完成范围

前序实施覆盖架构指南中的阶段 1「基础设施」和阶段 2「核心壳层 UI」。目标是先把国际化作为运行时服务接入应用，并让桌面主窗口的基础菜单、标题栏、核心操作和基础提示可以在运行时切换语言。

本轮继续推进阶段 3「主要业务页面」，已完成 `InfoPanel`、People Dashboard、相册导航面、gallery context menu、detail/player 控制区、detail/edit 收尾控件、share/export 首批反馈、face overlay、edit sidebar 首批控件和 edit sidebar 剩余 Apple Photos 对齐控件迁移，并补齐首个 locale-aware formatter helper。

已完成内容：

- 新增 `src/iPhoto/gui/i18n/`：
  - `language.py`：定义 `LanguageInfo`。
  - `translation_manager.py`：负责读取语言元数据、解析系统语言、加载/卸载 `QTranslator`、监听 `ui.language` 设置变化并发出 `languageChanged`。
  - `formatters.py`：使用当前 UI 有效语言对应的 `QLocale` 格式化日期时间、整数、小数和文件大小。
  - `__init__.py`：导出 `TranslationManager`、`LanguageInfo`、`formatters` 和 `tr()`。
- 新增包内翻译资源目录 `src/iPhoto/resources/i18n/`：
  - `languages.json`
  - `iPhoto_de.ts`
  - `iPhoto_de.qm`
  - `iPhoto_zh_CN.ts`
  - `iPhoto_zh_CN.qm`
- 扩展 settings：
  - `DEFAULT_SETTINGS["ui"]["language"] = "system"`
  - schema 允许 `system`、`de`、`zh-CN`
  - 旧 settings 文件可继续通过 `merge_with_defaults()` 自动补齐。
- `RuntimeContext` 已持有 `translation`，创建顺序为：
  - settings
  - translation
  - theme
  - 其他运行时服务
- `RuntimeEntryContract` 和 legacy `AppContext` 已补充 `translation`。
- `pyproject.toml` package data 已包含 `.ts` 和 `.qm`。
- 主窗口新增语言菜单：
  - `Settings > Language > System`
  - `Settings > Language > Deutsch`
  - `Settings > Language > 简体中文`
- `MainCoordinator` 已将语言 action 写入 `settings.set("ui.language", code)`，由 `TranslationManager` 监听设置变更并应用翻译。
- `MainWindow` 新增 `retranslate_ui_tree()`，并在语言变化后通过 `QTimer.singleShot(0, ...)` 延迟刷新，避免用户从菜单切换语言时 Qt 正在关闭 popup menu 导致 `QMenu` 底层对象失效。
- `MainHeaderWidget` 已显式创建并持有 `QMenu`，避免 PySide ownership 导致语言切换时访问已删除的菜单对象。
- `TranslationManager.apply_language()` 已同步更新 `formatters` 使用的当前 `QLocale`：
  - `de` 使用 `de_DE`。
  - `zh-CN` 使用 `zh_CN`。
  - 英文兜底使用 `en_US`。
- `MenuActionSpec` 生成的 `QAction` 和 submenu action 已写入稳定 `action_id` 到 `action.data()`，避免翻译后的 label 被业务逻辑用作命令判断。
- 已迁移的核心 UI 文案包括：
  - 主菜单和设置子菜单。
  - 语言、外观、导出、滚轮、分享相关菜单项。
  - 主窗口标题栏按钮 tooltip。
  - 选择/取消选择按钮。
  - edit 顶部基础 action 文案。
  - 基础目录选择、绑定基础图库、恢复失败确认等对话框文案。
  - 地图扩展下载提示、进度、错误和重启提示。
  - 状态栏扫描、加载、导入、移动/删除/恢复等核心进度提示。
- 已迁移的业务页面：
  - `InfoPanel` 标题、关闭 tooltip、location 区域按钮/placeholder/fallback、metadata loading/unavailable 状态。
  - `InfoPanel` face avatar context menu 和新建/选择人物弹窗文案。
  - `InfoPanel` metadata 格式化改用当前 UI locale，而不是 `QLocale.system()`。
  - face 菜单命令识别改为稳定 `action_id`，不再依赖 `chosen.text()`。
  - `PeopleDashboardWidget` 标题、刷新按钮、section 标题、菜单项、输入框、确认/警告弹窗、空态/加载/扫描/已填充状态文案。
  - `PeopleDashboardWidget.retranslate_ui()` 已支持运行时刷新长期存在页面的固定文案和当前状态文案，不重建已加载人物/分组卡片。
  - `people_dashboard_dialogs` 默认 merge 确认弹窗与人物选择弹窗文案；调用方传入的自定义弹窗文案由调用方负责翻译。
  - People Dashboard 菜单继续依赖 `MenuActionSpec.action_id` / `QAction.data()`，不依赖翻译后的 label 判断命令。
  - `AlbumSidebar` 标题、未绑定状态、固定 tree 节点展示名、context menu、新建/重命名弹窗与空白区域绑定入口文案。
  - `AlbumTreeModel` 保留英文 `AlbumTreeItem.title` 作为导航稳定 key，仅在 `DisplayRole` 返回翻译文案；运行时切换语言通过 `retranslate_ui()` 发出 `dataChanged`，不重建模型或打断选择状态。
  - `AlbumsDashboard` 标题、空态、卡片菜单和重命名弹窗文案；相册数量改用当前 UI locale 的整数格式化。
  - 相册导航面菜单继续由 node type、callback 和 `MenuActionSpec.action_id` 驱动，不依赖翻译后的 label 判断命令。
  - `GalleryMenu` 右键菜单 registry 已迁移，包括 Copy、Reveal、Export、Set as Cover、Move to、Delete、Restore、Paste、Open Folder Location。
  - `ContextMenuController` gallery 右键菜单路径的状态栏/toast 反馈已迁移；动态文件名和路径使用 `{filename}` / `{path}` 占位符，不进入翻译资源。
  - Gallery context menu 测试已改为优先断言 `QAction.data()` 中的稳定 `action_id`，不把翻译后的 label 作为业务契约。
  - `DetailPageWidget` header/detail player 区固定文案已迁移，包括返回网格、缩放、信息、分享、收藏、左旋、编辑按钮、默认预览占位文本和编辑态左旋 tooltip。
  - `DetailPageWidget.retranslate_ui()` 已支持运行时刷新长期存在页面固定文案，并会递归刷新 `PlayerBar`、`VideoTrimBar`、`EditSidebar` 和 `FaceNameOverlayWidget`。
  - `PlayerBar` 播放/暂停、音量、静音 tooltip 已迁移，并新增 `retranslate_ui()`。
  - `DetailPageWidget` edit header 收尾文案已迁移，包括 Adjust/Crop segment、Compare tooltip、Revert to Original、Reset tooltip 和 Done。
  - `VideoTrimBar` 播放/暂停、trim 起止 handle 和 scrub 区域 tooltip 已迁移，并新增 `retranslate_ui()`；播放状态切换时会同步刷新当前 tooltip。
  - `InfoLocationMapView` 地图预览不可用状态已迁移，并新增 `retranslate_ui()`；地点名、坐标和地图诊断仍保持原始数据。
  - `PlayerViewController.show_placeholder(message=None)` 对默认占位文案改为按当前语言即时计算，避免语言切换后从缓存写回英文；调用方传入的自定义 `message` 仍按调用方负责翻译。
  - `ShareController` 状态栏/toast 反馈已迁移，包括未选择项目、文件不存在、复制到剪贴板、准备渲染图像/视频、复制原始文件和在文件管理器中显示；文件名继续通过 `{filename}` 占位符插入，不进入翻译资源。
  - `ExportController` 状态栏、toast、目录选择标题和基础错误提示已迁移；导出数量和错误详情使用 `{current}`、`{total}`、`{success}`、`{fail}`、`{error}` 占位符。
  - `FaceNameOverlayWidget` 默认未命名人脸、手动人脸命名 placeholder 和保存前校验 tooltip 已迁移；人物真实姓名、用户输入姓名和建议列表不翻译。
  - `EditSidebar` section 标题、Reset/Toggle tooltip 和首批 edit/crop 控件文案已迁移，并通过 `retranslate_ui()` 刷新 `CollapsibleSection` 标题、header control tooltip、Light/Color/Black & White slider label、Curve 通道/tooltip 和 Perspective/Aspect label。
  - `EditSidebar` 剩余 Apple Photos 对齐控件已迁移：White Balance 模式/slider、Definition、Selective Color、Noise Reduction、Sharpen 和 Vignette 的可见 label/tooltip 支持运行时刷新。
  - White Balance combo 逻辑已改为稳定 mode id，不再依赖翻译后的显示文本判断 `Neutral Gray`、`Skin Tone`、`Temperature/Tint` 模式。
  - 本轮新增/补齐 edit 术语优先对齐 Apple 官方「照片/Fotos」支持文档命名，并新增 `docs/requirements/i18n/apple_photos_edit_glossary.md` 作为过程性术语表。中文使用“光效、颜色、黑白、白平衡、曲线、色阶、清晰度、可选颜色、减少噪点、锐化、晕影、鲜明度、黑点、中性、颗粒、校正、宽高比、自由格式、色温/色调、亮度、范围”；德文使用“Licht、Farbe、Schwarzweiß、Weißabgleich、Kurven、Tonwerte、Auflösung、Selektive Farbe、Bildrauschen reduzieren、Scharfzeichnen、Vignette、Brillanz、Schwarzpunkt、Neutraltöne、Körnung、Begradigen、Seitenverhältnis、Frei、Temperatur/Farbton、Leuchtkraft、Bereich”。
  - 继续不翻译文件名、路径、人物名、地点搜索结果、相机/镜头/codec 原始值等用户数据或技术原始值。

---

## 2. 工具链与验证

新增/更新工具：

- `scripts/i18n_compile.sh`
  - 调用 `pyside6-lrelease`。
  - 从 `.ts` 生成 `.qm`。
- `tools/extract_i18n_strings.py`
  - 使用 Python `ast` 扫描源码中的翻译调用。
  - 识别 `QCoreApplication.translate(context, text, ...)`。
  - 识别项目封装 `tr(context, text, ...)`。
  - 识别局部别名，例如 `tr = QCoreApplication.translate` 后的 `tr(...)`。
  - 只提取字面量 context/source text，跳过动态 context、动态文案和 f-string，避免误提取用户数据。
  - 合并到现有 `.ts` 时保留已有译文，不覆盖已完成翻译。
  - 新增未翻译 message 会标记为 `type="unfinished"`。
  - 支持基础 translator comment/disambiguation 和 `%n` plural metadata。
- `scripts/i18n_extract.sh`
  - 已改为调用 `tools/extract_i18n_strings.py`，不再依赖当前不可用的 `pyside6-lupdate` Python 提取路径。
  - 当前扫描 `src/iPhoto/gui` 和 `src/maps`。
  - 更新 `src/iPhoto/resources/i18n/iPhoto_de.ts` 和 `src/iPhoto/resources/i18n/iPhoto_zh_CN.ts`。
  - 脚本已加保护：如果提取结果不包含 `<message>`，会恢复原 `.ts` 并退出失败，避免误清空已有翻译。

阶段 1-2 原始验证：

```bash
pytest tests/test_i18n_translation_manager.py \
  tests/test_settings_manager.py \
  tests/application/test_appctx_runtime_context.py \
  tests/gui/test_main.py \
  tests/application/test_runtime_context.py \
  tests/architecture/test_layer_boundaries.py -q
```

结果：

```text
46 passed
```

本轮提取工具补齐后执行验证：

```bash
pytest tests/test_i18n_extract_tool.py -q
```

结果：

```text
8 passed
```

工具链提取验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 105 translation messages.
```

说明：105 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 105 条 message，0 条 unfinished。

翻译编译验证：

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 105 translation(s) (105 finished and 0 unfinished)
Generated 105 translation(s) (105 finished and 0 unfinished)
```

阶段 3 `InfoPanel` 迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 132 translation messages.
```

说明：132 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 132 条 message，0 条 unfinished。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 132 translation(s) (132 finished and 0 unfinished)
Generated 132 translation(s) (132 finished and 0 unfinished)
```

静态检查：

```bash
python -m ruff check src/iPhoto/gui/i18n
python -m ruff check tools/extract_i18n_strings.py
```

结果：

```text
All checks passed
```

说明：全仓库级 `ruff check` 仍会命中历史规则噪声，例如测试中的 `assert`、Qt mixedCase 方法名、旧类型注解风格等。本轮只保证新增 i18n 包和相关测试通过目标验证。

本轮还执行了目标回归：

```bash
pytest tests/test_info_panel.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py -q
```

结果：

```text
59 passed
```

阶段 1-2 相关回归：

```bash
pytest tests/test_i18n_translation_manager.py \
  tests/test_settings_manager.py \
  tests/application/test_runtime_context.py \
  tests/architecture/test_layer_boundaries.py -q
```

结果：

```text
30 passed
```

阶段 3 People Dashboard 迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 173 translation messages.
```

说明：173 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 173 条 message，0 条 unfinished。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 173 translation(s) (173 finished and 0 unfinished)
Generated 173 translation(s) (173 finished and 0 unfinished)
```

本轮 People/i18n/InfoPanel 目标回归：

```bash
pytest tests/gui/widgets/test_people_dashboard_widget.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py \
  tests/test_info_panel.py -q
```

结果：

```text
91 passed
```

阶段 1-2 相关回归复跑：

```bash
pytest tests/test_i18n_translation_manager.py \
  tests/test_settings_manager.py \
  tests/application/test_runtime_context.py \
  tests/architecture/test_layer_boundaries.py -q
```

结果：

```text
35 passed
```

本轮源码静态检查：

```bash
python -m ruff check \
  src/iPhoto/gui/ui/widgets/people_dashboard_widget.py \
  src/iPhoto/gui/ui/widgets/people_dashboard_dialogs.py
```

结果：

```text
All checks passed
```

阶段 3 相册导航面迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 210 translation messages.
```

说明：210 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 210 条 message，0 条 unfinished。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 210 translation(s) (210 finished and 0 unfinished)
Generated 210 translation(s) (210 finished and 0 unfinished)
```

本轮相册导航面/i18n 目标回归：

```bash
pytest tests/test_album_tree_model.py \
  tests/test_album_sidebar.py \
  tests/ui/test_albums_dashboard.py \
  tests/test_i18n_translation_manager.py -q
```

结果：

```text
32 passed
```

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/widgets/album_sidebar.py \
  src/iPhoto/gui/ui/models/album_tree_model.py \
  src/iPhoto/gui/ui/widgets/albums_dashboard.py \
  src/iPhoto/gui/ui/menus/album_sidebar_menu.py \
  tests/test_album_tree_model.py \
  tests/test_album_sidebar.py \
  tests/ui/test_albums_dashboard.py \
  tests/test_i18n_translation_manager.py
```

结果：

```text
All checks passed
```

说明：全规则 `ruff check` 对这些历史 Qt widget 文件仍会命中既有 Qt mixedCase、旧类型注解、B008、长行和 blind-exception 等规则噪声；本轮只保证新增/触碰路径的 import 与未使用符号检查通过，并通过目标测试覆盖行为。

阶段 3 gallery context menu 迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 238 translation messages.
```

说明：238 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 238 条 message，0 条 unfinished。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 238 translation(s) (238 finished and 0 unfinished)
Generated 238 translation(s) (238 finished and 0 unfinished)
```

本轮 gallery context menu/i18n 目标回归：

```bash
pytest tests/ui/controllers/test_context_menu_export.py \
  tests/ui/controllers/test_context_menu_cover.py \
  tests/ui/controllers/test_context_menu_operations.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py -q
```

结果：

```text
36 passed
```

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/menus/gallery_menu.py \
  src/iPhoto/gui/ui/controllers/context_menu_controller.py \
  tests/ui/controllers/test_context_menu_export.py \
  tests/ui/controllers/test_context_menu_cover.py \
  tests/ui/controllers/test_context_menu_operations.py \
  tests/test_i18n_translation_manager.py
```

结果：

```text
All checks passed
```

阶段 3 detail/player 迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 252 translation messages.
```

说明：252 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 252 条 message，0 条 unfinished。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 252 translation(s) (252 finished and 0 unfinished)
Generated 252 translation(s) (252 finished and 0 unfinished)
```

本轮 detail/player/i18n 目标回归：

```bash
pytest tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py \
  tests/ui/controllers/test_player_view_controller_adjustments.py -q
```

结果：

```text
15 passed, 1 warning
```

说明：warning 为仓库既有 `pytest.ini` 中 `env` 配置未被当前 pytest 识别。本轮曾尝试把 `tests/ui/widgets/test_video_area.py` 一并纳入目标回归，但该 Qt 多媒体/QRhi 重型集合在当前环境卡住超过 10 分钟；已改用上述窄范围回归覆盖本轮 i18n 改动。

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/widgets/detail_page.py \
  src/iPhoto/gui/ui/widgets/player_bar.py \
  src/iPhoto/gui/ui/controllers/player_view_controller.py \
  tests/test_i18n_translation_manager.py
```

结果：

```text
All checks passed
```

阶段 3 share/export、face overlay、edit sidebar 首批控件迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 317 translation messages.
```

说明：317 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 317 条 message，0 条 unfinished。本轮新增 edit 术语按 Apple 官方「照片/Fotos」中文和德文使用手册命名补齐。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 317 translation(s) (317 finished and 0 unfinished)
Generated 317 translation(s) (317 finished and 0 unfinished)
```

本轮 share/export、face overlay、edit sidebar/i18n 目标回归：

```bash
pytest tests/ui/controllers/test_share_controller.py \
  tests/ui/controllers/test_export_controller.py \
  tests/ui/widgets/test_face_name_overlay.py \
  tests/ui/widgets/test_edit_sidebar.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py -q
```

结果：

```text
35 passed, 1 warning
```

说明：warning 为仓库既有 `pytest.ini` 中 `env` 配置未被当前 pytest 识别。

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/controllers/share_controller.py \
  src/iPhoto/gui/ui/controllers/export_controller.py \
  src/iPhoto/gui/ui/widgets/face_name_overlay.py \
  src/iPhoto/gui/ui/widgets/collapsible_section.py \
  src/iPhoto/gui/ui/widgets/edit_sidebar.py \
  src/iPhoto/gui/ui/widgets/edit_sidebar_sections.py \
  src/iPhoto/gui/ui/widgets/edit_bw_section.py \
  src/iPhoto/gui/ui/widgets/edit_light_section.py \
  src/iPhoto/gui/ui/widgets/edit_color_section.py \
  src/iPhoto/gui/ui/widgets/edit_curve_section.py \
  src/iPhoto/gui/ui/widgets/edit_perspective_controls.py \
  tests/test_i18n_translation_manager.py
```

结果：

```text
All checks passed
```

阶段 3 edit sidebar 剩余 Apple Photos 对齐控件迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 338 translation messages.
```

说明：338 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 338 条 message，0 条 unfinished。本轮新增 `docs/requirements/i18n/apple_photos_edit_glossary.md`，并按 Apple 官方「照片/Fotos」支持文档补齐 edit 剩余控件译法。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 338 translation(s) (338 finished and 0 unfinished)
Generated 338 translation(s) (338 finished and 0 unfinished)
```

本轮 edit sidebar/i18n 目标回归：

```bash
QT_QPA_PLATFORM=offscreen pytest tests/ui/widgets/test_edit_sidebar.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py -q
```

结果：

```text
20 passed, 1 warning
```

说明：warning 为仓库既有 `pytest.ini` 中 `env` 配置未被当前 pytest 识别。未设置 `QT_QPA_PLATFORM=offscreen` 时，当前无显示环境会在创建 `QApplication` 时 abort；本轮以 offscreen 模式完成 Qt widget 目标回归。

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/widgets/edit_strip.py \
  src/iPhoto/gui/ui/widgets/wb_sliders.py \
  src/iPhoto/gui/ui/widgets/edit_wb_section.py \
  src/iPhoto/gui/ui/widgets/edit_selective_color_section.py \
  src/iPhoto/gui/ui/widgets/edit_definition_section.py \
  src/iPhoto/gui/ui/widgets/edit_denoise_section.py \
  src/iPhoto/gui/ui/widgets/edit_sharpen_section.py \
  src/iPhoto/gui/ui/widgets/edit_vignette_section.py \
  tests/ui/widgets/test_edit_sidebar.py \
  tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py
```

结果：

```text
All checks passed
```

说明：ruff 仍提示仓库顶层 linter 配置项迁移 warning，这是既有 `pyproject.toml` 配置风格问题，不影响本轮检查结果。

阶段 3 detail/edit 收尾控件迁移后工具链验证：

```bash
bash scripts/i18n_extract.sh
```

结果：

```text
Extracted 350 translation messages.
```

说明：350 是当前源码中已包裹翻译调用去重后的可提取 message 数；当前 `iPhoto_de.ts` 和 `iPhoto_zh_CN.ts` 各包含 350 条 message，0 条 unfinished。本轮新增 `DetailPage` edit header、`VideoTrimBar` 和 `InfoLocationMap` 文案。

```bash
bash scripts/i18n_compile.sh
```

结果：

```text
Generated 350 translation(s) (350 finished and 0 unfinished)
Generated 350 translation(s) (350 finished and 0 unfinished)
```

本轮 detail/edit 收尾目标回归：

```bash
QT_QPA_PLATFORM=offscreen pytest tests/test_i18n_translation_manager.py \
  tests/test_i18n_extract_tool.py \
  tests/test_info_panel.py \
  tests/ui/widgets/test_edit_sidebar.py \
  tests/ui/widgets/test_video_trim_bar.py \
  tests/gui/test_main.py -q
```

结果：

```text
88 passed, 1 warning
```

说明：warning 为仓库既有 `pytest.ini` 中 `env` 配置未被当前 pytest 识别。未设置 `QT_QPA_PLATFORM=offscreen` 时，当前无显示环境会在创建 `QApplication` 时 abort；本轮以 offscreen 模式完成 Qt widget 目标回归。

本轮窄范围静态检查：

```bash
python -m ruff check --select I,F \
  src/iPhoto/gui/ui/widgets/detail_page.py \
  src/iPhoto/gui/ui/widgets/info_location_map.py \
  src/iPhoto/gui/ui/widgets/video_trim_bar.py \
  tests/test_info_panel.py \
  tests/ui/widgets/test_video_trim_bar.py
```

结果：

```text
All checks passed
```

说明：ruff 仍提示仓库顶层 linter 配置项迁移 warning，这是既有 `pyproject.toml` 配置风格问题，不影响本轮检查结果。

---

## 3. 已知限制

当前完成的是核心壳层国际化，以及 `InfoPanel`、People Dashboard、相册导航面、gallery context menu、detail/player 控制区、detail/edit 收尾控件、share/export 首批反馈、face overlay、edit sidebar 首批控件和 edit sidebar 剩余 Apple Photos 对齐控件迁移，不是全应用文案迁移。

仍未完成的主要区域：

- `src/maps/main.py` 独立地图预览入口未迁移。
- 主应用 map view 中仍可能存在用户可见状态文案未系统迁移。
- `tools/check_i18n_strings.py` 硬编码文案门禁尚未实现。
- locale-aware formatter 已具备日期时间、整数、小数和文件大小能力，但百分比、复数和更完整的 domain-specific 格式化仍未系统接入。
- `tools/extract_i18n_strings.py` 只提取已经包裹的翻译调用；未包裹硬编码文案仍需要页面迁移和后续门禁识别。
- 当前提取器故意跳过动态 context/source text。后续迁移时应把用户可见文案改成稳定字面量加 `.format(...)` 占位符，而不是动态拼接。

运行时注意点：

- 语言切换依赖 `settingsChanged("ui.language")`。
- `TranslationManager` 负责全局安装 translator。由于 Qt translator 是 application-wide 状态，测试中多个 manager 会互相替换当前 translator，这是预期行为。
- 主窗口语言刷新已改为 deferred retranslate，后续新增同步刷新逻辑时不要直接在 menu action triggered 的调用栈中访问 popup menu 对象。
- `InfoPanel` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用。
- `PeopleDashboardWidget` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用；运行时切换语言会刷新页面标题、按钮、section 标题和当前状态文案，但不会重载或重建已有卡片。
- `AlbumSidebar` 和 `AlbumsDashboard` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用；`AlbumTreeModel` 只刷新 DisplayRole，内部英文 key 保持不变。
- `DetailPageWidget` 与 `PlayerBar` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用；默认预览占位文本由 `PlayerViewController` 按当前语言即时计算。
- `EditSidebar` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用；section 标题通过稳定英文 source text 翻译，session key、slider key、aspect ratio 数值和用户数据保持不变。
- `FaceNameOverlayWidget` 已实现 `retranslate_ui()`，并由 `MainWindow.retranslate_ui_tree()` 自动调用；只刷新 fallback/placeholder/校验提示，不翻译人物真实姓名或用户输入。
- 后续迁移 context menu 时不要用 `action.text()` 判断命令；应依赖 `MenuActionSpec.action_id` / `QAction.data()`。
- 如果传入 `InfoPanel.set_location_capability(fallback_text=...)` 的是外部自定义文案，该文案按调用方负责翻译；默认 fallback 已由 `InfoPanel` 自身翻译。
- 如果传入 `GroupPeopleDialog(title_text=..., prompt_text=..., confirm_text=...)` 或 `MergeConfirmDialog.confirm_action(...)` 的是自定义文案，该文案按调用方负责翻译；People Dashboard 内部调用已完成翻译。

---

## 4. 下一步建议

建议按架构指南继续推进阶段 3-5。

优先级 1：迁移主要业务页面

建议按用户可见度排序：

1. map view 中用户可见状态与 `src/maps/main.py` 独立入口
2. 新增或后续发现的 detail/edit 边缘控件继续按 widget/controller 小批次迁移
3. 后续新增 gallery context menu 文案继续使用 `GalleryMenu` / `GalleryContextMenu` context，并保持 `QAction.data()` 作为命令契约。

每个 widget/controller 迁移时需要同步完成：

- 将用户可见文案改为 `QCoreApplication.translate()` 或 `tr()`。
- 为长期存在的 widget 增加 `retranslate_ui()`。
- 避免拼接自然语言句子，动态值使用 `{name}`、`{count}` 等占位符。
- 不翻译文件名、路径、相册名、人物名、EXIF 原始值和内部诊断。
- 每完成一个页面后运行 `bash scripts/i18n_extract.sh`，补齐 `.ts` 中新增 message，再运行 `bash scripts/i18n_compile.sh`。

优先级 2：地图独立入口

- 迁移 `src/maps/main.py` 的菜单、对话框、状态栏和窗口标题。
- 初期可以继续使用同一 `iPhoto_*.qm`。
- 如果后续 maps 文案明显膨胀，再拆分 `maps_*.ts/.qm`。

优先级 3：继续扩展格式化 helper

`src/iPhoto/gui/i18n/formatters.py` 已新增，后续建议继续补：

- 百分比、复数和相对时间格式化。
- 更多页面接入 formatter，避免 widget 直接调用 `QLocale.system()`。
- 对数量文案优先使用 Qt `%n` plural 机制，而不是自行拼接。

优先级 4：硬编码门禁

新增 `tools/check_i18n_strings.py` 并接入架构测试：

- 扫描 GUI 层高风险 API：
  - `QAction(...)`
  - `addMenu(...)`
  - `setText(...)`
  - `setToolTip(...)`
  - `setPlaceholderText(...)`
  - `setWindowTitle(...)`
  - `QMessageBox.*(...)`
  - `showMessage(...)`
- 允许历史 allowlist，但新增 UI 文案不应进入 allowlist。
- 明确排除 objectName、CSS、icon filename、enum、logger、test fixture。

---

## 5. 交接检查清单

后续接手者开始新阶段前建议先执行：

```bash
pytest tests/test_i18n_translation_manager.py \
  tests/test_info_panel.py \
  tests/test_i18n_extract_tool.py \
  tests/test_settings_manager.py \
  tests/application/test_appctx_runtime_context.py \
  tests/gui/test_main.py \
  tests/application/test_runtime_context.py \
  tests/architecture/test_layer_boundaries.py -q

bash scripts/i18n_extract.sh
bash scripts/i18n_compile.sh
```

如果只接手提取工具链，可先执行：

```bash
pytest tests/test_i18n_extract_tool.py -q
python -m ruff check src/iPhoto/gui/i18n tools/extract_i18n_strings.py
```

手动验收建议：

- 启动 GUI。
- 打开 `Settings > Language`。
- 切换到 `简体中文`，确认菜单、核心按钮和标题栏 tooltip 刷新且不崩溃。
- 切换到 `Deutsch`，确认同样即时刷新。
- 切回 `System`，确认设置持久化且应用不崩溃。
- 在切换语言后打开基础图库绑定、地图扩展入口、扫描/加载流程，检查核心提示文案。
- 在切换语言后打开 detail 的 Info 面板，检查标题、关闭 tooltip、location fallback/download/confirm 文案、face 菜单和 metadata loading/unavailable 文案。
- 在切换语言后打开 People 页面，检查标题、刷新按钮、Groups/People & Pets section、人物/分组菜单、merge/hide/disband 弹窗和空态/加载/扫描状态文案。
- 在切换语言后打开相册侧边栏和 Albums 页面，检查 Basic Library、All Photos、Pinned、Albums、Recently Deleted、空态、相册卡片菜单、新建/重命名相册弹窗和 pin/unpin 菜单文案。
- 在切换语言后打开 gallery 右键菜单，检查 Copy、Reveal、Export、Set as Cover、Move to、Delete、Restore、Paste、Open Folder Location，以及删除/恢复/复制/粘贴/设为封面的状态提示和 toast 文案。

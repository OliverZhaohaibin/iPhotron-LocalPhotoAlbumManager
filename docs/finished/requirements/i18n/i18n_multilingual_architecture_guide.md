# iPhotron 国际化多语言架构开发指南

> 版本：1.0  
> 状态：未来国际化开发标准  
> 首批语言：德语 `de`、简体中文 `zh-CN`  
> 默认语言：英文源码文案

---

## 1. 目标与原则

本文档规定 iPhotron 后续国际化（i18n）与多语言 UI 开发的统一架构、文件结构、代码约束和测试要求。它是未来所有新增 UI、菜单、对话框、状态提示、设置项和用户可见文案的开发指南。

当前项目是基于 PySide6 的桌面应用，GUI 由 Python 代码直接构建，`RuntimeContext` 是运行时组合根，`SettingsManager` 负责用户设置，`ThemeManager` 已作为全局 UI 服务存在。因此国际化也必须作为一等运行时服务接入，而不是在 widget、controller 或业务服务中零散处理。

核心目标：

- 支持德语和简体中文作为首批正式语言。
- 英文作为源码默认语言和稳定兜底语言。
- 保持新增 UI 的持续可维护性，避免硬编码文案继续扩散。
- 支持未来继续加入新语言，而不改变 UI、controller、settings 的基本结构。
- 将翻译资源、翻译流程、运行时切换和测试门禁标准化。

设计原则：

- GUI 主链路采用 Qt 原生国际化能力：`QTranslator`、`QCoreApplication.translate()`、Qt Linguist `.ts/.qm`。
- 不以 `gettext` 作为 GUI 主方案，避免在 PySide6 应用中并行维护两套翻译机制。
- 国际化服务归属于 runtime/bootstrap 层，与主题服务同级。
- domain、application、infrastructure 不依赖 GUI 国际化模块。
- 用户数据不翻译，产品 UI 文案必须翻译。
- 新增 UI 必须从第一天符合国际化规范，不允许先硬编码再“以后补”。

---

## 2. 当前项目事实

现有结构中与国际化直接相关的事实：

- GUI 入口是 `src/iPhoto/gui/main.py`。
- 运行时组合根是 `src/iPhoto/bootstrap/runtime_context.py`。
- 用户设置由 `src/iPhoto/settings/manager.py` 和 `src/iPhoto/settings/schema.py` 管理。
- 主题服务 `src/iPhoto/gui/ui/theme_manager.py` 已监听 `settingsChanged` 并响应 `ui.theme`。
- 主窗口 `src/iPhoto/gui/ui/ui_main_window.py` 已有 `retranslateUi()`，并使用少量 `QCoreApplication.translate()`。
- 大量用户可见文案仍分布在 widgets、controllers、menus、maps 中，例如：
  - `src/iPhoto/gui/ui/widgets/main_header.py`
  - `src/iPhoto/gui/ui/widgets/info_panel.py`
  - `src/iPhoto/gui/ui/widgets/people_dashboard_widget.py`
  - `src/iPhoto/gui/ui/controllers/map_extension_download_controller.py`
  - `src/maps/main.py`
- `pyproject.toml` 当前 package data 已包含 `json/yaml/svg/qsb/frag/vert`，后续需要纳入翻译资源。

这些事实决定了本项目的最佳国际化方案不是新增一个独立文案系统，而是围绕 Qt translation 机制建立项目级封装、目录规范和静态检查。

---

## 3. 架构标准

### 3.1 运行时服务

新增国际化服务：

```text
src/iPhoto/gui/i18n/
├── __init__.py
├── language.py
└── translation_manager.py
```

`TranslationManager` 是唯一负责安装、卸载和切换应用翻译器的运行时对象。它由 `RuntimeContext` 创建并持有，与 `ThemeManager` 同级。

建议运行时结构：

```text
RuntimeContext
  settings: SettingsManager
  translation: TranslationManager
  theme: ThemeManager
  library: LibraryRuntimeController
  ...
```

职责划分：

| 模块 | 职责 |
| --- | --- |
| `SettingsManager` | 持久化用户选择的语言，不加载翻译资源。 |
| `TranslationManager` | 解析语言、加载 `.qm`、安装 `QTranslator`、发出语言变更信号。 |
| `MainWindow` / widgets | 响应语言变化并重新应用自身 UI 文案。 |
| controllers | 生成用户可见动态消息时调用翻译 API。 |
| domain/application/infrastructure | 不感知 GUI 语言，不导入 GUI i18n。 |

### 3.2 设置契约

在 `DEFAULT_SETTINGS["ui"]` 中新增：

```python
"language": "system"
```

在 settings schema 中新增：

```python
"language": {
    "type": "string",
    "enum": ["system", "de", "zh-CN"],
}
```

语义：

| 值 | 含义 |
| --- | --- |
| `system` | 默认值，跟随系统语言；如果系统语言不受支持则回退英文。 |
| `de` | 德语。 |
| `zh-CN` | 简体中文。 |

旧 `settings.json` 必须通过 `merge_with_defaults()` 自动补齐 `ui.language = "system"`，不得要求用户手动迁移。

### 3.3 启动顺序

GUI 启动必须遵守以下顺序：

```text
main()
  create QApplication
  RuntimeContext.create()
    load SettingsManager
    create TranslationManager(settings)
    apply saved/system language
    create ThemeManager(settings)
  create MainWindow(context)
  setupUi()
  retranslateUi()
  connect languageChanged -> window.retranslate_ui_tree()
```

原则：

- `QApplication` 必须先于 `QTranslator` 安装存在。
- `TranslationManager` 应在主窗口构建前应用初始语言。
- 语言切换后必须尽量即时刷新主窗口和已加载页面。
- 对历史组件可以阶段性标注“需要下次打开窗口刷新”，但新增 UI 不允许依赖重启。

### 3.4 翻译资源目录

统一放置在包内资源目录：

```text
src/iPhoto/resources/
├── __init__.py
└── i18n/
    ├── languages.json
    ├── iPhoto_de.ts
    ├── iPhoto_de.qm
    ├── iPhoto_zh_CN.ts
    └── iPhoto_zh_CN.qm
```

`languages.json` 用于 UI 展示和运行时枚举：

```json
{
  "default": "en",
  "languages": [
    {
      "code": "system",
      "native_name": "System",
      "english_name": "System"
    },
    {
      "code": "de",
      "native_name": "Deutsch",
      "english_name": "German",
      "qt_locale": "de_DE",
      "qm": "iPhoto_de.qm"
    },
    {
      "code": "zh-CN",
      "native_name": "简体中文",
      "english_name": "Simplified Chinese",
      "qt_locale": "zh_CN",
      "qm": "iPhoto_zh_CN.qm"
    }
  ]
}
```

`pyproject.toml` package data 必须包含：

```toml
"iPhoto" = [
  "**/*.qsb",
  "**/*.frag",
  "**/*.vert",
  "**/*.svg",
  "**/*.json",
  "**/*.yaml",
  "**/*.ts",
  "**/*.qm",
]
```

### 3.5 翻译管理器接口

推荐接口：

```python
class TranslationManager(QObject):
    languageChanged = Signal(str)

    def __init__(self, settings: SettingsManager) -> None: ...
    def current_language(self) -> str: ...
    def effective_language(self) -> str: ...
    def available_languages(self) -> list[LanguageInfo]: ...
    def set_language(self, language: str) -> None: ...
    def apply_language(self, language: str | None = None) -> None: ...
```

行为要求：

- `current_language()` 返回用户设置值，例如 `system`。
- `effective_language()` 返回实际加载语言，例如系统为德语时返回 `de`。
- 找不到 `.qm`、`.qm` 损坏或语言不支持时必须回退英文，不得阻止应用启动。
- 回退时记录 warning。
- 切换语言时先卸载旧 translator，再安装新 translator。
- 设置值变化来自 `settingsChanged("ui.language")` 时也必须生效。

---

## 4. UI 文案开发规范

### 4.1 新增 UI 必须支持重翻译

所有新 widget、page、dialog、controller 只要持有用户可见文案，就必须提供清晰的重翻译入口。

推荐命名：

```python
def retranslate_ui(self) -> None:
    ...
```

对 Qt Designer 风格或现有 `Ui_*` 类，允许继续使用：

```python
def retranslateUi(self, MainWindow: QMainWindow) -> None:
    ...
```

主窗口需要提供递归刷新入口：

```python
def retranslate_ui_tree(self) -> None:
    self.ui.retranslateUi(self)
    for child in self.findChildren(QWidget):
        method = getattr(child, "retranslate_ui", None)
        if callable(method):
            method()
```

新增 UI 禁止只在构造函数中设置英文文本而不提供刷新入口。

### 4.2 翻译 API 使用

基础写法：

```python
QCoreApplication.translate("MainWindow", "Open Album Folder…")
```

推荐项目封装：

```python
from iPhoto.gui.i18n import tr

button.setText(tr("MainWindow", "Open Album Folder…"))
```

封装必须保持 Qt 可提取性。如果封装影响 `pyside6-lupdate` 自动提取，则 UI 源码应优先使用 `QCoreApplication.translate()`，或者 extraction 脚本必须显式支持该封装。

context 命名要求：

- 使用稳定类名或模块名，例如 `MainWindow`、`InfoPanel`、`PeopleDashboard`。
- 不使用动态值作为 context。
- 同一页面相同语义文案应使用同一 context。
- 不同语义但英文相同的文本应使用不同 context 或 translator comment。

### 4.3 动态文案

禁止拼接自然语言句子：

```python
# 禁止
message = "Imported " + str(count) + " photos"
```

必须使用占位符：

```python
message = tr("Import", "Imported {count} photos").format(count=count)
```

对于数量、日期、时间、文件大小、百分比，应使用专用格式化 helper：

```text
src/iPhoto/gui/i18n/formatters.py
```

职责：

- 使用当前有效 `QLocale` 格式化日期时间。
- 使用当前有效 `QLocale` 格式化数字。
- 文件大小使用 locale-aware 数字格式，单位保持产品定义。
- 避免每个 widget 自己调用 `QLocale.system()`，因为用户可能选择了非系统语言。

### 4.4 复数与数量

Qt translation 支持基于 `%n` 的复数。涉及数量的文案应优先使用 Qt plural 机制，而不是写两个 if 分支：

```python
QCoreApplication.translate("Gallery", "%n item(s) selected", None, count)
```

翻译文件中由译者按目标语言处理复数形式。中文通常不区分单复数，德语需要区分。

### 4.5 不翻译内容

以下内容不得进入翻译资源：

- 相册名、人物名、分组名、文件名、路径。
- EXIF 原始值、相机型号、镜头型号。
- 枚举值、协议字段、数据库字段、JSON key。
- 日志消息，除非该日志明确展示给用户。
- 异常内部详情、debug diagnostics。
- 环境变量名、命令行参数名、文件格式扩展名。
- objectName、stylesheet selector、图标文件名。

以下内容必须翻译：

- 菜单、子菜单、action 文案。
- 按钮、tab、segmented control、checkbox、radio 文案。
- tooltip、status tip、placeholder。
- 对话框标题、正文、按钮。
- toast、notification、状态栏用户消息。
- 空状态、加载状态、错误状态。
- 设置项名称和值的用户展示文案。

---

## 5. 菜单与设置入口

语言切换入口必须放在主窗口 menubar 的设置菜单下，固定路径为：

```text
menubar
  Settings
    Language
```

主菜单新增结构：

```text
Settings
  Language
    System
    Deutsch
    简体中文
```

行为要求：

- `Language` 必须作为 `Settings` 下的一级子菜单，不放入工具栏、状态栏或独立弹窗作为唯一入口。
- `Settings > Language` 是桌面端用户切换语言的标准入口；后续如新增偏好设置窗口，也只能作为补充入口，不能替代 menubar 入口。
- 使用 `QActionGroup`，单选。
- 当前 `ui.language` 对应 action 处于 checked。
- 点击后调用 `SettingsManager.set("ui.language", code)`。
- `TranslationManager` 通过 settings signal 自动应用语言。
- 菜单本身也必须在语言切换后刷新。

语言名称展示规则：

- 语言列表中每种语言优先显示 native name。
- `System` 文案本身必须可翻译。
- 不要将 `de` 显示为 `German`，应显示 `Deutsch`。
- 不要将 `zh-CN` 显示为 `Chinese`，应显示 `简体中文`。

---

## 6. 迁移路线

### 阶段 1：基础设施

- 新增 `resources/i18n` 目录和 `languages.json`。
- 新增 `TranslationManager`。
- 扩展 settings schema 和 defaults。
- 将 `RuntimeContext` 接入 translation 服务。
- 修改 package data，确保 `.qm` 可打包。
- 新增提取与编译脚本。

### 阶段 2：核心壳层 UI

优先迁移：

- `ui_main_window.py`
- `main_header.py`
- `window_manager.py`
- `custom_title_bar.py`
- `chrome_status_bar.py`
- `selection_controller.py`
- `dialog_controller.py`
- `map_extension_download_controller.py`

目标是应用启动后主菜单、窗口标题、标题栏按钮、核心工具栏、设置菜单、基础对话框都可切换语言。

### 阶段 3：主要业务页面

继续迁移：

- 相册侧边栏与 albums dashboard。
- gallery/detail/player/edit sidebar。
- info panel。
- people dashboard。
- map view 中用户可见状态。

每个页面迁移时必须补齐 `retranslate_ui()`，不得只替换字符串调用。

### 阶段 4：地图独立预览与边缘入口

迁移 `src/maps/main.py` 中的菜单、对话框、状态栏、CLI parser help。地图独立预览可使用同一 `iPhoto_*.qm`，也可以后续拆分 `maps_*.qm`；首选同一资源，直到翻译文件过大或上下文冲突明显。

### 阶段 5：硬编码门禁

引入静态扫描，禁止 GUI 层新增未标记的用户可见英文文案。历史遗留可通过 allowlist 逐步收敛，但新增代码不允许进入 allowlist，除非该字符串明确属于“不翻译内容”。

---

## 7. 工具链要求

新增脚本：

```text
scripts/i18n_extract.sh
scripts/i18n_compile.sh
tools/check_i18n_strings.py
```

`scripts/i18n_extract.sh` 职责：

- 调用 `pyside6-lupdate` 扫描 `src/iPhoto/gui` 和必要的 `src/maps`。
- 更新 `src/iPhoto/resources/i18n/iPhoto_de.ts`。
- 更新 `src/iPhoto/resources/i18n/iPhoto_zh_CN.ts`。
- 不删除译者已有翻译，除非明确使用 obsolete 清理模式。

`scripts/i18n_compile.sh` 职责：

- 调用 `pyside6-lrelease`。
- 从 `.ts` 生成 `.qm`。
- CI 或 release build 前必须运行。

`tools/check_i18n_strings.py` 职责：

- 扫描 GUI 层高风险 API：
  - `QAction(...)`
  - `addMenu(...)`
  - `setText(...)`
  - `setToolTip(...)`
  - `setPlaceholderText(...)`
  - `setWindowTitle(...)`
  - `QMessageBox.*(...)`
  - `showMessage(...)`
- 对新增英文自然语言字符串发出违规。
- 允许 objectName、CSS、icon filename、enum、logger、test fixture。

---

## 8. 测试标准

### 8.1 单元测试

必须覆盖：

- 旧 settings 文件合并后包含 `ui.language == "system"`。
- schema 接受 `system`、`de`、`zh-CN`，拒绝未知语言。
- `TranslationManager.available_languages()` 正确读取 `languages.json`。
- `TranslationManager` 找不到 `.qm` 时回退英文且应用不崩溃。
- 设置 `ui.language` 后触发 `languageChanged`。
- `effective_language()` 在系统语言受支持时返回支持语言，否则返回英文。

### 8.2 UI 测试

必须覆盖：

- 设置菜单存在 `Language` 子菜单。
- `System`、`Deutsch`、`简体中文` 三个 action 存在且互斥。
- 切换 `zh-CN` 后主菜单和核心按钮显示中文。
- 切换 `de` 后主菜单和核心按钮显示德语。
- 切换语言不破坏主题、窗口布局、当前相册绑定状态。

### 8.3 架构测试

必须覆盖：

- domain/application/infrastructure 不导入 `iPhoto.gui.i18n`。
- GUI 层新增用户文案必须可提取。
- `TranslationManager` 不依赖 library session。
- 国际化资源被 package data 包含。

---

## 9. 翻译质量要求

翻译提交必须满足：

- 德语和简体中文 `.ts` 不允许存在核心 UI 未翻译项。
- 不翻译产品名 `iPhotron`、文件扩展名、技术标准名。
- 中文使用简体中文标点和自然表达。
- 德语 UI 文案优先简洁，避免过长导致按钮或菜单溢出。
- tooltip 可以比按钮文案更完整，但不能解释显而易见的操作。
- 同一概念必须术语一致：
  - Basic Library：中文建议“基础图库”，德语需在术语表中固定。
  - Album：中文建议“相册”。
  - People：中文建议“人物”。
  - Map Extension：中文建议“地图扩展”。
  - Rescan：中文建议“重新扫描”。

建议维护术语表：

```text
docs/requirements/i18n_glossary.md
```

如果后续翻译规模扩大，可将术语表升级为独立 QA 资源。

---

## 10. 开发验收清单

新增或修改 UI 时，开发者必须检查：

- 是否新增了用户可见文案。
- 文案是否使用 `QCoreApplication.translate()` 或项目认可的 `tr()` helper。
- 是否实现了 `retranslate_ui()` 或接入现有 `retranslateUi()`。
- 动态文案是否使用占位符，而不是字符串拼接。
- 数字、日期、时间是否使用 locale-aware formatter。
- 是否把用户数据误放入翻译资源。
- 是否运行了 i18n extract/compile。
- 是否补充或更新了德语、简体中文翻译。
- 是否通过 UI 语言切换测试。
- 是否通过硬编码文案静态检查。

---

## 11. 明确禁止

- 禁止在新增 GUI 代码中直接写未包裹的英文用户文案。
- 禁止在 domain/application/infrastructure 中导入 GUI 翻译模块。
- 禁止用用户当前语言作为业务逻辑判断条件。
- 禁止翻译枚举值、数据库值、配置 key。
- 禁止通过重启应用作为新增 UI 的语言切换方案。
- 禁止在不同模块中自建 `_()`、`tr()`、`translate()` 私有实现。
- 禁止在翻译文件中修改源码英文来“修复”产品文案；源码默认英文必须先修正。

---

## 12. 推荐完成定义

国际化首期可认为完成，当且仅当：

- `ui.language` 设置稳定持久化并兼容旧设置。
- `TranslationManager` 接入 `RuntimeContext`。
- `de` 与 `zh-CN` `.ts/.qm` 资源存在并被打包。
- 主窗口、主菜单、设置菜单、核心控制按钮、常用对话框支持即时切换。
- 新增 UI 有明确国际化开发规范和静态检查。
- 测试覆盖 settings、translation manager、核心 UI 切换和架构边界。
- 文档、脚本和验收流程足以支持未来新增第三种语言。

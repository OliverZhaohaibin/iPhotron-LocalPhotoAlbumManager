# 未来重构方向

> **版本**: v1.0 | **日期**: 2026-02-15  
> **目的**: 基于当前代码库现状，提出 Phase 1-5 计划之外的新重构方向

---

## 概述

Phase 1-5 覆盖了架构基础（DI/EventBus）、领域层、GUI MVVM、性能优化和测试/CI。
以下方向是在完成现有 5 阶段后，进一步提升项目可维护性、可扩展性和用户体验的建议。

---

## 方向一：gui/facade.py 深度重构 — 命令模式 + 中介者模式

### 现状问题

`gui/facade.py` 当前 733 行，承担 10+ 职责：
- 相册生命周期管理（打开、扫描、配对）
- 资产操作（导入、移动、删除、恢复）
- Manifest 管理
- Library 绑定与 Watcher 协调
- 13 个 Qt Signal 的中转

Phase 3 计划的目标是 ≤200 行，但未达成。

### 建议方案

采用 **命令模式 (Command Pattern)** + **中介者模式 (Mediator Pattern)**：

```
gui/
├── facade.py                  # ≤100 行，仅做命令分发
├── commands/
│   ├── base.py                # BaseCommand + CommandBus
│   ├── album_commands.py      # OpenAlbum, RescanAlbum, PairLive
│   ├── asset_commands.py      # ImportAssets, MoveAssets, DeleteAssets
│   ├── restore_command.py     # RestoreAssets（当前 138 行，最复杂）
│   └── metadata_commands.py   # SetCover, ToggleFeatured
└── mediator.py                # Signal 聚合与转发
```

**预期收益**:
- Facade 从 733 行降至 ≤100 行
- 每个命令独立可测试
- 支持 Undo/Redo 扩展（参考 Anki 的操作日志模式）
- 参考 02-industry-benchmarks.md 中 Shotwell 的 Command Pattern 实践

---

## 方向二：国际化 (i18n) 基础设施

### 现状问题

- 所有 UI 字符串硬编码为英文
- 无 `gettext`、`babel` 或任何翻译框架
- 无字符串资源文件

### 建议方案

**阶段 A — 字符串提取**:
1. 定义 `_()` 翻译函数 wrapper
2. 扫描所有 UI 层文件，将硬编码字符串替换为 `_("...")`
3. 生成 `.pot` 模板文件

**阶段 B — 翻译基础设施**:
1. 选择框架：推荐 `gettext`（Python 标准库，零依赖）
2. 创建 `locales/` 目录结构
3. 提供中文 (zh_CN) 作为第一个翻译

**阶段 C — QML 层集成**:
1. QML 文件中使用 `qsTr()` 包裹字符串
2. 通过 Qt Linguist 工具链管理 `.ts` / `.qm` 文件

**预期收益**:
- 支持多语言用户群
- 字符串集中管理，减少拼写错误和不一致
- 约 3-4 周工作量

---

## 方向三：严格类型检查 (Strict Type Checking)

### 现状问题

- `mypy` 已列为开发依赖，但 `pyproject.toml` 中 **无 `[tool.mypy]` 配置**
- 约 85% 函数有类型注解，15% 缺失返回类型
- 未声明 `py.typed` (PEP 561)
- 无 CI 中的类型检查门禁

### 建议方案

```toml
# pyproject.toml 增加配置
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
check_untyped_defs = true
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "iPhoto.gui.ui.*"
disallow_untyped_defs = false  # Qt 回调难以完全标注
```

**分步实施**:
1. 先启用 `warn_return_any` + `check_untyped_defs`（低噪音）
2. 修复现有类型错误（预计 50-100 处）
3. 逐包启用 `disallow_untyped_defs`（domain → application → infrastructure → gui）
4. 加入 CI 门禁（Phase 5 的 GitHub Actions 流程中）

**预期收益**:
- 编译时捕获 None/类型不匹配错误
- IDE 补全和导航体验提升
- 约 1-2 周工作量

---

## 方向四：地图模块深度集成

### 现状问题

`src/maps/` 是相对独立的模块（16 个文件），仅通过 2 个 widget 文件与主应用连接：
- `gui/ui/widgets/photo_map_view.py`
- `gui/ui/widgets/marker_controller.py`

存在问题：
- 无双向绑定：地图上选择标记不会联动相册视图
- Tile 加载为同步操作，可能阻塞 UI
- 无坐标系统扩展（仅支持 WGS84 lat/lon）

### 建议方案

1. **双向绑定**: 通过 EventBus 实现 `AssetSelected` / `MapMarkerClicked` 事件联动
2. **异步 Tile 加载**: 使用 `QThreadPool` 或 `asyncio` 进行后台加载
3. **聚合层缓存**: 使用 `AggregateGeoData` Use Case（Phase 2 P2 级）预聚合地理数据

**预期收益**:
- 完整的"地图浏览照片"体验
- 消除 Tile 加载卡顿
- 约 2-3 周工作量

---

## 方向五：可观测性中间件层

### 现状问题

- 日志层仅 24 行（`utils/logging.py`），全局 Logger 无结构化输出
- 无集中式错误处理中间件（虽有 `errors/` 层级，但无自动捕获/上报）
- 无性能指标采集（扫描耗时、缓存命中率等仅在 `CacheStatsCollector` 中局部实现）
- 无操作审计日志

### 建议方案

```
src/iPhoto/observability/
├── __init__.py
├── structured_logger.py    # JSON 格式化、上下文追踪 (correlation ID)
├── metrics_collector.py    # 关键路径耗时、缓存命中率、内存使用
├── error_reporter.py       # 全局异常处理 + 用户友好提示
└── audit_log.py            # 操作记录（导入/删除/移动）用于调试
```

**关键设计**:
- 使用 Python `logging` 的 `StructuredFormatter` 输出 JSON 日志
- `MetricsCollector` 通过 EventBus 订阅关键事件（`ScanCompleted`、`ImportCompleted`）
- `ErrorReporter` 作为全局异常钩子 (`sys.excepthook`)

**预期收益**:
- 用户问题排查时间减少 50%+
- 性能瓶颈可视化
- 约 2 周工作量

---

## 方向六：插件/扩展架构

### 现状问题

- 所有功能硬编码在主应用中
- 滤镜/调整工具无法由第三方扩展
- 导出格式固定，无法添加新格式

### 建议方案（长期）

参考 Calibre 的插件系统：
1. 定义 `PluginBase` 抽象类（滤镜插件、导出插件、元数据插件）
2. 通过 DI 容器注册插件（利用现有 Phase 1 基础）
3. 插件发现：扫描 `plugins/` 目录自动加载

**预期收益**:
- 社区贡献能力
- 核心代码复杂度降低
- 长期投资，约 4-6 周工作量

---

## 优先级总结

| 方向 | 影响范围 | 工作量 | 推荐时机 |
|------|---------|--------|---------|
| 🔴 Facade 命令模式重构 | 架构质量 | 2-3 周 | Phase 5 之后立即 |
| 🔴 严格类型检查 | 代码质量 | 1-2 周 | 与 Phase 5 CI 同步 |
| 🟡 可观测性中间件 | 运维效率 | 2 周 | Phase 5 之后 |
| 🟡 地图深度集成 | 用户体验 | 2-3 周 | P2 Use Cases 之后 |
| 🟢 国际化基础设施 | 用户覆盖 | 3-4 周 | 功能稳定后 |
| 🟢 插件架构 | 可扩展性 | 4-6 周 | 长期规划 |

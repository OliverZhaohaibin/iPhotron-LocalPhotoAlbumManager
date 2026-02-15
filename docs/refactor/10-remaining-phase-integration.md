# Phase 1-4 遗留集成任务清单

> **版本**: v1.0 | **日期**: 2026-02-15  
> **目的**: 汇总 Phase 1-4 已完成模块中仍需完成的集成和迁移工作

---

## 概述

Phase 1-4 的核心模块（DI、EventBus、Use Cases、ViewModels、性能组件）已全部实现并通过测试，
但部分模块尚未与现有生产代码完成集成。本文档汇总所有遗留任务，按优先级排列。

---

## 1. Phase 2 遗留：P2 级 Use Cases（5 个未实现）

**现状**: 9/14 Use Cases 已实现，5 个 P2 级别尚未创建。

| Use Case | 对应现有逻辑位置 | 优先级 | 估计工作量 |
|----------|------------------|--------|-----------|
| `ManageTrash` | `library/trash_manager.py` | P2 | 1-2 天 |
| `AggregateGeoData` | `library/geo_aggregator.py` | P2 | 1-2 天 |
| `WatchFilesystem` | `library/filesystem_watcher.py` | P2 | 2-3 天 |
| `ExportAssets` | `core/export.py` | P2 | 2-3 天 |
| `ApplyEdit` | `core/*_resolver.py` 系列 | P2 | 3-4 天 |

**建议**:
- 每个 Use Case 遵循 `application/use_cases/base.py` 的 `BaseUseCase` 模式
- 从对应的 library/core 模块中提取业务逻辑，Use Case 仅做编排
- 需配套 ≥2 个单元测试

---

## 2. Phase 3 遗留：Qt ViewModel 迁移

**现状**: 纯 Python ViewModel 已创建，但现有 Qt ViewModel 调用方尚未迁移。

### 2.1 待迁移的 ViewModel

| 纯 Python VM | 对应 Qt VM | 调用方 | 状态 |
|-------------|-----------|--------|------|
| `PureAssetListViewModel` | `AssetListViewModel` | `asset_data_source.py`, controllers | ❌ 未迁移 |
| `AlbumTreeViewModel` | `AlbumViewModel` | coordinators, controllers | ❌ 未迁移 |
| `DetailViewModel` | 无对应 Qt VM | — | ✅ 直接可用 |

### 2.2 大文件拆分

以下文件超出 300 行目标：

| 文件 | 当前行数 | 目标 | 拆分方案 |
|------|---------|------|---------|
| `gui/facade.py` | 733 行 | ≤200 行 | 提取 DeletionService、RestorationService、ScanCoordinator |
| `app.py` | 580 行 | ≤300 行 | 提取 PathNormalizer、IndexSyncService |

### 2.3 MainCoordinator 精简

- 当前 MainCoordinator 包含 DI bootstrap 逻辑，需提取到独立的 `AppBootstrap` 类
- 目标: MainCoordinator ≤200 行，仅负责页面协调

---

## 3. Phase 4 遗留：性能模块服务集成

**现状**: 所有性能模块已实现并通过单元测试，但未接入现有服务。

| 新模块 | 需接入的现有服务 | 集成方式 | 状态 |
|--------|----------------|---------|------|
| `ParallelScanner` | `ScanCoordinator` / `LibraryService` | 替换串行扫描逻辑 | ❌ |
| `MemoryThumbnailCache` + `DiskThumbnailCache` | `ThumbnailCacheService` (Qt) | 作为后端注入 | ❌ |
| `ThumbnailService` (3 层) | `GenerateThumbnail` Use Case | 替换直接缓存调用 | ❌ |
| `VirtualAssetGrid` | `GalleryGridView` | 替换全量加载为虚拟化 | ❌ |
| `WeakAssetCache` | `AssetService` | 管理非活跃 Asset 引用 | ❌ |
| `MemoryMonitor` | `AppFacade` / 全局 | 启动时注册阈值告警 | ❌ |
| GPU Pipeline (`ShaderPrecompiler` 等) | `GLRenderer` | 替换即时编译为预编译 | ❌ |
| `CacheStatsCollector` | 缓存层全局 | 接入 L1/L2 命中率监控 | ❌ |

**建议**:
- 采用 Feature Flag 策略，允许运行时切换新旧实现
- 优先集成 `ParallelScanner` 和 3 层缓存（用户体感提升最大）
- GPU Pipeline 可延后，待 OpenGL 渲染路径稳定后再接入

---

## 4. Phase 1 遗留：遗留模型完全迁移

**现状**: `ManifestService` 已创建，但 `models/album.py` 和 `models/types.py` 仍在使用中。

**任务**:
- [ ] 搜索所有 `from iPhoto.models.album import` 引用，逐步替换为 `domain/models/core.py`
- [ ] 搜索所有 `from iPhoto.models.types import` 引用，迁移到 domain 层
- [ ] 在完成 2 个版本周期后移除带 `@deprecated` 标记的遗留文件

---

## 优先级排序建议

| 优先级 | 任务 | 原因 |
|--------|------|------|
| 🔴 高 | `gui/facade.py` 拆分 | 当前最大 God Object，阻碍可测试性 |
| 🔴 高 | ParallelScanner + 3 层缓存集成 | 用户性能体感最显著 |
| 🟡 中 | P2 Use Cases 实现 | 补全业务逻辑覆盖率 |
| 🟡 中 | Qt ViewModel 迁移 | 减少 Qt 依赖渗透 |
| 🟢 低 | 遗留模型迁移 | 有兼容层，可延后 |
| 🟢 低 | GPU Pipeline 集成 | 依赖 OpenGL 路径稳定 |

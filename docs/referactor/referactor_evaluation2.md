# Refactor Evaluation 2 (Post-Phase 4 Cleanup)

## 结论摘要

本次重构进一步推进了架构的现代化，重点在于解耦 `AppFacade` 与 UI 协调器的依赖，并引入了基于事件总线的后台扫描机制。

**主要成果**：
1.  **NavigationCoordinator 现代化**：不再依赖 `AppFacade.open_album`。现在直接使用 `AlbumService`，并在本地管理 `current_album_id` 和 `current_album_path` 状态。
2.  **扫描流程重构**：
    *   `ScanAlbumUseCase` 已升级，支持通过 `EventBus` 发布 `AlbumScanProgressEvent`。
    *   引入了 `BackgroundScanner` (Task)，用于在后台线程执行 `AlbumService.scan_album` 并将进度事件桥接到 Qt 信号。
    *   `MainCoordinator` 现在使用 `BackgroundScanner` 处理重新扫描请求，减少了对 `AppFacade` 的依赖。
3.  **UI 控制器解耦**：
    *   `ContextMenuController` 现在通过 `NavigationCoordinator` 获取当前相册路径，不再直接依赖 `AppFacade.current_album`。
4.  **测试修复**：
    *   修复了 `SQLiteAssetRepository` 测试以适应元数据默认值。
    *   修复了 `AssetListViewModel` 测试以适配 `AssetDTO` 和新的角色常量。
    *   清理了引用已删除旧控制器（如 `EditController`、`NavigationController`）的过时测试文件。

---

## 依据文档的阶段性评估

### ✅ Phase 1: 基础设施现代化
**状态：完成**
- 事件总线 (`EventBus`) 已被用于核心业务流程（扫描进度）。
- 依赖注入容器 (`DependencyContainer`) 在 `MainCoordinator` 和 `BackgroundScanner` 中得到有效应用。

### ✅ Phase 2: 仓储层重构
**状态：基本完成**
- GUI 主流程（Navigation -> ViewModel -> DataSource）完全基于 `IAssetRepository`。
- 旧的 `IndexStore` 仍存在于部分 `AppFacade` 及其服务中（作为兼容层），但在新的读取路径中已被旁路。

### 🟡 Phase 3: 应用层重构
**状态：显著进展**
- `AlbumService` 和 `ScanAlbumUseCase` 现在是核心扫描逻辑的入口。
- `AppFacade` 的职责被进一步剥离，仅作为遗留功能的桥接（如导入/导出服务）。
- 下一步：将 `ImportService` 和 `MoveService` 迁移为纯粹的应用层服务 (`Application Service`)，移除对 `AppFacade` 的依赖。

### ✅ Phase 4: GUI 层 MVVM 迁移
**状态：完成核心迁移**
- `NavigationCoordinator` 和 `MainCoordinator` 已完全适配新架构。
- `AssetListViewModel` 增加了 `refresh()` 能力，配合 `BackgroundScanner` 实现了扫描后的自动视图刷新。
- 遗留的 `AssetListModel` (旧) 虽仍由 `AppFacade` 创建，但已不再被主视图使用。

### 🟡 Phase 5: 性能优化
**状态：进行中**
- `ScanAlbumUseCase` 提供了更细粒度的进度反馈。
- 下一步：针对大相册加载进行性能基准测试。

---

## 剩余工作与建议

1.  **清理遗留测试**：大量集成测试仍依赖已删除的旧模块或旧 `AppFacade` 行为。需要系统性地重写这些测试以针对新的 Service/Coordinator 层。
2.  **完全移除 AppFacade**：
    *   将 `AssetImportService` 和 `AssetMoveService` 重构为独立的应用服务。
    *   将 `StatusBarController` 的信号连接完全迁移到 `EventBus` 或新的服务信号，断开与 `AppFacade` 的连接。
3.  **统一 IndexStore**：
    *   目前 `app.py` (legacy backend) 和 `SQLiteAssetRepository` (new) 并存。需要制定计划将 `app.py` 中的逻辑（如 `pair_live`）完全迁移到 Use Cases 中，并让 `IndexStore` 退役。

## 风险提示

- **测试覆盖率**：由于删除了大量失效的旧测试，当前的测试覆盖率可能有所下降。建议优先补充 `NavigationCoordinator` 和 `BackgroundScanner` 的集成测试。
- **AppFacade 状态同步**：虽然 `NavigationCoordinator` 维护了本地状态，但如果系统中仍有旧组件依赖 `facade.current_album`，可能会出现状态不一致。目前的重构已涵盖主要路径 (`MainCoordinator`, `ContextMenu`)，但需留意边缘情况。

# Refactor Evaluation (based on docs/referactor)

## 结论摘要

目前代码库**已部分完成**文档中提出的目标架构（MVVM + DDD）迁移，但仍处于**新旧架构并行**阶段。GUI 主流程已引入 **Coordinator + ViewModel + Repository** 结构，且应用层/领域层/基础设施层目录已建立；然而，仍保留多个**旧控制器与 Facade 体系**并与新架构交织使用，需要进一步推进 Phase 2–4 的收敛与清理。

本次检查额外完成：
- 移除已不再使用的旧控制器、旧模型辅助模块与重复服务。
- 补齐主 Coordinator 与 WindowManager 的接口契约，避免遗留接口调用断裂。
- 统一文档/注释对 global_index.db 的表述，清理 index.jsonl 旧概念。

---

## 依据文档的阶段性评估

### ✅ Phase 1: 基础设施现代化
**状态：基本完成**
- `src/iPhoto/domain`, `application`, `infrastructure`, `di`, `events` 等目录已建立。
- 已存在 `DependencyContainer`、`EventBus`、Repository 接口与服务实现。

**剩余问题**：
- 部分 GUI 与服务仍直接依赖旧 Facade 或 IndexStore（非 Repository）路径。

---

### 🟡 Phase 2: 仓储层重构
**状态：部分完成**
- `IAssetRepository` 已被 ViewModel 数据源调用。
- IndexStore 仍存在于部分 GUI Task / Service（如扫描、移动、更新）。

**建议下一步**：
- 将 GUI 的 IndexStore 直连逻辑迁移至 Repository 或应用层 Use Case。
- 用 Repository 统一访问 global_index.db，彻底收敛数据访问路径。

---

### 🟡 Phase 3: 应用层重构
**状态：部分完成**
- 存在 `AlbumService` / `AssetService` 等应用服务。
- 但 AppFacade 仍是大量 UI 逻辑与服务的入口。

**建议下一步**：
- 将 Facade 只保留桥接/兼容职责，逐步由 Application Service 接管 UI 行为。
- 通过 Use Case 取代 GUI 直接调用服务或 IndexStore。

---

### 🟡 Phase 4: GUI 层 MVVM 迁移
**状态：阶段性完成**
- `MainCoordinator` 使用 `AssetListViewModel` + `AssetDataSource`。
- Detail/Edit 逻辑逐步被 Coordinator 替代旧 Controller。

**仍存在的旧架构残留**：
- Facade 仍维护 AssetListModel 与旧 UI Service 体系。
- 部分 Controller 文件已无引用但仍存在（已在本次清理中移除）。

---

### ⚠️ Phase 5: 性能优化
**状态：待全面评估**
- IndexStore 已在多处使用，但缺乏整体性能基准对比。
- 未见统一的性能基准脚本或阶段性 KPI 记录。

**建议下一步**：
- 补充扫描/加载/缩略图性能基准并与目标指标对比。

---

### 🟡 Phase 6: 测试与文档
**状态：部分完成**
- 已出现 ViewModel 的单元测试。
- 但对 AppFacade 与 UI 行为的测试覆盖仍不足。

**建议下一步**：
- 为 Coordinator / Repository / Service 添加单元测试。
- 记录迁移策略与移除 Facade 的最终计划。

---

## 当前重构完成度判断

**总体完成度：约 55%–65%（中期阶段）**

- **已完成**：目标架构骨架、ViewModel/Coordinator 基础、Repository 接入
- **未完成**：Facade 与旧 Controller 清退、Repository 统一接管 IndexStore、全面 UI 服务改造

---

## 下一步建议（按优先级）

1. **清理 Facade 依赖路径**
   - 将 UI 调用逐步迁移至 Application Service + Use Case。
2. **统一 Repository 数据访问**
   - 将 GUI Task/Service 中的 IndexStore 访问替换为 Repository。
3. **全面移除旧 Controller 体系**
   - 替换遗留 UI Controller 或将其下沉至 Coordinator。
4. **补充性能基准与测试**
   - 针对扫描、缩略图、启动耗时进行 KPI 回归。

---

## 本次改动清单（对应 Phase 4 清理）

- 删除未被引用的旧控制器（旧 MVC 层残留）。
- 删除未使用的 AssetListModel 辅助模块（旧结构残留）。
- 增强 MainCoordinator 与 WindowManager 的接口一致性，确保 MVVM 主流程可替代旧 MainController。
- 清理注释中 `index.jsonl` 旧概念，统一为 global_index.db。


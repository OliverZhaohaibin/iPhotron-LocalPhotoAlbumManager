# iPhotron Compatibility Cleanup Migration Table

版本：v1.0  
阶段：第七阶段（长期治理状态）  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 1. 目的

本文档是 `iPhotron_compatibility_lifecycle_plan.md` 的可执行补充，将每一个遗留入口映射到：

- 当前所在位置（旧入口）
- 推荐替代入口（新入口）
- 是否允许新代码引用
- 清退优先级（High / Medium / Low）
- 预计清退版本节点

---

## 2. Compatibility 入口一览

| 旧入口 | 模块路径 | 类型 | 替代入口 | 新代码可用？ | 清退优先级 | 预计版本节点 |
|--------|----------|------|----------|------------|-----------|------------|
| `AppContext` | `src/iPhoto/appctx.py` | 全局上下文包装器 | `RuntimeContext` (`bootstrap/runtime_context.py`) | ❌ 不允许 | Medium | v6.0（GUI 层迁移后） |
| `app.py` 模块入口 | `src/iPhoto/app.py` | 遗留 facade shim | `application/use_cases/` 或 `application/services/` | ❌ 不允许 | High | v5.5（下一轮清退） |
| `AppFacade` | `src/iPhoto/gui/facade.py` | Qt 信号桥接层 | `presentation/qt/facade/` 分解的 facades | ❌ 不允许（新签名） | Medium | v6.0 |
| `LibraryManager` | `src/iPhoto/library/manager.py` | 协调外壳 | mixin 层或 `application/services/` | ⚠️ 只读（方法调用可用，禁止新增业务逻辑） | Low | 持续精炼，无明确废弃时间表 |
| `library_update_service.py` | `src/iPhoto/gui/services/library_update_service.py` | Qt 展现层协调器 | 已拆出的 application-layer delegates | ⚠️ 只读（调用已有方法可用，禁止新增逻辑） | Low | 持续精炼 |

---

## 3. 各入口详细说明

### 3.1 `AppContext` → `RuntimeContext`

**当前调用方（GUI 层）：**

| 文件 | 使用方式 |
|------|----------|
| `gui/main.py` | 初始化入口 |
| `gui/coordinators/main_coordinator.py` | 访问全局状态 |
| `gui/coordinators/navigation_coordinator.py` | 访问全局状态 |
| `gui/ui/main_window.py` | 访问全局状态 |
| `gui/ui/controllers/status_bar_controller.py` | 访问全局状态 |
| `gui/ui/controllers/dialog_controller.py` | 访问全局状态 |
| `presentation/qt/session/app_session.py` | 持有会话引用 |

**迁移路径：**

```python
# 旧写法（仅允许在 GUI 层的现有调用点使用）
from iPhoto.appctx import AppContext
ctx = AppContext()

# 新写法（所有新代码必须使用）
from iPhoto.bootstrap.runtime_context import RuntimeContext
ctx = RuntimeContext.create()
```

**迁移前提：** GUI 层中全部 `AppContext` 调用点迁移至 `RuntimeContext` 后，方可废弃。

---

### 3.2 `app.py` shim → `application/` use cases

**当前调用方：**

| 文件 | 调用的函数 |
|------|-----------|
| `cli.py` | `open_album`, `rescan`, `scan_specific_files`, `pair` |
| `gui/facade.py` | 多个委托调用 |
| `gui/ui/tasks/move_worker.py` | 移动操作 |
| `gui/ui/tasks/import_worker.py` | 导入操作 |
| `library/workers/rescan_worker.py` | 重扫操作 |

**迁移路径：**

```python
# 旧写法（不得在新代码中新增）
from iPhoto import app
app.open_album(path)

# 新写法
from iPhoto.application.use_cases.scan.open_album_use_case import OpenAlbumUseCase
use_case = OpenAlbumUseCase(...)
use_case.execute(path)
```

**清退条件：** 所有上述调用点改为直接调用 application use case 后，`app.py` 可整体删除。

---

### 3.3 `AppFacade` → `presentation/qt/facade/` 分解 facades

**当前状态：**  
`AppFacade` 已经开始拆分为三个专职 facade：

| 分解后的 Facade | 路径 | 职责 |
|----------------|------|------|
| `AssetFacade` | `presentation/qt/facade/asset_facade.py` | 资源操作 |
| `AlbumFacade` | `presentation/qt/facade/album_facade.py` | 相册操作 |
| `LibraryFacade` | `presentation/qt/facade/library_facade.py` | 相册库操作 |

**迁移路径：**

```python
# 旧写法（不得新增方法）
from iPhoto.gui.facade import AppFacade
facade = AppFacade(ctx)
facade.some_method()

# 新写法
from iPhoto.presentation.qt.facade.asset_facade import AssetFacade
facade = AssetFacade(ctx)
facade.some_method()
```

**清退条件：** 所有调用点迁移至分解后的 facade 后，`gui/facade.py` 可废弃。

---

### 3.4 `LibraryManager` — 持续精炼

**当前状态：** `LibraryManager` 已作为薄壳协调器保留，通过 mixin 委托实际逻辑。

**规则：**
- ✅ 允许通过已有 public API 调用
- ❌ 禁止在 `LibraryManager` 类体中新增业务逻辑
- ❌ 禁止将已迁出的 mixin 方法移回类体

**无明确废弃时间表。**

---

### 3.5 `library_update_service.py` — 持续精炼

**当前状态：** Qt 展现层协调器，已拆出大量 application-layer delegates。

**规则：**
- ✅ 允许调用已有方法
- ❌ 禁止新增内联文件 I/O（`open(...)`）
- ❌ 禁止直接引入 `iPhoto.infrastructure` 模块
- ❌ 禁止在协调器内部实现业务逻辑

**无明确废弃时间表。**

---

## 4. 清退优先级说明

| 优先级 | 含义 |
|--------|------|
| **High** | 应在下一个主版本发布前清退，已有完整替代方案 |
| **Medium** | 需要较大迁移工作量，目标在两个主版本内完成 |
| **Low** | 持续精炼但无明确废弃时间表，以"不增加负担"为原则 |

---

## 5. 进入下一阶段前的 backlog

以下是从代码库统计出的、为未来清退做准备的待办事项：

- [ ] 统计 `app.py` 的剩余调用点（共 ~5 个文件），逐一迁移到 application use cases
- [ ] 统计 `AppContext` 在 GUI 层以外的残留依赖（当前：仅 `appctx.py` 本身 + bootstrap）
- [ ] 逐步将 `AppFacade` 的调用方迁移至分解后的 `AssetFacade` / `AlbumFacade` / `LibraryFacade`
- [ ] 确认 `cli.py` 中对 `app.py` 的依赖是否可直接迁移到 CLI-specific use case wrappers
- [ ] 在每次 PR review 中确认没有新的 `AppContext` 或 `app.py` 引用进入正式层

---

## 6. 版本节点参考

| 版本节点 | 预期动作 |
|----------|---------|
| v5.5（近期） | `app.py` 调用点迁移完成；`app.py` 进入 bugfix-only 模式 |
| v6.0（中期） | GUI 层 `AppContext` → `RuntimeContext` 迁移完成；`AppFacade` 完成拆分 |
| v7.0+（长期） | `app.py` 和 `AppFacade` 彻底删除；`AppContext` 仅保留最小转发壳 |

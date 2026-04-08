# iPhotron Compatibility Layer Lifecycle Plan

版本：v1.0  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`  
阶段：第六阶段（工程化收官）

---

## 1. 背景

第五阶段已将 compatibility layer 分为三个等级（Class A / B / C），并制定了对应的维护规则。  
本文档在此基础上，进一步明确每一层的**生命周期策略**，包括：

- 长期保留层（Long-term Stable）
- 仅 bugfix 层（Bugfix-only）
- 未来可清退层（Scheduled for Removal）

---

## 2. 分级定义（回顾）

| 等级 | 描述 | 示例 |
|------|------|------|
| Class A | 长期保留 compatibility shell，仍有主动转发价值 | `AppContext` |
| Class B | deprecated-only shim，仅做转发，不允许新增签名 | `app.py` |
| Class C | 协调外壳，持续精炼但不废弃 | `LibraryManager` |

---

## 3. 各层生命周期策略

### 3.1 `AppContext` — Class A（长期保留）

**当前状态**：  
- 在 GUI 层广泛使用，作为全局上下文访问点
- 已被约束：正式层（application/, bootstrap/）禁止在运行时导入

**策略**：长期保留，持续维护

**规则**：
- 允许新增属性转发（attribute forwarding）
- 禁止新增业务逻辑
- 禁止重新引入已迁出的功能
- 如新代码需要访问上下文，优先使用 `RuntimeContext`

**清退条件**：  
当 GUI 层完全迁移至 `RuntimeContext` 后，可考虑废弃。当前无时间表。

---

### 3.2 `app.py` — Class B（Bugfix-only）

**当前状态**：  
- Deprecated shim，仅转发调用至 application use cases
- 有自动化约束：`tests/architecture/test_shim_no_business_logic.py`

**策略**：Bugfix-only，不演进

**规则**：
- 禁止新增函数签名
- 禁止新增业务逻辑
- 禁止定义类
- 只允许 bug 修复性修改（如修正转发路径错误）

**清退条件**：  
当所有外部调用方（含文档、脚本、历史 CI）均已更新至 application 层入口时，可完整删除。  
预计版本：v6.x 或后续重大版本。

---

### 3.3 `LibraryManager` — Class C（持续精炼，不废弃）

**当前状态**：  
- 薄壳协调对象，已将逻辑迁出至 mixin 与 application services
- 有自动化约束：`tests/architecture/test_manager_shell_regression.py`

**策略**：持续精炼，长期保留（作为 Qt 信号聚合点）

**规则**：
- 禁止新增业务规则
- 禁止重新引入已迁出至 mixin 的方法
- 允许增加新信号（Signal）以支持 UI 事件
- 允许增加协调性 helper（需保持薄壳风格）

**清退条件**：  
无计划清退。`LibraryManager` 作为 Qt 信号聚合点具有长期价值。

---

### 3.4 `facade.py` / `AppFacade` — Class C（协调外壳）

**当前状态**：  
- 聚合多个 application services，提供统一的 GUI 接口
- 不允许实现新业务

**策略**：长期保留，只能引用，不实现新业务

**规则**：
- 禁止在 facade 中实现新业务逻辑
- 所有新功能必须先在 application/use_cases/ 实现，再通过 facade 暴露
- 允许新增转发方法

---

### 3.5 `library_update_service.py` — Class C（最后一轮精炼中）

**当前状态**：  
- Qt presentation-layer coordinator
- 第三、四阶段已将多轮业务逻辑迁出至 application services
- 有自动化约束：`tests/architecture/test_library_update_no_business_creep.py`

**策略**：完成最后一轮精炼后转为 Bugfix-only

**规则**：
- 禁止新增 business decision 逻辑
- 禁止直接 import infrastructure
- 允许保留 Qt 事件桥接、worker 调度、UI reload mapping
- 允许 bugfix

---

## 4. 综合生命周期表

| 模块 | 等级 | 策略 | 新增业务逻辑 | 可清退 |
|------|------|------|-------------|--------|
| `AppContext` | Class A | 长期保留 | ❌ | 未来（无时间表）|
| `app.py` | Class B | Bugfix-only | ❌ | 是（v6.x+）|
| `LibraryManager` | Class C | 持续精炼 | ❌ | 否 |
| `AppFacade` | Class C | 长期保留 | ❌ | 否 |
| `library_update_service.py` | Class C | 精炼→Bugfix | ❌ | 否 |

---

## 5. 清退路线图

### 短期（当前阶段 / Phase 6）
- [ ] `library_update_service.py` 完成最后一轮精炼
- [ ] `LibraryManager` 补充 thin-shell regression tests（已完成）
- [ ] 所有约束规则接入 CI（已完成）

### 中期（v5.x – v6.x）
- [ ] 评估 `app.py` 的所有调用方是否已完全迁移
- [ ] 准备 `app.py` 废弃通知（deprecation notice）

### 长期（v7.x+）
- [ ] 待 GUI 层迁移至 `RuntimeContext` 后，评估 `AppContext` 废弃可行性
- [ ] 完成 compatibility layer 整体清退

---

## 6. 自动化约束汇总

| 约束 | 工具 |
|------|------|
| AppContext 不得在正式层运行时导入 | `tests/architecture/test_no_new_appctx_usage.py` / `tools/check_runtime_entry_usage.py` |
| adapter 不得直接 import infrastructure | `tests/architecture/test_adapter_no_infra_imports.py` / `tools/check_adapter_boundary.py` |
| `app.py` 不得含业务逻辑 | `tests/architecture/test_shim_no_business_logic.py` |
| `LibraryManager` 保持薄壳 | `tests/architecture/test_manager_shell_regression.py` |
| `library_update_service` 不回流业务逻辑 | `tests/architecture/test_library_update_no_business_creep.py` |
| 所有约束统一 CI 入口 | `tools/check_architecture.py` / `.github/workflows/test.yml` |

---

## 7. 维护责任

本文档由项目维护者在每个开发阶段结束时审查。  
如需修改任一层的生命周期策略，必须同步更新本文档。

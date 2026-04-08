# iPhotron 第四阶段维护边界规范

版本：v1.0  
适用分支：`copilot/refactor-next-steps`  
适用阶段：Phase 4（架构定型）

---

## 1. 背景

第三阶段重构完成后，iPhotron 项目已经建立了清晰的正式入口（`RuntimeContext`）和兼容壳（`AppContext`、`app.py`）的主从关系。第四阶段的目标不是继续大规模拆分，而是：

- **收尾**：完成剩余瘦身
- **定型**：固化各层边界
- **规范化**：为长期维护建立制度性约束

本文档是第四阶段的维护边界规范，所有新代码必须遵守。

---

## 2. 运行时入口规范

### 2.1 正式入口

```
src/iPhoto/bootstrap/runtime_context.py → RuntimeContext
```

**规则：**
- **新代码必须使用 `RuntimeContext`**，禁止直接依赖 `AppContext`。
- `RuntimeContext.create()` 是唯一的正式构造入口。
- `RuntimeContext` 的属性和方法由 `RuntimeEntryContract` 协议定义，不得随意扩展。

### 2.2 正式合约

```
src/iPhoto/application/contracts/runtime_entry_contract.py → RuntimeEntryContract
```

- 新代码的类型注解应使用 `RuntimeEntryContract`，而不是 `RuntimeContext` 具体类。
- 测试应使用满足该协议的轻量级 fake 对象，不需要构建完整 Qt 应用栈。

### 2.3 兼容层（长期保留）

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/iPhoto/appctx.py` → `AppContext` | Compatibility shell | 仅作 API 兼容代理，不得新增业务逻辑 |
| `src/iPhoto/gui/facade.py` → `AppFacade` | Compatibility shell | 仅作信号聚合，不得新增业务规则 |

### 2.4 deprecated-only shim

| 文件 | 说明 |
|------|------|
| `src/iPhoto/app.py` | 仅转发调用，新代码零引用，标记为 deprecated |

---

## 3. 层次边界规范

### 3.1 各层职责

```
┌─────────────────────────────────────────────┐
│  Presentation layer (UI)                    │
│  - Qt widgets, views, controllers           │
│  - Adapters: signal relay only              │
│  - Facade: composition only                 │
├─────────────────────────────────────────────┤
│  Application layer                          │
│  - Use cases: business flow orchestration   │
│  - Services: business rule holders          │
│  - Contracts: formal API interfaces         │
│  - Policies: decision rules                 │
├─────────────────────────────────────────────┤
│  Domain layer                               │
│  - Models: pure data objects                │
│  - No Qt, no I/O                            │
├─────────────────────────────────────────────┤
│  Infrastructure layer                       │
│  - Database repositories                    │
│  - File system access                       │
│  - External service adapters                │
└─────────────────────────────────────────────┘
```

### 3.2 跨层调用规则

| From → To | 允许 | 说明 |
|-----------|------|------|
| Presentation → Application | ✅ | 调用 use case / service |
| Presentation → Domain | ✅ | 读取模型数据 |
| Presentation → Infrastructure | ❌ | 禁止，必须通过 application 层 |
| Application → Domain | ✅ | 核心关系 |
| Application → Infrastructure | ✅ | 通过接口 |
| Domain → Application/Infrastructure | ❌ | 禁止，Domain 层不向上依赖 |

---

## 4. Adapter 层规范

### 4.1 Adapter 的职责边界

Adapter 位于 `src/iPhoto/presentation/qt/adapters/`。

**允许：**
- Qt 信号转发（`connect`、`emit`）
- 将 application-layer 结果转换为 Qt 信号 payload
- 高频信号的节流/批处理

**禁止：**
- 业务规则或条件判断
- 直接访问基础设施（数据库、文件系统）
- 持有 worker 生命周期
- 复制 façade 逻辑

### 4.2 防止 adapter 膨胀

如果一个 adapter 开始增长为"新中间层"（例如包含 `if` 分支判断业务逻辑），该逻辑必须迁移到 application service 或 use case。

---

## 5. LibraryManager 规范

### 5.1 目标状态

`LibraryManager` 应接近以下最终形态：
- QObject signal carrier
- Compatibility API holder  
- Service composition shell（多数方法为委托调用）

### 5.2 禁止在 LibraryManager 中新增

- 业务规则
- 新的本地状态
- 直接的基础设施访问（除了 Qt-bound 的 QFileSystemWatcher 等）

### 5.3 方法分类

| 类型 | 说明 |
|------|------|
| Signal 声明 | 必须保留，QObject 职责 |
| Delegation methods | 推荐，转发到 mixin / service |
| Business logic | 禁止新增，存量应逐步迁移 |

---

## 6. library_update_service.py 规范

### 6.1 目标状态

更接近纯 presentation adapter / coordinator：
- 接收 Qt 事件
- 调用 use case / service
- 将结果转换为 UI reload / refresh 动作
- 转发 Qt signal

### 6.2 应保留

- Qt signal 声明和 emit
- Worker / task_manager 对接
- 对 use case / service 的调用
- 结果到 UI reload/refresh 的映射

### 6.3 应继续迁出

- 仍残留的流程性判断
- reload / refresh 触发条件中可下沉的规则
- restore / scan / move 之后的非 UI 业务后处理

---

## 7. 新代码开发约束

### 7.1 硬性规则

1. **新代码禁止 `from iPhoto.appctx import AppContext`**  
   使用 `from iPhoto.bootstrap.runtime_context import RuntimeContext`

2. **新代码禁止向 `app.py` 添加任何实现**  
   它只能保持现有的委托调用

3. **新代码禁止向 adapter 添加业务逻辑**  
   adapter 只做信号转发

4. **新代码不得在 LibraryManager 中添加业务规则**  
   新业务必须在 `application/use_cases/` 或 `application/services/` 中实现

### 7.2 建议规则

1. 尽量通过 `RuntimeEntryContract` 类型注解依赖，而不是 `RuntimeContext` 具体类
2. 测试优先使用满足 `RuntimeEntryContract` 协议的轻量级 fake
3. 新的 adapter 必须有对应的单元测试验证信号转发

---

## 8. 测试覆盖要求

Phase 4 要求以下测试覆盖：

| 测试目标 | 测试文件 |
|----------|----------|
| RuntimeContext contract | `tests/application/test_phase4_runtime_contracts.py` |
| Compatibility shell contract | `tests/application/test_phase4_runtime_contracts.py` |
| LibraryManager thin-shell behavior | `tests/library/test_manager_thin_shell.py` |
| LibraryUpdateAdapter boundary | `tests/presentation/qt/test_library_update_adapter.py` |

---

## 9. Definition of Done（Phase 4）

- [ ] `RuntimeEntryContract` 协议已定义并有测试覆盖
- [ ] `RuntimeContext` 满足 `RuntimeEntryContract`
- [ ] `AppContext` 仅作兼容代理，测试验证不自建依赖
- [ ] `app.py` 仅作 shim，有明确 deprecated 标注
- [ ] `LibraryManager` 方法多数为委托，无新增业务规则
- [ ] `library_update_service.py` 更接近纯 adapter，有对应测试
- [ ] adapter 边界有文档和测试覆盖
- [ ] 所有 CI 测试通过

# iPhotron 第五阶段收官规范

## 1. 概述

本文件是第五阶段（Phase 5）架构收官的正式规范文档，记录已完成的架构治理目标、
边界约束规则、自动化验证手段，以及后续维护指南。

---

## 2. 阶段目标回顾

第五阶段的核心目标是**稳固已有分层架构、阻止边界退化**，具体包括：

| 目标 | 状态 |
|------|------|
| LibraryManager 最终瘦身为薄壳协调器 | ✅ 已完成（Phase 2 mixin 拆分 + Phase 4 服务委托） |
| LibraryUpdateService 最终适配器精化 | ✅ 已完成（Phase 3 适配器边界建立） |
| app.py 稳定为仅委托的弃用垫片 | ✅ 已完成（Phase 4 Class B 分类） |
| appctx.py 稳定为兼容性代理 | ✅ 已完成（Phase 4 Class A 分类） |
| gui/facade.py 无新业务逻辑 | ✅ 已验证（仅信号转发与子 facade 组合） |
| 架构测试覆盖三大边界规则 | ✅ 新增 `tests/architecture/` |
| CLI 检查工具 | ✅ 新增 `tools/check_*.py` |

---

## 3. 架构边界规则（可机器验证）

### 规则 A：正式层禁止运行时导入 AppContext

**覆盖路径：** `src/iPhoto/application/`、`src/iPhoto/bootstrap/`

正式代码层（use_cases、application services、bootstrap）绝对禁止在运行时
（非 `TYPE_CHECKING` 块内）导入 `AppContext`。

```
# 违规示例
from iPhoto.appctx import AppContext   # ← 禁止出现在正式层

# 合规示例（仅类型注解用途）
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from iPhoto.appctx import AppContext
```

**自动化验证：**
- 测试：`tests/architecture/test_no_new_appctx_usage.py`
- 工具：`python tools/check_runtime_entry_usage.py`

---

### 规则 B：适配器层不得直接导入基础设施

**覆盖路径：** `src/iPhoto/presentation/qt/adapters/`、`src/iPhoto/gui/services/`

适配器模块只能通过应用服务（`application/services/` 或 `application/use_cases/`）
间接使用基础设施。禁止直接 import `iPhoto.infrastructure.*`。

```
# 违规示例（在适配器中）
from iPhoto.infrastructure.db.index_repository import IndexRepository  # ← 禁止

# 合规示例
from iPhoto.application.services.library_scan_service import LibraryScanService
```

**自动化验证：**
- 测试：`tests/architecture/test_adapter_no_infra_imports.py`
- 工具：`python tools/check_adapter_boundary.py`

---

### 规则 C：垫片文件不得包含业务逻辑

**覆盖文件：** `src/iPhoto/app.py`

`app.py` 是 Phase 4 分类 Class B 弃用垫片。允许的内容：
- 惰性 import（函数内部 import）
- 单行委托调用至应用层 use case
- 简单的可选参数传递

禁止的内容：
- `for` / `while` 循环
- 类定义
- 嵌套 if-elif-else 业务判断树
- 数据变换逻辑

**自动化验证：**
- 测试：`tests/architecture/test_shim_no_business_logic.py`

---

## 4. 文件分类与维护职责

### Class A — 长期保留的兼容性代理

| 文件 | 说明 |
|------|------|
| `src/iPhoto/appctx.py` | `AppContext` 兼容壳，属性全部转发至 `RuntimeContext` |

**维护规则：** 只允许新增属性转发；禁止添加业务逻辑或新的依赖构建。

---

### Class B — 弃用垫片（保留至下一次大清理）

| 文件 | 说明 |
|------|------|
| `src/iPhoto/app.py` | 模块级函数均委托至应用层 use case |

**维护规则：** 禁止添加新的函数签名或业务规则；只允许修复委托链中的 bug。

---

### Class C — 协调外壳（可演化，禁止添加业务规则）

| 文件 | 说明 |
|------|------|
| `src/iPhoto/library/manager.py` | mixin 聚合 + 应用服务委托，230 行左右 |
| `src/iPhoto/gui/facade.py` | 信号聚合 + 子 facade 转发，500 行左右 |

**维护规则：** 新业务逻辑必须进入 `application/use_cases/` 或 mixin 模块；
`manager.py` / `facade.py` 只引用，不实现。

---

## 5. 新增/改造文件清单

| 文件 | 类型 | 描述 |
|------|------|------|
| `tests/architecture/__init__.py` | 新增 | 包初始化 |
| `tests/architecture/test_no_new_appctx_usage.py` | 新增 | 规则 A 自动化测试 |
| `tests/architecture/test_adapter_no_infra_imports.py` | 新增 | 规则 B 自动化测试 |
| `tests/architecture/test_shim_no_business_logic.py` | 新增 | 规则 C 自动化测试 |
| `tools/check_runtime_entry_usage.py` | 新增 | 规则 A CLI 检查工具 |
| `tools/check_adapter_boundary.py` | 新增 | 规则 B CLI 检查工具 |
| `docs/refactor/iPhotron_第五阶段收官规范.md` | 新增 | 本文件 |

---

## 6. CI 集成建议

在 CI 流水线中添加以下步骤以防止边界退化：

```yaml
- name: Architecture boundary checks
  run: |
    python tools/check_runtime_entry_usage.py
    python tools/check_adapter_boundary.py
    python -m pytest tests/architecture/ -v
```

---

## 7. 后续维护说明

1. **新增 use case**：在 `application/use_cases/` 中添加，命名以 `_use_case.py` 结尾。
2. **新增 GUI 功能**：在 `presentation/qt/` 或 `gui/services/` 中添加适配器；
   通过应用服务调用业务逻辑，不得绕过应用层直接操作数据库或文件系统。
3. **新增设置项**：通过 `RuntimeContext` 或 `SettingsManager` 暴露，
   不得在 `AppContext` 中添加新属性。
4. **迁移遗留调用**：遗留代码中对 `AppContext` 或 `app.py` 的调用，
   迁移时改为使用 `RuntimeContext.create()` 或对应的 use case。

---

*本文档由第五阶段自动化收官流程生成，最后更新：2025 年第五阶段收官。*

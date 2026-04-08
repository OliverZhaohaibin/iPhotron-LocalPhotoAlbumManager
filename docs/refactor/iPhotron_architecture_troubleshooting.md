# iPhotron Architecture Troubleshooting Guide

版本：v1.0  
阶段：第七阶段（长期治理状态）  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 概述

本文档帮助开发者快速诊断并修复架构违规错误。架构检查在两个阶段运行：

1. **提交前（pre-commit hook）**：`python tools/check_architecture.py`
2. **CI（Pull Request 检查）**：GitHub Actions workflow `test.yml`

---

## 快速诊断入口

```bash
# 运行所有 CLI 静态检查
python tools/check_architecture.py

# 运行单项检查
python tools/check_architecture.py --only appctx
python tools/check_architecture.py --only adapter

# 运行 pytest 架构回归测试
python -m pytest tests/architecture/ -v

# 运行完整测试套件
QT_QPA_PLATFORM=offscreen python -m pytest tests/ --tb=short
```

---

## 常见架构违规与修复方法

### 问题 1：`AppContext` 在正式层被运行时导入

**错误信息（来自 `check_runtime_entry_usage.py`）：**
```
VIOLATION: iPhoto/application/services/my_service.py
  imports AppContext at runtime (not guarded by TYPE_CHECKING)
```

**原因：** `AppContext` 是遗留的全局上下文包装器，不允许在 `application/` 或 `bootstrap/` 模块中于运行时导入。

**修复方法：**

```python
# ❌ 违规写法
from iPhoto.appctx import AppContext

class MyService:
    def do_something(self):
        ctx = AppContext()
        ...

# ✅ 方案 A：改用 RuntimeContext（推荐）
from iPhoto.bootstrap.runtime_context import RuntimeContext

class MyService:
    def do_something(self):
        ctx = RuntimeContext.create()
        ...

# ✅ 方案 B：如果只需要类型注解，用 TYPE_CHECKING 守卫
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iPhoto.appctx import AppContext

class MyService:
    def __init__(self, ctx: "AppContext") -> None:
        self._ctx = ctx
```

---

### 问题 2：Adapter 模块直接引入 Infrastructure 层

**错误信息（来自 `check_adapter_boundary.py`）：**
```
VIOLATION: iPhoto/presentation/qt/adapters/my_adapter.py
  imports from iPhoto.infrastructure.db.connection_pool
```

**原因：** `presentation/qt/adapters/` 和 `gui/services/` 中的模块不允许直接引入 `iPhoto.infrastructure.*`，必须通过 application services 访问基础设施。

**修复方法：**

```python
# ❌ 违规写法（在 adapter/services 中）
from iPhoto.infrastructure.db.connection_pool import get_connection

class MyAdapter:
    def load(self):
        conn = get_connection()
        ...

# ✅ 正确写法：通过 application service 访问
from iPhoto.application.services.my_data_service import MyDataService

class MyAdapter:
    def __init__(self, service: MyDataService) -> None:
        self._service = service

    def load(self):
        return self._service.get_data()
```

---

### 问题 3：`app.py` shim 被新增了函数或类

**错误信息（来自 `tests/architecture/test_shim_no_business_logic.py`）：**
```
FAILED - app.py defines unexpected new function: my_new_function
```

**原因：** `app.py` 是 deprecated shim，不允许添加任何新的函数、类或循环。

**修复方法：**

```python
# ❌ 违规写法
# src/iPhoto/app.py
def my_new_function(path: Path) -> None:
    ...  # 新业务逻辑

# ✅ 正确写法：在 application use case 中实现新逻辑
# src/iPhoto/application/use_cases/my_domain/my_use_case.py
class MyUseCase:
    def execute(self, path: Path) -> None:
        ...
```

---

### 问题 4：`LibraryManager` 被添加了业务逻辑

**错误信息（来自 `tests/architecture/test_manager_shell_regression.py`）：**
```
FAILED - LibraryManager redefines mixin methods: ['scan_library']
```

**原因：** `LibraryManager` 是薄壳协调器，业务逻辑应保留在对应的 mixin 或 application service 中。

**修复方法：**

```python
# ❌ 违规写法
class LibraryManager:
    def scan_library(self, path: Path) -> None:
        # 直接实现扫描逻辑...
        for item in path.iterdir():
            ...

# ✅ 正确写法：在对应 mixin 中实现
# src/iPhoto/library/mixins/scan_mixin.py
class ScanMixin:
    def scan_library(self, path: Path) -> None:
        for item in path.iterdir():
            ...

class LibraryManager(ScanMixin, ...):
    pass  # 委托给 mixin
```

---

### 问题 5：`library_update_service.py` 新增了内联文件 I/O 或 infrastructure 引用

**错误信息（来自 `tests/architecture/test_library_update_no_business_creep.py`）：**
```
FAILED - library_update_service.py contains inline open() calls
FAILED - library_update_service.py imports from iPhoto.infrastructure
```

**原因：** `library_update_service.py` 是 Qt 展现层协调器，不应直接做文件 I/O 或引入基础设施层。

**修复方法：**

```python
# ❌ 违规写法
class LibraryUpdateService(QObject):
    def _save_result(self, data):
        with open("output.json", "w") as f:  # 不允许！
            json.dump(data, f)

# ✅ 正确写法：通过 application service 处理 I/O
class LibraryUpdateService(QObject):
    def __init__(self, persist_service: PersistScanResultUseCase) -> None:
        self._persist = persist_service

    def _save_result(self, data):
        self._persist.execute(data)  # 委托给 use case
```

---

## 架构检查分类说明

| 类型 | 命令 | 检查内容 | 运行时机 |
|------|------|----------|---------|
| CLI 静态检查 | `python tools/check_architecture.py` | `AppContext` 导入边界；adapter → infrastructure 边界 | pre-commit + CI |
| pytest 架构回归 | `python -m pytest tests/architecture/ -v` | shim 规则；manager shell；library_update creep；adapter imports | CI |
| 全量集成测试 | `python -m pytest tests/ --tb=short` | 所有功能和单元测试 | CI |

---

## pre-commit hook 不工作

如果 pre-commit hook 没有运行架构检查，执行以下步骤重新初始化：

```bash
# 安装 pre-commit
pip install pre-commit

# 重新安装 hooks
pre-commit install

# 手动触发一次（验证）
pre-commit run --all-files
```

确认 `.pre-commit-config.yaml` 中包含：
```yaml
- repo: local
  hooks:
    - id: architecture-checks
      name: Architecture boundary checks
      entry: python tools/check_architecture.py
      language: python
      always_run: true
      pass_filenames: false
```

---

## CI 失败但本地通过

可能原因：

1. **本地 Python 版本不同**：CI 使用 Python 3.12，确保本地使用相同版本。
2. **依赖未安装**：运行 `pip install -e ".[test]"` 确保所有测试依赖已安装。
3. **环境变量缺失**：CI 设置了 `QT_QPA_PLATFORM=offscreen` 和 `NUMBA_DISABLE_JIT=1`，本地运行时也应设置：
   ```bash
   export QT_QPA_PLATFORM=offscreen
   export NUMBA_DISABLE_JIT=1
   python -m pytest tests/ --tb=short
   ```
4. **分支未与 `main` 同步**：合并 `main` 的最新提交后重新运行测试。

---

## 相关文档

- [`docs/refactor/iPhotron_compatibility_lifecycle_plan.md`](iPhotron_compatibility_lifecycle_plan.md) — 生命周期策略
- [`docs/refactor/iPhotron_compatibility_cleanup_table.md`](iPhotron_compatibility_cleanup_table.md) — 迁移任务表
- [`CONTRIBUTING.md`](../../CONTRIBUTING.md) — 开发者规范

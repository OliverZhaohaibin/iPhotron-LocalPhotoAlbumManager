# iPhotron 第七阶段长期治理规范

版本：v1.0  
阶段：第七阶段（长期治理状态）  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 1. 概述

第七阶段标志着 iPhotron 从"重构期"切换到"**长期治理状态**"。  
架构的主要结构已在第一至第六阶段建立完毕，本阶段的目标不是继续大规模拆分，而是：

1. 确保已建立的架构边界不被侵蚀
2. 统一开发者体验，让后续维护更容易遵守规则
3. 为未来版本的 legacy 清退做好可执行准备

---

## 2. 长期治理原则

### 2.1 架构边界不回流（No Boundary Regression）

以下边界由 CI 自动检查，任何 PR 不得引入回归：

| 边界 | 检查工具 |
|------|---------|
| `AppContext` 不在正式层运行时导入 | `tools/check_architecture.py --only appctx` |
| Adapter 不直接引入 Infrastructure | `tools/check_architecture.py --only adapter` |
| `app.py` shim 不新增函数/类/循环 | `pytest tests/architecture/test_shim_no_business_logic.py` |
| `LibraryManager` 保持薄壳 | `pytest tests/architecture/test_manager_shell_regression.py` |
| `library_update_service.py` 不积累业务逻辑 | `pytest tests/architecture/test_library_update_no_business_creep.py` |
| Adapter 模块不引入 Infrastructure | `pytest tests/architecture/test_adapter_no_infra_imports.py` |

### 2.2 新代码放置规则（Where Does New Code Go）

在添加新功能前，按以下决策树确认代码落点：

```
新代码是什么类型？
├─ 业务规则 / 领域逻辑
│   └─ → application/use_cases/ 或 domain/
├─ 基础设施实现（数据库、文件系统、外部 API）
│   └─ → infrastructure/
├─ Qt 信号桥接 / UI 事件协调
│   └─ → gui/services/ 或 gui/coordinators/
├─ 新屏幕 / 新控件
│   └─ → gui/ui/widgets/ 或 gui/ui/controllers/
├─ 纯算法 / 数学工具
│   └─ → core/ 或 core/geo_utils.py
└─ 与平台无关的数据模型
    └─ → domain/ 或 models/
```

### 2.3 Legacy 层只减不增（Compatibility Layer Must Shrink）

对以下模块，采取"只减不增"原则：

| 模块 | 规则 |
|------|------|
| `app.py` | 禁止新增函数、类、复杂逻辑 |
| `AppContext` | 禁止新增业务方法；允许属性转发 |
| `AppFacade` (`gui/facade.py`) | 禁止新增方法签名；优先在分解后的 facade 中添加 |
| `LibraryManager` 类体 | 禁止新增业务逻辑；新逻辑放到对应 mixin 或 application service |

---

## 3. 日常开发工作流

### 3.1 提交前（本地）

```bash
# 步骤 1：运行 CLI 静态架构检查
python tools/check_architecture.py

# 步骤 2：运行 pytest 架构回归测试
python -m pytest tests/architecture/ -v

# 步骤 3：运行全量测试
QT_QPA_PLATFORM=offscreen python -m pytest tests/ --tb=short

# （可选）代码风格检查
ruff check .
black --check .
```

或者安装 pre-commit hook，在 `git commit` 时自动运行步骤 1：

```bash
pip install pre-commit
pre-commit install
```

### 3.2 CI 检查流程

每次 PR 触发以下检查（按顺序）：

1. `python tools/check_architecture.py` — CLI 静态检查
2. `python -m pytest tests/architecture/ -v` — 架构回归套件
3. `python -m pytest tests/ --tb=short -v` — 全量集成测试

所有步骤必须全部通过，PR 方可合并。

---

## 4. Compatibility Layer 清退路线图

### 4.1 当前状态（第七阶段起点）

| 模块 | 分级 | 状态 |
|------|------|------|
| `AppContext` | Class A（长期保留） | 活跃，GUI 层广泛使用 |
| `app.py` | Class B（仅 bugfix） | 遗留 shim，~5 个调用文件 |
| `AppFacade` | Class B（仅 bugfix） | 已开始拆分为 3 个专职 facade |
| `LibraryManager` | Class C（协调外壳） | 持续精炼，无废弃时间表 |
| `library_update_service.py` | Class C（协调外壳） | 持续精炼，无废弃时间表 |

### 4.2 清退目标版本

| 版本节点 | 预期动作 |
|----------|---------|
| v5.5（近期） | `app.py` 调用点全面迁移至 application use cases；`app.py` 进入 bugfix-only |
| v6.0（中期） | GUI 层 `AppContext` 全面迁移至 `RuntimeContext`；`AppFacade` 拆分完成 |
| v7.0+（长期） | `app.py` 彻底删除；`AppFacade` 彻底废弃；`AppContext` 保留最小转发壳 |

详细迁移任务见 [`iPhotron_compatibility_cleanup_table.md`](iPhotron_compatibility_cleanup_table.md)。

---

## 5. 架构治理文档体系

第七阶段建立的完整文档体系如下：

| 文档 | 目的 |
|------|------|
| [`iPhotron_compatibility_lifecycle_plan.md`](iPhotron_compatibility_lifecycle_plan.md) | 每个 compatibility 层的生命周期策略 |
| [`iPhotron_compatibility_cleanup_table.md`](iPhotron_compatibility_cleanup_table.md) | 可执行的迁移任务表（旧入口 → 新入口） |
| [`iPhotron_architecture_troubleshooting.md`](iPhotron_architecture_troubleshooting.md) | 常见架构违规的诊断与修复 |
| [`iPhotron_第七阶段长期治理规范.md`](iPhotron_第七阶段长期治理规范.md) | 本文档（长期治理原则与工作流） |
| [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | 开发者日常参考（架构规则 + PR 规范） |

---

## 6. 架构检查工具参考

### `tools/check_architecture.py`

统一 CLI 入口，支持以下选项：

| 选项 | 说明 |
|------|------|
| `--only appctx` | 只运行 AppContext 导入边界检查 |
| `--only adapter` | 只运行 Adapter → Infrastructure 边界检查 |
| `--verbose` | 显示更详细的输出（即使检查通过） |
| `--src PATH` | 覆盖源码根目录（默认：`src/iPhoto`） |

### `tests/architecture/` 套件

| 测试文件 | 保护的边界 |
|---------|-----------|
| `test_no_new_appctx_usage.py` | 正式层不引入 AppContext |
| `test_adapter_no_infra_imports.py` | Adapter 层不引入 Infrastructure |
| `test_shim_no_business_logic.py` | app.py shim 不积累业务逻辑 |
| `test_manager_shell_regression.py` | LibraryManager 保持薄壳 |
| `test_library_update_no_business_creep.py` | library_update_service 不积累业务逻辑 |

---

## 7. 进入第八阶段的条件

第七阶段验收标准（Definition of Done）：

- [x] 分支与 `main` 完全同步
- [x] `tools/check_architecture.py` 支持 `--only` / `--verbose` 参数
- [x] `docs/refactor/iPhotron_compatibility_cleanup_table.md` 已建立
- [x] `docs/refactor/iPhotron_architecture_troubleshooting.md` 已建立
- [x] `docs/refactor/iPhotron_第七阶段长期治理规范.md` 已建立
- [x] README / CONTRIBUTING 文档已收口
- [x] 全量测试通过
- [ ] legacy 清退 backlog 已在 issue tracker 中登记（可选，推迟到第八阶段）

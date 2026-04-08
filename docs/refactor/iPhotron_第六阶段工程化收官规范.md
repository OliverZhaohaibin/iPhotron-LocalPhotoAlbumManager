# iPhotron 第六阶段工程化收官规范

版本：v1.0  
适用分支：`copilot/refactor-iphotron-branch-review`  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 1. 阶段定位

第六阶段的定位是：**工程化收官 + CI 治理落地 + 最后一轮结构精炼**。

在第五阶段已经完成的基础上：
- 有收官规范
- 有 architecture tests
- 有 CLI 检查工具
- 有 compatibility layer 分类
- 有 shim / adapter / runtime entry 的明确约束

第六阶段的目标是：
1. 将架构检查真正接入 CI / pre-commit
2. 统一架构检查入口
3. 对剩余厚重点做最后一轮精炼
4. 给 compatibility layer 制定更清晰的清退策略
5. 完善文档与贡献规范
6. 让项目进入"长期稳定维护态"

---

## 2. 已完成工作

### 2.1 CI / pre-commit 集成

- `.github/workflows/test.yml` 现在包含以下步骤：
  1. `python tools/check_architecture.py` — 统一架构检查
  2. `python -m pytest tests/architecture/ -v` — 架构测试套件
  3. `python -m pytest tests/ --tb=short -v` — 完整测试套件

- `.pre-commit-config.yaml` 已创建，包含：
  - Ruff lint + format
  - architecture boundary checks（pre-commit 钩子）

### 2.2 统一架构检查入口

- `tools/check_architecture.py` 已创建。
- 聚合调用：
  - `check_runtime_entry_usage.py`
  - `check_adapter_boundary.py`
- 返回统一 exit code，CI 只需一个命令。

### 2.3 新增架构回归测试

新增测试文件（均通过）：

| 文件 | 约束内容 |
|------|---------|
| `tests/architecture/test_manager_shell_regression.py` | LibraryManager 必须保持薄壳结构 |
| `tests/architecture/test_library_update_no_business_creep.py` | LibraryUpdateService 不得回流业务逻辑 |

架构测试总数：14 项（全部通过）

### 2.4 Compatibility Layer 生命周期计划

- `docs/refactor/iPhotron_compatibility_lifecycle_plan.md` 已创建。
- 明确了每一层的策略（长期保留 / Bugfix-only / 精炼中）。
- 制定了短期 / 中期 / 长期清退路线图。

### 2.5 文档与贡献规范

- `README.md` 增加了架构规则说明区块。
- `CONTRIBUTING.md` 增加了架构规则与工程流程说明。

---

## 3. 架构约束规则（第六阶段完整版）

### 3.1 AppContext 规则
- 新代码（`application/`, `bootstrap/` 目录下）禁止在运行时 import `AppContext`
- 只允许 `TYPE_CHECKING` 块中出现
- 工具：`tools/check_runtime_entry_usage.py` + `tests/architecture/test_no_new_appctx_usage.py`

### 3.2 Adapter 边界规则
- `presentation/qt/adapters/` 和 `gui/services/` 禁止直接 import `iPhoto.infrastructure.*`
- 必须通过 application services 访问 infrastructure
- 工具：`tools/check_adapter_boundary.py` + `tests/architecture/test_adapter_no_infra_imports.py`

### 3.3 Shim 规则
- `app.py` 禁止业务逻辑（循环、类定义）
- 只允许转发（delegation）
- 工具：`tests/architecture/test_shim_no_business_logic.py`

### 3.4 LibraryManager 薄壳规则
- 模块内只允许定义一个类（`LibraryManager` 自身）
- 禁止模块级循环
- 必须暴露指定公共 API（`root`, `bind_path`, `list_albums`, `list_children`, `scan_tree`, `shutdown`）
- 禁止重新定义 mixin 层方法
- 工具：`tests/architecture/test_manager_shell_regression.py`

### 3.5 LibraryUpdateService 无业务回流规则
- 禁止 import `iPhoto.infrastructure`
- 禁止模块级循环
- 必须保留对 application services 的委托引用
- 禁止内联 `open()` 文件 I/O
- 工具：`tests/architecture/test_library_update_no_business_creep.py`

---

## 4. 行为验收（不可回归能力）

以下核心功能在任何阶段性提交后均不得出现回归：

| 功能 | 说明 |
|------|------|
| library bind | 绑定 Basic Library 路径 |
| scan | 扫描相册目录 |
| restore | 从"最近删除"恢复 |
| move / delete | 资产移动与删除 |
| import | 导入新资产 |
| pair live | Live Photo 配对 |
| nested album + global db | 嵌套相册与全局 SQLite 索引 |
| recently deleted chain | 最近删除还原链 |
| UI reload / refresh | UI 刷新与重载 |

---

## 5. 测试验收标准

| 测试类别 | 状态 |
|---------|------|
| 架构边界测试（14 项） | ✅ 全部通过 |
| 全量回归测试（1683 项） | ✅ 全部通过（6 skipped） |
| CI architecture checks | ✅ 已接入 |
| pre-commit hooks | ✅ 已配置 |

---

## 6. Definition of Done

- [x] 第五阶段治理成果已接入工程流程（CI + pre-commit）
- [x] 兼容层生命周期计划明确
- [x] 架构检查统一入口建立（`tools/check_architecture.py`）
- [x] 新增架构回归测试（manager + library_update_service）
- [x] README / CONTRIBUTING 增加架构规则说明
- [x] 全量测试通过

---

## 7. 后续维护建议

1. 每次新增功能时，先运行 `python tools/check_architecture.py` 确认无边界违规
2. 新代码只能进入 `application/use_cases/` 或 `domain/` 层，不得在 GUI/manager 层实现业务规则
3. 如需新增 compatibility layer 条目，必须同步更新 `docs/refactor/iPhotron_compatibility_lifecycle_plan.md`
4. 定期审查 `app.py` 调用方，推进向 application 层入口迁移

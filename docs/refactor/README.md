# Refactor vNext Workspace

> 当前有效的大架构重构工作区。旧的阶段性 refactor 文档已经归档到
> `docs/finished/referactor/`，这里只保留下一轮架构重写的目标、需求、
> 规划和执行检查清单。

## 文档定位

- `docs/architecture.md` 是项目顶层架构入口，描述 iPhotron 应该收敛到的长期目标。
- `docs/refactor/*` 是本轮重构实施规格，描述如何把当前项目推进到目标架构。
- `docs/finished/referactor/*` 是历史参考，不再作为当前重构的执行依据。

本轮重构的核心不是换目录名，而是：

- 收口业务入口，避免 `app.py`、`gui.facade.py`、`library.manager.py` 等兼容层继续承载新业务。
- 隔离可重建扫描事实与不可丢失的用户状态。
- 统一后台任务、扫描、仓储、缩略图、People、Maps、Edit 等能力的应用层边界。
- 让 GUI 只做 presentation 和 Qt 适配，业务规则进入 use case / application service。

## 阅读顺序

1. `01-target-architecture-vnext.md`：目标架构和关键数据流。
2. `02-detailed-requirements.md`：功能、非功能、边界和验收需求。
3. `03-development-roadmap.md`：阶段规划和迁移策略。
4. `04-implementation-checklist.md`：逐阶段执行清单、完成条件和回归测试。
5. `05-current-progress.md` 及 `06` 之后的编号文档：当前状态和过程性交接记录。

## 当前判断

当前架构方向正确，但还不是最终最优目标。项目已经有 Clean Architecture、
MVVM、RuntimeContext、global SQLite index、People、Maps、GPU editing 等基础，
但仍存在双仓储、双扫描路径、compat shim、直接 singleton 访问和跨层导入。

因此，本轮重构应以“行为收口”和“状态边界清晰”为第一优先级。旧文档中类似
“所有文件小于 300 行”“EventBus 使用率 100%”这类机械指标只作为历史参考；
新的验收标准以边界清晰、行为一致、关键路径测试覆盖和性能不回退为准。

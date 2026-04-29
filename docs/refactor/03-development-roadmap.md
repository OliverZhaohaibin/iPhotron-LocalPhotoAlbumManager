# 03 - Development Roadmap

> 本路线图用于实施 vNext 架构。每个阶段都应保持可运行、可回滚、
> 可测试，不要求一次性完成全量重写。

## Phase 0 - 文档与架构 Guardrail

目标：让团队先在同一张地图上工作。

主要工作：

- 将旧 refactor 文档归档，只保留当前 vNext 文档。
- 更新 `docs/architecture.md` 与 `docs/refactor/*` 的引用关系。
- 扩展 `tools/check_architecture.py`，把当前已知违规方向变成可执行检查。
- 在 CI 或本地质量门禁中加入架构检查。

必须新增的 guardrail：

- `application/` 不得导入 `gui/`。
- `application/` 不得导入 concrete `cache/` 或 `infrastructure/`。
- `infrastructure/`, `cache/`, `core/`, `io/`, `library/`, `people/` 不得导入 `gui/`。
- 新 runtime 代码不得导入 `iPhoto.models.*`，兼容 shim 和兼容测试除外。

完成条件：

- `docs/refactor/` 只有 vNext 文档。
- `python3 tools/check_architecture.py` 通过。
- 已知违规项被记录为 migration exceptions 或转化为失败检查并在对应阶段修复。

## Phase 1 - RuntimeContext / LibrarySession 收口

目标：建立唯一 library 会话入口。

主要工作：

- 引入 `LibrarySession`，由 `RuntimeContext` 创建和持有。
- 将 `LibraryAssetRuntime` 挂入或迁移到 `LibrarySession`。
- 让 GUI main/coordinators/viewmodels 获取 session surface，而不是散落获取 facade、library manager、asset runtime。
- 明确 `app.py`、`appctx.py`、`gui.facade.py` 的 compatibility role。
- 禁止给兼容层新增业务方法。

迁移路径：

1. 新增 `LibrarySession` skeleton，先包裹现有 repository、thumbnail service、library root。
2. 在 `RuntimeContext.open_library()` 或当前 startup binding 中创建 session。
3. 逐步把 GUI 服务的 library-root rebinding 改为 session rebinding。
4. 保留旧属性转发，避免一次性改爆 GUI。

完成条件：

- 新代码可以通过 `RuntimeContext.library_session` 获取 library-scoped surface。
- startup、open library、shutdown 生命周期集中。
- `appctx.py` 不再参与新依赖装配。

## Phase 2 - Asset Repository 与持久状态拆分

目标：消除双仓储和 scan facts/user choices 混写风险。

主要工作：

- 定义 `AssetRepositoryPort`。
- 定义 `LibraryStateRepositoryPort`。
- 明确 `cache/index_store.AssetRepository` 与 `SQLiteAssetRepository` 的目标取舍。
- 将 GUI 查询、分页、favorite、hidden、trash、face status、Live Photo role 等能力收敛到同一 port surface。
- 建立 scan tables 与 user-state tables 的逻辑边界。

迁移路径：

1. 以当前最完整、最接近生产行为的 repository 作为 source of truth。
2. 给目标 port 写 fake adapter 测试。
3. 将 `LibraryAssetRuntime.repository` 改为暴露目标 port。
4. 将 use cases 从 `domain.repositories.IAssetRepository` 迁移到 `application/ports`。
5. 移除或降级重复 repository API。

完成条件：

- asset 查询、scan merge、favorite、trash、face status 走同一 public port。
- scan merge 不会隐式清空用户状态。
- repository contract 有 integration tests 覆盖真实 SQLite。

## Phase 3 - 扫描管线统一

目标：只有一个 scan use case，所有入口共享行为。

主要工作：

- 定义 `ScanLibraryUseCase`。
- 定义 `MediaScannerPort`、`MetadataReaderPort`、`PeopleIndexPort`。
- 将 `app.rescan()`、`ScannerWorker`、`LibraryUpdateService.rescan_album()`、CLI scan 迁移为 use case adapter。
- 将 deletion/prune 从普通 scan 中剥离到 lifecycle use case。
- 扫描进度通过 application progress event 或 task result 输出。

迁移路径：

1. 保留 `io.scanner_adapter` 作为临时 scanner adapter。
2. 先让 `ScannerWorker` 调用 `ScanLibraryUseCase`，保持 Qt signals 不变。
3. 再让 `app.rescan()` 转发到同一 use case。
4. 最后让 CLI 和 watcher 复用同一入口。
5. 移除重复扫描逻辑。

完成条件：

- 全项目只有一个扫描编排实现。
- Qt worker 只负责线程、取消、signal。
- CLI/GUI 扫描行为一致。
- 扫描后 Live Photo pairing 和 People enqueue 行为一致。

## Phase 4 - GUI Facade / Services 降级为 Presentation Adapter

目标：GUI 不再拥有业务编排。

主要工作：

- 将 `gui.facade.AppFacade` 瘦身为 presentation signal facade。
- 将 `gui/services/*` 中的业务流程迁移到 use case/application service。
- Coordinators 负责 view/viewmodel lifecycle，不直接写 persistence。
- ViewModels 通过 session commands/queries 调用 application。
- Background task manager 只管理 Qt task transport。

迁移路径：

1. 给现有 GUI facade 方法逐个标注目标 use case。
2. 对导入、移动、删除、恢复、刷新、配对等高风险流程先建 application command。
3. GUI facade 转发到 command，并保留原 signal。
4. 删除 GUI service 中重复业务规则。

完成条件：

- `gui.facade.py` 不再直接调用 `iPhoto.app` 业务函数。
- GUI service 不直接调用 `get_global_repository()`。
- move/delete/restore/import 均有 application tests。

## Phase 5 - People / Maps / Thumbnail / Edit 端口化

目标：把复杂能力域挂到 application boundary 上，而不是被 GUI 或 concrete runtime 直接穿透。

People：

- 提供 `PeopleIndexPort`。
- UI mutation 通过 People application service。
- stable state 不被 scan commit 清空。

Maps：

- 提供 `MapRuntimePort`。
- map availability、search、asset aggregation 通过 session/query。
- native runtime fallback 可测试。

Thumbnail：

- 提供 `ThumbnailRendererPort`。
- 移除 infrastructure 到 GUI helper 的导入。
- pure geometry/adjustment 进入 `core/`。

Edit：

- 提供 `EditSidecarPort`。
- 保存、读取、重置、导出走应用层。
- GUI edit widgets 不直接绕过 sidecar port 修改业务状态。

完成条件：

- 这些 bounded context 对外都有 application-level port。
- concrete runtime 可以替换或 fake。
- GUI 不再是这些能力的业务入口。

## Phase 6 - 测试、性能基准、CI 架构门禁

目标：让重构结果可长期守住。

主要工作：

- 扩展 architecture tests。
- 给关键 use case 增加 fake-port unit tests。
- 给 SQLite repository 增加 integration tests。
- 给 scan/import/move/delete/restore 增加 end-to-end temp library tests。
- 建立扫描、分页、缩略图、People enqueue 的性能基准。
- 将 `tools/check_architecture.py`、pytest architecture tests、关键 use case tests 加入 CI。

完成条件：

- CI 能阻止新跨层依赖。
- 关键路径性能不低于重构前。
- 大 library 分页和扫描不会阻塞 UI。
- 用户状态保护有回归测试。

## Rollout Strategy

- 每个阶段保持 app 可启动。
- 先新增目标 surface，再逐步迁移调用方。
- 兼容层只桥接，不删到行为稳定后再清理。
- 删除旧路径前必须有 tests 覆盖目标路径。
- 每次迁移优先选择一个完整用户流程收口，而不是只移动文件。


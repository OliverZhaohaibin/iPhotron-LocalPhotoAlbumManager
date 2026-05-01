# 04 - Implementation Checklist

> 执行清单用于跟踪 vNext 重构。勾选前必须满足对应完成条件和回归测试。

## 全局规则

- [ ] 不新增业务逻辑到 `src/iPhoto/app.py`。
- [ ] 不新增业务逻辑到 `src/iPhoto/appctx.py`。
- [ ] 不新增业务逻辑到 `src/iPhoto/gui/facade.py`。
- [ ] 不新增业务逻辑到 `src/iPhoto/library/manager.py`。
- [ ] 新业务优先进入 use case、application service 或 infrastructure adapter。
- [ ] 新跨层能力必须先定义 application port。
- [ ] 不绕过 use case 直接从 GUI 写 persistence。
- [ ] 每个阶段结束运行 `python3 tools/check_architecture.py`。

## Phase 0 - 文档与 Guardrail

主要文件：

- `docs/refactor/*`
- `docs/architecture.md`
- `tools/check_architecture.py`
- `tests/architecture/*`

任务：

- [x] 旧 refactor 文档归档到 `docs/finished/referactor/`。
- [x] 新建 vNext refactor 文档集。
- [x] 标注旧 planning/phases 文档为历史参考。
- [x] 扩展架构检查：application 禁止 GUI import。
- [x] 扩展架构检查：application 禁止 concrete cache/infrastructure import。
- [x] 扩展架构检查：infrastructure/cache/core/io/library/people 禁止 GUI import。
- [x] 扩展架构检查：禁止新增 runtime `iPhoto.models.*` import。
- [x] 扩展架构检查：旧 domain-repository use case 只允许兼容入口导入。
- [x] 扩展架构检查：GUI runtime 禁止导入 `iPhoto.app`。
- [x] 将架构检查加入 CI 或 documented verification。

完成条件：

- [x] `find docs/refactor -maxdepth 2 -type f | sort` 只显示 vNext 文档。
- [x] `python3 tools/check_architecture.py` 通过。
- [x] 已知例外有明确 owner 和后续阶段。

回归测试：

- [x] `python3 tools/check_architecture.py`
- [x] `pytest tests/architecture -q`，如果 architecture tests 已存在。

## Phase 1 - RuntimeContext / LibrarySession

主要文件：

- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/bootstrap/container.py`
- `src/iPhoto/infrastructure/services/library_asset_runtime.py`
- `src/iPhoto/application/contracts/*`
- `src/iPhoto/gui/main.py`

任务：

- [x] 新增 `LibrarySession`。
- [x] `RuntimeContext` 持有单个 active `LibrarySession`。
- [x] library open/bind/shutdown 生命周期进入 session。
- [x] repository、thumbnail、people runtime、album metadata runtime 挂到 session；Maps runtime 仍待独立 session surface。
- [ ] GUI startup 使用 session surface。
- [x] `appctx.py` 标注并限制为 compatibility proxy。
- [x] 增加 runtime entry tests。

完成条件：

- [x] 启动时可以延迟创建或恢复 library session。
- [x] rebind library root 会重建 library-scoped adapters。
- [x] shutdown 会关闭连接池、thumbnail worker、background runtime。
- [x] 旧 GUI 路径仍能启动。

回归测试：

- [x] `pytest tests/application/test_appctx_runtime_context.py -q`
- [ ] GUI startup smoke test，如已有。
- [ ] 手动打开一个已有 library，确认资产能加载。

## Phase 2 - Repository 与用户状态拆分

主要文件：

- `src/iPhoto/application/ports/*`
- `src/iPhoto/cache/index_store/*`
- `src/iPhoto/infrastructure/repositories/*`
- `src/iPhoto/infrastructure/db/pool.py`
- `src/iPhoto/people/*`

任务：

- [x] 定义 `AssetRepositoryPort`。
- [x] 定义 `LibraryStateRepositoryPort`。
- [x] 明确现有两个 asset repository 的保留/合并策略：`cache/index_store.AssetRepository` / `global_index.db` 是运行时 source of truth，`SQLiteAssetRepository` 暂保留为 legacy/domain 测试适配器。
- [x] scan merge API 保留用户状态。
- [x] active GUI favorite 写入走 state boundary。
- [ ] hidden/trash/pinned/order 等其他用户状态继续收敛到 state boundary。
- [x] repository 支持 transaction boundary。
- [x] 写 integration tests 验证 scan rebuild 不丢用户状态。
- [x] 旧 domain-repository use case graph 标注为 compatibility-only，并由架构检查限制新导入。

完成条件：

- [x] GUI pagination/query 走目标 repository port。
- [x] Scan merge 走目标 repository port。
- [x] Move/delete/restore 状态迁移走目标 state port。
- [ ] 不再新增 `get_global_repository()` 调用点。

回归测试：

- [x] repository SQLite integration tests。
- [x] favorite 在 rescan 后保持。
- [x] trash/restore 在 rescan 后保持。
- [x] Live Photo role 在 pairing 后可查询。

## Phase 3 - 扫描管线统一

主要文件：

- `src/iPhoto/application/use_cases/scan_album.py`
- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/gui/services/library_update_service.py`
- `src/iPhoto/app.py`
- `src/iPhoto/cli.py`

任务：

- [x] 定义或重命名为 `ScanLibraryUseCase`。
- [x] 定义 `MediaScannerPort`。
- [x] 定义 progress/cancel contract。
- [x] `ScannerWorker` 改为调用 scan use case。
- [x] `app.rescan()` 改为 compatibility forwarder。
- [x] CLI scan 改为调用同一 use case。
- [x] `app.open_album()`、import 增量扫描、restore rescan 进入 session scan surface。
- [x] GUI `open/rescan/pair` 路由收口到 session scan/update surface。
- [x] Watcher/live-row 刷新读取改为调用 session query surface。
- [x] Watcher-triggered refresh 改为通过 session scan surface 触发扫描。
- [x] 删除普通 scan 中的隐式 delete/prune 决策。

完成条件：

- [x] 全项目只有一个 scan orchestration。
- [x] GUI、CLI、watcher 扫描结果一致。
- [x] scan cancellation 不留下半写坏状态。
- [x] scan progress 可被 GUI 和 CLI 消费。
- [x] stale-row prune 通过 lifecycle reconciliation 显式执行。

回归测试：

- [x] 新增文件被扫描并显示。
- [x] 修改文件只重读必要 metadata。
- [x] 删除文件不隐式清空用户状态。
- [x] 扫描后 People 候选状态正确。
- [x] 扫描后 Live Photo pairing 可恢复。
- [x] `LibraryScanService.finalize_scan()` 不再隐式删除 stale rows。

## Phase 4 - GUI Presentation Adapter

主要文件：

- `src/iPhoto/gui/facade.py`
- `src/iPhoto/gui/services/*`
- `src/iPhoto/gui/coordinators/*`
- `src/iPhoto/gui/viewmodels/*`
- `src/iPhoto/gui/background_task_manager.py`

任务：

- [x] 为 facade 方法建立目标 command/use case mapping。
- [x] 导入流程迁移到 application use case。
- [x] 移动流程迁移到 application use case。
- [x] 删除流程迁移到 application use case。
- [x] 恢复流程迁移到 application use case。
- [x] 配对/刷新流程迁移到 application use case。
- [x] album open / rescan / media-load-failure 路由迁移到 session update / lifecycle surface。
- [x] Gallery collection/windowed reads 迁移到 session query surface。
- [x] Move/delete/restore planning 迁移到 session asset operation surface。
- [x] album cover / featured / import-mark-featured durable 规则迁移到 session album metadata surface。
- [ ] GUI services 只保留 presentation coordination。
- [ ] Background task manager 只保留 Qt transport。

完成条件：

- [x] `gui.facade.py` 不直接调用 `iPhoto.app` 业务函数。
- [x] GUI 不直接调用 concrete repository singleton。
- [ ] ViewModels 通过 session commands/queries 访问业务。
- [ ] Coordinators 不拥有 persistence 规则。

回归测试：

- [x] 打开 library。
- [x] 打开 album。
- [x] 导入资产。
- [x] 移动资产。
- [x] 删除到 trash。
- [x] Restore 资产。
- [x] Favorite/hidden 状态刷新正确。
- [x] album cover / featured manifest 同步与 favorite state mirror 正确。

## Phase 5 - Bounded Context Ports

### People

- [x] 定义 `PeopleIndexPort`。
- [x] 定义 People asset row / `face_status` port。
- [x] People scan enqueue 通过 port。
- [x] People stable mutation 通过 application service。
- [x] 防止 scan commit 清空 stable state。
- [x] group asset cache 刷新有测试。

### Maps

- [x] 定义 `MapRuntimePort`。
- [ ] 地图可用性查询通过 session。
- [x] 地理资产聚合通过 application query。
- [ ] native runtime fallback 有测试。

### Thumbnail

- [x] 定义 `ThumbnailRendererPort`。
- [x] 移除 infrastructure 对 `gui.ui.tasks.geo_utils` 的导入。
- [x] geometry/adjustment helper 移到 `core/`。
- [x] thumbnail cache hit/miss 有测试。

### Edit

- [x] 定义 `EditSidecarPort`。
- [ ] `.ipo` 读写通过 port。
- [ ] edit save/reset/export 通过 use case。
- [ ] GUI edit widget 不直接拥有 durable business state。

完成条件：

- [ ] 每个 bounded context 都有 application-level boundary。
- [ ] GUI 可以用 fake port 做 viewmodel/coordinator 测试。
- [ ] runtime adapter 可替换。

回归测试：

- [x] People scan 后名字、隐藏、分组保持。
- [ ] Map 页面在 extension 缺失时 graceful fallback。
- [x] Thumbnail 生成不阻塞 UI。
- [ ] Edit sidecar 保存后重启仍可恢复。

## Phase 6 - 测试、性能、CI

主要文件：

- `tests/application/*`
- `tests/infrastructure/*`
- `tests/architecture/*`
- `tests/performance/*`
- `.github/workflows/*`，如果项目启用 GitHub Actions。

任务：

- [x] application use case fake-port tests。
- [x] SQLite repository integration tests。
- [ ] temp library end-to-end tests。
- [x] architecture guard tests。
- [ ] scan performance baseline。
- [ ] gallery pagination baseline。
- [ ] thumbnail cache baseline。
- [x] CI 加入 architecture checks。
- [x] CI 加入关键 use case tests。

完成条件：

- [x] 架构违规会在 CI 失败。
- [x] 用户状态保护有回归测试。
- [ ] 扫描、分页、缩略图性能不回退。
- [ ] 关键产品流程有 end-to-end tests。

回归测试：

- [x] `python3 tools/check_architecture.py`
- [x] `pytest tests/application -q`
- [ ] `pytest tests/infrastructure -q`
- [x] `pytest tests/architecture -q`
- [ ] 性能测试按项目约定运行。

## Definition of Done

- [ ] 代码边界符合 `01-target-architecture-vnext.md`。
- [ ] 行为需求符合 `02-detailed-requirements.md`。
- [ ] 阶段任务符合 `03-development-roadmap.md`。
- [ ] 本清单对应阶段全部完成。
- [ ] 没有新增兼容层业务债务。
- [ ] 没有丢失用户状态的迁移风险。
- [ ] 文档、测试和架构检查同步更新。
## Round 19 Update: GUI Update + Location/Trash

Phase 4 status adjustments:

- [x] `LibraryUpdateService` no longer imports `iPhoto.app`, `cache.index_store`, or `library.workers.*`; worker ownership moved behind a GUI task runner and durable scan finalization moved to runtime/library surfaces.
- [x] GUI scan update flows now use a runtime scan finalize hook for snapshot persistence, Recently Deleted preserved fields, stale-row reconciliation, and Live Photo pairing follow-up.
- [x] Recently Deleted preparation and cleanup throttling no longer live in `NavigationCoordinator`; they now flow through a dedicated Location/Trash GUI transport adapter that calls existing library/runtime surfaces.
- [x] Location geotagged-asset loading no longer reads directly from `GalleryViewModel`; background transport and request-token handling now flow through the dedicated Location/Trash adapter.
- [ ] People fallback still has residual coordinator/viewmodel touchpoints and remains the next GUI residual slice.

Phase 5 status adjustments:

- Maps runtime porting is still not complete in this round.
- This slice only cleaned the GUI-side Location entry point so later Maps runtime extraction has a narrower boundary to target.
- Do not mark `MapRuntimePort` complete from this round alone.

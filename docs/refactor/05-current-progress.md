# 05 - 当前进度

> Last updated: 2026-05-01

## 摘要

本文件记录当前 vNext 重构的真实落点。项目还没有完成全部六个阶段，
但运行时主路径已经建立起可执行的 session boundary、repository/state
boundary 和 architecture guardrail。

本轮新增完成的是 `18-album-metadata-session-migration.md`：album cover、
featured/favorite mirror 和 import 后 `mark_featured` 的 durable 规则，
已经从 GUI service 下沉到 session-owned album metadata service；
`gui/services/album_metadata_service.py` 现在只保留 Qt/watcher/presentation
adapter 职责。

## 本轮完成：Album Metadata Session 化

- 新增 `AlbumRepositoryPort` 与 `AlbumManifestRepository`。
  - album manifest 的 load/save/exists 现在通过 application/infrastructure
    boundary 表达，不再让 GUI service 直接持有 manifest persistence 规则。
- 新增 `LibraryAlbumMetadataService`。
  - 该 session-owned service 负责 album cover、featured toggle、多 album
    manifest mirror、favorite state mirror，以及 import 后的
    `mark_featured` 落盘。
- `LibrarySession` / `RuntimeContext` / `LibraryManager` 新增
  `album_metadata` surface 绑定。
  - active library 打开后，GUI adapter 可以优先走 bound session service；
    无 active session 时，仍保留 compatibility fallback 构造。
- `gui/services/album_metadata_service.py` 已瘦身为 presentation adapter。
  - GUI service 现在只负责 watcher pause/resume、错误转发、当前 album
    内存态同步和必要的 view refresh。
- 架构检查新增 guardrail。
  - `gui/services/album_metadata_service.py` 不得再 runtime import
    `iPhoto.models.album`、`library_session`、`jsonio` 等旧实现细节。

## 历史已完成切片摘要

以下切片已经完成，详细过程性交接分别见 `06` 到 `16`：

- 基础边界与 session 基础：
  已引入 `application/ports/*`、`LibrarySession`、state repository adapter、
  `RuntimeContext` session binding、Assign Location adapter、thumbnail/core
  边界清理，以及可执行 architecture checks。
- 扫描与生命周期主线：
  已引入 `LibraryScanService`、`ScanLibraryUseCase`、session-owned scan entry、
  move/delete/restore lifecycle service、Recently Deleted cleanup、watcher scan
  refresh、显式 stale-row reconciliation。
- 查询与 source-of-truth：
  已引入 `LibraryAssetQueryService`，GUI gallery/grid/export/map/album dashboard
  读取改走 session query surface；`global_index.db` 明确为当前 runtime
  asset source of truth。
- People session 化：
  已引入 `PeopleIndexPort` 与 library-scoped People service，GUI 关键
  People 流程改为优先走 session-bound service。
- Album metadata session 化：
  已引入 `LibraryAlbumMetadataService` 与 album manifest repository port，
  album cover / featured / import-mark-featured durable 规则已从 GUI service
  下沉到 session-owned command surface。
- GUI 文件操作 command 化：
  move/delete/restore 的 durable planning 已迁入
  `LibraryAssetOperationService`，GUI service 只负责 prompt、worker、
  signal 和用户提示。

## 当前阶段状态

- Phase 0：部分完成。
  vNext 文档与 guardrail 已落地，当前 guardrail 已覆盖 application/concrete
  imports、lower-layer GUI imports、GUI concrete index-store imports、legacy
  model shim imports、legacy domain use-case imports、GUI runtime `iPhoto.app`
  imports 等回归方向；仍保留少量明确 allowlist。
- Phase 1：部分完成。
  `LibrarySession` 已建立并接入 `RuntimeContext`，scan/query/state/
  album-metadata/lifecycle/operation/People surface 已挂到 active session；
  GUI startup 仍有进一步收口空间。
- Phase 2：部分完成。
  `global_index.db` 已成为 runtime asset source of truth，favorite 写入和
  gallery 查询已走 state/query boundary；更多 durable user state 仍待继续
  收口。
- Phase 3：已完成。
  GUI、CLI、watcher、`app.py` compatibility wrapper、restore/import follow-up、
  以及本轮的 GUI `open/rescan/pair` 入口都已收口到
  `LibraryScanService` / `ScanLibraryUseCase` 主线；普通 scan 中的 delete/prune
  也已分离为显式 lifecycle reconciliation。
- Phase 4：部分完成。
  `AppFacade` 的 open/rescan/pair routing、move/delete/restore planning、
  gallery query reads、media-load-failure 修复、album metadata durable
  mutation 都已迁到 session service；但 `gui/services/*` 和
  `BackgroundTaskManager` 仍未完全瘦身成纯 presentation transport。
- Phase 5：部分完成。
  People、Thumbnail、Assign Location 边界已有明显收口；Maps runtime
  availability/fallback 与 Edit sidecar port 仍待深入迁移。
- Phase 6：部分完成。
  architecture tests、targeted application/infrastructure tests 已存在；
  temp-library end-to-end 与性能 baseline 仍未完成。

## 已知迁移例外

- `app.py`、`appctx.py`、`gui.facade.py`、`library.manager.py` 仍是兼容入口，
  但不应继续承载新的 durable business logic。
- `application/services/album_service.py` 及旧
  `application/use_cases/*` 仍保留给 compatibility/test 路径，不应再被新的
  runtime 代码导入。
- `io/scanner_adapter.py` 仍是扫描迁移过程中的 allowlisted bridge，
  当前继续复用 legacy `FileDiscoveryThread`。
- `SQLiteAssetRepository` 仍保留给 legacy/domain 测试适配器；当前 library
  scoped runtime repository 已不再依赖它。
- user state 目前仍物理存放在 `global_index.db`；`LibraryStateRepositoryPort`
  提供的是 API boundary，而不是独立 `library_state.db`。
- `global_index.db` 兼容 schema 可能缺失 `metadata` 列；state adapter 保持
  best-effort 行为。
- Maps 与 Edit sidecar 仍缺少完整的 session/runtime boundary 收口。

## 最新验证

本轮在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/services/test_album_metadata_service.py tests/application/test_library_album_metadata_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述测试全部通过。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

之前各个切片的针对性验证命令和结果，继续以 `06` 到 `16` 交接文档为准；
本文件只保留当前整体验证结论和最新增量验证。

## 下一步交接

1. 继续瘦身 `gui/services/*` 与 `BackgroundTaskManager`，优先收口仍在
   coordinator/viewmodel 里的 location/trash-cleanup/People fallback 之类
   非 Qt durable orchestration。
2. 补 `temp library` 端到端回归：import / move / delete / restore，以及
   rescan 后用户状态保护。
3. 推进 Phase 5 的 Maps / Edit：地图可用性查询、native fallback、`.ipo`
   sidecar 读写与 save/reset/export use case。

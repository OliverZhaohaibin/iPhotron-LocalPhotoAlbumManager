# 22 - Edit Sidecar Session Migration

> **版本:** 1.0 | **日期:** 2026-05-02  
> **状态:** 已完成  
> **范围:** Phase 5 Edit sidecar session/runtime boundary

---

## 1. 背景与目标

承接 `21-maps-runtime-availability-session-migration.md` 的下一步交接。本轮目标不是
只把 edit save 挪一层，而是把 `.ipo` sidecar 的运行时读写、视频预览判定、
导出判定、剪贴板渲染、缩略图渲染依赖统一收口到 active session/runtime boundary。

迁移前的主要问题是：

- GUI/runtime 多处直接 import `iPhoto.io.sidecar`，各自拼
  sidecar 读取、resolved adjustments、trim、adjusted preview 与 visible edits
  判断。
- `EditCoordinator`、`PlaybackCoordinator.rotate_current_asset()`、
  `ShareController`、`PreviewController`、`DetailViewModel`、thumbnail 路径
  分别拥有一套 durable edit 读写或渲染判定逻辑。
- `LibrarySession` / `RuntimeContext` / `LibraryManager` 尚未把 edit
  surface 作为正式 active session surface 暴露出去。

目标是：

- 新增 session-owned edit surface，作为 GUI/runtime 唯一 durable edit 入口。
- `.ipo` XML 格式保持不变，只迁运行时边界，不做 schema 变更。
- reset 继续维持内存态 `EditSession.reset()`，不新增“立即清空 sidecar”的持久化命令。
- 为 GUI/runtime 新增 architecture guardrail，阻止新的
  `iPhoto.io.sidecar` 业务入口泄漏。

## 2. 变更摘要

### 2.1 Session-bound edit surface

- `application/ports/media.py`
  - 保留 `EditSidecarPort` 作为底层持久化协议。
  - 新增 `EditRenderingState` 与 `EditServicePort`。
- 新增 `infrastructure/repositories/edit_sidecar_repository.py`
  - `FileSystemEditSidecarRepository` 继续复用现有 `.ipo` XML 读写实现。
- 新增 `bootstrap/library_edit_service.py`
  - `LibraryEditService` 统一负责：
    sidecar existence、读写 adjustments、默认 adjustments 快照、
    resolved render adjustments、视频 trim、effective duration、
    adjusted preview 与 visible edits 判定。

### 2.2 Active session 绑定链路

- `LibrarySession`
  - 新增 `edit` surface，默认绑定 `LibraryEditService`。
  - `asset_runtime` 初始化和 shutdown 时同步 bind/unbind edit service。
- `RuntimeContext` / `LibraryManager`
  - 新增 `bind_edit_service()` / `edit_service`，与 scan/query/people/maps
    一样走 active session surface。

### 2.3 写路径收口

- `MediaAdjustmentCommitter`
  - 改为依赖 injected `EditServicePort`；自身只保留 watcher pause/resume、
    thumbnail invalidation 和 `adjustmentsCommitted` signal。
- `EditCoordinator`
  - 进入编辑时通过 edit service 加载 persisted adjustments。
  - 完成编辑时通过 committer 或 edit service surface 提交；不再直连
    `iPhoto.io.sidecar`。
- `PlaybackCoordinator.rotate_current_asset()`
  - 改为通过 active `edit_service` 读取当前 persisted adjustments，再合并旋转结果提交。

### 2.4 读路径与渲染判定收口

- `DetailViewModel`、`GalleryListModelAdapter`、`PreviewController`、
  `PlayerViewController`、`ShareController`、`ExportController`
  不再自己拼 video trim / adjusted preview / visible edits 判断。
- `core/export.py`
  - 继续作为导出与“复制已编辑图片/视频到剪贴板”的共用渲染入口。
  - 调用方现在统一传入 session-bound `edit_service`。
- `ThumbnailCacheService`
  - 新增 `set_edit_service()`，缩略图渲染走 injected edit surface。

### 2.5 Guardrail

- `tools/check_layer_boundaries.py`
  - 新增 GUI/runtime 业务入口禁止导入 `iPhoto.io.sidecar` 的检查。
  - 同时覆盖 `from iPhoto.io import sidecar` 这类 alias import 形态。
- 明确保留两个 path-level 例外：
  - `move_worker` 继续只为了伴随物一起移动使用 sidecar path helper。
  - `thumbnail_job` 继续只为了 cache stamp 读取 sidecar mtime。

## 3. 行为说明

- `.ipo` sidecar 内容和 XML schema 没有变化；旧文件无需迁移。
- reset 仍只重置当前编辑会话内存态；不会因为点 reset 立即删除或覆盖 sidecar。
- 视频预览、trim 范围、effective duration、是否需要 adjusted preview，
  现在都从 `EditRenderingState` 读取，不再由各个 controller/viewmodel 各自判断。
- 导出与剪贴板渲染继续共用 `core/export.py`，避免 share worker 再保留一套第三方
  sidecar 渲染逻辑。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_library_edit_service.py tests/application/test_runtime_context.py tests/gui/coordinators/test_edit_coordinator.py tests/gui/coordinators/test_playback_coordinator.py tests/gui/viewmodels/test_detail_viewmodel.py tests/ui/test_media_adjustment_committer.py tests/ui/controllers/test_preview_controller.py tests/ui/controllers/test_share_controller_rendering.py tests/core/test_export.py tests/test_thumbnail_loader.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused edit/runtime regressions 通过。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. Phase 5 的 Edit 已完成，后续优先回到 Maps residual，而不是继续扩张 Edit 功能面。
2. 若继续推进 Maps，应优先处理 widget 构造、event routing 与
   `LocationTrashNavigationService` 这个临时 seam。
3. 继续补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。

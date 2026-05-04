# 26 - Temp Library End-to-End Regression

> **版本:** 1.0 | **日期:** 2026-05-02
> **状态:** 已完成
> **范围:** Phase 6 temp-library session + worker end-to-end regression

---

## 1. 背景与目标

承接 `25-maps-gui-transport-overlay-residual-migration.md` 的下一步交接。本轮不再
继续扩新的 session/runtime boundary，而是回到 `05-current-progress.md` 已明确
排在最前的 Phase 6 缺口：temp-library 端到端回归。

迁移前的问题是：

- `import / move / delete / restore / rescan` 已分别有 session/service/worker
  级测试，但缺少一组在同一临时库里串起真实文件系统行为的回归。
- `favorite` 与 trash metadata 的状态保护虽然在较低层有断言，但还没有在
  `LibrarySession + workers` 这一条更接近产品主路径的层级被确认。
- 当前测试环境关闭第三方 pytest 插件自动加载，部分既有 worker/service
  focused regressions 依赖 `mocker` fixture，导致验证命令本身不稳定。

目标是：

- 新增 temp-library E2E，覆盖 `import / move / delete / restore / rescan`。
- 明确采用 `LibrarySession + ImportWorker / MoveWorker` 作为回归入口。
- 先锁定当前已稳定的 `favorite + trash` 用户状态保护。
- 让 focused regressions 在当前测试环境里可稳定执行。

## 2. 变更摘要

### 2.1 Temp-library E2E harness

- 新增 `tests/application/test_temp_library_end_to_end.py`
  - 使用真实 `tmp_path` 临时库目录与真实文件移动/复制。
  - 使用轻量 fake scanner 与 fake `process_media_paths` seam，避免依赖
    exiftool 或真实媒体解析。
  - `LibrarySession` 在测试中显式替换不相关的 Maps runtime capability probe，
    避免无头环境里的 OpenGL 探测影响回归稳定性。

### 2.2 Session + worker 主链路覆盖

- `ImportWorker`
  - 通过 session-bound `scan_service` / `asset_lifecycle_service` 写回
    library-root `global_index.db`。
- `MoveWorker`
  - 通过 session-bound `asset_operations` planning +
    `asset_lifecycle` apply path 验证 album move、delete-to-trash、
    restore-from-trash。
- restore 回归明确走 `plan_restore_request(...)` 批次规划，而不是绕回旧 GUI
  facade/service 入口。

### 2.3 用户状态保护

- `favorite`
  - 在 temp-library 下经过完整 `rescan_album()` 后仍保持。
- trash metadata
  - delete 后确认 `original_rel_path` / `original_album_id` /
    `original_album_subpath` 写入 trash row。
  - restore 后确认这些字段从目标 row 清除。

### 2.4 Focused test environment 补齐

- 更新 `tests/conftest.py`
  - 新增最小 `mocker` fixture。
  - 覆盖仓库当前实际依赖的 `Mock` / `MagicMock` / `patch` 能力。
  - 目的不是替代完整 `pytest-mock`，而是在当前禁用第三方插件自动加载的
    环境里保持 worker/service focused regressions 可运行。

## 3. 行为说明

- 本轮没有新增生产 public API，也没有改变 `LibrarySession` / `RuntimeContext`
  的业务边界。
- `global_index.db` 继续作为当前 runtime asset source of truth。
- temp-library E2E 目前只锁 `favorite + trash`，不把 `hidden / pinned / order`
  等更宽的 durable user state 一并塞进本轮范围。
- 本轮新增的 `mocker` fixture 只影响测试环境，不改变运行时行为。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py tests/ui/tasks/test_import_worker.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused regressions 通过（`47 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 优先继续补 Phase 2 residual：`hidden / pinned / order` 等 durable user state
   仍未全部纳入 temp-library E2E。
2. 补 Phase 6 性能 baseline：scan、gallery pagination、thumbnail cache。
3. 若后续再回到 Maps，只处理新暴露问题；当前不再主动扩新的
   session/runtime boundary。

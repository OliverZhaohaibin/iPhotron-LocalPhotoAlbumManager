# 28 - Performance Baseline

> **版本:** 1.0 | **日期:** 2026-05-03
> **状态:** 已完成
> **范围:** Phase 6 performance baseline：scan / gallery pagination / thumbnail cache

---

## 1. 背景与目标

承接 `27-durable-user-state-residual-boundary.md` 的下一步交接。本轮只处理
Phase 6 性能 baseline，不扩 Maps、People、Edit 或新的 GUI boundary。

迁移前的问题是：

- `tools/benchmarks/benchmark_refactor.py` 是历史 benchmark 入口，已无法作为当前
  session/repository 边界的可信验证。
- `tools/testbase/` 是本地真实素材集，不能进入 Git，也不能成为 CI 前置条件。
- Phase 6 清单里的 scan、gallery pagination、thumbnail cache baseline 仍未落地。

目标是：

- 清理旧 benchmark 入口，避免后续继续基于过时脚本扩展。
- 新增可在 CI 和本地稳定运行的小数据性能 baseline。
- 保持真实素材测试为手动可选验证，不影响普通测试。

## 2. 变更摘要

- 删除旧 benchmark 文件。
  - 移除 `tools/benchmarks/benchmark_refactor.py`。
  - `tools/benchmarks/` 清空后不再作为当前 Phase 6 入口。
- 将 `tools/testbase/` 加入 `.gitignore`。
  - 该目录仅作为本地真实素材集。
  - CI 和普通 pytest 不依赖它存在。
- 新增 `tests/performance/test_refactor_performance_baseline.py`。
  - scan baseline：通过 `LibraryScanService`、synthetic scanner 和
    `global_index.db` merge path 覆盖 scan orchestration + repository merge。
  - gallery pagination baseline：构造合成 index rows，验证
    `get_assets_page()` 的 cursor pagination 行为和小数据耗时上限。
  - thumbnail cache baseline：预填 L2 disk cache，验证 L2 回填 L1 后大量 L1 hits
    不触发 generator，且命中路径耗时受控。
- 新增 opt-in `tools/testbase` 压力测试。
  - `tests/performance/test_testbase_stress_workflows.py` 默认 skip。
  - 设置 `IPHOTO_RUN_STRESS=1` 后才会读取本地真实素材集。
  - 测试会把样本 hardlink/copy 到 `tmp_path` 后执行 scan、move、delete/restore
    与 unedited export，不会修改原始 `tools/testbase/`。

## 3. 行为说明

- 本轮 baseline 是 regression sanity check，不是跨机器绝对性能认证。
- 阈值有意保持宽松，重点捕获明显退化，例如分页退回全量读取、cache hit 触发
  generator、scan merge 路径异常变慢。
- 真实 `tools/testbase/` 可用于本地手动观察，但当前没有提交新的真实素材 benchmark
  脚本入口；当前只提供 opt-in pytest 压力测试。如果后续需要 CLI，应新增一个
  当前架构下可运行的脚本，而不是恢复旧 `benchmark_refactor.py`。

## 4. 验证

本轮计划验证：

- `.venv/bin/python -m pytest tests/performance -q`
- `IPHOTO_RUN_STRESS=1 IPHOTO_STRESS_TESTBASE=/Users/haibinzhao/Documents/python-code/iPhotron-LocalPhotoAlbumManager/tools/testbase .venv/bin/python -m pytest tests/performance/test_testbase_stress_workflows.py -q -s`
- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_scan_service.py tests/infrastructure/test_index_store_asset_repository_adapter.py tests/cache/test_sqlite_store.py -q`

## 5. 下一步交接

1. 若需要更严格的真实素材性能追踪，新增一个新的 benchmark CLI，并明确输出
   JSON/CSV 结果；不要恢复旧 `tools/benchmarks/benchmark_refactor.py`。
2. Phase 4 仍可继续收口剩余 GUI services / BackgroundTaskManager presentation
   transport，但不属于本轮 Phase 6 baseline 范围。

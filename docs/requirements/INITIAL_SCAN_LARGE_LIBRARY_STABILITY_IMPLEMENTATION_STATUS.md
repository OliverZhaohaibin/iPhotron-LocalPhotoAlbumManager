# 超大相册初始扫描稳定性改造实施记录

> **日期:** 2026-05-06  
> **对应需求:** `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`  
> **状态:** 已完成第一阶段和第二阶段主体改造，第三阶段 People 深化与第四阶段压测尚未完成

---

## 1. 本次改了什么

### 1.1 扫描生命周期模型统一

新增扫描域模型，统一 GUI、runtime、service 之间的扫描协议：

- `ScanMode`
  - `atomic`
  - `background`
  - `initial_safe`
- `ScanPlan`
- `ScanCompletion`
- `ScanProgressPhase`
- `ScanStatusUpdate`

涉及位置：

- `src/iPhoto/application/use_cases/scan_models.py`
- `src/iPhoto/application/use_cases/scan_library.py`
- `src/iPhoto/application/use_cases/__init__.py`

效果：

- GUI 背景扫描不再依赖 `finished(rows)` 传递完整结果。
- 扫描完成时改为传递 `scan_id + mode + processed_count + failed_count + phase`。
- CLI 和同步路径仍可保留 `atomic` 语义。

### 1.2 GUI 初始扫描改为 Safe Mode

首次绑定 Basic Library、以及启动恢复时如果不存在 `global_index.db`，现在会显式进入 `INITIAL_SAFE`。

涉及位置：

- `src/iPhoto/gui/ui/controllers/dialog_controller.py`
- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/gui/facade.py`
- `src/iPhoto/gui/services/library_update_service.py`

效果：

- 初次绑定大库时，默认走“分块发现 + 分块落库 + 状态上报”的安全路径。
- Safe Mode 下不触发库级 eager reload。
- Safe Mode 下不自动并发启动 People face scan。

### 1.3 ScannerWorker 不再在完成时持有整包 rows

`ScannerWorker` 现在以 `ScanCompletion` 作为 `finished` 载荷，而不是完整 `rows` 列表。

涉及位置：

- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/gui/services/library_update_tasks.py`
- `src/iPhoto/library/scan_coordinator.py`

效果：

- 避免 GUI worker 在大库扫描完成瞬间保留完整 Python dict 列表。
- 完成后的后处理由 service/repository 按 `scan_id` 从数据库驱动完成。

### 1.4 扫描索引引入 scan run 状态

`global_index.db` 新增了扫描会话持久化能力：

- `scan_runs` 表
- `assets.last_seen_scan_id` 字段

涉及位置：

- `src/iPhoto/cache/index_store/migrations.py`
- `src/iPhoto/cache/index_store/repository.py`
- `src/iPhoto/cache/index_store/row_mapper.py`
- `src/iPhoto/application/ports/repositories.py`

效果：

- chunk merge 时会记录本次 `scan_id`。
- successful full scan 的 prune 改为按 `last_seen_scan_id != current_scan_id` 删除当前 scope 的旧行。
- cancelled / failed scan 不会误删未重新发现的旧索引。
- 后续 resume / paused scan 有了可持久化的基础状态。

### 1.5 旧的整库 incremental preload 已从主路径移出

扫描器现在支持按批次调用 repository `get_rows_by_rels()` 做缓存复用，而不是先把整库 index preload 成一个巨大 dict。

涉及位置：

- `src/iPhoto/application/ports/media.py`
- `src/iPhoto/infrastructure/services/filesystem_media_scanner.py`
- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/bootstrap/library_scan_service.py`

效果：

- 重扫和恢复扫描时，主路径不再默认把整个 scope 的旧索引一次性搬进 Python 内存。
- 这一步直接降低了大库初扫和大 scope 重扫的内存峰值。

### 1.6 finalize 改为 repository 驱动

`LibraryScanService` 现在有了更清晰的扫描生命周期：

- `plan_scan()`
- `start_scan()`
- `resume_scan()`
- `complete_scan()`

效果：

- GUI 背景扫描完成后，不再通过 `finalize_scan_result(rows)` 传递整包扫描结果。
- prune 改为数据库驱动。
- completion phase 可以明确表达：
  - `completed`
  - `deferred_pairing`
  - `cancelled_resumable`
  - `paused_for_memory`
  - `failed`

### 1.7 Live Photo 配对去整库峰值

库级 `pair_album()` 已改为按目录前缀分区读取和处理，而不是直接 `list(read_all())`。

涉及位置：

- `src/iPhoto/bootstrap/library_scan_service.py`
- `src/iPhoto/cache/index_store/repository.py`

效果：

- 降低库级 Live Photo pairing 的一次性内存占用。
- 为后续进一步做 lazy `links.json` materialization 打下基础。

### 1.8 扫描状态与内存降级接入 GUI

状态栏现在可以接收更高层级的扫描状态，而不只是一串 `(current, total)` 进度数字。

涉及位置：

- `src/iPhoto/gui/ui/controllers/status_bar_controller.py`
- `src/iPhoto/gui/coordinators/main_coordinator.py`
- `src/iPhoto/gui/services/library_update_service.py`
- `src/iPhoto/gui/facade.py`
- `src/iPhoto/library/runtime_controller.py`

当前已接入的状态包括：

- `discovering`
- `indexing`
- `deferred_pairing`
- `paused_for_memory`
- `cancelled_resumable`

此外，`ScannerWorker` 已接入 `MemoryMonitor`：

- warning 时：关闭后续 micro thumbnail 生成
- critical 时：当前批完成后进入可恢复暂停

### 1.9 Safe Mode 下解耦 People 并发

`ScanCoordinatorMixin` 会根据 `ScanPlan.allow_face_scan` 决定是否启动 `FaceScanWorker`。

效果：

- `initial_safe` 扫描不再和 People face scan 抢同一波峰值资源。
- People 的完全增量化还没做完，但最危险的“初扫并发叠加”已被切开。

### 1.10 非首次大 scope 扫描也接入了压力分级

本轮继续把保护范围从“首次空库扫描”扩展到了“非首次的大 scope 扫描”。

涉及位置：

- `src/iPhoto/application/use_cases/scan_models.py`
- `src/iPhoto/bootstrap/library_scan_service.py`
- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/library/scan_coordinator.py`
- `src/iPhoto/gui/services/library_update_service.py`
- `src/iPhoto/gui/ui/controllers/status_bar_controller.py`
- `src/iPhoto/gui/ui/tasks/import_worker.py`

效果：

- 扫描模型新增了 `ScanPressureLevel` 与 `ScanScopeKind`，扫描状态不再只区分 mode/phase，也能表达是否处于 constrained / critical。
- `scan_runs` 现在会持久化 `pressure_level`、`degrade_reason` 和 `deferred_tasks`，恢复扫描不会悄悄回到未受限状态。
- 非首次 `background` 扫描如果在计划阶段就识别出大 scope，会直接以内置 constrained 策略启动，但对 UI 仍表现为普通重扫入口。
- `ScannerWorker` 在发现数量放大或内存 warning 时会切换到 constrained：关闭新的 micro thumbnail、停止继续进行 People、延后 Live Photo pairing。
- constrained 的 `background` 扫描完成后同样允许进入 `deferred_pairing`，避免把峰值重新压到扫描尾部。
- 导入兜底全量重扫和 restore refresh 已改走 bounded session path，不再强依赖 `scan_album(... persist_chunks=False)` 物化整包 `rows`。
- constrained / deferred pairing 完成时，GUI 不再无条件触发整页 reload，而是继续优先依赖 chunk 驱动和可见窗口刷新。

---

## 2. 本次没有完成的部分

以下内容仍然属于“需求已识别，但尚未完全交付”：

### 2.1 People 管线仍未彻底增量化

当前只完成了：

- 初始 Safe Mode 不启动 `FaceScanWorker`

尚未完成：

- `FaceScanWorker` 按 checkpoint 提交 People snapshot
- `PeopleIndexCoordinator` 避免每个小批次读取全量 faces / persons
- `FaceClusterPipeline` 避免无界 `N x N` 距离矩阵

### 2.2 `links.json` 仍不是完全 lazy materialization

当前已做到：

- Safe Mode 可以延后主扫描完成时的 pairing
- 库级 pairing 改为按目录分区

尚未做到：

- 仅对当前打开 scope materialize `links.json`
- 对 deferred pairing 建立后台补齐任务和明确“配对处理中”状态

### 2.3 大库预估与用户确认还没做

当前 Safe Mode 的触发条件仍然是：

- `global_index.db` 不存在

尚未完成：

- 20k / 50k / 100k 预估分档
- 首次绑定前快速计数
- 用户可见的安全扫描确认或更细粒度选项

### 2.4 内存阈值只接入了主扫描 worker

当前已做到：

- 主扫描 worker warning/critical 降级
- 非首次大 scope 背景扫描的 constrained / paused 状态持久化

尚未做到：

- People worker 的阈值控制
- thumbnail service 的统一阈值策略
- 更细的峰值 RSS 日志和阶段统计

### 2.5 压测与性能基线尚未建立

目前已经补了行为回归测试，但还没有落地：

- 20k / 50k / 100k synthetic library 压测
- RSS 峰值断言
- 可恢复暂停/恢复的大库压力回归

---

## 3. 代码层面的行为变化

### 已经改变的默认行为

1. GUI 初次绑定空库时，不再直接以普通 background scan 语义处理，而是走 `initial_safe`。
2. `ScannerWorker.finished` 的载荷不再是 `(root, rows)`，而是 `ScanCompletion`。
3. 背景扫描完成后的 prune 不再依赖 Python 全量 `fresh rel set`。
4. Safe Mode 不会自动并发启动 People face scan。
5. 主扫描高内存 warning 后，会停止后续 micro thumbnail 生成。
6. 主扫描高内存 critical 后，会以“可恢复暂停”结束当前扫描，而不是继续盲跑。
7. 非首次大 scope `background` 扫描在发现量或内存压力升高时，也会自动进入 constrained，并延后 People / Live Photo pairing。
8. constrained 或 deferred pairing 完成的非首次扫描，不会再默认触发一次性整页 reload。

### 仍然保留的兼容行为

1. CLI / 同步扫描仍可保留 `atomic` 的 collect rows 语义。
2. 现有 `scanFinished(root, bool)` 信号对大部分 GUI 监听方仍保持兼容。
3. `pair_album()` 仍可显式重建当前 scope 的 `links.json`。

---

## 4. 测试与验证

本轮已补充或更新的测试覆盖点包括：

- background scan 不保留完整 `rows`
- `ScannerWorker.finished` 改为 `ScanCompletion`
- 初始绑定 / 启动恢复走 `INITIAL_SAFE`
- `ScanPlan` 在 safe mode 下会关闭 face scan 和 eager pairing
- `complete_scan()` 使用 `last_seen_scan_id` 做 prune
- `LibraryUpdateService` 使用新的 completion hook

已跑通的一组针对性回归：

```bash
.venv/bin/python -m pytest \
  tests/application/test_scan_library_use_case.py \
  tests/application/test_runtime_context.py \
  tests/application/test_dialog_controller_runtime_binding.py \
  tests/application/test_library_scan_service.py \
  tests/application/test_app_rescan_atomicity.py \
  tests/application/test_cli_session_scan.py \
  tests/services/test_library_update_service_global_db.py \
  tests/library/test_scanner_worker.py \
  tests/library/test_rescan_worker_session.py \
  tests/test_app_facade_session_open.py \
  tests/test_facade_cancel_scans.py \
  tests/test_pairing_live.py \
  tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py \
  -q
```

结果：

- `71 passed`
- 现有 `PytestConfigWarning: Unknown config option: env` 仍然存在，但不是本次改造引入

---

## 5. 下一步建议

建议按下面顺序继续推进。

### 下一步 1：把 People 管线从“解耦并发”推进到“真正去峰值”

优先做：

- `FaceScanWorker` 改为较大 checkpoint 批次提交，而不是每小批次实时重建
- `PeopleIndexCoordinator.submit_detected_batch()` 避免每次 `get_all_faces()` / `get_all_person_records()`
- 为 clustering 引入分片或增量策略，先去掉最危险的无界 `N x N`

这是下一阶段最重要的稳定性缺口。

### 下一步 2：把 deferred pairing 做成明确的后台补齐流程

建议做：

- Safe Mode 完成后记录 `deferred_pairing` task
- 当前 scope 打开时优先补当前目录
- 状态栏或 UI 增加“Live Photo 配对处理中”的弱提示

这样可以把“主扫描先稳定完成”和“配对最终一致性”彻底分开。

### 下一步 3：补大库预估和用户可见策略

建议做：

- 绑定前快速只计数候选媒体
- 分档阈值先用 `20k / 50k / 100k`
- 对超大库显示将采用 Safe Mode、People 延后、配对延后

这一步主要提升可解释性和用户预期管理。

### 下一步 4：把内存监控接到 People 和更广泛的缩略图路径

建议做：

- People worker warning 时暂停入队或降批量
- People worker critical 时停止新批次并保留 retry/pending 状态
- 统一记录 `scan_id + phase + RSS + processed_count + last_processed_rel`

### 下一步 5：建立压力测试和性能基线

建议新增：

- synthetic 20k / 50k / 100k library scan tests
- RSS 峰值采样脚本或 stress suite
- cancelled / paused scan 的恢复回归
- 分区 pairing 的性能与正确性回归

---

## 6. 当前结论

这一轮改造已经完成了“止血”和“主链路去整包 rows / 去整库 preload / repository 驱动 prune”的主体工作，也把 Safe Mode、状态上报、People 并发解耦、内存降级的主框架搭起来了。

离最终需求还差两块最重要的工作：

1. People 管线彻底去峰值
2. 大库压测与基线固化

如果后续继续沿这份文档推进，建议优先完成 People checkpoint / 增量聚类，再补 deferred pairing 背景任务和 50k+ 压测。

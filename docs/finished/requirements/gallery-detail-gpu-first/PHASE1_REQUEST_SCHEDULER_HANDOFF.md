# Phase 1 Handoff：Detail 请求调度与去重

> **状态：已完成的历史 handoff。** 当前实现以
> [`docs/architecture.md`](../../../architecture.md) 和生产测试为准。

> 更新日期：2026-07-18
> Phase 1 状态：代码与定向自动化测试完成，随本 handoff 一并提交
> 当前分支：`codex/gallery-detail-gpu-first-phase1`
> 工作树状态：本 handoff 所在提交包含全部 Phase 1 文件；提交前无已知测试失败

本文只记录 Phase 1 的真实落地状态。整体架构、最终性能目标和 Phase 2–5 约束以
[`GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`](GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md)
为唯一契约。

## 1. 已实现内容

- 删除 `GalleryGridView.mousePressEvent()` 的无条件 Detail 全图预取；favorite badge 点击路径保留。
- hover dwell 从 120ms 调整为 150ms；离开 Gallery、切换多选、model reset/rows removed 和正式
  mousePress 都会取消尚未发出的 hover 请求。
- `detailPrefetchRequested` 从 `QModelIndex` 改为完整 `DetailPrefetchDescriptor`，不再在 runtime
  lambda 中根据 row 二次解析身份。
- 新增 `DetailStillRequestScheduler`，以 `asset_id + absolute source path` 作为 Phase 1 key。
- queued hover 请求被点击时复用同一 runnable 并以交互优先级重新提交；running hover 请求被点击时
  将 foreground generation 附着到同一 worker，不创建第二个 decoder。
- 新资产请求会移除尚未运行的旧任务；已进入原生 decoder 的旧任务继续完成但失去 foreground
  generation。两线程 lane 保留，因此新请求可以绕过一个不可中断的旧 decoder。
- controller 不再为每个 still worker 临时连接自己的 completion lambda；scheduler 统一接收
  worker started/completed/failed/finished，并统一发布 ready/failed/finished。
- 保留现有 full-resolution `_AdjustedImageWorker` 和 `DetailFrameCache`；worker 现在只能通过
  controller 提供给 scheduler 的 factory 创建。
- 新增默认开启的 `IPHOTO_DETAIL_SCHEDULER_V3`。设置为 `0/false/no/off` 时可诊断性恢复同 key
  重建任务的旧行为，不作为长期双实现。
- `emit_detail_event()` 改为只生成时间戳和隐私清洗后的 payload，再放入有界内存队列；后台
  `iPhoto-detail-profile-writer` 批量写 JSONL。profiling 关闭时不创建线程或文件，应用 shutdown
  执行有界 flush。

## 2. 新旧调用链对比

旧链路：

```text
Gallery entered 120ms / mousePress
  -> QModelIndex signal
  -> runtime row lambda
  -> PlaybackCoordinator resolves descriptor
  -> PlayerViewController creates low-priority full worker

itemClicked
  -> Detail presentation
  -> controller cancels old worker and creates a second foreground worker
  -> per-worker temporary callbacks
  -> GUI presentation
```

Phase 1 链路：

```text
Gallery entered 150ms
  -> resolve DetailPrefetchDescriptor once
  -> PlaybackCoordinator.prefetch_descriptor
  -> PlayerViewController.prefetch_image
  -> DetailStillRequestScheduler.prefetch(key)

itemClicked
  -> Detail presentation(asset_id, generation)
  -> PlayerViewController.display_image
  -> DetailStillRequestScheduler.request(key, generation)
       -> promote queued same-key worker
       -> or reuse running same-key worker
       -> or remove queued stale work / use free second lane
  -> scheduler.ready only for current foreground generation
  -> final GPU viewer presentation
```

mousePress 本身不再进入 decode 链路。

## 3. Scheduler API、状态与线程所有权

主要 API：

```python
DetailStillRequestScheduler.prefetch(*, asset_id: str, source: Path) -> bool
DetailStillRequestScheduler.request(
    *, asset_id: str, source: Path, generation: int
) -> bool
DetailStillRequestScheduler.cancel_foreground() -> None
DetailStillRequestScheduler.shutdown(*, timeout_ms: int = 1500) -> None
```

统一 signal：

- `ready(generation, source, image, adjustments, frame_identity)`
- `failed(generation, source, message)`
- `finished(detail_decode_key)`

Phase 1 内部状态只有 `queued` 和 `running`：

```text
prefetch -> queued(priority=-1)
queued + same-key click -> tryTake -> queued(priority=1), same worker
queued crossed into decoder -> running
running + same-key click -> running + foreground generation, same worker
queued + different-key click -> removed/cancelled
running + different-key click -> stale delivery; new key enters second lane
completed/failed -> publish only if generation is current -> finished/release
```

线程所有权：

- GUI 线程拥有 scheduler、generation、Qt signal 聚合和轻量 UI 状态。
- decode pool 仍为两个高优先级 worker lane；worker 负责文件/stat、full decode、sidecar、ColorStats 和
  frame cache（这些重工作将在后续阶段拆分）。
- GPU/render 线程仍由现有 QRhi viewer 完成纹理上传和 shader draw。
- profiler writer 是唯一允许 open/append JSONL 的线程；调用 `emit_detail_event()` 的 GUI/worker
  线程不执行文件 open/write。

## 4. 实际修改文件

生产代码：

- `src/iPhoto/gui/detail_request_scheduler.py`（新增）
- `src/iPhoto/gui/detail_pipeline.py`
- `src/iPhoto/gui/detail_profile.py`
- `src/iPhoto/gui/ui/controllers/player_view_controller.py`
- `src/iPhoto/gui/ui/widgets/gallery_grid_view.py`
- `src/iPhoto/gui/coordinators/playback_coordinator.py`
- `src/iPhoto/gui/coordinators/desktop_coordinator_runtime.py`

测试：

- `tests/gui/test_detail_request_scheduler.py`（新增）
- `tests/gui/test_detail_profile.py`（新增）
- `tests/gui/coordinators/test_playback_coordinator.py`
- `tests/ui/test_gallery_grid_view.py`

文档：

- `docs/requirements/gallery-detail-gpu-first/GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`（新增）
- `docs/requirements/gallery-detail-gpu-first/PHASE1_REQUEST_SCHEDULER_HANDOFF.md`（本文件）

## 5. 自动化验证实录

用户固定的 Phase 1 定向命令：

```bash
.venv/bin/python -m pytest -q \
  tests/gui/coordinators/test_playback_coordinator.py \
  tests/gui/viewmodels/test_detail_viewmodel.py \
  tests/ui/controllers/test_player_view_init_cover.py \
  tests/ui/controllers/test_player_view_controller_adjustments.py \
  tests/gui/test_detail_pipeline.py \
  tests/gui/test_detail_request_scheduler.py \
  tests/gui/test_detail_profile.py
```

结果：`116 passed in 0.77s`；失败 0，跳过 0。

Gallery mousePress/hover/cancel 专项：

```bash
.venv/bin/python -m pytest -q tests/ui/test_gallery_grid_view.py
```

结果：`7 passed in 1.21s`；失败 0，跳过 0。

最终 `compileall` 和 `git diff --check` 的结果记录在本次交付消息中；如交接后继续修改，接手者必须
重新执行全部固定命令，不能沿用本记录。

## 6. Profiler 事件与隐私确认

Phase 1 新增/固定事件为 `scheduled`、`promoted`、`reused`、`worker_started`、
`worker_finished`、`stale` 和 `presented`；原有 route/decode/cancel 事件继续保留。

JSONL 结构示例（时间值仅示意）：

```json
{"stage":"scheduled","monotonic_ms":123456.789,"wall_time":1784304000.0,"generation":4,"details":{"asset_id":"asset-1","suffix":".jpg"}}
```

隐私行为已由自动化测试确认：顶层及嵌套的 `path`、`absolute_path`、`source` 字段会被丢弃；
`Path` 或绝对路径字符串值最多保留 basename；测试生成的 JSONL 不包含测试目录绝对路径。

## 7. 正确性证据与性能证据状态

自动化已证明：

- queued/running 同 key 请求均只创建一个 worker。
- 同资产重复点击更新 foreground generation，不产生并行 decoder。
- A running 时 B 可以在 A 完成前进入另一 lane。
- A 迟到结果不会发布 ready，只有当前 B generation 呈现。
- profiler JSONL 的 open/append 不在调用线程执行；关闭状态不创建 writer/file。
- 正常 shutdown 会取消并释放 queued worker、等待 pool 并 flush writer。

尚未采集 packaged、真实 GPU backend、每组 >=30 次的 click-to-present P95。因此本阶段**没有**证明
普通照片 <=150ms、重型照片 <=300ms 或热重访 <=80ms。源码结构、offscreen 单测耗时和 worker
创建计数都不得替代正式性能证据。

## 8. 明确未完成项

- 首屏仍是 full-resolution decode，耗时仍随传感器像素数增长。
- `DetailFrameCache` identity 仍把 sidecar revision 与中性像素绑定。
- GPU 仍上传整张纹理，并沿用现有 mipmap 行为。
- Detail/Edit 仍会重复 decode，尚未共享 `PhotoRenderSessionHandle`。
- 平台硬解、RAW embedded preview、中性磁盘/mapped cache 均未实现。

## 9. Phase 2 唯一接手方向

保持 `DetailStillRequestScheduler` 的去重、promotion、generation、stale 和统一发布契约不变，将当前
full-resolution worker 替换为 viewport-aware：

```text
StillDecodeBackend + DecodedSurface
```

Phase 2 允许扩展 `DetailDecodeKey` 为 source revision + decode level，并让 request 携带 viewport
physical size、DPR、crop/rotation；不得把 source/edit-state 缓存、GPU texture residency 或
Detail/Edit session 一并提前塞入 scheduler。后续工作必须从这个边界继续。

# Gallery 滚动性能 Phase 2 Micro 优先加载交接文档

> 状态：开发完成，待 Windows/Linux 实机视觉与性能验收  
> 日期：2026-06-11  
> 上游交接：`GALLERY_SCROLL_PERFORMANCE_PHASE1_HANDOFF.md`

## 1. 本阶段结论

本阶段针对高速大跨度滚动期间长时间显示纯色 placeholder 的问题，实现了
visible-first / warm-second 两阶段窗口加载，同时保持 Gallery 滚动、绘制和
`model.data()` 路径零 I/O：

- 可见范围以 `16ms` single-shot timer 合并；连续滚动期间 timer 不再被反复重启，
  因此无需等待滚动停止 `100ms` 才提交新 viewport。
- adapter 在同一帧内只保留最新 viewport，不再把不连续的大跨度位置合并成错误的
  `min(first)..max(last)` 请求。
- visible 请求只查询当前可见区及 view 已包含的 20 行缓冲区，不再等待至少 300 行
  warm window 全部查询和 micro 解码完成。
- visible micro 发布后，稳定 `100ms` timer 才提交 warm window 与完整缩略图请求。
- 单 worker 队列中，最新 visible 请求优先于尚未开始的 warm 请求；过期 generation
  结果继续丢弃。
- 性能事件新增 `tier` 与 `request_to_publish_ms`，可区分 visible/warm 请求和发布延迟。

对具备有效 micro thumbnail 的资产，最终 viewport 会优先显示 micro；只有 micro
缺失、损坏或 backfill 尚未完成时才继续使用纯色 placeholder。

## 2. API 与调度行为

### `AssetGrid`

- visible-range timer 从 `100ms` 改为 `16ms`。
- timer 已激活时不重复启动，实现每帧最多一次的范围计算与通知。
- 相同 visible range 仍不会重复发射。

### Gallery window

- `GalleryWindowRequest` 新增：
  - `tier`: `visible` 或 `warm`
  - `requested_at_ms`: 用于计算请求到发布延迟
- `GalleryCollectionStore.prioritize_rows()` 负责最新 visible micro 请求。
- `GalleryCollectionStore.prefetch_rows()` 仅在 visible 已发布且范围稳定后请求 warm
  window；已覆盖的 warm window 不重复查询。
- visible 请求使用精确范围；warm 请求继续使用既有有界窗口和 hysteresis 策略。
- 新 visible viewport 会递增 generation，使旧 visible/warm 结果失效。

### Adapter 与完整缩略图

- `GalleryListModelAdapter` 的 16ms pending range 只保留最后一次 viewport。
- 稳定 `100ms` timer 同时请求 warm window 和当前可见行完整缩略图。
- visible 结果到达后会重新启动稳定 timer，避免慢 visible 查询与 warm 请求竞争。
- warm 结果不会再次启动稳定 timer。

## 3. 本机性能采集

执行环境：

- macOS 26.5.1 arm64
- Qt backend：`offscreen`
- DPI ratio：`1.0`
- runtime label：`development-offscreen`
- 每场景 120 个 wheel event，batch size 8

执行命令：

```bash
QT_QPA_PLATFORM=offscreen \
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
IPHOTO_RUNTIME_LABEL=development-offscreen \
.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

结果：

| Rows | scroll P95 | paint P95 | frame interval P95 | 输入追平 | 最终 micro 发布 | micro 覆盖 | placeholder |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10k | 0.002ms | 1.836ms | 26.841ms | 0.073ms | 25.154ms | 100% | 0% |
| 100k | 0.001ms | 1.399ms | 50.379ms | 0.063ms | 48.677ms | 100% | 0% |
| 1M | 0.002ms | 1.366ms | 28.800ms | 0.062ms | 27.157ms | 100% | 0% |

三个场景均确认：

- visible micro 在 warm 阶段前发布；
- 最终 viewport micro/full 覆盖率为 `100%`；
- 最终 viewport placeholder 覆盖率为 `0%`；
- 禁止调用计数为 `0`。

offscreen 的 frame interval P95 仍高于真实平台最低门槛 `24ms`，因此本阶段不能视为
已经通过 Windows/Linux 平台验收。报告位于：

```text
/tmp/iphoto-gallery-scroll-performance
```

## 4. 验证记录

已通过：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/gui/viewmodels/test_gallery_window_loader.py \
  tests/gui/viewmodels/test_gallery_collection_store.py \
  tests/gui/viewmodels/test_gallery_list_model_adapter.py \
  tests/test_asset_grid_scroll.py \
  tests/ui/test_gallery_grid_view.py -q
# 81 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/gui/viewmodels/test_gallery_viewmodel.py \
  tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py \
  tests/gui/coordinators/test_playback_coordinator.py \
  tests/ui/controllers/test_selection_controller.py \
  tests/ui/controllers/test_context_menu_cover.py \
  tests/ui/controllers/test_context_menu_operations.py \
  tests/ui/controllers/test_context_menu_export.py \
  tests/ui/controllers/test_preview_controller.py \
  tests/ui/widgets/test_filmstrip_view.py \
  tests/ui/widgets/test_filmstrip_performance.py \
  tests/performance/test_refactor_performance_baseline.py -q
# 153 passed, 2 skipped

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/cache/test_index_store_features.py \
  tests/application/test_library_asset_query_service.py \
  tests/test_thumbnail_cache_service.py -q
# 59 passed

QT_QPA_PLATFORM=offscreen IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
  IPHOTO_RUNTIME_LABEL=development-offscreen \
  .venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
# 3 passed

python3 -m compileall -q src/iPhoto tests/performance/test_gallery_scroll_qt_benchmark.py
git diff --check
# passed
```

已知 pytest 警告仍为：

```text
PytestConfigWarning: Unknown config option: env
```

## 5. 下一步交接

### 优先验收

1. 在 Windows packaged runtime、Linux XCB 和 Linux Wayland 上执行真实 benchmark。
2. 使用真实大型图库快速拖动 scrollbar 和高速滚轮，确认大跨度过程中 micro 会持续
   出现，而不是只在停止后出现。
3. 分别记录 visible `request_to_publish_ms`、placeholder 覆盖率和 frame interval；
   若真实平台 frame interval P95 超过 `24ms`，优先 profile window-ready
   `dataChanged` 与 micro 绘制成本。

### 后续 Phase 2/3 开发

1. 将当前 Gallery 投影后的 `AssetDTO` 收敛为轻量 `GalleryTileDTO`，减少 metadata
   materialization 和 worker 到 GUI 的对象体积。
2. 将 visible/warm window-ready 更新按帧合并，并只刷新准确 row 与 role。
3. 为完整缩略图请求增加 viewport generation、visible/hot/warm 优先级和 stale result
   丢弃；当前 generation 只覆盖 window 请求。
4. 将逐 tile thumbnail-ready 合并为每帧局部更新。
5. 将缩略图内存缓存迁移到真实字节预算 LRU，并短期 pin visible pixmap。

本阶段未执行完整默认 `pytest -q`，也未完成 Windows/Linux 实机验收。

最终组合 GUI 回归曾在既有 `icons.py::load_icon` Qt 原生调用中发生一次 segmentation
fault；随后隔离重跑 `tests/ui/test_gallery_grid_view.py` 为 `4 passed`，其余本阶段
核心测试隔离重跑为 `77 passed`。该原生测试环境不稳定问题仍需后续处理。

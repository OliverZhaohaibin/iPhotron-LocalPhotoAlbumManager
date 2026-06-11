# Gallery 滚动性能 Phase 0+1 交接文档

> 状态：开发完成，待 Windows/Linux 实机验收  
> 日期：2026-06-11  
> 上游方案：`docs/requirements/GALLERY_SCROLL_PERFORMANCE_REARCHITECTURE.md`

## 1. 本阶段结论

本阶段完成了真实 Qt 事件链采集器，并移除了 Gallery 的
`paint/model.data/scrollContentsBy` 直接同步阻塞：

- Linux 滚动和 resize 路径不再调用同步 layout 或 `repaint()`，改为零间隔
  single-shot timer 合并 `viewport().update()`。
- 删除 Gallery viewport 上下额外行的手动 delegate 绘制。
- `GalleryListModelAdapter.data()` miss 立即返回稳定 placeholder，不再调用
  `ensure_row_loaded()`。
- Gallery `DecorationRole` 只调用内存 `peek()`，不再同步访问或解码 L2。
- 可见范围稳定后通过 `request_many()` 异步请求完整缩略图，避免 micro thumbnail
  永久停留。
- L2 检查、L2 `QImage` 解码、源图渲染和缩略图落盘均在 worker 中执行；GUI
  结果处理只完成 `QImage -> QPixmap`、内存发布和 ready signal。

该阶段保护的是滚动与绘制直接调用栈，尚未达到整个 Gallery GUI 线程严格零 I/O。

## 2. API 与行为变更

### `ThumbnailCacheService`

- 新增 `peek(path, size)`：仅查询内存 cache，不访问磁盘、不解码、不提交任务。
- 新增 `request_many(paths, size, priority=...)`：去重后提交异步 L2 加载或生成。
- worker 新增 L2-first 加载流程；L2 miss 时生成并在 worker 内落盘。
- 保留 `get_thumbnail()` 的旧兼容行为，非 Gallery 调用方暂不迁移。

### Gallery

- `DecorationRole` 使用 `peek()`；miss 时 delegate 继续显示 micro thumbnail 或
  深色稳定 placeholder。
- 可见范围 timer 完成同步窗口刷新后，为当前已加载 row 调用 `request_many()`。
- model reset 会清空 view 的 visible-range 快照，确保 collection 切换后重新调度
  当前可见缩略图。
- 快速滚动期间允许 placeholder；停止后完整缩略图会异步替换 micro thumbnail。

## 3. 性能采集器

新增 opt-in benchmark：

```text
tests/performance/test_gallery_scroll_qt_benchmark.py
```

它使用真实 `QApplication`、`GalleryGridView`、delegate 和 model adapter，驱动：

```text
QWheelEvent -> scrollbar -> scrollContentsBy -> paintEvent -> model.data
```

采集 wheel、scroll、paint、frame interval、输入追平时间和以下禁止调用计数：

- `ensure_row_loaded`
- `get_thumbnail`
- `executeDelayedItemsLayout`
- `repaint`

默认输出目录：

```text
/tmp/iphoto-gallery-scroll-performance
```

可通过 `IPHOTO_GALLERY_SCROLL_REPORT_DIR` 修改；每个数据规模分别输出 JSON 和 CSV。

## 4. 本机采集结果

执行环境：

- macOS 26.5.1 arm64
- Qt backend：`offscreen`
- DPI ratio：`1.0`
- runtime label：`development-offscreen`
- 每个场景：120 个 wheel event，batch size 8

执行命令：

```bash
QT_QPA_PLATFORM=offscreen \
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
IPHOTO_RUNTIME_LABEL=development-offscreen \
.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

结果：

| Rows | scroll P95 | paint P95 | frame interval P95 | 输入追平 | 禁止调用 |
|---:|---:|---:|---:|---:|---:|
| 10k | 0.002ms | 0.952ms | 1.581ms | 0.120ms | 0 |
| 100k | 0.002ms | 0.934ms | 1.699ms | 0.137ms | 0 |
| 1M | 0.002ms | 0.976ms | 1.579ms | 0.130ms | 0 |

三个场景均确认 visible-range timer 会在滚动后提交异步完整缩略图请求。

报告文件：

```text
/tmp/iphoto-gallery-scroll-performance/gallery-scroll-darwin-offscreen-10000.json
/tmp/iphoto-gallery-scroll-performance/gallery-scroll-darwin-offscreen-100000.json
/tmp/iphoto-gallery-scroll-performance/gallery-scroll-darwin-offscreen-1000000.json
```

这些数据仅用于本机事件链诊断，不替代 macOS 可视 no-regression、Windows packaged
runtime 或 Linux XCB/Wayland 验收。

## 5. 待执行平台采集

### Windows development/runtime 诊断

```powershell
$env:QT_QPA_PLATFORM = "windows"
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK = "1"
$env:IPHOTO_RUNTIME_LABEL = "windows-development"
$env:IPHOTO_GALLERY_SCROLL_REPORT_DIR = "artifacts\gallery-scroll-windows"
.\.venv\Scripts\python.exe -m pytest tests\performance\test_gallery_scroll_qt_benchmark.py -q
```

packaged runtime 仍需把同等 collector 接入发布构建后执行；不得仅修改
`IPHOTO_RUNTIME_LABEL` 冒充 packaged 数据。

### Linux XCB

```bash
QT_QPA_PLATFORM=xcb \
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
IPHOTO_RUNTIME_LABEL=linux-xcb-development \
IPHOTO_GALLERY_SCROLL_REPORT_DIR=artifacts/gallery-scroll-linux-xcb \
.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

### Linux Wayland

```bash
QT_QPA_PLATFORM=wayland \
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
IPHOTO_RUNTIME_LABEL=linux-wayland-development \
IPHOTO_GALLERY_SCROLL_REPORT_DIR=artifacts/gallery-scroll-linux-wayland \
.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

各平台最低门槛：

- 输入追平不超过 `100ms`
- `scrollContentsBy()` P95 不超过 `2ms`
- frame interval P95 不超过 `24ms`
- 禁止调用计数为 `0`
- 实机检查 selection、focus、scrollbar、圆角窗口和自定义顶部按钮栏

## 6. 与原 Phase 1 退出条件的差异

已满足：

- `model.data()` miss 不同步加载。
- paint/model direct path 不访问 L2。
- Linux scroll path 不调用同步 layout 或 `repaint()`。
- viewport update 可在同一事件循环周期合并。
- 快速滚动期间 scrollbar 可先移动并显示 placeholder。

未满足：

- `prioritize_rows()` 在可见范围 timer 到期后仍会在 GUI 线程同步查询 window，并在
  DTO 转换时解码 micro thumbnail。
- 初始 selection/window 加载仍为同步路径。
- `Roles.LOCATION`、`Roles.SIZE` 等非绘制 role 尚未完成严格零 I/O/副作用审计。
- `get_thumbnail()` 兼容 API 仍可同步访问 L2，但 Gallery 已不再调用它。
- Windows packaged、Linux XCB、Linux Wayland 尚未执行实机采集。

本阶段为修复完整缩略图永久不加载的问题，提前实现了 Phase 3 的最小子集：
`request_many()`、worker L2 decode 和 worker 落盘。generation、stale 丢弃、按帧
ready 合并和 byte-budget LRU 仍未实现。

## 7. 后续开发接续

### Phase 2：异步 Gallery window

1. 引入轻量 `GalleryTileDTO` 和显式 Gallery SQL 投影。
2. 将 `prioritize_rows()` 的同步 window 查询和 micro thumbnail 解码移入单 worker。
3. 引入 generation、collection revision、合并请求和 stale result 丢弃。
4. GUI 仅发布有界内存 snapshot，并准确合并 `dataChanged`。

### Phase 3：完整异步 thumbnail pipeline

1. 为 `request_many()` 增加 viewport generation、visible/hot/warm 优先级和 stale 取消。
2. worker result 应校验 asset key、尺寸和 generation 后再发布。
3. thumbnail-ready 按帧合并，避免逐 tile `dataChanged`。
4. 将固定 item 数缓存迁移到真实字节预算 LRU，并 pin visible pixmap。
5. 审计并迁移仍调用兼容 `get_thumbnail()` 的 GUI 路径。

## 8. 验证记录

已通过：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/gui/viewmodels/test_gallery_list_model_adapter.py -q
# 23 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_thumbnail_cache_service.py -q
# 10 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/test_asset_grid_scroll.py tests/ui/test_gallery_grid_view.py -q
# 10 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/gui/viewmodels/test_gallery_collection_store.py \
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
# 192 passed, 2 skipped

python3 -m compileall -q src/iPhoto tests/performance/test_gallery_scroll_qt_benchmark.py
git diff --check
# passed
```

完整默认 `pytest -q` 曾运行到约 35% 后，在既有
`edit_perspective_controls -> load_icon` Qt 原生调用中发生 segmentation fault；
不是 assertion failure，且调用栈不涉及本阶段修改文件。该全量套件未完成，后续应在
稳定 Qt GUI 测试环境中重新执行。

已知 pytest 警告：

```text
PytestConfigWarning: Unknown config option: env
```

# Phase 5 Handoff：统一 Render Transaction 与三平台工程关闭

> 更新日期：2026-07-18
> Phase 5 状态：source/offscreen 代码候选已落地；三平台真实 packaged 数据未采集，不满足整体关闭条件
> 基线提交：`da427c6bffd844bab0ee395fefc1475f4e542d97`
> 当前分支：`codex/gallery-detail-gpu-first-phase1`

## 1. 已落地的代码边界

- 新增 immutable `DetailRenderTransaction` 与 GUI-owned `DetailRenderCoordinator`。still、video 共用
  generation/source/media identity，且每个当前事务只能进入一次 `presented`、`failed` 或 `cancelled`。
- Gallery→Detail 的 route、still surface、video first GPU frame 均通过 coordinator 终结；A→B 的旧事务在
  新事务开始时取消，迟到 still/video signal 不再产生最终 `presented`。
- `DetailRenderRequest.from_transaction()` 继承 viewport/DPR；GPU hit/upload、LOD 与 video first frame 使用
  实际 generation。speculative warm 只使用 residency window generation。
- 删除 `DetailFrameCache`、`DetailFrameIdentity`、`IPHOTO_DETAIL_PIPELINE_V2`、
  `IPHOTO_DETAIL_SCHEDULER_V3`、ViewModel 同步 geocode/sidecar fallback 与 `VideoArea.load_video()`。
- `StillDecodeBackendRegistry` 固定 RAW→rawpy、Linux/通用→Qt，并支持 macOS ImageIO、Windows WIC 优先及
  worker 内 Qt fallback；macOS 提供可选 PyObjC ImageIO/CoreGraphics 实现，Windows 提供 COM/WIC
  scaler/flip-rotator/format-converter 实现。两者均输出 detached RGBA8888/sRGB surface。

## 2. Benchmark 与 CI

- packaged 应用在 `IPHOTO_DETAIL_BENCHMARK_PLAN` 存在时启动内部 harness，通过真实
  `GalleryViewModel.open_row()` 驱动，不调用 scheduler/viewer 私有捷径。
- manifest 可自动控制 cold/disk/memory/GPU cache、sidecar-only、Edit Done/Cancel、fullscreen、LOD、
  memory pressure 与 A→B→A；场景写入只发生在临时图库。
- `tools/run_detail_packaged_benchmark.py` 只把 manifest 指定样本及 `.ipo` 复制到临时图库；不会改写
  `tools/testbase` 或用户图库。输出 `events.jsonl`、runtime metadata、summary 和 validation。
- summary 按 manifest category 统计，区分 image/video，记录 stale presented 与 GUI event-loop task P95；
  validate 执行 30 样本、32/24/150/300/80ms 门槛。
- CI 新增 macOS/Windows/Linux 的 Detail transaction、decoder、scheduler、cache、session、residency、benchmark
  定向矩阵；offscreen CI 只作正确性回归，不能作为真实 GPU 性能证据。

## 3. 当前自动化证据

- Phase 5 decoder/transaction/benchmark 核心定向组合：`22 passed in 0.68s`。
- 最终单进程全仓：`2797 passed, 12 skipped, 12 warnings in 40.87s`。此前一次全仓尝试在既有 SVG icon
  fixture 触发 Qt native segfault；对应 `tests/test_aspect_ratio_constraint.py` 随即独立 `29 passed`，随后上述
  单进程全仓完整通过。该原生偶发记录不删除。
- `tools/check_architecture.py`、`compileall -q src tools`、Phase 5 变更集 Ruff `F,E9` 与
  `git diff --check` 均通过。全仓 Ruff `F,E9` 仍有基线既存 286 项（集中在 demo/旧测试），不在本次重构中
  扩散修改或用 ignore 隐藏。
- 已证明旧 flag/cache/API 不再有源码引用；native provider 失败不会吞掉 cancellation，且只回退 Qt decoder。

## 4. 尚未证明及严格阻塞项

- 尚未构建/运行 macOS Metal、Windows OpenGL QRhi、Linux OpenGL QRhi 的 baseline/candidate packaged 矩阵。
- Windows WIC production provider 已实现，但尚未在 Windows packaged 环境加载和做 backend parity；加载或
  codec 失败时 registry 会安全使用 Qt，不能把 source/offscreen 结果写成 WIC 已验证。
- 尚无每格式/缓存组 >=30 次 P50/P95，也无相对 baseline 40%/25% 改善证据。
- MKV、H.265、4K/HDR、context loss、GPU allocation failure、系统内存压力及 Definition 视觉签核仍待真机。

因此总体文档必须保持“Phase 5 进行中/packaged 待验证”。只有本节阻塞全部清零、validation 全部通过，
才能更新 changelog 为工程关闭并删除本节的待验证状态。

## 5. 唯一接手动作

1. 在三平台分别构建基线与候选 packaged 应用，并使用同一 manifest 运行四组 cache/session/video 矩阵。
2. 在 Windows 接入并证明 WIC provider；任何 native backend 失败率和 Qt fallback 比例原样写入结果。
3. 对失败门槛按 queue/cache/decode/upload/draw 定位，修正后先重跑失败组，再重跑全矩阵。
4. 回填本 handoff 的平台表、样本数、backend/fallback 分布、P50/P95 与视觉签核；全部通过后才关闭总契约。

# Phase 5 Handoff：统一 Render Transaction 与三平台工程关闭

> **状态：已完成的历史 closure。** 后续平台 decoder、QRhi startup 和 surface
> lifecycle 已继续演进；当前合同以
> [`docs/architecture.md`](../../../architecture.md) 和
> [`DETAIL_OPEN_BENCHMARK_RUNBOOK.md`](../../../requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md)
> 为准。

> 更新日期：2026-07-19
> Phase 5 状态：工程收口完成；GPU-first 为唯一生产路径，Windows/Linux 手工验收完成
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
- Windows 实机验收暴露并修复了 `ctypes.wintypes.HRESULT` 在部分 CPython 中不存在的问题；所有 WIC/COM
  签名现使用固定 32-bit signed HRESULT，失败码按 unsigned 32-bit 格式记录。

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

- 关闭后 Detail decoder/transaction/scheduler/cache/session/residency/benchmark 定向组合：
  `64 passed, 1 skipped in 1.51s`；skip 是 macOS 上真实 Windows WIC 用例的平台门控。
- 最终单进程全仓：`2806 passed, 12 skipped, 12 warnings in 38.39s`。
- 全仓曾在 `_AspectRatioSection -> load_icon()` 偶发 QtSvg native segfault。根因是 function-scoped `qapp`/
  `qtbot` 没有为整个 pytest 进程保留 `QApplication` Python 强引用，而模块级 QIcon/QPixmap cache 跨 fixture
  存活。测试基础设施现持有唯一 QApplication；修复后同一全仓命令完整通过。
- `tools/check_architecture.py`、`compileall -q src tools`、Phase 5 变更集 Ruff `F,E9` 与
  `git diff --check` 均通过。全仓 Ruff `F,E9` 仍有基线既存 286 项（集中在 demo/旧测试），不在本次重构中
  扩散修改或用 ignore 隐藏。
- 已证明旧 flag/cache/API 不再有源码引用；native provider 失败不会吞掉 cancellation，且只回退 Qt decoder。

## 4. 平台验收与关闭结论

- macOS 保持 QRhi/Metal 默认路径、ImageIO→Qt fallback 和已有开发验证；OpenGL 继续作为诊断兼容模式。
- Windows 已对 QRhi/OpenGL、WIC 实际加载/解码和 Qt fallback 做手工验收；WIC alpha PNG 自动化用例在真实
  Windows Python 执行，不再被平台 skip。
- Linux 已对 QRhi/OpenGL、Qt still decode、Detail/Edit 会话和主要交互做手工验收。
- source/offscreen 自动化继续承担确定性状态机、scheduler、cache/session 和 benchmark schema 回归；平台
  手工验收承担真实驱动、codec、窗口系统和视觉结果。

据此 Phase 5 作为工程实现和跨平台功能验收关闭。packaged harness、30 次采样和绝对/相对 SLO 校验继续作为
发布性能回归工具；原始数据位于 ignored `benchmark-output/`，本 handoff 不伪造未归档的 P50/P95 数值。

## 5. 后续维护约束

1. 修改 decoder registry、WIC/ImageIO/Qt/rawpy、QRhi shader 或 texture residency 时，必须重跑对应目标平台。
2. `wic_to_qt`、`imageio_to_qt` 和 RAW fallback 分布必须原样保留，不能把 fallback 统计为 native 成功。
3. 性能回归按 queue/cache/decode/upload/draw 定位；不得恢复旧 v2、full-frame cache 或 still Edit CPU preview。
4. profiler/manifest/schema 变化必须同步 `DETAIL_OPEN_BENCHMARK_RUNBOOK.md`、benchmark 测试及本架构文档。

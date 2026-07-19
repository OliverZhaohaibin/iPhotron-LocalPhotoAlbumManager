# Gallery → Detail GPU-first 打开链路重构

> 状态：Phase 1–5 工程收口完成；GPU-first 为唯一生产路径，三平台功能/视觉验收完成
> 文档版本：1.1
> 创建日期：2026-07-18
> 完成日期：2026-07-19
> 适用范围：Gallery 点击静态照片至 Detail/Edit 最终呈现
> 主要平台：macOS Metal、Windows OpenGL QRhi、Linux OpenGL QRhi

## 1. 执行摘要

重构前，从 Gallery 点击缩略图到 Detail 显示已应用编辑效果的照片明显缓慢，
大尺寸 JPEG/HEIC、RAW 和带 `.ipo` sidecar 的照片尤其突出。旧实现虽然具备
generation、后台解码、全分辨率帧 LRU 和 GPU shader，但关键路径的调度单位仍然是
“整张原图”：首屏必须等待全分辨率解码、sidecar 解析、颜色统计、全纹理上传和
mipmap 生成。Gallery 的 hover/mousePress 预取还可能与正式点击同时解码同一文件；
进入 Edit 后又会重新读取 sidecar、重新解码原图并建立一套 CPU preview session。

本项目不以最小修补为目标，而是重建以下边界：

```text
Gallery interaction
  -> Detail render transaction
  -> source/edit-state resolution
  -> viewport-aware decode
  -> neutral surface cache
  -> GPU texture residency
  -> shader edit pipeline
  -> final presentation
  -> shared Detail/Edit render session
```

最终链路采用 GPU-first 而不是不现实的 GPU-only：文件读取和压缩格式解码可以由
CPU 或平台原生库完成；decoder 输出一次规范化的上传表面，此后的变换、编辑、LOD
采样和呈现全部在 GPU 完成。首屏质量以当前视口物理像素需求为准，不再以相机传感器
分辨率为准。

## 2. 问题定义

### 2.1 重构前的用户可见问题

- 点击缩略图后 Detail 长时间显示空白/加载表面。
- 24–60MP 照片、HEIC、RAW 和带编辑 sidecar 的照片打开更慢。
- 快速切换照片时，旧 decoder 会继续占用 CPU、磁盘和内存带宽。
- 返回刚浏览过的照片仍可能重新做 sidecar 和全图工作。
- 进入 Edit 后即使 Detail 已有同一张照片，仍再次加载和准备原图。

### 2.2 重构前的真实链路

1. Gallery hover 120ms 或 mousePress 发出 Detail 预取。
2. mousePress 预取启动低优先级全分辨率 `_AdjustedImageWorker`。
3. `itemClicked` 经 `GalleryViewModel`、`NavigationCoordinator` 和
   `PlaybackCoordinator` 请求打开 Detail。
4. `DetailViewModel` 先切换路由，再创建轻量 presentation。
5. `PlayerViewController` 取消旧 worker；已经进入 `QImageReader`/`rawpy` 的原生
   decoder 无法真正中断。
6. 正式请求可能在第二条 decode lane 再次解码相同源文件。
7. worker 执行路径规范化、源文件和 sidecar stat、全图解码、sidecar
   exists/read、颜色统计和 adjustment resolve。
8. 全分辨率 `QImage` 进入 frame cache，再交给 GUI/render 边界。
9. viewer 清理或创建纹理、执行必要的颜色格式转换、上传全图并生成 mipmap。
10. shader 应用编辑，实际 draw 后发出最终 presented 事件。
11. 进入 Edit 时再次读取 sidecar、再次全图解码、创建 CPU preview session 并重算
    颜色统计。

### 2.3 根因与优先级

| 优先级 | 根因 | 直接影响 |
|---|---|---|
| P0 | mousePress 预取与正式点击重复解码 | 同一原图竞争 I/O、CPU 和内存带宽 |
| P0 | 首屏按传感器分辨率解码 | 用户可见延迟随像素总量增长 |
| P0 | Detail/Edit 不共享 render session | 进入编辑重复读取、解码和统计 |
| P0 | 全纹理上传与首屏 mipmap | GPU 成本与原图尺寸绑定 |
| P1 | 中性像素缓存与 sidecar revision 耦合 | 仅修改编辑参数也会重解码源文件 |
| P1 | ColorStats/edit state 未索引化 | 每次 cache miss 重复 I/O 和统计 |
| P1 | profiler 同步追加 JSONL | 测量本身污染 GUI/decoder 时序 |
| P2 | 平台硬解和 RAW embedded preview 利用不足 | 重型格式冷打开上限偏高 |

## 3. 最终目标与性能 SLO

### 3.1 用户体验目标

- Gallery 点击到静止首屏只呈现一次最终照片，不先显示低清图后自动替换。
- Detail 路由立即可见；媒体工作不得阻塞 GUI event loop。
- 当前视口对应的最终编辑结果准备好后原子切换，不显示半成品或旧 generation。
- 用户主动放大或进入更高倍率编辑时才加载更高 LOD；加载期间保留当前层。
- Detail 与 Edit 共享同一 source/render session，切换 chrome 不触发同源重解码。

### 3.2 延迟与正确性门槛

| 指标 | 最终门槛 |
|---|---:|
| 普通 JPEG/PNG/HEIC click -> final photo P95 | <= 150ms |
| RAW、40–60MP、重裁剪 click -> final photo P95 | <= 300ms |
| 热缓存重访 P95 | <= 80ms |
| click -> Detail route visible P95 | <= 32ms |
| interactive 后 GUI 单任务 P95 | <= 24ms |
| 同 asset、同 decode level 并行 decoder | <= 1 |
| stale generation 最终呈现次数 | 0 |
| GUI/render 线程同步文件、sidecar、decode I/O | 0 |

### 3.3 “最终高清”的定义

“最终高清”指在当前显示条件下无可见分辨率损失的最终编辑结果：

- 以 viewport physical pixels、device pixel ratio、crop、旋转和透视投影后的源像素需求
  计算 decode level。
- 首屏不要求加载完整传感器分辨率。
- 只有当缩放倍率、crop ROI 或编辑工具需要更多源像素时，才请求更高 LOD。
- LOD 替换必须原子完成；静止首屏不进行无用户动作的自动清晰度升级。

## 4. GPU-first 技术边界

### 4.1 本项目中的 GPU-first

- 文件读取由操作系统完成。
- JPEG、PNG、HEIC 和无硬件后端时的 RAW 压缩解码允许在 worker CPU/平台库执行。
- decoder 直接产生规范化的 GPU 上传格式，避免 GUI/render 线程全图格式转换。
- 解码后的缩放、颜色调整、曲线、levels、白平衡、黑白、选择性色彩、裁剪、旋转、
  透视、锐化、降噪、暗角和 LOD 采样全部由 GPU 完成。
- 同一 surface 只上传一次；Detail/Edit 共享纹理与 render state。
- macOS 默认 Metal QRhi；Windows/Linux 使用当前 QRhi/OpenGL 后端。

### 4.2 明确非目标

- 首轮不实现 GPU RAW demosaic、坏点修复、镜头模型和相机色彩校准。
- 首轮不新增 HDR/10-bit 输出语义，保持当前 RGBA8/sRGB 显示行为。
- CoreGraphics/ImageIO、WIC 等平台硬解是后续可插拔 backend，不是 Phase 1 前置条件。
- Export 保持独立的全分辨率质量路径，不把导出要求重新带回 Detail 首屏。

## 5. 最终架构与内部接口

### 5.1 核心类型

`DetailRenderRequest`

- 一次用户可见打开事务的不可变描述。
- 包含 generation、asset descriptor、viewport physical size、DPR、crop/rotation 和请求原因。

`DetailDecodeKey`

- decoder 去重与缓存的稳定 key。
- 最终由 asset id、source revision 和 decode level 组成，不包含 sidecar revision。

`AssetSourceIdentity`

- 描述中性源像素版本：文件大小、mtime_ns/index revision、方向和色彩空间。
- 由扫描/index/watcher 更新，点击关键路径不重新 stat。

`EditRenderState`

- 描述 sidecar revision、原始 adjustment、解析后的 shader 参数和 `ColorStats`。
- 与 source surface 独立失效。

`DecodedSurface`

- 中性、方向已规范化的 RGBA8888/sRGB surface。
- 包含实际尺寸、LOD、source identity 和明确的缓冲区所有权。

`PhotoRenderSessionHandle`

- 持有当前 GPU texture、可用 LOD、source identity 和 edit render state。
- 由 Detail 与 Edit 共同使用，不暴露可变 `QImage` 所有权。

`StillDecodeBackend`

- `decode(request, cancellation) -> DecodedSurface`。
- 默认实现使用 Qt 缩放解码；RAW backend 先探测几何和候选尺寸，再在 embedded preview、half-size、
  full 中只解码一个满足当前 LOD 的候选。numpy RGB 直接复制为 QImage，不经过 PNG 编解码中转。

`DetailStillRequestScheduler`

- 对同 key 任务去重、提升优先级、管理 generation、取消和结果发布。
- 不承担 GPU 呈现。

`DetailRenderCoordinator`

- 管理 click、route、decode、upload、draw 和 presented 的完整事务。
- 是最终 UI generation 的唯一所有者。

### 5.2 线程契约

- GUI 线程：创建请求、更新轻量 UI、提交 render state；禁止同步 stat、sidecar I/O 和 decode。
- decoder worker：文件访问、压缩解码、必要的规范化输出。
- GPU/render 线程：texture upload、shader state 和 draw。
- profiler writer：独立批量追加 JSONL。
- 任一过期 generation 都不得越过下一线程边界。

### 5.3 请求状态机

```text
created
  -> routed
  -> queued | reused | promoted
  -> decoding
  -> surface_ready
  -> upload_pending
  -> draw_pending
  -> presented

任意非终态 -> stale/cancelled/failed
```

每个有效 generation 只能产生一个 `presented`；stale、cancelled、failed 是互斥终态。

## 6. 缓存与失效规格

### 6.1 三层缓存

磁盘中性 surface cache：

- 默认预算 `min(可用磁盘空间 2%, 2GB)`，LRU 回收。
- 层级为 1024、2048、3072、4096 最长边。
- 使用带版本头、可 memory-map 的 RGBA8/sRGB surface。
- key 包含 source identity、方向/色彩空间和 LOD；不包含 sidecar。

CPU/mapped surface cache：

- 预算为物理内存 2%，限制在 128–512MB。
- 以真实字节数计费，不使用固定 entry 数近似。

GPU texture cache：

- 保留当前、前一项和后一项。
- 最多三张或 192MB，任一限制先到即回收。
- 同尺寸 texture 应复用资源，首屏 surface 不生成 mipmap。

### 6.2 失效规则

- 源文件变化：失效 source surface、GPU texture 和对应 render state。
- sidecar 变化：只失效 `EditRenderState`，中性 surface 保留。
- viewport/DPR 变化：重新选择 LOD，不修改 source identity。
- library 切换：取消旧 generation，清理库作用域的 mapped/GPU 资源。
- 内存压力：先清相邻 GPU texture，再清 CPU surface，磁盘 LRU 不同步阻塞 GUI。
- 外部修改由 watcher/index revision 驱动；点击关键路径不通过重复 stat 保证一致性。

## 7. 分阶段开发方案

### Phase 1：请求调度、去重与可信观测

- **输入架构**：现有 `_AdjustedImageWorker`、两线程 decode pool、Detail v2 generation、
  full-frame LRU 和 Gallery hover/mousePress 预取。
- **生产代码产物**：删除 mousePress 全图预取；hover dwell 改为 150ms 并传完整
  `DetailPrefetchDescriptor`；新增 `DetailStillRequestScheduler`；JSONL profiler 改为有界队列和
  后台批量 writer。
- **内部 API 变化**：Phase 1 `DetailDecodeKey = asset_id + absolute source path`；
  `PlayerViewController.display_image()` 接收 `asset_id` 和 generation；worker 只能由 scheduler
  factory 创建，controller 仅订阅 scheduler 的统一 ready/failed/finished 生命周期。
- **自动化测试**：覆盖 mousePress、hover 取消、queued promotion、running reuse、重复点击、
  A->B 绕行、stale 丢弃、writer 线程归属和有界 shutdown。
- **性能/正确性门槛**：同 key 并行 decoder <=1，stale presented=0，profiler 热路径同步文件
  I/O=0；不以源码推断最终 P95。
- **已知降级路径**：`IPHOTO_DETAIL_SCHEDULER_V3=0` 可恢复诊断性旧式重复调度；
  full-resolution worker 和旧 frame cache 暂时保留。
- **handoff 完成条件**：定向测试、compileall、diff check 全部通过；记录真实修改文件、事件
  样例、测试计数和 Phase 2 唯一接手点；不得声称达到 150/300ms SLO。

### Phase 2：视口级 decoder

- **输入架构**：保持 Phase 1 scheduler 的 key 去重、priority promotion、generation 和发布契约。
- **生产代码产物**：落地 `DetailRenderRequest`、`AssetSourceIdentity`、`StillDecodeBackend`、
  `DecodedSurface`；实现 viewport/DPR/crop/rotation-aware level 选择；RAW 优先 embedded preview。
- **内部 API 变化**：`DetailDecodeKey` 扩展为 asset id、source revision、decode level；scheduler
  的 worker factory 改为 backend request，ready payload 从 full `QImage` 改为 `DecodedSurface`。
- **自动化测试**：验证 DPR、旋转、透视和 crop 投影尺寸；JPEG/PNG/HEIC scaled decode；RAW
  embedded/half/full fallback；透明图与 EXIF orientation；取消时 buffer 所有权安全。
- **性能/正确性门槛**：普通首屏 decode level 不超过视口需求的容差层级；RAW/极端 crop 的
  fallback 不阻塞 GUI；同 key decoder 和 stale 门槛保持 Phase 1 水平。
- **已知降级路径**：backend 不支持 scaled/ROI 时允许 worker 完整解码后规范化缩放；失败时回退
  Qt 当前 decoder，但仍由 scheduler 独占任务。
- **handoff 完成条件**：所有格式返回版本化中性 surface；首屏不再默认请求传感器全尺寸；
  记录每种 backend/格式的实际选择与 fallback 覆盖率。

### Phase 3：中性 surface 与 GPU residency

- **输入架构**：Phase 2 的 versioned `DecodedSurface`、source identity 和 decode level。
- **生产代码产物**：中性磁盘 surface store、mapped CPU LRU、QRhi texture residency manager、
  当前/前一/后一 texture ring；首屏 upload 禁止生成 mipmap。
- **内部 API 变化**：scheduler surface-ready 后交给 residency manager；viewer 接收 texture/session
  引用而不是每次接收并重新上传 `QImage`；cache key 明确排除 sidecar revision。
- **自动化测试**：预算与 LRU、cache header/version/损坏、memory pressure、sidecar/source 分离失效、
  texture 复用、无首屏 mipmap、Metal/OpenGL context loss 恢复。
- **性能/正确性门槛**：热 surface cache和热 GPU cache分别采集 >=30 次 packaged P95；sidecar
  修改导致 source decode 次数为 0；同一 surface upload 次数为 1。
- **已知降级路径**：cache 文件损坏或 mmap 失败按 miss 处理并异步重建；GPU 分配失败时回收相邻
  texture 后重试，必要时仅保留当前 texture。
- **handoff 完成条件**：三层预算和失效审计通过；提交真实 upload/cache 命中指标；明确仍未共享的
  Edit 调用点。

### Phase 4：Detail/Edit 共用会话

- **输入架构**：Phase 3 texture residency、独立 `EditRenderState` 和 shader adjustment pipeline。
- **生产代码产物**：实现 `PhotoRenderSessionHandle`；Detail/Edit 共享 source texture、LOD 和 edit
  state；删除 Edit 同源 loader、CPU realtime preview 和重复 ColorStats 路径。
- **内部 API 变化**：Detail/Edit 页面只交换 session handle；sidecar 更新生成新的 immutable
  `EditRenderState` 并提交 shader，不触碰 source surface identity。
- **自动化测试**：Detail->Edit->Detail、撤销/重做、全部 adjustment 组合、crop/rotate/perspective、
  高倍率 LOD、关闭时 session 引用释放、Export 隔离。
- **性能/正确性门槛**：进入 Edit 的同源 decode 和 texture upload 均为 0；LOD 原子替换期间无空白；
  shader 结果与现有基准图容差一致。
- **已知降级路径**：Export、元数据写回和不支持 GPU 的离线算法继续独立请求全分辨率 source；
  session 丢失时经 scheduler 重建，不恢复旧 CPU preview 双链路。
- **handoff 完成条件**：旧 Edit 重复 decode/preview session 删除；session 生命周期和资源预算在三平台
  通过；列出仍依赖旧 v2 的入口。

### Phase 5：平台优化与工程关闭（已完成）

- **输入架构**：Phase 1–4 跨平台稳定的 GPU-first still pipeline 和完整 profiler 事件。
- **生产代码产物**：可插拔 ImageIO/CoreGraphics、WIC backend；视频接入同一 render transaction；
  packaged benchmark 自动化；删除 Detail v2、旧 full-frame cache 耦合和诊断 fallback。
- **内部 API 变化**：backend registry 按平台/格式选择，公共 coordinator 只认 render transaction；Detail
  渲染保持单一生产链路，不再保留版本 flag 或旧实现分支。
- **自动化测试**：macOS Metal、Windows/Linux OpenGL QRhi 全矩阵；backend parity；视频取消/切换；
  packaged 冷/热缓存重复 >=30 次；升级/回退兼容。
- **性能/正确性门槛**：普通格式 P95 <=150ms，重型格式 P95 <=300ms，热重访 P95 <=80ms，
  route <=32ms，GUI task <=24ms，stale presented=0。
- **已知降级路径**：平台 decoder 失败只允许在 worker 内回退 Qt 通用 backend；不恢复旧 ViewModel、
  full-frame cache 或视频兼容双链路。
- **handoff 完成条件**：三平台 packaged 证据齐全、SLO 和正确性矩阵全部通过、旧实现删除、运行手册和
  发布说明更新，本总体契约的完成定义全部关闭。

## 8. 测试、Benchmark 与发布策略

### 8.1 自动化矩阵

- 格式：JPEG、PNG、HEIC、RAW、透明图片。
- 状态：无编辑、全部编辑组合、旋转、普通 crop、极端 crop。
- 交互：单击、hover 后单击、快速 A->B->A、返回 Gallery、进入/退出 Edit、关闭应用。
- 缓存：冷 Detail RAM、热 surface cache、热 GPU texture。
- 资源：正常内存、低内存回收、缓存损坏、源文件/sidecar 外部修改。

### 8.2 正式性能证据

- macOS Metal、Windows OpenGL QRhi、Linux OpenGL QRhi 分别采集。
- 使用 packaged 构建、本地图形后端和专用测试图库。
- 每个媒体等级和冷热组合至少 30 次。
- 记录 click、route、queue、cache、decode、upload、draw 和 presented 的独立耗时。
- 取消事务单独统计，不并入完成延迟。
- 原始 JSONL 留在被忽略的 `benchmark-output/`，不得记录用户绝对路径。
- 具体命令沿用并逐阶段更新
  [`DETAIL_OPEN_BENCHMARK_RUNBOOK.md`](../DETAIL_OPEN_BENCHMARK_RUNBOOK.md)。

### 8.3 发布与迁移

- GPU-first 已是唯一生产 Detail 链路；`IPHOTO_DETAIL_RENDER_V3`、
  `IPHOTO_DETAIL_PIPELINE_V2` 和 `IPHOTO_DETAIL_SCHEDULER_V3` 均不存在。
- 平台原生 decoder 失败只在同一 worker 内回退 Qt，不恢复旧 ViewModel、
  full-frame cache 或重复 Edit loader。
- 后续性能回归继续使用 packaged harness 与本节矩阵；benchmark 原始输出保持
  ignored，不把用户图库路径或机器本地路径提交到仓库。

## 9. 总体完成定义

本项目已按以下条件完成工程收口：

- Gallery 点击关键路径没有同步文件、sidecar 或图片解码 I/O。
- 同 asset、同 level 在 queued/running 状态下最多一个 decoder。
- 首屏使用视口级最终质量且只呈现一次。
- 中性 source surface 与 sidecar render state 独立缓存和失效。
- Detail/Edit 共享 GPU render session；进入 Edit 不重解码同源照片。
- GPU texture 上传、资源复用和 LOD 行为满足三平台正确性测试。
- packaged harness 固化第 3 节 SLO 和快速切换 `stale presented=0` 门禁；
  Windows/Linux 已完成项目所有者手工验收，macOS 保持 Metal 开发/验证路径。
- profiling 不污染被测线程，日志满足隐私要求。
- 旧 Detail v2 full-resolution 首屏、旧 frame cache 耦合和重复 Edit loader 已删除。

Phase 1–5 的生产代码、自动化门禁、平台 fallback 与运行手册均已落地；旧链路已删除。
具体机器的 P50/P95 原始数据仍按 runbook 作为 ignored 发布证据保存，本文不虚构或回填未归档的数值。

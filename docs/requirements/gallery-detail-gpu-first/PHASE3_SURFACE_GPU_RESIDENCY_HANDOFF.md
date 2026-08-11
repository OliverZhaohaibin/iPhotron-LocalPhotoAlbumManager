# Phase 3 Handoff：中性 Surface Cache 与 GPU Residency

> 更新日期：2026-07-18
> Phase 3 状态：代码与自动化测试完成；三平台 packaged 性能采样未执行
> 当前分支：`codex/gallery-detail-gpu-first-phase1`
> 基线提交：`5d5a8d490a5110fb9dec850452bfdafdad88ef50`
> 最终交付提交：`0849d624f428bea730a9471324aee5673b4469f7`
> 历史工作树状态：Phase 3 已包含在上述最终交付提交中；交付时工作树 clean

本文只记录 Phase 3 的真实落地结果。Phase 1–2 历史分别见
[`PHASE1_REQUEST_SCHEDULER_HANDOFF.md`](PHASE1_REQUEST_SCHEDULER_HANDOFF.md) 和
[`PHASE2_VIEWPORT_DECODER_HANDOFF.md`](PHASE2_VIEWPORT_DECODER_HANDOFF.md)，总体边界继续以
[`GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`](GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md) 为准。

## 1. 已落地的数据流与接口

- `DetailRenderRequest` 新增 `zoom_factor`、`residency_slot` 和 `window_generation`；LOD 选择把主动 zoom
  乘入 viewport/crop/rotation/perspective 源像素需求，缩小不会低于 fit LOD。
- `DecodedSurface` 新增 `cache_tier=decode|memory|disk` 和 `backing_owner`；disk hit 的 QImage 直接引用
  read-only mmap，owner 与 surface/viewer pending upload 同生命周期。
- `CachedStillDecodeBackend` 固定执行 memory → disk → codec；decode miss 先交付 heap surface，再由单独
  `iPhoto-surface-cache` lane 异步写盘。
- scheduler 新增 `warmed(request, surface, adjustments)` 和 `prefetch_window()`。previous/next 最多保留两个
  请求，但同时最多一个 speculative decoder；第二张串行等待，正式请求仍可使用另一 lane。
- viewer 新增 `set_still_surface()`、`warm_still_surface()`、`activate_resident_surface()`、
  `clear_still_residency()` 和 `trim_still_residency()`。热 GPU key 只切换绑定和 shader state，draw 后仍会
  发出本次 generation 的 `presented`。
- Player controller 监听 zoom 和 viewport resize，使用 80ms trailing debounce 请求更高 LOD；旧纹理在
  decode/upload 期间持续绘制，新纹理实际 draw 后才完成 generation。library rebind 清 mapped/GPU 层并
  切换 disk namespace。

## 2. Cache 格式、预算与所有权

磁盘 store 位于：

```text
<library>/.iPhoto/cache/detail-surfaces/v1/<sha256-prefix>/<sha256>.ipsurface
```

- schema 1 固定 4096-byte header，包含 `IPHSURF` magic、schema、metadata/payload 长度、尺寸、stride、
  source size、原 backend/fallback 和 xxhash64 payload 校验。
- key 摘要由 `DetailDecodeKey + orientation + RGBA8888/sRGB contract` 构成，不包含 sidecar revision。
- 写入使用同目录临时文件和 `os.replace()`；损坏、截断、版本不匹配、checksum/mmap 失败均按 miss 处理，
  损坏项异步删除并在 codec 成功后重建。
- disk LRU 预算为当前可用空间 2%，上限 2GB；访问在 worker 中更新 mtime，prune 也只在 cache I/O lane。
  目录未绑定或不可写时 disk tier 关闭，不影响 memory/decode。
- mapped/heap LRU 预算为物理内存 2%，限制在 128–512MB，以 `bytesPerLine × height` 计费。disk load 在
  decoder worker 内完成 checksum，因而同时预热所有 mmap 页面；GUI/render 代码不调用文件 API。
- library rebind 后，旧 source 若不属于新 library root，disk store 不生成路径，避免迟到 writer 污染新
  namespace。LRU eviction 只移除 cache 引用，不主动关闭仍由 viewer 持有的 mmap。

## 3. GPU residency 与 LOD 行为

- OpenGL `TextureManager` 与 Metal/QRhi `RhiImageRenderer` 都保留最多三张静态图，基础 RGBA 总量不超过
  192MB；当前 texture 受保护，预算不足时回收最旧邻图。
- 同尺寸被回收 texture 直接复用 storage/resource；同 key activation 不调用 texture upload。
- Detail still 使用 linear、非 mipmapped texture；OpenGL 不调用 `glGenerateMipmap`，QRhi 不创建
  `MipMapped` flag 或调用 `generateMips`。视频上传路径保持原行为。
- 为保持 Definition 可见效果，静态图 shader 使用 base-level 8/32/128 texel 多半径采样；视频继续使用
  原 mip LOD。OpenGL GLSL、QRhi GLSL 与 `image_viewer_rhi.frag.qsb` 已同步。
- 邻图 surface 每个 render tick 最多 warm 一张；warm 不改变 current source，也不发出 presentation。
- QRhi/OpenGL resource release 会清 GPU objects 并把当前 CPU surface 标记为待重传；下一 render 先恢复
  当前。显式 memory pressure 清 memory LRU、pending warm 和邻图 GPU texture，保留当前 draw state。
- upload 抛出内存/运行时错误时先清邻图再重试一次；第二次失败沿用原错误路径。主动 LOD 失败或 stale
  不替换旧层。

## 4. Profiler 与 benchmark

新增事件：

- `surface_cache_hit`、`surface_cache_miss`、`surface_cache_write`、`surface_cache_corrupt`
- `gpu_cache_hit`、`gpu_cache_miss`、`gpu_upload`、`gpu_evict`
- `lod_upgrade_requested`、`lod_upgrade_presented`、`context_rebuild`

示例（数值仅示意）：

```json
{"stage":"surface_cache_hit","generation":8,"details":{"asset_id":"as_42","tier":"disk"}}
{"stage":"gpu_cache_hit","generation":9,"details":{"key":"DetailDecodeKey(...)"}}
{"stage":"lod_upgrade_presented","generation":10,"details":{"decode_level":3072}}
```

`tools/detail_benchmark.py` 输出 schema 2，兼容旧 `image_presented` 和生产 `presented`，并汇总 cache tier、
codec decode、GPU upload/hit 次数。runbook 已增加 cold decode、hot disk/mapped、hot GPU 和 sidecar-only
四组采样要求。

## 5. 实际修改文件

生产代码：

- `src/iPhoto/gui/detail_surface_cache.py`（新增）
- `src/iPhoto/gui/detail_pipeline.py`
- `src/iPhoto/gui/detail_decode_backend.py`
- `src/iPhoto/gui/detail_request_scheduler.py`
- `src/iPhoto/gui/ui/controllers/player_view_controller.py`
- `src/iPhoto/gui/coordinators/desktop_coordinator_runtime.py`
- `src/iPhoto/gui/ui/widgets/gl_image_viewer/{resources.py,widget.py}`
- `src/iPhoto/gui/ui/widgets/{gl_texture_manager.py,gl_renderer.py,rhi_image_renderer.py}`
- `src/iPhoto/gui/ui/widgets/{gl_image_viewer.frag,image_viewer_rhi.frag,image_viewer_rhi.frag.qsb}`
- `tools/detail_benchmark.py`

测试：

- `tests/gui/test_detail_surface_cache.py`（新增）
- `tests/ui/widgets/test_still_texture_residency.py`（新增）
- `tests/gui/test_detail_pipeline.py`
- `tests/gui/test_detail_decode_backend.py`
- `tests/gui/test_detail_request_scheduler.py`
- `tests/ui/widgets/test_gl_image_texture_resources.py`
- `tests/test_detail_benchmark.py`

文档：

- `docs/requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md`
- `docs/requirements/gallery-detail-gpu-first/GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`
- `docs/requirements/gallery-detail-gpu-first/PHASE3_SURFACE_GPU_RESIDENCY_HANDOFF.md`（本文件）

## 6. 自动化验证实录

Phase 1–3、renderer、cache 与 benchmark 定向组合：

```text
174 passed in 2.00s
```

完整仓库：

```text
2752 passed, 11 skipped, 12 warnings in 39.65s
```

warnings 为既有 pytest collection、deprecated compatibility import、Torch/Qt deprecation；没有 Phase 3
失败。修改文件的 Ruff `F,E9`、`compileall` 与 `git diff --check` 均通过；handoff 最终修改后仍会再次
执行轻量门禁，最终结果以交付消息为准。

## 7. 已证明与未证明

自动化已证明：

- surface 文件 round-trip、mmap owner、真实字节 LRU、source revision miss、sidecar 不进入 key。
- truncated/payload corruption 被拒绝；未绑定 store 安全降级。
- previous/next 串行 speculative decode、两张 warm 发布和旧 window generation 丢弃。
- zoom factor 提高 LOD，缩小不低于 fit level。
- OpenGL still texture 不生成 mipmap；三张/192MB 回收、同 key activation 不上传、当前 texture 受保护。
- benchmark 能识别生产 `presented` 并统计 cache tier/GPU upload。
- 全仓现有行为没有自动化回归失败。

尚未证明：

- 未运行 macOS Metal、Windows/Linux OpenGL QRhi packaged，每组 >=30 次的 cold/hot/sidecar 数据为空。
- 没有真实 P50/P95，不能声称普通 <=150ms、重型 <=300ms 或热重访 <=80ms。
- context loss、GPU allocation failure 和 mmap page residency 只有代码级降级与 mock/offscreen 覆盖，没有
  三平台驱动/系统内存压力实测。
- Definition base-level 多半径实现已通过 shader 编译与现有测试，但尚未完成 packaged 基准图人工视觉签核。
- sidecar-only 可复用 source/GPU key，但 Phase 3 没有缓存 `EditRenderState`，仍会读取 sidecar和重新计算
  ColorStats；Detail/Edit 也仍未共享 session。

因此 Phase 3 当前只能标记为“代码与自动化完成、packaged 性能待验证”。

## 8. 降级路径与 Phase 4 唯一接手方向

- cache 文件损坏、mmap/写入失败：按 miss 走现有 codec；memory/GPU 层继续可用。
- GPU 预算/分配压力：先清邻图并重试；当前已呈现纹理不被主动回收。
- library/context 重建：通过 scheduler 和 retained surface 重建，不恢复旧 full-frame cache。

Phase 4 只能从现有 `DecodedSurface + DetailDecodeKey + texture residency` 边界建立
`PhotoRenderSessionHandle`，让 Detail/Edit 共享 source texture、LOD 和 immutable edit state，并删除 Edit
同源 loader、CPU realtime preview 与重复 ColorStats。不得把本阶段未执行的 packaged SLO 或仍存在的 Edit
重复工作写成已完成。

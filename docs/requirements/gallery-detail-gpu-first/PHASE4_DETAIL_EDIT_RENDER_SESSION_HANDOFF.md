# Phase 4 Handoff：Detail/Edit 共享 GPU Render Session

> 更新日期：2026-07-18
> Phase 4 状态：代码与自动化测试完成；三平台 packaged 性能采样未执行
> 当前分支：`codex/gallery-detail-gpu-first-phase1`
> 基线提交：`0849d624f428bea730a9471324aee5673b4469f7`
> Phase 5 接手基线：`da427c6bffd844bab0ee395fefc1475f4e542d97`
> 历史工作树状态：Phase 4 代码及后续修正均已提交；Phase 5 接手时工作树 clean

本文记录 Phase 4 的真实落地结果。总体边界以
[`GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`](GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md) 为准；
Phase 3 的 surface/GPU residency 历史见
[`PHASE3_SURFACE_GPU_RESIDENCY_HANDOFF.md`](PHASE3_SURFACE_GPU_RESIDENCY_HANDOFF.md)。

## 1. 共享会话与数据流

- 新增 immutable `EditRenderState`，保存 revision、只读 raw adjustments、shader adjustments 和
  `ColorStats`；`PhotoRenderSessionHandle` 保存 source identity、当前 texture key、可用 LOD、live state 与
  persisted baseline。
- `PlayerViewController` 是 still render session 的唯一所有者，按 asset/source revision/orientation 保留
  current/previous/next 最多三个 handle；library rebind 清空，memory pressure 仅保留 current。
- `DecodedSurface` 携带 source-derived `ColorStats`。`CachedStillDecodeBackend` 对同 source revision 最多计算
  一次并跨 LOD 复用；disk surface schema/namespace 升为 v2，header 持久化统计值。
- scheduler 的 `ready(generation, surface)` 与 `warmed(request, surface)` 只发布中性 surface；shader resolve
  在 GUI-owned session 边界完成，sidecar revision 不进入 `DetailDecodeKey`。

```text
Detail sidecar preparation + neutral surface
  -> create/reuse PhotoRenderSessionHandle
  -> resident DetailDecodeKey texture + immutable EditRenderState
  -> Edit acquire same handle
  -> slider/undo/redo updates shader state only
  -> Done promotes live state / Cancel restores baseline
  -> Detail keeps the same resident texture
```

## 2. Edit、fullscreen 与 LOD 行为

- 静态 Edit 入口不调用 `read_adjustments()`、独立 image worker、`set_image()` 或 texture upload；若当前
  session 不存在，拒绝进入旧双链路并保留 Detail。
- sidebar 从当前 viewport surface 生成小图并直接使用 session ColorStats，不重复统计。
- slider、undo/redo、crop、rotate 和 perspective 生成新的 immutable state；compare 只临时提交中性 shader。
- edit-state 更新会重新评估 viewport/geometry LOD；decode/upload 期间旧层继续绘制，新层 draw 后原子替换。
- static Done/Cancel 不再发 `MediaRestoreRequest`。Done 写 sidecar 后提升 baseline；Cancel 恢复原 baseline；
  Playback 只轻量恢复 Detail 人脸 overlay。
- fullscreen 只改变 chrome/splitter/window 布局并触发既有 viewport LOD 逻辑，不再同步读取全分辨率原图。
- 视频 Edit、Export、元数据写回和离线全分辨率算法保持独立。

## 3. 已删除的旧 still Edit 路径

- 删除 `EditPreviewManager`、`PreviewRenderWorker`、`ImageLoadWorker` 和仅供该链路使用的
  `core.preview_backends`。
- `EditPipelineLoader` 只保留 sidebar preview generation/cancellation，不再暴露 full-image load signal/API。
- Edit 与 fullscreen 不再创建 CPU preview session，也不再以 `Path` 替换 viewer 的 `DetailDecodeKey`。

## 4. Profiler 与自动化证据

新增事件：`render_session_created`、`render_session_acquired`、`edit_state_updated`、
`render_session_released`。这些事件不记录绝对路径。

Phase 1–4 定向组合：

```text
200 passed in 3.32s
```

Phase 5 接手前重新执行单进程完整仓库：

```text
2791 passed, 11 skipped, 12 warnings in 41.38s
```

早期 handoff 记录的 `icons.py -> EditPerspectiveControls` Qt 原生构造段错误在接手基线已不再复现；现状以
上述单进程结果为准。warnings 仍为既有 pytest collection、compatibility import、Torch/Qt deprecation。

修改文件 Ruff `F,E9`、`compileall` 与 `git diff --check` 必须在 handoff 最终编辑后再次执行；最终结果以
交付消息为准。

自动化已证明：

- edit state mapping immutable，live/commit/cancel revision 与 baseline 行为正确。
- edit state 更新只调用 viewer adjustments，不调用 `set_image()`，texture key 保持不变。
- LOD 替换保留 edit state 与 available-level 记录。
- surface v2 round-trip 保留 ColorStats；同 source revision 跨两个 codec LOD 只统计一次。
- static Edit loader/CPU preview API 已从 runtime 删除；视频与全仓现有行为无 assertion 回归。

## 5. 未证明与 Phase 5 接手方向

- 未运行 macOS Metal、Windows/Linux OpenGL QRhi packaged 的每组 >=30 次采样；没有真实 P50/P95，不能
  声称普通 <=150ms、重型 <=300ms、热重访 <=80ms。
- context loss、GPU allocation failure 与系统内存压力仍只有自动化/mock 证据；Definition 视觉基准未完成
  三平台人工签核。
- `index_revision` 是 edit-state 的保守外部 revision，可能产生多余 state 重建，但不会失效中性 source key。

Phase 5 从现有 render session/coordinator 边界继续：完成三平台 packaged 矩阵、平台 backend 优化、视频
render transaction 接入、旧 Detail v2/diagnostic fallback 清理与最终 SLO 关闭。不得把本 handoff 的
source/offscreen 自动化结果写成 packaged 性能证据。

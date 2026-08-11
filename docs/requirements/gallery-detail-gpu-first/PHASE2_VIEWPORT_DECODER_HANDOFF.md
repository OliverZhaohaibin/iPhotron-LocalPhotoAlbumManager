# Phase 2 Handoff：Detail 视口级 Decoder

> 更新日期：2026-07-18
> Phase 2 状态：代码、定向自动化测试与文档完成；packaged 性能采样未执行
> 当前分支：`codex/gallery-detail-gpu-first-phase1`
> 基线提交：`3f7e5cbf72e79f42aec568685282601af1aeb499`（`fix: preserve detail scheduler request ownership`）
> 工作树状态：Phase 2 作为未提交变更位于上述基线之上；交付前无已知测试失败

本文只记录 Phase 2 的真实落地结果。Phase 1 历史保持在
[`PHASE1_REQUEST_SCHEDULER_HANDOFF.md`](PHASE1_REQUEST_SCHEDULER_HANDOFF.md)，总体边界以
[`GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md`](GALLERY_DETAIL_GPU_FIRST_REARCHITECTURE.md) 为准。

## 1. 已落地的数据流与 API

新增的不可变请求/身份类型：

```python
AssetSourceIdentity(path, size_bytes, source_mtime_ns, index_revision,
                    width, height, orientation)
DetailGeometryState(crop_*, rotate90, straighten,
                    perspective_vertical, perspective_horizontal)
DetailRenderRequest(generation, asset_id, source_identity,
                    viewport_physical_size, device_pixel_ratio, geometry,
                    reason, texture_limit, raw_adjustments, decode_level)
DetailDecodeKey(asset_id, source, source_revision, decode_level)
DecodedSurface(image, decode_key, source_size, decoded_size, decode_level,
               backend, fallback, pixel_format, color_space, orientation_applied)
StillDecodeBackend.decode(request, cancellation) -> DecodedSurface
```

`DetailRenderRequest.raw_adjustments` 在构造时复制为只读 mapping。`DecodedSurface.image` 是 worker
独占、detached 的 `RGBA8888/sRGB QImage`。key 只包含中性源 revision 与 decode level，不包含 sidecar
revision。

Scheduler 现在接收完整 request：

```python
DetailStillRequestScheduler.prefetch(request) -> bool
DetailStillRequestScheduler.request(request) -> bool
ready(generation, DecodedSurface, adjustments)
failed(generation, source, message)
finished(DetailDecodeKey)
```

Phase 1 的 queued promotion、running reuse、A→B 双 lane 绕行、generation/stale 丢弃和 bounded
shutdown 保留。同 key hover/click 复用时会把最新 render request 交给 worker，因此复用的是中性 surface
decode，而不是 hover 的旧 adjustments/generation；不同 level 不复用。

## 2. 线程与所有权

```text
GUI thread
  -> 由内存索引构造 AssetSourceIdentity（点击/hover 不 stat）
  -> 读取 viewer layout + DPR，选择 LOD，提交完整 request

sidecar preparation pool (2 lanes)
  -> read_adjustments；latest-only queue 可绕过一个卡住的 native RAW probe
  -> hover/click 可 promotion/reuse；running stale cooperative cancel 后不再按 key 复用
  -> 完成后释放，不形成 edit-state cache

still decode pool (2 lanes)
  -> Qt/rawpy decode + cancellation checkpoints
  -> orientation/color/RGBA8888 规范化和 detached copy
  -> surface ColorStats + shader adjustment resolve

GUI/render boundary
  -> viewer 直接接收 DecodedSurface.image
  -> DetailDecodeKey 作为稳定 image_source
  -> QRhi render thread 上传与绘制
```

无有效 viewport 时 hover 不提交；正式点击保持原子 placeholder，并在有效 layout tick 后提交。controller
不再做 texture-limit 缩放，也不再让 `DetailFrameCache` 参与 Detail 打开链路。旧 cache 类型仅为兼容测试
保留，Phase 3 才建立正确的中性 surface cache。

## 3. LOD、backend 与 fallback

LOD 以物理 viewport 为目标，在源显示尺寸上组合 crop 放大、90° rotation、straighten 和 perspective
投影放大，选择最小满足的 `1024/2048/3072/4096` 最长边层级。小源不放大；需求超过 4096 返回
`full` 并记录 `full_level`。未知尺寸的旧库行也走可见的 `full` 兼容路径，下一次扫描后恢复正常 LOD。

| 格式 | 正常 backend | worker 内 fallback | 当前证据 |
|---|---|---|---|
| JPEG | Qt `QImageReader` auto-transform + scaled decode | `pillow`；插件忽略 scaled decode 时 `qt_full_scale` | 实际 JPEG EXIF orientation 测试；fallback mock |
| PNG | Qt `QImageReader` scaled decode | 同上 | 实际透明 PNG，alpha/RGBA8888/sRGB/缩放测试 |
| HEIC | 已安装 Qt image plugin | `pillow` 或 `qt_full_scale`（取决于 packaged 插件） | 扩展名/忽略 scaled decode mock；尚无 packaged 样本 |
| RAW | `rawpy` embedded preview | embedded 不足后 `half`，仍不足后 `full` | mocked rawpy preview/half/full/损坏测试 |

Qt 或 Pillow 失败统一进入 decode failure。RAW 顺序固定为 embedded → half → full。取消检查位于原生
decode 前后、规范化前和发布前；numpy/Pillow 数据在跨线程前复制进独立 QImage。有效 profile 转 sRGB，
无 profile 沿用 sRGB 显示语义；透明 alpha 保留，EXIF orientation 只规范化一次。

上表是实现与自动化覆盖，不是平台 fallback 覆盖率。macOS/Windows/Linux packaged 的逐格式真实选择仍须
按 runbook 各采样至少 30 次。

## 4. 索引 revision 与兼容迁移

schema version 从 2 升为 3，`assets` 新增：

- `source_mtime_ns INTEGER DEFAULT 0`
- `image_orientation INTEGER DEFAULT 1`

scanner、metadata provider、row mapper、geometry query、legacy repository metadata、DTO presentation 和
prefetch descriptor 已全链路传递。中性 revision 首选 `size_bytes + source_mtime_ns`；旧库迁移后值为 0，
在首次重扫前使用 `size_bytes + index_revision`。`index_revision` 可能被缩略图状态推进，因此只作为旧库
兼容，不是长期源像素 revision。点击和 hover 身份构造有禁止 `Path.stat()` 的自动化测试。

## 5. 人脸标签坐标漂移修正

人脸 annotation 保持原图坐标，而 Phase 2 viewer texture 是较小的 LOD surface。原
`image_rect_to_viewport()` 把 annotation 的原图像素直接交给已按 texture 尺寸配置的 zoom controller，
只有全图纹理时碰巧正确。现在正向矩形/点映射会先按 `surface_size / annotation_image_size` 转为实际
texture 坐标，再进入 rotate/flip/zoom/pan；`viewport_to_image()` 做对称逆变换。

回归用例覆盖 `6000×4000` 原图 face box 映射到 `600×400` surface，并验证矩形位置、尺寸及反向拾取。
因此显示人脸状态不会因 viewport LOD 本身产生比例漂移。straighten/perspective 后让 overlay 轮廓逐像素
贴合 shader homography 不属于本次报告已证明的范围。

## 6. Profiler 事件

Phase 2 新增：`level_selected`、`backend_selected`、`decode_fallback`、`surface_ready`。仅记录 asset id、
suffix、尺寸、层级、backend/fallback 与原因，不记录绝对路径。示例（数值仅示意）：

```json
{"stage":"level_selected","generation":12,"details":{"asset_id":"as_42","suffix":".jpg","decode_level":2048,"viewport_width":1512,"viewport_height":982,"reason":"initial"}}
{"stage":"backend_selected","generation":12,"details":{"asset_id":"as_42","suffix":".jpg","backend":"qt","decode_level":2048}}
{"stage":"surface_ready","generation":12,"details":{"asset_id":"as_42","width":2048,"height":1365,"decode_level":2048}}
```

采集字段和逐格式步骤已同步到
[`DETAIL_OPEN_BENCHMARK_RUNBOOK.md`](../DETAIL_OPEN_BENCHMARK_RUNBOOK.md)。原始输出继续放在被忽略的
`benchmark-output/`。

## 7. 自动化验证实录

Phase 1 固定组合在 Phase 2 上回归：

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

结果：`130 passed in 1.24s`。

Phase 2 backend、索引迁移、scanner 与 Gallery：

```bash
.venv/bin/python -m pytest -q \
  tests/ui/test_gallery_grid_view.py \
  tests/gui/test_detail_decode_backend.py \
  tests/cache/test_index_store_features.py \
  tests/application/test_library_probe.py \
  tests/test_scanner_adapter.py
```

结果：`105 passed in 10.50s`。

现有 RAW/image-loader、source identity DTO 与人脸 overlay：

```bash
.venv/bin/python -m pytest -q \
  tests/test_utils_image_loader.py \
  tests/test_raw_support.py \
  tests/gui/viewmodels/test_gallery_list_model_adapter.py \
  tests/ui/widgets/test_gl_image_viewer_post_load_signal.py \
  tests/ui/widgets/test_face_name_overlay.py
```

结果：`114 passed in 1.27s`。

最终还必须执行 `python -m compileall -q src/iPhoto` 与 `git diff --check`；本 handoff 不把中间结果冒充
最终工作树验证，最终结果以交付消息为准。

## 8. 已证明与未证明

自动化已证明：

- 首屏在已索引尺寸、普通 fit viewport 下选择有界 LOD，而非默认传感器全尺寸。
- crop/rotation/straighten/perspective 进入 LOD 选择，极端需求显式标记 full。
- Qt surface 的透明 alpha、EXIF orientation、RGBA8888/sRGB 和 detached 所有权。
- RAW preview/half/full 顺序、损坏失败和 cancellation checkpoints。
- 同 asset 同 level decoder ≤1；不同 level 不错误复用；latest generation/adjustments 更新。
- A→B 绕行、stale presented=0、无效 viewport 延迟、shutdown 与索引迁移兼容。
- 原图人脸坐标在降采样 Detail surface 上不产生比例漂移。

尚未证明：

- 没有运行三平台 packaged、每格式至少 30 次采样，因而没有真实 backend/fallback 覆盖率。
- 没有证明普通照片 ≤150ms、重型照片 ≤300ms、热重访 ≤80ms，也没有关闭最终 SLO。
- 没有磁盘/mapped surface cache、GPU residency、主动 zoom 原子 LOD 升级或 Detail/Edit 共用会话。
- 没有证明 straighten/perspective shader homography 下的人脸轮廓逐像素贴合。

## 9. 唯一接手方向：Phase 3

下一阶段只从 versioned `DecodedSurface + DetailDecodeKey` 边界继续：实现中性 surface store、mapped CPU
LRU 与 GPU residency，并建立当前/前一/后一 texture ring、upload/cache 命中观测和主动 zoom 原子 LOD
升级。不得把 Detail/Edit 共用会话提前伪装成 Phase 3 cache 完成，也不得把本阶段尚未采集的 packaged
SLO 写成已达标。

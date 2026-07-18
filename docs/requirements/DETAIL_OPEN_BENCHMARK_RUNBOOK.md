# Gallery → Detail benchmark runbook

正式数据必须来自 packaged 构建、本地图形后端和专用测试图库。每个平台、媒体等级及冷/热缓存组合至少采集 30 次；原始输出放在被忽略的 `benchmark-output/`，不得提交。

## 采集

启动应用前指定结构化输出文件：

```bash
IPHOTO_DETAIL_PROFILE=1 \
IPHOTO_DETAIL_PROFILE_PATH=benchmark-output/macos-metal/candidate/hot/events.jsonl \
<packaged-app-command>
```

依次点击固定媒体矩阵。冷 Detail cache 组每次重启应用；系统冷缓存只能在平台允许且不会影响其他用户任务时单独执行。日志不包含绝对媒体路径，只含媒体类型、后缀、generation 与时间。

Phase 2 still 采样必须同时保留以下事件，并按 `asset_id + generation` 关联：

- `level_selected`：检查 `suffix`、`decode_level`、物理 viewport、请求原因；`full` 必须有可解释的
  极端 crop、未知旧库尺寸或 texture-limit 原因。
- `backend_selected`：记录格式实际使用 `qt` 或 `rawpy`，不得从扩展名推断 backend。
- `decode_fallback`：统计 `pillow`、`qt_full_scale`、`half`、`full`、`full_level`；没有 fallback
  的样本也要计入分母。
- `surface_ready`：核对最终 detached surface 的宽高与 decode level。
- `presented`：仍是 click-to-present 的终点；stale generation 不得产生该事件。

Phase 3 追加四组互斥采样，每组、每格式、每平台至少 30 次：

- cold decode：清空 Detail surface/GPU cache 后打开，必须出现 `surface_cache_miss` 和
  `backend_selected`。
- hot disk/mapped surface：保留 `<library>/.iPhoto/cache/detail-surfaces/v1`，重启或清空内存层；
  必须出现 tier=`disk`/`memory` 的 `surface_cache_hit`。
- hot GPU：不离开当前图库并重访 current/previous/next；必须出现 `gpu_cache_hit`，同 key 不得新增
  `gpu_upload`。
- sidecar-only：只修改 `.ipo` 后重访；source revision 不变时 `backend_selected` 增量为 0，已有 GPU
  texture 的 `gpu_upload` 增量也为 0。

同时保留 `surface_cache_write/corrupt`、`gpu_cache_miss/upload/evict`、
`lod_upgrade_requested/presented` 和 `context_rebuild`。主动 zoom 用独立 generation 统计；LOD 替换前的旧层
继续显示，但只有新纹理实际 draw 后才能记录 `lod_upgrade_presented`。`tools/detail_benchmark.py` schema 2
兼容旧 `image_presented` 与生产 `presented`，并输出 cache tier、decode、GPU upload/hit 计数。

JPEG、PNG、HEIC、RAW 每种格式、每个平台至少采集 30 次。汇总表至少包含样本数、level 分布、backend
分布、fallback 次数/比例、surface 尺寸和 click-to-present P50/P95。插件或系统能力不同导致的 backend
差异必须原样记录，不能用 source/offscreen 单测结果代替 packaged 数据。

## 汇总与对比

```bash
.venv/bin/python tools/detail_benchmark.py summarize \
  benchmark-output/macos-metal/candidate/hot/events.jsonl \
  --output benchmark-output/macos-metal/candidate/hot/summary.json

.venv/bin/python tools/detail_benchmark.py compare \
  --baseline benchmark-output/macos-metal/baseline/hot/summary.json \
  --candidate benchmark-output/macos-metal/candidate/hot/summary.json \
  --output benchmark-output/macos-metal/comparison-hot.json
```

正式检查 `click_to_image`、`click_to_video_first_frame` 与合并的 `click_to_final_media`。P95 使用 nearest-rank。取消事务单独计数，不混入完成延迟；快速切换场景必须同时检查旧 generation 未产生最终呈现事件。

## 平台矩阵

- macOS Metal；macOS OpenGL 诊断模式。
- Windows OpenGL QRhi。
- Linux OpenGL QRhi。
- JPEG/PNG/HEIC/RAW，MP4/MOV/MKV，H.264/H.265，4K/HDR、旋转、trim、adjusted video 与 Live Photo。

source/offscreen 数据只作回归趋势，不作为正式性能证据。门槛采用实施计划中的普通/重型媒体绝对 P95，并要求 P50 至少改善 40%、P95 至少改善 25%。

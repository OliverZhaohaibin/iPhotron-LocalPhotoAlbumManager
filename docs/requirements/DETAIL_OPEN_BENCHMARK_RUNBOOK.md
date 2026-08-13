# Gallery → Detail benchmark runbook

正式数据必须来自 packaged 构建、本地图形后端和专用测试图库。每个平台、媒体等级及冷/热缓存组合至少采集 30 次；原始输出放在被忽略的 `benchmark-output/`，不得提交。

## 采集

### 自动 packaged harness（Phase 5）

先复制示例 manifest，并为每个平台补齐专用 JPEG/PNG/HEIC/RAW、MP4/MOV/MKV、H.264/H.265、4K/HDR
样本。manifest 只保存相对路径与非隐私 category；驱动会把列出的媒体和同名 `.ipo` 复制到临时图库，退出后
删除临时副本，不修改源测试图库。

```bash
.venv/bin/python tools/run_detail_packaged_benchmark.py \
  --app dist/main.app \
  --library tools/testbase \
  --manifest docs/requirements/gallery-detail-gpu-first/DETAIL_BENCHMARK_MANIFEST.example.json \
  --output-dir benchmark-output/macos-metal/candidate/cold \
  --baseline-summary benchmark-output/macos-metal/baseline/cold/summary.json \
  --commit <candidate-commit> \
  --build-label candidate \
  --repetitions 30
```

packaged 应用通过私有 `IPHOTO_DETAIL_BENCHMARK_PLAN` 启动 harness，并沿真实
`GalleryViewModel.open_row()` 路径发出事务。`runtime.json` 必须显示目标 QRhi backend；`offscreen`、`minimal`、
software/null renderer 会让本次运行直接无效。退出码非 0 或 `validation.json passed=false` 均不得进入正式汇总。
传入 `--baseline-summary` 时还会生成 `comparison.json` 与 `comparison_validation.json`；后者逐 category 强制
P50 改善至少 40%、P95 改善至少 25%。

manifest 的 `cache_group` 支持 `cold`、`disk`、`memory`、`gpu`、`preserve`；`scenario` 支持 `open`、
`sidecar-only`、`fullscreen`、`lod`、`memory-pressure`、`edit-cancel`、`edit-done`、`rapid-switch`。快速切换使用
`switch_paths` 描述 B→A，所有附加媒体同样只复制到临时图库。cold 会在每次样本前清除临时图库的 Detail
surface 与 GPU residency；sidecar/edit-done 也只修改临时副本。
`runtime.json` 记录 app/version、显式 `--commit`、baseline/candidate 标签、OS/Qt、实际 graphics backend 及
去路径化样本 category/suffix；`summary.json` 记录 backend 与 fallback 分布。

baseline 必须在只加入同一 benchmark instrumentation、尚未实施 Phase 5 render/backend 清理的提交构建；candidate
使用最终代码。两者必须使用相同机器、packaged 配置、manifest、重复次数和缓存场景。

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
- `backend_selected`：记录格式实际使用 `qt`、`wic` 或 `rawpy` 以及 worker 解码耗时，
  不得从扩展名推断 backend。Windows JPEG 路由变更必须在同一台机器、同一批样本上与变更前基线比较；
  `jpeg-cold` 和 `jpeg-hot-gpu` 的 `click_to_image` P50/P95 均不得回退，并继续满足 150/80ms 绝对门槛。
- `decode_fallback`：统计 `pillow`、`qt_full_scale`、`half`、`full`、`full_level`；没有 fallback
  的样本也要计入分母。
- `surface_ready`：核对最终 detached surface 的宽高与 decode level。
- `presented`：仍是 click-to-present 的终点；stale generation 不得产生该事件。
- RAW 冷解码同时保留 `raw_probe`、`raw_candidate_selected`、`raw_thumb_decode`、
  `raw_postprocess`、`raw_surface_convert` 和 `color_stats`。未知几何必须先由 `raw_probe` 修复后再产生
  `level_selected`；每个 cache miss 只能选择 embedded、half、full 之一，不得在同一请求中连续执行
  half 和 full。事件只记录阶段耗时、候选和尺寸，不记录媒体路径。

开发机可用仓库 NEF 运行非门禁分段微基准；结果只用于定位 codec 阶段，不能替代 packaged SLO：

```bash
.venv/bin/python tools/benchmark_raw_detail_decode.py \
  tools/testbase/15/DSC_0291.NEF
```

Phase 3 追加四组互斥采样，每组、每格式、每平台至少 30 次：

- cold decode：清空 Detail surface/GPU cache 后打开，必须出现 `surface_cache_miss` 和
  `backend_selected`。
- hot disk/mapped surface：保留 `<library>/.iPhoto/cache/detail-surfaces/v3` 及其 `index.sqlite3`，重启或清空内存层；
  必须出现 tier=`disk`/`memory` 的 `surface_cache_hit`。
- hot GPU：不离开当前图库并重访 current/previous/next；必须出现 `gpu_cache_hit`，同 key 不得新增
  `gpu_upload`。
- sidecar-only：只修改 `.ipo` 后重访；source revision 不变时 `backend_selected` 增量为 0，已有 GPU
  texture 的 `gpu_upload` 增量也为 0。

同时保留 `surface_cache_write/corrupt`、`gpu_cache_miss/upload/evict`、
`lod_upgrade_requested/presented` 和 `context_rebuild`。主动 zoom 用独立 generation 统计；LOD 替换前的旧层
继续显示，但只有新纹理实际 draw 后才能记录 `lod_upgrade_presented`。`tools/detail_benchmark.py` schema 2
兼容旧 `image_presented` 与生产 `presented`，并输出 cache tier、decode、GPU upload/hit 计数。

Phase 4 增加共享 render session 采样。每张静态照片在已完成首次 Detail 呈现后，分别执行至少 30 次：

- Detail → Edit → Detail Cancel：应出现 `render_session_acquired`、`edit_state_updated`、
  `render_session_released(committed=false)`；区间内 `decode_started/backend_selected/gpu_upload` 增量均为 0。
- Detail → Edit → Detail Done：写入 sidecar 后应出现 `render_session_released(committed=true)`，不得通过
  `MediaRestoreRequest` 重放静态 Detail；同 source texture key 保持不变。
- Edit fullscreen enter/exit：只允许 viewport/LOD 事件；不得出现同步 source load、CPU preview session 或
  以 `Path` 为 key 的新 GPU upload。
- Edit crop/rotate/perspective/zoom：若需要更高 LOD，应记录 `lod_upgrade_requested/presented`，旧层保持显示，
  stale/failed upgrade 不得替换 current texture。

同时保留 `render_session_created/acquired/released`、`edit_state_updated` 和 `surface_owner_*` 诊断事件。ColorStats 从 surface cache v3 header
复用；同 source revision 跨 LOD 的统计计算次数必须为 1。packaged 日志只能证明实际运行结果，不能以
session 单测或 shader compile 代替三平台数据。

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

.venv/bin/python tools/detail_benchmark.py validate \
  benchmark-output/macos-metal/candidate/hot/summary.json \
  --minimum-samples 30 \
  --output benchmark-output/macos-metal/candidate/hot/validation.json
```

正式检查 `click_to_image`、`click_to_video_first_frame` 与合并的 `click_to_final_media`。P95 使用 nearest-rank。取消事务单独计数，不混入完成延迟；快速切换场景必须同时检查旧 generation 未产生最终呈现事件。

## 平台矩阵

- macOS Metal；macOS OpenGL 诊断模式。
- Windows OpenGL QRhi。
- Linux OpenGL QRhi。
- JPEG/PNG/HEIC/RAW，MP4/MOV/MKV，H.264/H.265，4K/HDR、旋转、trim、adjusted video 与 Live Photo。

source/offscreen 数据只作回归趋势，不作为正式性能证据。门槛采用实施计划中的普通/重型媒体绝对 P95，并要求 P50 至少改善 40%、P95 至少改善 25%。

任何失败组必须保留原 events/summary/validation，按 queue、surface cache、decode、GPU upload、draw 定位；修正后
先重跑失败组，再完整重跑该平台矩阵。不得用删除失败样本、合并取消事务或降低重复次数的方式通过门槛。

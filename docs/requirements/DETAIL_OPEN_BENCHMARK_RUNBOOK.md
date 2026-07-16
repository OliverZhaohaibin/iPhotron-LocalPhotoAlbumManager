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

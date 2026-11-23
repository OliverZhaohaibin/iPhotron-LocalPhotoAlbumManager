# Bug report：Windows filmstrip 滚轮切换媒体期间访问冲突，随后无响应

- **状态：已确认故障，根因未定位；本轮仅记录和分析，不修改实现。**
- **建议优先级：P1。** 浏览媒体时进程发生致命异常，之后窗口无响应，需要强制结束；未发现足以证明原始媒体或持久化状态损坏的证据。
- **研究价值：高。** 本次包含同一进程的 Python fatal exception、Windows Application Error、APPCRASH、主线程调用链及无响应采样，能够支持原生崩溃调查，明显强于只有“程序卡住”的现象描述。
- **样本范围：一次用户复现。** 尚未独立复现，复现概率、是否必须包含 Live Photo、是否必须先进入全屏均未知。

## 1. 用户现象与预期

用户反馈（2026-09-20 澄清）：通过采集器 `-Scenario Fullscreen -FullscreenOverscan` 启动时，已无肉眼可见闪烁，裁剪偏移也已解决；同一解释器和提交直接从 PyCharm Run 启动仍闪烁。本文采集包来自前一种启动配置，用户随后在 **filmstrip 上滚轮切换媒体**时捕捉到程序无响应。该视觉改善结论仅适用于明确启用 overscan 的这次运行，不能推广为当时的默认启动行为。

预期：滚轮切换当前媒体时，filmstrip 选中项、居中位置和 Detail 内容正常更新；过期加载可以取消，GUI 应持续响应。

实际：连续切换期间出现原生访问冲突；GUI 心跳和业务日志停止，Windows 随后检测到窗口无响应，用户通过采集器强制结束进程。

**本问题应单独建档，不将本次采集启动配置下已获用户确认改善的全屏闪烁/偏移重新判为失败。** 故障前最后几条 viewer 状态是 `fullscreen=false, qt_fullscreen=false`，尺寸为 899×421 逻辑像素，因此不能把这次异常描述为“发生在全屏缩放过程中”。

## 2. 环境与证据完整性

| 项目 | 记录值 |
|---|---|
| 采集包 | `iPhoto-windows-scan-playback-20260919-204025.zip` |
| 运行源码提交 | `14dcd388d201f8209da5c968c02cc175421bfe5f` |
| 源码脏状态 | `source_git_dirty=null`，不能据此证明工作树干净 |
| 启动方式 | source；Python 3.12.6，WER 文件版本为 `3.12.6150.1013` |
| Qt / PySide | 6.10.1 / 6.10.1 |
| 系统 | Windows 11 Pro，10.0.26200，64 位 |
| 设备 | Dell Precision 5680，约 64 GB 内存 |
| 实际渲染设备 | Intel Iris Xe，OpenGL 4.6 CompatibilityProfile |
| Intel 驱动 | `32.0.101.7088` |
| 其他枚举 GPU | NVIDIA RTX 4000 Ada Laptop，未据此认定其承担本次渲染 |
| 显示 | 1536×960 逻辑像素，DPR 2.5，对应 3840×2400 物理像素 |
| 采集器 | version 3，scenario `Fullscreen` |
| GUI 进程 | PID **30072**，Windows 错误事件中的十六进制 PID **0x7578** 与之完全相同 |

ZIP SHA-256：

```text
855bbe7136be128ba38beed5dbe98a723eebf984a5141751d9c5af8dd652c646
```

已经逐一校验 `manifest.json` 列出的 **9 个文件**：字节数和 SHA-256 全部吻合。ZIP 包含完整 manifest 和 Windows 事件日志。本次 `runtime_diagnostics_started`、生命周期 marker、性能表及 Application Error 的 PID 一致；上一轮“采到了 Python 启动器”的限制不适用于本包。

主要证据文件：

| 文件 | 用途与锚点 |
|---|---|
| `runtime_stacks.log` | 617 行；第 566 行包含 fatal exception；第 568–617 行为随后输出的线程栈 |
| `windows_application_events.json` | Application Error / Event 1000 的 Message 位于第 56 行；APPCRASH / Event 1001 的 Message 位于第 49 行 |
| `detail_events.jsonl` | 1749 条；第 1730–1749 行为最后一次媒体选择、路由、解码调度与 cache miss |
| `stderr.log` | 第 10213 行是最后一条 GUI heartbeat；第 10234–10239 行为末尾 FFmpeg/HEVC 硬件解码探测及 still cache miss |
| `process_metrics.csv` | 103 条数据；第 94–104 行连续记录 `is_hung=1` |
| `reproduction_markers.jsonl` | 记录启动、采集场景、强制结束和进程退出；没有用户按 R 的 `problem_reproduced` marker |

以上行号按包内原文件计数，不按 JSON 美化重排后的文件计数。报告不复制用户媒体路径或原始照片。

## 3. 时间线

以下时间均为 **2026-09-19，UTC+02:00**；原始 marker / Windows Event 的 UTC 时间加两小时。Windows 事件时间是事件记录时间，不等于精确的 CPU 故障指令执行时间。

| 时间 | 事件及意义 |
|---|---|
| 20:40:28.870 | 应用启用 runtime diagnostics，PID 30072。 |
| 20:40:29.447 | 采集器记录应用启动，PID 30072。 |
| 20:41:00.894–20:41:43.846 | 5 次 `fullscreen_composition_overscan(applied=true)`；同一 native ID 133448，目标和实际尺寸均为 1536×961，`qt_fullscreen=false`。证明候选配置确实运行，但肉眼效果以用户反馈为准。 |
| 20:41:53.817–20:41:59.215 | 最后约 5.4 秒内记录 10 次选择请求：generation 35–44，row 30→31→33→35→36→37→38→39→40→41。 |
| 20:41:54.147 起 | 其中 generation 36–44 均路由为 `live_motion`；同一过程中调度 `.heic` still 和切换 `.mov` motion。 |
| 20:41:57.771 | 最后一条 `presented`，generation 41，`live_motion`。 |
| 20:41:59.215 | 最后一次 `click_received`，generation 44，row 41。事件名是统一入口的名称，不能用它否定用户的滚轮触发描述。 |
| 20:41:59.392–20:41:59.532 | generation 44 路由为 Live Photo motion，设置 MOV source，并调度 HEIC / LOD 2048；最终 asset ID 为 `as_936e84ddd00e760d9eacd0a26c63ac48`。 |
| 20:42:00.402 | 最后一条 GUI heartbeat：#82，current row 41。 |
| 20:42:00.553 | 最后一条 Detail 事件：generation 44 的 `surface_cache_miss`。 |
| **20:42:00.701** | **Windows Application Error / 1000：PID 0x7578，`python312.dll`，`0xc0000005`。** |
| **20:42:05.861** | 性能采集首次记录 `is_hung=1`。随后 11 个连续采样均为 1，最后一个在 20:42:16.254。 |
| 20:42:15.493 | Windows Error Reporting / 1001 记录 APPCRASH，Report ID 与上面的 Event 1000 相同。 |
| 20:42:16.626 | `collector_forced_stop`；这是采集器强制结束，不是程序自行恢复或正常关闭。 |
| 20:42:17.081 | 采集器观察到进程退出。 |

时间线支持：**先发生访问冲突，再表现为窗口无响应。** 不能仅按 hang/deadlock 处理，也不能把最终强制结束误当成异常的起因。

## 4. 最强证据：原生访问冲突与现场调用链

### 4.1 两个独立记录来源指向同一进程

`runtime_stacks.log:566`：

```text
Windows fatal exception: access violation
```

Windows Application Error 的匹配字段：

```text
Faulting application: python.exe
Faulting module: python312.dll
Exception code: 0xc0000005
Fault offset: 0x000000000027ac90
Faulting process id: 0x7578  (= 30072)
Report Id: 7204eeb3-9277-4edc-afbc-7afc52bb879b
```

后续 APPCRASH 具有相同 Report ID、模块、异常码和偏移。这足以确认本进程的原生异常，**不能据模块名进一步认定是 CPython 自身 bug**；扩展模块、FFI 或其他原生组件造成的内存破坏也可能最终在解释器内暴露。

### 4.2 fatal exception 后输出的主线程栈

主线程 `0x00001b1c` 的调用链，从媒体选择入口到当时最上层 Python 帧：

```text
playback_coordinator._execute_pending_play:940
  -> _dispatch_play_row:970
  -> DetailViewModel.show_row:170 / _request_selection_row:181
  -> MediaSelectionSession.set_current_row:128 / _publish_snapshot:337
  -> DetailViewModel._handle_selection_changed:520
  -> _publish_user_selection:220 / _refresh_presentation:428
  -> playback_coordinator._handle_presentation_changed:1045
  -> _select_filmstrip_row:2164
  -> FilmstripView.select_index_for_centering:525
  -> AssetDelegate.sizeHint:45
  -> SpacerProxyModel.data:161
```

核对记录提交的源码：

- `filmstrip_view.py:525` 调用 `selection_model.setCurrentIndex(..., ClearAndSelect)`。
- `asset_delegate.py:45` 查询 `index.data(Roles.IS_SPACER)`。
- `spacer_proxy_model.py:161` 将查询转发为 `source.data(source_index, role)`。

这将调查范围收敛到**媒体选择广播、Qt selection/layout/sizeHint 与代理模型取数的同步调用链**。但该文件没有可靠标注哪个线程执行了故障指令；不能把“主线程最上层可见 Python 帧”直接等同于原生 faulting frame，也不能据此认定 line 161 本身有 bug。

fatal exception 行前，周期栈在 `header_controller._apply_header_text:128` 的 `_timestamp_label.show()` 附近被打断；后面紧接异常线程转储。两段输出有交错，不能把 header 和 filmstrip 栈硬拼成一条调用链。

### 4.3 同时存在的 Windows 解码与多媒体活动

- 后台线程 `0x00005aa4` 停留在 `detail_decode_windows._copy_rgba:449`，即 FFI 调用 `IWICBitmapSource.CopyPixels`；上层经过 Windows decoder、Detail surface cache 和 PlayerView worker。
- 最后一段 stderr 显示 FFmpeg 正创建 HEVC PlaybackEngine，并检查 `d3d11va` 硬件解码上下文。
- `d3d11va` 在此是**视频硬解码能力探测**，不代表应用改用了 DX 渲染；本包记录的媒体渲染后端仍为 OpenGL。
- 较早的一次周期栈出现 `VideoArea.stop:1048 -> QMediaPlayer.setSource(QUrl())`；后续仍有大量正常交互，因此不能把那次采样当作最终挂死位置。

这些是需要联合调查的原生边界，尚不足以在 WIC、Qt/PySide 模型回调和 Qt Multimedia 之间确定责任方。

## 5. 无响应与资源数据的解读

最后一次 heartbeat 中：current row 41、collection revision 2、cached rows 512、总行数 2452，`pending_moves/pending_row_loads/pending_scan_batches/pending_scan_rows/pending_window_requests` 均为 0，`pending_scan_refresh=false`。

故障前最后一轮 filmstrip 发布显示 13 个可见 full thumbnails、0 个 placeholder，guard 52/52 已驻留，thumbnail generation/prefetch 的 active 和 queued 均为 0。因此，**没有直接证据表明这次无响应是可见缩略图队列积压或等待扫描发布造成的**。这并不排除模型对象生命周期、重入或已损坏原生对象的问题。

进程指标：

- CPU 累计时间从 20:42:00.639 到 20:42:16.254 均为 `106.281 s`，基本停止进展。
- `is_hung=1` 的连续采样覆盖约 10.39 秒，期间没有恢复记录。
- 故障附近固定为 192 个 OS 线程、2603 个 handle；整个采集的峰值分别为 253 和 2808。
- Private memory 峰值约 3585.6 MB，working set 峰值约 2822.0 MB；不能由这些数值单独推出内存泄漏、OOM 或 GPU 显存耗尽。

CPU 停止和窗口无响应，与访问冲突后的故障处理/进程停滞相容；本包不能证明是独立于崩溃之外的锁死。

Windows 事件日志还包含多条 LiveKernelEvent，但其中附件命名日期覆盖 2025 年和其他历史日期，并有故障前就已出现的批量 WER 记录。**不得仅因这些记录的写入时间接近本次采集，就认定本次发生了显卡 watchdog reset 或硬件故障。** 本报告用于确认本次故障的是 PID 和 Report ID 匹配的 Application Error / APPCRASH。

## 6. 调查方向与证据缺口

| 方向 | 当前依据 | 尚不能下的结论 |
|---|---|---|
| Qt/PySide 选择与代理模型生命周期、重入 | fatal 转储时主线程经过 setCurrentIndex → sizeHint → source.data；与用户滚轮切换吻合 | 未证明 QModelIndex 失效、QObject 被提前销毁或跨线程访问 |
| WIC/HEIC 原生复制与取消过程 | 同时存在 CopyPixels FFI 调用和连续新请求/取消 | 未证明缓冲区越界、COM 生命周期错误，也不能将线程直接归属到某个 generation |
| Live Photo 视频源切换、播放器 teardown/startup | 最后多个请求为 live_motion，MOV source 频繁替换；有 FFmpeg HEVC 初始化 | 末尾日志不等于崩溃栈，不能把 D3D11VA 探测认定为故障点 |
| 全屏兼容路径关联性 | 本会话运行过 overscan；最后 viewer 状态已回到 windowed | 无开关对照，不能证明或排除历史全屏切换的间接影响；不能据此回退已获用户确认的视觉修复 |

关键缺口是**同一次访问冲突的 native crash dump 和符号栈**。包内没有 `.dmp`；缺少 faulting thread 的寄存器、访问地址、读/写/执行类型、所有原生模块栈及加载模块版本。因此本包足以立案、缩小范围、设计复现，但不足以确定最终修复点。

周期 Python 栈约每 5 秒一次，没有独立墙钟时间；第 18 次周期输出与 fatal 转储发生交错。不要为每一行栈赋予毫秒级故障时间，也不要因异常后没有继续周期输出就另行认定 watchdog 死锁。

`generation` 来自多条生产链路，本包中相同数字可关联不同阶段/资产。后续分析应联合时间、stage、asset_id、媒体类型和 renderer，不能只按 generation 数字合并事件。日志里出现 `input_kind=scrollbar` 也不排斥滚轮输入，程序化居中会产生滚动条事件。

## 7. 后续复现与验收建议（本轮不执行修复）

1. 优先保存或重新采集同一 `0xc0000005` 的完整 native dump，关联上述 Report ID；在强制结束前保留现场，解析故障线程、访问地址及 `python312.dll+0x27ac90` 的符号。
2. 以本次媒体序列为起点，在 filmstrip 连续向同一方向滚轮切换；重点包含 HEIC＋MOV 的 Live Photo，同时记录方向、速率与当前行。不应将原始用户媒体自动纳入公共 bug 附件。
3. 分别比较纯静态照片与 Live Photo 自动播放、只在窗口模式启动与先全屏再退出、相同素材慢速与连续快速切换。先确定触发条件，再讨论实现修改。
4. 调查 selection-model 更新、代理模型 reset/removal、delegate 查询期间的对象存活和线程归属；并关联旧解码请求取消、WIC buffer/COM 生命周期、旧视频帧释放和新视频源初始化。
5. 后续修复验收应覆盖连续切换、切换中返回 Gallery/关闭库、没有新的访问冲突或持续 hung 状态、selection 与呈现内容一致；同时回归已改善的全屏闪烁与裁剪居中。

**本轮交付范围：仅新增此 bug report。没有修改运行代码、采集器或测试，也没有尝试修复该异常。**

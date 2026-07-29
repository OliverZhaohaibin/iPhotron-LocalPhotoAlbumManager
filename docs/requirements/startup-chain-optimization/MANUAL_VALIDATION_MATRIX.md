# 启动链路人工实机验收包

更新日期：2026-07-27

> 2026-07-22：用户报告已完成多平台人工 smoke，未发现明显 bug。由于未附
> artifact、build fingerprint 和逐项报告，该记录为
> `user_reported_multi_platform_smoke_pass`，下列正式矩阵状态保持不变。

## 通用要求

每个平台使用独立、可丢弃的图库和 settings。每个 generation 必须恰好一个 terminal：`startup.completed`、`startup.degraded`、`startup.failed`、`startup.cancelled`。报告不得包含真实用户路径；原始日志先脱敏再共享。

通用通过阈值：

- `show -> interactive/degraded <= 2000 ms`
- 每个命名 GUI job 与 interactive 后最大 GUI stall `<= 100 ms`
- 不可用存储约 3 秒进入可重试状态
- 无遗留 helper、thread、timer、watcher、input guard 或信号连接
- Gallery/首缩略图 P50 相对固定 baseline 改善至少 30%，P95 不退化

## macOS Apple Silicon / Intel

Metal 与 OpenGL 分开采集：

```bash
.venv/bin/python tools/startup_benchmark.py collect \
  --revision CURRENT_SHA --scenario local-ssd-indexed \
  --library /absolute/dedicated/library --confirm-dedicated-library \
  --build-manifest /absolute/path/to/build-manifest.json \
  --runtime packaged --qt-backend cocoa --graphics-backend metal \
  --cache-state hot --samples 30 \
  --output-dir benchmark-output/macos-ARCH/candidate/metal/hot \
  -- /absolute/iPhotron.app/Contents/MacOS/iPhotron
```

把 `metal` 替换为 `opengl` 采集兼容路径。验证库不可用、SQLite lock/corrupt、probe 超时、重试、启动中关窗、切库和首次地图安装。Apple Silicon 本轮由工程任务执行；Intel 保持 `pending_manual_validation`。

## Windows

保持 Defender 开启，使用 Nuitka `.exe` 执行同一 `collect` 协议。至少覆盖：

- 本地 SSD indexed hot 30 次
- 断开的移动盘和盘符复用
- 延迟/离线 UNC 或 SMB
- SQLite lock/corrupt 与 helper timeout
- 启动中关闭、连续重试、切库
- 单顶层窗口；结束后无 probe/helper 子进程

收集 Windows 版本、CPU/架构、Qt backend、构建参数、Defender 状态、盘类型和事件阈值。当前状态：`pending_manual_validation`。

## Linux

交付物使用 AppImage，分别在 XCB、原生 Wayland、Wayland 无 XWayland 环境执行。至少覆盖：

- 本地 SSD indexed hot 30 次
- AppImage 冷/热启动（只有受控 OS cache eviction 才算正式 cold）
- 网络 mount、卸载/不可用 mount
- helper timeout、关窗、重试、切库
- Wayland 无 XWayland 时主程序可用、地图明确降级
- XCB/GLX 地图 helper 崩溃不结束主进程

记录发行版、kernel、桌面环境、Wayland/XCB、GPU/driver、AppImage runtime。当前状态：`pending_manual_validation`。

## Pets 审查修复人工矩阵

下列项目只保留当前主机和自动化无法证明的 packaged 权限、网络故障、真实升级
数据与交互体验。所有未附证据项一律为 `pending_manual_validation`。

| 场景 | 必测平台/输入 | 通过条件 | 状态 |
| --- | --- | --- | --- |
| 只读安装目录与首次模型落盘 | macOS Apple Silicon、macOS Intel、Windows `Program Files`、Linux AppImage | bundled root 保持只读；查找顺序为 override、用户 cache、bundled；缺失模型只写用户 cache；首次识别成功 | `pending_manual_validation` |
| 模型获取与自愈 | 在线下载、离线 bundled fallback、损坏 cache、代理失败、证书失败 | 在线文件 hash/size/shape 正确；离线可用 bundled；损坏 cache 可重取；代理/证书失败给出可操作提示且不污染 cache | `pending_manual_validation` |
| 固定 DINO Release 产物 | 使用固定 source commit 的受控构建环境和本仓库不可变 `pet-models-v1` 标签 | 生产 Torch Hub 路径已删除；仍须发布 `dinov2_vits14.pt`、记录 artifact/build manifest SHA、填写 Release HTTPS URL，并重新通过 hash/shape/packaging 门禁 | `pending_manual_validation` |
| 真实旧图库升级 | 含 name、cover、hidden、rejection、pet merge、跨类型 merge 的脱敏副本 | interactive 不被 backfill 阻塞；后台清空 pending/retry；durable state 不丢；失败资产明确显示 stale/source generation | `pending_manual_validation` |
| 真实照片识别 | 多宠照片、小目标 tile、People overlap、大狗+远处小猫、重叠 cat/dog | 类别、bbox、去重和 People 优先级符合契约；旧 source annotation 与 canonical identity 显示一致 | `pending_manual_validation` |
| Windows Live Photo 静态图方向 | Windows packaged，优先复测 `IMG_3684.HEIC` 的脱敏副本，并覆盖 iPhone Orientation 5/6/7/8 的 HEIC+MOV/JPEG+MOV | 静态图与动态视频视觉方向一致；静态图无二次 EXIF 旋转；JPEG 等 WIC 未预转正格式仍正确应用 EXIF；首次展示、Live Motion 返回静帧和连续切换均通过 | `user_verified_original_sample_pass / formal_artifact_evidence_pending` |
| 取消、切库与恢复 | 扫描中快速取消、连续切库、journal 各提交点进程 kill/restart | GUI 不阻塞；旧 worker 不写新库或发迟到事件；重启最终 `finalized`；无 redirect/detection/rejection/cover/group-cache split-brain | `pending_manual_validation` |
| 50k packaged 报告 | 上述四类平台各自正式产物，已有 50k 再新增 2 个 | 记录耗时、峰值 RAM、WAL 增量、取消延迟、退出线程与后台恢复；满足 `pets-scale-contract` 阈值 | `pending_manual_validation` |

每条结果必须记录以下字段，任一缺失都不能改为 pass：

```text
artifact_sha256:
build_manifest_path_and_sha256:
platform / arch / OS version:
model_sha256:
redacted_fixture_id_and_sha256:
redacted_log_path_and_sha256:
scenario:
observed_metrics:
result: pass | fail | pending_manual_validation
```

真实图库只允许使用脱敏 fixture；日志不得包含用户名、图库绝对路径、原图内容或
数据库中的用户文本。失败项应同时保留 operation ID、generation ID、journal 状态
和恢复后的最终状态。

## 故障采集

```bash
IPHOTO_STARTUP_PROFILE=1 \
IPHOTO_STARTUP_HANG_DIAG=1 \
IPHOTO_STARTUP_PROFILE_PATH=/safe/path/startup.jsonl \
/path/to/iPhotron
```

同时保存 stdout/stderr、退出码、是否超时、残留进程列表和复现步骤。不要上传图库、settings、数据库、用户名或完整绝对路径。

## 脱敏报告模板

```text
platform/arch:
os/runtime:
artifact revision:
build/dependency fingerprint:
scenario/backend/cache state:
sample count / eligible count:
P50/P95 metrics:
max GUI job/stall:
terminal event count:
helper/thread/watcher cleanup:
result: pass | fail | pending_manual_validation
failure code and redacted reproduction:
```

所有目标平台都为 `pass` 后，状态才能改为 `cross_platform_validated`，并把本需求目录移动到 `docs/finished/requirements`。

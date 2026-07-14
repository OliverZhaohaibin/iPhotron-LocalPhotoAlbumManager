# 启动链路人工实机验收包

更新日期：2026-07-14

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

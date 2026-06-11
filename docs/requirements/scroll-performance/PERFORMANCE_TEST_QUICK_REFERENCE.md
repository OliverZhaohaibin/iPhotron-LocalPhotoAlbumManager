# Gallery 滚动性能测试快速参考指南

> 本指南帮助后续开发者快速在各平台上执行和对比性能基准测试  
> 最后更新：2026-06-11

## 目录

- [快速开始](#快速开始)
- [Windows 平台](#windows-平台)
- [macOS 平台](#macos-平台)
- [Linux 平台](#linux-平台)
- [结果对比与分析](#结果对比与分析)
- [故障排除](#故障排除)

---

## 快速开始

### 一条命令启动测试

```powershell
# Windows PowerShell
cd D:\python_code\iPhoto\iPhotos
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

```bash
# macOS / Linux
cd ~/iPhoto/iPhotos
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

### 期望的输出

```
...                                           [100%]
=== 3 passed in X.XsX ===
```

**如果看不到这个，请参考 [故障排除](#故障排除) 部分**

---

## Windows 平台

### 环境准备

#### 1. 虚拟环境设置

```powershell
cd D:\python_code\iPhoto\iPhotos

# 首次使用：创建虚拟环境
python -m venv .venv

# 激活虚拟环境
.venv\Scripts\Activate.ps1

# 如果遇到执行策略错误，运行：
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

#### 2. 安装依赖

```powershell
# 安装项目及其测试依赖
pip install -e ".[test]"

# 验证关键依赖
python -c "import pytest; import PySide6; print('OK')"
```

### 测试执行

#### 选项 1：快速测试 (推荐用于首次运行)

```powershell
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
$env:IPHOTO_RUNTIME_LABEL="windows-development"
$env:IPHOTO_GALLERY_SCROLL_WHEEL_EVENTS="30"  # 仅 30 个事件，快速验证

pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

**执行时间**：~2 秒

#### 选项 2：标准测试 (完整基准)

```powershell
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
$env:IPHOTO_RUNTIME_LABEL="windows-development"

pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

**执行时间**：~8-10 秒

**输出示例**：
```
test_real_qt_gallery_scroll_benchmark[10000] PASSED         [33%]
test_real_qt_gallery_scroll_benchmark[100000] PASSED        [66%]
test_real_qt_gallery_scroll_benchmark[1000000] PASSED       [100%]
```

#### 选项 3：自定义报告位置

```powershell
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
$env:IPHOTO_GALLERY_SCROLL_REPORT_DIR="D:\my-benchmarks\2026-06-11"

pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v

# 查看报告
Get-ChildItem D:\my-benchmarks\2026-06-11
```

### 查看结果

#### 默认报告位置

```powershell
cd D:\tmp\iphoto-gallery-scroll-performance

# 列出所有报告
Get-ChildItem

# 查看最新的 10K 行测试结果
Get-Content gallery-scroll-windows-offscreen-10000.csv

# 打开 JSON 详细数据
notepad gallery-scroll-windows-offscreen-10000.json
```

#### CSV 字段说明

```
row_count                    # 被测试的行数
wheel_p95_ms               # 轮转事件处理的 P95 延迟
scroll_p95_ms              # 滚动操作的 P95 延迟
paint_p95_ms               # 绘制操作的 P95 延迟
frame_interval_p95_ms      # 帧间隔的 P95 延迟
input_catchup_ms           # 输入追平时间
final_micro_publish_ms     # Micro 缩略图发布延迟
micro_or_full_ratio        # Micro/Full 缩略图覆盖率 (1.0 = 100%)
placeholder_ratio          # Placeholder 覆盖率 (0.0 = 0%)
visible_before_warm        # Visible 是否优先 Warm (true)
*_violations               # 保护路径违反数 (应为 0)
```

#### 与基线对比

```powershell
# 基线数据位置
$baselinePath = "D:\python_code\iPhoto\iPhotos\docs\requirements\scroll-performance"

# 查看 Windows 验收报告中的基线表格
notepad "$baselinePath\GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md"

# 新测试结果
$latestPath = "D:\tmp\iphoto-gallery-scroll-performance"

# 对比 P95 帧间隔
Compare-Object (Import-Csv "$latestPath\gallery-scroll-windows-offscreen-10000.csv") `
               (Import-Csv "$baselinePath\...") -Property frame_interval_p95_ms
```

---

## macOS 平台

### 环境准备

```bash
# 虚拟环境
cd ~/iPhoto/iPhotos
python3 -m venv .venv
source .venv/bin/activate

# 依赖安装
pip install -e ".[test]"
```

### 测试执行

#### Offscreen 后端 (用于对标 Phase 2 报告)

```bash
export QT_QPA_PLATFORM=offscreen
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
export IPHOTO_RUNTIME_LABEL=development-offscreen

.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

**注意**："offscreen" 模拟了虚拟帧缓冲，与 Phase 2 原始测试相同。

#### 原生 macOS 后端 (Cocoa)

```bash
# 不设置 QT_QPA_PLATFORM，使用系统默认
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
export IPHOTO_RUNTIME_LABEL=development-cocoa

.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

**期望改进**：Paint 时间会显著降低 (GPU 加速受益)。

### 查看结果

```bash
# 报告位置
cd /tmp/iphoto-gallery-scroll-performance

# 查看特定结果
cat gallery-scroll-darwin-offscreen-10000.csv

# 与 Phase 2 基线对比
# (参考 GALLERY_SCROLL_PERFORMANCE_PHASE2_HANDOFF.md)
```

---

## Linux 平台

### 环境准备

#### Ubuntu/Debian

```bash
# 系统依赖 (PySide6 需要)
sudo apt-get install -y \
    python3.12 python3.12-venv \
    libgl1-mesa-glx libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-xfixes0 libxkbcommon-x11-0 libdbus-1-3

# 虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate

# 依赖
pip install -e ".[test]"
```

#### Fedora/RHEL

```bash
# 系统依赖
sudo dnf install -y python3.12-devel mesa-libGL-devel \
    libxcb-devel libxkbcommon-x11-devel dbus-devel

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

### 测试执行

#### XCB 后端 (X11)

```bash
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
export QT_QPA_PLATFORM=xcb
export IPHOTO_RUNTIME_LABEL=development-xcb

.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

#### Wayland 后端

```bash
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
export QT_QPA_PLATFORM=wayland
export IPHOTO_RUNTIME_LABEL=development-wayland

.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

#### Offscreen 后端 (CI 环境)

```bash
export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
export QT_QPA_PLATFORM=offscreen
export IPHOTO_RUNTIME_LABEL=ci-offscreen

.venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

### 查看结果

```bash
cd /tmp/iphoto-gallery-scroll-performance
ls -lh gallery-scroll-linux-*.{json,csv}

# 对比 XCB vs Wayland
diff <(cat gallery-scroll-linux-xcb-10000.csv) \
      <(cat gallery-scroll-linux-wayland-10000.csv)
```

---

## 结果对比与分析

### 基准对标表

| 平台 | Backend | Paint P95 | Frame Interval P95 | Micro Coverage | Status |
|:---|:---|---:|---:|:---|:---|
| **macOS** | offscreen | 1.836ms | 26.841ms | ✅ 100% | 基线 |
| **Windows** | offscreen | 3.830ms | 37.752ms | ✅ 100% | 已验收 |
| **Linux** | XCB | ? | ? | ? | 待验证 |
| **Linux** | Wayland | ? | ? | ? | 待验证 |

### 如何分析新结果

#### 性能回归检测

```powershell
# Windows 示例：对比新旧结果
$old = Import-Csv "D:\tmp\iphoto-gallery-scroll-performance\old\gallery-scroll-windows-offscreen-10000.csv"
$new = Import-Csv "D:\tmp\iphoto-gallery-scroll-performance\new\gallery-scroll-windows-offscreen-10000.csv"

# 对比关键指标
@("paint_p95_ms", "frame_interval_p95_ms") | ForEach-Object {
    $metric = $_
    $oldVal = [float]$old.$metric
    $newVal = [float]$new.$metric
    $delta = $newVal - $oldVal
    $pcnt = ([math]::Round($delta / $oldVal * 100, 2))
    Write-Host "$metric : $oldVal → $newVal (${delta:+}$delta, ${pcnt}%)"
}
```

#### 跨平台性能对比

```bash
# 将所有 CSV 合并为一个对比表
echo "Platform,Backend,Paint_P95,Frame_Interval_P95" > comparison.csv

for file in /tmp/iphoto-gallery-scroll-performance/**/*.csv; do
  platform=$(echo $file | grep -o 'darwin\|windows\|linux')
  backend=$(echo $file | grep -o 'offscreen\|xcb\|wayland\|cocoa')
  paint=$(grep -o '"paint_p95_ms": [0-9.]*' "$file" | cut -d' ' -f2)
  frame=$(grep -o '"frame_interval_p95_ms": [0-9.]*' "$file" | cut -d' ' -f2)
  echo "$platform,$backend,$paint,$frame" >> comparison.csv
done

cat comparison.csv
```

---

## 故障排除

### 问题 1：ImportError: PySide6

**症状**：
```
ModuleNotFoundError: No module named 'PySide6'
```

**解决**：
```powershell
# Windows
pip install --upgrade pip
pip install -e ".[test]"
pip show PySide6  # 验证版本 >= 6.10.1
```

```bash
# macOS/Linux
pip install --upgrade pip setuptools
pip install -e ".[test]"
```

### 问题 2：测试被跳过

**症状**：
```
collected 0 items
```

**解决**：检查环境变量

```powershell
# Windows
Write-Host $env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK
# 应输出：1

if ($null -eq $env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK) {
    $env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK = "1"
}
```

```bash
# macOS/Linux
echo $IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK
# 应输出：1

if [ -z "$IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK" ]; then
    export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
fi
```

### 问题 3：Qt 平台插件错误

**症状**：
```
Could not find the Qt platform plugin in ""
```

**解决**：

```powershell
# Windows - 显式使用 offscreen
$env:QT_QPA_PLATFORM="offscreen"
pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

```bash
# macOS/Linux - 类似
export QT_QPA_PLATFORM=offscreen
pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
```

### 问题 4：报告不生成

**症状**：
```
AssertionError: assert json_path.exists()
```

**原因**：报告目录无写权限或路径无效

**解决**：

```powershell
# Windows - 检查和创建目录
$reportDir = "D:\tmp\iphoto-gallery-scroll-performance"
New-Item -ItemType Directory -Force -Path $reportDir

# 设置自定义路径
$env:IPHOTO_GALLERY_SCROLL_REPORT_DIR = $reportDir
```

```bash
# macOS/Linux - 类似
mkdir -p /tmp/iphoto-gallery-scroll-performance
export IPHOTO_GALLERY_SCROLL_REPORT_DIR="/tmp/iphoto-gallery-scroll-performance"
```

### 问题 5：超时或内存不足

**症状**：
```
Timeout exceeded
MemoryError
```

**解决**：

（1）减少测试规模

```powershell
# 只测试 10K 行
pytest tests/performance/test_gallery_scroll_qt_benchmark.py::test_real_qt_gallery_scroll_benchmark[10000] -v
```

（2）关闭后台程序

- Windows 任务管理器：关闭浏览器、IDE、其他重应用
- macOS：`Activity Monitor` 确保可用内存 > 1GB
- Linux：`top` 监控系统资源

（3）增加超时

```powershell
# Windows 添加超时参数
pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v --timeout=300
```

---

## 附录：环境变量参考

| 变量名 | 默认值 | 说明 |
|:---|:---|:---|
| `IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK` | (无) | 必须设为 `1` 才能启动测试 |
| `IPHOTO_RUNTIME_LABEL` | `development` | 运行时标签，用于报告标识 |
| `IPHOTO_GALLERY_SCROLL_REPORT_DIR` | `/tmp/iphoto-gallery-scroll-performance` | 报告输出目录 |
| `IPHOTO_GALLERY_SCROLL_WHEEL_EVENTS` | `120` | 轮转事件数量（调试用减少到 30） |
| `IPHOTO_GALLERY_SCROLL_WHEEL_BATCH_SIZE` | `8` | 事件批处理大小 |
| `QT_QPA_PLATFORM` | (系统默认) | Qt 平台后端：offscreen/xcb/wayland/cocoa |
| `NUMBA_DISABLE_JIT` | `1` | pytest 配置，通常由 pytest.ini 自动设置 |

---

## 相关文档

- 📄 [Phase 2 Handoff 文档](./GALLERY_SCROLL_PERFORMANCE_PHASE2_HANDOFF.md)
- 📄 [Windows 验收报告](./GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md)
- 🔧 [测试源码](../../tests/performance/test_gallery_scroll_qt_benchmark.py)
- 🎯 [功能设计文档](../design/)

---

## 更新历史

| 日期 | 作者 | 内容 |
|:---|:---|:---|
| 2026-06-11 | CI/CD | 初版：添加 Windows/macOS/Linux 平台指南 |



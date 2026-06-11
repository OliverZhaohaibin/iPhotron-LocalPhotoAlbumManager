# Gallery 滚动性能验收进度跟踪

> 当前版本：Phase 2  
> 最后更新：2026-06-11  
> 维护者：CI/CD 自动化体系

## 验收清单

### ✅ 已完成

#### Phase 2 - macOS Offscreen (2026-06-11 原始)
- [x] 可见优先 + Warm 预取架构实现
- [x] Micro 缓略图 100% 覆盖
- [x] Placeholder 完全消除
- [x] 零保护路径违反
- [x] macOS offscreen 基准测试通过

#### Phase 2 - Windows Offscreen (2026-06-11 新增)
- [x] Windows 环境兼容性验证
- [x] 完整基准测试 (10K/100K/1M 行)
- [x] 性能指标收集与分析
- [x] 跨平台对比与文档化
- [x] 后续开发者指南编制

**验收报告**：
- `GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md`
- `PERFORMANCE_TEST_QUICK_REFERENCE.md`

### ⏳ 进行中

#### GPU 加速平台验收
- [ ] Windows DirectX/DXVK 真实驱动测试
- [ ] macOS Metal 加速验证
- [ ] Linux Intel/NVIDIA Mesa 验证
- [ ] 真实大型图库 (100K+) 滚动测试

**优先级**：P0 - 用户实际体验的基础

#### 大规模数据验证
- [ ] 50K~200K 真实照片库验收
- [ ] 高速大跨度滚动场景 (scrollbar 快速拖动)
- [ ] 并发后台操作兼容性 (库扫描、缓存预热)
- [ ] 边界情况处理 (极小/极大缩略图、损坏文件)

**优先级**：P0 - 功能完整性的关键

### 📋 计划中

#### Phase 2/3 微优化
- [ ] `AssetDTO` → `GalleryTileDTO` 轻量化
- [ ] Window-ready 更新按帧合并
- [ ] 完整缩略图的 tier 优先级实现
- [ ] 逐 tile 更新 → 帧局部更新
- [ ] 缩略图内存缓存 LRU 迁移

**优先级**：P1 - 长期性能改进

---

## 平台验收矩阵

| 平台 | Backend | Offscreen 验收 | GPU 验收 | 大规模数据 | 预期交付 |
|:---|:---|:---:|:---:|:---:|:---|
| **macOS** | Cocoa | ✅ 2026-06-11 | ⏳ | ⏳ | 2026-Q3 |
| **Windows** | DirectX | ✅ 2026-06-11 | ⏳ | ⏳ | 2026-Q3 |
| **Linux** | XCB | ⏳ | ⏳ | ⏳ | 2026-Q4 |
| **Linux** | Wayland | ⏳ | ⏳ | ⏳ | 2026-Q4 |

### 图例

- ✅ 已完成
- ⏳ 进行中
- ❌ 阻断/延期

---

## 关键指标基线

### 当前 Offscreen 基线 (2026-06-11)

#### macOS (arm64)

| 场景 | Paint P95 | Frame Interval P95 | Micro 覆盖 |
|:---|---:|---:|:---|
| 10K | 1.836ms | 26.841ms | 100% ✅ |
| 100K | 1.399ms | 50.379ms | 100% ✅ |
| 1M | 1.366ms | 28.800ms | 100% ✅ |

#### Windows (x86_64)

| 场景 | Paint P95 | Frame Interval P95 | Micro 覆盖 |
|:---|---:|---:|:---|
| 10K | 3.830ms | 37.752ms | 100% ✅ |
| 100K | 4.409ms | 37.057ms | 100% ✅ |
| 1M | 5.314ms | 22.091ms | 100% ✅ |

### 性能目标 (GPU 加速后)

| 指标 | 目标值 | 依据 |
|:---|---:|:---|
| Paint P95 | < 2.0ms | 典型应用帧预算 16ms 内占比 < 12.5% |
| Frame Interval P95 | < 24ms | 目标帧率 60fps (16.67ms) 允许 1.5x 抖动 |
| Micro 覆盖 | 100% | 零 placeholder 承诺 |
| Placeholder 覆盖 | 0% | 核心设计目标 |

---

## 测试执行指南

### Windows 平台本地测试

```powershell
# 快速验证 (2 秒)
cd D:\python_code\iPhoto\iPhotos
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
$env:IPHOTO_GALLERY_SCROLL_WHEEL_EVENTS="30"
python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q

# 完整基准 (8-10 秒)
$env:IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK="1"
python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v

# 结果位置
# D:\tmp\iphoto-gallery-scroll-performance\gallery-scroll-windows-offscreen-*.{json,csv}
```

### 与基线对比

```powershell
# 查看基线 (第 3 节表格)
notepad D:\python_code\iPhoto\iPhotos\docs\requirements\scroll-performance\GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md

# 下载或导入 CSV 到 Excel 进行图表对比
Import-Csv "D:\tmp\iphoto-gallery-scroll-performance\gallery-scroll-windows-offscreen-10000.csv" | Format-Table
```

### 多平台 CI 集成

```yaml
# 示例：GitHub Actions (供参考)
jobs:
  gallery-scroll-benchmark:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        include:
          - os: ubuntu-latest
            backend: xcb
          - os: macos-latest
            backend: cocoa
          - os: windows-latest
            backend: direct3d
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: "3.12"
      - run: pip install -e ".[test]"
      - run: |
          export IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1
          export QT_QPA_PLATFORM=${{ matrix.backend }}
          pytest tests/performance/test_gallery_scroll_qt_benchmark.py -v
      - uses: actions/upload-artifact@v4
        with:
          name: benchmark-${{ matrix.os }}-${{ matrix.backend }}
          path: /tmp/iphoto-gallery-scroll-performance/
```

---

## 已知问题与解决方案

| 问题 | 状态 | 影响 | 解决方案 |
|:---|:---:|:---|:---|
| PytestConfig 警告 `env` 选项 | 已知 | 无功能影响 | pytest.ini 配置项，可忽略 |
| Offscreen Paint 成本高 | 已期望 | 不代表真实用户体验 | GPU 平台会显著改善 |
| Frame Interval 抖动大 | 已期望 | Offscreen 特性，虚拟化环境 | GPU 平台会改善 |
| 大数据量内存占用 | 已观察 | 1M 行需要 >500MB | 正常，LRU 优化可改善 |

---

## 后续交接建议

### 对于 GPU 加速验收

1. **优先顺序**
   - Windows GPU (DXVK/Direct3D) - 用户主要平台
   - macOS Metal - 生态完整性
   - Linux GPU (Mesa) - 企业支持

2. **验收清单**
   - Frame Interval P95 < 24ms ✓
   - 无性能回归 ✓
   - Micro 覆盖 100% ✓
   - Placeholder 0% ✓

3. **性能分析工具**
   - Windows：PIX for Windows, GPU.zip
   - macOS：Instruments (Metal 渲染器)
   - Linux：VK_LAYER_LUNARG_monitor, NVIDIA-Tools

### 对于大规模数据验收

1. **测试数据准备**
   - 真实照片库 100K+ 图片
   - 多格式混合 (JPEG/PNG/HEIF/RAW)
   - 变化的缩略图大小

2. **验证场景**
   - 高速滚轮快速指令 (mice wheel delta > 120)
   - Scrollbar 拖拽大跨度 (从 top 拖到 middle)
   - 并发后台操作 (库扫描 + 缓存)

3. **监控指标**
   - `frame_interval` 分布 (P50/P95/P99)
   - `placeholder_ratio` 实时值
   - 内存使用曲线

### 对于下一个开发者

1. **快速了解**
   - 阅读 `GALLERY_SCROLL_PERFORMANCE_PHASE2_HANDOFF.md` 理解设计
   - 阅读 `GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md` 了解现状
   - 运行 `PERFORMANCE_TEST_QUICK_REFERENCE.md` 中的快速测试

2. **本地开发环保**
   - 使用 offscreen 后端进行日常开发测试 (快 10 倍)
   - 在提交前运行完整基准测试
   - 保存 CSV 历史用于对比

3. **性能分析技能**
   - 学会使用 pyprof/cProfile 分析 Python 瓶颈
   - 学会使用平台 GPU profiler 分析渲染成本
   - 建立性能回归预警意识

---

## 文档结构参考

```
docs/requirements/scroll-performance/
├── GALLERY_SCROLL_PERFORMANCE_PHASE1_HANDOFF.md
│   └── Phase 1 设计与实现
├── GALLERY_SCROLL_PERFORMANCE_PHASE2_HANDOFF.md
│   └── Phase 2 设计与 macOS 验收
├── GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md  [新]
│   └── Windows offscreen 验收报告与分析
├── PERFORMANCE_TEST_QUICK_REFERENCE.md  [新]
│   └── 开发者快速参考指南
└── SCROLL_PERFORMANCE_PROGRESS_TRACKING.md  [本文]
    └── 进度跟踪与后续计划
```

---

## 更新日志

### 2026-06-11

**新增内容**
- [x] Windows offscreen 基准测试完成 (3 passed, 7.70s)
- [x] 验收报告 `GALLERY_SCROLL_PERFORMANCE_WINDOWS_VALIDATION.md`
- [x] 快速参考指南 `PERFORMANCE_TEST_QUICK_REFERENCE.md`
- [x] 进度跟踪本文档

**关键数据**
- Windows Paint P95: 3.83~5.31ms (vs macOS 1.37~1.84ms)
- Windows Frame Interval P95: 22.09~37.75ms (vs macOS 26.84~50.38ms)
- 所有场景 Micro 覆盖 100%, Placeholder 0%

**下一阶段**
- GPU 加速平台验收优先级 P0
- 大规模数据场景验收优先级 P0

---

## 相关链接

- 📚 [设计文档首页](../)
- 🔧 [测试源码](../../tests/performance/test_gallery_scroll_qt_benchmark.py)
- 📊 [报告数据](../../../../tmp/iphoto-gallery-scroll-performance/)
- 🎯 [功能需求文档](../README.md)



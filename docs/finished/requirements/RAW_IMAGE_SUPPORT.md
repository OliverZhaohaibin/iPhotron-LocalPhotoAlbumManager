# RAW 图片支持 — 开发文档

> **版本:** 1.0 | **完成日期:** 2026-03-06  
> **状态:** ✅ 已实施

---

## 目录

1. [需求概述](#1-需求概述)
2. [技术方案](#2-技术方案)
3. [模块变更清单](#3-模块变更清单)
4. [核心模块设计](#4-核心模块设计)
5. [性能优化策略](#5-性能优化策略)
6. [导出格式选择](#6-导出格式选择)
7. [设置扩展](#7-设置扩展)
8. [支持的 RAW 格式](#8-支持的-raw-格式)
9. [测试覆盖](#9-测试覆盖)
10. [依赖变更](#10-依赖变更)

---

## 1. 需求概述

基于 **rawpy** 库实现对 RAW 相机原始文件的全链路支持，涵盖：

| 功能 | 说明 |
|------|------|
| 浏览 | RAW 文件在图库网格和详情页中正常显示 |
| 缩略图 | 自动为 RAW 文件生成缩略图（L1/L2/L3 三级缓存） |
| 编辑 | 支持对 RAW 解码后的图像应用调整（曝光、色彩、裁剪等） |
| 导出 | 将 RAW 文件渲染为用户选择的格式（JPG/PNG/TIFF）导出 |
| 设置 | 在 Settings 中新增导出格式选择项 |

### 设计原则

1. **UI 流畅性** — 利用 rawpy 的 `half_size` 解码和自动降采样策略，确保缩略图生成和浏览不阻塞主线程。
2. **优雅扩展** — 新增独立的 `raw_processor.py` 模块，不修改现有图片处理链路的核心逻辑，仅在入口层做格式分发。
3. **渐进降级** — 当 rawpy 未安装时，所有 RAW 相关功能安静降级，不影响应用启动。

---

## 2. 技术方案

### 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户操作层                                    │
│  浏览(Gallery Grid)  ·  缩略图(Thumbnail)  ·  编辑(Edit)  ·  导出   │
└───────────┬────────────────┬─────────────────────┬──────────────────┘
            │                │                     │
            ▼                ▼                     ▼
   ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ image_loader │  │ thumbnail_       │  │ export.py        │
   │   .py        │  │   generator.py   │  │                  │
   └──────┬──────┘  └──────┬───────────┘  └──────┬───────────┘
          │                │                      │
          ▼                ▼                      ▼
   ┌──────────────────────────────────────────────────────────┐
   │              core/raw_processor.py                        │
   │  is_raw_extension()  ·  load_raw_to_pil()                │
   │  RAW_EXTENSIONS                                           │
   └──────────────────────────────────────────────────────────┘
          │
          ▼
   ┌──────────────┐
   │   rawpy       │
   │  (libraw)     │
   └──────────────┘
```

### 数据流

1. **浏览** — `image_loader.load_qimage()` 检测到 RAW 后缀 → 调用 `_load_raw_qimage()` → `raw_processor.load_raw_to_pil()` 解码 → 转换为 `QImage` 返回。
2. **缩略图** — `thumbnail_generator.generate()` 和 `thumbnail_renderer.render_image()` 均通过 `image_loader` 间接使用 RAW 解码。对于小尺寸目标自动启用 `half_size` 解码。
3. **微缩略图** — `generate_micro_thumbnail()` 检测 RAW 后缀 → `_generate_raw_micro_thumbnail()` 使用 `half_size=True` 快速解码并压缩为 16×16 JPEG。
4. **导出** — `export_asset()` 识别 RAW 文件后总是执行渲染（即使没有 sidecar 编辑），将结果保存为用户选择的格式。

---

## 3. 模块变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/iPhoto/core/raw_processor.py` | **新增** | RAW 解码核心模块 |
| `src/iPhoto/media_classifier.py` | 修改 | 新增 `RAW_EXTENSIONS` 和 `ALL_IMAGE_EXTENSIONS` |
| `src/iPhoto/config.py` | 修改 | `DEFAULT_INCLUDE` glob 加入 RAW 后缀 |
| `src/iPhoto/utils/image_loader.py` | 修改 | `load_qimage` / `generate_micro_thumbnail` 加入 RAW 分支 |
| `src/iPhoto/infrastructure/services/thumbnail_generator.py` | 修改 | `PillowThumbnailGenerator` 加入 RAW 分支 |
| `src/iPhoto/core/export.py` | 修改 | 新增 `EXPORT_FORMATS`、`export_format` 参数、RAW 导出 |
| `src/iPhoto/gui/ui/controllers/export_controller.py` | 修改 | 读取 `export_format` 设置并传递给 worker |
| `src/iPhoto/settings/schema.py` | 修改 | 新增 `export_format` 枚举 |
| `pyproject.toml` | 修改 | 新增 `rawpy>=0.23` 依赖 |
| `tests/test_raw_support.py` | **新增** | RAW 全链路测试 |

---

## 4. 核心模块设计

### `core/raw_processor.py`

```python
RAW_EXTENSIONS: frozenset[str]       # 所有已知 RAW 后缀（小写带点号）
is_raw_extension(suffix: str) -> bool
load_raw_to_pil(
    path: Path,
    *,
    half_size: bool = False,
    target_size: tuple[int, int] | None = None,
) -> Image.Image | None
```

**设计要点：**

- rawpy 通过 `_import_rawpy()` 懒加载，未安装时返回 `None` 实现降级。
- `load_raw_to_pil` 支持 `half_size` 和 `target_size` 两种加速模式：
  - `half_size=True`：直接请求半分辨率解码（~4× 加速）。
  - `target_size` 指定时自动判断是否需要半分辨率。
- 使用 `use_camera_wb=True` 应用相机白平衡，输出 sRGB 8-bit。

### `media_classifier.py` 扩展

```python
IMAGE_EXTENSIONS      # 原有标准光栅格式
RAW_EXTENSIONS        # 新增 RAW 格式集
ALL_IMAGE_EXTENSIONS  # IMAGE_EXTENSIONS | RAW_EXTENSIONS（统一查找）
```

`classify_media()` 和 `get_media_type()` 现在使用 `ALL_IMAGE_EXTENSIONS` 进行匹配。

---

## 5. 性能优化策略

| 策略 | 场景 | 效果 |
|------|------|------|
| `half_size` 解码 | 缩略图 ≤1024px | 解码耗时降低 ~75% |
| 自动降采样检测 | `target_size` << 传感器分辨率 | 避免全分辨率解码后再缩放 |
| RAW 早期分发 | `load_qimage()` 开头检测后缀 | 跳过无效的 QImageReader 尝试 |
| 三级缓存复用 | L1(内存) → L2(磁盘) → L3(生成) | 只在首次生成时调用 rawpy |
| 后台线程解码 | ThumbnailLoader / ExportWorker | 不阻塞 UI 主线程 |

---

## 6. 导出格式选择

### 支持格式

| 格式 | Qt 保存标识 | 后缀 | 说明 |
|------|------------|------|------|
| JPG  | `JPEG`     | `.jpg` | 默认格式，有损压缩，兼容性最佳 |
| PNG  | `PNG`      | `.png` | 无损压缩，支持透明通道 |
| TIFF | `TIFF`     | `.tiff` | 无损，适合专业后期流程 |

### `export.py` 新增 API

```python
EXPORT_FORMATS: dict[str, tuple[str, str]]  # {"jpg": ("JPEG", ".jpg"), ...}
DEFAULT_EXPORT_FORMAT = "jpg"

def export_asset(
    source_path: Path,
    export_root: Path,
    library_root: Path,
    export_format: str = DEFAULT_EXPORT_FORMAT,  # 新增参数
) -> bool
```

### 导出逻辑变更

- **RAW 文件**：始终渲染后导出（即使没有 sidecar 编辑），因为 RAW 格式不能被标准查看器打开。
- **编辑过的光栅图**：渲染后以选定格式保存。
- **未编辑光栅/视频**：直接 `shutil.copy2`，保持原格式。

---

## 7. 设置扩展

### Schema 变更

```json
{
  "ui": {
    "export_format": {
      "type": "string",
      "enum": ["jpg", "png", "tiff"]
    }
  }
}
```

默认值：`"jpg"`

### 控制器变更

`ExportController` 在启动导出时从 `SettingsManager` 读取 `ui.export_format`，并将其传递给 `ExportWorker` / `LibraryExportWorker`。

---

## 8. 支持的 RAW 格式

| 后缀 | 相机品牌 |
|------|---------|
| `.cr2`, `.cr3` | Canon |
| `.nef`, `.nrw` | Nikon |
| `.arw`, `.srf`, `.sr2` | Sony |
| `.orf` | Olympus |
| `.rw2` | Panasonic |
| `.raf` | Fujifilm |
| `.pef` | Pentax |
| `.dng` | Adobe DNG / Leica / 通用 |
| `.raw` | 通用 |
| `.3fr` | Hasselblad |
| `.iiq` | Phase One |
| `.rwl` | Leica |
| `.srw` | Samsung |
| `.x3f` | Sigma |
| `.kdc`, `.dcr` | Kodak |
| `.erf` | Epson |

---

## 9. 测试覆盖

新增测试文件：`tests/test_raw_support.py`

| 测试类/方法 | 覆盖功能 |
|------------|---------|
| `TestIsRawExtension` | RAW 后缀识别（大小写） |
| `TestRawExtensionsSets` | 集合完整性、无重叠、联合集 |
| `TestLoadRawToPil` | 解码成功/失败/降级、自动半分辨率 |
| `TestMediaClassifierRaw` | RAW 分类为图片 |
| `TestExportFormatSetting` | 设置默认值、有效/无效格式验证 |
| `TestExportFormatConstants` | 导出格式常量 |
| `TestExportAssetRaw` | RAW 无 sidecar 导出、TIFF 格式导出 |

---

## 10. 依赖变更

### pyproject.toml

```diff
 dependencies = [
   ...
+  "rawpy>=0.23",
 ]
```

rawpy 底层依赖 **LibRaw**（C 库），在 pip 安装时自动下载预编译 wheel，无需手动安装系统库。

### 兼容性

- Python ≥ 3.12
- rawpy ≥ 0.23（支持 LibRaw 0.21+）
- 无已知安全漏洞（已通过 GitHub Advisory Database 验证）

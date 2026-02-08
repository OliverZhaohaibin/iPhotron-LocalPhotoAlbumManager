# 📸 iPhotron
> 将 macOS *照片* 体验带到 Windows —— 文件夹原生、非破坏性的照片管理，支持实况照片、地图和智能相册。

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)
![Language](https://img.shields.io/badge/language-Python%203.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-PySide6%20(Qt6)-orange)
![License](https://img.shields.io/badge/license-MIT-green)
[![GitHub Repo](https://img.shields.io/badge/github-iPhotos-181717?logo=github)](https://github.com/OliverZhaohaibin/iPhotos-LocalPhotoAlbumManager)

**语言 / Languages:**  
[![English](https://img.shields.io/badge/English-Click-blue?style=flat)](../../README.md) | [![中文简体](https://img.shields.io/badge/中文简体-点击-red?style=flat)](README_zh-CN.md) | [![Deutsch](https://img.shields.io/badge/Deutsch-Klick-yellow?style=flat)](README_de.md)

---

## ☕ 支持

[![请我喝杯咖啡](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-支持开发-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/oliverzhao)

## 📥 下载

[![下载 iPhoto 最新版本](https://img.shields.io/badge/⬇️%20下载-iPhoto%20最新版本-blue?style=for-the-badge&logo=windows)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v3.1.5/v3.15.exe)

**💡 快速安装：** 点击上方按钮直接下载最新的 Windows 安装程序（.exe）。

---

## 🌟 Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right)](https://www.star-history.com/#OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right)

---

## 🌟 概述

**iPhoto** 是一款受 macOS *照片* 启发的**文件夹原生照片管理器**。  
它使用轻量级 JSON 清单和缓存文件来组织您的媒体文件 ——  
提供丰富的相册功能，同时**保持所有原始文件完整无损**。

核心亮点：
- 🗂 文件夹原生设计 —— 每个文件夹*就是*一个相册，无需导入。
- ⚙️ 基于 JSON 的清单记录"人工决策"（封面、精选、排序）。
- ⚡ **SQLite 驱动的全局数据库**，为海量图库提供闪电般快速的查询。
- 🧠 智能增量扫描，使用持久化 SQLite 索引。
- 🎥 完整的**实况照片**配对和播放支持。
- 🗺 地图视图，可视化所有照片和视频的 GPS 元数据。
![主界面](../mainview.png)
![预览界面](../preview.png)
---

## ✨ 功能特性

### 🗺 位置视图
在交互式地图上显示您的照片足迹，根据 GPS 元数据聚类附近的照片。
![位置界面](../map1.png)
![位置界面](../map2.png)

### 🎞 实况照片支持
使用 Apple 的 `ContentIdentifier` 无缝配对 HEIC/JPG 和 MOV 文件。  
静态照片上会显示"实况"徽章 —— 点击即可内联播放动态视频。
![实况界面](../live.png)

### 🧩 智能相册
侧边栏提供自动生成的**基础图库**，将照片分组为：
`所有照片`、`视频`、`实况照片`、`收藏`和`最近删除`。

### 🖼 沉浸式详细视图
优雅的照片/视频查看器，带有胶片条导航器和浮动播放栏。

### 🎨 非破坏性照片编辑
全面的编辑套件，包含**调整**和**裁剪**模式：

#### 调整模式
- **光线调整：** 亮度、曝光、高光、阴影、明度、对比度、黑场
- **颜色调整：** 饱和度、自然饱和度、色偏（白平衡校正）
- **黑白：** 强度、中性、色调、颗粒，带有艺术胶片预设
- **色彩曲线：** RGB 和单通道（R/G/B）曲线编辑器，可拖动控制点进行精确色调调整
- **可选颜色：** 针对六个色相范围（红/黄/绿/青/蓝/品红）进行独立的色相/饱和度/亮度控制
- **色阶：** 5 点输入-输出色调映射，带有直方图背景和单通道控制
- **主滑块：** 每个部分都有一个智能主滑块，可在多个微调控件之间分配值
- **实时缩略图：** 实时预览条显示每个调整的效果范围
<img width="1925" height="1086" alt="image" src="https://github.com/user-attachments/assets/9ac3095a-4be4-48fa-84cc-db0a3d58fe16" />

#### 裁剪模式
- **透视校正：** 垂直和水平梯形失真调整
- **拉直工具：** ±45° 旋转，亚度精度
- **翻转（水平）：** 水平翻转支持
- **交互式裁剪框：** 拖动手柄、边缘吸附和宽高比约束
- **黑边防止：** 自动验证确保透视变换后不出现黑边
  
<img width="1925" height="1086" alt="image" src="https://github.com/user-attachments/assets/6a5e927d-3403-4c22-9512-7564a0f24702" />
所有编辑都存储在 `.ipo` 附属文件中，保持原始照片不被触动。

### ℹ️ 浮动信息面板
切换浮动元数据面板，显示 EXIF、相机/镜头信息、曝光、光圈、焦距、文件大小等。
![信息界面](../info1.png)

### 💬 丰富的交互
- 从资源管理器/访达直接拖放文件到相册。
- 多选和上下文菜单，用于复制、在文件夹中显示、移动、删除、恢复。
- 流畅的缩略图过渡和 macOS 风格的相册导航。

---

## ⚙️ 核心引擎

| 概念 | 描述 |
|----------|--------------|
| **文件夹 = 相册** | 通过 `.iphoto.album.json` 清单文件管理。 |
| **全局 SQLite 数据库** | 所有资产元数据存储在库根目录的单个高性能数据库中（`global_index.db`）。 |
| **增量扫描** | 通过幂等 upsert 操作将新/更改的文件扫描到全局数据库中。 |
| **智能索引** | 对 `parent_album_path`、`ts`、`media_type` 和 `is_favorite` 建立多列索引，实现即时查询。 |
| **实况配对** | 使用 `ContentIdentifier` 或时间接近度自动匹配实况照片。 |
| **反向地理编码** | 将 GPS 坐标转换为人类可读的位置（例如"伦敦"）。 |
| **非破坏性编辑** | 在 `.ipo` 附属文件中存储光线/颜色/黑白/裁剪调整。 |
| **GPU 渲染** | 实时 OpenGL 3.3 预览，带有透视变换和色彩分级。 |
| **命令行工具** | 提供 `iphoto` CLI，用于相册初始化、扫描、配对和报告生成。 |

---

## 🧰 命令行使用

```bash
# 1️⃣ 安装依赖
pip install -e .

# 2️⃣ 初始化相册（创建 .iphoto.album.json）
iphoto init /path/to/album

# 3️⃣ 扫描文件并构建索引
iphoto scan /path/to/album

# 4️⃣ 配对实况照片（HEIC/JPG + MOV）
iphoto pair /path/to/album

# 5️⃣ 管理相册属性
iphoto cover set /path/to/album IMG_1234.HEIC
iphoto feature add /path/to/album museum/IMG_9999.HEIC#live
iphoto report /path/to/album
```

## 🖥 GUI 界面（PySide6 / Qt6）

安装后，您可以启动完整的桌面界面：

```bash
iphoto-gui
```
或直接打开特定相册：

```bash
iphoto-gui /photos/LondonTrip
```

### GUI 亮点

- **相册侧边栏：** 分层文件夹视图，带有收藏和智能相册。  
- **资产网格：** 自适应缩略图布局、选择和延迟加载预览。  
- **地图视图：** 交互式 GPS 聚类，带有瓦片缓存。  
- **详细查看器：** 胶片条导航和播放控件。  
- **编辑模式：** 非破坏性调整（光线/颜色/黑白）和裁剪（透视/拉直）工具。  
- **元数据面板：** 可折叠的 EXIF + QuickTime 信息面板。  
- **上下文菜单：** 复制、移动、删除、恢复。

## 🧱 项目结构

源代码位于 `src/iPhoto/` 目录下，遵循基于 **MVVM + DDD（领域驱动设计）** 原则的**分层架构**。

---

### 1️⃣ 领域层（`src/iPhoto/domain/`）

纯业务模型和存储库接口，独立于任何框架。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`models/`** | 领域实体：`Album`、`Asset`、`MediaType`、`LiveGroup`。 |
| **`models/query.py`** | 资产过滤、排序和分页的查询对象模式。 |
| **`repositories.py`** | 存储库接口：`IAlbumRepository`、`IAssetRepository`。 |

---

### 2️⃣ 应用层（`src/iPhoto/application/`）

用用例和应用服务封装的业务逻辑。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`use_cases/open_album.py`** | 打开相册并发布事件的用例。 |
| **`use_cases/scan_album.py`** | 扫描相册文件并更新索引的用例。 |
| **`use_cases/pair_live_photos.py`** | 实况照片配对逻辑的用例。 |
| **`services/album_service.py`** | 相册操作的应用服务。 |
| **`services/asset_service.py`** | 资产操作（收藏、查询）的应用服务。 |
| **`interfaces.py`** | 抽象：`IMetadataProvider`、`IThumbnailGenerator`。 |
| **`dtos.py`** | 用例请求/响应的数据传输对象。 |

---

### 3️⃣ 基础设施层（`src/iPhoto/infrastructure/`）

领域接口的具体实现。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`repositories/sqlite_asset_repository.py`** | `IAssetRepository` 的 SQLite 实现。 |
| **`repositories/sqlite_album_repository.py`** | `IAlbumRepository` 的 SQLite 实现。 |
| **`db/pool.py`** | 线程安全的数据库连接池。 |
| **`services/`** | 基础设施服务（元数据提取、缩略图）。 |

---

### 4️⃣ 核心后端（`src/iPhoto/`）

不依赖任何 GUI 框架（如 PySide6）的纯 Python 逻辑。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`app.py`** | 高级后端**外观**，协调所有核心模块，供 CLI 和 GUI 使用。 |
| **`cli.py`** | 基于 Typer 的命令行入口点，解析用户命令并调用 `app.py` 中的方法。 |
| **`models/`** | 遗留数据结构，如 `Album`（清单读/写）和 `LiveGroup`。 |
| **`io/`** | 处理文件系统交互，主要是 `scanner.py`（文件扫描）和 `metadata.py`（元数据读取）。 |
| **`core/`** | 核心算法逻辑，包括 `pairing.py`（实况照片配对）和图像调整解析器。 |
| ├─ **`light_resolver.py`** | 将光线主滑块解析为 7 个微调参数（亮度、曝光等）。 |
| ├─ **`color_resolver.py`** | 通过图像统计将颜色主滑块解析为饱和度/自然饱和度/色偏。 |
| ├─ **`bw_resolver.py`** | 使用 3 点高斯插值解析黑白主滑块。 |
| ├─ **`curve_resolver.py`** | 使用贝塞尔插值和 LUT 生成管理色彩曲线调整。 |
| ├─ **`selective_color_resolver.py`** | 使用 HSL 处理实现针对六个色相范围的可选颜色调整。 |
| ├─ **`levels_resolver.py`** | 使用 5 点输入-输出色调映射处理色阶调整。 |
| └─ **`filters/`** | 高性能图像处理（NumPy 矢量化 → Numba JIT → QColor 回退）。 |
| **`cache/`** | 管理全局 SQLite 数据库（`index_store/`），包含模块化组件：引擎、迁移、恢复、查询和存储库。包括用于文件级锁定的 `lock.py`。 |
| **`utils/`** | 通用实用工具，特别是外部工具（`exiftool.py`、`ffmpeg.py`）的包装器。 |
| **`schemas/`** | JSON Schema 定义，例如 `album.schema.json`。 |
| **`di/`** | 依赖注入容器，用于服务注册和解析。 |
| **`events/`** | 领域事件的事件总线（发布-订阅模式）。 |
| **`errors/`** | 统一错误处理，带有严重级别和事件发布。 |

---

### 5️⃣ GUI 层（`src/iPhoto/gui/`）

基于 PySide6 的桌面应用程序，遵循 **MVVM（模型-视图-视图模型）** 模式。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`main.py`** | GUI 应用程序的入口点（`iphoto-gui` 命令）。 |
| **`appctx.py`** | 定义 `AppContext`，一个共享的全局状态管理器，用于设置、库管理器和后端外观实例。 |
| **`facade.py`** | 定义 `AppFacade`（一个 `QObject`）—— GUI 和后端之间的**桥梁**。使用 Qt **信号/槽**将后端操作与 GUI 事件循环解耦。 |
| **`coordinators/`** | **MVVM 协调器**，编排视图导航和业务流程。 |
| ├─ **`main_coordinator.py`** | 主窗口协调器，管理子协调器。 |
| ├─ **`navigation_coordinator.py`** | 处理相册/库导航。 |
| ├─ **`playback_coordinator.py`** | 媒体播放协调。 |
| ├─ **`edit_coordinator.py`** | 编辑工作流程协调。 |
| └─ **`view_router.py`** | 集中式视图路由逻辑。 |
| **`viewmodels/`** | 用于 MVVM 数据绑定的**视图模型**。 |
| ├─ **`asset_list_viewmodel.py`** | 资产列表展示的视图模型。 |
| ├─ **`album_viewmodel.py`** | 相册展示的视图模型。 |
| └─ **`asset_data_source.py`** | 资产查询的数据源抽象。 |
| **`services/`** | 后台操作服务（导入、移动、更新）。 |
| **`background_task_manager.py`** | 管理 `QThreadPool` 和任务生命周期。 |
| **`ui/`** | UI 组件：窗口、控制器、模型和小部件。 |
| ├─ **`main_window.py`** | 主 `QMainWindow` 实现。 |
| ├─ **`controllers/`** | 专门的 UI 控制器（上下文菜单、对话框、导出、播放器等）。 |
| ├─ **`models/`** | Qt 模型-视图数据模型（例如 `AlbumTreeModel`、`EditSession`）。 |
| ├─ **`widgets/`** | 可重用的 QWidget 组件（侧边栏、地图、播放器栏、编辑小部件）。 |
| └─ **`tasks/`** | 后台任务的 `QRunnable` 实现。 |

#### 编辑小部件和模块（`src/iPhoto/gui/ui/widgets/`）

编辑系统由模块化小部件和子模块组成，用于非破坏性照片调整：

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`edit_sidebar.py`** | 容器小部件，托管带有堆叠布局的调整/裁剪模式页面。 |
| **`edit_light_section.py`** | 光线调整面板（亮度、曝光、高光、阴影、明度、对比度、黑场）。 |
| **`edit_color_section.py`** | 颜色调整面板（饱和度、自然饱和度、色偏），带有图像统计分析。 |
| **`edit_bw_section.py`** | 黑白面板（强度、中性、色调、颗粒），带有艺术预设。 |
| **`edit_curve_section.py`** | 色彩曲线面板，带有 RGB 和单通道曲线编辑，可拖动控制点。 |
| **`edit_selective_color_section.py`** | 可选颜色面板，针对六个色相范围（红/黄/绿/青/蓝/品红），带有色相/饱和度/亮度控制。 |
| **`edit_levels_section.py`** | 色阶面板，带有 5 点色调映射、直方图显示和单通道控制。 |
| **`edit_perspective_controls.py`** | 透视校正滑块（垂直、水平、拉直）。 |
| **`edit_topbar.py`** | 编辑模式工具栏，带有调整/裁剪切换和操作按钮。 |
| **`edit_strip.py`** | 在整个编辑面板中使用的自定义滑块小部件（`BWSlider`）。 |
| **`thumbnail_strip_slider.py`** | 带有实时缩略图预览条的滑块。 |
| **`gl_image_viewer/`** | 基于 OpenGL 的图像查看器子模块，用于实时预览渲染。 |
| **`gl_crop/`** | 裁剪交互子模块（模型、控制器、命中测试器、动画器、策略）。 |
| **`gl_renderer.py`** | 核心 OpenGL 渲染器，处理纹理上传和着色器统一变量。 |
| **`perspective_math.py`** | 透视矩阵计算和黑边验证的几何实用工具。 |

---

### 6️⃣ 地图组件（`maps/`）

该目录包含一个半独立的**地图渲染模块**，供 `PhotoMapView` 小部件使用。

| 文件 / 模块 | 描述 |
|----------------|-------------|
| **`map_widget/`** | 包含核心地图小部件类和渲染逻辑。 |
| ├─ **`map_widget.py`** | 主地图小部件类，管理用户交互和视口状态。 |
| ├─ **`map_gl_widget.py`** | 基于 OpenGL 的渲染小部件，用于高效的瓦片和矢量绘制。 |
| ├─ **`map_renderer.py`** | 负责渲染地图瓦片和矢量图层。 |
| └─ **`tile_manager.py`** | 处理瓦片获取、缓存和生命周期管理。 |
| **`style_resolver.py`** | 解析 MapLibre 样式表（`style.json`）并将样式规则应用于渲染器。 |
| **`tile_parser.py`** | 解析 `.pbf` 矢量瓦片文件并将其转换为可绘制的地图基元。 |

---

这种模块化分离确保：
- ✅ **领域逻辑**保持纯净且独立于框架。
- ✅ **应用层**在可测试的用例中封装业务规则。
- ✅ **GUI 架构**遵循 MVVM 原则（协调器管理视图模型和视图）。
- ✅ **依赖注入**实现松耦合和易于测试。
- ✅ **后台任务**异步处理，实现流畅的用户交互。

---

## 🧩 外部工具

| 工具 | 用途 |
|------|----------|
| **ExifTool** | 读取 EXIF、GPS、QuickTime 和实况照片元数据。 |
| **FFmpeg / FFprobe** | 生成视频缩略图并解析视频信息。 |

> 确保两者都在您的系统 `PATH` 中可用。

Python 依赖项（例如 `Pillow`、`reverse-geocoder`）通过 `pyproject.toml` 自动安装。

---

## 🧪 开发

### 运行测试

```bash
pytest
```

### 代码风格

- **Linters 和 Formatters：** `ruff`、`black` 和 `mypy`  
- **行长度：** ≤ 100 字符  
- **类型提示：** 使用完整注释（例如 `Optional[str]`、`list[Path]`、`dict[str, Any]`）

## 📄 许可证

**MIT 许可证 © 2025**  
由 **Haibin Zhao (OliverZhaohaibin)** 创建  

> *iPhoto —— 一个文件夹原生、人类可读且完全可重建的照片系统。*  
> *无需导入。无需数据库。只有您的照片，优雅地组织。*

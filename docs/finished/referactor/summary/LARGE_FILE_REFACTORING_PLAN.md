# 大型文件重构拆分方案
# Large File Refactoring & Splitting Plan

> **文档版本 / Document Version:** 1.0
> **创建日期 / Created:** 2026-02-08
> **关联文档 / Related:** [ARCHITECTURE_ANALYSIS_AND_REFACTORING.md](./ARCHITECTURE_ANALYSIS_AND_REFACTORING.md)
> **项目 / Project:** iPhotron LocalPhotoAlbumManager

---

## 目录 / Table of Contents

1. [概述 / Overview](#概述--overview)
2. [文件清单与问题摘要 / File Inventory & Problem Summary](#文件清单与问题摘要--file-inventory--problem-summary)
3. [详细重构方案 / Detailed Refactoring Plans](#详细重构方案--detailed-refactoring-plans)
   - 3.1 [manager.py — 库管理器](#31-managerpy--librarymanager-库管理器)
   - 3.2 [asset_data_source.py — 资产数据源](#32-asset_data_sourcepy--assetdatasource-资产数据源)
   - 3.3 [thumbnail_loader.py — 缩略图加载器](#33-thumbnail_loaderpy--thumbnailloader-缩略图加载器)
   - 3.4 [edit_sidebar.py — 编辑侧边栏](#34-edit_sidebarpy--editsidebar-编辑侧边栏)
   - 3.5 [edit_curve_section.py — 曲线编辑区](#35-edit_curve_sectionpy--editcurvesection-曲线编辑区)
   - 3.6 [gl_renderer.py — OpenGL 渲染器](#36-gl_rendererpy--glrenderer-opengl-渲染器)
   - 3.7 [widget.py — GL 图片查看器](#37-widgetpy--glimageviewer-gl-图片查看器)
   - 3.8 [map_renderer.py — 地图渲染器](#38-map_rendererpy--maprenderer-地图渲染器)
   - 3.9 [curve.py — 曲线 Demo](#39-curvepy--曲线-demo)
   - 3.10 [white balance.py — 白平衡 Demo](#310-white-balancepy--白平衡-demo)
4. [跨文件代码重复分析 / Cross-File Duplication Analysis](#跨文件代码重复分析--cross-file-duplication-analysis)
5. [实施优先级与路线图 / Implementation Priority & Roadmap](#实施优先级与路线图--implementation-priority--roadmap)
6. [重构原则与注意事项 / Refactoring Principles & Guidelines](#重构原则与注意事项--refactoring-principles--guidelines)

---

## 概述 / Overview

当前项目中存在 **10 个超大文件**（均超过 870 行），这些文件违反了单一职责原则（SRP），将多种关注点混合在同一模块中，导致可读性差、测试困难、维护成本高。

The project currently has **10 oversized files** (all exceeding 870 lines). These files violate the Single Responsibility Principle (SRP), mixing multiple concerns in a single module, resulting in poor readability, difficult testing, and high maintenance cost.

**核心问题 / Core Problems:**
- 🔴 **上帝对象** — 单个类承担过多职责（如 `EditSidebar`, `LibraryManager`, `GLRenderer`）
- 🔴 **逻辑混杂** — UI 渲染、业务逻辑、线程管理混在一个文件中
- 🟡 **Demo 与产品代码重复** — `demo/` 中的组件与 `src/` 中的实现大量重复
- 🟡 **测试困难** — 过长的方法和复杂的初始化使单元测试难以编写

---

## 文件清单与问题摘要 / File Inventory & Problem Summary

| # | 文件 / File | 行数 / Lines | 主类 / Main Class | 严重度 / Severity | 主要问题 / Primary Issue |
|---|-------------|-------------|-------------------|-------------------|------------------------|
| 1 | `demo/curve/curve.py` | 1384 | `CurvesDemo` + 6 类 | 🟡 中 | Demo 单文件包含完整应用 |
| 2 | `src/.../edit_curve_section.py` | 1165 | `EditCurveSection` + 3 类 | 🔴 高 | 数学算法与 UI 紧耦合 |
| 3 | `src/.../edit_sidebar.py` | 1052 | `EditSidebar` | 🔴 高 | 300 行 `__init__`，40+ 信号连接 |
| 4 | `demo/white balance/white balance.py` | 998 | `WBMain` + 6 类 | 🟡 中 | Demo 单文件包含完整应用 |
| 5 | `src/.../gl_image_viewer/widget.py` | 997 | `GLImageViewer` | 🟡 中 | 已有部分拆分，仍需进一步 |
| 6 | `src/.../thumbnail_loader.py` | 963 | `ThumbnailLoader` + `ThumbnailJob` | 🔴 高 | 缓存/渲染/调度混杂 |
| 7 | `src/.../gl_renderer.py` | 940 | `GLRenderer` | 🔴 高 | 着色器/纹理/渲染管线混杂 |
| 8 | `src/.../asset_data_source.py` | 938 | `AssetDataSource` + 4 类 | 🔴 高 | 分页/线程/缓存/移动混杂 |
| 9 | `src/.../library/manager.py` | 909 | `LibraryManager` | 🔴 高 | 扫描/监视/树管理/地理混杂 |
| 10 | `maps/.../map_renderer.py` | 878 | `MapRenderer` + 3 数据类 | 🟡 中 | 视口/瓦片/标注/渲染混杂 |

---

## 详细重构方案 / Detailed Refactoring Plans

---

### 3.1 `manager.py` — LibraryManager 库管理器

**路径 / Path:** `src/iPhoto/library/manager.py` (909 行)

#### 当前职责分析 / Current Responsibility Analysis

`LibraryManager` 是一个典型的上帝对象，同时管理：
- 📂 相册树的构建与刷新（`_refresh_tree`, `_build_node`）
- 🔍 文件系统扫描的调度与监控（`start_scanning`, `_on_chunk_ready`）
- 👁️ 文件系统监视器（`QFileSystemWatcher` 绑定）
- 🌍 地理位置资产的聚合（`geotagged_assets` 属性）
- 🗑️ 删除项目的管理（`empty_trash`, `_cleanup_deleted`）
- ✏️ 相册 CRUD 操作（`create_album`, `rename_album`, `delete_album`）
- 📄 Manifest 文件管理

#### 拆分方案 / Splitting Plan

```
src/iPhoto/library/
├── __init__.py                  # 公开接口不变
├── manager.py                   # ≈200 行 — 精简为协调者/门面
├── tree.py                      # (已存在) 相册树数据结构
├── album_operations.py          # ≈180 行 — 新建: 相册 CRUD 操作
├── scan_coordinator.py          # ≈200 行 — 新建: 扫描调度与进度管理
├── filesystem_watcher.py        # ≈100 行 — 新建: 文件系统监视封装
├── geo_aggregator.py            # ≈80 行  — 新建: 地理资产聚合
├── trash_manager.py             # ≈80 行  — 新建: 删除项管理
└── workers/                     # (已存在)
    ├── scanner_worker.py
    └── rescan_worker.py
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `AlbumOperations` 类**

将相册的创建、重命名、删除、manifest 管理提取到 `album_operations.py`：

```python
# album_operations.py
class AlbumOperations:
    """相册 CRUD 操作 / Album CRUD operations."""

    def __init__(self, library_path: Path, repository: IAssetRepository):
        self._library_path = library_path
        self._repository = repository

    def create_album(self, parent_path: Path, name: str) -> Path: ...
    def rename_album(self, album_path: Path, new_name: str) -> Path: ...
    def delete_album(self, album_path: Path) -> None: ...
    def write_manifest(self, album_path: Path, manifest: dict) -> None: ...
    def read_manifest(self, album_path: Path) -> dict: ...
```

**Step 2: 提取 `ScanCoordinator` 类**

将扫描的启动、进度跟踪、结果缓冲提取到 `scan_coordinator.py`：

```python
# scan_coordinator.py
class ScanCoordinator(QObject):
    """扫描调度与进度管理 / Scan scheduling & progress tracking."""

    scanProgress = Signal(int, int)
    scanChunkReady = Signal(list)
    scanFinished = Signal()
    scanBatchFailed = Signal(str)

    def __init__(self, thread_pool: QThreadPool):
        ...
    def start_scanning(self, album_path: Path, recursive: bool = True) -> None: ...
    def cancel_scanning(self) -> None: ...
    def _on_chunk_ready(self, assets: list) -> None: ...
    def _on_scan_finished(self) -> None: ...
```

**Step 3: 提取 `FileSystemWatcher` 封装**

```python
# filesystem_watcher.py
class LibraryFileWatcher(QObject):
    """文件系统监视封装 / Filesystem watcher wrapper."""

    directoryChanged = Signal(str)

    def __init__(self):
        self._watcher = QFileSystemWatcher()
    def watch(self, paths: list[str]) -> None: ...
    def unwatch_all(self) -> None: ...
```

**Step 4: 提取 `GeoAggregator` 和 `TrashManager`**

```python
# geo_aggregator.py
@dataclass
class GeotaggedAsset:
    latitude: float
    longitude: float
    asset_path: Path

class GeoAggregator:
    """地理资产聚合 / Geotagged asset aggregation."""
    def collect(self, repository: IAssetRepository) -> list[GeotaggedAsset]: ...

# trash_manager.py
class TrashManager:
    """删除项管理 / Trash/deleted items management."""
    def empty_trash(self, library_path: Path) -> int: ...
    def cleanup_deleted(self, album_path: Path) -> None: ...
```

**Step 5: 重构 `LibraryManager` 为门面**

```python
# manager.py (精简后)
class LibraryManager(QObject):
    """精简为协调者，委托给各子模块 / Slimmed down to coordinator."""

    def __init__(self, library_path, repository):
        self._albums = AlbumOperations(library_path, repository)
        self._scanner = ScanCoordinator(self._thread_pool)
        self._watcher = LibraryFileWatcher()
        self._geo = GeoAggregator()
        self._trash = TrashManager()
        # 信号转发 / Signal forwarding
        self._scanner.scanProgress.connect(self.scanProgress)
        ...

    # 委托方法 / Delegate methods
    def create_album(self, parent, name):
        return self._albums.create_album(parent, name)
```

---

### 3.2 `asset_data_source.py` — AssetDataSource 资产数据源

**路径 / Path:** `src/iPhoto/gui/viewmodels/asset_data_source.py` (938 行)

#### 当前职责分析 / Current Responsibility Analysis

- 📄 分页加载策略（页码管理、预加载窗口）
- 🧵 多线程 Worker 协调（`_AssetLoadWorker`, `_AssetPageWorker`）
- 💾 路径存在性缓存（`_path_exists_cache`）
- 🔀 待移动缓冲（`_PendingMove`）
- 🔄 Asset → AssetDTO 转换
- ✏️ 编辑协调（sidecar 读写、编辑状态同步）

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/viewmodels/
├── asset_data_source.py          # ≈300 行 — 精简为数据源门面
├── asset_paging.py               # ≈150 行 — 新建: 分页策略
├── asset_workers.py              # ≈200 行 — 新建: 线程 Worker 类
├── asset_dto_converter.py        # ≈80 行  — 新建: DTO 转换逻辑
├── path_cache.py                 # ≈60 行  — 新建: 路径缓存工具
└── pending_move_buffer.py        # ≈60 行  — 新建: 待移动操作缓冲
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `PathExistsCache`**

路径缓存是一个独立的、可复用的工具类：

```python
# path_cache.py
class PathExistsCache:
    """带 TTL 的路径存在性缓存 / Path existence cache with TTL."""

    def __init__(self, ttl_seconds: float = 5.0):
        self._cache: dict[Path, tuple[bool, float]] = {}
        self._ttl = ttl_seconds

    def exists(self, path: Path) -> bool: ...
    def invalidate(self, path: Path) -> None: ...
    def clear(self) -> None: ...
```

**Step 2: 提取 Worker 类到 `asset_workers.py`**

将 `_AssetLoadSignals`, `_AssetLoadWorker`, `_AssetPageSignals`, `_AssetPageWorker` 全部移出：

```python
# asset_workers.py
class AssetLoadSignals(QObject):
    completed = Signal(list)

class AssetLoadWorker(QRunnable):
    """单资产详情加载 / Single asset detail loader."""
    ...

class AssetPageSignals(QObject):
    completed = Signal(int, list)

class AssetPageWorker(QRunnable):
    """分页资产加载 / Paged asset loader."""
    ...
```

**Step 3: 提取 `PagingStrategy`**

```python
# asset_paging.py
class PagingStrategy:
    """分页管理策略 / Pagination management."""

    def __init__(self, page_size: int = 200):
        self._page_size = page_size
        self._loaded_pages: set[int] = set()
        self._total_count: int = 0

    def page_for_index(self, index: int) -> int: ...
    def pages_to_prefetch(self, visible_range: range) -> list[int]: ...
    def mark_loaded(self, page: int) -> None: ...
    def reset(self) -> None: ...
```

**Step 4: 提取 `PendingMoveBuffer` 和 `AssetDtoConverter`**

```python
# pending_move_buffer.py
@dataclass
class PendingMove:
    source: Path
    destination: Path
    timestamp: float

class PendingMoveBuffer:
    """待移动操作缓冲 / Pending move operations buffer."""
    def add(self, move: PendingMove) -> None: ...
    def apply_all(self, repository) -> list[bool]: ...
    def clear(self) -> None: ...

# asset_dto_converter.py
class AssetDtoConverter:
    """Asset → AssetDTO 转换 / Asset to DTO conversion."""
    def convert(self, asset: Asset, path_cache: PathExistsCache) -> AssetDTO: ...
    def convert_batch(self, assets: list[Asset], ...) -> list[AssetDTO]: ...
```

---

### 3.3 `thumbnail_loader.py` — ThumbnailLoader 缩略图加载器

**路径 / Path:** `src/iPhoto/gui/ui/tasks/thumbnail_loader.py` (963 行)

#### 当前职责分析 / Current Responsibility Analysis

- 📁 缓存路径生成与管理（`generate_cache_path`, `safe_unlink`, `stat_mtime_ns`）
- 🖼️ 图片渲染管线（缩放、EXIF 旋转、调整应用）
- 🎥 视频帧提取
- 🎨 画布合成（带背景的方形缩略图）
- ⚙️ 作业调度与线程池管理
- 📊 内存管理（LRU 缓存淘汰）
- ✅ 缓存验证（时间戳 + sidecar 对比）

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/ui/tasks/
├── thumbnail_loader.py           # ≈250 行 — 精简: 调度与内存管理
├── thumbnail_cache.py            # ≈150 行 — 新建: 缓存路径/验证/IO
├── thumbnail_renderer.py         # ≈200 行 — 新建: 图像渲染管线
├── thumbnail_job.py              # ≈200 行 — 新建: ThumbnailJob 类
└── thumbnail_compositor.py       # ≈80 行  — 新建: 画布合成逻辑
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `ThumbnailCache`**

将缓存管理逻辑独立：

```python
# thumbnail_cache.py
def safe_unlink(path: Path) -> None: ...
def stat_mtime_ns(path: Path) -> int | None: ...

class ThumbnailCache:
    """缩略图磁盘缓存管理 / Thumbnail disk cache management."""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir

    def generate_path(self, source: Path, size: int, mtime_ns: int) -> Path:
        """生成缓存路径 (基于 hash) / Generate cache path (hash-based)."""
        ...
    def is_valid(self, cache_path: Path, source_mtime_ns: int,
                 sidecar_mtime_ns: int | None) -> bool:
        """检查缓存是否新鲜 / Check cache freshness."""
        ...
    def read(self, cache_path: Path) -> QImage | None: ...
    def write(self, cache_path: Path, image: QImage) -> None: ...
```

**Step 2: 提取 `ThumbnailRenderer`**

将图像处理管线独立，使其可以独立测试：

```python
# thumbnail_renderer.py
class ThumbnailRenderer:
    """缩略图渲染管线 / Thumbnail rendering pipeline."""

    def render_image(self, source: Path, target_size: int,
                     adjustments: dict | None = None) -> QImage:
        """加载、缩放、旋转、调整 / Load, scale, rotate, adjust."""
        ...
    def extract_video_frame(self, video_path: Path,
                            timestamp_ms: int = 0) -> QImage: ...
    def composite_canvas(self, image: QImage, canvas_size: int,
                         bg_color: QColor) -> QImage:
        """合成带背景的方形缩略图 / Compose square thumbnail with background."""
        ...
```

**Step 3: 提取 `ThumbnailJob` 到独立文件**

```python
# thumbnail_job.py
class ThumbnailJob(QRunnable):
    """单个缩略图渲染作业 / Single thumbnail render job."""

    def __init__(self, asset_path, size, cache, renderer, compositor):
        self._cache = cache        # ThumbnailCache
        self._renderer = renderer  # ThumbnailRenderer
        self._compositor = compositor
        ...

    def run(self) -> None:
        # 1. 检查缓存 → 2. 渲染 → 3. 合成 → 4. 写缓存 → 5. 发信号
        ...
```

**Step 4: 精简 `ThumbnailLoader`**

```python
# thumbnail_loader.py (精简后)
class ThumbnailLoader(QObject):
    """缩略图调度器 — 只负责调度和内存管理
       Thumbnail scheduler — only handles scheduling & memory."""

    ready = Signal(str, QImage)

    def __init__(self, cache_dir, thread_pool):
        self._cache = ThumbnailCache(cache_dir)
        self._renderer = ThumbnailRenderer()
        self._compositor = ThumbnailCompositor()
        self._memory_cache = OrderedDict()  # LRU
        self._thread_pool = thread_pool

    def request(self, asset_path, size) -> None: ...
    def cancel_all(self) -> None: ...
    def _evict_if_needed(self) -> None: ...
```

---

### 3.4 `edit_sidebar.py` — EditSidebar 编辑侧边栏

**路径 / Path:** `src/iPhoto/gui/ui/widgets/edit_sidebar.py` (1052 行)

#### 当前职责分析 / Current Responsibility Analysis

`EditSidebar` 的 `__init__` 方法长达约 **300 行**，包含：
- 🏗️ 8 个编辑工具区域的实例化（Light, Color, B&W, WB, Curves, Levels, Selective Color, Perspective）
- 🔗 40+ 个信号槽连接
- 🎨 滚动区域 / 布局构建
- 🔄 状态同步与编辑会话管理
- ↩️ 重置 / 撤销逻辑

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/ui/widgets/
├── edit_sidebar.py               # ≈300 行 — 精简为容器 + 布局
├── edit_sidebar_signals.py       # ≈120 行 — 新建: 信号路由/分发
├── edit_sidebar_sections.py      # ≈200 行 — 新建: 区域注册与工厂
└── edit_section_coordinator.py   # ≈150 行 — 新建: 编辑会话协调
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `EditSectionRegistry`（区域注册工厂）**

将 8 个编辑区域的创建与注册逻辑提取：

```python
# edit_sidebar_sections.py
from dataclasses import dataclass
from typing import Callable

@dataclass
class SectionConfig:
    """编辑区域配置 / Edit section configuration."""
    name: str
    widget_factory: Callable  # 创建 widget 的工厂函数
    icon: str
    default_expanded: bool = False

class EditSectionRegistry:
    """管理所有编辑区域的注册与创建 / Manages registration & creation of sections."""

    def __init__(self):
        self._configs: list[SectionConfig] = []

    def register(self, config: SectionConfig) -> None: ...

    def create_all(self, edit_session) -> list[tuple[SectionConfig, QWidget]]:
        """创建所有已注册区域的 widget / Create widgets for all registered sections."""
        ...

    @staticmethod
    def default_sections() -> "EditSectionRegistry":
        """返回默认的 8 个编辑区域 / Return default 8 edit sections."""
        registry = EditSectionRegistry()
        registry.register(SectionConfig("Light", EditLightSection, "light.svg"))
        registry.register(SectionConfig("Color", EditColorSection, "color.svg"))
        registry.register(SectionConfig("Curves", EditCurveSection, "curve.svg"))
        # ... 其余区域
        return registry
```

**Step 2: 提取 `EditSignalRouter`（信号路由）**

将 40+ 信号连接逻辑集中管理：

```python
# edit_sidebar_signals.py
class EditSignalRouter(QObject):
    """集中管理编辑区域信号的路由与分发
       Centralizes signal routing for all edit sections."""

    # 汇总信号 / Aggregated signals
    anyInteractionStarted = Signal()
    anyInteractionFinished = Signal()
    anyParamsPreviewed = Signal(dict)
    anyParamsCommitted = Signal(dict)

    def connect_section(self, section: QWidget) -> None:
        """自动连接一个编辑区域的标准信号 / Auto-connect standard signals."""
        if hasattr(section, "interactionStarted"):
            section.interactionStarted.connect(self.anyInteractionStarted)
        if hasattr(section, "interactionFinished"):
            section.interactionFinished.connect(self.anyInteractionFinished)
        # ... 其他标准信号
```

**Step 3: 提取 `EditSessionCoordinator`**

```python
# edit_section_coordinator.py
class EditSessionCoordinator(QObject):
    """编辑会话协调: 管理预览/提交/重置流程
       Coordinates preview / commit / reset workflow."""

    def __init__(self, edit_session):
        self._session = edit_session
        self._dirty_sections: set[str] = set()

    def on_preview(self, section_name: str, params: dict) -> None: ...
    def on_commit(self, section_name: str, params: dict) -> None: ...
    def reset_section(self, section_name: str) -> None: ...
    def reset_all(self) -> None: ...
```

**Step 4: 精简 `EditSidebar`**

```python
# edit_sidebar.py (精简后)
class EditSidebar(QWidget):
    """编辑侧边栏 — 纯容器 + 布局 / Edit sidebar — pure container + layout."""

    def __init__(self, edit_session, parent=None):
        super().__init__(parent)
        self._registry = EditSectionRegistry.default_sections()
        self._router = EditSignalRouter()
        self._coordinator = EditSessionCoordinator(edit_session)

        sections = self._registry.create_all(edit_session)
        self._build_layout(sections)

        for config, widget in sections:
            self._router.connect_section(widget)

    def _build_layout(self, sections): ...  # 布局构建，约 50 行
```

---

### 3.5 `edit_curve_section.py` — EditCurveSection 曲线编辑区

**路径 / Path:** `src/iPhoto/gui/ui/widgets/edit_curve_section.py` (1165 行)

#### 当前职责分析 / Current Responsibility Analysis

- 📈 样条曲线数学计算（控制点管理、插值）
- 🎨 直方图绘制（`paintEvent` 约 80 行）
- 🖱️ 鼠标交互（拖拽控制点、命中检测）
- 📊 通道选择（RGB/R/G/B 切换）
- 🎚️ 输入级别滑块（黑白点调节）
- 🔗 信号协调（预览/提交/取消）

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/ui/widgets/
├── edit_curve_section.py         # ≈250 行 — 精简: 区域容器 + 信号
├── curve_graph.py                # ≈300 行 — 新建: CurveGraph 控件
├── curve_interaction.py          # ≈120 行 — 新建: 鼠标交互逻辑
├── input_level_sliders.py        # ≈100 行 — 新建: 级别滑块控件
└── _styled_combo_box.py          # ≈30 行  — 新建: 样式化下拉框
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `CurveGraph` 到独立文件**

`CurveGraph` 是最大的内部类（约 250 行），包含绘图和交互逻辑：

```python
# curve_graph.py
class CurveGraph(QWidget):
    """曲线图控件 — 绘制曲线与直方图 / Curve graph widget."""

    curveChanged = Signal(list)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent=None): ...
    def set_histogram(self, histogram: np.ndarray) -> None: ...
    def set_channel(self, channel: str) -> None: ...
    def paintEvent(self, event) -> None: ...
```

**Step 2: 提取鼠标交互到 `CurveInteraction`**

将 `mousePressEvent`, `mouseMoveEvent`, `mouseReleaseEvent` 中的命中检测和拖拽逻辑提取：

```python
# curve_interaction.py
class CurveInteraction:
    """曲线控制点交互逻辑 / Curve control point interaction logic."""

    def __init__(self, control_points: list):
        self._points = control_points
        self._dragging_index: int | None = None

    def hit_test(self, pos: QPointF, tolerance: float = 8.0) -> int | None: ...
    def start_drag(self, index: int, pos: QPointF) -> None: ...
    def update_drag(self, pos: QPointF) -> list: ...
    def end_drag(self) -> None: ...
```

**Step 3: 提取 `InputLevelSliders` 和 `_StyledComboBox`**

这两个小组件完全独立，直接移到各自文件：

```python
# input_level_sliders.py
class InputLevelSliders(QWidget):
    """黑白点级别滑块 / Black & white point level sliders."""
    blackPointChanged = Signal(int)
    whitePointChanged = Signal(int)
    ...

# _styled_combo_box.py
class StyledComboBox(QComboBox):
    """样式化下拉框 / Styled combo box with custom appearance."""
    ...
```

---

### 3.6 `gl_renderer.py` — GLRenderer OpenGL 渲染器

**路径 / Path:** `src/iPhoto/gui/ui/widgets/gl_renderer.py` (940 行)

#### 当前职责分析 / Current Responsibility Analysis

- 🔧 着色器编译与管理（`_compile_shader`, `_link_program`）
- 📐 VAO/VBO 几何体设置
- 🖼️ 纹理管理（图像纹理、LUT 纹理、叠加纹理）
- 🎛️ Uniform 状态管理（矩阵、参数）
- 🖥️ 渲染管线（主渲染、离屏渲染、叠加层渲染）
- 📊 LUT 纹理生成（曲线/色阶 → 1D 纹理）

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/ui/widgets/
├── gl_renderer.py                # ≈250 行 — 精简: 渲染协调器
├── gl_shader_manager.py          # ≈150 行 — 新建: 着色器编译/链接
├── gl_texture_manager.py         # ≈180 行 — 新建: 纹理生命周期管理
├── gl_lut_generator.py           # ≈120 行 — 新建: LUT 纹理生成
├── gl_uniform_state.py           # ≈80 行  — 新建: Uniform 参数状态
└── gl_offscreen.py               # ≈100 行 — 新建: 离屏 FBO 渲染
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `ShaderManager`**

着色器的加载、编译、链接是独立的生命周期：

```python
# gl_shader_manager.py
def load_shader_source(name: str) -> str:
    """从文件加载 GLSL 源码 / Load GLSL source from file."""
    ...

class ShaderManager:
    """着色器编译与程序管理 / Shader compilation & program management."""

    def __init__(self):
        self._programs: dict[str, int] = {}

    def compile_and_link(self, name: str, vert_src: str, frag_src: str) -> int: ...
    def use(self, name: str) -> None: ...
    def get_uniform_location(self, program: str, name: str) -> int: ...
    def cleanup(self) -> None: ...
```

**Step 2: 提取 `TextureManager`**

纹理的上传、绑定、删除是另一个独立生命周期：

```python
# gl_texture_manager.py
class TextureManager:
    """OpenGL 纹理生命周期管理 / OpenGL texture lifecycle management."""

    def upload(self, image: QImage, texture_unit: int = 0) -> int: ...
    def upload_1d(self, data: np.ndarray, texture_unit: int) -> int: ...
    def bind(self, texture_id: int, unit: int) -> None: ...
    def delete(self, texture_id: int) -> None: ...
    def cleanup_all(self) -> None: ...
```

**Step 3: 提取 `LutGenerator`**

LUT (查找表) 的数学计算完全独立于 OpenGL：

```python
# gl_lut_generator.py
class LutGenerator:
    """从曲线/色阶参数生成 LUT 数据 / Generate LUT data from curve/level params."""

    @staticmethod
    def from_curves(control_points: list, channel: str) -> np.ndarray: ...
    @staticmethod
    def from_levels(black: int, white: int, gamma: float) -> np.ndarray: ...
    @staticmethod
    def identity() -> np.ndarray: ...
```

**Step 4: 提取离屏渲染**

```python
# gl_offscreen.py
class OffscreenRenderer:
    """FBO 离屏渲染管理 / FBO offscreen render management."""

    def __init__(self, shader_manager, texture_manager):
        ...
    def render_to_image(self, width: int, height: int, ...) -> QImage: ...
    def _create_fbo(self, width, height) -> int: ...
    def _destroy_fbo(self, fbo_id: int) -> None: ...
```

> ⚠️ **注意 / Note:** `gl_image_viewer/offscreen.py` 已经存在。此处的离屏渲染侧重于 `GLRenderer` 内部的 FBO 管理，与 viewer 的离屏渲染不同。应通过命名或模块路径区分。

---

### 3.7 `widget.py` — GLImageViewer GL 图片查看器

**路径 / Path:** `src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py` (997 行)

#### 当前职责分析 / Current Responsibility Analysis

该模块已经进行过一次拆分（`gl_image_viewer/` 目录下有 `components.py`, `crop_logic.py`, `geometry.py`, `input_handler.py`, `offscreen.py`, `resources.py`, `utils.py`, `view_helpers.py`），但 `widget.py` 仍然过大：

- 🖥️ OpenGL Widget 生命周期（`initializeGL`, `paintGL`, `resizeGL`）
- 🔍 缩放/平移逻辑
- ✂️ 裁剪交互响应
- 🎛️ 调整参数应用
- 🖼️ 全屏模式管理
- ⏳ 加载状态叠加显示
- 📐 透视变换协调

#### 拆分方案 / Splitting Plan

```
src/iPhoto/gui/ui/widgets/gl_image_viewer/
├── widget.py                     # ≈300 行 — 精简: GL 生命周期 + 委托
├── components.py                 # (已存在)
├── crop_logic.py                 # (已存在)
├── geometry.py                   # (已存在)
├── input_handler.py              # (已存在)
├── offscreen.py                  # (已存在)
├── resources.py                  # (已存在)
├── utils.py                      # (已存在)
├── view_helpers.py               # (已存在)
├── zoom_controller.py            # ≈120 行 — 新建: 缩放/平移状态管理
├── adjustment_applicator.py      # ≈100 行 — 新建: 调整参数应用
├── loading_overlay.py            # ≈60 行  — 新建: 加载状态叠加
└── fullscreen_handler.py         # ≈80 行  — 新建: 全屏模式管理
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `ZoomController`**

缩放/平移是最核心的独立逻辑：

```python
# zoom_controller.py
class ZoomController:
    """缩放/平移状态管理 / Zoom & pan state management."""

    def __init__(self):
        self._zoom_level: float = 1.0
        self._pan_offset: QPointF = QPointF(0, 0)
        self._fit_mode: bool = True

    def zoom_to(self, level: float, anchor: QPointF) -> None: ...
    def zoom_by(self, delta: float, anchor: QPointF) -> None: ...
    def pan_by(self, delta: QPointF) -> None: ...
    def fit_to_viewport(self, image_size: QSize, viewport_size: QSize) -> None: ...
    def get_transform_matrix(self) -> QMatrix4x4: ...
```

**Step 2: 提取 `AdjustmentApplicator`**

```python
# adjustment_applicator.py
class AdjustmentApplicator:
    """调整参数 → 渲染器 Uniform 的映射
       Maps adjustment params to renderer uniforms."""

    def apply(self, renderer: GLRenderer, adjustments: dict) -> None: ...
    def apply_curves(self, renderer, curve_params) -> None: ...
    def apply_levels(self, renderer, level_params) -> None: ...
    def apply_white_balance(self, renderer, wb_params) -> None: ...
```

**Step 3: 提取 `LoadingOverlay` 和 `FullscreenHandler`**

```python
# loading_overlay.py
class LoadingOverlay:
    """加载状态叠加显示 / Loading state overlay."""
    def set_loading(self, is_loading: bool) -> None: ...
    def paint(self, painter: QPainter, rect: QRect) -> None: ...

# fullscreen_handler.py
class FullscreenHandler:
    """全屏模式管理 / Fullscreen mode management."""
    def __init__(self, widget: QWidget): ...
    def toggle_fullscreen(self) -> None: ...
    def enter_fullscreen(self) -> None: ...
    def exit_fullscreen(self) -> None: ...
```

---

### 3.8 `map_renderer.py` — MapRenderer 地图渲染器

**路径 / Path:** `maps/map_widget/map_renderer.py` (878 行)

#### 当前职责分析 / Current Responsibility Analysis

- 🗺️ 视口状态计算（`_ViewState` — 缩放级别、中心坐标、可见瓦片范围）
- 🧱 瓦片收集与裁剪（`_collect_visible_tiles`, `_cull_tiles`）
- 🎨 图层渲染（路网、水域、建筑、POI 等）
- 🏷️ 城市标注布局（碰撞检测、字体计算、优先级排序）
- 📐 几何体缓存管理

#### 拆分方案 / Splitting Plan

```
maps/map_widget/
├── map_renderer.py               # ≈250 行 — 精简: 渲染编排
├── viewport.py                   # ≈100 行 — 新建: 视口状态与计算
├── tile_collector.py             # ≈120 行 — 新建: 瓦片收集与裁剪
├── city_label_layout.py          # ≈180 行 — 新建: 城市标注布局
├── geometry.py                   # (已存在) 几何工具
├── layer.py                      # (已存在) 图层定义
└── tile_manager.py               # (已存在) 瓦片缓存
```

#### 详细拆分步骤 / Detailed Steps

**Step 1: 提取 `Viewport`**

```python
# viewport.py
@dataclass
class ViewState:
    """视口状态 / Viewport state."""
    center_lat: float
    center_lon: float
    zoom: float
    width: int
    height: int
    tile_range: tuple  # (min_x, min_y, max_x, max_y)

class Viewport:
    """视口计算 / Viewport calculations."""
    def compute_state(self, center, zoom, size) -> ViewState: ...
    def lat_lon_to_pixel(self, lat, lon, state) -> QPointF: ...
    def pixel_to_lat_lon(self, x, y, state) -> tuple[float, float]: ...
```

**Step 2: 提取 `TileCollector`**

```python
# tile_collector.py
class TileCollector:
    """可见瓦片收集与裁剪 / Visible tile collection & culling."""

    def collect_visible(self, state: ViewState) -> list[TileKey]: ...
    def cull_offscreen(self, tiles: list, viewport_rect: QRect) -> list: ...
    def compute_tile_geometry(self, tile_key, state) -> QRectF: ...
```

**Step 3: 提取 `CityLabelLayout`**

城市标注的布局算法是最复杂的独立逻辑：

```python
# city_label_layout.py
@dataclass
class CityAnnotation:
    name: str
    latitude: float
    longitude: float
    population: int

@dataclass
class RenderedCityLabel:
    annotation: CityAnnotation
    screen_pos: QPointF
    bounding_rect: QRectF
    font_size: int

class CityLabelLayout:
    """城市标注布局 — 碰撞检测与优先级排序
       City label layout — collision detection & priority sorting."""

    def layout(self, annotations: list[CityAnnotation],
               state: ViewState) -> list[RenderedCityLabel]:
        """计算不重叠的标注位置 / Compute non-overlapping label positions."""
        ...
    def _detect_collisions(self, labels) -> list: ...
    def _sort_by_priority(self, annotations) -> list: ...
```

---

### 3.9 `curve.py` — 曲线 Demo

**路径 / Path:** `demo/curve/curve.py` (1384 行)

#### 当前职责分析 / Current Responsibility Analysis

这是一个独立的 Demo 文件，包含 **7 个类** 和 **2 个顶层函数**，实际上是一个完整的迷你应用。

#### 拆分方案 / Splitting Plan

```
demo/curve/
├── main.py                       # ≈50 行  — 入口: 启动应用
├── curve_demo.py                 # ≈200 行 — CurvesDemo 主窗口
├── gl_viewer.py                  # ≈200 行 — GLImageViewer 控件
├── curve_graph.py                # ≈250 行 — CurveGraph 控件
├── histogram.py                  # ≈80 行  — 直方图计算函数
├── level_sliders.py              # ≈100 行 — InputLevelSliders 控件
└── widgets.py                    # ≈60 行  — StyledComboBox, IconButton
```

#### 重构建议 / Refactoring Recommendations

> 💡 **最佳方案 / Best approach:** Demo 文件应尽可能复用 `src/` 中的产品代码，而不是复制实现。重构后 Demo 应导入产品组件并只添加 Demo 特有的胶水代码。

```python
# demo/curve/main.py (理想状态)
from iPhoto.gui.ui.widgets.curve_graph import CurveGraph
from iPhoto.gui.ui.widgets.input_level_sliders import InputLevelSliders

class CurvesDemo(QWidget):
    """曲线 Demo — 复用产品组件 / Curve demo — reuses production components."""
    def __init__(self):
        self._curve_graph = CurveGraph()        # 来自产品代码
        self._sliders = InputLevelSliders()      # 来自产品代码
        self._viewer = DemoGLViewer()            # Demo 特有的简化查看器
        ...
```

---

### 3.10 `white balance.py` — 白平衡 Demo

**路径 / Path:** `demo/white balance/white balance.py` (998 行)

#### 当前职责分析 / Current Responsibility Analysis

类似 `curve.py`，包含 **7 个类** 的完整迷你应用。

#### 拆分方案 / Splitting Plan

```
demo/white_balance/              # ⚠️ 重命名: 去掉空格
├── main.py                       # ≈50 行  — 入口
├── wb_demo.py                    # ≈200 行 — WBMain 主窗口
├── gl_wb_viewer.py               # ≈200 行 — GLWBViewer 控件
├── sliders.py                    # ≈250 行 — WarmthSlider, TemperatureSlider, TintSlider
├── pipette.py                    # ≈40 行  — PipetteButton 控件
└── widgets.py                    # ≈30 行  — StyledComboBox
```

> ⚠️ **注意:** 目录名 `white balance` 含空格，建议重命名为 `white_balance` 以符合 Python 包命名规范。

#### 重构建议 / Refactoring Recommendations

与曲线 Demo 相同策略：尽量从 `src/` 导入已有组件，避免代码重复。

---

## 跨文件代码重复分析 / Cross-File Duplication Analysis

### 🔴 高优先级重复 / High Priority Duplications

| 重复组件 / Duplicated Component | 文件 A | 文件 B | 估计重复行 |
|-------------------------------|--------|--------|-----------|
| `InputLevelSliders` | `edit_curve_section.py` | `demo/curve/curve.py` | ~80 行 |
| `CurveGraph` | `edit_curve_section.py` | `demo/curve/curve.py` | ~250 行 |
| `StyledComboBox` | `edit_curve_section.py` | `demo/curve/curve.py` + `demo/white balance/white balance.py` | ~30 行 × 3 |
| `GLImageViewer` (简化版) | `gl_image_viewer/widget.py` | `demo/curve/curve.py` | ~200 行 |
| 滑块控件概念 | `edit_wb_section.py` | `demo/white balance/white balance.py` | ~150 行 |

### 建议消除策略 / Duplication Elimination Strategy

1. **产品代码先拆分** — 将 `InputLevelSliders`, `CurveGraph`, `StyledComboBox` 拆分为独立模块
2. **Demo 复用产品代码** — Demo 文件导入拆分后的产品组件
3. **提取共享控件库** — 在 `src/iPhoto/gui/ui/widgets/common/` 下创建可复用基础控件

```
src/iPhoto/gui/ui/widgets/common/
├── __init__.py
├── styled_combo_box.py        # StyledComboBox
├── icon_button.py             # IconButton
└── level_sliders.py           # InputLevelSliders (可被曲线和色阶共享)
```

---

## 实施优先级与路线图 / Implementation Priority & Roadmap

### 阶段一: 核心产品代码 (高优先级) 🔴

> **目标:** 拆分影响日常开发效率最大的产品代码文件

| 顺序 | 文件 | 复杂度 | 预估工时 | 风险 |
|------|------|--------|---------|------|
| 1 | `edit_sidebar.py` | 中 | 4h | 低 — 信号路由提取相对安全 |
| 2 | `edit_curve_section.py` | 中 | 3h | 低 — 组件边界清晰 |
| 3 | `thumbnail_loader.py` | 高 | 6h | 中 — 多线程逻辑需谨慎 |
| 4 | `asset_data_source.py` | 高 | 6h | 中 — Worker 线程安全需验证 |

### 阶段二: 渲染与库管理 (中优先级) 🟡

| 顺序 | 文件 | 复杂度 | 预估工时 | 风险 |
|------|------|--------|---------|------|
| 5 | `gl_renderer.py` | 高 | 8h | 高 — OpenGL 状态敏感 |
| 6 | `manager.py` | 中 | 5h | 中 — 信号/锁需仔细迁移 |
| 7 | `widget.py` | 中 | 4h | 中 — 已有部分拆分基础 |

### 阶段三: 地图与 Demo (低优先级) 🟢

| 顺序 | 文件 | 复杂度 | 预估工时 | 风险 |
|------|------|--------|---------|------|
| 8 | `map_renderer.py` | 中 | 4h | 低 — 独立模块 |
| 9 | `curve.py` | 低 | 2h | 极低 — Demo 代码 |
| 10 | `white balance.py` | 低 | 2h | 极低 — Demo 代码 |

### 总计预估 / Total Estimate

- **总工时:** ~44 小时
- **建议时间跨度:** 2-3 周（每阶段约 1 周）
- **每次拆分后需运行的测试:** 对应模块的单元测试 + GUI 冒烟测试

---

## 重构原则与注意事项 / Refactoring Principles & Guidelines

### ✅ 遵循原则 / Principles to Follow

1. **单一职责原则 (SRP)** — 每个文件/类只有一个被修改的理由
2. **依赖倒置原则 (DIP)** — 新模块通过接口/协议交互，而非具体类
3. **渐进重构** — 每次只拆分一个文件，确保测试通过后再继续
4. **保持公开接口不变** — 通过 `__init__.py` 的 re-export 保持向后兼容

### ⚠️ 注意事项 / Important Considerations

1. **信号槽迁移**
   - 拆分后的子模块如果需要发信号，需要继承 `QObject`
   - 信号连接的 `sender()` 会发生变化，需检查是否有代码依赖 `sender()`
   - 建议使用信号路由器模式集中管理

2. **线程安全**
   - `thumbnail_loader.py` 和 `asset_data_source.py` 涉及多线程
   - 拆分时需确保 `QMutex`/`QReadWriteLock` 的作用域不变
   - Worker 类的信号跨线程传递方式不能改变

3. **OpenGL 上下文**
   - `gl_renderer.py` 的所有 GL 调用必须在同一个 OpenGL 上下文中
   - 拆分的子模块不能在构造函数中调用 GL 函数
   - 建议使用延迟初始化 (`initializeGL` 中初始化子模块)

4. **向后兼容**
   - 在原文件的 `__init__.py` 或模块顶部添加 re-export：
   ```python
   # 向后兼容: 从新位置导入
   from .curve_graph import CurveGraph
   from .input_level_sliders import InputLevelSliders
   ```
   - 这允许旧代码继续工作，同时新代码可以直接导入新路径

5. **测试覆盖**
   - 每个拆分出的新模块应有对应的单元测试
   - 拆分前先编写集成测试，拆分后确保集成测试仍通过
   - 现有测试文件路径可能需要更新导入

6. **Demo 文件特殊处理**
   - Demo 文件 (`curve.py`, `white balance.py`) 优先级最低
   - 可以选择仅拆分文件结构，不改变行为
   - 长期目标: 复用产品组件，减少维护负担

---

> **下一步 / Next Steps:**
> 1. 在每个阶段开始前创建 feature branch
> 2. 每拆分一个文件后提交 PR 并运行 CI
> 3. 邀请团队 review 拆分后的模块边界
> 4. 更新 [ARCHITECTURE_DIAGRAMS.md](./ARCHITECTURE_DIAGRAMS.md) 中的模块依赖图

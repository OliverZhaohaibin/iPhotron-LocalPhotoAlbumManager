# 🔄 组件映射对照表 / Component Mapping Reference

> **版本 / Version:** 1.0  
> **创建日期 / Created:** 2026-02-08  
> **关联文档 / Related:** [MIGRATION_PLAN.md](./MIGRATION_PLAN.md) · [QML_FILE_STRUCTURE.md](./QML_FILE_STRUCTURE.md)

---

## 📑 目录 / Table of Contents

1. [Widget → QML 组件映射总表 / Complete Mapping Table](#1-widget--qml-组件映射总表--complete-mapping-table)
2. [页面视图映射 / View Mapping](#2-页面视图映射--view-mapping)
3. [核心组件映射 / Core Component Mapping](#3-核心组件映射--core-component-mapping)
4. [编辑器组件映射 / Editor Component Mapping](#4-编辑器组件映射--editor-component-mapping)
5. [控制器映射 / Controller Mapping](#5-控制器映射--controller-mapping)
6. [数据模型映射 / Data Model Mapping](#6-数据模型映射--data-model-mapping)
7. [后台任务映射 / Background Task Mapping](#7-后台任务映射--background-task-mapping)
8. [Qt 基类映射 / Qt Base Class Mapping](#8-qt-基类映射--qt-base-class-mapping)
9. [信号/槽映射 / Signal-Slot Mapping](#9-信号槽映射--signal-slot-mapping)
10. [样式与主题映射 / Style & Theme Mapping](#10-样式与主题映射--style--theme-mapping)

---

## 1. Widget → QML 组件映射总表 / Complete Mapping Table

### 📊 快速索引

| Widget 文件 (Python) | QML 文件 | 迁移策略 | 阶段 |
|---------------------|----------|---------|------|
| **页面视图** | | | |
| `main_window.py` → `MainWindow` | `Main.qml` | 重写 | P1 |
| `gallery_page.py` → `GalleryPage` | `views/GalleryView.qml` | 重写 | P2 |
| `gallery_grid_view.py` → `GalleryGridView` | 融入 `GalleryView.qml` | 合并 | P2 |
| `detail_page.py` → `DetailPage` | `views/DetailView.qml` | 重写 | P2 |
| `photo_map_view.py` → `PhotoMapView` | `views/MapView.qml` | 重写 | P3 |
| `albums_dashboard.py` → `AlbumsDashboard` | `views/DashboardView.qml` | 重写 | P3 |
| **核心组件** | | | |
| `asset_grid.py` → `AssetGrid` | `components/AssetGrid.qml` | 重写 | P2 |
| `asset_delegate.py` → `AssetGridDelegate` | `components/AssetGridDelegate.qml` | 重写 | P2 |
| `album_sidebar.py` → `AlbumSidebar` | `components/AlbumSidebar.qml` | 重写 | P2 |
| `filmstrip_view.py` → `FilmstripView` | `components/FilmstripView.qml` | 重写 | P2 |
| `player_bar.py` → `PlayerBar` | `components/PlayerBar.qml` | 重写 | P2 |
| `gl_image_viewer/` → `GLImageViewer` | `components/ImageViewer.qml` | 重写 | P2 |
| `video_area.py` → `VideoArea` | `components/VideoArea.qml` | 重写 | P2 |
| `info_panel.py` → `InfoPanel` | `components/InfoPanel.qml` | 重写 | P2 |
| `main_header.py` → `MainHeader` | `components/MainHeader.qml` | 重写 | P2 |
| `notification_toast.py` → `NotificationToast` | `components/NotificationToast.qml` | 重写 | P2 |
| `custom_title_bar.py` → `CustomTitleBar` | `components/CustomTitleBar.qml` | 重写 | P1 |
| `chrome_status_bar.py` → `ChromeStatusBar` | `components/ChromeStatusBar.qml` | 重写 | P2 |
| `live_badge.py` → `LiveBadge` | `components/LiveBadge.qml` | 重写 | P2 |
| `sliding_segmented_control.py` | `components/SlidingSegmented.qml` | 重写 | P2 |
| `collapsible_section.py` | `components/CollapsibleSection.qml` | 重写 | P2 |
| `flow_layout.py` → `FlowLayout` | `components/FlowLayout.qml` | 重写 | P3 |
| `custom_tooltip.py` → `CustomTooltip` | QML 内置 `ToolTip` | 替换 | P2 |
| `preview_window.py` → `PreviewWindow` | 独立 `Window` QML | 重写 | P3 |
| `dialogs.py` → 各种对话框 | `dialogs/*.qml` | 拆分重写 | P3 |
| **编辑器组件** | | | |
| `edit_sidebar.py` → `EditSidebar` | `components/EditSidebar.qml` | 重写 | P3 |
| `edit_topbar.py` → `EditTopbar` | `components/EditTopbar.qml` | 重写 | P3 |
| `edit_strip.py` → `EditStrip` | 融入 `EditView.qml` | 合并 | P3 |
| `edit_light_section.py` | `components/edit/EditLightSection.qml` | 重写 | P3 |
| `edit_color_section.py` | `components/edit/EditColorSection.qml` | 重写 | P3 |
| `edit_bw_section.py` | `components/edit/EditBWSection.qml` | 重写 | P3 |
| `edit_wb_section.py` | `components/edit/EditWBSection.qml` | 重写 | P3 |
| `edit_curve_section.py` | `components/edit/EditCurveSection.qml` | 重写 | P3 |
| `edit_levels_section.py` | `components/edit/EditLevelsSection.qml` | 重写 | P3 |
| `edit_selective_color_section.py` | `components/edit/EditSelectiveColor.qml` | 重写 | P3 |
| `gl_crop/` → `GLCropWidget` | Canvas / ShaderEffect | 重写 | P3 |
| **控制器（复制为 `_qml.py` 副本）** | | | |
| `header_controller.py` | 复制为 `header_controller_qml.py`，添加 `@Property` | 副本隔离 | P2 |
| `player_view_controller.py` | 复制为 `player_view_controller_qml.py`，添加 `@Property/@Slot` | 副本隔离 | P2 |
| `selection_controller.py` | 复制为 `selection_controller_qml.py`，添加 `@Slot` | 副本隔离 | P2 |
| `context_menu_controller.py` | 复制为 `context_menu_controller_qml.py`（QML Menu 替代） | 副本隔离 | P2 |
| `dialog_controller.py` | 复制为 `dialog_controller_qml.py`（QML Dialog 替代） | 副本隔离 | P3 |
| `export_controller.py` | 复制为 `export_controller_qml.py`，添加 `@Slot` | 副本隔离 | P3 |
| `share_controller.py` | 复制为 `share_controller_qml.py`，添加 `@Slot` | 副本隔离 | P3 |
| `status_bar_controller.py` | 复制为 `status_bar_controller_qml.py`，添加 `@Property` | 副本隔离 | P2 |
| `edit_*.py` (6 controllers) | 各自复制为 `edit_*_qml.py`，添加 `@Property/@Slot` | 副本隔离 | P3 |
| `window_theme_controller.py` | 复制为 `window_theme_controller_qml.py` → QML Theme | 副本隔离 | P1 |
| **协调器（复制为 `_qml.py` 副本）** | | | |
| `main_coordinator.py` | 复制为 `main_coordinator_qml.py`，添加 QML 桥接 | 副本隔离 | P1 |
| `navigation_coordinator.py` | 复制为 `navigation_coordinator_qml.py`，添加 `@Slot` | 副本隔离 | P2 |
| `playback_coordinator.py` | 复制为 `playback_coordinator_qml.py`，添加 `@Property/@Slot` | 副本隔离 | P2 |
| `edit_coordinator.py` | 复制为 `edit_coordinator_qml.py`，添加 `@Slot` | 副本隔离 | P3 |
| `view_router.py` | 复制为 `view_router_qml.py`，信号驱动 QML StackView | 副本隔离 | P1 |
| **数据模型（需 QML 适配的复制为 `_qml.py` 副本）** | | | |
| `album_tree_model.py` | 复制为 `album_tree_model_qml.py`，添加 `roleNames()` | 副本隔离 | P2 |
| `asset_cache_manager.py` | 共享 | 不变 | - |
| `edit_session.py` | 复制为 `edit_session_qml.py`，添加 `@Property` | 副本隔离 | P3 |
| `proxy_filter.py` | 共享 | 不变 | - |
| `roles.py` | 复制为 `roles_qml.py`，添加 `roleNames()` 映射 | 副本隔离 | P1 |
| **后台任务（共享不变）** | | | |
| 所有 `tasks/*.py` Worker | 共享 | 不变 | - |
| **委托（融入 QML）** | | | |
| `album_sidebar_delegate.py` | 融入 `AlbumSidebar.qml` delegate | 合并 | P2 |

---

## 2. 页面视图映射 / View Mapping

### 2.1 MainWindow → Main.qml

```
┌─────────────────────────────────────────────────────────────┐
│ Widget: MainWindow (QMainWindow)                            │
│ ├── Ui_MainWindow.setupUi()                                 │
│ ├── FramelessWindowManager                                  │
│ ├── QStackedWidget (页面切换)                                 │
│ │   ├── GalleryPage                                         │
│ │   ├── DetailPage                                          │
│ │   ├── PhotoMapView                                        │
│ │   └── AlbumsDashboard                                     │
│ ├── AlbumSidebar (QDockWidget)                              │
│ └── ChromeStatusBar                                         │
│                                                             │
│ ════════════════════════ ⇓ 迁移为 ⇓ ════════════════════════ │
│                                                             │
│ QML: Main.qml (ApplicationWindow)                           │
│ ├── header: CustomTitleBar {}                               │
│ ├── RowLayout                                               │
│ │   ├── AlbumSidebar {}                                     │
│ │   └── StackView (页面路由)                                  │
│ │       ├── GalleryView                                     │
│ │       ├── DetailView                                      │
│ │       ├── EditView                                        │
│ │       ├── MapView                                         │
│ │       └── DashboardView                                   │
│ ├── footer: ChromeStatusBar {}                              │
│ └── NotificationToast {}                                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 GalleryPage → GalleryView.qml

```
Widget 结构:                          QML 结构:
GalleryPage (QWidget)                 GalleryView.qml (Item)
├── QVBoxLayout                       ├── ColumnLayout
│   ├── MainHeader (QWidget)          │   ├── MainHeader {}
│   └── GalleryGridView (QWidget)     │   └── AssetGrid {
│       └── AssetGrid (QListView)     │       model: assetListVM
│           └── AssetGridDelegate     │       delegate: AssetGridDelegate {}
│              (QStyledItemDelegate)   │   }
└── [SelectionController 信号连接]      └── SelectionToolbar { visible: ... }
```

### 2.3 DetailPage → DetailView.qml

```
Widget 结构:                          QML 结构:
DetailPage (QWidget)                  DetailView.qml (Item)
├── QVBoxLayout                       ├── ColumnLayout
│   ├── 位置/时间标签                    │   ├── DetailHeader {}
│   ├── QStackedWidget                │   ├── Loader {
│   │   ├── GLImageViewer (OpenGL)    │   │   imageComponent: ImageViewer {}
│   │   └── VideoArea (FFmpeg)        │   │   videoComponent: VideoArea {}
│   ├── PlayerBar (QWidget)           │   │ }
│   └── FilmstripView (QListView)     │   ├── PlayerBar {}
└── InfoPanel (QDockWidget)           │   └── FilmstripView {}
                                      └── InfoPanel {}
```

### 2.4 编辑器 → EditView.qml

```
Widget 结构:                          QML 结构:
[分散在多个 Widget 中]                  EditView.qml (Item)
├── EditTopbar (QWidget)              ├── ColumnLayout
├── GLImageViewer (编辑模式)             │   ├── EditTopbar {}
├── GLCropWidget (裁剪)                │   ├── RowLayout
├── EditSidebar (QWidget)             │   │   ├── ImageViewer { editMode: true }
│   ├── EditLightSection              │   │   └── EditSidebar {}
│   ├── EditColorSection              │   │       ├── EditLightSection {}
│   ├── EditBWSection                 │   │       ├── EditColorSection {}
│   ├── EditWBSection                 │   │       ├── EditBWSection {}
│   ├── EditCurveSection              │   │       ├── EditWBSection {}
│   ├── EditLevelsSection             │   │       ├── EditCurveSection {}
│   └── EditSelectiveColorSection     │   │       ├── EditLevelsSection {}
└── EditStrip (QWidget)               │   │       └── EditSelectiveColor {}
                                      │   └── EditStrip {} (可选)
                                      └── [EditCoordinator 保留 Python]
```

---

## 3. 核心组件映射 / Core Component Mapping

### 3.1 AssetGrid (网格视图)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | `QListView` (ViewMode.IconMode) | `GridView` |
| **数据模型** | `AssetListViewModel` (QAbstractListModel) | 同（共享） |
| **委托渲染** | `AssetGridDelegate` (QStyledItemDelegate + QPainter) | `AssetGridDelegate.qml` (声明式) |
| **虚拟滚动** | `QListView` 内置 | `GridView` 内置 |
| **多选** | `SelectionController` + `QItemSelectionModel` | `SelectionController` + QML selection state |
| **右键菜单** | `ContextMenuController` → `QMenu` | `ContextMenuController` → QML `Menu` |
| **缩略图加载** | `ThumbnailLoader` (QThread) | `QQuickAsyncImageProvider` 或复用 |
| **拖拽** | `QDrag` | `DragHandler` + `DropArea` |

### 3.2 AlbumSidebar (相册侧边栏)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | `QTreeView` | `TreeView` (Qt 6.4+) |
| **数据模型** | `AlbumTreeModel` (QAbstractItemModel) | 同（共享） |
| **委托** | `AlbumSidebarDelegate` (QStyledItemDelegate) | 内嵌 `TreeViewDelegate` |
| **展开指示器** | `BranchIndicator.qml` (已有!) | 复用 |
| **右键菜单** | `AlbumSidebarMenu` (QMenu) | QML `Menu` + `MenuItem` |
| **拖拽排序** | 未实现 | `DelegateModel` + `DragHandler` |

### 3.3 ImageViewer (图片查看器)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | `QOpenGLWidget` (GLImageViewer) | `Flickable` + `Image` |
| **渲染** | OpenGL 3.3 Core Profile 着色器 | QML Scene Graph (GPU 加速) |
| **缩放** | 自定义 `ViewTransformController` | `PinchArea` + `WheelHandler` |
| **平移** | 鼠标事件 → 变换矩阵 | `Flickable` 内置 |
| **旋转** | 变换矩阵 | `Image.rotation` + `Behavior` |
| **编辑预览** | GLSL 片段着色器 | `ShaderEffect` + GLSL |
| **裁剪叠层** | `GLCropWidget` (OpenGL) | Canvas overlay + `DragHandler` |
| **高 DPI** | `devicePixelRatio` 处理 | QML 自动处理 |

### 3.4 VideoArea (视频播放)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | 自定义 QWidget + FFmpeg (PyAV) | `MediaPlayer` + `VideoOutput` |
| **解码** | 手动 FFmpeg 帧解码 | Qt Multimedia 后端 |
| **控制** | `PlayerBar` (自定义 QWidget) | `PlayerBar.qml` |
| **音频** | PyAV 音频流 | `AudioOutput` |
| **Live Photo** | 特殊处理（短视频循环） | `MediaPlayer` + `loops: MediaPlayer.Infinite` |

> **注意**: 如果 `MediaPlayer` 不支持某些编码格式，可保留 Python FFmpeg 解码层，通过 `QQuickImageProvider` 逐帧提供给 QML `AnimatedImage` 或自定义 `VideoOutput`。

### 3.5 FilmstripView (胶片条)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | `QListView` (水平) | `ListView` (orientation: Horizontal) |
| **委托** | `AssetGridDelegate` (缩小版) | QML delegate (内嵌) |
| **当前项高亮** | `QItemSelectionModel` + delegate 绘制 | `ListView.highlight` Component |
| **居中间距** | `SpacerProxyModel` | QML `header` / `footer` spacer |
| **缩略图大小** | `ThumbnailStripSlider` | QML `Slider` 绑定 `cellWidth` |

### 3.6 PlayerBar (播放控制条)

| 特性 | Widget 实现 | QML 实现 |
|------|-----------|---------|
| **基类** | 自定义 `QWidget` | QML `Item` + `RowLayout` |
| **进度条** | `QSlider` | `Slider` |
| **播放/暂停** | `QPushButton` | `ToolButton` + 图标切换 |
| **时间标签** | `QLabel` | `Text` 绑定 `position` / `duration` |
| **音量** | `QSlider` | `Slider` |
| **全屏** | `QPushButton` | `ToolButton` |

---

## 4. 编辑器组件映射 / Editor Component Mapping

### 4.1 编辑面板通用模式

**Widget 模式：**
```python
class EditLightSection(QWidget):
    valueChanged = Signal(str, float)

    def __init__(self):
        self.exposure_slider = QSlider(Qt.Horizontal)
        self.exposure_slider.valueChanged.connect(
            lambda v: self.valueChanged.emit("exposure", v / 100)
        )
```

**QML 模式：**
```qml
CollapsibleSection {
    title: qsTr("Light")
    Column {
        Slider {
            from: -3.0; to: 3.0
            value: editSession.exposure
            onMoved: editSession.exposure = value
        }
    }
}
```

### 4.2 各编辑面板映射

| 编辑面板 | Widget 控件 | QML 控件 | 数据绑定 |
|---------|-----------|---------|---------|
| **Light** | 6× `QSlider` | 6× `Slider` | `editSession.exposure` 等 |
| **Color** | 4× `QSlider` | 4× `Slider` | `editSession.saturation` 等 |
| **B&W** | 6× `QSlider` (通道) | 6× `Slider` | `editSession.bwRed` 等 |
| **White Balance** | 2× `QSlider` + `QPushButton`(吸管) | 2× `Slider` + `ToolButton` | `editSession.wbTemp` 等 |
| **Curves** | `QPainter` 绘制 + 鼠标拖拽 | `Canvas` + `MouseArea` | `editSession.curvePoints` |
| **Levels** | `QPainter` 直方图 + 拖拽手柄 | `Canvas` + `DragHandler` | `editSession.levelBlack` 等 |
| **Selective Color** | 颜色按钮 + 4× `QSlider` | `Button` 组 + 4× `Slider` | `editSession.selectiveColor` |

### 4.3 裁剪工具映射

| 特性 | Widget (`GLCropWidget`) | QML |
|------|----------------------|-----|
| 裁剪框绘制 | OpenGL overlay | `Canvas` 或 `Rectangle` overlay |
| 手柄拖拽 | 鼠标事件 + Hit Test | `DragHandler` × 8 (角 + 边) |
| 比例锁定 | 手动计算 | JS 约束逻辑 |
| 网格线 | OpenGL 线段 | `Canvas` 或 `Repeater` + `Rectangle` |
| 透视变换 | `perspective_math.py` | 保留 Python，结果传给 QML Transform |

---

## 5. 控制器映射 / Controller Mapping

### 5.1 迁移策略（`_qml.py` 副本隔离）

控制器**不修改原文件**，而是复制为 `_qml.py` 副本，在副本中添加 QML 适配：

| 暴露方式 | 用途 | 示例（在 `_qml.py` 副本中） |
|---------|------|------|
| `@Property(type, notify=signal)` | 只读状态绑定 | `header_controller_qml.py: locationText` |
| `@Slot(type)` | QML 调用 Python 方法 | `navigation_coordinator_qml.py: openAlbum(path)` |
| `Signal(type)` | Python 通知 QML 更新 | `view_router_qml.py: galleryViewShown` |
| Context Property | 全局注入 | `ctx.setContextProperty("appFacade", facade_qml)` |

### 5.2 控制器 `_qml.py` 副本清单

| 原文件 | QML 副本 | 需添加的 QML 适配 | 复杂度 |
|--------|---------|------------------|-------|
| `header_controller.py` | `header_controller_qml.py` | `@Property` for `locationText`, `timestampText` | 低 |
| `player_view_controller.py` | `player_view_controller_qml.py` | `@Property` for `currentImageSource`, `isVideo`; `@Slot` for `play()`, `pause()` | 中 |
| `selection_controller.py` | `selection_controller_qml.py` | `@Slot` for `toggleSelection(int)`; `@Property` for `isActive`, `count` | 中 |
| `context_menu_controller.py` | `context_menu_controller_qml.py` | 简化：QML 端直接构建 `Menu`，调用 `@Slot` | 低 |
| `dialog_controller.py` | `dialog_controller_qml.py` | 简化：QML 端使用 `FileDialog`，结果传给 `@Slot` | 低 |
| `status_bar_controller.py` | `status_bar_controller_qml.py` | `@Property` for `message`, `progress` | 低 |
| `export_controller.py` | `export_controller_qml.py` | `@Slot` for `exportCurrent(format, quality)` | 低 |
| `share_controller.py` | `share_controller_qml.py` | `@Slot` for `copyToClipboard()`, `revealInFinder()` | 低 |
| `edit_history_manager.py` | `edit_history_manager_qml.py` | `@Slot` for `undo()`, `redo()`; `@Property` for `canUndo`, `canRedo` | 低 |
| `edit_pipeline_loader.py` | 无（共享，内部使用） | 无需 QML 适配 | 无 |
| `edit_preview_manager.py` | `edit_preview_manager_qml.py` | `@Property` for `previewImage`; 或通过 ImageProvider | 中 |
| `edit_zoom_handler.py` | `edit_zoom_handler_qml.py` | `@Slot` for `zoomIn()`, `zoomOut()`, `fitToView()` | 低 |
| `edit_fullscreen_manager.py` | `edit_fullscreen_manager_qml.py` | `@Slot` for `enterFullscreen()`, `exitFullscreen()` | 低 |
| `edit_view_transition.py` | 无（QML StackView 自带转场） | 无需 QML 适配 | 无 |
| `window_theme_controller.py` | `window_theme_controller_qml.py` | 桥接到 QML `Theme` singleton | 低 |

> **原文件零修改**：Widget 入口继续使用原 `header_controller.py` 等，QML 入口使用 `header_controller_qml.py` 副本。

---

## 6. 数据模型映射 / Data Model Mapping

### 6.1 模型 `_qml.py` 副本策略

需要 QML 适配（`roleNames()`、`@Property`）的模型复制为 `_qml.py` 副本，其余共享不变：

| 模型 | 基类 | QML 处理方式 | 副本文件 |
|------|------|-----------|--------|
| `AssetListViewModel` | `QAbstractListModel` | **复制副本** | `asset_list_viewmodel_qml.py` |
| `AlbumTreeModel` | `QAbstractItemModel` | **复制副本** | `album_tree_model_qml.py` |
| `EditSession` | `QObject` | **复制副本** | `edit_session_qml.py` |
| `Roles` | `IntEnum` | **复制副本** | `roles_qml.py`（添加 roleNames 字典） |
| `ProxyFilterModel` | `QSortFilterProxyModel` | 共享不变 | 无 |
| `SpacerProxyModel` | `QAbstractListModel` | 共享不变 | 无 |
| `AssetCacheManager` | - | 共享不变 | 无 |

### 6.2 roleNames() 在 `_qml.py` 副本中实现

QML 通过 `roleNames()` 将 C++ role enum 映射为 JS 属性名。
**此方法仅在 `_qml.py` 副本中添加，原文件不修改：**

```python
# src/iPhoto/gui/ui/models/roles_qml.py  (复制自 roles.py)
# 在副本中添加 roleNames 映射字典
class Roles(IntEnum):
    REL = Qt.UserRole + 1
    ABS = Qt.UserRole + 2
    IS_IMAGE = Qt.UserRole + 3
    IS_VIDEO = Qt.UserRole + 4
    IS_LIVE = Qt.UserRole + 5
    FEATURED = Qt.UserRole + 6
    # ...

# 新增: QML 专用映射字典
ROLE_NAMES: dict[int, bytes] = {
    Qt.DisplayRole: b"display",
    Qt.DecorationRole: b"decoration",
    Roles.REL: b"rel",
    Roles.ABS: b"abs",
    Roles.IS_IMAGE: b"isImage",
    Roles.IS_VIDEO: b"isVideo",
    Roles.IS_LIVE: b"isLive",
    Roles.FEATURED: b"featured",
    Roles.LIVE_MOTION_REL: b"liveMotionRel",
    Roles.LIVE_MOTION_ABS: b"liveMotionAbs",
    Roles.SIZE: b"size",
    Roles.DT: b"dt",
    Roles.LOCATION: b"location",
    Roles.INFO: b"info",
    Roles.ASSET_ID: b"assetId",
}
```

```python
# src/iPhoto/gui/viewmodels/asset_list_viewmodel_qml.py  (复制自原文件)
from iPhoto.gui.ui.models.roles_qml import ROLE_NAMES

class AssetListViewModelQml(QAbstractListModel):
    """QML-adapted copy with roleNames() and @Property."""

    def roleNames(self) -> dict[int, bytes]:
        names = super().roleNames()
        names.update(ROLE_NAMES)
        return names
```

**QML 中使用：**
```qml
delegate: Item {
    // 这些属性名来自 _qml.py 副本的 roleNames()
    required property string abs
    required property bool isLive
    required property bool featured
    // ...
}
```

### 6.3 EditSession `_qml.py` 副本

`edit_session_qml.py` 在原文件基础上添加 `@Property` 供 QML 双向绑定：

```python
# src/iPhoto/gui/ui/models/edit_session_qml.py  (复制自 edit_session.py)
class EditSessionQml(QObject):
    """QML-adapted copy with @Property for bidirectional binding."""
    exposureChanged = Signal()

    @Property(float, notify=exposureChanged)
    def exposure(self) -> float:
        return self._exposure

    @exposure.setter
    def exposure(self, value: float) -> None:
        if self._exposure != value:
            self._exposure = value
            self.exposureChanged.emit()
```

> **原文件 `edit_session.py` 零修改**，Widget 入口继续使用它。

---

## 7. 后台任务映射 / Background Task Mapping

### 7.1 策略：全部保留 Python，不创建副本

所有后台 Worker 保留在 Python 中，**不需要 `_qml.py` 副本**（Worker 不直接暴露给 QML）。
QML 通过 `_qml.py` 副本的 ViewModel/Coordinator 的 signal 接收 Worker 结果。

| Worker | 状态 | QML 交互方式 |
|--------|------|-------------|
| `AssetLoaderWorker` | 共享不变 | 结果通过 `AssetListViewModelQml` model 通知 |
| `ThumbnailLoader` | 共享不变 / 替换为 `QQuickAsyncImageProvider` | `Image.source = "image://thumbnails/..."` |
| `ImageLoadWorker` | 共享不变 | 结果通过 `player_view_controller_qml` Property |
| `PreviewRenderWorker` | 共享不变 | 结果通过 `edit_preview_manager_qml` Property |
| `VideoFrameGrabber` | 共享不变 | 结果通过 Signal → QML 更新 |
| `ImportWorker` | 共享不变 | 进度通过 `facade_qml.scanProgress` Signal |
| `MoveWorker` | 共享不变 | 完成通知通过 Signal |
| `IncrementalRefreshWorker` | 共享不变 | 结果通过 model 更新 |
| `ThumbnailGeneratorWorker` | 共享不变 | 生成后通过 ImageProvider 可用 |
| `EditSidebarPreviewWorker` | 共享不变 | 结果通过 Property 或 ImageProvider |

### 7.2 ThumbnailProvider 桥接

```
Widget 模式:                              QML 模式:
ThumbnailLoader (QThread)                 ThumbnailProvider (QQuickAsyncImageProvider)
    │                                         │
    ├── pixmapReady(path, QPixmap)           ├── requestImageResponse(id, size)
    │       │                                 │       │
    │       ▼                                 │       ▼
    │   AssetGridDelegate.paint()            │   QML Image { source: "image://..." }
    │   (QPainter 手动绘制)                    │   (QML Scene Graph 自动渲染)
    │                                         │
    └── 缓存: AssetCacheManager              └── 缓存: 可复用 AssetCacheManager
```

---

## 8. Qt 基类映射 / Qt Base Class Mapping

### 8.1 Widget → QML 元素对照

| Widget 类 | QML 元素 | 说明 |
|-----------|---------|------|
| `QMainWindow` | `ApplicationWindow` | 主窗口 |
| `QWidget` | `Item` / `Rectangle` | 通用容器 |
| `QLabel` | `Text` / `Label` | 文本显示 |
| `QPushButton` | `Button` / `ToolButton` | 按钮 |
| `QSlider` | `Slider` | 滑块 |
| `QScrollArea` | `ScrollView` / `Flickable` | 滚动区域 |
| `QListView` | `ListView` | 列表视图 |
| `QGridLayout` + `QListView` | `GridView` | 网格视图 |
| `QTreeView` | `TreeView` (Qt 6.4+) | 树形视图 |
| `QStackedWidget` | `StackView` / `SwipeView` | 页面栈 |
| `QSplitter` | `SplitView` | 分割视图 |
| `QTabWidget` | `TabBar` + `StackLayout` | 标签页 |
| `QToolBar` | `ToolBar` | 工具栏 |
| `QMenuBar` | `MenuBar` | 菜单栏 |
| `QMenu` | `Menu` + `MenuItem` | 菜单 |
| `QFileDialog` | `FileDialog` (Qt.labs / QtQuick.Dialogs) | 文件对话框 |
| `QMessageBox` | `MessageDialog` / `Dialog` | 消息对话框 |
| `QProgressBar` | `ProgressBar` | 进度条 |
| `QCheckBox` | `CheckBox` | 复选框 |
| `QComboBox` | `ComboBox` | 下拉框 |
| `QLineEdit` | `TextField` | 输入框 |
| `QTextEdit` | `TextArea` | 多行文本 |
| `QDockWidget` | 自定义可拖拽 `Item` | 停靠面板 |
| `QOpenGLWidget` | `ShaderEffect` / `Canvas` | OpenGL 渲染 |
| `QGraphicsView` | `Flickable` + 子元素 | 图形视图 |

### 8.2 布局映射

| Widget 布局 | QML 布局 | 说明 |
|------------|---------|------|
| `QVBoxLayout` | `ColumnLayout` | 垂直布局 |
| `QHBoxLayout` | `RowLayout` | 水平布局 |
| `QGridLayout` | `GridLayout` | 网格布局 |
| `QFormLayout` | `GridLayout` (2 列) | 表单布局 |
| `QStackedLayout` | `StackLayout` | 层叠布局 |
| `FlowLayout` (自定义) | `Flow` | 流式布局 |
| `addStretch()` | `Item { Layout.fillWidth: true }` | 弹性间距 |
| `setContentsMargins()` | `anchors.margins` | 内边距 |
| `setSpacing()` | `spacing` 属性 | 间距 |

---

## 9. 信号/槽映射 / Signal-Slot Mapping

### 9.1 Python → QML 信号连接

**Widget 方式：**
```python
self.facade.scanProgress.connect(self._on_scan_progress)
```

**QML 方式：**
```qml
Connections {
    target: appFacade
    function onScanProgress(path, current, total) {
        statusBar.progress = current / total
    }
}
```

### 9.2 QML → Python 调用

**Widget 方式：**
```python
button.clicked.connect(lambda: self.coordinator.open_album(path))
```

**QML 方式：**
```qml
Button {
    onClicked: navigationCoord.openAlbum(pathString)
}
```

### 9.3 关键信号映射表

| 信号 | 来源 (Python) | Widget 接收 | QML 接收 |
|------|-------------|-----------|---------|
| `albumOpened(Path)` | `AppFacade` / `AppFacadeQml` | `connect()` in coordinator | `Connections { target: appFacade }` |
| `scanProgress(Path, int, int)` | `AppFacade` / `AppFacadeQml` | `StatusBarController` | QML `ChromeStatusBar` via `_qml` |
| `galleryViewShown` | `ViewRouter` / `ViewRouterQml` | `connect()` in coordinator | QML `StackView` 切换 |
| `detailViewShown` | `ViewRouter` / `ViewRouterQml` | `connect()` in coordinator | QML `StackView.push` |
| `assetChanged(int)` | `PlaybackCoordinator` / `*Qml` | `connect()` in coordinator | QML `Connections` |
| `dataChanged` | `AssetListViewModel` / `*Qml` | `QListView` 自动 | `GridView` / `ListView` 自动 |
| `valuesChanged` | `EditSession` / `EditSessionQml` | `connect()` in edit controller | QML property binding 自动 |

> **Widget** 使用原 Python 类（`AppFacade`, `ViewRouter` 等），
> **QML** 使用 `_qml.py` 副本（`AppFacadeQml`, `ViewRouterQml` 等）。
> 信号名称相同，但实例完全独立。

---

## 10. 样式与主题映射 / Style & Theme Mapping

### 10.1 当前样式实现

```python
# Widget 方式: QSS + QPalette
app.setStyleSheet("""
    QWidget { background-color: #1e1e1e; color: #e0e0e0; }
    QPushButton { background-color: #3a3a3a; border-radius: 4px; }
""")
```

### 10.2 QML 主题实现

```qml
// Theme.qml (Singleton)
pragma Singleton
import QtQuick

QtObject {
    property string mode: windowThemeControllerQml.currentTheme  // 绑定 _qml 副本

    readonly property color bgPrimary: mode === "dark" ? "#1e1e1e" : "#ffffff"
    // ...
}

// 使用方式:
Rectangle {
    color: Theme.bgPrimary
    Text {
        color: Theme.textColor
        font.pixelSize: Theme.fontSizeNormal
    }
}
```

### 10.3 动画映射

| Widget 动画 | QML 动画 |
|------------|---------|
| `QPropertyAnimation` | `PropertyAnimation` / `NumberAnimation` |
| `QParallelAnimationGroup` | `ParallelAnimation` |
| `QSequentialAnimationGroup` | `SequentialAnimation` |
| `QTimeLine` | `Timer` + `NumberAnimation` |
| 手写插值 | `Behavior on property { ... }` |
| `QEasingCurve` | `easing.type: Easing.InOutQuad` |

---

> **维护者 / Maintainer:** iPhotron Team  
> **最后更新 / Last Updated:** 2026-02-08

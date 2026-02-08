# 📋 iPhotron Widget → QML 迁移方案 / Migration Plan

> **版本 / Version:** 1.0  
> **创建日期 / Created:** 2026-02-08  
> **项目 / Project:** iPhotron – Local Photo Album Manager  
> **目标 / Goal:** 将现有 PySide6 传统 Widget 界面迁移至纯 QML 实现，保留双入口

---

## 📑 目录 / Table of Contents

1. [执行摘要 / Executive Summary](#1-执行摘要--executive-summary)
2. [迁移目标与原则 / Migration Goals & Principles](#2-迁移目标与原则--migration-goals--principles)
3. [架构对比 / Architecture Comparison](#3-架构对比--architecture-comparison)
4. [迁移阶段总览 / Migration Phases Overview](#4-迁移阶段总览--migration-phases-overview)
5. [Phase 1: 基础设施搭建 / Infrastructure Setup](#5-phase-1-基础设施搭建--infrastructure-setup)
6. [Phase 2: 核心视图迁移 / Core Views Migration](#6-phase-2-核心视图迁移--core-views-migration)
7. [Phase 3: 编辑与高级功能 / Editing & Advanced Features](#7-phase-3-编辑与高级功能--editing--advanced-features)
8. [Phase 4: 整合与优化 / Integration & Optimization](#8-phase-4-整合与优化--integration--optimization)
9. [双入口设计 / Dual Entry Point Design](#9-双入口设计--dual-entry-point-design)
10. [风险评估与缓解 / Risk Assessment & Mitigation](#10-风险评估与缓解--risk-assessment--mitigation)
11. [验收标准 / Acceptance Criteria](#11-验收标准--acceptance-criteria)

---

## 1. 执行摘要 / Executive Summary

iPhotron 当前使用 PySide6 传统 Widget（`QMainWindow` / `QWidget` / `QGraphicsView`）构建 GUI 层。本方案旨在将所有 UI 层迁移至 **纯 QML** 实现，同时：

- **保留**现有 Widget 入口（`iphoto-gui` → `src/iPhoto/gui/main.py`）——**零修改**
- **新增** QML 入口（`iphoto-qml` → `src/iPhoto/gui/main_qml.py`）
- **共享**底层业务逻辑（Domain、Application、Infrastructure 层不变）
- **完全隔离** GUI 层：所有需要 QML 适配的 Python 文件均**复制为 `_qml.py` 副本**，QML 入口仅使用副本，原文件不做任何修改

```
迁移范围: 仅 src/iPhoto/gui/ui/ 目录（视图层）
不变范围: domain/, application/, infrastructure/, core/, di/, events/, library/
不变范围: gui/viewmodels/, gui/coordinators/, gui/facade.py（原文件零修改）
新增范围: gui/*_qml.py 副本（QML 专用，添加 @Property/@Slot/roleNames）
```

### ⚡ 隔离策略核心原则

> **凡是需要为 QML 添加 `@Property`、`@Slot`、`roleNames()` 等适配的 Python 文件，
> 一律复制为 `{原文件名}_qml.py`，QML 入口仅导入 `_qml` 副本。
> Widget 入口继续使用原文件，两套实现完全隔离、互不影响。**

---

## 2. 迁移目标与原则 / Migration Goals & Principles

### 🎯 目标

| # | 目标 | 说明 |
|---|------|------|
| G1 | 纯 QML 界面 | 所有视觉元素用 QML 声明式语法实现 |
| G2 | 双入口共存 | `main` (Widget) 和 `main-qml` (QML) 可独立启动 |
| G3 | 共享业务逻辑 | Service / Repository 层零重复；ViewModel / Coordinator / Facade 通过 `_qml.py` 副本隔离 |
| G4 | 功能对等 | QML 版本实现与 Widget 版本完全相同的功能 |
| G5 | 渐进式迁移 | 可按阶段独立交付，每阶段均可运行 |

### 📐 原则

1. **QML-First UI**：所有布局、动画、主题均在 QML 中声明
2. **Python Backend**：业务逻辑保留在 Python 中，通过 `_qml.py` 副本中的 `QObject` 属性暴露
3. **Signal/Slot 桥接**：Python ↔ QML 通过 Qt 的 signal/slot 和 property 系统通信
4. **不修改 Domain/Infra**：迁移仅影响 GUI 层
5. **不修改原 GUI 文件**：所有需要 QML 适配的 py 文件均复制为 `_qml.py` 副本
6. **可回退**：任何阶段均可切回 Widget 入口，原文件完全不受影响

---

## 3. 架构对比 / Architecture Comparison

### 3.1 当前 Widget 架构

```
┌─────────────────────────────────────────────────┐
│                    GUI Layer                     │
│  ┌───────────┐  ┌────────────┐  ┌────────────┐  │
│  │  Widgets   │  │ Controllers│  │  Delegates  │  │
│  │ (QWidget)  │  │ (Python)   │  │ (QPainter)  │  │
│  └─────┬─────┘  └─────┬──────┘  └─────┬──────┘  │
│        │              │               │          │
│  ┌─────┴──────────────┴───────────────┴──────┐   │
│  │           Coordinators (Python)           │   │
│  │  Main / Navigation / Playback / Edit      │   │
│  └─────────────────┬─────────────────────────┘   │
│                    │                             │
│  ┌─────────────────┴─────────────────────────┐   │
│  │        ViewModels (QAbstractListModel)    │   │
│  └─────────────────┬─────────────────────────┘   │
│                    │                             │
│  ┌─────────────────┴─────────────────────────┐   │
│  │           AppFacade (QObject)             │   │
│  └───────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│    Application / Domain / Infrastructure        │
│    (Use Cases, Repositories, Services)          │
└─────────────────────────────────────────────────┘
```

### 3.2 目标 QML 架构

```
┌─────────────────────────────────────────────────┐
│                 QML UI Layer                     │
│  ┌───────────┐  ┌────────────┐  ┌────────────┐  │
│  │ QML Views  │  │ QML Comps  │  │  JS Logic   │  │
│  │ (.qml)     │  │ (.qml)     │  │ (minimal)   │  │
│  └─────┬─────┘  └─────┬──────┘  └─────┬──────┘  │
│        │              │               │          │
│        └──────────────┼───────────────┘          │
│                       │ context properties       │
│  ┌────────────────────┴──────────────────────┐   │
│  │     QML Bridge (_qml.py 副本, Python)     │   │
│  │  Coordinators_qml + ViewModels_qml       │   │
│  │  + Facade_qml (添加 @Property/@Slot)     │   │
│  └────────────────────┬──────────────────────┘   │
│                       │                          │
│  ┌────────────────────┴──────────────────────┐   │
│  │      ViewModels_qml (QAbstractListModel)  │   │
│  │      (副本，添加 roleNames/@Property)      │   │
│  └────────────────────┬──────────────────────┘   │
│                       │                          │
│  ┌────────────────────┴──────────────────────┐   │
│  │           AppFacade (QObject)             │   │
│  └───────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│    Application / Domain / Infrastructure        │
│    (Use Cases, Repositories, Services) [不变]    │
└─────────────────────────────────────────────────┘
```

### 3.3 关键差异

| 维度 | Widget 方式 | QML 方式 |
|------|-----------|---------|
| **布局** | Python 代码 (`QVBoxLayout`, `addWidget`) | QML 声明式 (`ColumnLayout`, `RowLayout`) |
| **样式** | QSS 样式表 + `QPalette` | QML `Style` / `Material` / 内联属性 |
| **动画** | `QPropertyAnimation` / 手写 | `Behavior`, `NumberAnimation`, `ParallelAnimation` |
| **列表渲染** | `QListView` + `QStyledItemDelegate` | `ListView` + QML `delegate` Component |
| **绘制** | `QPainter` / OpenGL Widget | `Canvas` / `ShaderEffect` / `QtQuick3D` |
| **数据绑定** | 手动 `connect(signal, slot)` | QML 声明式 property binding |
| **主题** | 运行时切换 QSS | Material / Universal Style + 自定义 Theme |
| **图像查看** | `QOpenGLWidget` (`GLImageViewer`) | `Image` + `PinchArea` 或 `ShaderEffect` |

---

## 4. 迁移阶段总览 / Migration Phases Overview

```
Phase 1                 Phase 2                 Phase 3                 Phase 4
基础设施搭建            核心视图迁移             编辑与高级功能          整合与优化
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
│ QML 入口              │ Gallery Grid          │ Edit Sidebar          │ 性能优化
│ QML Engine 初始化      │ Album Sidebar         │ Curve/Levels/WB      │ 主题系统
│ ViewModel 适配        │ Detail Page           │ Crop Tool             │ 无障碍
│ Theme 基础            │ Filmstrip             │ Map View              │ 测试覆盖
│ 路由框架              │ Player Bar            │ Export/Share          │ 文档完善
│                       │ Status Bar            │ Preview Window        │
```

---

## 5. Phase 1: 基础设施搭建 / Infrastructure Setup

### 5.1 QML 入口文件

**新建文件**: `src/iPhoto/gui/main_qml.py`

```python
"""QML entry point for iPhotron."""
import sys
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuickControls2 import QQuickStyle

from iPhoto.di.container import DependencyContainer
from iPhoto.events.bus import EventBus
# ... 其余 DI 注册与 Phase 1 main.py 相同 ...


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    app = QGuiApplication(argv)          # 注意: QGuiApplication 而非 QApplication
    QQuickStyle.setStyle("Material")

    # ── Phase 1: DI 容器初始化（与 Widget 入口共享） ──
    container = DependencyContainer()
    _register_infrastructure(container)
    _register_application(container)

    # ── Phase 2: QML Engine ──
    engine = QQmlApplicationEngine()

    # 将 Python QObject 注入 QML context
    ctx = engine.rootContext()
    ctx.setContextProperty("appFacade", facade)
    ctx.setContextProperty("assetListVM", asset_list_vm)
    ctx.setContextProperty("albumVM", album_vm)
    ctx.setContextProperty("navigationCoord", navigation_coord)
    ctx.setContextProperty("viewRouter", view_router)

    # 加载主 QML 文件
    qml_dir = Path(__file__).parent / "ui" / "qml"
    engine.load(qml_dir / "Main.qml")

    if not engine.rootObjects():
        return -1

    return app.exec()
```

### 5.2 pyproject.toml 入口注册

```toml
[project.scripts]
iphoto     = "iPhoto.cli:app"
iphoto-gui = "iPhoto.gui.main:main"        # 传统 Widget 入口（保留）
iphoto-qml = "iPhoto.gui.main_qml:main"    # 新 QML 入口
```

### 5.3 QML 目录结构初始化

```
src/iPhoto/gui/ui/qml/
├── Main.qml                    # QML 应用根组件
├── Theme.qml                   # 全局主题定义 (singleton)
├── qmldir                      # QML 模块注册文件
│
├── views/                      # 页面级视图
│   ├── GalleryView.qml         # 相册网格页
│   ├── DetailView.qml          # 单图详情页
│   ├── EditView.qml            # 编辑器页
│   ├── MapView.qml             # 地图页
│   └── DashboardView.qml       # 相册仪表盘
│
├── components/                 # 可复用组件
│   ├── AlbumSidebar.qml        # 左侧导航树
│   ├── AssetGrid.qml           # 缩略图网格
│   ├── AssetGridDelegate.qml   # 网格项渲染
│   ├── FilmstripView.qml       # 底部胶片条
│   ├── PlayerBar.qml           # 视频播放控制
│   ├── ImageViewer.qml         # 图片查看器
│   ├── VideoArea.qml           # 视频播放区域
│   ├── EditSidebar.qml         # 编辑参数面板
│   ├── EditTopbar.qml          # 编辑器顶栏
│   ├── InfoPanel.qml           # 元数据面板
│   ├── MainHeader.qml          # 主界面顶栏
│   ├── NotificationToast.qml   # 提示消息
│   ├── CustomTitleBar.qml      # 自定义标题栏
│   ├── ChromeStatusBar.qml     # 自定义状态栏
│   ├── LiveBadge.qml           # Live Photo 标识
│   ├── BranchIndicator.qml     # 树展开指示器（已有）
│   └── SlidingSegmented.qml    # 分段选择器
│
├── components/edit/            # 编辑子面板
│   ├── EditLightSection.qml
│   ├── EditColorSection.qml
│   ├── EditBWSection.qml
│   ├── EditWBSection.qml
│   ├── EditCurveSection.qml
│   ├── EditLevelsSection.qml
│   ├── EditSelectiveColor.qml
│   └── CollapsibleSection.qml
│
├── dialogs/                    # 对话框
│   ├── OpenAlbumDialog.qml
│   ├── BindLibraryDialog.qml
│   └── ErrorDialog.qml
│
└── styles/                     # 样式
    ├── Colors.qml              # 颜色常量
    ├── Fonts.qml               # 字体常量
    └── Dimensions.qml          # 尺寸常量
```

### 5.4 ViewModel QML 副本（`_qml.py` 隔离）

现有 ViewModel 已继承 `QAbstractListModel`，但 QML 需要额外的 `roleNames()`、`@Property` 等适配。
**不修改原文件**，而是复制为 `_qml.py` 副本，在副本中做 QML 适配：

**需要创建的 `_qml.py` 副本清单：**

| 原文件 | QML 副本 | 添加内容 |
|--------|---------|---------|
| `viewmodels/asset_list_viewmodel.py` | `viewmodels/asset_list_viewmodel_qml.py` | `roleNames()`, `@Property(count, isEmpty)` |
| `viewmodels/asset_data_source.py` | `viewmodels/asset_data_source_qml.py` | `@Property` 暴露加载状态 |
| `viewmodels/album_viewmodel.py` | `viewmodels/album_viewmodel_qml.py` | `@Slot` / `@Property` |
| `facade.py` | `facade_qml.py` | `@Property` 暴露状态给 QML |
| `coordinators/view_router.py` | `coordinators/view_router_qml.py` | `@Property(isGallery, isDetail, isEdit)` |
| `coordinators/navigation_coordinator.py` | `coordinators/navigation_coordinator_qml.py` | `@Slot(openAlbum, openAllPhotos)` |
| `coordinators/playback_coordinator.py` | `coordinators/playback_coordinator_qml.py` | `@Slot/@Property` |
| `coordinators/edit_coordinator.py` | `coordinators/edit_coordinator_qml.py` | `@Slot/@Property` |
| `coordinators/main_coordinator.py` | `coordinators/main_coordinator_qml.py` | QML 桥接方法 |
| `ui/models/edit_session.py` | `ui/models/edit_session_qml.py` | `@Property` 双向绑定 |
| `ui/models/roles.py` | `ui/models/roles_qml.py` | 添加 `roleNames()` 映射字典 |
| `ui/models/album_tree_model.py` | `ui/models/album_tree_model_qml.py` | `roleNames()` |

**1. `asset_list_viewmodel_qml.py` 副本示例**

```python
# src/iPhoto/gui/viewmodels/asset_list_viewmodel_qml.py
# 复制自 asset_list_viewmodel.py，添加 QML 适配
from PySide6.QtCore import Property, Signal

class AssetListViewModelQml(QAbstractListModel):
    """QML-adapted copy of AssetListViewModel with roleNames and Properties."""
    countChanged = Signal()

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self.rowCount()

    def roleNames(self) -> dict[int, bytes]:
        """Map role enums to QML-accessible property names."""
        names = super().roleNames()
        names.update({
            Roles.REL:             b"rel",
            Roles.ABS:             b"abs",
            Roles.IS_IMAGE:        b"isImage",
            Roles.IS_VIDEO:        b"isVideo",
            Roles.IS_LIVE:         b"isLive",
            Roles.FEATURED:        b"featured",
            # ... 其余 roles ...
        })
        return names
```

**2. `navigation_coordinator_qml.py` 副本示例**

```python
# src/iPhoto/gui/coordinators/navigation_coordinator_qml.py
# 复制自 navigation_coordinator.py，添加 @Slot 供 QML 调用
from PySide6.QtCore import Slot

class NavigationCoordinatorQml(QObject):
    """QML-adapted copy of NavigationCoordinator with @Slot decorators."""

    @Slot(str)
    def openAlbum(self, path: str) -> None:
        self.open_album(Path(path))

    @Slot()
    def openAllPhotos(self) -> None:
        self.open_all_photos()
```

> **关键**: `main.py` (Widget) 继续 `from .viewmodels.asset_list_viewmodel import AssetListViewModel`，
> `main_qml.py` (QML) 则 `from .viewmodels.asset_list_viewmodel_qml import AssetListViewModelQml`。
> 两个入口完全隔离，互不影响。

### 5.5 QML 路由框架

```qml
// Main.qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1400; height: 900
    title: "iPhotron"

    // 视图路由器 (StackView)
    StackView {
        id: viewStack
        anchors.fill: parent
        initialItem: galleryView

        Component { id: galleryView;   GalleryView {} }
        Component { id: detailView;    DetailView {} }
        Component { id: editView;      EditView {} }
        Component { id: mapView;       MapView {} }
        Component { id: dashboardView; DashboardView {} }
    }

    // 连接 Python ViewRouter 信号
    Connections {
        target: viewRouter
        function onGalleryViewShown()  { viewStack.replace(galleryView) }
        function onDetailViewShown()   { viewStack.push(detailView) }
        function onEditViewShown()     { viewStack.push(editView) }
        function onMapViewShown()      { viewStack.replace(mapView) }
    }
}
```

---

## 6. Phase 2: 核心视图迁移 / Core Views Migration

### 6.1 Gallery View（相册网格页）

**Widget 原件 → QML 对照**

| Widget 组件 | QML 组件 | 说明 |
|-------------|----------|------|
| `GalleryPage` (QWidget) | `views/GalleryView.qml` | 页面容器 |
| `GalleryGridView` (QWidget) | 内嵌于 `GalleryView.qml` | 布局容器 |
| `AssetGrid` (QListView) | `components/AssetGrid.qml` (GridView) | 缩略图网格 |
| `AssetGridDelegate` (QStyledItemDelegate) | `components/AssetGridDelegate.qml` | 网格项 delegate |
| `MainHeader` (QWidget) | `components/MainHeader.qml` | 顶部工具栏 |
| `AlbumSidebar` (QTreeView) | `components/AlbumSidebar.qml` (TreeView) | 左侧导航 |
| `AlbumSidebarDelegate` (QStyledItemDelegate) | 内嵌于 `AlbumSidebar.qml` | 树节点渲染 |
| `AlbumSidebarMenu` (QMenu) | `dialogs/` 或内嵌 Menu | 右键菜单 |
| `LiveBadge` (QWidget) | `components/LiveBadge.qml` | Live 标识 |

**QML 实现示例 - AssetGrid.qml:**

```qml
import QtQuick
import QtQuick.Controls

GridView {
    id: assetGrid
    model: assetListVM               // Python ViewModel 注入
    cellWidth: 200; cellHeight: 200
    clip: true

    delegate: AssetGridDelegate {
        width: assetGrid.cellWidth
        height: assetGrid.cellHeight
        thumbnailSource: model.decoration  // Qt::DecorationRole
        isLive: model.isLive               // 自定义 Role
        isFeatured: model.featured         // 自定义 Role
        assetPath: model.abs               // 自定义 Role

        onClicked: playbackCoord.playAsset(index)
        onDoubleClicked: viewRouter.showDetail()
    }

    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
}
```

### 6.2 Detail View（单图详情页）

| Widget 组件 | QML 组件 | 说明 |
|-------------|----------|------|
| `DetailPage` (QWidget) | `views/DetailView.qml` | 详情页容器 |
| `GLImageViewer` (QOpenGLWidget) | `components/ImageViewer.qml` | 图片查看（`Image` + `PinchArea`）|
| `VideoArea` (QWidget + FFmpeg) | `components/VideoArea.qml` (MediaPlayer) | 视频播放 |
| `PlayerBar` (QWidget) | `components/PlayerBar.qml` | 播放控制条 |
| `FilmstripView` (QListView) | `components/FilmstripView.qml` (ListView) | 底部胶片条 |
| `InfoPanel` (QWidget) | `components/InfoPanel.qml` | 元数据面板 |
| `HeaderController` → 显示栏 | 内嵌于 `DetailView.qml` | 位置/时间戳 |

**关键迁移点 - 图片查看器：**

Widget 版使用 `QOpenGLWidget` + 自定义着色器。QML 版方案：

```qml
// components/ImageViewer.qml
import QtQuick

Flickable {
    id: flickable
    contentWidth: image.width * image.scale
    contentHeight: image.height * image.scale
    clip: true

    Image {
        id: image
        source: playerViewController.currentImageSource
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true

        // 缩放手势
        PinchArea {
            anchors.fill: parent
            onPinchUpdated: (pinch) => {
                let newScale = image.scale * pinch.scale
                image.scale = Math.max(0.1, Math.min(newScale, 10.0))
            }
        }

        // 鼠标滚轮缩放
        MouseArea {
            anchors.fill: parent
            onWheel: (wheel) => {
                let factor = wheel.angleDelta.y > 0 ? 1.1 : 0.9
                image.scale = Math.max(0.1, Math.min(image.scale * factor, 10.0))
            }
        }
    }
}
```

> **注意**：若需要 OpenGL 着色器效果（如非破坏性编辑预览），可使用 `ShaderEffect` QML 元素替代。

### 6.3 Album Sidebar（相册侧边栏）

```qml
// components/AlbumSidebar.qml
import QtQuick
import QtQuick.Controls

TreeView {
    id: albumTree
    model: albumTreeModel          // Python AlbumTreeModel (QAbstractItemModel)
    selectionModel: ItemSelectionModel {}

    delegate: TreeViewDelegate {
        contentItem: RowLayout {
            BranchIndicator {       // 已有 QML 组件
                angle: row.expanded ? 90 : 0
                Behavior on angle { NumberAnimation { duration: 150 } }
            }
            Image {
                source: model.icon
                width: 16; height: 16
            }
            Text {
                text: model.display
                color: Theme.textColor
            }
        }

        onClicked: navigationCoord.openAlbum(model.path)
    }
}
```

### 6.4 Filmstrip View（胶片条）

```qml
// components/FilmstripView.qml
import QtQuick
import QtQuick.Controls

ListView {
    id: filmstrip
    orientation: ListView.Horizontal
    model: assetListVM
    height: 80
    clip: true

    delegate: Item {
        width: 60; height: 60
        Image {
            anchors.fill: parent
            source: model.decoration
            fillMode: Image.PreserveAspectCrop
        }
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: filmstrip.currentIndex === index ? Theme.accentColor : "transparent"
            border.width: 2
        }
        MouseArea {
            anchors.fill: parent
            onClicked: playbackCoord.playAsset(index)
        }
    }

    highlight: Rectangle { color: Theme.highlightColor; opacity: 0.3 }
}
```

---

## 7. Phase 3: 编辑与高级功能 / Editing & Advanced Features

### 7.1 编辑器视图

| Widget 组件 | QML 组件 | 说明 |
|-------------|----------|------|
| `EditSidebar` (QWidget) | `components/EditSidebar.qml` | 编辑参数面板 |
| `EditTopbar` (QWidget) | `components/EditTopbar.qml` | 编辑器顶栏 |
| `EditLightSection` | `components/edit/EditLightSection.qml` | 曝光/亮度/阴影 |
| `EditColorSection` | `components/edit/EditColorSection.qml` | 饱和/鲜明/色温 |
| `EditBWSection` | `components/edit/EditBWSection.qml` | 黑白 |
| `EditWBSection` | `components/edit/EditWBSection.qml` | 白平衡 |
| `EditCurveSection` | `components/edit/EditCurveSection.qml` | 曲线（Canvas 绘制） |
| `EditLevelsSection` | `components/edit/EditLevelsSection.qml` | 色阶 |
| `EditSelectiveColorSection` | `components/edit/EditSelectiveColor.qml` | 选择性颜色 |
| `CollapsibleSection` | `components/edit/CollapsibleSection.qml` | 可折叠容器 |
| `GLCropWidget` (QOpenGLWidget) | Canvas / ShaderEffect | 裁剪工具 |
| `EditHistoryManager` (Python) | 保持 Python（通过 Slot 暴露） | 撤销/重做 |
| `EditPipelineLoader` (Python) | 保持 Python | 异步图片加载 |
| `EditPreviewManager` (Python) | 保持 Python + `ShaderEffect` | 实时预览 |

**编辑器关键迁移 - 曲线面板（Canvas）：**

```qml
// components/edit/EditCurveSection.qml
import QtQuick

CollapsibleSection {
    title: qsTr("Curves")

    Canvas {
        id: curveCanvas
        width: 256; height: 256

        property var controlPoints: editSession.curvePoints

        onControlPointsChanged: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            // 背景网格
            ctx.strokeStyle = Theme.gridColor
            ctx.lineWidth = 0.5
            for (var i = 0; i <= 4; i++) {
                var pos = i * width / 4
                ctx.beginPath(); ctx.moveTo(pos, 0); ctx.lineTo(pos, height); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(0, pos); ctx.lineTo(width, pos); ctx.stroke()
            }

            // 曲线
            ctx.strokeStyle = Theme.accentColor
            ctx.lineWidth = 2
            ctx.beginPath()
            // ... 贝塞尔曲线绘制逻辑 ...
            ctx.stroke()
        }

        MouseArea {
            anchors.fill: parent
            onPositionChanged: (mouse) => {
                // 拖动控制点 → 更新 editSession
                editSession.updateCurvePoint(/* ... */)
            }
        }
    }
}
```

### 7.2 地图视图

| Widget 组件 | QML 组件 | 说明 |
|-------------|----------|------|
| `PhotoMapView` (QWidget) | `views/MapView.qml` | 地图页 |
| `MarkerController` (Python) | 保持 Python + `MapItemView` | 标记管理 |
| `LiveMap` (Python) | 保持 Python | 聚类计算 |

QML 方案使用 `QtLocation` 模块：

```qml
import QtLocation
import QtPositioning

Map {
    id: photoMap
    plugin: Plugin { name: "osm" }
    center: QtPositioning.coordinate(39.9, 116.4)
    zoomLevel: 10

    MapItemView {
        model: geoAssetModel
        delegate: MapQuickItem {
            coordinate: QtPositioning.coordinate(model.latitude, model.longitude)
            sourceItem: Image {
                source: model.thumbnail
                width: 40; height: 40
            }
        }
    }
}
```

### 7.3 对话框与菜单

| Widget 组件 | QML 组件 |
|-------------|----------|
| `QFileDialog` | `FileDialog` (QtQuick.Dialogs) |
| `QMessageBox` | `MessageDialog` (QtQuick.Dialogs) |
| `QMenu` (右键) | `Menu` + `MenuItem` (QtQuick.Controls) |
| `dialogs.py` 各种对话框 | `dialogs/*.qml` |

---

## 8. Phase 4: 整合与优化 / Integration & Optimization

### 8.1 主题系统

```qml
// styles/Theme.qml (Singleton)
pragma Singleton
import QtQuick

QtObject {
    // 动态切换: light / dark / system
    property string mode: "dark"

    readonly property color bgPrimary:   mode === "dark" ? "#1e1e1e" : "#ffffff"
    readonly property color bgSecondary: mode === "dark" ? "#2d2d2d" : "#f5f5f5"
    readonly property color textColor:   mode === "dark" ? "#e0e0e0" : "#1a1a1a"
    readonly property color accentColor: "#0078d4"
    readonly property color gridColor:   mode === "dark" ? "#3a3a3a" : "#e0e0e0"
    readonly property color highlightColor: accentColor

    readonly property int fontSizeSmall:  12
    readonly property int fontSizeNormal: 14
    readonly property int fontSizeLarge:  18

    readonly property int spacingSmall:  4
    readonly property int spacingNormal: 8
    readonly property int spacingLarge:  16

    readonly property int radiusSmall:  4
    readonly property int radiusNormal: 8
}
```

### 8.2 性能优化清单

| 优化项 | 方法 |
|--------|------|
| 缩略图懒加载 | `GridView` 自带虚拟化 + `asynchronous: true` on `Image` |
| 大图延迟加载 | `Image.sourceSize` 限制 + `Loader` 按需加载 |
| 列表虚拟化 | QML `ListView` / `GridView` 内置虚拟滚动 |
| 动画性能 | 使用 QML `Behavior` + `enableAnimation` flag |
| 着色器效果 | `ShaderEffect` 替代 `QOpenGLWidget` 手写着色器 |
| 线程安全 | 保持 Python Worker + Qt Signal 桥接模式 |

### 8.3 测试策略

| 测试类型 | 工具 | 范围 |
|----------|------|------|
| QML 单元测试 | `Qt Quick Test` (`TestCase`) | 组件渲染、交互 |
| Python ↔ QML 集成 | `pytest-qt` + QML engine | Signal/Slot 桥接 |
| 视觉回归 | 截图对比 | 关键页面一致性 |
| 现有测试 | `pytest` (不变) | Domain / Infra / ViewModel |

---

## 9. 双入口设计 / Dual Entry Point Design

> 详细实现方案见 [DUAL_ENTRY_POINT.md](./DUAL_ENTRY_POINT.md)

### 9.1 入口对比

| 维度 | Widget 入口 (`main`) | QML 入口 (`main-qml`) |
|------|---------------------|----------------------|
| **文件** | `src/iPhoto/gui/main.py` | `src/iPhoto/gui/main_qml.py` |
| **Application** | `QApplication` | `QGuiApplication` |
| **窗口** | `MainWindow(QMainWindow)` | `QQmlApplicationEngine` + `ApplicationWindow` |
| **UI 层** | Python Widget 类 | `.qml` 文件 |
| **pyproject.toml** | `iphoto-gui = "iPhoto.gui.main:main"` | `iphoto-qml = "iPhoto.gui.main_qml:main"` |
| **DI 容器** | 共享 `DependencyContainer` | 共享 `DependencyContainer` |
| **ViewModel** | 原文件 (`asset_list_viewmodel.py`) | QML 副本 (`asset_list_viewmodel_qml.py`) |
| **Facade** | 原文件 (`facade.py`) | QML 副本 (`facade_qml.py`) |
| **Coordinators** | 原文件 | QML 副本 (`*_qml.py`) |

### 9.2 共享层提取（`_qml.py` 隔离策略）

```
src/iPhoto/gui/
├── main.py                    # Widget 入口 (零修改)
├── main_qml.py                # QML 入口 (新增)
├── bootstrap.py               # 【新增】共享 DI 初始化（仅 Infra/App 层）
├── bootstrap_qml.py           # 【新增】QML 专用初始化（使用 _qml 副本）
│
├── facade.py                  # Widget 用 AppFacade (不修改)
├── facade_qml.py              # QML 用 AppFacade 副本 (添加 @Property)
│
├── coordinators/
│   ├── main_coordinator.py          # Widget 用 (不修改)
│   ├── main_coordinator_qml.py      # QML 副本
│   ├── navigation_coordinator.py    # Widget 用 (不修改)
│   ├── navigation_coordinator_qml.py # QML 副本 (添加 @Slot)
│   ├── playback_coordinator.py      # Widget 用 (不修改)
│   ├── playback_coordinator_qml.py  # QML 副本 (添加 @Slot/@Property)
│   ├── edit_coordinator.py          # Widget 用 (不修改)
│   ├── edit_coordinator_qml.py      # QML 副本 (添加 @Slot)
│   ├── view_router.py               # Widget 用 (不修改)
│   └── view_router_qml.py           # QML 副本 (添加 @Property)
│
├── viewmodels/
│   ├── asset_list_viewmodel.py      # Widget 用 (不修改)
│   ├── asset_list_viewmodel_qml.py  # QML 副本 (添加 roleNames/@Property)
│   ├── asset_data_source.py         # Widget 用 (不修改)
│   ├── asset_data_source_qml.py     # QML 副本
│   ├── album_viewmodel.py           # Widget 用 (不修改)
│   └── album_viewmodel_qml.py       # QML 副本
│
├── services/              # 共享 (不修改)
│
└── ui/
    ├── widgets/           # Widget 专用 (不修改)
    ├── controllers/       # Widget 专用 (不修改)
    ├── models/
    │   ├── edit_session.py          # Widget 用 (不修改)
    │   ├── edit_session_qml.py      # QML 副本 (添加 @Property)
    │   ├── roles.py                 # Widget 用 (不修改)
    │   ├── roles_qml.py             # QML 副本 (添加 roleNames 映射)
    │   ├── album_tree_model.py      # Widget 用 (不修改)
    │   ├── album_tree_model_qml.py  # QML 副本 (添加 roleNames)
    │   └── ...其余 (共享不变)
    ├── delegates/         # Widget 专用 (不修改)
    ├── tasks/             # 共享 (不修改)
    ├── menus/             # Widget 专用 (不修改)
    ├── icon/              # 共享 (不修改)
    └── qml/               # QML 专用 (全部新增)
        ├── Main.qml
        ├── views/
        ├── components/
        ├── dialogs/
        └── styles/
```

### 9.3 bootstrap.py 共享初始化（仅 Infra/App 层）

```python
"""Shared bootstrap logic — only Infrastructure & Application layer.
Widget and QML entry points share this, then diverge for GUI objects."""
from iPhoto.di.container import DependencyContainer
from iPhoto.events.bus import EventBus
from iPhoto.infrastructure.db.pool import ConnectionPool
# ...

def create_container() -> DependencyContainer:
    """Create and configure DI container (shared between Widget and QML)."""
    container = DependencyContainer()
    container.register_singleton(EventBus, EventBus())
    container.register_singleton(ConnectionPool, ConnectionPool(...))
    # ... Infrastructure + Application 注册 ...
    return container
```

### 9.4 bootstrap_qml.py QML 专用初始化

```python
"""QML-specific bootstrap — creates _qml.py variant objects."""
from iPhoto.gui.bootstrap import create_container
from iPhoto.gui.facade_qml import AppFacadeQml
from iPhoto.gui.viewmodels.asset_list_viewmodel_qml import AssetListViewModelQml
from iPhoto.gui.viewmodels.album_viewmodel_qml import AlbumViewModelQml
from iPhoto.gui.coordinators.view_router_qml import ViewRouterQml
from iPhoto.gui.coordinators.navigation_coordinator_qml import NavigationCoordinatorQml

def create_qml_components(container):
    """Create QML-adapted ViewModels, Facade, Coordinators."""
    facade = AppFacadeQml()
    asset_list_vm = AssetListViewModelQml(...)
    album_vm = AlbumViewModelQml()
    view_router = ViewRouterQml()
    navigation_coord = NavigationCoordinatorQml(...)
    return facade, asset_list_vm, album_vm, view_router, navigation_coord
```

> **main.py** (Widget) 不使用 `bootstrap_qml.py`，继续使用原有的初始化逻辑（零修改）。
> **main_qml.py** (QML) 使用 `bootstrap.py` + `bootstrap_qml.py`。

---

## 10. 风险评估与缓解 / Risk Assessment & Mitigation

| ⚠️ 风险 | 影响 | 概率 | 缓解措施 |
|---------|------|------|---------|
| OpenGL 着色器迁移 | `GLImageViewer` 的自定义着色器在 QML 中需重写 | 高 | 使用 `ShaderEffect` + GLSL 片段着色器；阶段性迁移 |
| 性能差异 | QML 渲染管线与 Widget 不同，可能有帧率差异 | 中 | 使用 `GridView` 虚拟化；`Image.asynchronous: true` |
| FFmpeg 视频播放 | 当前 `VideoArea` 直接使用 FFmpeg，QML `MediaPlayer` 接口不同 | 高 | 保留 Python 视频解码层，通过 `VideoOutput` + 自定义 `QQuickImageProvider` 桥接 |
| 地图组件 | `PhotoMapView` 使用自定义瓦片加载，迁移到 `QtLocation` 需适配 | 中 | 分阶段：先用 `QtLocation` OSM 插件，再自定义 `MapPlugin` |
| Exif 工具栏提示 | 自定义 `CustomTooltip` 在 QML 中需重写 | 低 | 使用 QML `ToolTip` 组件 + 自定义样式 |
| 无框窗口 | `FramelessWindowManager` 在 QML 中需要不同的实现 | 中 | 使用 `ApplicationWindow` + `flags: Qt.FramelessWindowHint` + 自定义 `DragHandler` |
| 平台兼容性 | QML 在不同 OS 上渲染差异 | 低 | CI 多平台测试 |

---

## 11. 验收标准 / Acceptance Criteria

### Phase 1 验收
- [ ] `iphoto-qml` 命令可启动 QML 窗口
- [ ] QML 窗口显示空白 `ApplicationWindow`
- [ ] Python ViewModel 可在 QML 中访问
- [ ] 主题切换（亮/暗）生效

### Phase 2 验收
- [ ] QML 相册侧边栏可浏览目录树
- [ ] QML 网格视图显示缩略图
- [ ] 点击缩略图进入 QML 详情页
- [ ] 胶片条导航正常
- [ ] 视频播放基本功能

### Phase 3 验收
- [ ] QML 编辑器所有调整面板可用
- [ ] 曲线/色阶 Canvas 绘制正确
- [ ] 裁剪工具可用
- [ ] 地图视图显示标记
- [ ] 导出/分享功能

### Phase 4 验收
- [ ] 性能对标 Widget 版本（帧率、内存）
- [ ] 全功能测试通过
- [ ] 双入口均可正常启动和运行
- [ ] 文档完善

---

## 📎 相关文档

- [QML 文件结构详解 / QML File Structure](./QML_FILE_STRUCTURE.md)
- [组件映射对照表 / Component Mapping](./COMPONENT_MAPPING.md)
- [双入口实现指南 / Dual Entry Point Guide](./DUAL_ENTRY_POINT.md)

---

> **维护者 / Maintainer:** iPhotron Team  
> **最后更新 / Last Updated:** 2026-02-08  

# 📁 QML 文件结构详解 / QML File Structure

> **版本 / Version:** 1.0  
> **创建日期 / Created:** 2026-02-08  
> **关联文档 / Related:** [MIGRATION_PLAN.md](./MIGRATION_PLAN.md)

---

## 📑 目录 / Table of Contents

1. [目录总览 / Directory Overview](#1-目录总览--directory-overview)
2. [文件详解 / File Details](#2-文件详解--file-details)
3. [模块注册 / Module Registration](#3-模块注册--module-registration)
4. [资源管理 / Resource Management](#4-资源管理--resource-management)
5. [与现有结构的对比 / Comparison with Current Structure](#5-与现有结构的对比--comparison-with-current-structure)

---

## 1. 目录总览 / Directory Overview

```
src/iPhoto/gui/ui/qml/
│
├── Main.qml                        # 应用根组件 / App root component
├── Theme.qml                       # 全局主题单例 / Global theme singleton
├── qmldir                          # QML 模块注册 / Module registration
│
├── views/                          # 📄 页面级视图 / Page-level views
│   ├── GalleryView.qml             #   相册网格页 / Album grid page
│   ├── DetailView.qml              #   单图详情页 / Single asset detail
│   ├── EditView.qml                #   编辑器页 / Photo editor
│   ├── MapView.qml                 #   地图页 / Map view
│   └── DashboardView.qml           #   仪表盘页 / Albums dashboard
│
├── components/                     # 🧩 可复用组件 / Reusable components
│   ├── AlbumSidebar.qml            #   相册导航树 / Album navigation tree
│   ├── AssetGrid.qml               #   缩略图网格 / Thumbnail grid
│   ├── AssetGridDelegate.qml       #   网格项渲染器 / Grid item renderer
│   ├── FilmstripView.qml           #   胶片条视图 / Filmstrip strip
│   ├── PlayerBar.qml               #   播放控制条 / Video playback controls
│   ├── ImageViewer.qml             #   图片查看器 / Image viewer (zoom/pan)
│   ├── VideoArea.qml               #   视频播放区域 / Video playback area
│   ├── EditSidebar.qml             #   编辑参数面板 / Edit adjustments panel
│   ├── EditTopbar.qml              #   编辑器顶栏 / Editor top bar
│   ├── InfoPanel.qml               #   元数据面板 / Metadata info panel
│   ├── MainHeader.qml              #   主界面顶栏 / Main header toolbar
│   ├── NotificationToast.qml       #   提示消息 / Toast notification
│   ├── CustomTitleBar.qml          #   自定义标题栏 / Frameless title bar
│   ├── ChromeStatusBar.qml         #   自定义状态栏 / Custom status bar
│   ├── LiveBadge.qml               #   Live Photo 标识 / Live photo indicator
│   ├── BranchIndicator.qml         #   树展开指示器 / Tree branch indicator (已有)
│   ├── SlidingSegmented.qml        #   分段选择器 / Segmented control
│   ├── CollapsibleSection.qml      #   可折叠容器 / Collapsible container
│   └── FlowLayout.qml              #   流式布局 / Flow/wrap layout
│
├── components/edit/                # ✏️ 编辑子面板 / Edit sub-panels
│   ├── EditLightSection.qml        #   曝光/亮度/阴影 / Exposure, brightness
│   ├── EditColorSection.qml        #   饱和/色温/色调 / Saturation, temp, tint
│   ├── EditBWSection.qml           #   黑白转换 / Black & white
│   ├── EditWBSection.qml           #   白平衡+吸管 / White balance + picker
│   ├── EditCurveSection.qml        #   曲线调整 / Curves (Canvas)
│   ├── EditLevelsSection.qml       #   色阶调整 / Levels (Canvas)
│   └── EditSelectiveColor.qml      #   选择性颜色 / Selective color
│
├── dialogs/                        # 💬 对话框 / Dialogs
│   ├── OpenAlbumDialog.qml         #   打开相册 / Open album
│   ├── BindLibraryDialog.qml       #   绑定图库 / Bind library
│   ├── ErrorDialog.qml             #   错误提示 / Error message
│   ├── ConfirmDialog.qml           #   确认对话框 / Confirmation
│   └── ExportDialog.qml            #   导出选项 / Export options
│
└── styles/                         # 🎨 样式常量 / Style constants
    ├── Colors.qml                  #   颜色定义 / Color definitions
    ├── Fonts.qml                   #   字体定义 / Font definitions
    └── Dimensions.qml              #   尺寸/间距 / Dimensions & spacing
```

---

## 2. 文件详解 / File Details

### 2.1 根文件 / Root Files

#### `Main.qml` — 应用根组件

**职责：**
- 作为 QML 应用的顶层 `ApplicationWindow`
- 包含全局布局结构（侧边栏 + 内容区 + 状态栏）
- 集成 `StackView` 视图路由器
- 连接 Python `ViewRouter` 信号驱动页面切换

**结构：**
```qml
ApplicationWindow {
    id: root
    visible: true
    width: 1400; height: 900
    title: "iPhotron"
    flags: Qt.Window | Qt.FramelessWindowHint  // 可选无框

    // 自定义标题栏
    header: CustomTitleBar { ... }

    // 主布局: 侧边栏 + 内容区
    RowLayout {
        anchors.fill: parent
        spacing: 0

        AlbumSidebar {
            Layout.preferredWidth: 240
            Layout.fillHeight: true
        }

        StackView {
            id: viewStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            initialItem: galleryView
        }
    }

    // 状态栏
    footer: ChromeStatusBar { ... }

    // 视图路由连接
    Connections {
        target: viewRouter
        function onGalleryViewShown()  { viewStack.replace(null, galleryView) }
        function onDetailViewShown()   { viewStack.push(detailView) }
        function onEditViewShown()     { viewStack.push(editView) }
        function onMapViewShown()      { viewStack.replace(null, mapView) }
        function onDashboardViewShown(){ viewStack.replace(null, dashboardView) }
    }

    // 全局通知
    NotificationToast { id: toast; anchors.bottom: parent.bottom }
}
```

#### `Theme.qml` — 全局主题单例

**职责：**
- 定义颜色、字体、尺寸常量
- 支持 light / dark / system 三种模式动态切换
- 被所有 QML 组件引用

**使用方式：**
```qml
import "styles" as Styles
// 在组件中:
color: Theme.bgPrimary
font.pixelSize: Theme.fontSizeNormal
```

#### `qmldir` — 模块注册

```
module iPhotron

# Singletons
singleton Theme 1.0 Theme.qml

# Views
GalleryView 1.0 views/GalleryView.qml
DetailView 1.0 views/DetailView.qml
EditView 1.0 views/EditView.qml
MapView 1.0 views/MapView.qml
DashboardView 1.0 views/DashboardView.qml

# Components
AlbumSidebar 1.0 components/AlbumSidebar.qml
AssetGrid 1.0 components/AssetGrid.qml
AssetGridDelegate 1.0 components/AssetGridDelegate.qml
# ... 其余组件 ...
```

---

### 2.2 views/ — 页面级视图

每个视图文件对应一个**全屏页面**，由 `StackView` 管理。

| 文件 | Python 对应 | 功能描述 |
|------|------------|---------|
| `GalleryView.qml` | `GalleryPage` + `GalleryGridView` | 相册网格页：包含 `MainHeader` + `AssetGrid`，支持多选模式 |
| `DetailView.qml` | `DetailPage` | 单图详情页：包含 `ImageViewer` / `VideoArea` + `PlayerBar` + `FilmstripView` + `InfoPanel` |
| `EditView.qml` | 编辑器相关 widgets | 编辑器页：包含 `ImageViewer`(可编辑模式) + `EditSidebar` + `EditTopbar` |
| `MapView.qml` | `PhotoMapView` | 地图页：包含 `Map` + 标记集群 + 缩略图弹窗 |
| `DashboardView.qml` | `AlbumsDashboard` | 仪表盘：包含相册卡片网格 + 统计信息 |

#### GalleryView.qml 详细结构

```qml
import QtQuick
import QtQuick.Layouts

Item {
    id: galleryView

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // 顶栏
        MainHeader {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            onSearchTextChanged: (text) => assetListVM.filterByName(text)
        }

        // 网格
        AssetGrid {
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: assetListVM
            onAssetClicked: (index) => playbackCoord.playAsset(index)
            onAssetDoubleClicked: (index) => viewRouter.showDetail()
        }
    }

    // 多选浮动工具栏
    SelectionToolbar {
        visible: selectionController.isActive
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
    }
}
```

#### DetailView.qml 详细结构

```qml
import QtQuick
import QtQuick.Layouts

Item {
    id: detailView

    // 主内容区
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // 顶栏 (位置 + 时间戳)
        DetailHeader {
            Layout.fillWidth: true
            location: headerController.locationText
            timestamp: headerController.timestampText
        }

        // 图片/视频查看器
        Loader {
            id: mediaLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: playerViewController.isVideo ? videoComponent : imageComponent
        }

        Component {
            id: imageComponent
            ImageViewer {
                source: playerViewController.currentImageSource
            }
        }

        Component {
            id: videoComponent
            VideoArea {
                source: playerViewController.currentVideoSource
            }
        }

        // 播放控制条 (仅视频可见)
        PlayerBar {
            Layout.fillWidth: true
            visible: playerViewController.isVideo
        }

        // 胶片条
        FilmstripView {
            Layout.fillWidth: true
            Layout.preferredHeight: 80
            model: assetListVM
            currentIndex: playbackCoord.currentRow
        }
    }

    // 侧边信息面板
    InfoPanel {
        id: infoPanel
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        visible: playbackCoord.infoPanelVisible
        width: 300
    }
}
```

---

### 2.3 components/ — 可复用组件

#### 核心组件规格

| 组件 | 类型 | 数据来源 | 交互 |
|------|------|---------|------|
| `AlbumSidebar.qml` | `TreeView` | `albumTreeModel` (Python) | 点击选择相册，右键菜单 |
| `AssetGrid.qml` | `GridView` | `assetListVM` (Python) | 点击/双击/多选/右键 |
| `AssetGridDelegate.qml` | 自定义 `Item` | 单个 model role | 缩略图 + Live 标识 + 收藏心 |
| `FilmstripView.qml` | `ListView` (水平) | `assetListVM` (Python) | 点击切换，拖拽滚动 |
| `ImageViewer.qml` | `Flickable` + `Image` | `playerViewController` | 缩放/平移/双击重置 |
| `VideoArea.qml` | `MediaPlayer` + `VideoOutput` | `playerViewController` | 播放/暂停/进度 |
| `PlayerBar.qml` | 自定义 `Item` | `playerViewController` | 播放/暂停/进度条/音量 |
| `EditSidebar.qml` | `ScrollView` + 子面板 | `editSession` (Python) | 滑块/曲线/色板调整 |
| `InfoPanel.qml` | `ScrollView` | `assetListVM` 当前项 | 只读元数据展示 |
| `MainHeader.qml` | `ToolBar` | 无 | 搜索/排序/视图切换 |
| `NotificationToast.qml` | `Popup` | `appFacade` 信号 | 自动消失/点击关闭 |
| `LiveBadge.qml` | `Rectangle` + `Text` | 单个 model role | 静态显示 |
| `BranchIndicator.qml` | `Shape` + `ShapePath` | 展开状态 | 旋转动画 |

#### AssetGridDelegate.qml 详细规格

```qml
// components/AssetGridDelegate.qml
import QtQuick

Item {
    id: delegateRoot
    required property int index
    required property string abs          // AssetListViewModel.Roles.ABS
    required property var decoration      // Qt::DecorationRole (thumbnail)
    required property bool isLive         // Roles.IS_LIVE
    required property bool featured       // Roles.FEATURED
    required property bool isVideo        // Roles.IS_VIDEO

    signal clicked(int index)
    signal doubleClicked(int index)
    signal rightClicked(int index, real x, real y)

    // 缩略图
    Image {
        id: thumbnail
        anchors.fill: parent
        anchors.margins: 2
        source: delegateRoot.decoration
        fillMode: Image.PreserveAspectCrop
        asynchronous: true              // 异步加载
        sourceSize: Qt.size(200, 200)   // 限制解码尺寸

        // 加载占位
        Rectangle {
            anchors.fill: parent
            color: Theme.bgSecondary
            visible: thumbnail.status === Image.Loading
        }
    }

    // Live Photo 标识
    LiveBadge {
        visible: delegateRoot.isLive
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 4
    }

    // 收藏心形
    Text {
        text: "♥"
        visible: delegateRoot.featured
        color: Theme.accentColor
        font.pixelSize: 16
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 4
    }

    // 视频时长标签
    Rectangle {
        visible: delegateRoot.isVideo
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.margins: 4
        // ... 时长显示 ...
    }

    // 鼠标交互
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: (mouse) => {
            if (mouse.button === Qt.RightButton)
                delegateRoot.rightClicked(delegateRoot.index, mouse.x, mouse.y)
            else
                delegateRoot.clicked(delegateRoot.index)
        }
        onDoubleClicked: delegateRoot.doubleClicked(delegateRoot.index)
    }

    // 选中高亮
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: selectionController.isSelected(delegateRoot.index)
                      ? Theme.accentColor : "transparent"
        border.width: 3
        radius: Theme.radiusSmall
    }
}
```

---

### 2.4 components/edit/ — 编辑子面板

每个编辑子面板封装在 `CollapsibleSection` 中，与 `EditSession` (Python QObject) 双向绑定。

| 文件 | Widget 对应 | 包含控件 |
|------|-----------|---------|
| `EditLightSection.qml` | `edit_light_section.py` | 6 个 Slider：曝光、亮度、高光、阴影、对比度、清晰度 |
| `EditColorSection.qml` | `edit_color_section.py` | 4 个 Slider：饱和度、鲜明度、色温、色调 |
| `EditBWSection.qml` | `edit_bw_section.py` | 多个 Slider：红/橙/黄/绿/蓝/紫 通道 |
| `EditWBSection.qml` | `edit_wb_section.py` | 色温/色调 Slider + 吸管工具（MouseArea） |
| `EditCurveSection.qml` | `edit_curve_section.py` | Canvas 绘制贝塞尔曲线 + 通道切换 |
| `EditLevelsSection.qml` | `edit_levels_section.py` | Canvas 绘制直方图 + 3 个拖拽手柄 |
| `EditSelectiveColor.qml` | `edit_selective_color_section.py` | 颜色选择 + 4 个 Slider (C/M/Y/K) |

#### 通用 Slider 绑定模式

```qml
// components/edit/EditLightSection.qml
CollapsibleSection {
    title: qsTr("Light")

    Column {
        width: parent.width
        spacing: Theme.spacingSmall

        // 曝光 Slider
        LabeledSlider {
            label: qsTr("Exposure")
            from: -3.0; to: 3.0; value: editSession.exposure
            onValueChanged: editSession.exposure = value
        }

        // 亮度 Slider
        LabeledSlider {
            label: qsTr("Brightness")
            from: -100; to: 100; value: editSession.brightness
            onValueChanged: editSession.brightness = value
        }

        // ... 其余 Slider ...
    }
}
```

---

### 2.5 dialogs/ — 对话框

| 文件 | Widget 对应 | 功能 |
|------|-----------|------|
| `OpenAlbumDialog.qml` | `QFileDialog` (in `dialog_controller.py`) | 文件夹选择器 |
| `BindLibraryDialog.qml` | `QFileDialog` (in `dialog_controller.py`) | 图库绑定选择器 |
| `ErrorDialog.qml` | `QMessageBox` (in `dialogs.py`) | 错误提示 |
| `ConfirmDialog.qml` | `QMessageBox` (in `dialogs.py`) | 确认操作 |
| `ExportDialog.qml` | 自定义 (in `export_controller.py`) | 导出设置 |

#### 对话框 QML 示例

```qml
// dialogs/ConfirmDialog.qml
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

Dialog {
    id: confirmDialog
    title: qsTr("Confirm")
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel

    property string message: ""

    Label {
        text: confirmDialog.message
        wrapMode: Text.WordWrap
    }

    onAccepted: { /* 由调用者连接 */ }
    onRejected: { /* 关闭 */ }
}
```

---

### 2.6 styles/ — 样式常量

#### Colors.qml

```qml
pragma Singleton
import QtQuick

QtObject {
    // 基础色板
    readonly property color primary:    "#0078d4"
    readonly property color secondary:  "#106ebe"
    readonly property color success:    "#10b981"
    readonly property color warning:    "#f59e0b"
    readonly property color error:      "#ef4444"

    // 深色主题
    readonly property color darkBg1:    "#1e1e1e"
    readonly property color darkBg2:    "#2d2d2d"
    readonly property color darkBg3:    "#3a3a3a"
    readonly property color darkText1:  "#e0e0e0"
    readonly property color darkText2:  "#a0a0a0"

    // 浅色主题
    readonly property color lightBg1:   "#ffffff"
    readonly property color lightBg2:   "#f5f5f5"
    readonly property color lightBg3:   "#e0e0e0"
    readonly property color lightText1: "#1a1a1a"
    readonly property color lightText2: "#6b6b6b"
}
```

#### Dimensions.qml

```qml
pragma Singleton
import QtQuick

QtObject {
    // 间距
    readonly property int spacingXS:  2
    readonly property int spacingS:   4
    readonly property int spacingM:   8
    readonly property int spacingL:   16
    readonly property int spacingXL:  24

    // 圆角
    readonly property int radiusS:    4
    readonly property int radiusM:    8
    readonly property int radiusL:    12

    // 组件尺寸
    readonly property int headerHeight:     48
    readonly property int statusBarHeight:  24
    readonly property int sidebarWidth:     240
    readonly property int filmstripHeight:  80
    readonly property int gridCellSize:     200
    readonly property int infoPanelWidth:   300
    readonly property int editSidebarWidth: 280

    // 缩略图
    readonly property int thumbnailSize:    200
    readonly property int filmstripThumbSize: 60
}
```

---

## 3. 模块注册 / Module Registration

### 3.1 qmldir 文件

QML 模块系统通过 `qmldir` 文件注册组件，使得 QML 文件可以通过模块名导入。

```
# src/iPhoto/gui/ui/qml/qmldir
module iPhotron

# Singletons (全局可用)
singleton Theme    1.0 Theme.qml
singleton Colors   1.0 styles/Colors.qml
singleton Fonts    1.0 styles/Fonts.qml
singleton Dims     1.0 styles/Dimensions.qml

# Views (页面)
GalleryView    1.0 views/GalleryView.qml
DetailView     1.0 views/DetailView.qml
EditView       1.0 views/EditView.qml
MapView        1.0 views/MapView.qml
DashboardView  1.0 views/DashboardView.qml

# Components (组件)
AlbumSidebar       1.0 components/AlbumSidebar.qml
AssetGrid          1.0 components/AssetGrid.qml
AssetGridDelegate  1.0 components/AssetGridDelegate.qml
FilmstripView      1.0 components/FilmstripView.qml
PlayerBar          1.0 components/PlayerBar.qml
ImageViewer        1.0 components/ImageViewer.qml
VideoArea          1.0 components/VideoArea.qml
EditSidebar        1.0 components/EditSidebar.qml
EditTopbar         1.0 components/EditTopbar.qml
InfoPanel          1.0 components/InfoPanel.qml
MainHeader         1.0 components/MainHeader.qml
NotificationToast  1.0 components/NotificationToast.qml
CustomTitleBar     1.0 components/CustomTitleBar.qml
ChromeStatusBar    1.0 components/ChromeStatusBar.qml
LiveBadge          1.0 components/LiveBadge.qml
BranchIndicator    1.0 components/BranchIndicator.qml
SlidingSegmented   1.0 components/SlidingSegmented.qml
CollapsibleSection 1.0 components/CollapsibleSection.qml

# Edit panels (编辑子面板)
EditLightSection     1.0 components/edit/EditLightSection.qml
EditColorSection     1.0 components/edit/EditColorSection.qml
EditBWSection        1.0 components/edit/EditBWSection.qml
EditWBSection        1.0 components/edit/EditWBSection.qml
EditCurveSection     1.0 components/edit/EditCurveSection.qml
EditLevelsSection    1.0 components/edit/EditLevelsSection.qml
EditSelectiveColor   1.0 components/edit/EditSelectiveColor.qml

# Dialogs (对话框)
OpenAlbumDialog    1.0 dialogs/OpenAlbumDialog.qml
BindLibraryDialog  1.0 dialogs/BindLibraryDialog.qml
ErrorDialog        1.0 dialogs/ErrorDialog.qml
ConfirmDialog      1.0 dialogs/ConfirmDialog.qml
ExportDialog       1.0 dialogs/ExportDialog.qml
```

### 3.2 Python 端类型注册

```python
# src/iPhoto/gui/main_qml.py
from PySide6.QtQml import qmlRegisterType, qmlRegisterSingletonType

# 注册自定义 QObject 类型供 QML 使用
qmlRegisterType(EditSession,       "iPhotron", 1, 0, "EditSession")
qmlRegisterType(AssetListViewModel,"iPhotron", 1, 0, "AssetListModel")

# 单例注册
qmlRegisterSingletonType(AppFacade, "iPhotron", 1, 0, "AppFacade",
                         lambda engine, script_engine: app_facade)
```

---

## 4. 资源管理 / Resource Management

### 4.1 图标资源

现有图标位于 `src/iPhoto/gui/ui/icon/`。QML 中引用方式：

```python
# main_qml.py 中设置图标搜索路径
engine.addImageProvider("icons", IconImageProvider())
# 或者使用 QDir 设置
```

```qml
// QML 中引用
Image {
    source: "qrc:/icons/play.svg"      // 方式 1: Qt 资源系统
    source: "../../icon/play.svg"       // 方式 2: 相对路径
    source: "image://icons/play.svg"    // 方式 3: ImageProvider
}
```

**推荐方式：** 使用 `QQuickImageProvider` 自定义图标提供器，统一管理。

### 4.2 缩略图资源

现有缩略图通过 `ThumbnailLoader` (Worker) 异步加载。QML 版使用 `QQuickAsyncImageProvider`：

```python
# src/iPhoto/gui/ui/qml/providers/thumbnail_provider.py
class ThumbnailProvider(QQuickAsyncImageProvider):
    """Provides thumbnails to QML Image elements via image://thumbnails/."""

    def requestImageResponse(self, id: str, requested_size):
        response = ThumbnailResponse(id, requested_size, self._cache)
        return response
```

```qml
// QML 中使用
Image {
    source: "image://thumbnails/" + model.abs
    asynchronous: true
    sourceSize: Qt.size(200, 200)
}
```

---

## 5. 与现有结构的对比 / Comparison with Current Structure

### 5.1 目录结构对照

```
现有 Widget 结构                          QML 结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
src/iPhoto/gui/ui/                       src/iPhoto/gui/ui/qml/
├── widgets/                             ├── views/          (页面)
│   ├── gallery_page.py        →         │   └── GalleryView.qml
│   ├── detail_page.py         →         │   └── DetailView.qml
│   ├── photo_map_view.py      →         │   └── MapView.qml
│   ├── albums_dashboard.py    →         │   └── DashboardView.qml
│   │                                    │
│   ├── asset_grid.py          →         ├── components/     (组件)
│   ├── album_sidebar.py       →         │   ├── AssetGrid.qml
│   ├── filmstrip_view.py      →         │   ├── AlbumSidebar.qml
│   ├── player_bar.py          →         │   ├── FilmstripView.qml
│   ├── image_viewer.py        →         │   ├── ImageViewer.qml
│   ├── video_area.py          →         │   ├── VideoArea.qml
│   ├── edit_sidebar.py        →         │   ├── EditSidebar.qml
│   ├── info_panel.py          →         │   ├── InfoPanel.qml
│   ├── main_header.py         →         │   ├── MainHeader.qml
│   ├── notification_toast.py  →         │   └── NotificationToast.qml
│   │                                    │
│   ├── edit_light_section.py  →         ├── components/edit/ (编辑面板)
│   ├── edit_color_section.py  →         │   ├── EditLightSection.qml
│   ├── edit_curve_section.py  →         │   ├── EditColorSection.qml
│   └── ...                    →         │   └── ...
│                                        │
├── controllers/               →         │  (逻辑保留在 Python Coordinator 中)
├── delegates/                 →         │  (融入 QML delegate Component)
├── models/                    →         │  (共享, 不迁移)
├── tasks/                     →         │  (共享, 不迁移)
├── menus/                     →         ├── dialogs/         (对话框)
└── icon/                      →         └── styles/          (样式)
```

### 5.2 文件数量对比

| 类别 | Widget 文件数 | QML 文件数 | 说明 |
|------|-------------|-----------|------|
| 页面视图 | 5 (.py) | 5 (.qml) | 1:1 映射 |
| 组件 | ~30 (.py) | ~20 (.qml) | QML 组件更内聚，部分合并 |
| 编辑面板 | 8 (.py) | 7 (.qml) | 接近 1:1 |
| 控制器 | 17 (.py) | 0 | 逻辑保留在 Python Coordinator |
| 委托 | 1 (.py) | 0 | 融入 QML delegate |
| 对话框 | 1 (.py) + 内嵌 | 5 (.qml) | 独立文件化 |
| 样式 | 0 (QSS 内嵌) | 3 (.qml) | 独立样式模块 |
| **总计** | **~62 文件** | **~40 文件** | QML 更简洁 |

### 5.3 代码量估算

| 层级 | Widget (Python) | QML 估算 | 变化 |
|------|----------------|---------|------|
| 视图层 UI | ~8,000 行 | ~4,000 行 | -50% (声明式更简洁) |
| 控制器层 | ~3,500 行 | 0 行 (保留 Python) | 不变 |
| ViewModel 适配 | 0 行 | ~300 行 (@Property/@Slot) | +300 行 |
| 桥接/入口 | 0 行 | ~200 行 (main_qml + bootstrap) | +200 行 |
| **净变化** | | | **UI 代码量减少 ~45%** |

---

> **维护者 / Maintainer:** iPhotron Team  
> **最后更新 / Last Updated:** 2026-02-08

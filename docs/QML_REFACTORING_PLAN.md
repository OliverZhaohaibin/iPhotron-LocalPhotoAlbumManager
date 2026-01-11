# iPhoto 纯QML界面重构开发方案

> **项目目标**：将 iPhoto 的所有界面从混合 QWidget/QML 架构重构为纯 QML 界面
> 
> **编制日期**：2026-01-11
> 
> **版本**：V1.0

---

## 📋 目录

1. [项目概述](#1-项目概述)
2. [现状分析](#2-现状分析)
3. [架构设计](#3-架构设计)
4. [技术选型](#4-技术选型)
5. [实施步骤](#5-实施步骤)
6. [组件迁移清单](#6-组件迁移清单)
7. [关键技术点](#7-关键技术点)
8. [风险与挑战](#8-风险与挑战)
9. [测试策略](#9-测试策略)
10. [时间与资源估算](#10-时间与资源估算)

---

## 1. 项目概述

### 1.1 背景

iPhoto 是一个基于 PySide6 (Qt6) 的照片管理应用，当前采用 **混合架构**：
- **主体界面**：使用 QWidget 构建（约60个 Python Widget 文件，12,000+ 行代码）
- **部分组件**：已使用 QML（如 `GalleryGrid.qml`、`BranchIndicator.qml`）

这种混合架构带来以下问题：
- 维护成本高（需要同时维护两套UI范式）
- 性能不统一（QML渲染效率更高，但与 QWidget 混用会产生开销）
- 现代化程度不一致（QML 支持更好的动画和声明式编程）

### 1.2 目标

将整个应用重构为 **纯 QML 界面**，同时保持：
- ✅ 所有现有功能完整保留
- ✅ 后端逻辑（Python）不变，仅重构前端
- ✅ 保持 MVC/MVVM 架构清晰分离
- ✅ 提升性能和用户体验
- ✅ 改善代码可维护性

### 1.3 预期收益

| 收益类型 | 具体内容 |
|---------|---------|
| **性能提升** | - QML 使用硬件加速的 Scene Graph 渲染<br>- 更流畅的动画和过渡效果<br>- 更好的大列表性能（ListView/GridView） |
| **开发效率** | - 声明式语法更简洁<br>- 热重载支持（qmlscene）<br>- 更容易调试和迭代 |
| **维护性** | - 统一的 UI 技术栈<br>- 更清晰的 UI/逻辑分离<br>- 更少的样板代码 |
| **现代化** | - 更好的触摸屏支持<br>- 现代化的动画系统<br>- 更灵活的布局系统 |

---

## 2. 现状分析

### 2.1 现有架构概览

```
iPhoto 项目结构 (GUI Layer)
└── src/iPhoto/gui/
    ├── main.py                    # GUI 入口
    ├── facade.py                  # Qt-Python 桥接层
    ├── appctx.py                  # 全局上下文
    ├── services/                  # 后台服务
    └── ui/
        ├── main_window.py         # ⚠️ QMainWindow (待重构)
        ├── ui_main_window.py      # ⚠️ Qt Designer 生成 (待重构)
        ├── controllers/           # 控制器层（保留，但需适配）
        ├── models/                # Qt 数据模型（保留）
        ├── widgets/               # ⚠️ 60个 QWidget 文件 (待重构)
        │   ├── album_sidebar.py
        │   ├── gallery_grid_view.py  # 已部分使用 QML
        │   ├── detail_page.py
        │   ├── edit_sidebar.py
        │   ├── photo_map_view.py
        │   └── ... (约60个文件)
        └── qml/                   # ✅ 现有 QML 组件
            ├── GalleryGrid.qml
            └── BranchIndicator.qml
```

### 2.2 需要重构的核心组件

根据代码分析，需要重构的主要组件包括：

| 组件类别 | 组件名称 | 当前实现 | 复杂度 | 优先级 |
|---------|---------|---------|-------|-------|
| **主窗口** | MainWindow | QMainWindow | 高 | P0 |
| **侧边栏** | AlbumSidebar | QWidget + QTreeView | 中 | P0 |
| **相册网格** | GalleryGridView | QQuickWidget (已部分QML) | 低 | P0 |
| **详情页** | DetailPageWidget | QWidget | 高 | P1 |
| **编辑侧栏** | EditSidebar | QWidget + 多个子组件 | 高 | P1 |
| **图片查看器** | GLImageViewer | QOpenGLWidget | 高 | P1 |
| **裁剪工具** | GLCrop 系列 | QOpenGLWidget + 复杂逻辑 | 极高 | P2 |
| **地图视图** | PhotoMapView | 自定义 OpenGL 渲染 | 极高 | P2 |
| **播放器** | PlayerBar | QWidget | 中 | P1 |
| **胶片条** | FilmstripView | QListView | 中 | P1 |
| **对话框** | 各种 Dialog | QDialog | 低 | P2 |
| **顶栏/状态栏** | Header/StatusBar | QWidget | 低 | P0 |

**总计**：约 **60 个 Python Widget 文件**，12,000+ 行代码

---

## 3. 架构设计

### 3.1 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│                      QML Frontend (UI)                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ main.qml (ApplicationWindow)                          │  │
│  │  ├─ AlbumSidebar.qml                                  │  │
│  │  ├─ GalleryView.qml                                   │  │
│  │  ├─ DetailView.qml                                    │  │
│  │  │   ├─ ImageViewer.qml (ShaderEffect)               │  │
│  │  │   ├─ CropTool.qml                                  │  │
│  │  │   └─ EditToolbar.qml                               │  │
│  │  ├─ MapView.qml                                       │  │
│  │  └─ PlayerControls.qml                                │  │
│  └───────────────────────────────────────────────────────┘  │
│                            ▲                                 │
│                            │ Property Bindings / Signals     │
│                            ▼                                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │          QML-Exposed Controllers (QObject)            │  │
│  │  ├─ AlbumTreeController                               │  │
│  │  ├─ AssetListController                               │  │
│  │  ├─ EditSessionController                             │  │
│  │  └─ ThemeController                                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ▲
                             │ Signals/Slots
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              Python Backend (Logic & Data)                   │
│  ├─ AppFacade (QObject)                                     │
│  ├─ Services (Import/Scan/Move)                             │
│  ├─ Models (AssetListModel, AlbumTreeModel)                 │
│  └─ Core Logic (Scanner, Pairing, Filters, Database)        │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 关键原则

1. **分层清晰**
   - **QML Layer**：纯声明式 UI，不包含业务逻辑
   - **Controller Layer**：QObject 子类，暴露属性/信号/槽给 QML
   - **Backend Layer**：纯 Python 逻辑，与 UI 完全解耦

2. **数据流向**
   ```
   User Interaction (QML)
         ↓
   Signal → Controller Method
         ↓
   Facade/Service Operation
         ↓
   Model Update (Qt Signal)
         ↓
   Property Binding → QML Auto-Update
   ```

3. **渲染策略**
   - 普通 UI 组件：纯 QML
   - 图片查看器：QML ShaderEffect + 自定义纹理提供者
   - 地图渲染：QQuickFramebufferObject（保留 OpenGL 逻辑）
   - 裁剪工具：QML Canvas + JavaScript（或 C++ QQuickItem）

---

## 4. 技术选型

### 4.1 核心技术栈

| 技术 | 用途 | 说明 |
|-----|------|------|
| **QML 6.x** | UI 声明 | Qt Quick 2.15+ 特性 |
| **Qt Quick Controls 2** | 标准控件 | Button, Slider, ListView 等 |
| **QQmlApplicationEngine** | QML 引擎 | 替代 QApplication + QMainWindow |
| **QObject (Python)** | Controller | 通过 @Property / @Signal / @Slot 暴露 |
| **Qt Quick Scene Graph** | 自定义渲染 | 用于 OpenGL 集成 |
| **QQuickImageProvider** | 图片缓存 | 缩略图异步加载 |

### 4.2 渲染方案对比

#### A. 图片查看器（GLImageViewer）

| 方案 | 实现方式 | 优点 | 缺点 | 建议 |
|-----|---------|------|------|------|
| **方案1** | QML Image + QQuickImageProvider | 简单易实现 | 无法直接应用 GLSL Shader | ❌ |
| **方案2** | QML ShaderEffect | 可使用自定义 Fragment Shader | 需要手动纹理上传 | ✅ 推荐 |
| **方案3** | QQuickFramebufferObject | 完全控制 OpenGL 渲染 | 实现复杂，需要 C++ | ⚠️ 备选 |

**推荐方案**：**ShaderEffect + QQuickTextureProvider**
```qml
ShaderEffect {
    vertexShader: "image_viewer.vert.qsb"
    fragmentShader: "image_viewer.frag.qsb"
    property variant texture: textureProvider.texture
    property real exposure: editSession.exposure
    property real contrast: editSession.contrast
    // ...
}
```

#### B. 地图视图（PhotoMapView）

| 方案 | 建议 |
|-----|------|
| **保留现有方案** | 继续使用 `QQuickFramebufferObject`，Python 包装为 QObject 暴露给 QML |
| **优化** | 将 `map_gl_widget.py` 重构为更清晰的接口 |

#### C. 裁剪工具（GLCrop）

| 方案 | 实现方式 | 建议 |
|-----|---------|------|
| **方案1** | QML Canvas + JavaScript | 性能可能不足，难以实现透视变换 | ❌ |
| **方案2** | QQuickPaintedItem (Python) | 需要继承 C++ 类 | ⚠️ |
| **方案3** | QQuickFramebufferObject | 保留现有 OpenGL 逻辑 | ✅ 推荐 |

**推荐方案**：将 `gl_crop/` 模块封装为 QObject，提供 `CropBoxState` 属性给 QML

---

## 5. 实施步骤

### 5.1 总体策略

采用 **渐进式迁移 + 并行开发** 的策略：
1. 不破坏现有功能
2. 逐模块迁移验证
3. 新旧版本可共存（通过配置切换）
4. 最后完全移除旧代码

### 5.2 详细步骤（6个阶段）

---

#### **阶段 1：基础设施搭建** [2周]

**目标**：建立 QML 应用骨架和开发环境

##### 1.1 创建 QML 项目结构

```
src/iPhoto/gui/
├── qml_main.py               # 新的 QML 应用入口
├── qml/
│   ├── main.qml             # 根 ApplicationWindow
│   ├── qmldir               # QML 模块定义
│   ├── components/          # 可复用组件
│   │   ├── Button.qml
│   │   ├── Slider.qml
│   │   └── ...
│   ├── views/               # 主要视图
│   │   ├── AlbumSidebar.qml
│   │   ├── GalleryView.qml
│   │   ├── DetailView.qml
│   │   └── MapView.qml
│   ├── dialogs/             # 对话框
│   ├── styles/              # 主题样式
│   │   └── Theme.qml
│   └── shaders/             # GLSL 着色器
│       ├── image_viewer.vert
│       └── image_viewer.frag
└── ui/controllers/
    └── qml_controllers.py   # QML 控制器桥接
```

##### 1.2 设置 QML 引擎（伪代码）

```python
# qml_main.py
class QMLApplication:
    def __init__(self, context: AppContext):
        self.engine = QQmlApplicationEngine()
        
        # 注册 Controllers 到 QML
        self._register_controllers(context)
        
        # 注册自定义类型
        qmlRegisterType(ImageTextureProvider, "iPhoto", 1, 0, "ImageTexture")
        
        # 加载主 QML 文件
        self.engine.load(QML_DIR / "main.qml")
    
    def _register_controllers(self, context):
        # 暴露 Controllers 作为 Context Properties
        root = self.engine.rootContext()
        root.setContextProperty("albumController", AlbumController(context))
        root.setContextProperty("assetController", AssetController(context))
        root.setContextProperty("themeController", ThemeController())
```

##### 1.3 主题系统（伪代码）

```qml
// styles/Theme.qml
pragma Singleton
import QtQuick 2.15

QtObject {
    // Colors
    property color background: "#1E1E1E"
    property color sidebar: "#2D2D30"
    property color accent: "#007ACC"
    
    // Typography
    property font titleFont: Qt.font({ family: "Segoe UI", pixelSize: 16 })
    property font bodyFont: Qt.font({ family: "Segoe UI", pixelSize: 14 })
    
    // Metrics
    property int sidebarWidth: 240
    property int headerHeight: 48
}
```

---

#### **阶段 2：基础组件迁移** [3周]

**目标**：迁移最简单的组件，建立迁移模式

##### 2.1 按钮和控件（伪代码）

```qml
// components/Button.qml
import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    id: control
    
    background: Rectangle {
        color: control.pressed ? Theme.accentPressed :
               control.hovered ? Theme.accentHover :
               Theme.accent
        radius: 4
        
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    
    contentItem: Text {
        text: control.text
        font: Theme.bodyFont
        color: "white"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
```

##### 2.2 状态栏和顶栏（伪代码）

```qml
// views/StatusBar.qml
import QtQuick 2.15

Rectangle {
    id: statusBar
    height: 24
    color: Theme.statusBarBackground
    
    Row {
        anchors.fill: parent
        spacing: 8
        padding: 4
        
        Text {
            text: statusController.message
            font: Theme.smallFont
            color: Theme.textSecondary
        }
        
        Text {
            text: qsTr("%1 items").arg(assetController.totalCount)
            font: Theme.smallFont
            color: Theme.textSecondary
        }
    }
}
```

##### 2.3 对话框框架（伪代码）

```qml
// dialogs/BaseDialog.qml
import QtQuick 2.15
import QtQuick.Controls 2.15

Dialog {
    id: dialog
    modal: true
    dim: true
    
    background: Rectangle {
        color: Theme.dialogBackground
        radius: 8
        border.color: Theme.dialogBorder
        border.width: 1
        
        layer.enabled: true
        layer.effect: DropShadow {
            radius: 16
            samples: 32
            color: "#40000000"
        }
    }
}
```

---

#### **阶段 3：侧边栏和导航** [3周]

**目标**：迁移相册树侧边栏

##### 3.1 AlbumTreeController (Python 伪代码)

```python
# controllers/album_tree_controller.py
class AlbumTreeController(QObject):
    # Signals
    modelChanged = Signal()
    selectionChanged = Signal(str)  # album_path
    
    def __init__(self, context: AppContext):
        super().__init__()
        self._model = AlbumTreeModel(context.library)
        self._selection = None
    
    @Property(QObject, notify=modelChanged)
    def model(self):
        return self._model
    
    @Property(str, notify=selectionChanged)
    def currentAlbum(self):
        return self._selection
    
    @Slot(str)
    def selectAlbum(self, path: str):
        if self._selection != path:
            self._selection = path
            self.selectionChanged.emit(path)
```

##### 3.2 AlbumSidebar.qml（伪代码）

```qml
// views/AlbumSidebar.qml
import QtQuick 2.15
import QtQuick.Controls 2.15

Rectangle {
    id: sidebar
    width: Theme.sidebarWidth
    color: Theme.sidebar
    
    ListView {
        anchors.fill: parent
        model: albumController.model
        
        delegate: ItemDelegate {
            width: parent.width
            height: 32
            
            contentItem: Row {
                spacing: 8
                
                Image {
                    source: "qrc:/icons/" + model.icon
                    width: 16; height: 16
                }
                
                Text {
                    text: model.displayName
                    font: Theme.bodyFont
                    color: Theme.text
                }
            }
            
            onClicked: albumController.selectAlbum(model.path)
            
            background: Rectangle {
                color: model.path === albumController.currentAlbum ?
                       Theme.sidebarSelected : "transparent"
            }
        }
    }
}
```

---

#### **阶段 4：相册网格视图** [2周]

**目标**：完善现有 GalleryGrid.qml，集成缩略图加载

##### 4.1 ThumbnailImageProvider (Python 伪代码)

```python
# controllers/thumbnail_provider.py
class ThumbnailImageProvider(QQuickImageProvider):
    def __init__(self, cache_manager):
        super().__init__(QQuickImageProvider.Pixmap)
        self._cache = cache_manager
    
    def requestPixmap(self, id: str, size, requestedSize):
        rel_path = id.split('?')[0]
        pixmap = self._cache.thumbnail_for(rel_path)
        return pixmap or QPixmap()  # Fallback to empty
```

##### 4.2 GalleryView.qml（伪代码）

```qml
// views/GalleryView.qml
import QtQuick 2.15

GridView {
    id: grid
    model: assetController.model
    cellWidth: 180
    cellHeight: 180
    
    delegate: Rectangle {
        width: grid.cellWidth - 8
        height: grid.cellHeight - 8
        color: "transparent"
        
        Image {
            id: thumbnail
            anchors.fill: parent
            anchors.margins: 4
            source: "image://thumbnails/" + model.relativePath
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: false  // Provider handles caching
            
            // Loading placeholder
            Rectangle {
                anchors.fill: parent
                color: Theme.placeholderBackground
                visible: thumbnail.status === Image.Loading
            }
        }
        
        MouseArea {
            anchors.fill: parent
            onClicked: assetController.selectAsset(index)
            onDoubleClicked: assetController.openDetail(index)
        }
    }
}
```

---

#### **阶段 5：详情页和编辑器** [6周]

**目标**：迁移图片查看器和编辑工具

##### 5.1 图片查看器（ShaderEffect 方案伪代码）

```qml
// views/ImageViewer.qml
import QtQuick 2.15

ShaderEffect {
    id: viewer
    
    property variant source: imageTextureProvider.texture
    property real exposure: editSession.exposure
    property real contrast: editSession.contrast
    property real saturation: editSession.saturation
    // ... 其他编辑参数
    
    vertexShader: "qrc:/shaders/image_viewer.vert.qsb"
    fragmentShader: "qrc:/shaders/image_viewer.frag.qsb"
    
    // 鼠标交互
    PinchArea {
        anchors.fill: parent
        onPinchUpdated: {
            viewer.scale = pinch.scale
        }
    }
    
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onWheel: {
            // Zoom logic
        }
    }
}
```

##### 5.2 着色器示例（GLSL 伪代码）

```glsl
// shaders/image_viewer.frag
#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(binding = 1) uniform sampler2D source;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float exposure;
    float contrast;
    float saturation;
};

void main() {
    vec4 color = texture(source, qt_TexCoord0);
    
    // Apply exposure
    color.rgb *= pow(2.0, exposure);
    
    // Apply contrast
    color.rgb = (color.rgb - 0.5) * contrast + 0.5;
    
    // Apply saturation
    float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    color.rgb = mix(vec3(gray), color.rgb, saturation);
    
    fragColor = color * qt_Opacity;
}
```

##### 5.3 编辑侧栏（伪代码）

```qml
// views/EditSidebar.qml
import QtQuick 2.15

Rectangle {
    id: sidebar
    width: 280
    color: Theme.sidebar
    
    Flickable {
        anchors.fill: parent
        contentHeight: column.height
        
        Column {
            id: column
            width: parent.width
            spacing: 0
            
            // Light Section
            CollapsibleSection {
                title: qsTr("Light")
                
                Column {
                    SliderRow {
                        label: qsTr("Brilliance")
                        value: editSession.brilliance
                        onValueChanged: editSession.brilliance = value
                    }
                    SliderRow {
                        label: qsTr("Exposure")
                        value: editSession.exposure
                        onValueChanged: editSession.exposure = value
                    }
                    // ... 其他滑块
                }
            }
            
            // Color Section
            CollapsibleSection {
                title: qsTr("Color")
                // ...
            }
            
            // B&W Section
            CollapsibleSection {
                title: qsTr("Black & White")
                // ...
            }
        }
    }
}
```

##### 5.4 裁剪工具（使用 QQuickFramebufferObject 伪代码）

```python
# widgets/crop_tool_item.py
class CropToolItem(QQuickFramebufferObject):
    """QML 可用的裁剪工具（保留 OpenGL 渲染）"""
    
    cropBoxChanged = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller = CropController()
    
    @Property(QRectF, notify=cropBoxChanged)
    def cropBox(self):
        return self._controller.crop_rect
    
    @Slot(QPointF)
    def handleMousePress(self, pos):
        self._controller.on_mouse_press(pos)
    
    def createRenderer(self):
        return CropRenderer(self._controller)
```

```qml
// components/CropTool.qml
import iPhoto 1.0

CropToolItem {
    id: cropTool
    anchors.fill: parent
    
    MouseArea {
        anchors.fill: parent
        onPressed: cropTool.handleMousePress(Qt.point(mouse.x, mouse.y))
        onPositionChanged: cropTool.handleMouseMove(Qt.point(mouse.x, mouse.y))
        onReleased: cropTool.handleMouseRelease()
    }
}
```

---

#### **阶段 6：地图视图和收尾** [4周]

##### 6.1 地图视图（伪代码）

```python
# widgets/map_view_item.py
class MapViewItem(QQuickFramebufferObject):
    """封装现有地图渲染逻辑为 QML 组件"""
    
    centerChanged = Signal()
    zoomChanged = Signal()
    
    @Property(QPointF, notify=centerChanged)
    def center(self):
        return self._map_widget.center
    
    def createRenderer(self):
        return MapRenderer(self._map_widget)
```

```qml
// views/MapView.qml
import iPhoto 1.0

Item {
    MapViewItem {
        id: map
        anchors.fill: parent
        center: mapController.center
        zoom: mapController.zoom
    }
    
    // Overlay: Asset markers
    Repeater {
        model: mapController.visibleAssets
        
        delegate: Rectangle {
            x: model.screenX - width/2
            y: model.screenY - height/2
            width: 32; height: 32
            radius: 16
            color: Theme.accent
            
            Image {
                anchors.fill: parent
                anchors.margins: 2
                source: "image://thumbnails/" + model.relativePath
                fillMode: Image.PreserveAspectCrop
                layer.enabled: true
                layer.effect: OpacityMask {
                    maskSource: Rectangle { radius: 15 }
                }
            }
        }
    }
}
```

##### 6.2 最终集成（main.qml 伪代码）

```qml
// main.qml
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Window 2.15

ApplicationWindow {
    id: mainWindow
    visible: true
    width: 1280
    height: 800
    title: "iPhoto"
    
    // Custom window chrome
    flags: Qt.FramelessWindowHint | Qt.Window
    
    // Header
    Item {
        id: header
        height: Theme.headerHeight
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        
        CustomTitleBar {
            anchors.fill: parent
        }
    }
    
    // Main layout
    SplitView {
        anchors.top: header.bottom
        anchors.bottom: statusBar.top
        anchors.left: parent.left
        anchors.right: parent.right
        
        // Sidebar
        AlbumSidebar {
            SplitView.minimumWidth: 200
            SplitView.preferredWidth: Theme.sidebarWidth
        }
        
        // Content
        StackLayout {
            currentIndex: viewController.currentViewIndex
            
            GalleryView { }
            DetailView { }
            MapView { }
        }
    }
    
    // Status bar
    StatusBar {
        id: statusBar
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
    }
}
```

---

## 6. 组件迁移清单

### 6.1 优先级划分

| 优先级 | 组件 | 工作量（人天） | 依赖 |
|-------|------|--------------|------|
| **P0** | 主窗口框架 | 3 | 无 |
| **P0** | 主题系统 | 2 | 无 |
| **P0** | 基础控件（Button, Slider等） | 5 | 主题 |
| **P0** | 状态栏/标题栏 | 2 | 框架 |
| **P0** | 相册侧边栏 | 8 | 框架, 控件 |
| **P0** | 相册网格视图 | 5 | 框架, 缩略图 |
| **P1** | 详情页框架 | 3 | 框架 |
| **P1** | 图片查看器（ShaderEffect） | 10 | 详情页 |
| **P1** | 编辑侧栏（Light/Color/BW） | 12 | 控件, 查看器 |
| **P1** | 播放器控件 | 5 | 详情页 |
| **P1** | 胶片条 | 4 | 详情页 |
| **P2** | 裁剪工具 | 15 | 查看器 |
| **P2** | 地图视图 | 10 | 框架 |
| **P2** | 对话框（Preferences等） | 8 | 控件 |
| **P3** | 动画和过渡优化 | 5 | 全部完成 |
| **P3** | 性能优化 | 5 | 全部完成 |

**总计**：约 **102 人天**（约 **20 人周**）

### 6.2 完整组件清单（60个）

| # | Python 文件 | QML 对应文件 | 复杂度 | 优先级 |
|---|------------|-------------|-------|-------|
| 1 | `main_window.py` | `main.qml` | 高 | P0 |
| 2 | `album_sidebar.py` | `AlbumSidebar.qml` | 中 | P0 |
| 3 | `gallery_grid_view.py` | ✅ 已完成 | - | - |
| 4 | `gallery_page.py` | `GalleryView.qml` | 低 | P0 |
| 5 | `detail_page.py` | `DetailView.qml` | 高 | P1 |
| 6 | `edit_sidebar.py` | `EditSidebar.qml` | 高 | P1 |
| 7 | `edit_light_section.py` | `LightSection.qml` | 中 | P1 |
| 8 | `edit_color_section.py` | `ColorSection.qml` | 中 | P1 |
| 9 | `edit_bw_section.py` | `BWSection.qml` | 中 | P1 |
| 10 | `edit_perspective_controls.py` | `PerspectiveSection.qml` | 中 | P2 |
| 11 | `edit_topbar.py` | `EditTopBar.qml` | 低 | P1 |
| 12 | `edit_strip.py` | `SliderRow.qml` | 低 | P1 |
| 13 | `gl_image_viewer/widget.py` | `ImageViewer.qml` | 极高 | P1 |
| 14 | `gl_crop/controller.py` | `CropTool.qml` | 极高 | P2 |
| 15 | `photo_map_view.py` | `MapView.qml` | 极高 | P2 |
| 16 | `player_bar.py` | `PlayerBar.qml` | 中 | P1 |
| 17 | `filmstrip_view.py` | `Filmstrip.qml` | 中 | P1 |
| 18 | `main_header.py` | `MainHeader.qml` | 低 | P0 |
| 19 | `chrome_status_bar.py` | `StatusBar.qml` | 低 | P0 |
| 20 | `custom_title_bar.py` | `TitleBar.qml` | 中 | P0 |
| 21 | `info_panel.py` | `InfoPanel.qml` | 低 | P2 |
| 22 | `notification_toast.py` | `Toast.qml` | 低 | P2 |
| 23 | `dialogs.py` (多个对话框) | `dialogs/*.qml` | 中 | P2 |
| 24 | `collapsible_section.py` | `CollapsibleSection.qml` | 低 | P1 |
| 25 | `sliding_segmented_control.py` | `SegmentedControl.qml` | 低 | P1 |
| 26 | `thumbnail_strip_slider.py` | `ThumbnailSlider.qml` | 中 | P1 |
| 27 | `live_badge.py` | `LiveBadge.qml` | 低 | P0 |
| 28 | `custom_tooltip.py` | `Tooltip.qml` | 低 | P2 |
| 29 | `preview_window.py` | `PreviewWindow.qml` | 中 | P2 |
| 30 | `albums_dashboard.py` | `AlbumsDashboard.qml` | 低 | P2 |
| 31-60 | ... (其他30个组件) | ... | 各异 | P2-P3 |

---

## 7. 关键技术点

### 7.1 QML 与 Python 通信

#### 方式1：Context Properties (推荐用于 Singleton Controller)

```python
engine = QQmlApplicationEngine()
root = engine.rootContext()
root.setContextProperty("albumController", AlbumController())
```

```qml
// 直接使用
Text { text: albumController.currentAlbumName }
```

#### 方式2：注册 QML 类型

```python
qmlRegisterType(CropToolItem, "iPhoto", 1, 0, "CropTool")
```

```qml
import iPhoto 1.0

CropTool {
    id: crop
}
```

#### 方式3：在 Controller 中暴露 Model

```python
class AssetController(QObject):
    @Property(QObject, constant=True)
    def model(self):
        return self._asset_list_model
```

```qml
ListView {
    model: assetController.model
}
```

### 7.2 着色器编译

Qt 6 要求使用预编译的 `.qsb` 格式（Qt Shader Baker）：

```bash
# 编译着色器
qsb --glsl "100 es,120" --hlsl 50 --msl 12 -o image_viewer.frag.qsb image_viewer.frag
qsb --glsl "100 es,120" --hlsl 50 --msl 12 -o image_viewer.vert.qsb image_viewer.vert
```

在项目中集成：
```python
# setup.py 或 pyproject.toml
[tool.setuptools.package-data]
"iPhoto.gui.qml.shaders" = ["*.qsb"]
```

### 7.3 QML 缓存和资源系统

#### 方案1：使用 Qt Resource System (推荐)

```xml
<!-- resources.qrc -->
<RCC>
    <qresource prefix="/qml">
        <file>main.qml</file>
        <file>views/AlbumSidebar.qml</file>
        <!-- ... -->
    </qresource>
</RCC>
```

编译：
```bash
pyside6-rcc resources.qrc -o resources_rc.py
```

使用：
```python
import resources_rc  # noqa
engine.load("qrc:/qml/main.qml")
```

#### 方案2：直接加载文件（开发时更方便）

```python
engine.load(QML_DIR / "main.qml")
```

### 7.4 性能优化技巧

| 优化点 | 说明 |
|-------|------|
| **异步加载** | 使用 `Loader { asynchronous: true }` |
| **延迟实例化** | 使用 `Loader { active: visible }` |
| **缓存** | 设置 `Image { cache: true }` |
| **Layer 优化** | 复杂组件使用 `layer.enabled: true` |
| **减少绑定** | 避免频繁变化的属性绑定，改用 `Connections` |
| **使用 FastBlur** | 模糊效果使用 `FastBlur` 而非 `GaussianBlur` |

### 7.5 调试技巧

| 技巧 | 命令/方法 |
|-----|---------|
| **QML Profiler** | `QQmlEngine::setObjectOwnership()` + Qt Creator Profiler |
| **Console 输出** | `console.log()`, `console.warn()`, `console.error()` |
| **QML 调试器** | 设置 `QT_QML_DEBUG=1` 环境变量 |
| **属性监控** | `onPropertyChanged: console.log(property)` |

---

## 8. 风险与挑战

### 8.1 技术风险

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| **OpenGL 渲染性能** | 图片查看器、裁剪工具性能下降 | • 使用 QQuickFramebufferObject 保留现有渲染逻辑<br>• 性能测试和 Profiling |
| **着色器兼容性** | 不同平台着色器不工作 | • 使用 qsb 编译多版本<br>• 提供 Fallback 方案 |
| **QML 调试困难** | Bug 难以定位 | • 启用 QML Profiler<br>• 使用 `console.log`<br>• 单元测试 Controller |
| **大数据量性能** | GridView 卡顿 | • 使用 `cacheBuffer`<br>• 异步加载<br>• 虚拟化（DelegateModel） |

### 8.2 开发风险

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| **学习曲线** | 团队不熟悉 QML | • 前期培训<br>• 建立 QML 最佳实践文档<br>• Code Review |
| **工期延误** | 低估复杂度 | • 迭代开发<br>• 保留旧版本并行<br>• 每周 Review 进度 |
| **功能回归** | 迁移时遗漏功能 | • 完整的回归测试<br>• Feature Checklist<br>• Beta 测试 |

### 8.3 兼容性风险

| 平台 | 风险 | 处理 |
|-----|------|------|
| **Windows** | DWM 透明窗口问题 | 已通过 `WA_NativeWindow` 解决 |
| **macOS** | Metal 着色器支持 | qsb 自动生成 `.msl` |
| **Linux** | 不同桌面环境兼容性 | 测试 Gnome/KDE/Xfce |

---

## 9. 测试策略

### 9.1 单元测试

**Python Controller 层**：
```python
# tests/test_qml_controllers.py
def test_album_controller_selection():
    controller = AlbumController(mock_context)
    controller.selectAlbum("/Albums/2023")
    assert controller.currentAlbum == "/Albums/2023"
```

**QML 组件测试**：
```qml
// tests/tst_AlbumSidebar.qml
import QtTest 1.15

TestCase {
    name: "AlbumSidebarTests"
    
    AlbumSidebar {
        id: sidebar
    }
    
    function test_selection() {
        sidebar.selectAlbum("/Albums/2023")
        compare(sidebar.currentAlbum, "/Albums/2023")
    }
}
```

### 9.2 集成测试

使用 `pytest` + `QTest`：
```python
# tests/test_gallery_integration.py
def test_thumbnail_loading(qtbot):
    app = QMLApplication(test_context)
    root = app.engine.rootObjects()[0]
    
    # 等待加载完成
    qtbot.waitUntil(lambda: root.property("loaded"), timeout=5000)
    
    # 验证缩略图数量
    grid_view = root.findChild(QQuickItem, "galleryGrid")
    assert grid_view.property("count") == 100
```

### 9.3 UI 测试

使用 **Squish** 或 **Qt Test** 进行自动化 UI 测试：
```python
# UI 测试伪代码
def test_edit_workflow():
    # 1. 打开相册
    click("AlbumSidebar", "Vacation 2023")
    
    # 2. 双击打开详情
    doubleClick("GalleryGrid", index=0)
    
    # 3. 调整 Exposure
    drag_slider("EditSidebar.exposure", value=0.5)
    
    # 4. 保存
    click("EditTopBar.doneButton")
    
    # 5. 验证 sidecar 文件
    assert Path(".ipo").exists()
```

### 9.4 性能测试

| 测试项 | 指标 | 工具 |
|-------|------|------|
| **启动时间** | < 2s | 计时代码 |
| **缩略图加载** | 60 FPS | QML Profiler |
| **编辑响应** | < 16ms | QML Profiler |
| **内存占用** | < 500MB (1000张照片) | valgrind / heaptrack |

---

## 10. 时间与资源估算

### 10.1 总体时间线

| 阶段 | 工期 | 产出 |
|-----|------|------|
| 阶段1：基础设施 | 2周 | QML 引擎、主题系统 |
| 阶段2：基础组件 | 3周 | 按钮、对话框、状态栏 |
| 阶段3：侧边栏和导航 | 3周 | 相册树、网格视图 |
| 阶段4：相册网格 | 2周 | 完整网格功能 |
| 阶段5：详情页和编辑器 | 6周 | 查看器、编辑工具 |
| 阶段6：地图和收尾 | 4周 | 地图、集成测试 |
| **总计** | **20周** | 完整 QML 应用 |

### 10.2 人力需求

| 角色 | 人数 | 职责 |
|-----|------|------|
| **QML 开发工程师** | 2 | 编写 QML 界面 |
| **Python 后端工程师** | 1 | Controller 层、数据绑定 |
| **OpenGL 工程师** | 1 | 图片查看器、裁剪工具渲染 |
| **测试工程师** | 1 | 测试用例、自动化测试 |
| **项目经理** | 0.5 | 进度跟踪、协调 |

**总计**：约 **5.5 人** × **20 周** ≈ **110 人周**

### 10.3 成本估算（参考）

假设：
- 平均人天成本：¥800
- 总人天：110 人周 × 5 天 = 550 人天
- **总成本**：¥440,000

---

## 11. 附录

### 11.1 参考资源

| 资源 | 链接 |
|-----|------|
| **Qt QML 文档** | https://doc.qt.io/qt-6/qmlapplications.html |
| **Qt Quick Controls** | https://doc.qt.io/qt-6/qtquickcontrols-index.html |
| **PySide6 示例** | https://doc.qt.io/qtforpython-6/examples/index.html |
| **ShaderEffect** | https://doc.qt.io/qt-6/qml-qtquick-shadereffect.html |
| **QQuickFramebufferObject** | https://doc.qt.io/qt-6/qquickframebufferobject.html |

### 11.2 术语表

| 术语 | 说明 |
|-----|------|
| **QML** | Qt Meta-Object Language，Qt 的声明式 UI 语言 |
| **Qt Quick** | 基于 QML 的 UI 框架 |
| **Scene Graph** | Qt Quick 的渲染引擎 |
| **QQuickItem** | QML 中所有可视元素的基类 |
| **Context Property** | 从 Python 暴露给 QML 的全局对象 |
| **qmlRegisterType** | 注册 Python 类型为 QML 类型 |
| **ShaderEffect** | QML 中应用自定义 GLSL 着色器的组件 |
| **QQuickFramebufferObject** | 自定义 OpenGL 渲染的 QML 组件基类 |

### 11.3 示例项目结构（最终形态）

```
iPhoto/
├── src/iPhoto/
│   ├── gui/
│   │   ├── qml_main.py              # QML 应用入口 ✅
│   │   ├── qml/
│   │   │   ├── main.qml             # 主窗口 ✅
│   │   │   ├── qmldir               # 模块定义 ✅
│   │   │   ├── components/          # 可复用组件 ✅
│   │   │   │   ├── Button.qml
│   │   │   │   ├── Slider.qml
│   │   │   │   ├── CollapsibleSection.qml
│   │   │   │   └── ...
│   │   │   ├── views/               # 主要视图 ✅
│   │   │   │   ├── AlbumSidebar.qml
│   │   │   │   ├── GalleryView.qml
│   │   │   │   ├── DetailView.qml
│   │   │   │   ├── EditSidebar.qml
│   │   │   │   ├── ImageViewer.qml
│   │   │   │   ├── MapView.qml
│   │   │   │   └── ...
│   │   │   ├── dialogs/             # 对话框 ✅
│   │   │   │   ├── PreferencesDialog.qml
│   │   │   │   └── ...
│   │   │   ├── styles/              # 主题 ✅
│   │   │   │   └── Theme.qml
│   │   │   └── shaders/             # 着色器 ✅
│   │   │       ├── image_viewer.frag.qsb
│   │   │       └── image_viewer.vert.qsb
│   │   └── ui/
│   │       ├── controllers/
│   │       │   ├── qml_controllers.py   # QML 控制器 ✅
│   │       │   ├── album_controller.py
│   │       │   ├── asset_controller.py
│   │       │   └── ...
│   │       ├── models/                  # 保持不变 ✅
│   │       └── widgets/
│   │           ├── crop_tool_item.py    # 自定义 QML 组件 ✅
│   │           ├── map_view_item.py
│   │           └── ...
│   └── ... (后端代码不变)
└── tests/
    ├── qml/
    │   ├── tst_AlbumSidebar.qml        # QML 组件测试 ✅
    │   └── ...
    └── test_qml_controllers.py         # Controller 测试 ✅
```

---

## 总结

本文档提供了将 iPhoto 重构为纯 QML 界面的完整方案，包括：

1. ✅ **明确的目标和收益**（性能、可维护性、现代化）
2. ✅ **详细的架构设计**（三层分离：QML → Controller → Backend）
3. ✅ **6个阶段的实施步骤**（从基础设施到最终集成）
4. ✅ **60个组件的迁移清单**（包含优先级和工作量估算）
5. ✅ **关键技术点**（ShaderEffect、QQuickFramebufferObject、性能优化）
6. ✅ **风险评估和测试策略**（技术风险、开发风险、测试方案）
7. ✅ **时间和资源估算**（20周，5.5人，110人周）

该方案采用 **渐进式迁移** 策略，确保在重构过程中不影响现有功能，最终实现：
- 🎯 统一的 QML 技术栈
- 🚀 更好的性能和用户体验
- 🧹 更清晰的代码结构
- 📦 更易于维护和扩展

---

**建议下一步行动**：

1. **团队 Review 本方案**
   - 与开发团队讨论技术选型
   - 确认时间线和资源分配
   - 识别潜在的技术难点

2. **准备开发环境**
   - 搭建 QML 开发工具链
   - 配置 qsb 着色器编译工具
   - 准备测试环境

3. **启动阶段1（基础设施搭建）**
   - 创建 QML 项目结构
   - 实现 QML 引擎初始化
   - 建立主题系统
   - 创建基础组件库

4. **建立持续集成**
   - 配置 QML 自动测试
   - 设置性能基准测试
   - 准备 Beta 测试渠道

---

**文档版本历史**：
- V1.0 (2026-01-11): 初始版本

**维护者**：iPhoto 开发团队

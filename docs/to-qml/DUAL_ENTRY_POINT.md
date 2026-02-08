# 🔀 双入口实现指南 / Dual Entry Point Implementation Guide

> **版本 / Version:** 1.0  
> **创建日期 / Created:** 2026-02-08  
> **关联文档 / Related:** [MIGRATION_PLAN.md](./MIGRATION_PLAN.md)

---

## 📑 目录 / Table of Contents

1. [概述 / Overview](#1-概述--overview)
2. [当前入口分析 / Current Entry Point Analysis](#2-当前入口分析--current-entry-point-analysis)
3. [双入口架构设计 / Dual Entry Architecture](#3-双入口架构设计--dual-entry-architecture)
4. [共享层提取 / Shared Layer Extraction](#4-共享层提取--shared-layer-extraction)
5. [Widget 入口实现 / Widget Entry (main.py)](#5-widget-入口实现--widget-entry-mainpy)
6. [QML 入口实现 / QML Entry (main_qml.py)](#6-qml-入口实现--qml-entry-main_qmlpy)
7. [pyproject.toml 配置 / Configuration](#7-pyprojecttoml-配置--configuration)
8. [ViewModel QML 适配 / ViewModel QML Adaptation](#8-viewmodel-qml-适配--viewmodel-qml-adaptation)
9. [Coordinator QML 适配 / Coordinator QML Adaptation](#9-coordinator-qml-适配--coordinator-qml-adaptation)
10. [测试策略 / Testing Strategy](#10-测试策略--testing-strategy)

---

## 1. 概述 / Overview

iPhotron 采用**双入口**设计，允许用户选择传统 Widget 界面或纯 QML 界面启动应用：

```
┌─────────────────────────────────────────────────┐
│                  用户选择启动方式                   │
│                                                 │
│    iphoto-gui  (传统 Widget)                     │
│         │                                       │
│         ▼                                       │
│    main.py ──┐                                  │
│              │    ┌─────────────────────┐        │
│              ├───▶│   bootstrap.py      │        │
│              │    │  (共享 DI 初始化)     │        │
│    main_qml.py──┘ └─────────┬───────────┘        │
│         ▲                   │                    │
│         │                   ▼                    │
│    iphoto-qml (纯 QML)      │                    │
│                   ┌─────────┴───────────┐        │
│                   │  共享业务层           │        │
│                   │  Domain / App / Infra │        │
│                   │  ViewModels / Facade │        │
│                   └─────────────────────┘        │
└─────────────────────────────────────────────────┘
```

### 设计原则

1. **DRY（Don't Repeat Yourself）**: DI 容器初始化、ViewModel 创建、Service 注册只写一次
2. **接口隔离**: Widget 入口只依赖 Widget 组件，QML 入口只依赖 QML 文件
3. **共享后端**: 两个入口共享完全相同的 Domain / Application / Infrastructure 层
4. **独立运行**: 两个入口可以独立安装、独立运行、互不影响

---

## 2. 当前入口分析 / Current Entry Point Analysis

### 2.1 现有 `main.py` 结构

```python
# src/iPhoto/gui/main.py (现有，简化版)
def main(argv=None):
    app = QApplication(argv or sys.argv)

    # Phase 1: Infrastructure
    container = DependencyContainer()
    bus = EventBus()
    container.register_singleton(EventBus, bus)
    pool = ConnectionPool(...)
    container.register_singleton(ConnectionPool, pool)
    container.register_singleton(IAlbumRepository, SQLiteAlbumRepository(pool))
    container.register_singleton(IAssetRepository, SQLiteAssetRepository(pool))
    container.register_singleton(IMetadataProvider, ExifToolMetadataProvider())
    container.register_singleton(IThumbnailGenerator, PillowThumbnailGenerator())

    # Phase 2: Application
    album_svc = AlbumService(container.resolve(IAlbumRepository))
    asset_svc = AssetService(container.resolve(IAssetRepository))
    container.register_singleton(AlbumService, album_svc)
    container.register_singleton(AssetService, asset_svc)
    # Use Cases...
    open_album_uc = OpenAlbumUseCase(album_svc, asset_svc)
    scan_album_uc = ScanAlbumUseCase(...)
    pair_live_uc  = PairLivePhotosUseCase(...)

    # Phase 3: UI (Widget 特有)
    context = AppContext(...)
    window = MainWindow(context)
    coordinator = MainCoordinator(window, context, container)
    window.set_coordinator(coordinator)
    coordinator.start()
    window.show()

    return app.exec()
```

### 2.2 依赖关系

```
main.py 依赖:
├── QApplication                     # Widget 特有
├── DependencyContainer              # 共享
├── EventBus                         # 共享
├── ConnectionPool                   # 共享
├── SQLiteAlbumRepository            # 共享
├── SQLiteAssetRepository            # 共享
├── ExifToolMetadataProvider         # 共享
├── PillowThumbnailGenerator         # 共享
├── AlbumService                     # 共享
├── AssetService                     # 共享
├── OpenAlbumUseCase                 # 共享
├── ScanAlbumUseCase                 # 共享
├── PairLivePhotosUseCase            # 共享
├── AppContext                       # Widget 特有
├── MainWindow (QMainWindow)         # Widget 特有
└── MainCoordinator                  # 部分共享
```

---

## 3. 双入口架构设计 / Dual Entry Architecture

### 3.1 文件结构

```
src/iPhoto/gui/
├── main.py              # Widget 入口 (保留，重构提取共享逻辑)
├── main_qml.py          # QML 入口 (新增)
├── bootstrap.py         # 共享初始化 (新增)
├── facade.py            # AppFacade (添加 @Property 装饰)
├── coordinators/        # 协调器 (添加 @Slot 装饰)
│   ├── main_coordinator.py
│   ├── navigation_coordinator.py
│   ├── playback_coordinator.py
│   ├── edit_coordinator.py
│   └── view_router.py
├── viewmodels/          # ViewModel (添加 roleNames, @Property)
│   ├── asset_list_viewmodel.py
│   ├── asset_data_source.py
│   └── album_viewmodel.py
├── services/            # 服务 (共享不变)
└── ui/
    ├── widgets/         # Widget 专用
    ├── controllers/     # Widget 专用
    ├── delegates/       # Widget 专用
    ├── models/          # 共享
    ├── tasks/           # 共享
    ├── menus/           # Widget 专用
    ├── icon/            # 共享
    └── qml/             # QML 专用
```

### 3.2 共享 vs 专用边界

```
                    ┌─────────────────────────────────────┐
                    │         共享层 (Shared)              │
                    │                                     │
                    │  bootstrap.py                       │
                    │  facade.py                          │
                    │  coordinators/*                     │
                    │  viewmodels/*                       │
                    │  services/*                         │
                    │  ui/models/*                        │
                    │  ui/tasks/*                         │
                    │  ui/icon/*                          │
                    │                                     │
        ┌───────────┤                                     ├───────────┐
        │           └─────────────────────────────────────┘           │
        │                                                             │
        ▼                                                             ▼
┌───────────────────┐                                 ┌───────────────────┐
│  Widget 专用       │                                 │  QML 专用          │
│                   │                                 │                   │
│  main.py          │                                 │  main_qml.py      │
│  ui/widgets/*     │                                 │  ui/qml/*         │
│  ui/controllers/* │                                 │    views/         │
│  ui/delegates/*   │                                 │    components/    │
│  ui/menus/*       │                                 │    dialogs/       │
│  main_window.py   │                                 │    styles/        │
└───────────────────┘                                 └───────────────────┘
```

---

## 4. 共享层提取 / Shared Layer Extraction

### 4.1 bootstrap.py 完整实现

```python
"""
Shared bootstrap logic for both Widget and QML entry points.

This module extracts DI container setup from main.py to avoid
code duplication between Widget and QML entry points.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

from iPhoto.di.container import DependencyContainer
from iPhoto.events.bus import EventBus
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.infrastructure.repositories.album_repository import SQLiteAlbumRepository
from iPhoto.infrastructure.repositories.asset_repository import SQLiteAssetRepository
from iPhoto.infrastructure.services.metadata_provider import ExifToolMetadataProvider
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.application.services.album_service import AlbumService
from iPhoto.application.services.asset_service import AssetService
from iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from iPhoto.application.interfaces import (
    IAlbumRepository,
    IAssetRepository,
    IMetadataProvider,
    IThumbnailGenerator,
)
from iPhoto.gui.facade import AppFacade
from iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from iPhoto.gui.viewmodels.album_viewmodel import AlbumViewModel
from iPhoto.gui.coordinators.view_router import ViewRouter


class AppComponents(NamedTuple):
    """All shared components created during bootstrap."""
    container: DependencyContainer
    facade: AppFacade
    asset_list_vm: AssetListViewModel
    album_vm: AlbumViewModel
    view_router: ViewRouter
    event_bus: EventBus


def create_container() -> DependencyContainer:
    """
    Create and configure DI container with all infrastructure
    and application layer registrations.

    Returns:
        Fully configured DependencyContainer.
    """
    container = DependencyContainer()

    # ── Infrastructure ──
    bus = EventBus()
    container.register_singleton(EventBus, bus)

    db_path = _resolve_db_path()
    pool = ConnectionPool(db_path)
    container.register_singleton(ConnectionPool, pool)

    container.register_singleton(
        IAlbumRepository, SQLiteAlbumRepository(pool)
    )
    container.register_singleton(
        IAssetRepository, SQLiteAssetRepository(pool)
    )
    container.register_singleton(
        IMetadataProvider, ExifToolMetadataProvider()
    )
    container.register_singleton(
        IThumbnailGenerator, PillowThumbnailGenerator()
    )

    # ── Application Services ──
    album_repo = container.resolve(IAlbumRepository)
    asset_repo = container.resolve(IAssetRepository)

    album_svc = AlbumService(album_repo)
    asset_svc = AssetService(asset_repo)
    container.register_singleton(AlbumService, album_svc)
    container.register_singleton(AssetService, asset_svc)

    # ── Use Cases ──
    container.register_singleton(
        OpenAlbumUseCase,
        OpenAlbumUseCase(album_svc, asset_svc),
    )
    container.register_singleton(
        ScanAlbumUseCase,
        ScanAlbumUseCase(
            container.resolve(IMetadataProvider),
            container.resolve(IThumbnailGenerator),
            asset_repo,
        ),
    )
    container.register_singleton(
        PairLivePhotosUseCase,
        PairLivePhotosUseCase(asset_repo),
    )

    return container


def create_shared_components(container: DependencyContainer) -> AppComponents:
    """
    Create ViewModels, Facade, and other shared components
    that both Widget and QML entry points need.

    Args:
        container: Configured DependencyContainer.

    Returns:
        AppComponents namedtuple with all shared objects.
    """
    bus = container.resolve(EventBus)

    # Facade
    facade = AppFacade()

    # ViewModels
    data_source = AssetDataSource()
    data_source.set_repository(container.resolve(IAssetRepository))
    asset_list_vm = AssetListViewModel(data_source, None)

    album_vm = AlbumViewModel()

    # ViewRouter
    view_router = ViewRouter()

    return AppComponents(
        container=container,
        facade=facade,
        asset_list_vm=asset_list_vm,
        album_vm=album_vm,
        view_router=view_router,
        event_bus=bus,
    )


def _resolve_db_path() -> Path:
    """Resolve the global database path."""
    # 使用与当前 main.py 相同的逻辑
    home = Path.home()
    return home / ".iphoto" / "global_index.db"
```

### 4.2 main.py 重构

将现有 `main.py` 重构为使用 `bootstrap.py`：

```python
"""Traditional Widget entry point for iPhotron (保留)."""
import sys
from PySide6.QtWidgets import QApplication
from iPhoto.gui.bootstrap import create_container, create_shared_components
from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.coordinators.main_coordinator import MainCoordinator


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    app = QApplication(argv)
    app.setStyle("Fusion")

    # 共享初始化
    container = create_container()
    components = create_shared_components(container)

    # Widget 特有: 创建 MainWindow + MainCoordinator
    context = _build_app_context(components)
    window = MainWindow(context)
    coordinator = MainCoordinator(window, context, container)
    window.set_coordinator(coordinator)

    # 启动
    coordinator.start()
    window.show()

    # 可选: 从命令行打开相册
    if len(argv) > 1:
        window.open_album_from_path(argv[1])

    return app.exec()
```

---

## 5. Widget 入口实现 / Widget Entry (main.py)

### 5.1 调用链

```
用户执行: iphoto-gui
    │
    ▼
main.py::main()
    │
    ├── QApplication(argv)                    # Widget 需要 QApplication
    ├── create_container()                    # 共享: DI 注册
    ├── create_shared_components(container)   # 共享: ViewModel, Facade
    │
    ├── MainWindow(context)                   # Widget 特有
    ├── MainCoordinator(window, ...)          # Widget 特有连线
    ├── window.set_coordinator(coordinator)
    ├── coordinator.start()
    ├── window.show()
    │
    └── app.exec()
```

### 5.2 Widget 特有依赖

```
main.py 额外依赖 (Widget 专有):
├── PySide6.QtWidgets.QApplication
├── gui.ui.main_window.MainWindow
├── gui.ui.widgets/*                 (所有 Widget 组件)
├── gui.ui.controllers/*             (所有 UI 控制器)
├── gui.ui.delegates/*               (所有 delegate)
└── gui.ui.menus/*                   (所有菜单)
```

---

## 6. QML 入口实现 / QML Entry (main_qml.py)

### 6.1 完整实现

```python
"""
Pure QML entry point for iPhotron.

This module launches the QML-based UI while sharing the same
backend infrastructure (DI, ViewModels, Services) as the Widget entry.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from iPhoto.gui.bootstrap import create_container, create_shared_components


def main(argv: list[str] | None = None) -> int:
    """QML application entry point."""
    argv = argv or sys.argv
    app = QGuiApplication(argv)

    # 设置 QML 样式 (Material / Universal / Fusion)
    QQuickStyle.setStyle("Material")

    # ── 共享初始化 (与 Widget 入口相同) ──
    container = create_container()
    components = create_shared_components(container)

    # ── QML Engine 初始化 ──
    engine = QQmlApplicationEngine()

    # 将 Python 对象注入 QML 上下文
    ctx = engine.rootContext()
    ctx.setContextProperty("appFacade", components.facade)
    ctx.setContextProperty("assetListVM", components.asset_list_vm)
    ctx.setContextProperty("albumVM", components.album_vm)
    ctx.setContextProperty("viewRouter", components.view_router)

    # 可选: 注册缩略图 ImageProvider
    # from iPhoto.gui.ui.qml.providers.thumbnail_provider import ThumbnailProvider
    # engine.addImageProvider("thumbnails", ThumbnailProvider(cache_manager))

    # ── 加载主 QML 文件 ──
    qml_dir = Path(__file__).parent / "ui" / "qml"
    main_qml = qml_dir / "Main.qml"

    if not main_qml.exists():
        print(f"Error: Main.qml not found at {main_qml}", file=sys.stderr)
        return -1

    engine.load(QUrl.fromLocalFile(str(main_qml)))

    if not engine.rootObjects():
        print("Error: Failed to load QML root", file=sys.stderr)
        return -1

    # 可选: 从命令行打开相册
    if len(argv) > 1:
        album_path = argv[1]
        # 通过 facade 或 coordinator 打开
        components.facade.open_album(Path(album_path))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

### 6.2 QML 调用链

```
用户执行: iphoto-qml
    │
    ▼
main_qml.py::main()
    │
    ├── QGuiApplication(argv)                # QML 只需 QGuiApplication
    ├── QQuickStyle.setStyle("Material")     # QML 样式引擎
    ├── create_container()                   # 共享: DI 注册
    ├── create_shared_components(container)  # 共享: ViewModel, Facade
    │
    ├── QQmlApplicationEngine()              # QML 特有
    ├── ctx.setContextProperty(...)          # 注入 Python 对象
    ├── engine.load("Main.qml")             # 加载 QML 根
    │
    │   ┌──── QML 内部 ────────────┐
    │   │ Main.qml                 │
    │   │ ├── ApplicationWindow    │
    │   │ ├── AlbumSidebar {}      │  ← 读取 albumTreeModel
    │   │ ├── StackView {}         │  ← 监听 viewRouter 信号
    │   │ │   ├── GalleryView      │  ← 使用 assetListVM
    │   │ │   ├── DetailView       │
    │   │ │   └── EditView         │
    │   │ └── ChromeStatusBar {}   │  ← 监听 appFacade 信号
    │   └──────────────────────────┘
    │
    └── app.exec()
```

### 6.3 QML 特有依赖

```
main_qml.py 额外依赖 (QML 专有):
├── PySide6.QtGui.QGuiApplication           (更轻量)
├── PySide6.QtQml.QQmlApplicationEngine
├── PySide6.QtQuickControls2.QQuickStyle
└── gui/ui/qml/*.qml                        (所有 QML 文件)
```

### 6.4 QGuiApplication vs QApplication

| 特性 | `QApplication` (Widget) | `QGuiApplication` (QML) |
|------|------------------------|------------------------|
| 用途 | Widget 应用 | QML / 非 Widget 应用 |
| 依赖 | `PySide6.QtWidgets` | `PySide6.QtGui` |
| 功能 | Widget 管理 + 样式引擎 | 窗口/事件基础设施 |
| 开销 | 较大 | 较小 |
| QML 兼容 | ✅ (也可以用) | ✅ (推荐) |

> **注意**: QML 入口使用 `QGuiApplication` 而非 `QApplication`，因为不需要 Widget 框架。如果某些功能需要 `QApplication`（如系统托盘），可以改回。

---

## 7. pyproject.toml 配置 / Configuration

### 7.1 入口注册

```toml
[project.scripts]
iphoto     = "iPhoto.cli:app"                    # CLI (不变)
iphoto-gui = "iPhoto.gui.main:main"              # Widget 入口 (不变)
iphoto-qml = "iPhoto.gui.main_qml:main"          # QML 入口 (新增)
```

### 7.2 使用方式

```bash
# 传统 Widget 界面启动
iphoto-gui

# 纯 QML 界面启动
iphoto-qml

# 传统 Widget 界面启动并打开指定相册
iphoto-gui /path/to/album

# 纯 QML 界面启动并打开指定相册
iphoto-qml /path/to/album

# Python 模块方式启动
python -m iPhoto.gui.main          # Widget
python -m iPhoto.gui.main_qml      # QML
```

### 7.3 可选依赖分组

如果将来需要让 Widget 和 QML 的依赖可选安装：

```toml
[project.optional-dependencies]
widget = [
    # Widget 特有依赖（目前 PySide6 已包含全部）
]
qml = [
    # QML 特有依赖
    # PySide6 已包含 QtQuick, QtQml 模块
]
```

> **当前**: PySide6 >= 6.10.1 已同时包含 Widget 和 QML 模块，无需额外依赖。

---

## 8. ViewModel QML 适配 / ViewModel QML Adaptation

### 8.1 需要的最小改动

ViewModel 已是 `QAbstractListModel` 子类，QML 可直接使用。只需确保：

#### 1. 实现 `roleNames()` 方法

```python
# src/iPhoto/gui/viewmodels/asset_list_viewmodel.py
from PySide6.QtCore import Qt

class AssetListViewModel(QAbstractListModel):

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
            Roles.LIVE_MOTION_REL: b"liveMotionRel",
            Roles.LIVE_MOTION_ABS: b"liveMotionAbs",
            Roles.SIZE:            b"size",
            Roles.DT:              b"dt",
            Roles.LOCATION:        b"location",
            Roles.INFO:            b"info",
            Roles.ASSET_ID:        b"assetId",
        })
        return names
```

#### 2. 添加 `@Property` 用于状态暴露

```python
from PySide6.QtCore import Property, Signal

class AssetListViewModel(QAbstractListModel):
    countChanged = Signal()

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self.rowCount()

    @Property(bool, notify=countChanged)
    def isEmpty(self) -> bool:
        return self.rowCount() == 0
```

### 8.2 兼容性说明

这些改动对 Widget 入口**完全无影响**：
- `roleNames()` 在 Widget 模式下不被调用（`QStyledItemDelegate` 使用 `data(index, role)`）
- `@Property` 装饰器只添加 Qt 元对象信息，不改变任何方法行为
- `Signal` 在 Widget 模式下仍可正常 connect

---

## 9. Coordinator QML 适配 / Coordinator QML Adaptation

### 9.1 添加 `@Slot` 装饰器

Python 方法默认不可从 QML 调用。需要用 `@Slot` 标记：

```python
# src/iPhoto/gui/coordinators/navigation_coordinator.py
from PySide6.QtCore import Slot

class NavigationCoordinator(QObject):

    @Slot(str)
    def openAlbum(self, path: str) -> None:
        """QML-callable wrapper for open_album()."""
        self.open_album(Path(path))

    @Slot()
    def openAllPhotos(self) -> None:
        """QML-callable wrapper for open_all_photos()."""
        self.open_all_photos()

    @Slot()
    def openRecentlyDeleted(self) -> None:
        """QML-callable wrapper for open_recently_deleted()."""
        self.open_recently_deleted()
```

### 9.2 适配策略

| 协调器 | 需添加 `@Slot` | 需添加 `@Property` | 复杂度 |
|--------|---------------|-------------------|-------|
| `ViewRouter` | 已有信号，无需改动 | `isGallery`, `isDetail`, `isEdit` | 低 |
| `NavigationCoordinator` | `openAlbum`, `openAllPhotos` | `staticSelection` | 低 |
| `PlaybackCoordinator` | `playAsset`, `selectNext`, `selectPrevious` | `currentRow`, `isPlaying` | 中 |
| `EditCoordinator` | `enterEditMode`, `leaveEditMode`, `undo`, `redo` | `isEditing`, `canUndo`, `canRedo` | 中 |
| `MainCoordinator` | 通常不直接从 QML 调用 | - | 低 |

### 9.3 QML 中调用示例

```qml
// components/AlbumSidebar.qml
TreeView {
    delegate: TreeViewDelegate {
        onClicked: {
            navigationCoord.openAlbum(model.path)  // 调用 Python @Slot
        }
    }
}

// views/DetailView.qml
Item {
    // 读取 Python @Property
    Text { text: headerController.locationText }

    // 调用 Python @Slot
    Button {
        text: "Next"
        onClicked: playbackCoord.selectNext()
    }
}
```

### 9.4 Widget 兼容性

`@Slot` 装饰器对 Widget 入口无影响：
- 在 Widget 模式下，Python 方法仍可通过 `signal.connect(method)` 正常调用
- `@Slot` 仅注册元对象信息，不改变方法签名

---

## 10. 测试策略 / Testing Strategy

### 10.1 测试层级

```
┌─────────────────────────────────────────┐
│  E2E Tests (独立)                        │
│  ├── test_widget_launch.py              │
│  └── test_qml_launch.py                │
├─────────────────────────────────────────┤
│  Integration Tests (双入口共享)           │
│  ├── test_bootstrap.py                  │
│  ├── test_viewmodel_qml_roles.py        │
│  └── test_coordinator_slots.py          │
├─────────────────────────────────────────┤
│  Unit Tests (不变)                       │
│  ├── test_album_service.py              │
│  ├── test_asset_repository.py           │
│  └── ...                                │
└─────────────────────────────────────────┘
```

### 10.2 新增测试

#### `test_bootstrap.py`

```python
"""Test shared bootstrap creates all required components."""
import pytest
from iPhoto.gui.bootstrap import create_container, create_shared_components

def test_create_container():
    container = create_container()
    assert container.resolve(EventBus) is not None
    assert container.resolve(IAlbumRepository) is not None

def test_create_shared_components():
    container = create_container()
    components = create_shared_components(container)
    assert components.facade is not None
    assert components.asset_list_vm is not None
    assert components.view_router is not None
```

#### `test_viewmodel_qml_roles.py`

```python
"""Test ViewModel provides QML-compatible role names."""
def test_role_names_include_custom_roles(asset_list_vm):
    names = asset_list_vm.roleNames()
    assert b"abs" in names.values()
    assert b"isLive" in names.values()
    assert b"featured" in names.values()
```

#### `test_qml_launch.py`

```python
"""Test QML entry point can initialize without errors."""
from unittest.mock import patch
from iPhoto.gui.main_qml import main

@patch("iPhoto.gui.main_qml.QGuiApplication")
@patch("iPhoto.gui.main_qml.QQmlApplicationEngine")
def test_qml_main_initializes(mock_engine, mock_app):
    # Verify main_qml.py can be imported and called
    mock_engine.return_value.rootObjects.return_value = [object()]
    mock_app.return_value.exec.return_value = 0
    result = main(["test"])
    assert result == 0
```

### 10.3 现有测试不变

所有位于 `tests/` 目录下的现有测试保持不变：
- Domain / Application / Infrastructure 测试不受影响
- Widget UI 测试仍然通过（`@Property` / `@Slot` 不影响行为）
- 新增测试只在新文件中

---

## 📎 附录: 迁移检查清单 / Migration Checklist

### Phase 1: 基础设施

- [ ] 创建 `src/iPhoto/gui/bootstrap.py`
- [ ] 重构 `src/iPhoto/gui/main.py` 使用 bootstrap
- [ ] 创建 `src/iPhoto/gui/main_qml.py`
- [ ] 更新 `pyproject.toml` 添加 `iphoto-qml` 入口
- [ ] 创建 `src/iPhoto/gui/ui/qml/Main.qml` (空窗口)
- [ ] 创建 `src/iPhoto/gui/ui/qml/Theme.qml`
- [ ] 验证 `iphoto-gui` 仍然正常启动
- [ ] 验证 `iphoto-qml` 可以启动空窗口
- [ ] 编写 `test_bootstrap.py`

### Phase 2: ViewModel 适配

- [ ] 为 `AssetListViewModel` 添加 `roleNames()`
- [ ] 为 `AssetListViewModel` 添加 QML `@Property`
- [ ] 为 `NavigationCoordinator` 添加 `@Slot`
- [ ] 为 `PlaybackCoordinator` 添加 `@Slot` / `@Property`
- [ ] 为 `ViewRouter` 添加 `@Property`
- [ ] 验证 Widget 入口测试全部通过
- [ ] 编写 `test_viewmodel_qml_roles.py`

### Phase 3: QML 视图开发

- [ ] 实现 `GalleryView.qml` + 子组件
- [ ] 实现 `DetailView.qml` + 子组件
- [ ] 实现 `EditView.qml` + 子组件
- [ ] 实现 `MapView.qml`
- [ ] 实现 `DashboardView.qml`
- [ ] 全部对话框 QML 化
- [ ] 功能对等性验证

### Phase 4: 整合

- [ ] 性能优化
- [ ] 主题系统完善
- [ ] 双入口回归测试
- [ ] 文档更新

---

> **维护者 / Maintainer:** iPhotron Team  
> **最后更新 / Last Updated:** 2026-02-08

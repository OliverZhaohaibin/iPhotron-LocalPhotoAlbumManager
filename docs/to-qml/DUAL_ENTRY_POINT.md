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
│    main.py ───── 原文件 (零修改)                   │
│         │                                       │
│         │    ┌─────────────────────┐             │
│         ├───▶│   bootstrap.py      │             │
│         │    │  (共享 Infra/App)    │             │
│    main_qml.py─┘ └─────────┬───────────┘         │
│         │                  │                     │
│         ▼                  ▼                     │
│    bootstrap_qml.py     共享业务层                 │
│    (使用 _qml.py 副本)   Domain / App / Infra     │
│                                                 │
│    iphoto-qml (纯 QML)                           │
└─────────────────────────────────────────────────┘
```

### 设计原则

1. **完全隔离**: Widget 入口使用原 Python 文件，QML 入口使用 `_qml.py` 副本，互不修改
2. **接口隔离**: Widget 入口只依赖 Widget 组件，QML 入口只依赖 QML 文件 + `_qml.py` 副本
3. **共享后端**: 两个入口共享 Domain / Application / Infrastructure 层（这些层不需要 QML 适配）
4. **独立运行**: 两个入口可以独立安装、独立运行、互不影响
5. **原文件零修改**: 所有需要 QML 适配的 Python 文件均复制为 `_qml.py` 副本

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
└── MainCoordinator                  # Widget 特有 (不修改)
```

> **注意**: 在新方案中，`main.py` 保持**零修改**。QML 入口 `main_qml.py` 有自己独立的初始化路径，使用 `_qml.py` 副本。

---

## 3. 双入口架构设计 / Dual Entry Architecture

### 3.1 文件结构（`_qml.py` 副本隔离）

```
src/iPhoto/gui/
├── main.py                    # Widget 入口 (零修改)
├── main_qml.py                # QML 入口 (新增)
├── bootstrap.py               # 共享 Infra/App 初始化 (新增)
├── bootstrap_qml.py           # QML 专用初始化，使用 _qml 副本 (新增)
│
├── facade.py                  # Widget 用 (零修改)
├── facade_qml.py              # QML 副本 (新增，添加 @Property)
│
├── coordinators/
│   ├── main_coordinator.py          # Widget 用 (零修改)
│   ├── main_coordinator_qml.py      # QML 副本 (新增)
│   ├── navigation_coordinator.py    # Widget 用 (零修改)
│   ├── navigation_coordinator_qml.py # QML 副本 (新增，添加 @Slot)
│   ├── playback_coordinator.py      # Widget 用 (零修改)
│   ├── playback_coordinator_qml.py  # QML 副本 (新增)
│   ├── edit_coordinator.py          # Widget 用 (零修改)
│   ├── edit_coordinator_qml.py      # QML 副本 (新增)
│   ├── view_router.py               # Widget 用 (零修改)
│   └── view_router_qml.py           # QML 副本 (新增，添加 @Property)
│
├── viewmodels/
│   ├── asset_list_viewmodel.py      # Widget 用 (零修改)
│   ├── asset_list_viewmodel_qml.py  # QML 副本 (新增，添加 roleNames/@Property)
│   ├── asset_data_source.py         # Widget 用 (零修改)
│   ├── asset_data_source_qml.py     # QML 副本 (新增)
│   ├── album_viewmodel.py           # Widget 用 (零修改)
│   └── album_viewmodel_qml.py       # QML 副本 (新增)
│
├── services/              # 共享 (零修改)
│
└── ui/
    ├── widgets/           # Widget 专用 (零修改)
    ├── controllers/
    │   ├── *.py                     # Widget 用 (零修改)
    │   └── *_qml.py                 # QML 副本 (新增)
    ├── delegates/         # Widget 专用 (零修改)
    ├── models/
    │   ├── edit_session.py          # Widget 用 (零修改)
    │   ├── edit_session_qml.py      # QML 副本 (新增)
    │   ├── roles.py                 # Widget 用 (零修改)
    │   ├── roles_qml.py             # QML 副本 (新增)
    │   ├── album_tree_model.py      # Widget 用 (零修改)
    │   ├── album_tree_model_qml.py  # QML 副本 (新增)
    │   └── ...其余 (共享不变)
    ├── tasks/             # 共享 (零修改)
    ├── menus/             # Widget 专用 (零修改)
    ├── icon/              # 共享 (零修改)
    └── qml/               # QML 专用 (全部新增)
```

### 3.2 共享 vs 专用边界（`_qml.py` 隔离）

```
                    ┌─────────────────────────────────────┐
                    │     真正共享层 (零修改)               │
                    │                                     │
                    │  bootstrap.py (Infra/App 初始化)     │
                    │  services/*                         │
                    │  ui/tasks/*                         │
                    │  ui/icon/*                          │
                    │  ui/models/ (无需适配的部分)          │
                    │                                     │
        ┌───────────┤                                     ├───────────┐
        │           └─────────────────────────────────────┘           │
        │                                                             │
        ▼                                                             ▼
┌───────────────────┐                                 ┌───────────────────┐
│  Widget 专用       │                                 │  QML 专用          │
│  (原文件，零修改)    │                                 │  (_qml.py 副本)    │
│                   │                                 │                   │
│  main.py          │                                 │  main_qml.py      │
│  facade.py        │                                 │  bootstrap_qml.py │
│  coordinators/*.py│                                 │  facade_qml.py    │
│  viewmodels/*.py  │                                 │  coordinators/*_qml.py │
│  ui/widgets/*     │                                 │  viewmodels/*_qml.py │
│  ui/controllers/*.py │                              │  ui/controllers/*_qml.py │
│  ui/delegates/*   │                                 │  ui/models/*_qml.py │
│  ui/menus/*       │                                 │  ui/qml/*.qml     │
│  main_window.py   │                                 │                   │
└───────────────────┘                                 └───────────────────┘
```

---

## 4. 共享层提取 / Shared Layer Extraction

### 4.1 bootstrap.py — 仅 Infra/App 层

`bootstrap.py` 仅负责 Infrastructure 和 Application 层的 DI 注册——这些层不涉及 GUI，
对 Widget 和 QML 入口完全相同：

```python
"""
Shared bootstrap logic — Infrastructure & Application layer only.

This module creates the DI container with Infra/App registrations that
the QML entry point uses. GUI-layer objects (ViewModels, Coordinators,
Facade) are NOT created here — they are created in bootstrap_qml.py
using _qml.py copies. The Widget entry (main.py) does NOT use this
module; it retains its own inline initialization.
"""
from __future__ import annotations

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


def create_container() -> DependencyContainer:
    """
    Create DI container with Infrastructure + Application registrations.
    Does NOT create any GUI-layer objects.
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


def _resolve_db_path() -> Path:
    """Resolve the global database path."""
    home = Path.home()
    return home / ".iphoto" / "global_index.db"
```

### 4.2 bootstrap_qml.py — QML 专用 GUI 层初始化

```python
"""
QML-specific bootstrap — creates _qml.py variant GUI objects.

This module uses the _qml.py copies (not the originals) to create
ViewModels, Coordinators, and Facade for the QML entry point.
"""
from __future__ import annotations

from typing import NamedTuple

from iPhoto.di.container import DependencyContainer
from iPhoto.events.bus import EventBus
from iPhoto.application.interfaces import IAssetRepository

# ── QML 副本导入 (不导入原文件!) ──
from iPhoto.gui.facade_qml import AppFacadeQml
from iPhoto.gui.viewmodels.asset_list_viewmodel_qml import AssetListViewModelQml
from iPhoto.gui.viewmodels.asset_data_source_qml import AssetDataSourceQml
from iPhoto.gui.viewmodels.album_viewmodel_qml import AlbumViewModelQml
from iPhoto.gui.coordinators.view_router_qml import ViewRouterQml
from iPhoto.gui.coordinators.navigation_coordinator_qml import NavigationCoordinatorQml
from iPhoto.gui.coordinators.playback_coordinator_qml import PlaybackCoordinatorQml
from iPhoto.gui.coordinators.edit_coordinator_qml import EditCoordinatorQml


class QmlComponents(NamedTuple):
    """All QML-specific components created during bootstrap."""
    facade: AppFacadeQml
    asset_list_vm: AssetListViewModelQml
    album_vm: AlbumViewModelQml
    view_router: ViewRouterQml
    navigation_coord: NavigationCoordinatorQml
    playback_coord: PlaybackCoordinatorQml
    edit_coord: EditCoordinatorQml


def create_qml_components(container: DependencyContainer) -> QmlComponents:
    """
    Create QML-adapted GUI-layer objects using _qml.py copies.

    Args:
        container: Configured DI container (from bootstrap.create_container).

    Returns:
        QmlComponents namedtuple with all QML-specific objects.
    """
    bus = container.resolve(EventBus)

    # Facade (QML copy)
    facade = AppFacadeQml()

    # ViewModels (QML copies)
    data_source = AssetDataSourceQml()
    data_source.set_repository(container.resolve(IAssetRepository))
    asset_list_vm = AssetListViewModelQml(data_source, None)

    album_vm = AlbumViewModelQml()

    # Coordinators (QML copies)
    view_router = ViewRouterQml()
    navigation_coord = NavigationCoordinatorQml(...)
    playback_coord = PlaybackCoordinatorQml(...)
    edit_coord = EditCoordinatorQml(...)

    return QmlComponents(
        facade=facade,
        asset_list_vm=asset_list_vm,
        album_vm=album_vm,
        view_router=view_router,
        navigation_coord=navigation_coord,
        playback_coord=playback_coord,
        edit_coord=edit_coord,
    )
```

### 4.3 main.py 保持零修改

**Widget 入口 `main.py` 完全不改动**。它继续使用原有的初始化逻辑，
导入原 `facade.py`、`coordinators/*.py`、`viewmodels/*.py`。

> `bootstrap.py` 仅供 `main_qml.py` 使用。`main.py` 保持原样，继续使用其内联的 DI 初始化逻辑。
> `main_qml.py` 使用 `bootstrap.py`（Infra/App 层） + `bootstrap_qml.py`（GUI 层 `_qml` 副本）。

---

## 5. Widget 入口实现 / Widget Entry (main.py)

### 5.1 调用链（零修改）

```
用户执行: iphoto-gui
    │
    ▼
main.py::main()  (原文件，零修改)
    │
    ├── QApplication(argv)
    ├── DependencyContainer()          # 原有内联初始化
    ├── _register_infrastructure()
    ├── _register_application()
    │
    ├── MainWindow(context)            # 原有 Widget 组件
    ├── MainCoordinator(window, ...)   # 原有 Coordinator
    ├── window.set_coordinator(coordinator)
    ├── coordinator.start()
    ├── window.show()
    │
    └── app.exec()
```

> **main.py 不做任何改动**——继续使用原有的所有 Python 文件。

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

### 6.1 完整实现（使用 `_qml.py` 副本）

```python
"""
Pure QML entry point for iPhotron.

This module launches the QML-based UI using _qml.py copies of
ViewModels, Coordinators, and Facade. Original files are never imported.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from iPhoto.gui.bootstrap import create_container
from iPhoto.gui.bootstrap_qml import create_qml_components


def main(argv: list[str] | None = None) -> int:
    """QML application entry point."""
    argv = argv or sys.argv
    app = QGuiApplication(argv)

    # 设置 QML 样式 (Material / Universal / Fusion)
    QQuickStyle.setStyle("Material")

    # ── 共享 Infra/App 初始化 ──
    container = create_container()

    # ── QML 专用 GUI 层初始化 (使用 _qml.py 副本) ──
    components = create_qml_components(container)

    # ── QML Engine 初始化 ──
    engine = QQmlApplicationEngine()

    # 将 _qml.py 副本对象注入 QML 上下文
    ctx = engine.rootContext()
    ctx.setContextProperty("appFacade", components.facade)
    ctx.setContextProperty("assetListVM", components.asset_list_vm)
    ctx.setContextProperty("albumVM", components.album_vm)
    ctx.setContextProperty("viewRouter", components.view_router)
    ctx.setContextProperty("navigationCoord", components.navigation_coord)
    ctx.setContextProperty("playbackCoord", components.playback_coord)
    ctx.setContextProperty("editCoord", components.edit_coord)

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
        components.facade.open_album(Path(album_path))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

### 6.2 QML 调用链（完全使用 `_qml.py` 副本）

```
用户执行: iphoto-qml
    │
    ▼
main_qml.py::main()
    │
    ├── QGuiApplication(argv)                # QML 只需 QGuiApplication
    ├── QQuickStyle.setStyle("Material")     # QML 样式引擎
    ├── create_container()                   # 共享: Infra/App DI 注册
    ├── create_qml_components(container)     # QML 专用: 使用 _qml.py 副本
    │   ├── AppFacadeQml()                   # facade_qml.py
    │   ├── AssetListViewModelQml(...)       # asset_list_viewmodel_qml.py
    │   ├── AlbumViewModelQml()              # album_viewmodel_qml.py
    │   ├── ViewRouterQml()                  # view_router_qml.py
    │   ├── NavigationCoordinatorQml(...)    # navigation_coordinator_qml.py
    │   ├── PlaybackCoordinatorQml(...)      # playback_coordinator_qml.py
    │   └── EditCoordinatorQml(...)          # edit_coordinator_qml.py
    │
    ├── QQmlApplicationEngine()              # QML 特有
    ├── ctx.setContextProperty(...)          # 注入 _qml 副本对象
    ├── engine.load("Main.qml")             # 加载 QML 根
    │
    │   ┌──── QML 内部 ────────────┐
    │   │ Main.qml                 │
    │   │ ├── ApplicationWindow    │
    │   │ ├── AlbumSidebar {}      │  ← 读取 albumTreeModelQml
    │   │ ├── StackView {}         │  ← 监听 viewRouter (QML副本) 信号
    │   │ │   ├── GalleryView      │  ← 使用 assetListVM (QML副本)
    │   │ │   ├── DetailView       │
    │   │ │   └── EditView         │
    │   │ └── ChromeStatusBar {}   │  ← 监听 appFacade (QML副本) 信号
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
├── gui/bootstrap.py                         (共享 Infra/App)
├── gui/bootstrap_qml.py                    (QML 专用 GUI 初始化)
├── gui/facade_qml.py                       (QML 副本)
├── gui/coordinators/*_qml.py               (QML 副本)
├── gui/viewmodels/*_qml.py                 (QML 副本)
├── gui/ui/models/*_qml.py                  (QML 副本)
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

## 8. ViewModel QML 副本 / ViewModel QML Copies

### 8.1 `_qml.py` 副本策略

**不修改原文件**。将每个需要 QML 适配的 ViewModel 复制为 `_qml.py` 副本：

| 原文件 | QML 副本 | 添加内容 |
|--------|---------|---------|
| `asset_list_viewmodel.py` | `asset_list_viewmodel_qml.py` | `roleNames()`, `@Property(count, isEmpty)` |
| `asset_data_source.py` | `asset_data_source_qml.py` | `@Property` 暴露加载状态 |
| `album_viewmodel.py` | `album_viewmodel_qml.py` | `@Property` / `@Slot` |

#### `asset_list_viewmodel_qml.py` 副本示例

```python
# src/iPhoto/gui/viewmodels/asset_list_viewmodel_qml.py
# ── 复制自 asset_list_viewmodel.py，添加 QML 适配 ──
from PySide6.QtCore import Qt, Property, Signal
from iPhoto.gui.ui.models.roles_qml import ROLE_NAMES

class AssetListViewModelQml(QAbstractListModel):
    """QML-adapted copy — adds roleNames() and @Property."""

    countChanged = Signal()

    def roleNames(self) -> dict[int, bytes]:
        """Map role enums to QML-accessible property names."""
        names = super().roleNames()
        names.update(ROLE_NAMES)
        return names

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self.rowCount()

    @Property(bool, notify=countChanged)
    def isEmpty(self) -> bool:
        return self.rowCount() == 0
```

### 8.2 隔离说明

原文件**完全不受影响**：
- `asset_list_viewmodel.py` 保持原样，Widget 入口继续使用它
- `asset_list_viewmodel_qml.py` 是独立副本，仅 QML 入口导入
- 未来原文件的任何改动不会自动同步到 `_qml.py`（需手动同步或通过继承）

> **可选优化**：如果未来两个副本的差异仅是添加 `roleNames()` / `@Property` / `@Slot`，
> 可以让 `_qml.py` 副本继承原类并只覆盖需要的方法，减少代码重复。
> 但初始阶段建议完整复制，确保完全隔离。

---

## 9. Coordinator QML 副本 / Coordinator QML Copies

### 9.1 `_qml.py` 副本策略

**不修改原文件**。将每个需要 QML 适配的 Coordinator 复制为 `_qml.py` 副本，
在副本中添加 `@Slot` 装饰器：

```python
# src/iPhoto/gui/coordinators/navigation_coordinator_qml.py
# ── 复制自 navigation_coordinator.py，添加 @Slot ──
from PySide6.QtCore import Slot

class NavigationCoordinatorQml(QObject):
    """QML-adapted copy — adds @Slot for QML interop."""

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

### 9.2 Coordinator `_qml.py` 副本清单

| 原文件 | QML 副本 | 需添加 `@Slot` | 需添加 `@Property` | 复杂度 |
|--------|---------|---------------|-------------------|-------|
| `view_router.py` | `view_router_qml.py` | 已有信号，无需 | `isGallery`, `isDetail`, `isEdit` | 低 |
| `navigation_coordinator.py` | `navigation_coordinator_qml.py` | `openAlbum`, `openAllPhotos` | `staticSelection` | 低 |
| `playback_coordinator.py` | `playback_coordinator_qml.py` | `playAsset`, `selectNext`, `selectPrevious` | `currentRow`, `isPlaying` | 中 |
| `edit_coordinator.py` | `edit_coordinator_qml.py` | `enterEditMode`, `leaveEditMode`, `undo`, `redo` | `isEditing`, `canUndo`, `canRedo` | 中 |
| `main_coordinator.py` | `main_coordinator_qml.py` | 通常不直接从 QML 调用 | - | 低 |

### 9.3 QML 中调用示例

```qml
// components/AlbumSidebar.qml
TreeView {
    delegate: TreeViewDelegate {
        onClicked: {
            navigationCoord.openAlbum(model.path)  // 调用 _qml 副本的 @Slot
        }
    }
}

// views/DetailView.qml
Item {
    // 读取 _qml 副本的 @Property
    Text { text: headerController.locationText }

    // 调用 _qml 副本的 @Slot
    Button {
        text: "Next"
        onClicked: playbackCoord.selectNext()
    }
}
```

### 9.4 隔离说明

原文件**完全不受影响**：
- `navigation_coordinator.py` 保持原样，Widget 入口继续使用
- `navigation_coordinator_qml.py` 是独立副本，仅 QML 入口导入
- `@Slot` 在原文件中不存在，Widget 入口不会有任何元对象变更

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
"""Test shared bootstrap creates DI container correctly."""
import pytest
from iPhoto.gui.bootstrap import create_container

def test_create_container():
    container = create_container()
    assert container.resolve(EventBus) is not None
    assert container.resolve(IAlbumRepository) is not None
```

#### `test_bootstrap_qml.py`

```python
"""Test QML bootstrap creates _qml.py variant components."""
from iPhoto.gui.bootstrap import create_container
from iPhoto.gui.bootstrap_qml import create_qml_components

def test_create_qml_components():
    container = create_container()
    components = create_qml_components(container)
    assert components.facade is not None
    assert components.asset_list_vm is not None
    assert components.view_router is not None
    # Verify these are QML copies, not originals
    assert type(components.facade).__name__ == "AppFacadeQml"
    assert type(components.asset_list_vm).__name__ == "AssetListViewModelQml"
```

#### `test_viewmodel_qml_roles.py`

```python
"""Test QML ViewModel copy provides roleNames."""
def test_role_names_include_custom_roles():
    from iPhoto.gui.viewmodels.asset_list_viewmodel_qml import AssetListViewModelQml
    vm = AssetListViewModelQml(...)
    names = vm.roleNames()
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
    mock_engine.return_value.rootObjects.return_value = [object()]
    mock_app.return_value.exec.return_value = 0
    result = main(["test"])
    assert result == 0
```

### 10.3 现有测试不变

所有位于 `tests/` 目录下的现有测试保持不变：
- Domain / Application / Infrastructure 测试不受影响
- Widget UI 测试不受影响（原文件零修改）
- 新增测试只在新文件中，测试 `_qml.py` 副本行为

---

## 📎 附录: 迁移检查清单 / Migration Checklist

### Phase 1: 基础设施

- [ ] 创建 `src/iPhoto/gui/bootstrap.py` (仅 Infra/App 层)
- [ ] 创建 `src/iPhoto/gui/bootstrap_qml.py` (QML 专用 GUI 层)
- [ ] 复制 `facade.py` → `facade_qml.py`
- [ ] 复制 `coordinators/view_router.py` → `view_router_qml.py`
- [ ] 复制 `coordinators/main_coordinator.py` → `main_coordinator_qml.py`
- [ ] 复制 `ui/models/roles.py` → `roles_qml.py`
- [ ] 创建 `src/iPhoto/gui/main_qml.py`
- [ ] 更新 `pyproject.toml` 添加 `iphoto-qml` 入口
- [ ] 创建 `src/iPhoto/gui/ui/qml/Main.qml` (空窗口)
- [ ] 创建 `src/iPhoto/gui/ui/qml/Theme.qml`
- [ ] 验证 `iphoto-gui` 仍然正常启动（原文件零修改）
- [ ] 验证 `iphoto-qml` 可以启动空窗口
- [ ] 编写 `test_bootstrap.py` + `test_bootstrap_qml.py`

### Phase 2: ViewModel/Coordinator 副本

- [ ] 复制 `viewmodels/asset_list_viewmodel.py` → `asset_list_viewmodel_qml.py`，添加 `roleNames()`, `@Property`
- [ ] 复制 `viewmodels/asset_data_source.py` → `asset_data_source_qml.py`
- [ ] 复制 `viewmodels/album_viewmodel.py` → `album_viewmodel_qml.py`
- [ ] 复制 `coordinators/navigation_coordinator.py` → `navigation_coordinator_qml.py`，添加 `@Slot`
- [ ] 复制 `coordinators/playback_coordinator.py` → `playback_coordinator_qml.py`，添加 `@Slot/@Property`
- [ ] 复制 `ui/models/album_tree_model.py` → `album_tree_model_qml.py`，添加 `roleNames()`
- [ ] 复制需要 QML 适配的 `ui/controllers/*.py` → `*_qml.py`
- [ ] 验证 Widget 入口测试全部通过（零修改确认）
- [ ] 编写 `test_viewmodel_qml_roles.py`

### Phase 3: QML 视图开发 + 编辑器副本

- [ ] 复制 `coordinators/edit_coordinator.py` → `edit_coordinator_qml.py`，添加 `@Slot`
- [ ] 复制 `ui/models/edit_session.py` → `edit_session_qml.py`，添加 `@Property`
- [ ] 复制编辑相关 controllers → `*_qml.py` 副本

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

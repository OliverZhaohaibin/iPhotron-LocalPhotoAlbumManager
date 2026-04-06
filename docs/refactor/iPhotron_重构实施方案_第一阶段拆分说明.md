# iPhotron 重构实施文档（可直接交付开发团队）

版本：v1.0  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`  
文档目的：指导开发团队对现有大体量项目进行低耦合拆分，降低维护风险，建立清晰的单向依赖与可持续演进能力。

---

## 1. 背景与问题

当前项目已经有明显的“架构意图”，例如：

- `domain / application / infrastructure` 分层
- `MVVM + Facade + Coordinator`
- Repository / UseCase / Service 等抽象

但实际运行路径仍然存在明显的**双轨并存**与**职责混杂**现象。

### 1.1 发现的问题

#### 问题 A：旧入口与新入口并存
仓库中同时存在：
- 新路径：`application/use_cases/*`, `application/services/*`
- 旧路径：`src/iPhoto/app.py`, `src/iPhoto/gui/facade.py`

结果是同一类业务可能存在两套实现，后续维护容易出现：
- 修一个需求时不知道该改哪条链路
- 一个 bug 修了 A 路径，B 路径仍然保留问题
- 行为逐步漂移

#### 问题 B：`app.py` 已经成为混合职责入口
当前 `src/iPhoto/app.py` 不只是 façade，它同时承担了：
- 打开相册
- 自动扫描
- 增量索引判断
- 路径转换（album root / library root）
- links 同步
- favorites 同步
- trash restore 元数据保留
- Live Photo 配对

#### 问题 C：`AppContext` 负担过重
当前 `src/iPhoto/appctx.py` 同时负责：
- GUI 全局上下文
- DI 容器初始化
- 配置读取
- 数据库连接池装配
- Repository 装配
- UseCase 装配
- Service 装配
- Theme / Library / Facade 会话级状态
- 启动任务协调

#### 问题 D：GUI Facade 知道太多
当前 `src/iPhoto/gui/facade.py` 已经直接掌握：
- current album
- library manager
- background task manager
- library update service
- import / move / delete / restore service
- reload / refresh / signal relay
- backend 调用

#### 问题 E：扫描链路跨层穿透
扫描能力分散在多个位置：
- `src/iPhoto/app.py`
- `src/iPhoto/gui/services/library_update_service.py`
- `src/iPhoto/library/manager.py`
- `src/iPhoto/library/workers/*`
- `src/iPhoto/io/*`
- `src/iPhoto/cache/*`

导致扫描逻辑同时耦合了：
- 文件系统遍历
- metadata 提取
- SQLite 写入
- links 计算
- UI 进度 signal
- library watcher
- reload 流程
- trash 特殊逻辑

---

## 2. 重构目标

本次重构不是推倒重写，而是“收口”和“降耦合”。

### 2.1 总目标
1. 消除双轨实现，建立唯一主业务入口
2. 收紧依赖方向，形成单向依赖
3. 从 GUI 中剥离业务编排
4. 从 Context 中剥离依赖装配
5. 将扫描、导入、删除、恢复等复杂链路收敛为明确用例
6. 减少跨层直接调用和重复状态同步
7. 提升测试能力、替换能力与增量迭代速度

### 2.2 目标依赖方向

```text
presentation -> application -> domain
infrastructure -> application/domain（实现接口）
domain 不依赖外层
application 不依赖 Qt / PySide6
presentation 不直接依赖 sqlite/exiftool/fs 具体实现
```

---

## 3. 目标架构

```text
src/iPhoto/
├── domain/
│   ├── models/
│   ├── repositories.py
│   ├── services/
│   └── value_objects/
├── application/
│   ├── use_cases/
│   │   ├── album/
│   │   ├── asset/
│   │   ├── library/
│   │   └── scan/
│   ├── services/
│   ├── dto/
│   ├── commands/
│   ├── queries/
│   └── interfaces.py
├── infrastructure/
│   ├── repositories/
│   ├── persistence/sqlite/
│   ├── metadata/
│   ├── thumbnail/
│   ├── scan/
│   ├── external_tools/
│   └── watcher/
├── presentation/
│   └── qt/
│       ├── facade/
│       ├── coordinators/
│       ├── viewmodels/
│       ├── ui/
│       ├── services/
│       └── session/
├── bootstrap/
│   ├── container.py
│   ├── startup.py
│   └── config_loader.py
└── shared/
    ├── logging/
    ├── errors/
    ├── events/
    └── utils/
```

---

## 4. 本次重构策略

### 4.1 原则
- 不大面积重写
- 优先迁移主路径
- 优先消除重复入口
- 旧文件先降权，再删除
- 复杂能力优先按“用例边界”收口，不优先按“技术层”微切

### 4.2 拆分优先级
优先处理以下 4 个高风险对象：
1. `src/iPhoto/app.py`
2. `src/iPhoto/appctx.py`
3. `src/iPhoto/gui/facade.py`
4. `src/iPhoto/library/manager.py`

---

## 5. 第一阶段实施目标（第一步拆分）

第一步不是一次性完成全量重构。第一步的目标只有一个：

> **建立新的主业务入口，并冻结旧入口继续膨胀。**

### 第一阶段必须达成的结果
1. `gui/facade.py` 不再直接承载新增业务
2. `app.py` 不再新增业务逻辑
3. `appctx.py` 不再继续增长装配逻辑
4. 新增的业务入口全部进入 `application/use_cases/*`
5. `gui/facade.py` 改为调新的 use case / application service
6. 为后续扫描子域拆分预留明确边界

---

## 6. 第一阶段：详细文件级拆分方案

### 6.1 现状中必须先处理的文件

#### 需要直接改造的现有文件
```text
src/iPhoto/app.py
src/iPhoto/appctx.py
src/iPhoto/gui/facade.py
src/iPhoto/gui/services/library_update_service.py
src/iPhoto/library/manager.py
src/iPhoto/gui/main.py
```

#### 第一阶段需要新增的文件
```text
src/iPhoto/application/use_cases/album/open_album_legacy_bridge.py
src/iPhoto/application/use_cases/scan/rescan_album_use_case.py
src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py
src/iPhoto/application/use_cases/asset/import_assets_use_case.py
src/iPhoto/application/use_cases/asset/move_assets_use_case.py
src/iPhoto/application/use_cases/asset/delete_assets_use_case.py
src/iPhoto/application/use_cases/asset/restore_assets_use_case.py
src/iPhoto/application/use_cases/album/toggle_featured_use_case.py
src/iPhoto/application/use_cases/album/set_album_cover_use_case.py
src/iPhoto/presentation/qt/facade/album_facade.py
src/iPhoto/presentation/qt/facade/asset_facade.py
src/iPhoto/presentation/qt/facade/library_facade.py
src/iPhoto/bootstrap/container.py
src/iPhoto/presentation/qt/session/app_session.py
```

---

## 7. 第一阶段逐文件改造说明

### 7.1 `src/iPhoto/app.py`
#### 当前问题
该文件承担过多业务编排职责，是当前最危险的历史大文件。

#### 第一阶段处理目标
- 冻结
- 不继续增加逻辑
- 作为兼容桥接层存在
- 其内部逻辑逐步转发到新的 use case

#### 第一阶段具体动作
1. 文件头增加兼容层注释：

```python
"""
Compatibility backend facade.

This module is deprecated as a business entrypoint.
New business logic must be implemented in application/use_cases/*
and only bridged here temporarily for backward compatibility.
"""
```

2. 拆出以下函数的目标归属：

| 旧函数 | 第一阶段目标 |
|---|---|
| `open_album()` | `application/use_cases/album/open_album_legacy_bridge.py` |
| `rescan()` | `application/use_cases/scan/rescan_album_use_case.py` |
| `pair()` | `application/use_cases/scan/pair_live_photos_use_case_v2.py` |
| `scan_specific_files()` | `application/use_cases/asset/import_assets_use_case.py` 内部辅助流程 |

3. 修改方式：
- `app.py` 保留函数签名
- 函数内部只做参数标准化、调用新 use case、返回旧格式结果

---

### 7.2 `src/iPhoto/appctx.py`
#### 当前问题
这个文件同时包含：
- 容器装配
- 配置初始化
- 会话状态
- 启动任务流程

#### 第一阶段处理目标
把它从超级上下文拆成两个角色：
1. 容器装配
2. GUI 会话状态

#### 第一阶段具体动作
1. 新建 `src/iPhoto/bootstrap/container.py`，迁入 `_create_di_container()`，改名为 `build_container()`
2. 新建 `src/iPhoto/presentation/qt/session/app_session.py`，迁入：
   - `settings`
   - `library`
   - `facade`
   - `recent_albums`
   - `theme`
   - `_pending_basic_library_path`
   - `resume_startup_tasks()`
   - `remember_album()`
3. `appctx.py` 保留 `AppContext` 名称，但内部只组合 `build_container()` 与 `AppSession`
4. 后续 DI 调整只改 `bootstrap/container.py`，后续 GUI 状态只改 `presentation/qt/session/app_session.py`

---

### 7.3 `src/iPhoto/gui/facade.py`
#### 当前问题
此文件过重，既承担 UI 对接，也承担应用编排。

#### 第一阶段处理目标
把它拆成 3 个 façade，并让现有 `AppFacade` 变成组合器。

#### 第一阶段具体动作
新建：
- `src/iPhoto/presentation/qt/facade/album_facade.py`
- `src/iPhoto/presentation/qt/facade/asset_facade.py`
- `src/iPhoto/presentation/qt/facade/library_facade.py`

职责拆分：
- `album_facade.py`：`open_album`、`set_cover`、`toggle_featured`、`pair_live_current`
- `asset_facade.py`：`import_files`、`move_assets`、`delete_assets`、`restore_assets`
- `library_facade.py`：`rescan_current`、`rescan_current_async`、`cancel_active_scans`、scan signal relay、reload / announce refresh

现有 `gui/facade.py` 第一阶段修改方式：
- 保留 `AppFacade` 类名
- 只维护 signal
- 只聚合三个 façade
- 旧方法内部只转发，不再写真实业务逻辑

---

### 7.4 `src/iPhoto/gui/services/library_update_service.py`
#### 当前问题
这个文件已经包含扫描编排、move aftermath、refresh bookkeeping、links/index 写入等复合职责。

#### 第一阶段处理目标
不立即大拆，但先切出应用层用例入口。

#### 第一阶段具体动作
1. 保留它作为 presentation 服务，因为其中仍包含 Qt signal、worker、task manager
2. 将以下动作下沉到 `application/use_cases/scan/*`：
   - `backend.rescan(...)`
   - `backend.pair(...)`
   - `_update_index_snapshot`
   - `_ensure_links`
   - trash preserved metadata merge 逻辑
3. 新增：
```text
src/iPhoto/application/use_cases/scan/rescan_album_use_case.py
src/iPhoto/application/use_cases/scan/persist_scan_result_use_case.py
src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py
```
4. `library_update_service.py` 改为：
   - 调 application use case
   - 自己只负责 worker 生命周期、Qt signal relay、task_manager 协调

---

### 7.5 `src/iPhoto/library/manager.py`
#### 当前问题
这个对象仍是超级协调者。

#### 第一阶段处理目标
先止血，不继续扩展职责。

#### 第一阶段具体动作
1. 增加开发规则注释：

```python
# NOTE:
# LibraryManager is currently a legacy coordination object.
# Do not add new business rules here.
# New behaviour must be implemented in application/use_cases/* or dedicated services.
```

2. 明确当前职责边界只视为：
- Tree coordination
- Scan coordination
- Watch coordination

3. 第一阶段不动 mixin 拆分结构

---

### 7.6 `src/iPhoto/gui/main.py`
#### 当前问题
启动入口仍直接依赖 `AppContext` 这个历史大对象。

#### 第一阶段处理目标
改为依赖新的 `AppContext` 兼容壳 + `AppSession`

#### 第一阶段具体动作
1. 保持 `context.container` 可用
2. 新增 `context.session` 可访问
3. 后续 coordinator / facade / viewmodel 逐步从 `context.xxx` 切换为：
   - `context.session.xxx`
   - `context.container`

---

## 8. 第一阶段新增文件建议骨架

### 8.1 `src/iPhoto/bootstrap/container.py`
```python
from __future__ import annotations

from pathlib import Path
import logging

from iPhoto.di.container import DependencyContainer
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from iPhoto.infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.events.bus import EventBus
from iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from iPhoto.application.services.album_service import AlbumService
from iPhoto.application.services.asset_service import AssetService
from iPhoto.infrastructure.services.metadata_provider import ExifToolMetadataProvider
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator

def build_container() -> DependencyContainer:
    container = DependencyContainer()

    db_path = Path.home() / ".iPhoto" / "global_index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pool = ConnectionPool(db_path)
    container.register_instance(ConnectionPool, pool)

    logger = logging.getLogger("EventBus")
    container.register_factory(EventBus, lambda: EventBus(logger), singleton=True)

    container.register_singleton(IMetadataProvider, ExifToolMetadataProvider)
    container.register_singleton(IThumbnailGenerator, PillowThumbnailGenerator)

    container.register_factory(
        IAlbumRepository,
        lambda: SQLiteAlbumRepository(container.resolve(ConnectionPool)),
        singleton=True,
    )
    container.register_factory(
        IAssetRepository,
        lambda: SQLiteAssetRepository(container.resolve(ConnectionPool)),
        singleton=True,
    )

    container.register_factory(
        OpenAlbumUseCase,
        lambda: OpenAlbumUseCase(
            album_repo=container.resolve(IAlbumRepository),
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
        ),
    )

    container.register_factory(
        ScanAlbumUseCase,
        lambda: ScanAlbumUseCase(
            album_repo=container.resolve(IAlbumRepository),
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
            metadata_provider=container.resolve(IMetadataProvider),
            thumbnail_generator=container.resolve(IThumbnailGenerator),
        ),
    )

    container.register_factory(
        PairLivePhotosUseCase,
        lambda: PairLivePhotosUseCase(
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
        ),
    )

    container.register_factory(
        AlbumService,
        lambda: AlbumService(
            open_album_use_case=container.resolve(OpenAlbumUseCase),
            scan_album_use_case=container.resolve(ScanAlbumUseCase),
            pair_live_photos_use_case=container.resolve(PairLivePhotosUseCase),
        ),
        singleton=True,
    )

    container.register_factory(
        AssetService,
        lambda: AssetService(asset_repo=container.resolve(IAssetRepository)),
        singleton=True,
    )

    return container
```

### 8.2 `src/iPhoto/presentation/qt/session/app_session.py`
```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

@dataclass
class AppSession:
    settings: object
    library: object
    facade: object
    recent_albums: List[Path] = field(default_factory=list)
    theme: object | None = None
    defer_startup_tasks: bool = False
    _pending_basic_library_path: Path | None = None

    def resume_startup_tasks(self) -> None:
        ...

    def remember_album(self, root: Path) -> None:
        ...
```

### 8.3 `src/iPhoto/presentation/qt/facade/album_facade.py`
```python
from __future__ import annotations

from pathlib import Path

class AlbumFacade:
    def __init__(self, *, backend_bridge, metadata_service, library_update_service, current_album_getter, library_manager_getter, error_emitter, album_opened_emitter, load_started_emitter, load_finished_emitter):
        self._backend = backend_bridge
        self._metadata_service = metadata_service
        self._library_update_service = library_update_service
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._error = error_emitter
        self._album_opened = album_opened_emitter
        self._load_started = load_started_emitter
        self._load_finished = load_finished_emitter

    def open_album(self, root: Path):
        ...

    def set_cover(self, rel: str) -> bool:
        ...

    def toggle_featured(self, ref: str) -> bool:
        ...

    def pair_live_current(self):
        ...
```

### 8.4 `src/iPhoto/presentation/qt/facade/asset_facade.py`
```python
class AssetFacade:
    def __init__(self, *, import_service, move_service, deletion_service, restoration_service):
        self._import_service = import_service
        self._move_service = move_service
        self._deletion_service = deletion_service
        self._restoration_service = restoration_service

    def import_files(self, sources, *, destination=None, mark_featured=False) -> None:
        ...

    def move_assets(self, sources, destination) -> None:
        ...

    def delete_assets(self, sources) -> None:
        ...

    def restore_assets(self, sources) -> bool:
        ...
```

### 8.5 `src/iPhoto/presentation/qt/facade/library_facade.py`
```python
class LibraryFacade:
    def __init__(self, *, library_update_service, task_manager, current_album_getter, library_manager_getter, error_emitter):
        self._library_update_service = library_update_service
        self._task_manager = task_manager
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._error = error_emitter

    def rescan_current(self):
        ...

    def rescan_current_async(self) -> None:
        ...

    def cancel_active_scans(self) -> None:
        ...

    def announce_album_refresh(self, root, *, request_reload=True, force_reload=False, announce_index=False):
        ...
```

---

## 9. 第一阶段开发过程

### 9.1 开发顺序
1. 建立新骨架文件：
   - `bootstrap/container.py`
   - `presentation/qt/session/app_session.py`
   - `presentation/qt/facade/album_facade.py`
   - `presentation/qt/facade/asset_facade.py`
   - `presentation/qt/facade/library_facade.py`
2. 迁移 `appctx.py`
3. 瘦身 `gui/facade.py`
4. 冻结 `app.py`
5. 为扫描链路建立 use case 入口：
   - `rescan_album_use_case.py`
   - `pair_live_photos_use_case_v2.py`
   - `persist_scan_result_use_case.py`
6. 把 `library_update_service.py` 改为调用 use case

---

## 10. 第一阶段任务清单

- [ ] 新建 `bootstrap/container.py`
- [ ] 新建 `presentation/qt/session/app_session.py`
- [ ] 修改 `appctx.py` 为兼容壳
- [ ] 修改 `gui/main.py` 适配新的 context/session 结构
- [ ] 新建 `presentation/qt/facade/album_facade.py`
- [ ] 新建 `presentation/qt/facade/asset_facade.py`
- [ ] 新建 `presentation/qt/facade/library_facade.py`
- [ ] 修改 `gui/facade.py`，改为组合器
- [ ] 冻结 `app.py`
- [ ] 新建 `application/use_cases/scan/rescan_album_use_case.py`
- [ ] 新建 `application/use_cases/scan/pair_live_photos_use_case_v2.py`
- [ ] 新建 `application/use_cases/scan/persist_scan_result_use_case.py`
- [ ] 修改 `gui/services/library_update_service.py`
- [ ] 将底层业务处理改为调用 use case
- [ ] 保持 UI signal 行为不变

---

## 11. 第一阶段验收标准

### 11.1 代码结构验收
1. 新增业务不再进入 `app.py`
2. `gui/facade.py` 不再新增真实业务逻辑
3. `appctx.py` 不再承担容器主装配逻辑
4. `library_update_service.py` 不再直接成为底层业务规则宿主
5. 新入口文件已经建立并可调用

### 11.2 运行行为验收
以下行为必须与现状保持一致：
- 打开相册
- 自动扫描
- 重扫
- Live Photo 配对
- 收藏切换
- 设置封面
- 导入
- 移动
- 删除
- 恢复

### 11.3 测试验收
至少补齐以下测试：

#### 单元测试
- `build_container()` 能正确解析关键依赖
- `AppContext` 兼容层能正常创建 session 与 container
- `AppFacade` 调用时能正确委托到子 façade

#### 集成测试
- 打开相册流程不回归
- rescan 流程不回归
- pair live 流程不回归
- import/move/delete/restore 信号不回归

---

## 12. 第一阶段完成后的最终状态

### 已完成
- 业务主入口开始向 `application/use_cases/*` 收拢
- GUI façade 已拆出子 façade
- AppContext 已拆出 container/session 概念
- 旧 `app.py` 已降级为兼容桥
- 第二阶段扫描子域独立拆分已具备条件

### 尚未完成
- `LibraryManager` 仍未完全拆掉
- 扫描子域仍未彻底从 Qt service 中抽离
- 基础设施目录还未完全规整
- 一些 legacy model / dto / path policy 仍待收口

---

## 13. 第二阶段预告（不是本阶段任务）

1. 扫描子域彻底独立
2. 路径策略统一到 `policy/resolver`
3. `LibraryManager` 拆成 tree / watch / scan service
4. 清理 legacy model 与旧 backend façade
5. 统一 Result / Error 语义

---

## 14. 风险与回滚

### 风险
1. façade 拆分后 signal 中转遗漏
2. `appctx.py` 切分后初始化顺序问题
3. `library_update_service.py` 切到 use case 后行为细节变化
4. CLI / GUI 兼容路径未完全覆盖

### 回滚策略
- 保留 `app.py` 原函数签名
- 保留 `AppContext` 类名
- 保留 `AppFacade` 类名
- 所有新拆分先做内部委托，不先改调用方接口

---

## 15. Definition of Done

- [ ] `app.py` 已标记为兼容层，且无新增业务逻辑
- [ ] `appctx.py` 已拆出 `bootstrap/container.py`
- [ ] `appctx.py` 已拆出 `presentation/qt/session/app_session.py`
- [ ] `gui/facade.py` 已变为组合器
- [ ] `album_facade.py` / `asset_facade.py` / `library_facade.py` 已落地
- [ ] `library_update_service.py` 已通过 use case 进行业务调用
- [ ] 打开、扫描、导入、删除、恢复、配对流程不回归
- [ ] 关键集成测试通过

---

## 16. 第一刀最小执行顺序

### 先建文件，不先删文件
先建：
```text
src/iPhoto/bootstrap/container.py
src/iPhoto/presentation/qt/session/app_session.py
src/iPhoto/presentation/qt/facade/album_facade.py
src/iPhoto/presentation/qt/facade/asset_facade.py
src/iPhoto/presentation/qt/facade/library_facade.py
```

### 然后做 3 个最小替换
1. 把 `appctx.py` 里的 `_create_di_container()` 挪到 `bootstrap/container.py`
2. 把 `gui/facade.py` 中的方法按职责分发到三个新 façade
3. 把 `app.py` 标记为 compatibility only

### 最后再碰扫描链路
第一阶段里先只改：
```text
src/iPhoto/gui/services/library_update_service.py
```
把它内部直接用到 `backend.rescan()` / `backend.pair()` 的地方，替换为新的 use case 调用。

---

## 17. 建议的最小提交顺序

### Commit 1
- 新建 `bootstrap/container.py`
- 新建 `presentation/qt/session/app_session.py`
- 修改 `appctx.py`

### Commit 2
- 新建三个 façade 文件
- 修改 `gui/facade.py`

### Commit 3
- 冻结 `app.py`
- 新建 scan use case
- 修改 `library_update_service.py`

### Commit 4
- 补单元测试与回归测试
- 修兼容问题

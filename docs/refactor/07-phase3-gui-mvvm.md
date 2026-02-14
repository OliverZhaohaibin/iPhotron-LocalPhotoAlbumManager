# 07 — 阶段三：GUI 层 MVVM 重构

> 目标：ViewModel 纯化、Coordinator 精简、大文件拆分、Qt Signal 解耦。  
> 时间：4-5 周  
> 风险：🔴 高（GUI 层变更最容易引入可见回归）  
> 前置：阶段一、阶段二完成

---

## 1. MVVM 模式落地

### 1.1 当前 vs 目标

```mermaid
graph TB
    subgraph "当前：混合模式 ⚠️"
        C_View["View (QWidget)"]
        C_Coord["MainCoordinator<br/>535行<br/>DI + 编排 + 状态"]
        C_VM["ViewModel<br/>含 Qt 依赖"]
        C_DS["DataSource<br/>938行"]
        C_Facade["AppFacade<br/>734行"]

        C_View --> C_Coord
        C_Coord --> C_VM
        C_Coord --> C_Facade
        C_VM --> C_DS
        C_DS --> C_Facade
    end

    subgraph "目标：纯 MVVM ✅"
        T_View["View (QWidget)<br/>仅渲染 + 输入"]
        T_VM2["ViewModel<br/>纯 Python<br/>自定义 Signal"]
        T_Coord2["Coordinator<br/>≤200行<br/>仅导航 + 生命周期"]
        T_UC["Use Cases"]
        T_EB["EventBus"]

        T_View --> T_VM2
        T_VM2 --> T_Coord2
        T_Coord2 --> T_UC
        T_UC --> T_EB
        T_EB --> T_VM2
    end

    style C_Coord fill:#ff6b6b,color:#fff
    style C_DS fill:#ff6b6b,color:#fff
    style C_Facade fill:#ff6b6b,color:#fff
    style T_VM2 fill:#51cf66,color:#fff
    style T_Coord2 fill:#51cf66,color:#fff
    style T_EB fill:#fcc419,color:#333
```

### 1.2 数据流规范

```mermaid
graph LR
    subgraph "单向数据流"
        User["用户操作"] -->|"1"| View2["View"]
        View2 -->|"2. 调用方法"| VM2["ViewModel"]
        VM2 -->|"3. 执行"| UC2["UseCase"]
        UC2 -->|"4. 发布事件"| EB2["EventBus"]
        EB2 -->|"5. 通知"| VM2
        VM2 -->|"6. 更新属性"| View2
        View2 -->|"7. 渲染"| User
    end

    style View2 fill:#339af0,color:#fff
    style VM2 fill:#51cf66,color:#fff
    style UC2 fill:#845ef7,color:#fff
    style EB2 fill:#fcc419,color:#333
```

**规则**:
1. View **不能** 直接调用 Use Case 或 Service
2. ViewModel **不能** 持有 Qt Widget 引用
3. Coordinator **不能** 包含业务逻辑
4. EventBus **不能** 传递 Qt 对象

---

## 2. ViewModel 纯化

### 2.1 自定义信号系统

为了让 ViewModel 脱离 Qt 依赖，引入纯 Python 信号机制：

```python
# src/iPhoto/gui/viewmodels/signal.py
class Signal:
    """纯 Python 信号 — 不依赖 Qt"""

    def __init__(self):
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> None:
        self._handlers.append(handler)

    def disconnect(self, handler: Callable) -> None:
        self._handlers.remove(handler)

    def emit(self, *args, **kwargs) -> None:
        for handler in self._handlers:
            handler(*args, **kwargs)


class ObservableProperty:
    """可观察属性 — ViewModel 数据绑定基础"""

    def __init__(self, initial_value=None):
        self._value = initial_value
        self.changed = Signal()

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        if self._value != new_value:
            old_value = self._value
            self._value = new_value
            self.changed.emit(new_value, old_value)
```

### 2.2 ViewModel 基类

```python
# src/iPhoto/gui/viewmodels/base.py
class BaseViewModel:
    """ViewModel 基类 — 纯 Python，无 Qt 依赖"""

    def __init__(self):
        self._subscriptions: list[Subscription] = []

    def subscribe_event(self, event_bus: EventBus, event_type: type, handler: Callable):
        sub = event_bus.subscribe(event_type, handler)
        self._subscriptions.append(sub)

    def dispose(self):
        """清理所有事件订阅"""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
```

### 2.3 AssetListViewModel 重构

```mermaid
graph TB
    subgraph "当前 AssetListViewModel ⚠️"
        AVM1["AssetListViewModel"]
        AVM1 --> D1["数据加载"]
        AVM1 --> D2["缩略图缓存"]
        AVM1 --> D3["选择状态"]
        AVM1 --> D4["排序/过滤"]
        AVM1 --> D5["Qt Model 适配"]
    end

    subgraph "目标：职责拆分 ✅"
        AVM2["AssetListViewModel<br/>≤150行"]
        ADS["AssetDataSource<br/>(数据加载)"]
        TCS["ThumbnailCacheService<br/>(缩略图缓存)"]
        SS["SelectionState<br/>(选择状态)"]
        SF["SortFilterModel<br/>(排序/过滤)"]
        QA["QtAssetListAdapter<br/>(Qt Model 适配)"]

        AVM2 --> ADS
        AVM2 --> TCS
        AVM2 --> SS
        AVM2 --> SF
        QA -->|"适配"| AVM2
    end

    style AVM1 fill:#ff6b6b,color:#fff
    style AVM2 fill:#51cf66,color:#fff
    style ADS fill:#74c0fc,color:#333
    style TCS fill:#74c0fc,color:#333
    style SS fill:#74c0fc,color:#333
    style SF fill:#74c0fc,color:#333
    style QA fill:#339af0,color:#fff
```

示例实现：

```python
# src/iPhoto/gui/viewmodels/asset_list_viewmodel.py (目标: ≤150行)
class AssetListViewModel(BaseViewModel):
    """资产列表 ViewModel — 纯 Python"""

    def __init__(
        self,
        data_source: AssetDataSource,
        thumbnail_cache: ThumbnailCacheService,
        event_bus: EventBus,
    ):
        super().__init__()
        self._data_source = data_source
        self._thumbnail_cache = thumbnail_cache

        # 可观察属性
        self.assets = ObservableProperty([])
        self.selected_indices = ObservableProperty([])
        self.loading = ObservableProperty(False)
        self.total_count = ObservableProperty(0)

        # 事件订阅
        self.subscribe_event(event_bus, ScanCompletedEvent, self._on_scan_completed)
        self.subscribe_event(event_bus, AssetImportedEvent, self._on_assets_imported)

    def load_album(self, album_id: str) -> None:
        self.loading.value = True
        assets = self._data_source.load_assets(album_id)
        self.assets.value = assets
        self.total_count.value = len(assets)
        self.loading.value = False

    def select(self, index: int) -> None:
        current = list(self.selected_indices.value)
        if index not in current:
            current.append(index)
        self.selected_indices.value = current

    def get_thumbnail(self, asset_id: str) -> bytes | None:
        return self._thumbnail_cache.get(asset_id)

    def _on_scan_completed(self, event: ScanCompletedEvent):
        self.load_album(event.album_id)

    def _on_assets_imported(self, event: AssetImportedEvent):
        self.load_album(event.album_id)
```

---

## 3. Coordinator 精简

### 3.1 Coordinator 拆分计划

```mermaid
graph TB
    subgraph "当前 MainCoordinator (535行)"
        MC["MainCoordinator"]
        MC --> R1["DI 解析"]
        MC --> R2["15+ 子 Coordinator 管理"]
        MC --> R3["Service 连线"]
        MC --> R4["UI 状态管理"]
        MC --> R5["ViewModel 创建"]
        MC --> R6["导航逻辑"]
    end

    subgraph "目标：精简到 ≤200行"
        MC2["MainCoordinator<br/>≤200行"]
        MC2 --> R2_2["子 Coordinator 协调"]
        MC2 --> R6_2["页面导航"]

        DIB["DI Bootstrap<br/>(独立模块)"]
        VMF["ViewModelFactory<br/>(独立工厂)"]
        NavS["NavigationService<br/>(导航逻辑)"]
    end

    R1 -->|"提取"| DIB
    R5 -->|"提取"| VMF
    R6 -->|"提取"| NavS

    style MC fill:#ff6b6b,color:#fff
    style MC2 fill:#51cf66,color:#fff
    style DIB fill:#74c0fc,color:#333
    style VMF fill:#74c0fc,color:#333
    style NavS fill:#74c0fc,color:#333
```

### 3.2 ViewModelFactory

```python
# src/iPhoto/gui/factories/viewmodel_factory.py
class ViewModelFactory:
    """集中创建 ViewModel — 替代 Coordinator 中的手动创建"""

    def __init__(self, container: Container):
        self._container = container

    def create_asset_list_vm(self) -> AssetListViewModel:
        return AssetListViewModel(
            data_source=self._container.resolve(AssetDataSource),
            thumbnail_cache=self._container.resolve(ThumbnailCacheService),
            event_bus=self._container.resolve(EventBus),
        )

    def create_album_tree_vm(self) -> AlbumTreeViewModel:
        return AlbumTreeViewModel(
            album_service=self._container.resolve(AlbumService),
            event_bus=self._container.resolve(EventBus),
        )

    def create_detail_vm(self) -> DetailViewModel:
        return DetailViewModel(
            asset_service=self._container.resolve(AssetService),
            edit_service=self._container.resolve(EditService),
            event_bus=self._container.resolve(EventBus),
        )
```

### 3.3 NavigationService

```python
# src/iPhoto/gui/services/navigation_service.py
class NavigationService:
    """页面导航管理 — 替代 Coordinator 中的导航逻辑"""

    def __init__(self):
        self.page_changed = Signal()  # (page_name, params)
        self._history: list[tuple[str, dict]] = []

    def navigate_to(self, page: str, **params):
        self._history.append((page, params))
        self.page_changed.emit(page, params)

    def go_back(self):
        if len(self._history) > 1:
            self._history.pop()
            page, params = self._history[-1]
            self.page_changed.emit(page, params)
```

---

## 4. 大文件拆分

### 4.1 拆分计划

```mermaid
graph TB
    subgraph "拆分优先级"
        F1["edit_sidebar.py<br/>1,052行 → 4个文件"]
        F2["edit_curve_section.py<br/>1,165行 → 3个文件"]
        F3["thumbnail_loader.py<br/>963行 → 3个文件"]
        F4["asset_data_source.py<br/>938行 → 4个文件"]
        F5["gl_renderer.py<br/>940行 → 3个文件"]
        F6["manager.py (Library)<br/>909行 → 5个文件"]
    end

    style F1 fill:#ff6b6b,color:#fff
    style F2 fill:#ff6b6b,color:#fff
    style F3 fill:#ffa94d,color:#fff
    style F4 fill:#ffa94d,color:#fff
    style F5 fill:#ffa94d,color:#fff
    style F6 fill:#ffa94d,color:#fff
```

### 4.2 edit_sidebar.py 拆分方案

```
当前: edit_sidebar.py (1,052行)

目标:
├── edit_sidebar.py           (≤200行, 容器 + 布局)
├── edit_section_manager.py   (≤150行, Section 切换管理)
├── edit_signal_router.py     (≤150行, 信号连接)
└── edit_state_manager.py     (≤150行, 编辑状态管理)
```

### 4.3 edit_curve_section.py 拆分方案

```
当前: edit_curve_section.py (1,165行)

目标:
├── edit_curve_section.py     (≤200行, UI 部分)
├── curve_algorithm.py        (≤300行, 贝塞尔曲线数学) → 移到 core/
└── curve_interaction.py      (≤200行, 鼠标交互逻辑)
```

### 4.4 asset_data_source.py 拆分方案

```
当前: asset_data_source.py (938行)

目标:
├── asset_data_source.py      (≤200行, 接口 + 协调)
├── asset_data_loader.py      (≤200行, 数据加载)
├── asset_cache_manager.py    (≤150行, 本地缓存)
└── asset_async_mover.py      (≤150行, 异步移动)  → 移到 Use Case
```

### 4.5 LibraryManager 拆分方案 (已部分完成)

```
当前: manager.py (909行) — 已有 scan_coordinator, filesystem_watcher, trash_manager

进一步拆分:
├── manager.py                (≤200行, 协调者)
├── scan_coordinator.py       (已存在 ✅)
├── filesystem_watcher.py     (已存在 ✅)
├── trash_manager.py          (已存在 ✅)
├── geo_aggregator.py         (≤150行, 地理编码聚合) 🆕
└── album_operations.py       (≤200行, 相册 CRUD) 🆕
```

---

## 5. Qt Signal → EventBus 迁移

### 5.1 迁移策略

```mermaid
graph TB
    subgraph "阶段 A: 双轨运行"
        EB3["EventBus"]
        Bridge["QtEventBridge"]
        OldSignal["旧 Qt Signal"]

        EB3 --> Bridge
        Bridge -->|"转发为 Qt Signal"| OldSignal
    end

    subgraph "阶段 B: ViewModel 切换"
        EB4["EventBus"]
        VM4["ViewModel<br/>(订阅 EventBus)"]
        QA2["Qt Adapter"]

        EB4 --> VM4
        VM4 --> QA2
    end

    subgraph "阶段 C: 完全迁移"
        EB5["EventBus"]
        VM5["ViewModel"]
        QA3["Qt Adapter<br/>(仅 UI 更新)"]

        EB5 --> VM5
        VM5 --> QA3
    end

    style Bridge fill:#fcc419,color:#333
    style OldSignal fill:#ff6b6b,color:#fff
    style EB5 fill:#51cf66,color:#fff
```

### 5.2 迁移步骤

1. **阶段 A** (与阶段二重叠): 启用 `QtEventBridge`，将 EventBus 事件转发为 Qt Signal
2. **阶段 B**: 新的 ViewModel 直接订阅 EventBus，不通过 Qt Signal
3. **阶段 C**: 删除 `QtEventBridge`，所有 Qt Signal 仅用于 View ↔ ViewModel 的 UI 更新

---

## 6. 阶段三检查清单

- [ ] **ViewModel 纯化**
  - [ ] 实现纯 Python `Signal` 类
  - [ ] 实现 `ObservableProperty` 数据绑定
  - [ ] 实现 `BaseViewModel` 基类
  - [ ] 重构 `AssetListViewModel` (≤150行)
  - [ ] 重构 `AlbumTreeViewModel`
  - [ ] 重构 `DetailViewModel`
  - [ ] 每个 ViewModel ≥3 个单元测试（无需 QApplication）
- [ ] **Coordinator 精简**
  - [ ] 提取 `ViewModelFactory`
  - [ ] 提取 `NavigationService`
  - [ ] 提取 DI Bootstrap 到独立模块
  - [ ] `MainCoordinator` ≤200行
- [ ] **大文件拆分**
  - [ ] `edit_sidebar.py` → 4 个文件
  - [ ] `edit_curve_section.py` → 3 个文件
  - [ ] `asset_data_source.py` → 4 个文件
  - [ ] `thumbnail_loader.py` → 3 个文件
  - [ ] `gl_renderer.py` → 3 个文件
  - [ ] `manager.py` → 增加 `geo_aggregator.py` + `album_operations.py`
- [ ] **Qt Signal 迁移**
  - [ ] 阶段 A: 启用 QtEventBridge
  - [ ] 阶段 B: 新 ViewModel 订阅 EventBus
  - [ ] 阶段 C: 删除 QtEventBridge，完成迁移

# 03 — 目标架构设计

> iPhoton 目标架构：MVVM + Clean Architecture，完全解耦 GUI 与业务逻辑。

---

## 1. 目标架构全景

### 1.1 层次结构

```mermaid
graph TB
    subgraph "表现层 Presentation"
        direction TB
        GUI["PySide6 GUI"]
        CLI["Typer CLI"]

        subgraph "GUI 内部 (MVVM)"
            Views["Views<br/>QWidget / QML"]
            VMs["ViewModels<br/>纯 Python + 自定义信号"]
            Coords["Coordinators<br/>≤15个，职责单一"]
        end

        Views --> VMs
        VMs --> Coords
    end

    subgraph "应用层 Application"
        direction TB
        UseCases["Use Cases<br/>≥11个，覆盖所有业务场景"]
        AppServices["Application Services<br/>AlbumService / AssetService / EditService"]
        EventBus["EventBus<br/>发布-订阅，跨层通信"]
        TaskQueue["Task Queue<br/>后台任务管理"]
    end

    subgraph "领域层 Domain"
        direction TB
        Entities["Entities<br/>Album / Asset / LiveGroup"]
        ValueObj["Value Objects<br/>MediaType / GeoLocation / EditParams"]
        DomainSvc["Domain Services<br/>PairingService / ClassificationService"]
        RepoIface["Repository Interfaces<br/>IAlbumRepo / IAssetRepo / ICacheRepo"]
    end

    subgraph "基础设施层 Infrastructure"
        direction TB
        SQLiteRepo["SQLite Repositories"]
        ConnPool["Connection Pool<br/>线程安全"]
        FileIO["File I/O<br/>Scanner / Metadata"]
        ThumbSvc["Thumbnail Service<br/>三级缓存"]
        ExternalTools["External Tools<br/>ExifTool / FFmpeg"]
    end

    subgraph "核心算法层 Core"
        Pairing2["Live Photo Pairing"]
        ImgPipeline["Image Pipeline<br/>Light / Color / Curves"]
        JIT["JIT Filters (Numba)"]
        GLRender["OpenGL Renderer"]
    end

    GUI --> Coords
    CLI --> AppServices
    Coords --> UseCases
    Coords --> EventBus
    UseCases --> AppServices
    AppServices --> RepoIface
    AppServices --> DomainSvc
    RepoIface --> SQLiteRepo
    SQLiteRepo --> ConnPool
    AppServices --> FileIO
    AppServices --> ThumbSvc
    ThumbSvc --> ExternalTools
    UseCases --> TaskQueue
    ImgPipeline --> JIT
    GLRender --> ImgPipeline

    style GUI fill:#339af0,color:#fff
    style CLI fill:#339af0,color:#fff
    style UseCases fill:#51cf66,color:#fff
    style EventBus fill:#fcc419,color:#333
    style TaskQueue fill:#fcc419,color:#333
    style Entities fill:#845ef7,color:#fff
    style RepoIface fill:#845ef7,color:#fff
    style ConnPool fill:#ff922b,color:#fff
    style ThumbSvc fill:#ff922b,color:#fff
```

### 1.2 依赖规则（内层不依赖外层）

```mermaid
graph LR
    subgraph "依赖方向 →"
        P["Presentation"] --> A["Application"]
        A --> D["Domain"]
        I["Infrastructure"] --> D
        P --> A
        A -.->|"通过接口"| I
    end

    subgraph "禁止的依赖 ✘"
        D2["Domain"] -.-x|"❌"| P2["Presentation"]
        D3["Domain"] -.-x|"❌"| I2["Infrastructure"]
        A2["Application"] -.-x|"❌"| P3["Presentation"]
    end

    style D fill:#845ef7,color:#fff
    style A fill:#51cf66,color:#fff
    style P fill:#339af0,color:#fff
    style I fill:#ff922b,color:#fff
```

---

## 2. 各层详细设计

### 2.1 表现层 — MVVM 模式

```mermaid
graph TB
    subgraph "MVVM 数据流"
        View["View (QWidget)"]
        VM["ViewModel (Python)"]
        Model["Domain Model"]
        Cmd["Command / Use Case"]

        View -->|"1. 用户操作"| VM
        VM -->|"2. 执行命令"| Cmd
        Cmd -->|"3. 更新模型"| Model
        Model -->|"4. 通知变化"| VM
        VM -->|"5. 更新展示"| View
    end

    style View fill:#339af0,color:#fff
    style VM fill:#74c0fc,color:#333
    style Model fill:#845ef7,color:#fff
    style Cmd fill:#51cf66,color:#fff
```

**设计要点**:
- **View** 只负责渲染和用户输入捕获，不包含任何业务逻辑
- **ViewModel** 使用自定义信号（非 Qt Signal），可在非 GUI 环境测试
- **Coordinator** 负责页面导航和 ViewModel 生命周期管理
- 单向数据流：View → ViewModel → UseCase → Model → ViewModel → View

### 2.2 应用层 — Use Case 驱动

**目标 Use Case 清单**:

```mermaid
graph TB
    subgraph "Use Cases (完整覆盖)"
        UC1["OpenAlbumUseCase ✅"]
        UC2["ScanAlbumUseCase ✅"]
        UC3["PairLivePhotosUseCase ✅"]
        UC4["ImportAssetsUseCase 🆕"]
        UC5["MoveAssetsUseCase 🆕"]
        UC6["GenerateThumbnailUseCase 🆕"]
        UC7["UpdateMetadataUseCase 🆕"]
        UC8["CreateAlbumUseCase 🆕"]
        UC9["DeleteAlbumUseCase 🆕"]
        UC10["ManageTrashUseCase 🆕"]
        UC11["AggregateGeoDataUseCase 🆕"]
        UC12["WatchFilesystemUseCase 🆕"]
        UC13["ExportAssetsUseCase 🆕"]
        UC14["ApplyEditUseCase 🆕"]
    end

    style UC1 fill:#51cf66,color:#fff
    style UC2 fill:#51cf66,color:#fff
    style UC3 fill:#51cf66,color:#fff
    style UC4 fill:#fcc419,color:#333
    style UC5 fill:#fcc419,color:#333
    style UC6 fill:#fcc419,color:#333
    style UC7 fill:#fcc419,color:#333
    style UC8 fill:#fcc419,color:#333
    style UC9 fill:#fcc419,color:#333
    style UC10 fill:#fcc419,color:#333
    style UC11 fill:#fcc419,color:#333
    style UC12 fill:#fcc419,color:#333
    style UC13 fill:#fcc419,color:#333
    style UC14 fill:#fcc419,color:#333
```

**EventBus 设计**:

```python
# 目标 EventBus 接口
class EventBus:
    def publish(self, event: DomainEvent) -> None: ...
    def subscribe(self, event_type: type, handler: Callable) -> Subscription: ...
    def unsubscribe(self, subscription: Subscription) -> None: ...

# 事件类型
class AlbumOpenedEvent(DomainEvent): ...
class ScanCompletedEvent(DomainEvent): ...
class AssetImportedEvent(DomainEvent): ...
class ThumbnailReadyEvent(DomainEvent): ...
class MetadataUpdatedEvent(DomainEvent): ...
```

### 2.3 领域层 — 统一模型

```mermaid
classDiagram
    class Album {
        +str id
        +str name
        +Path root_path
        +datetime created_at
        +list~Asset~ assets
    }

    class Asset {
        +str id
        +str filename
        +Path relative_path
        +MediaType media_type
        +AssetMetadata metadata
        +Optional~LiveGroup~ live_group
    }

    class MediaType {
        <<enumeration>>
        PHOTO
        VIDEO
        LIVE_PHOTO
        RAW
    }

    class AssetMetadata {
        +datetime date_taken
        +Optional~GeoLocation~ location
        +dict exif_data
        +int width
        +int height
    }

    class GeoLocation {
        +float latitude
        +float longitude
        +Optional~str~ place_name
    }

    class LiveGroup {
        +str content_identifier
        +Asset photo
        +Asset video
    }

    class EditSession {
        +str asset_id
        +LightParams light
        +ColorParams color
        +CurveParams curves
        +CropParams crop
    }

    Album "1" --> "*" Asset
    Asset --> "1" MediaType
    Asset --> "1" AssetMetadata
    AssetMetadata --> "0..1" GeoLocation
    Asset --> "0..1" LiveGroup
    Asset --> "0..1" EditSession
```

### 2.4 基础设施层 — 服务实现

**三级缩略图缓存**:

```mermaid
graph LR
    Request["缩略图请求"]
    L1["L1: LRU 内存缓存<br/>≤500 张，<100ms"]
    L2["L2: 磁盘缓存<br/>.thumbnails/ 目录"]
    L3["L3: 实时生成<br/>Pillow / FFmpeg"]

    Request --> L1
    L1 -->|"Miss"| L2
    L2 -->|"Miss"| L3
    L3 -->|"回填"| L2
    L2 -->|"回填"| L1

    style L1 fill:#51cf66,color:#fff
    style L2 fill:#fcc419,color:#333
    style L3 fill:#ff922b,color:#fff
```

**连接池设计**:

```python
# 目标连接池接口
class ConnectionPool:
    def __init__(self, db_path: Path, max_connections: int = 4): ...
    def acquire(self) -> Connection: ...
    def release(self, conn: Connection) -> None: ...

    # 上下文管理器
    @contextmanager
    def connection(self) -> Generator[Connection, None, None]: ...
```

---

## 3. 目标数据流

### 3.1 打开相册流程（目标）

```mermaid
sequenceDiagram
    participant User as 用户
    participant View as AlbumListView
    participant VM as AlbumListViewModel
    participant Coord as AlbumCoordinator
    participant UC as OpenAlbumUseCase
    participant Repo as IAlbumRepository
    participant EB as EventBus

    User->>View: 点击相册
    View->>VM: select_album(album_id)
    VM->>Coord: request_open(album_id)
    Coord->>UC: execute(album_id)
    UC->>Repo: find_by_id(album_id)
    Repo-->>UC: Album
    UC->>EB: publish(AlbumOpenedEvent)
    EB-->>VM: on_album_opened(event)
    VM-->>View: 更新绑定数据
    View-->>User: 显示相册内容
```

### 3.2 文件扫描流程（目标）

```mermaid
sequenceDiagram
    participant Coord as ScanCoordinator
    participant UC as ScanAlbumUseCase
    participant TQ as TaskQueue
    participant Scanner as FileScanner
    participant Repo as IAssetRepository
    participant EB as EventBus
    participant VM as AssetListViewModel

    Coord->>UC: execute(album_path)
    UC->>TQ: submit(scan_job, priority=HIGH)
    TQ->>Scanner: scan(album_path)

    loop 每批 100 个文件
        Scanner-->>Repo: batch_insert(assets)
        Scanner-->>EB: publish(ScanProgressEvent)
        EB-->>VM: on_progress(count, total)
    end

    Scanner-->>UC: ScanResult
    UC->>EB: publish(ScanCompletedEvent)
    EB-->>VM: on_scan_completed()
    EB-->>Coord: on_scan_completed()
```

---

## 4. 当前架构 vs 目标架构对比

### 4.1 对比总览

```mermaid
graph TB
    subgraph "当前架构 ⚠️"
        direction TB
        C_GUI["GUI (PySide6)"]
        C_Facade["AppFacade ⚠️<br/>734行 God Object"]
        C_Legacy["Legacy Models<br/>models/album.py"]
        C_Domain["Domain Models"]
        C_GUISvc["GUI Services ⚠️<br/>4个 Qt 耦合服务"]
        C_UC["Use Cases<br/>仅3个"]
        C_Infra["Infrastructure"]

        C_GUI --> C_Facade
        C_Facade --> C_Legacy
        C_Facade --> C_Domain
        C_Facade --> C_GUISvc
        C_Facade --> C_UC
        C_UC --> C_Infra
    end

    subgraph "目标架构 ✅"
        direction TB
        T_GUI["GUI (PySide6)"]
        T_VM["ViewModels<br/>纯 Python"]
        T_Coord["Coordinators<br/>≤15个"]
        T_UC["Use Cases<br/>≥14个"]
        T_EB["EventBus<br/>跨层通信"]
        T_Domain2["Domain Models<br/>唯一模型"]
        T_Infra2["Infrastructure"]

        T_GUI --> T_VM
        T_VM --> T_Coord
        T_Coord --> T_UC
        T_UC --> T_Domain2
        T_UC --> T_EB
        T_EB --> T_VM
        T_Domain2 --> T_Infra2
    end

    style C_Facade fill:#ff6b6b,color:#fff
    style C_Legacy fill:#ff6b6b,color:#fff
    style C_GUISvc fill:#ff6b6b,color:#fff
    style T_VM fill:#51cf66,color:#fff
    style T_UC fill:#51cf66,color:#fff
    style T_EB fill:#fcc419,color:#333
    style T_Domain2 fill:#845ef7,color:#fff
```

### 4.2 量化目标

| 指标 | 当前值 | 目标值 | 改善 |
|------|--------|--------|------|
| 最大文件行数 | 1,165行 | ≤300行 | 🟢 -74% |
| God Object | 2个 | 0个 | 🟢 消除 |
| 重复模型 | 2套 | 1套 | 🟢 统一 |
| Use Case 覆盖 | 27% (3/11) | 100% (14/14) | 🟢 +73% |
| EventBus 使用率 | 0% | 100% | 🟢 全面启用 |
| DI 覆盖率 | ~40% | ≥95% | 🟢 +55% |
| Qt 渗透层数 | 3层 | 1层 (仅View) | 🟢 -67% |
| 测试覆盖率 | ~20% | ≥80% | 🟢 +60% |
| 扫描性能 (10K文件) | 85秒 | ≤30秒 | 🟢 -65% |
| UI 阻塞时间 | 8秒 | ≤200ms | 🟢 -97.5% |

---

## 5. 目标架构核心优势

### 5.1 可测试性

```mermaid
graph TB
    subgraph "当前：难以测试 ❌"
        T1_Facade["AppFacade<br/>(需要Qt环境)"]
        T1_GUISvc["GUI Services<br/>(需要Qt环境)"]
        T1_UC["Use Cases<br/>(可独立测试 ✅)"]

        T1_Facade -.->|"需要 QApplication"| QApp1["QApplication"]
        T1_GUISvc -.->|"需要 QApplication"| QApp1
    end

    subgraph "目标：全面可测试 ✅"
        T2_VM["ViewModels<br/>(纯Python测试)"]
        T2_UC["Use Cases<br/>(纯Python测试)"]
        T2_Svc["Services<br/>(纯Python测试)"]
        T2_View["Views<br/>(仅UI层需Qt)"]

        T2_VM -.->|"mock 即可"| Mock["Mock Objects"]
        T2_UC -.->|"mock 即可"| Mock
        T2_Svc -.->|"mock 即可"| Mock
    end

    style T1_Facade fill:#ff6b6b,color:#fff
    style T1_GUISvc fill:#ff6b6b,color:#fff
    style T2_VM fill:#51cf66,color:#fff
    style T2_UC fill:#51cf66,color:#fff
    style T2_Svc fill:#51cf66,color:#fff
```

### 5.2 可维护性

- **单一职责**: 每个类 ≤300 行，职责明确
- **低耦合**: 通过接口和 EventBus 通信，修改一处不影响其他模块
- **高内聚**: 相关功能聚集在同一模块，减少跨模块修改

### 5.3 可扩展性

- **新功能添加**: 只需新增 Use Case + ViewModel，无需修改现有代码
- **新 UI 适配**: 换用 QML 或 Web 前端只需替换 View 层
- **新存储后端**: 实现 Repository 接口即可切换数据库

### 5.4 性能

- **并行扫描**: TaskQueue + Worker Pool，10K 文件 ≤30秒
- **三级缓存**: 缩略图命中率 >95%，首屏加载 <200ms
- **异步加载**: UI 线程零阻塞，所有 I/O 在后台完成

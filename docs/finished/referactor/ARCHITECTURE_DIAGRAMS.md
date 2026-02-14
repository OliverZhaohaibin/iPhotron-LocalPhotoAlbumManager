# iPhotron 架构可视化图表
# Architecture Visualization Diagrams

本文档包含iPhotron项目的关键架构图表，帮助理解系统设计和数据流。

---

## 1. 当前架构 vs 目标架构对比

### 当前架构 (Current Architecture)

```mermaid
graph TB
    subgraph "GUI Layer - 43 Controllers"
        MC[MainController<br/>God Object]
        MC --> VC1[ViewController]
        MC --> VC2[EditController]
        MC --> VC3[NavigationController]
        MC --> VC4[PlaybackController]
        MC --> VC5[InteractionManager]
        MC --> VC6[DataManager]
        MC --> VC7[DialogController]
        MC --> VC8[StatusBarController]
        MC --> Others[+35 more controllers...]
    end
    
    subgraph "Data Layer"
        ALM[AssetListModel<br/>400+ LOC<br/>混合职责]
        ALM --> Cache[CacheManager]
        ALM --> State[StateManager]
        ALM --> Adapter[RowAdapter]
        ALM --> Loader[DataLoader]
    end
    
    subgraph "Backend"
        APP[app.py<br/>Backend Facade]
        APP --> Repo[AssetRepository<br/>Concrete Class]
        Repo --> DB[(SQLite)]
    end
    
    MC -.直接引用.-> ALM
    MC -.直接引用.-> APP
    ALM --> APP
    
    style MC fill:#ff9999
    style ALM fill:#ff9999
    style Repo fill:#ffcc99
```

### 目标架构 (Target Architecture - MVVM + DDD)

```mermaid
graph TB
    subgraph "Presentation Layer - 15 Coordinators"
        MCoord[MainCoordinator<br/>Thin Orchestrator]
        MCoord --> NavCoord[NavigationCoordinator]
        MCoord --> ViewRouter[ViewRouter]
        MCoord --> PlayCoord[PlaybackCoordinator]
        MCoord --> EditCoord[EditCoordinator]
    end
    
    subgraph "ViewModel Layer"
        AlbumVM[AlbumViewModel<br/>80 LOC]
        AssetVM[AssetListViewModel<br/>120 LOC]
        EditVM[EditViewModel<br/>100 LOC]
    end
    
    subgraph "Application Layer"
        OpenUC[OpenAlbumUseCase]
        ScanUC[ScanAlbumUseCase]
        EditUC[ApplyEditUseCase]
        AlbumSvc[AlbumService]
        AssetSvc[AssetService]
    end
    
    subgraph "Domain Layer"
        IAlbumRepo[IAlbumRepository<br/>Interface]
        IAssetRepo[IAssetRepository<br/>Interface]
        Album[Album Entity]
        Asset[Asset Entity]
    end
    
    subgraph "Infrastructure Layer"
        SQLiteAlbum[SQLiteAlbumRepository<br/>Implementation]
        SQLiteAsset[SQLiteAssetRepository<br/>Implementation]
        Pool[Connection Pool]
        DB[(SQLite)]
    end
    
    subgraph "Cross-Cutting"
        EventBus[Event Bus]
        DI[DI Container]
    end
    
    MCoord -.EventBus.-> EventBus
    AlbumVM --> OpenUC
    AssetVM --> ScanUC
    EditVM --> EditUC
    
    OpenUC --> IAlbumRepo
    ScanUC --> IAssetRepo
    
    IAlbumRepo -.实现.-> SQLiteAlbum
    IAssetRepo -.实现.-> SQLiteAsset
    
    SQLiteAlbum --> Pool
    SQLiteAsset --> Pool
    Pool --> DB
    
    DI -.注入.-> OpenUC
    DI -.注入.-> IAssetRepo
    
    style MCoord fill:#99ff99
    style AlbumVM fill:#99ff99
    style OpenUC fill:#99ccff
    style IAssetRepo fill:#cc99ff
    style SQLiteAsset fill:#ffff99
```

---

## 2. 数据流程对比

### 当前扫描流程 (Current Scanning - Serial)

```mermaid
sequenceDiagram
    participant User
    participant MainController
    participant AppFacade
    participant ScannerWorker
    participant FileDiscoverer
    participant ExifTool
    participant AssetRepository
    participant SQLite

    User->>MainController: Click Rescan
    MainController->>AppFacade: scan_current_album()
    AppFacade->>ScannerWorker: submit_task()
    
    Note over ScannerWorker,FileDiscoverer: 单线程文件遍历
    ScannerWorker->>FileDiscoverer: walk directory
    FileDiscoverer-->>ScannerWorker: files queue
    
    loop For each file (Serial)
        ScannerWorker->>ExifTool: extract_metadata(file)
        ExifTool-->>ScannerWorker: metadata
    end
    
    Note over ScannerWorker,AssetRepository: 串行写入数据库
    loop For each row
        ScannerWorker->>AssetRepository: append_row()
        AssetRepository->>SQLite: INSERT OR REPLACE
    end
    
    ScannerWorker-->>AppFacade: scanFinished
    AppFacade-->>MainController: Update UI
    MainController-->>User: Show results
    
    Note right of User: 10K files = 85秒<br/>100K files = 15分钟
```

### 目标扫描流程 (Target Scanning - Parallel)

```mermaid
sequenceDiagram
    participant User
    participant MainCoordinator
    participant ScanUseCase
    participant FileDiscovery
    participant MetadataPool
    participant BatchWriter
    participant SQLite

    User->>MainCoordinator: Click Rescan
    MainCoordinator->>ScanUseCase: execute(ScanRequest)
    
    Note over ScanUseCase,FileDiscovery: 多线程文件发现
    par Thread 1
        ScanUseCase->>FileDiscovery: walk subdirectory 1
    and Thread 2
        ScanUseCase->>FileDiscovery: walk subdirectory 2
    and Thread 3
        ScanUseCase->>FileDiscovery: walk subdirectory 3
    and Thread 4
        ScanUseCase->>FileDiscovery: walk subdirectory 4
    end
    
    FileDiscovery-->>ScanUseCase: Merged file queue
    
    Note over ScanUseCase,MetadataPool: 批量元数据提取 (100文件/批)
    par Process 1
        ScanUseCase->>MetadataPool: extract_batch(files[0:100])
    and Process 2
        ScanUseCase->>MetadataPool: extract_batch(files[100:200])
    and Process 3
        ScanUseCase->>MetadataPool: extract_batch(files[200:300])
    end
    
    MetadataPool-->>ScanUseCase: Metadata batches
    
    Note over ScanUseCase,BatchWriter: 批量数据库写入 (事务)
    ScanUseCase->>BatchWriter: save_batch(all_metadata)
    BatchWriter->>SQLite: BEGIN TRANSACTION
    BatchWriter->>SQLite: INSERT OR REPLACE (bulk)
    BatchWriter->>SQLite: COMMIT
    
    BatchWriter-->>ScanUseCase: Success
    ScanUseCase-->>MainCoordinator: ScanCompletedEvent
    MainCoordinator-->>User: Show results
    
    Note right of User: 10K files = 30秒 (65% ↓)<br/>100K files = 5分钟 (67% ↓)
```

---

## 3. 组件交互模式

### 当前模式 (Direct Coupling)

```mermaid
graph LR
    A[Controller A] -->|直接调用| B[Controller B]
    B -->|直接调用| C[Controller C]
    C -->|直接调用| D[Model]
    A -->|直接调用| D
    B -->|直接调用| D
    
    E[Controller E] -->|直接调用| A
    E -->|直接调用| B
    E -->|直接调用| C
    
    style A fill:#ff9999
    style B fill:#ff9999
    style C fill:#ff9999
    style E fill:#ff9999
    
    Note1[问题: 紧耦合<br/>循环依赖<br/>难以测试]
```

### 目标模式 (Event-Driven)

```mermaid
graph TB
    subgraph "Publishers"
        CoordA[Coordinator A]
        CoordB[Coordinator B]
        UseCaseX[UseCase X]
    end
    
    EventBus[Event Bus<br/>Publish/Subscribe]
    
    subgraph "Subscribers"
        CoordC[Coordinator C]
        ServiceY[Service Y]
        CacheZ[Cache Z]
    end
    
    CoordA -.publish.-> EventBus
    CoordB -.publish.-> EventBus
    UseCaseX -.publish.-> EventBus
    
    EventBus -.notify.-> CoordC
    EventBus -.notify.-> ServiceY
    EventBus -.notify.-> CacheZ
    
    style EventBus fill:#99ff99
    style CoordA fill:#99ccff
    style CoordB fill:#99ccff
    style CoordC fill:#99ccff
    
    Note1[优势: 松耦合<br/>无循环依赖<br/>易于测试]
```

---

## 4. 依赖注入流程

### 目标架构依赖注入

```mermaid
graph TB
    subgraph "Application Bootstrap"
        Main[main.py]
        Main --> Config[Load Config]
        Config --> DI[DI Container Setup]
    end
    
    subgraph "DI Container Registration"
        DI --> RegRepo[Register Repositories]
        DI --> RegSvc[Register Services]
        DI --> RegUC[Register Use Cases]
        DI --> RegVM[Register ViewModels]
    end
    
    subgraph "Infrastructure Bindings"
        RegRepo --> BindDB["IAssetRepository → SQLiteAssetRepository"]
        RegRepo --> BindAlbum["IAlbumRepository → FileSystemAlbumRepository"]
        BindDB --> Pool[Connection Pool]
    end
    
    subgraph "Application Bindings"
        RegUC --> BindOpen["OpenAlbumUseCase(IAlbumRepository, IAssetRepository)"]
        RegUC --> BindScan["ScanAlbumUseCase(IAssetRepository, IMetadataProvider)"]
    end
    
    subgraph "Presentation Bindings"
        RegVM --> BindAlbumVM["AlbumViewModel(OpenAlbumUseCase, EventBus)"]
        RegVM --> BindAssetVM["AssetListViewModel(AssetService, CacheService)"]
    end
    
    subgraph "Runtime Resolution"
        Request[User Action]
        Request --> ResolveVM[container.resolve<br/>AlbumViewModel]
        ResolveVM --> InjectDeps[Inject Dependencies]
        InjectDeps --> Instance[Create Instance]
    end
    
    style DI fill:#99ff99
    style BindDB fill:#ffff99
    style InjectDeps fill:#99ccff
```

---

## 5. 重构迁移路径

### 渐进式迁移策略

```mermaid
graph TB
    Start[当前系统<br/>MVC + 具体实现]
    
    subgraph "Phase 1: 基础设施 (2-3周)"
        P1A[实现DI容器]
        P1B[创建EventBus]
        P1C[添加连接池]
    end
    
    subgraph "Phase 2: 仓储层 (3-4周)"
        P2A[定义仓储接口]
        P2B[实现SQLite仓储]
        P2C[创建适配器]
        P2D[渐进式替换]
    end
    
    subgraph "Phase 3: 应用层 (4-5周)"
        P3A[提取Use Cases]
        P3B[创建应用服务]
        P3C[引入DTOs]
    end
    
    subgraph "Phase 4: GUI层 (5-6周)"
        P4A[创建ViewModels]
        P4B[简化Coordinators]
        P4C[重构Views]
    end
    
    subgraph "Phase 5: 优化 (3-4周)"
        P5A[并行扫描]
        P5B[多级缓存]
        P5C[异步加载]
    end
    
    End[新架构<br/>MVVM + DDD + 高性能]
    
    Start --> P1A
    P1A --> P1B --> P1C
    P1C --> P2A
    P2A --> P2B --> P2C --> P2D
    P2D --> P3A
    P3A --> P3B --> P3C
    P3C --> P4A
    P4A --> P4B --> P4C
    P4C --> P5A
    P5A --> P5B --> P5C
    P5C --> End
    
    style Start fill:#ff9999
    style End fill:#99ff99
    style P1A fill:#99ccff
    style P2A fill:#99ccff
    style P3A fill:#99ccff
    style P4A fill:#99ccff
    style P5A fill:#99ccff
```

---

## 6. 控制器简化对比

### 当前控制器网络 (43个)

```mermaid
graph TB
    MC[MainController]
    
    MC --> VCM[ViewControllerManager]
    MC --> NC[NavigationController]
    MC --> IM[InteractionManager]
    MC --> DM[DataManager]
    MC --> DC[DialogController]
    MC --> SBC[StatusBarController]
    
    VCM --> VC[ViewController]
    VCM --> EC[EditController]
    VCM --> DVC[DetailViewController]
    
    IM --> PC[PlaybackController]
    IM --> SC[SelectionController]
    IM --> ASM[AssetStateManager]
    IM --> DDC[DragDropController]
    
    EC --> EPM[EditPreviewManager]
    EC --> EHM[EditHistoryManager]
    EC --> EVT[EditViewTransition]
    EC --> ELS[EditLightSection]
    EC --> ECS[EditColorSection]
    EC --> EBS[EditBWSection]
    
    DVC --> FVC[FilmstripViewController]
    DVC --> PBC[PlayerBarController]
    DVC --> IPC[InfoPanelController]
    
    NC --> ABC[AlbumBrowserController]
    NC --> LNC[LibraryNavigationController]
    
    MC --> Others["+18 more controllers..."]
    
    style MC fill:#ff0000
    style VCM fill:#ff6666
    style NC fill:#ff6666
    style IM fill:#ff6666
```

### 目标协调器结构 (15个)

```mermaid
graph TB
    MCoord[MainCoordinator<br/>Thin Orchestrator]
    
    MCoord --> NavCoord[NavigationCoordinator]
    MCoord --> ViewRouter[ViewRouter]
    MCoord --> PlayCoord[PlaybackCoordinator]
    MCoord --> EditCoord[EditCoordinator]
    
    ViewRouter --> GalleryCtx[GalleryViewContext]
    ViewRouter --> EditCtx[EditViewContext]
    ViewRouter --> DetailCtx[DetailViewContext]
    
    EditCoord --> AdjustCtx[AdjustContext]
    EditCoord --> CropCtx[CropContext]
    
    NavCoord --> AlbumNav[AlbumNavigator]
    NavCoord --> LibNav[LibraryNavigator]
    
    PlayCoord --> VideoPlay[VideoPlayer]
    PlayCoord --> AudioPlay[AudioPlayer]
    
    style MCoord fill:#99ff99
    style NavCoord fill:#99ccff
    style ViewRouter fill:#99ccff
    style PlayCoord fill:#99ccff
    style EditCoord fill:#99ccff
```

---

## 7. 性能优化对比

### 缩略图缓存策略

#### 当前策略 (无限制缓存)

```mermaid
graph LR
    Request[请求缩略图]
    Request --> Check{已缓存?}
    Check -->|是| Return[返回]
    Check -->|否| Generate[生成<br/>FFmpeg 200ms]
    Generate --> Store[存储到内存]
    Store --> Return
    
    Memory[(内存缓存<br/>无限制<br/>10GB+)]
    Store --> Memory
    
    Note1[问题:<br/>- 内存泄漏<br/>- 无驱逐策略<br/>- 冷启动慢]
    
    style Memory fill:#ff9999
```

#### 目标策略 (多级缓存)

```mermaid
graph TB
    Request[请求缩略图]
    Request --> L1{L1: 内存LRU<br/>500项}
    
    L1 -->|命中 5ms| Return[返回]
    L1 -->|未命中| L2{L2: 磁盘缓存<br/>1GB}
    
    L2 -->|命中 20ms| ToL1[提升到L1]
    ToL1 --> Return
    
    L2 -->|未命中| L3[L3: 生成<br/>FFmpeg 100ms]
    L3 --> Optimize[优化参数]
    Optimize --> ToL2[存储到L2]
    ToL2 --> ToL1
    
    Prefetch[智能预取]
    Prefetch -.后台加载.-> L2
    
    Evict[自适应驱逐]
    Evict -.内存压力.-> L1
    
    style L1 fill:#99ff99
    style L2 fill:#99ccff
    style L3 fill:#ffff99
```

---

## 8. AssetListModel 重构

### 当前结构 (400+ LOC)

```mermaid
classDiagram
    class AssetListModel {
        -_facade: AppFacade
        -_cache_manager: AssetCacheManager
        -_state_manager: AssetListStateManager
        -_row_adapter: AssetRowAdapter
        -_controller: AssetListController
        -_thumb_size: QSize
        -_album_root: Path
        +__init__(facade)  // 80+ lines
        +bind(album)
        +rowCount()
        +data(index, role)
        +load_index()
        +_on_thumb_ready()
        +_apply_incremental_results()
        +handle_links_updated()
        +handle_asset_updated()
        // ... 30+ more methods
    }
    
    AssetListModel --|> QAbstractListModel
    AssetListModel --> AssetCacheManager
    AssetListModel --> AssetListStateManager
    AssetListModel --> AssetRowAdapter
    AssetListModel --> AssetListController
    
    Note for AssetListModel: "问题:\n- 混合5种职责\n- 400+ LOC\n- 难以测试\n- 高耦合"
```

### 目标结构 (分离职责)

```mermaid
classDiagram
    class AssetListViewModel {
        -_data_source: AssetDataSource
        -_cache: ThumbnailCacheService
        -_items: List~AssetDTO~
        +bind_query(query)
        +rowCount()
        +data(index, role)
    }
    
    class AssetDataSource {
        -_repo: IAssetRepository
        +load_page(query, page, size)
    }
    
    class ThumbnailCacheService {
        -_memory: LRUCache
        -_disk: DiskCache
        +get_or_generate(path, size)
        +prefetch(paths)
    }
    
    class AssetDTO {
        +id: int
        +path: Path
        +media_type: str
        +timestamp: datetime
    }
    
    AssetListViewModel --|> QAbstractListModel
    AssetListViewModel --> AssetDataSource
    AssetListViewModel --> ThumbnailCacheService
    AssetDataSource --> IAssetRepository
    AssetDataSource ..> AssetDTO
    
    Note for AssetListViewModel: "优势:\n- 单一职责\n- 150 LOC (减少62%)\n- 易于测试\n- 低耦合"
```

---

## 9. 时间线甘特图

```mermaid
gantt
    title 重构实施时间线 (5-6个月)
    dateFormat  YYYY-MM-DD
    section Phase 1
    DI容器实现           :p1a, 2026-01-20, 7d
    EventBus创建        :p1b, after p1a, 7d
    连接池添加          :p1c, after p1b, 7d
    
    section Phase 2
    定义仓储接口        :p2a, after p1c, 7d
    SQLite实现         :p2b, after p2a, 7d
    适配器创建          :p2c, after p2b, 7d
    渐进式替换          :p2d, after p2c, 7d
    
    section Phase 3
    提取Use Cases      :p3a, after p2d, 14d
    应用服务创建        :p3b, after p3a, 7d
    DTO引入            :p3c, after p3b, 7d
    
    section Phase 4
    ViewModels创建     :p4a, after p3c, 14d
    Coordinators简化   :p4b, after p4a, 14d
    Views重构          :p4c, after p4b, 14d
    
    section Phase 5
    并行扫描优化        :p5a, after p4c, 7d
    多级缓存实现        :p5b, after p5a, 7d
    异步加载优化        :p5c, after p5b, 7d
    
    section Phase 6
    集成测试           :p6a, after p5c, 7d
    文档更新           :p6b, after p5c, 7d
    性能测试           :p6c, after p6a, 7d
    
    section Milestones
    Alpha发布          :milestone, after p3c, 0d
    Beta发布           :milestone, after p4c, 0d
    正式发布           :milestone, after p6c, 0d
```

---

**文档说明:**
- 所有图表使用Mermaid语法，可在支持Mermaid的Markdown查看器中渲染
- 推荐工具: GitHub, GitLab, VS Code (Mermaid插件), Obsidian
- 完整架构分析请参阅: [ARCHITECTURE_ANALYSIS_AND_REFACTORING.md](./ARCHITECTURE_ANALYSIS_AND_REFACTORING.md)

---

**最后更新:** 2026-01-19  
**维护者:** Architecture Team

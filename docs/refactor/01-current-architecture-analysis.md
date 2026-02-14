# 01 — 现有架构分析

> 基于代码审计（2026-02）对 iPhoton 项目的架构现状进行全面诊断。

---

## 1. 项目总体架构全景

### 1.1 现有层次结构

```mermaid
graph TB
    subgraph "表现层 Presentation"
        GUI["GUI Layer<br/>PySide6 / Qt6"]
        CLI["CLI Layer<br/>Typer"]
    end

    subgraph "应用层 Application"
        Facade["AppFacade<br/>⚠️ 734行 God Object"]
        Coordinators["MainCoordinator<br/>⚠️ 535行"]
        UC["Use Cases<br/>仅3个"]
        AppSvc["Application Services<br/>AlbumService / AssetService"]
        GUISvc["GUI Services ⚠️<br/>4个重复服务"]
    end

    subgraph "领域层 Domain"
        DM["Domain Models<br/>Asset / Album / LiveGroup"]
        Repo["Repository Interfaces<br/>IAlbumRepo / IAssetRepo"]
        LegacyM["Legacy Models ⚠️<br/>models/album.py 重复"]
    end

    subgraph "基础设施层 Infrastructure"
        SQLite["SQLite Repository"]
        Cache["Index Store / Cache"]
        IO["IO / Scanner"]
        Meta["Metadata / ExifTool"]
        Thumb["Thumbnail Service"]
    end

    subgraph "核心算法层 Core"
        Pairing["Live Photo Pairing"]
        Adjust["Light / Color / BW"]
        Curves["Curve Resolver"]
        Filters["JIT Filters (Numba)"]
    end

    subgraph "外部模块 External"
        Maps["Map Widget<br/>OpenGL / Vector Tiles"]
    end

    GUI --> Facade
    GUI --> Coordinators
    CLI --> UC
    Facade --> |"直接调用 ⚠️"| UC
    Facade --> |"直接调用 ⚠️"| LegacyM
    Facade --> GUISvc
    Coordinators --> AppSvc
    AppSvc --> UC
    UC --> Repo
    Repo --> SQLite
    SQLite --> Cache
    IO --> Meta
    GUI --> Maps

    style Facade fill:#ff6b6b,color:#fff
    style GUISvc fill:#ff6b6b,color:#fff
    style LegacyM fill:#ff6b6b,color:#fff
    style Coordinators fill:#ffa94d,color:#fff
    style UC fill:#ffa94d,color:#fff
```

### 1.2 数据流概览

```mermaid
sequenceDiagram
    participant User as 用户
    participant MW as MainWindow
    participant MC as MainCoordinator
    participant Facade as AppFacade ⚠️
    participant UC as Use Cases
    participant Repo as SQLite Repo
    participant FS as FileSystem

    User->>MW: 打开相册
    MW->>MC: on_album_selected()
    MC->>Facade: open_album(path)
    Note over Facade: ⚠️ Facade 同时调用<br/>Legacy Model 和 Use Case
    Facade->>UC: OpenAlbumUseCase.execute()
    Facade->>Facade: Album.open() [Legacy ⚠️]
    UC->>Repo: find_album(path)
    Repo->>FS: 读取 manifest.json
    FS-->>Repo: album data
    Repo-->>UC: Album
    UC-->>Facade: Album
    Facade-->>MC: Signal: album_opened
    MC->>MW: 更新 UI
```

---

## 2. 核心问题诊断

### 2.1 问题全景 — 严重性矩阵

```mermaid
quadrantChart
    title 问题严重性 vs 修复难度
    x-axis "修复难度 低" --> "修复难度 高"
    y-axis "影响程度 低" --> "影响程度 高"
    quadrant-1 "优先处理"
    quadrant-2 "战略规划"
    quadrant-3 "顺手修复"
    quadrant-4 "择机处理"
    "God Object Facade": [0.7, 0.9]
    "双重模型": [0.3, 0.8]
    "EventBus 未启用": [0.4, 0.7]
    "DI 容器缺陷": [0.5, 0.65]
    "Use Case 不完整": [0.6, 0.75]
    "GUI Service 重复": [0.45, 0.6]
    "Settings 无事务": [0.2, 0.3]
    "测试覆盖不足": [0.65, 0.55]
```

### 2.2 问题一：God Object — AppFacade (734行)

**文件**: `src/iPhoto/gui/facade.py`

**症状**:
- 一个类承担了 15+ 个职责
- 继承 `QObject`，导致业务逻辑与 Qt 框架深度耦合
- 暴露 15+ 个 `Signal()` 实例，所有 GUI 组件都依赖它
- 直接调用 `backend.open_album()`，同时又使用 Use Case

**影响**:
- 任何业务逻辑变更都需要修改此文件
- 无法在非 Qt 环境下测试业务逻辑
- 信号连接形成隐式依赖图，难以追踪数据流

```mermaid
graph LR
    subgraph "当前 AppFacade 职责 ⚠️"
        F["AppFacade<br/>734行"]
        F --> R1["相册管理"]
        F --> R2["资产扫描"]
        F --> R3["Live Photo 配对"]
        F --> R4["缩略图服务"]
        F --> R5["元数据服务"]
        F --> R6["导入服务"]
        F --> R7["移动服务"]
        F --> R8["信号路由"]
        F --> R9["后端桥接"]
        F --> R10["错误处理"]
        F --> R11["线程调度"]
        F --> R12["缓存协调"]
        F --> R13["Library 更新"]
        F --> R14["设置管理"]
        F --> R15["事件转发"]
    end

    style F fill:#ff6b6b,color:#fff
```

### 2.3 问题二：双重模型并存

**冲突来源**:

| 文件路径 | 类型 | 状态 |
|---------|------|------|
| `src/iPhoto/domain/models/core.py` | 新 Domain Model (dataclass) | ✅ 纯净，无框架依赖 |
| `src/iPhoto/models/album.py` (117行) | Legacy Model (带 manifest 读写) | ⚠️ 仍在使用 |
| `src/iPhoto/models/types.py` | Legacy 类型定义 | ⚠️ 与 domain 重复 |

**问题**:
- `facade.py` 中同时引用两套模型
- `Album.open()` (legacy) 与 `OpenAlbumUseCase.execute()` (new) 并行调用
- 数据在两套模型之间转换时存在不一致风险

```mermaid
graph TB
    subgraph "当前：双重模型 ⚠️"
        Legacy["models/album.py<br/>Album (Legacy)<br/>- open() 方法<br/>- manifest 读写<br/>- 直接文件操作"]
        Domain["domain/models/core.py<br/>Album (Domain)<br/>- 纯 dataclass<br/>- 无副作用<br/>- 值对象"]
        Facade2["AppFacade"]
        Facade2 --> Legacy
        Facade2 --> Domain
    end

    style Legacy fill:#ff6b6b,color:#fff
    style Domain fill:#51cf66,color:#fff
    style Facade2 fill:#ffa94d,color:#fff
```

### 2.4 问题三：EventBus 创建但未使用

**文件**: `src/iPhoto/events/bus.py` (~50行)

**现状**:
- EventBus 已实现（`ThreadPoolExecutor` + 发布/订阅）
- `MainCoordinator` 中已解析 EventBus（line 77）
- **但从未实际发布或订阅任何事件**
- 所有跨层通信仍依赖 Qt Signal

**问题**:
- Qt Signal 将 GUI 框架渗透到 Service 层
- 非 GUI 环境（CLI、测试）无法使用信号机制
- 事件追踪困难，没有统一的事件日志

```mermaid
graph TB
    subgraph "当前：Qt Signal 耦合 ⚠️"
        S1["AlbumMetadataService<br/>QObject + Signal"]
        S2["LibraryUpdateService<br/>QObject + Signal"]
        S3["AssetImportService<br/>QObject + Signal"]
        S4["AssetMoveService<br/>QObject + Signal"]
        EB["EventBus<br/>⚠️ 已创建但闲置"]

        S1 -->|"Qt Signal"| MC2["MainCoordinator"]
        S2 -->|"Qt Signal"| MC2
        S3 -->|"Qt Signal"| MC2
        S4 -->|"Qt Signal"| MC2
        EB -.->|"未连接"| MC2
    end

    style EB fill:#868e96,color:#fff
    style S1 fill:#ff6b6b,color:#fff
    style S2 fill:#ff6b6b,color:#fff
    style S3 fill:#ff6b6b,color:#fff
    style S4 fill:#ff6b6b,color:#fff
```

### 2.5 问题四：DI 容器缺陷

**文件**: `src/iPhoto/di/container.py` (~44行)

**已知缺陷**:
1. **无循环依赖检测** — A→B→A 将导致无限递归
2. **Lambda 闭包陷阱** — `args`/`kwargs` 在注册时捕获，非解析时
3. **无惰性初始化** — 所有依赖在解析时立即创建
4. **无生命周期管理** — 没有 Singleton / Transient / Scoped 区分
5. **无构造函数签名保留** — 工厂模式丢失类型信息

**影响**:
- `MainCoordinator` 手动解析服务而非注入（lines 76-82）
- 部分服务仍使用 `@property` getter 而非构造函数注入
- Legacy Facade 完全绕过 DI

### 2.6 问题五：Use Case 覆盖不足

**已实现** (3个):

| Use Case | 文件 | 状态 |
|----------|------|------|
| `OpenAlbumUseCase` | `application/use_cases/open_album.py` | ✅ |
| `ScanAlbumUseCase` | `application/use_cases/scan_album.py` | ✅ |
| `PairLivePhotosUseCase` | `application/use_cases/pair_live_photos.py` | ✅ |

**缺失** (至少需要):

| 业务场景 | 当前处理方式 |
|---------|-------------|
| 资产导入 | Facade 直接调用 |
| 资产移动 | GUI Service (AssetMoveService) |
| 缩略图生成 | GUI Service + Coordinator 直连 |
| 元数据更新 | Facade 直接调用 |
| 相册创建/删除 | Legacy Model 方法 |
| 回收站管理 | LibraryManager 直接处理 |
| 地理编码聚合 | LibraryManager 直接处理 |
| 文件系统监控 | LibraryManager 直接处理 |

### 2.7 问题六：GUI 层大文件

**超过 500 行的文件**:

| 文件 | 行数 | 职责混杂 |
|------|------|---------|
| `gui/facade.py` | 734 | 15+ 职责 |
| `gui/coordinators/main_coordinator.py` | 535 | UI编排 + DI + Service连线 |
| `gui/ui/widgets/gl_image_viewer/widget.py` | 686 | 缩放/平移/裁剪/调整 |
| `gui/ui/widgets/edit_sidebar.py` | 1052 | 300行 `__init__` + 40+ 信号 |
| `gui/ui/widgets/edit_curve_section.py` | 1165 | 数学算法 + UI |
| `infrastructure/services/thumbnail_loader.py` | 963 | 缓存/渲染/调度 |

### 2.8 问题七：GUI Service 与 Application Service 重复

```mermaid
graph TB
    subgraph "GUI Services (Qt耦合)"
        GS1["AlbumMetadataService<br/>gui/services/"]
        GS2["LibraryUpdateService<br/>gui/services/"]
        GS3["AssetImportService<br/>gui/services/"]
        GS4["AssetMoveService<br/>gui/services/"]
    end

    subgraph "Application Services (纯Python)"
        AS1["AlbumService<br/>application/services/"]
        AS2["AssetService<br/>application/services/"]
    end

    GS1 -.->|"功能重叠"| AS1
    GS3 -.->|"功能重叠"| AS2
    GS4 -.->|"功能重叠"| AS2

    style GS1 fill:#ff6b6b,color:#fff
    style GS2 fill:#ff6b6b,color:#fff
    style GS3 fill:#ff6b6b,color:#fff
    style GS4 fill:#ff6b6b,color:#fff
    style AS1 fill:#51cf66,color:#fff
    style AS2 fill:#51cf66,color:#fff
```

---

## 3. 架构债务总结

### 3.1 量化评估

| 指标 | 当前值 | 行业基准 | 差距 |
|------|--------|---------|------|
| 最大文件行数 | 1,165行 | ≤300行 | 🔴 3.9x |
| God Object 数量 | 2 (Facade+Coordinator) | 0 | 🔴 |
| 重复模型 | 2套 (models/ + domain/) | 1套 | 🟠 |
| Use Case 覆盖率 | 3/11 (27%) | ≥90% | 🔴 |
| EventBus 使用率 | 0% (已创建未使用) | 100% | 🔴 |
| DI 覆盖率 | ~40% (部分手动) | ≥95% | 🟠 |
| Qt 渗透层数 | 3 (GUI+Service+Facade) | 1 (仅GUI) | 🔴 |
| 测试覆盖率 (集成) | ~0% | ≥60% | 🔴 |

### 3.2 技术债务风险评级

```mermaid
pie title 技术债务分布
    "God Object / 职责混杂" : 30
    "双重架构并存" : 25
    "Qt 框架渗透" : 20
    "测试覆盖不足" : 15
    "性能瓶颈" : 10
```

---

## 4. 积极方面

尽管存在上述问题，项目已具备良好的重构基础：

1. ✅ **Domain 层已建立** — `domain/models/core.py` 是纯净的值对象
2. ✅ **Repository 接口已定义** — `IAlbumRepository`, `IAssetRepository`
3. ✅ **3个 Use Case 已实现** — 可作为后续 Use Case 的模板
4. ✅ **DI 容器已存在** — 虽不完善但框架已搭好
5. ✅ **EventBus 已实现** — 只需接入使用
6. ✅ **测试基础设施完善** — pytest + pytest-qt + 123个测试文件
7. ✅ **代码质量工具已配置** — ruff + black + mypy
8. ✅ **文档基础良好** — README、CONTRIBUTING 已建立

> **结论**：项目处于架构转型的中间阶段。旧架构和新架构并存是过渡期的正常现象，但需要有明确的迁移计划来避免长期维持双轨制的成本。

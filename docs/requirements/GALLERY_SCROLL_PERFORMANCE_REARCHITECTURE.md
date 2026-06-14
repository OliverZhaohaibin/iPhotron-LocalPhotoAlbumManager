# Windows/Linux 大型图库 Gallery 滚动性能重构

> 状态：实施中
> 文档版本：1.2
> 创建日期：2026-06-11
> 适用架构：vNext / Qt Widgets Gallery
> 主要验收平台：Windows、Linux
> 性能回归基线：macOS

## 1. 执行摘要

当前 Gallery 已具备基于 SQLite 的窗口化加载能力，在 macOS 上大型图库的滚动体验基本可接受；但 Windows 和 Linux 在高速滚动时仍会出现明显卡顿，实际 UI 位移无法及时追随滚轮输入，表现为滚动速度被 UI 强行拉低。

该问题不是单一 Python 热点，而是滚动、绘制、数据加载和缩略图加载之间缺少明确的异步边界。当前真实调用链允许 Qt GUI 线程在滚动和绘制过程中同步执行 SQLite 查询、文件系统访问、图片解码、整窗 DTO 构造以及强制重绘。任何一个操作超过单帧预算，都会阻塞后续滚轮事件；多个操作叠加时，输入积压会直接造成“滚轮已经停止，界面仍在缓慢追赶”的体验。

本方案将 Windows 和 Linux 同时定义为主要优化与验收平台，优先级相同：

- **Windows**：重点消除 GUI 线程上的 L2 缩略图文件访问和解码，降低 NTFS、实时安全扫描、高 DPI 绘制及频繁刷新放大的延迟。
- **Linux**：重点移除滚动路径中的同步布局和 `viewport().repaint()`，改为每帧最多一次的异步合并刷新。
- **跨平台**：建立 GUI 线程零 I/O 契约，将 Gallery 窗口查询与缩略图管线重构为 generation-aware 的异步调度系统。
- **macOS**：作为当前体验基线，实施过程中不得发生可感知性能回退。

本方案不以最小改动为目标，而以清晰的线程边界、可验证的性能契约和较低的长期技术债为目标。只有在完成 Python/Qt 架构重构后，性能剖析仍证明 Python/PySide 调度或 Qt Widgets 光栅绘制是主要瓶颈时，才进入 Qt Quick 或 C++ renderer 的 Native 门禁评估。

本次实施以该文档为持续架构契约，并同步修改生产代码、测试与性能门禁。

## 2. 决策结论

### 2.1 核心决策

1. Windows 与 Linux 是本次优化的同等主要目标平台，必须分别测试、分别出具结果、分别通过验收。
2. `paintEvent()`、delegate `paint()`、model `data()` 和 `scrollContentsBy()` 必须成为纯内存、可预测耗时的 GUI 线程路径。
3. Gallery 固定使用 full -> micro -> 纯色 placeholder 的绘制优先级；高速滚动以 micro 为主要渲染层，纯色仅用于真实缺失、损坏或远距离跳转后的极短兜底。
4. Gallery collection window 查询必须迁移到专用异步加载器，并支持 generation、优先级和过期结果丢弃。
5. 缩略图 API 必须拆分为内存只读查询与异步批量请求；所有磁盘访问和图片解码均在 worker 中完成。
6. Linux 必须移除滚动路径中的同步布局和 `repaint()`；Windows 必须避免 L2 单文件同步访问和高 DPI 下的重复完整绘制。
7. 保留 Windows、Linux 和 macOS 上的自定义顶部按钮栏与圆角窗口，不以退化窗口外观换取滚动性能。
8. 不使用 C/C++ 掩盖同步 I/O、查询投影过重或任务调度缺失等架构问题。

### 2.2 为什么该方案会修复 Windows 性能

该方案并非以 Linux 专项修复为主、顺带覆盖 Windows。Linux 的同步 `repaint()` 是一个明确且直接的滚动阻塞点，但 Windows 同样存在更广泛的跨平台阻塞链路：

- model 绘制请求可能同步触发 SQLite window 加载；
- L2 缩略图命中时，GUI 线程仍同步执行文件检查和 `QPixmap` 解码；
- collection window 使用宽投影并同步复制 metadata、解码 micro thumbnail；
- 快速滚动时旧 viewport 的查询和缩略图任务未被有效取消；
- thumbnail-ready 和大范围 `dataChanged` 可能造成高 DPI 下重复绘制。

Phase 1 至 Phase 3 会直接移除这些 Windows 热点。Phase 0 和 Phase 4 还要求使用 **Windows packaged runtime** 独立采集和验收，确保优化结果覆盖真实发布环境中的 NTFS、实时安全扫描、DWM 和 DPI 缩放成本。因此，Windows 性能提升是本方案的主要交付目标，不是推断性收益。

## 3. 背景与问题定义

### 3.1 用户可见问题

在 Windows 和 Linux 的大型图库 Gallery view 中高速滚动时：

- 滚轮输入频率高于 UI 实际滚动更新频率；
- scrollbar 位移落后于累计滚轮输入；
- 图片和 placeholder 更新会打断滚动连续性；
- 快速滚动后，UI 仍处理已经离开 viewport 的旧区域任务；
- 严重时出现滚动速度被限制、短暂冻结或视觉撕裂。

### 3.2 当前真实事件链

当前风险链路可概括为：

```text
QWheelEvent
  -> QListView / scrollbar 更新
  -> scrollContentsBy()
  -> layout / repaint / paintEvent()
  -> delegate.paint()
  -> GalleryListModelAdapter.data()
  -> GalleryCollectionStore.asset_at()
  -> ensure_row_loaded()
  -> SQLite window query
  -> DTO/materialization/micro-thumbnail decode
  -> ThumbnailCacheService.get_thumbnail()
  -> disk exists/read + QPixmap decode
  -> dataChanged / thumbnail-ready
  -> additional paint
```

只要链路中的同步操作占用 GUI 线程，后续 wheel、scrollbar 和 paint 事件就无法及时处理。对用户而言，这不是单纯的帧率下降，而是输入与界面位移失去同步。

### 3.3 大型图库放大效应

图库规模增大后，以下因素会同时放大：

- window miss 和窗口切换频率增加；
- 缩略图内存命中率下降，L2 文件访问增加；
- 快速滚动跨越更多 generation；
- broad `dataChanged` 覆盖更多未真正变化的 tile；
- 旧任务积压更容易占满 worker 和 GUI 信号队列；
- 真实图库 metadata 和文件布局比 synthetic 数据更复杂。

因此，必须优化真实事件链和调度模型，而不能只优化单次 SQL 查询或单个 delegate paint。

## 4. 目标、非目标与约束

### 4.1 目标

- Windows/Linux 高速滚动时，UI 位移及时追随滚轮输入。
- GUI 线程滚动与绘制路径不执行数据库查询、文件访问或图片解码。
- 在 100k 和 1M synthetic library 以及真实 L2 thumbnail cache 下满足性能 SLO。
- 快速滚动时，过期 window 查询和缩略图任务能够取消、降级或丢弃。
- Windows packaged build、Linux XCB 和 Linux Wayland 分别通过验收。
- 保持 macOS 当前滚动体验，不引入可感知回退。
- 形成可持续维护的性能观测、回归门禁和线程契约。

### 4.2 非目标

- 本方案不重构整个媒体索引、扫描或详情页架构。
- 本方案不要求首阶段迁移整个 UI 到 Qt Quick。
- 本方案不以提升缩略图视觉质量为目标。
- 本方案不通过移除自定义顶部按钮栏、圆角窗口或其他既有外观能力换取性能。
- 本方案不在未定位瓶颈前直接引入 C/C++。
- 本次交付不包含生产代码实现。

### 4.3 约束

- 主线继续使用当前 vNext 架构和 Qt Widgets Gallery。
- 保留现有 selection、详情打开、播放、收藏、拖放及扫描可见更新行为。
- SQL-first/windowed collection 方向保留，但需要重构其线程边界和数据投影。
- 任何兼容路径都不得重新允许 paint/model data 同步加载。

## 5. 根因分析

### 5.1 跨平台共同根因

| 根因 | 当前行为 | 影响 | 严重度 | 置信度 |
|---|---|---|---|---|
| 绘制路径同步加载 SQLite window | `GalleryListModelAdapter.data()` 在 row 未加载时可调用 `ensure_row_loaded()` | wheel/paint 被查询和整窗构造阻塞 | 阻断级 | 高 |
| GUI 线程同步读取和解码 L2 缩略图 | `ThumbnailCacheService.get_thumbnail()` 检查磁盘并构造 `QPixmap` | L2 命中仍可能造成长帧，Windows 风险更高 | 阻断级 | 高 |
| Gallery 查询投影过重 | collection window 使用 `SELECT *` | 传输和复制非绘制必需字段 | 高 | 高 |
| 整窗同步 materialization | DTO 转换复制 metadata，并解码整窗 micro thumbnail | window miss 成本远高于必要值 | 高 | 高 |
| viewport 外额外行绘制无有效缓存收益 | `gallery_grid_view.py` 在正常 paint 后手动绘制上下额外行 | 增加 delegate、model data 和 thumbnail 请求 | 中 | 高 |
| 取消能力未接入 viewport 调度 | 缩略图取消接口存在，但未形成 generation-aware 调度 | 快速滚动继续处理旧区域 | 高 | 高 |
| 更新信号范围过宽 | 大范围 `dataChanged` 和逐个 thumbnail-ready 信号 | 造成重复绘制和 GUI 事件队列压力 | 高 | 高 |
| 内存缓存预算不准确 | 使用固定 item 数近似，而非真实字节预算 | 大图/DPI 变化时命中率和内存不可控 | 中 | 高 |
| 性能测试链路不真实 | 未覆盖 wheel 到 paint、SQLite、L2 的完整链路 | 当前门禁无法发现用户可见卡顿 | 阻断级 | 高 |

### 5.2 Linux 专项根因

`src/iPhoto/gui/ui/widgets/asset_grid.py` 的 Linux 滚动路径会在每次 `scrollContentsBy()` 中同步执行：

- `executeDelayedItemsLayout()`；
- `viewport().repaint()`。

`repaint()` 会立即执行绘制，而不是合并到事件循环后续帧。高速滚动时，每个输入事件都可能被迫完成布局和绘制，直接限制 scrollbar 更新速度。现有 `tests/test_asset_grid_scroll.py` 还固化了该行为，实施时需要同步重写测试契约。

Linux 修复原则：

- 滚动路径禁止同步 layout 和 `repaint()`；
- 使用事件循环驱动的 `update()`；
- 同一帧内多次滚动只保留最新位置，最多提交一次 viewport 更新；
- XCB 与 Wayland 分别验证；
- 如仍存在撕裂，可隔离 Gallery 内部 opaque 绘制表面，但顶层圆角透明窗口和自定义顶部按钮栏必须保留。

### 5.3 Windows 专项根因

Windows 没有 Linux 同一处显式同步 `repaint()` 问题，但会显著放大跨平台共同根因：

- 单文件 L2 缩略图访问可能受 NTFS 元数据访问和实时安全扫描影响；
- GUI 线程 `QPixmap` 解码会阻塞 DWM 合成前的应用事件处理；
- 高 DPI 下 tile 实际像素面积增加，重复完整绘制成本更高；
- packaged runtime 与开发环境的路径、缓存布局和安全扫描行为可能不同；
- thumbnail-ready 与 broad `dataChanged` 叠加时，容易形成绘制反压。

Windows 修复原则：

- L2 文件检查、读取和 JPEG 解码完全移出 GUI 线程；
- worker 返回 `QImage`，GUI 线程仅做必要的 `QImage -> QPixmap` 转换；
- 对 thumbnail-ready 与 window-ready 更新按帧合并；
- 验证 packaged runtime 的 L2 文件布局和批量访问行为；
- 保留 DWM、自定义顶部按钮栏和圆角窗口。

### 5.4 macOS 基线

macOS 当前体验可接受，不代表同步链路合理。实施过程中仍需在 macOS 跑相同 benchmark，以确认：

- 平均与 P95 帧间隔不回退；
- 首屏和普通滚动不回退；
- selection、拖放和窗口外观不回退；
- 新异步调度不会引入缩略图闪烁或错误复用。

macOS 数据不得替代 Windows/Linux 验收结果。

## 6. 已有测试与观测盲区

### 6.1 当前测试结论

现有滚动和大图库性能测试能够验证部分窗口大小、materialization 上限和辅助行为，但没有驱动真实 Qt 事件链：

```text
QWheelEvent
  -> scrollbar
  -> scrollContentsBy()
  -> paintEvent()
  -> model.data()
  -> SQLite/L2 cache
```

因此，即使当前测试通过，也不能证明真实高速滚动不会阻塞。

### 6.2 本地诊断数据

在 macOS 开发环境中的一次诊断性测量中：

- 300 个 L2 `QPixmap` 加载约为 `173.6ms`；
- 300 个 micro thumbnail 解码约为 `34.8ms`。

该数据不是 Windows/Linux benchmark，也不能直接用于跨平台验收；但它足以证明同步批量图片工作可轻易超过单帧预算。Windows 上的文件系统和安全扫描因素可能进一步放大 L2 同步访问延迟。

### 6.3 必须新增的可观测项

每次 benchmark 至少记录：

- wheel event 时间戳、delta 和累计目标位移；
- scrollbar 实际位移及追平目标位移所需时间；
- `scrollContentsBy()` 耗时；
- paint 开始、结束时间和绘制区域；
- GUI stall 次数与持续时间；
- GUI 线程 SQLite 查询次数及耗时；
- GUI 线程文件访问次数及耗时；
- GUI 线程图片解码次数及耗时；
- window 请求、开始、完成、丢弃和取消次数；
- thumbnail memory hit、L2 hit、decode、丢弃和取消次数；
- 每帧 `dataChanged`、thumbnail-ready 和 viewport update 次数；
- 当前 platform、backend、DPI、packaged/development runtime 标识。

性能日志必须可采样或聚合，禁止在热路径逐 tile 同步写日志而污染结果。

## 7. 性能 SLO 与强制契约

### 7.1 Windows/Linux 验收 SLO

Windows 和 Linux 必须分别满足以下指标：

| 指标 | 目标 |
|---|---|
| 高速滚动结束后输入追平 | scrollbar 在 `100ms` 内追平累计滚轮输入 |
| 连续滚动帧间隔 | P95 不超过 `24ms` |
| `scrollContentsBy()` | P95 不超过 `2ms` |
| GUI 线程同步 SQLite 查询 | `0` 次 |
| GUI 线程同步文件访问 | `0` 次 |
| GUI 线程图片解码 | `0` 次 |
| 滚动路径同步 `repaint()` | `0` 次 |
| 过期 window/thumbnail 结果 | 被取消、降级或丢弃，不更新错误 viewport |
| 快速滚动视觉正确性 | 100 次快速滚动无错误缩略图、严重撕裂或长期 checkerboard |
| 大型图库 query P95 | 延续现有要求，不超过 `80ms` |

`24ms` P95 是最低验收门槛，不是最终理想值。实现应尽量接近显示器刷新节奏，并避免通过降低 scrollbar 位移速度来“稳定帧率”。

### 7.2 macOS 回归门禁

- 当前基线场景的滚动 P95 不得显著回退；
- 不得增加 GUI 线程 I/O；
- 不得出现新的视觉闪烁、错误缩略图或交互回归；
- 不以牺牲 macOS 体验来满足其他平台指标。

### 7.3 GUI 线程零 I/O 契约

以下调用路径只能访问内存中的稳定快照，且必须在有界时间内返回：

- `paintEvent()`；
- delegate `paint()`；
- model `data()`；
- `scrollContentsBy()`；
- scrollbar value-change 的直接回调；
- thumbnail-ready/window-ready 的 GUI 应用阶段。

明确禁止：

- SQLite 查询或等待数据库 future；
- `Path.exists()`、`stat()`、`open()` 等文件系统访问；
- JPEG/PNG/WebP/micro thumbnail 解码；
- 阻塞等待 worker；
- 同步 `repaint()`；
- 为 viewport 外区域同步构造 DTO 或 pixmap。

违反该契约应在测试环境中直接失败，而不是只记录警告。

## 8. 目标架构

### 8.1 总体数据流

```text
Wheel / Scrollbar
  -> update logical scroll position immediately
  -> calculate visible/hot/warm ranges
  -> paint full or micro from in-memory snapshots
  -> use a solid placeholder only when both image layers are unavailable
  -> submit/coalesce generation-aware requests

Gallery Window Loader (single worker)
  -> explicit lightweight SQL projection
  -> build GalleryTileDTO without GUI image objects
  -> return WindowResult(generation, range, tiles)
  -> GUI applies only current/relevant results

Thumbnail Workers
  -> batch memory-miss requests
  -> L2 file check/read/decode
  -> return QImage results
  -> GUI converts current visible/hot images to QPixmap
  -> coalesce local dataChanged/update once per frame
```

### 8.2 轻量 `GalleryTileDTO`

Gallery 绘制不应复用携带完整 metadata 和解码图片对象的宽 DTO。新增轻量、typed、与 Qt GUI 对象解耦的 `GalleryTileDTO`，只包含 tile 绘制、badge 和基本交互所需字段。

建议字段范围：

```python
@dataclass(frozen=True, slots=True)
class GalleryTileDTO:
    asset_id: int
    media_type: str
    display_name: str
    aspect_ratio: float | None
    favorite: bool
    rating: int | None
    duration_ms: int | None
    thumbnail_key: str | None
    orientation: int | None
    availability_flags: int
```

设计约束：

- `GalleryTileRecord` 不包含 `QImage`、`QPixmap` 或原始 BLOB；
- 不复制完整 `metadata` 字典；
- SQL 投影包含 `micro_thumbnail` BLOB，并在 worker 解码后丢弃原始 BLOB；
- window result 将 record 与已解码、线程安全的 `QImage` micro 一同发布；不得在 worker 创建 `QPixmap`；
- 字段应根据实际 delegate 使用情况审计后确定；
- 详情页需要的完整信息通过独立显式请求获取。

### 8.3 显式 Gallery SQL 投影

collection window 查询必须使用明确列清单，不再使用 `SELECT *`。查询投影只覆盖：

- 排序与稳定定位所需列；
- `GalleryTileDTO` 所需列；
- Gallery 过滤/分组显示必需列；
- `micro_thumbnail` 与 full thumbnail key。

禁止为了兼容通用 DTO 转换器而读取完整 metadata。micro thumbnail 是 Gallery 轻量投影的强制字段，并必须在 window worker 解码。Gallery 投影应有独立 row mapper，并通过查询计划和 benchmark 验证索引使用情况。

### 8.4 异步窗口加载器

新增专用于 Gallery 的单线程异步窗口加载器。SQLite 查询保持串行，避免数据库并发争用，但不占用 GUI 线程。

建议协议：

```python
@dataclass(frozen=True, slots=True)
class GalleryWindowRequest:
    generation: int
    start_row: int
    end_row: int
    priority: int
    collection_revision: int

@dataclass(frozen=True, slots=True)
class GalleryWindowResult:
    generation: int
    start_row: int
    tiles: tuple[GalleryTileDTO, ...]
    collection_revision: int
```

调度规则：

- viewport 或 collection 改变时递增 generation；
- 同一 generation 的重叠请求合并；
- 尚未开始的旧 generation 请求直接取消；
- 已开始但无法中断的查询允许完成，但结果在 GUI 应用前丢弃；
- visible range 优先于 hot range，hot range 优先于 warm range；
- 只缓存有界数量的窗口，并按真实使用情况淘汰；
- GUI 只应用 collection revision 与当前状态一致的结果。

### 8.5 model 与 paint 的三层行为

`GalleryListModelAdapter.data()` 必须改为纯内存查询：

- row 已加载：返回稳定的 tile snapshot，优先 full，其次 micro；
- row 未加载：立即返回稳定 placeholder，同时调度新 viewport 的 micro window；
- 不调用 `ensure_row_loaded()`；
- 不触发 SQLite、文件访问或图片解码；
- 可通过轻量、去重的缺失提示通知调度器，但通知不得阻塞。

micro 与 full 使用完全相同的裁剪和目标矩形，full 到达后原子替换，不做逐 tile 淡入。纯色 placeholder 必须保持布局稳定，且只允许在两层图片都不可用时出现。

### 8.6 单一 tile snapshot role

当前 delegate 若通过多个 role 分别请求 title、badge、thumbnail、状态等，会重复进入 Python/PySide model dispatch。建议提供单一只读 tile snapshot role，使一次 delegate paint 获取完整绘制快照：

```python
@dataclass(frozen=True, slots=True)
class GalleryTileSnapshot:
    tile: GalleryTileDTO | None
    pixmap: QPixmap | None
    loading_state: int
```

该 snapshot 仅由 GUI 线程内存缓存构造，不承担加载职责。现有 role 可在迁移期保留，但新 delegate 应优先使用 snapshot role。

### 8.7 异步缩略图 API

将当前“可能访问内存、也可能访问磁盘”的同步 API 拆分为两个职责明确的接口：

```python
pixmap = thumbnail_cache.peek(key, target_size)
thumbnail_cache.request_many(requests, generation)
```

`peek()`：

- 只查询 GUI 线程可访问的内存 pixmap cache；
- 保证不触发文件系统访问、解码或 worker 等待；
- miss 时立即返回 `None`。

`request_many()` / `reconcile_demand()`：

- 接收去重后的批量请求；
- 每个 viewport generation 使用 `reconcile_demand()` 原子替换 visible/hot 排队需求；
- 在 worker 中执行 L2 文件检查、读取和解码；
- worker 返回 `QImage`，不在 worker 创建 `QPixmap`；
- GUI 线程只对仍相关的结果执行 `QImage -> QPixmap`；
- 结果按帧合并后触发局部更新。

### 8.8 缩略图调度范围

根据当前 viewport、滚动方向和速度划分请求优先级：

| 范围 | 定义 | 策略 |
|---|---|---|
| visible | 当前可见 tile | 最高优先级，尽快请求与应用 |
| full prefetch | slow/settled 时可见区上下各两个 viewport | 独立单 worker、仅 L2、best effort |
| warm | 更远但可能即将进入 viewport 的 tile | 低优先级，仅在 worker 空闲时执行 |
| stale | 已离开相关 generation 的 tile | 取消或丢弃 |

调度使用 `GalleryViewportDemand` 描述 generation、visible、hot、warm、方向、
EWMA `screens_per_second` 与 `settled / slow / medium / fast` 阶段。速度只影响资源
范围，不得改变滚轮输入距离。

- 传统鼠标滚轮每个 notch 使用恒定增益并遵循系统 `wheelScrollLines()`；禁止连续
  notch 自适应加速。触控板 `pixelDelta` 保持 1:1。
- 滚动速度使用约 `120ms` EWMA；低于 `2` 屏/秒为 slow，`2–8` 屏/秒为
  medium，高于 `8` 屏/秒为 fast；约 `120ms` 无输入后标记 settled。
- 所有阶段持续请求 visible full；slow/settled 在可见区上下各预取两屏 full，
  按上方最近、下方最近交替向远处流式推进；medium/fast 仅请求 visible full。
- visible full 固定使用两个 foreground worker。full prefetch 使用独立单 worker，
  仅在 foreground 无待处理工作时启动，且只读取已有 L2 thumbnail，不允许从源图生成。
- 任意 visible miss 到达时取消正在运行的 prefetch；最新 viewport generation 原子替换
  未开始的 prefetch，过期结果不得转换为 `QPixmap` 或触发 `dataChanged`。
- visible full hard-pin；full prefetch 仅 soft-protect，内存压力下从最远预取项开始淘汰。
- micro warm-up 使用 `256` 张分块，visible -> hot -> warm 顺序提交。
- slow/settled warm 至少 `300` 张或 `6` 屏，medium 至少 `300` 张或 `24` 屏，
  fast 扩展至最多 `2000` 张。
- 有方向时约 `75%` warm 容量分配在前方，settled 时居中分配。
- micro 稀疏 LRU 每个活动 Gallery 最多保留 `2000` 张，显式交互 pin 行可单独保留。

旧 generation 的未开始请求会取消；已经完成的 micro window 若仍落在当前 warm
范围且 collection revision 一致，可以合并复用。full 结果若不再属于当前
visible/hot demand，则在 `QImage -> QPixmap` 前丢弃。

### 8.9 generation 与取消

window 和 thumbnail 调度共享 viewport generation，但保留各自任务状态。每次以下事件发生时递增 generation：

- 大幅 scrollbar 位移；
- collection、filter 或 sort 改变；
- viewport 尺寸或列数发生显著变化；
- tile size/DPI 改变导致目标缩略图尺寸变化。

取消分为三类：

- **排队前去重**：相同 key、尺寸和 generation 不重复提交；
- **未开始取消**：从队列移除 stale 请求；
- **完成后丢弃**：无法中断的 I/O 或解码完成后，不应用 stale 结果。

已有 `cancel_pending_except()` 能力应接入 viewport 调度，不再作为孤立接口存在。

### 8.10 真实字节预算 LRU

缩略图内存缓存必须按真实或保守估算的字节数管理，而不是固定 item 数量：

```text
estimated_bytes = bytes_per_line * image_height
```

要求：

- Windows/Linux/macOS 可配置独立默认预算；
- DPI 或 tile size 改变时预算行为可预测；
- visible pixmap 可短期 pin，避免滚动中被立即淘汰；
- 淘汰过程不得在单帧执行大量析构；
- 记录 hit rate、bytes、eviction 次数和峰值。

### 8.11 更新合并与局部重绘

window-ready 和 thumbnail-ready 不得逐项触发无限制 GUI 更新。新增 frame-level update coordinator：

- 同一事件循环帧内合并变化 row；
- 将连续 row 合并为最小范围；
- 为 `dataChanged` 指定准确 roles；
- 只更新实际受影响 viewport 区域；
- 每帧最多提交一次 viewport `update()`；
- stale 或 viewport 外更新可延后或跳过；
- 避免完整 viewport 重绘，除非布局或主题确实发生变化。

### 8.12 删除 viewport 外额外行绘制

删除 `gallery_grid_view.py` 中对 viewport 上下额外行的手动绘制。`QPainter(viewport)` 受 viewport clip 限制，该逻辑不能形成可靠的 retained offscreen cache，却会增加：

- delegate paint 调用；
- model role 请求；
- thumbnail 请求；
- Python/PySide dispatch。

预取应由显式 hot/warm 调度完成，不应通过无效绘制间接触发。

## 9. 平台专项绘制方案

### 9.1 Windows

Windows 实施与验收必须覆盖真实 packaged runtime。

实施重点：

- 保留 DWM、自定义顶部按钮栏和圆角窗口；
- 合并滚动帧与 thumbnail-ready 刷新，避免高 DPI 下重复完整绘制；
- L2 文件访问、读取、校验和解码全部在 worker；
- 验证 L2 cache 是否因大量小文件导致元数据访问瓶颈；
- 对真实安全扫描环境采集长尾延迟；
- 评估批量请求是否改善访问局部性；
- 记录不同 DPI 比例下的 paint area 与帧间隔。

L2 文件布局优化是 Phase 3 后的可选项。只有 profile 证明大量小文件和目录查找仍是主要 worker 瓶颈时，才考虑分片目录、容器化 cache 或索引文件；该优化不得恢复 GUI 线程文件访问。

### 9.2 Linux

实施重点：

- 保留自定义顶部按钮栏和圆角顶层窗口；
- 移除 `scrollContentsBy()` 中的 `executeDelayedItemsLayout()` 和 `viewport().repaint()`；
- 滚动立即更新逻辑位置，并请求异步 `viewport.update()`；
- 同一帧内只绘制最新 scrollbar 位置；
- XCB 与 Wayland 分别采集；
- 对合成器、DPI 和透明窗口组合进行视觉验证。

如果移除同步 repaint 后仍存在撕裂，允许将 Gallery 内部 viewport 隔离为 opaque native child surface，以减少透明合成和背景传播成本。但不得将整个顶层窗口退化为方角或移除自定义顶部按钮栏。

### 9.3 macOS

macOS 使用相同异步数据和缩略图架构，但不引入针对 Windows/Linux 的不必要平台分支。所有关键 benchmark 在 macOS 运行，作为 no-regression gate。

## 10. 分阶段实施计划

### Phase 0：Windows/Linux 真实性能门禁

目标：先建立可复现、可归因的真实滚动 benchmark，避免优化后仍无法证明用户问题被修复。

实施项：

- 增加真实 Qt 事件循环 benchmark；
- 连续驱动 `QWheelEvent`、scrollbar 更新和 paint；
- 注入 GUI 线程 I/O 监控；
- 分别采集 Windows packaged runtime、Linux XCB、Linux Wayland 和 macOS；
- 支持 10k、100k、1M synthetic library；
- 支持真实 L2 thumbnail cache；
- 输出按平台隔离的 JSON/CSV 性能结果和摘要；
- 建立基线并保存 profile。

退出条件：

- benchmark 能稳定复现 Windows/Linux 输入追赶与长帧问题；
- 能识别 GUI 线程 SQLite、文件访问、解码和同步 repaint；
- Windows/Linux 结果不被 macOS 数据替代；
- 指标波动范围足以作为后续回归门禁。

### Phase 1：跨平台 UI-thread 零 I/O

目标：首先解除输入和绘制路径的直接阻塞。

实施项：

- `model.data()` miss 时立即返回内存 snapshot；优先 full/micro，二者皆无时才返回 placeholder；
- 禁止 `data()` 调用 `ensure_row_loaded()`；
- 删除 viewport 外额外行绘制；
- 移除 Linux 滚动路径同步 layout 和 `repaint()`；
- 引入每帧最多一次的 viewport update coordinator；
- 为 GUI 线程 I/O 契约增加测试断言；
- 保持 selection、focus 和 scrollbar 行为正确。

退出条件：

- GUI 线程同步 SQLite、文件访问、图片解码和 `repaint()` 计数为零；
- `scrollContentsBy()` P95 不超过 `2ms`；
- 快速滚动时 scrollbar 可即时移动；micro 完整图库不应持续露出纯色 placeholder。

### Phase 2：异步窗口与轻量 Gallery 数据

目标：将 collection window 构建从 GUI 线程移出，并减少每个窗口的无效工作。

实施项：

- 引入 `GalleryTileDTO` 和显式 SQL 投影；
- 禁止 Gallery window 查询 `SELECT *`；
- 禁止 Gallery window 同步复制完整 metadata；micro thumbnail 必须在 window worker 解码；
- 新增单线程异步 Gallery window loader；
- 引入 generation、collection revision、请求合并和 stale result 丢弃；
- model 使用有界内存 window snapshot；
- 按准确范围和 role 合并 `dataChanged`。

退出条件：

- paint/model data 只读取已发布的内存 snapshot；
- 旧 viewport window 结果不会覆盖当前 viewport；
- 100k/1M collection window 内存和 materialization 成本有界；
- 查询与 DTO 构建不会阻塞 GUI。

### Phase 3：跨平台异步缩略图管线

目标：消除 GUI 线程 L2 文件访问和解码，并使请求优先服务当前 viewport。

实施项：

- 将 API 拆分为 `peek()` 与 `request_many()`；
- worker 执行 L2 检查、读取和解码，返回 `QImage`；
- GUI 线程仅转换仍相关的 `QImage -> QPixmap`；
- 实现 visible/hot/warm 优先级；
- 接入滚动方向、速度、generation 和取消；
- 引入真实字节预算 LRU；
- 合并 thumbnail-ready 更新；
- 采集 L2 hit、decode、stale discard 和 worker queue 指标。

退出条件：

- GUI 线程文件访问和图片解码为零；
- 快速滚动时旧任务会取消或丢弃；
- 当前 viewport 请求不会长期被旧任务阻塞；
- Windows packaged runtime 与 Linux 真实 L2 cache 下满足输入追平 SLO。

### Phase 4：平台绘制稳定与验收

目标：解决架构重构后剩余的平台绘制差异，并完成跨平台发布门禁。

Windows：

- 验证 DWM、自定义顶部按钮栏、圆角和不同 DPI；
- profile 重复绘制、pixmap 转换和 L2 worker 长尾；
- 验证 packaged runtime cache 布局与安全扫描影响。

Linux：

- 分别验证 XCB 和 Wayland；
- 确认滚动路径无同步 layout/repaint；
- 验证透明圆角顶层窗口下无严重撕裂；
- 必要时试验 Gallery 内部 opaque child viewport。

macOS：

- 执行相同回归套件；
- 确认滚动体验和外观不回退。

退出条件：

- Windows 和 Linux 分别满足全部 SLO；
- macOS no-regression gate 通过；
- 外观、交互和功能回归通过；
- 性能报告包含平台、backend、DPI 和 runtime 信息。

### Phase 5：Native 门禁

仅当 Phase 1 至 Phase 4 完成后，Windows 或 Linux 仍无法达到 SLO，才进入 Native 评估。

进入条件必须同时满足：

1. GUI 线程同步 SQLite、文件访问、图片解码和 `repaint()` 已为零；
2. window 和 thumbnail 调度不存在明显过期任务积压；
3. profile 证明主要剩余成本来自 Python/PySide dispatch、delegate paint 或 Qt Widgets 光栅绘制；
4. 已尝试批量 snapshot、更新合并和局部绘制；
5. Windows/Linux 至少一个平台仍稳定无法满足帧间隔或输入追平 SLO。

候选方案：

- Qt Quick/GridView GPU 渲染层；
- 独立 C++ Gallery viewport，通过稳定 DTO/window 接口消费数据。

Native 实现必须复用 Phase 1 至 Phase 3 建立的数据、窗口和缩略图接口。不得在 Native renderer 中重新引入同步数据库或文件访问。

## 11. 兼容与迁移策略

### 11.1 `ensure_row_loaded()` 迁移

当前 `ensure_row_loaded()` 可在迁移期保留给明确的非绘制交互路径，但必须满足：

- 不从 `model.data()`、delegate paint 或 `paintEvent()` 调用；
- 不从滚动事件直接调用；
- 若明确交互需要加载，优先提供异步完成回调；
- 测试中对禁止调用栈做断言。

最终应将同步接口收敛为测试、维护或明确阻塞式工具用途，而非 Gallery 主路径能力。

### 11.2 查询兼容

现有通用 `read_query_asset_window` 可继续服务需要完整 DTO 的调用方；Gallery 必须使用独立轻量查询接口。不得为了减少初期改动而让 Gallery 异步 worker 继续执行 `SELECT *` 和完整 DTO 转换，否则只会把过重工作从 GUI 线程转移到 worker，仍会造成任务积压和高内存。

### 11.3 功能兼容清单

实施过程中必须保持：

- selection、multi-selection 和键盘导航；
- 双击/回车打开详情；
- 视频播放入口；
- favorite/rating/badge 更新；
- drag and drop；
- filter、sort 和 collection 切换；
- 扫描期间可见资产发布；
- 删除、移动后 row/selection 稳定性；
- DPI、主题和 tile size 变化；
- 自定义顶部按钮栏与圆角窗口。

### 11.4 direct mode 审计

所有绕过 SQL-first/windowed collection 的 direct mode 或小图库兼容模式也必须接受 GUI 线程零 I/O 审计。小图库路径可以采用更简单的数据结构，但不能在 paint/model data 中同步访问文件或解码图片。

## 12. 测试计划

### 12.1 单元测试

- `model.data()` miss 返回稳定内存 snapshot，且不调用同步加载；
- micro 完整图库高速滚动时，visible tile-frame 至少 `99.5%` 显示 micro 或 full；
- `peek()` miss 不访问文件系统；
- `request_many()` 去重、排序和 generation 取消正确；
- stale window/thumbnail result 不应用；
- `GalleryTileDTO` mapper 不读取非投影字段；
- byte-budget LRU 计算、pin 和淘汰正确；
- update coordinator 每帧最多调度一次 viewport update；
- Linux `scrollContentsBy()` 不调用同步 layout 或 `repaint()`。

### 12.2 集成测试

- 真实 SQLite collection window 异步请求与发布；
- filter/sort/collection revision 改变时丢弃旧结果；
- L2 hit、L2 miss、损坏缩略图和解码失败；
- 快速来回滚动时 worker 不被旧 generation 长期占用；
- thumbnail-ready 只更新准确 row/role；
- selection 与 row snapshot 更新保持一致；
- 扫描、删除和移动期间 Gallery 不显示错误资产。

### 12.3 真实 Qt 事件循环性能测试

benchmark 必须使用真实 `QApplication` 事件循环并驱动：

- 连续 `QWheelEvent`；
- scrollbar value 改变；
- `scrollContentsBy()`；
- `paintEvent()` 与 delegate paint；
- model data；
- SQLite window miss/hit；
- thumbnail memory/L2 miss/hit；
- window 与 thumbnail ready 信号。

不得使用只调用 collection API 或只计算窗口 materialization 的测试代替真实链路。

### 12.4 数据集矩阵

| 场景 | 目的 |
|---|---|
| 10k synthetic | 日常图库和快速 CI |
| 100k synthetic | 标准大型图库验收 |
| 1M synthetic | 极限窗口化、调度和内存验收 |
| 真实 L2 thumbnail cache | 文件布局、解码和长尾延迟 |
| 混合图片/视频/缺失文件 | badge、失败路径和稳定性 |

### 12.5 平台矩阵

| 平台 | 必须覆盖的环境 |
|---|---|
| Windows | packaged runtime；主要支持 DPI；真实 L2 cache |
| Linux | XCB；Wayland；主要支持 DPI；真实 L2 cache |
| macOS | 当前支持版本；no-regression 基线 |

每个平台的结果单独输出。Windows/Linux 必须分别通过，不采用跨平台平均值。

### 12.6 失败注入

- SQLite 查询人为延迟；
- L2 文件访问人为延迟；
- 缩略图解码人为延迟；
- worker 队列拥塞；
- generation 高频变化；
- 损坏 L2 文件；
- collection revision 在查询期间改变。

即使 worker 变慢，scrollbar 和已有 micro paint 仍必须保持响应；慢 worker 只能延迟内容完善，不能拖慢输入位移。

## 13. 性能实现守则

以下规则应进入代码审查清单：

- paint/model/scroll 路径新增调用必须证明为纯内存；
- 新增 Gallery SQL 必须使用显式投影；
- 新增图片解码必须明确线程归属；
- 新增信号必须评估频率、合并方式和 stale 行为；
- 新增缓存必须说明字节预算和淘汰策略；
- 新增预取必须有优先级和取消策略；
- 平台分支必须有对应平台测试；
- 性能优化不得通过降低滚动输入增益或丢弃有效 wheel delta 实现；
- 禁止用 macOS benchmark 代替 Windows/Linux；
- Native 方案必须先通过门禁评审。

## 14. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 异步结果与 collection 状态不一致 | 显示错误资产或错误缩略图 | generation + collection revision + asset key 校验 |
| placeholder 增多造成感知质量下降 | 快速滚动时短暂空白 | 强制 micro、方向预取、旧库 L2 回填、停止后优先补齐 full |
| worker 队列被旧任务占满 | 当前 viewport 缩略图延迟 | 去重、优先级、未开始取消、完成后丢弃 |
| 批量更新仍触发过大重绘 | 帧间隔长尾 | 准确 row/role、viewport clip、每帧合并 |
| Windows packaged 环境与开发环境差异 | 开发测试通过但发布版卡顿 | packaged runtime 强制门禁 |
| Linux backend 差异 | XCB 通过但 Wayland 撕裂 | 两个 backend 分别验收 |
| 真实字节 LRU 淘汰抖动 | 内存峰值或重复解码 | pin visible、分批淘汰、采集指标 |
| 兼容模式绕过新架构 | 隐性 GUI I/O 回归 | direct mode 审计和 UI-thread I/O 断言 |
| Native 过早引入 | 复杂度增加且根因未解决 | 严格 Phase 5 门禁 |

## 15. 发布与回滚策略

### 15.1 建议 PR 拆分

1. 性能观测、真实 Qt benchmark 和 UI-thread I/O 断言。
2. Linux 同步 repaint/layout 移除、viewport update coordinator、额外行绘制删除。
3. `GalleryTileDTO`、显式投影与异步 window loader。
4. model micro-first snapshot 与 generation-aware window 发布。
5. thumbnail `peek()` / `request_many()`、worker `QImage` 解码和真实字节 LRU。
6. viewport 优先级、取消和按帧更新合并。
7. Windows packaged、Linux XCB/Wayland 和 macOS 回归收敛。
8. 仅在门禁触发时创建独立 Native 方案与原型。

每个 PR 都必须包含对应测试和性能结果，避免在最终阶段才发现事件链回归。

### 15.2 功能开关

迁移期间可使用短期内部功能开关切换旧/新 Gallery 数据和缩略图管线，以支持 A/B profile 与回滚。功能开关必须：

- 不长期保留两套架构；
- 不允许旧路径绕过 GUI 线程 I/O 门禁进入发布版本；
- 在新路径稳定后删除。

### 15.3 回滚原则

- 单阶段出现功能回归时，可回滚该阶段实现，但保留 Phase 0 性能观测；
- 不以恢复同步 paint-path I/O 作为长期回滚方案；
- 平台专项优化可独立禁用，但跨平台线程契约不得回退；
- 回滚后必须重新记录 Windows/Linux 性能基线。

## 16. Definition of Done

只有同时满足以下条件，重构才视为完成：

- Windows packaged build 通过全部功能与性能 SLO；
- Linux XCB 和 Wayland 分别通过全部功能与性能 SLO；
- macOS no-regression gate 通过；
- 高速滚动结束后 scrollbar 在 `100ms` 内追平累计滚轮输入；
- 连续滚动 P95 帧间隔不超过 `24ms`；
- `scrollContentsBy()` P95 不超过 `2ms`；
- GUI 线程同步 SQLite 查询、文件访问、图片解码和 `repaint()` 次数为零；
- paint/model data 只读取内存 snapshot，绘制优先级固定为 full -> micro -> placeholder；
- 已完成且 micro 完整的图库连续滚动时，至少 `99.5%` visible tile-frame 显示 micro 或 full；
- 纯色 placeholder 不持续超过 `50ms`，远距离随机跳转除外；
- 旧 viewport window 与 thumbnail 任务可取消或丢弃；
- Gallery 查询使用轻量显式投影，不再 `SELECT *`；
- 缩略图内存缓存使用真实字节预算；
- 100k、1M synthetic library 和真实 L2 cache 均通过；
- selection、详情、播放、收藏、拖放、扫描、删除和移动行为无回归；
- 三平台自定义顶部按钮栏和圆角窗口保持；
- 性能 benchmark 和 UI-thread 契约进入持续集成或发布门禁；
- 若启用 Native renderer，已满足 Phase 5 门禁且接口不含同步 I/O。

## 17. 最终结论

Windows 与 Linux 的 Gallery 滚动卡顿具有共同的架构根因，但放大方式不同：Linux 存在明确的同步布局和强制重绘；Windows 更容易受到 L2 单文件访问、同步解码、高 DPI 和绘制反压影响。仅修复 Linux 的 `repaint()` 无法解决 Windows 问题，单独优化 Windows 缩略图也无法消除 paint-path SQLite 阻塞。

因此，本方案以跨平台 GUI 线程零 I/O 为核心，配合异步轻量 window、generation-aware 缩略图调度、准确更新合并和平台专项绘制稳定化。Windows 与 Linux 必须使用各自真实运行环境独立验收，macOS 作为不得回退的基线。

只有在这些架构问题全部解决后仍有明确 profile 证据时，才考虑 Qt Quick 或 C++ renderer。这样可以确保 Native 投入解决的是实际渲染上限，而不是掩盖同步 I/O 和任务调度技术债。

# iPhotron Gallery View 显示渲染结构重构开发文档

目标分支：`codex/resumable-scan-lifecycle`  
目标项目：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`  
文档主题：重构超大型相册 Gallery View 的显示、渲染、缩略图缓存与加载调度结构

---

## 1. 背景与问题

当前项目定位是 Windows 上的本地照片管理器，核心特性包括：

- Folder = Album，本地文件夹即相册；
- SQLite/索引化方向逐步替代 JSONL/manifest；
- 支持 Live Photo、GPS、收藏、非破坏性编辑、缩略图缓存；
- UI 基于 PySide6/Qt6，后续可能局部引入 QML/Qt Quick；
- 大相册场景可能达到数万、十万甚至几十万张图片。

Gallery View 是整个应用最容易出现性能瓶颈的模块。典型问题包括：

1. 打开相册时同步扫描文件系统、读取 EXIF 或生成缩略图，导致首屏慢；
2. 使用 `QGridLayout + QWidget PhotoCard` 创建大量 item，几千张后明显卡顿；
3. UI 线程直接读取图片、解码图片或转换 `QPixmap`，滚动时掉帧；
4. 缩略图缓存 key 不稳定，父相册/子相册、移动文件、编辑版本之间容易冲突或失效不正确；
5. 快速滚动时后台仍在处理远离视口的旧任务；
6. 每张缩略图 ready 后触发大范围 repaint，造成界面抖动；
7. `OFFSET` 深分页在超大相册里性能下降；
8. Gallery View 与扫描、缩略图、数据库耦合过重，不利于 resumable scan lifecycle 的继续演进。

本重构文档的目标是把 Gallery View 改造成“索引驱动、虚拟化显示、视口优先调度、分层缓存、UI 线程轻量绘制”的架构。

---

## 2. 重构目标

### 2.1 性能目标

| 场景 | 目标 |
|---|---|
| 10 万张相册首次打开 | 首屏 metadata 300ms 内可见，缩略图异步补齐 |
| 已有磁盘缩略图缓存 | 首屏 100~300ms 内显示主要缩略图 |
| 快速滚动 | UI 不被缩略图解码阻塞，滚动保持可交互 |
| 内存占用 | Gallery 缩略图内存缓存有预算上限，不随照片总数线性增长 |
| 任务调度 | 当前视口优先，远离视口的旧任务可取消或降级 |

### 2.2 架构目标

Gallery View 不再直接面对文件系统和原图文件。

目标调用链：

```text
GalleryView / AlbumGridView
    ↓
AssetGridModel / GalleryModel
    ↓
IndexStore / SQLite
    ↓
ThumbnailService
    ↓
MemoryThumbnailCache + DiskThumbnailCache
    ↓
DecodeWorker / ImageReader
    ↓
Original files
```

核心原则：

- Gallery View 只负责显示和交互；
- Model 只提供 asset metadata 与 thumbnail 状态；
- SQLite 是相册列表的唯一数据源；
- 缩略图由 ThumbnailService 统一调度；
- Worker 线程负责解码和 resize；
- GUI 线程只做 `QImage -> QPixmap` 转换与绘制；
- 所有耗时 IO、EXIF、decode、thumbnail generation 禁止在 UI 线程执行。

---

## 3. 推荐目录结构

建议新增或整理以下模块。路径可按现有项目实际包名调整。

```text
src/iPhoto/
  gui/
    views/
      gallery_view.py
    models/
      asset_grid_model.py
      asset_roles.py
    delegates/
      asset_grid_delegate.py
    controllers/
      gallery_controller.py
  library/
    index_store.py
    album_query.py
  thumbnails/
    thumbnail_service.py
    thumbnail_cache.py
    thumbnail_disk_cache.py
    thumbnail_memory_cache.py
    thumbnail_worker.py
    thumbnail_key.py
    thumbnail_types.py
```

如果现有项目已有 `gui/ui/widgets`、`gui/services`、`library`、`cache` 等目录，可以按现有层级合并，但应保持职责边界：

- `gui/views`：Qt View、滚动、选择、右键菜单；
- `gui/models`：`QAbstractListModel`，向 View 暴露数据；
- `gui/delegates`：绘制缩略图、标题、角标、选中边框；
- `thumbnails`：缩略图缓存、生成、调度；
- `library`：SQLite 查询，不依赖 GUI；
- `cache`：通用缓存实现，不反向依赖 GUI。

---

## 4. 目标架构

### 4.1 组件职责

#### GalleryView

建议基于：

```text
QListView + IconMode + 自定义 QStyledItemDelegate
```

或后续 QML：

```text
QML GridView + Python QAbstractListModel
```

优先推荐 QWidget 版本先落地：

- 不创建海量 QWidget；
- 使用 Qt view/model/delegate 虚拟化机制；
- delegate 只绘制可见 item；
- 滚动时根据 viewport range 请求缩略图。

#### AssetGridModel

继承 `QAbstractListModel`。

负责：

- 保存当前相册的 asset metadata；
- 提供 `AssetIdRole`、`RelPathRole`、`CaptureTimeRole`、`AspectRatioRole`、`FavoriteRole`、`LiveRole`、`ThumbnailRole` 等；
- 接收 thumbnail ready 信号后更新对应 index；
- 分页加载更多 asset；
- 不直接读取原图；
- 不直接生成缩略图。

#### AssetGridDelegate

继承 `QStyledItemDelegate`。

负责：

- 绘制 placeholder；
- 绘制 thumbnail pixmap；
- 绘制选中态、hover 态、favorite badge、live photo badge；
- 禁止在 paint() 内做任何磁盘 IO；
- 禁止在 paint() 内发起高成本同步逻辑。

#### ThumbnailService

负责统一处理缩略图请求：

- 查询内存缓存；
- 查询磁盘缓存；
- 提交后台生成任务；
- 合并重复请求；
- 取消或降级远离视口的任务；
- 根据滚动方向调整优先级；
- 对外通过 signal 返回结果。

#### ThumbnailMemoryCache

内存 LRU/距离加权缓存。

建议缓存对象：

- Worker 线程产出 `QImage`；
- GUI 线程维护 `QPixmap`；
- 或者 service 内部区分 `image_cache` 与 `pixmap_cache`。

注意：`QPixmap` 只能在 GUI 线程安全使用。

#### ThumbnailDiskCache

磁盘缩略图缓存。

建议目录：

```text
<library_root>/.iPhoto/thumbs/
  256/
  512/
  1024/
```

也可以采用 hash 分片：

```text
.iPhoto/thumbs/512/ab/cd/abcdef123.webp
```

#### IndexStore

负责 SQLite 查询。

Gallery 不直接扫描文件系统，而是只查询索引表。

---

## 5. 数据模型设计

### 5.1 Asset metadata

建议 Gallery 首屏只需要轻量 metadata：

```python
@dataclass(frozen=True)
class AssetGridItem:
    asset_id: int
    rel_path: str
    abs_path: str
    capture_time: int | None
    mtime_ns: int
    size_bytes: int
    width: int | None
    height: int | None
    orientation: int
    favorite: bool
    live_role: str | None
    edit_version: int
    thumb_key: str
```

### 5.2 SQLite 查询

基础查询：

```sql
SELECT
    id,
    rel_path,
    abs_path,
    capture_time,
    mtime_ns,
    size_bytes,
    width,
    height,
    orientation,
    favorite,
    live_role,
    edit_version,
    thumb_key
FROM assets
WHERE album_id = :album_id
ORDER BY capture_time DESC, id DESC
LIMIT :limit;
```

深分页不建议长期使用 `OFFSET`。推荐 seek pagination：

```sql
SELECT *
FROM assets
WHERE album_id = :album_id
  AND (
    capture_time < :last_capture_time
    OR (capture_time = :last_capture_time AND id < :last_id)
  )
ORDER BY capture_time DESC, id DESC
LIMIT :limit;
```

### 5.3 必要索引

```sql
CREATE INDEX IF NOT EXISTS idx_assets_album_time
ON assets(album_id, capture_time DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_assets_rel
ON assets(rel_path);

CREATE INDEX IF NOT EXISTS idx_assets_thumb_key
ON assets(thumb_key);
```

---

## 6. Thumbnail Key 设计

缩略图 key 必须稳定且可失效。

不要只用文件名，也不要只用相对路径。

推荐：

```python
def build_thumb_key(
    library_id: str,
    asset_id: int,
    normalized_rel_path: str,
    file_size: int,
    mtime_ns: int,
    orientation: int,
    edit_version: int,
    pipeline_version: str = "thumb-v3",
) -> str:
    raw = (
        f"{library_id}|{asset_id}|{normalized_rel_path}|"
        f"{file_size}|{mtime_ns}|{orientation}|"
        f"{edit_version}|{pipeline_version}"
    )
    return sha1(raw.encode("utf-8")).hexdigest()
```

失效条件：

| 条件 | 是否更新 thumb key |
|---|---|
| 原图 mtime 变化 | 是 |
| 原图 size 变化 | 是 |
| orientation 变化 | 是 |
| edit sidecar 版本变化 | 是 |
| thumbnail pipeline 版本变化 | 是 |
| 文件仅移动但 asset_id 不变 | 可选：若 key 包含 rel_path 则更新；若希望跨移动复用则不包含 rel_path |

建议初期包含 `rel_path`，更容易避免父/子相册同名文件冲突。后续如要支持跨移动复用，可引入 content hash 或 persistent asset uuid。

---

## 7. 缩略图缓存设计

### 7.1 缓存层级

```text
L1: GUI memory pixmap cache
L2: worker memory image cache
L3: disk thumbnail cache
L4: original image decode
```

最低可实现三层：

```text
Memory cache -> Disk cache -> Generate from original
```

### 7.2 尺寸等级

建议：

| size class | 用途 |
|---|---|
| 256 | 小网格 |
| 512 | Retina / 中等网格 |
| 1024 | 预览过渡 / 大缩略图 |

Gallery 根据 cell size 选择最接近但不小于目标尺寸的 size class。

### 7.3 磁盘缓存格式

推荐优先：

- JPEG：编码/解码快，兼容稳定；
- WebP：体积更小，质量更好，但依赖 Qt/WebP 支持；
- AVIF：不建议作为实时缩略图缓存首选，编码成本较高。

桌面端建议默认 JPEG 或 WebP。

### 7.4 thumb_cache 表

```sql
CREATE TABLE IF NOT EXISTS thumb_cache (
    asset_id INTEGER NOT NULL,
    size_class INTEGER NOT NULL,
    cache_key TEXT NOT NULL,
    path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    created_at INTEGER NOT NULL,
    last_access INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    PRIMARY KEY(asset_id, size_class)
);

CREATE INDEX IF NOT EXISTS idx_thumb_cache_access
ON thumb_cache(last_access);
```

---

## 8. View 虚拟化设计

### 8.1 禁止方案

禁止：

```python
for asset in assets:
    grid_layout.addWidget(PhotoCard(asset))
```

原因：

- QWidget 数量随照片数线性增长；
- layout 计算成本高；
- 大量 repaint；
- 内存不可控。

### 8.2 推荐 QWidget 方案

```python
class GalleryView(QListView):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(AssetGridDelegate(self))
```

`setUniformItemSizes(True)` 对固定网格非常重要。

### 8.3 Delegate 绘制策略

Delegate 只做绘制：

```python
class AssetGridDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        item = index.data(AssetRoles.ItemRole)
        pixmap = index.data(AssetRoles.ThumbnailRole)

        if pixmap is None:
            self._draw_placeholder(painter, option.rect)
        else:
            self._draw_thumbnail(painter, option.rect, pixmap)

        if item.favorite:
            self._draw_favorite_badge(painter, option.rect)

        if item.live_role:
            self._draw_live_badge(painter, option.rect)

        if option.state & QStyle.StateFlag.State_Selected:
            self._draw_selection(painter, option.rect)
```

禁止在 `paint()` 内调用：

- `QImageReader.read()`；
- `PIL.Image.open()`；
- `os.path.exists()` 的高频批量检查；
- SQLite 查询；
- ExifTool；
- FFmpeg/ffprobe。

---

## 9. 视口驱动加载

### 9.1 可见范围计算

GalleryView 在滚动或 resize 后计算可见 index range：

```python
def visible_index_range(view: QListView) -> tuple[int, int]:
    top_left = QPoint(0, 0)
    bottom_right = QPoint(view.viewport().width() - 1, view.viewport().height() - 1)

    first = view.indexAt(top_left)
    last = view.indexAt(bottom_right)

    if not first.isValid():
        return 0, 0

    first_row = first.row()
    last_row = last.row() if last.isValid() else min(first_row + 200, view.model().rowCount() - 1)
    return first_row, last_row
```

然后扩展热区：

```python
visible = (first, last)
hot = (first - 2 * screen_items, last + 3 * screen_items)
warm = (first - 3 * screen_items, last + 8 * screen_items)
```

### 9.2 滚动方向

记录上一次 scrollbar value：

```python
scroll_direction = "down" if current_value > previous_value else "up"
```

向下滚动时：

```text
当前视口：最高优先级
下方 3~8 屏：高优先级
上方 1~2 屏：中优先级
更远：取消或忽略
```

### 9.3 Debounce / Throttle

滚动信号非常密集，应使用 timer 合并：

| 事件 | 建议频率 |
|---|---|
| viewport range 更新 | 16~33ms |
| thumbnail 批量请求 | 50ms |
| SQLite 加载下一页 | 100~200ms |

---

## 10. ThumbnailService 调度设计

### 10.1 请求对象

```python
@dataclass(frozen=True)
class ThumbnailRequest:
    asset_id: int
    abs_path: str
    thumb_key: str
    size_class: int
    priority: int
    generation: int
```

`generation` 用于防止旧请求覆盖新 UI 状态。

### 10.2 响应对象

```python
@dataclass(frozen=True)
class ThumbnailResult:
    asset_id: int
    thumb_key: str
    size_class: int
    image: QImage
    from_cache: bool
    generation: int
```

### 10.3 去重

同一个 `(thumb_key, size_class)` 同时只能有一个 worker 任务。

```python
inflight: dict[tuple[str, int], ThumbnailRequest]
```

### 10.4 优先队列

建议使用 `PriorityQueue`：

```text
priority 越小越先执行
0 = visible
10 = scroll direction hot range
30 = nearby buffer
80 = warm prefetch
```

### 10.5 取消策略

Python 线程任务无法强制杀死，但可以软取消：

```python
if request.generation != current_generation:
    return

if not scheduler.is_still_needed(request.asset_id):
    return
```

快速滚动时提高 generation，旧任务完成后自动丢弃。

---

## 11. UI 更新策略

### 11.1 单 index 更新

Thumbnail ready 后只更新对应 item：

```python
self.dataChanged.emit(index, index, [AssetRoles.ThumbnailRole])
```

不要刷新整个 model。

### 11.2 防止复用错图

更新前检查：

```python
if item.asset_id != result.asset_id:
    return
if item.thumb_key != result.thumb_key:
    return
```

### 11.3 批量合并 repaint

对于大量 ready 事件，可每 16ms 合并一次：

```text
pending_changed_rows = set()
QTimer.singleShot(16, flush_data_changed)
```

连续 row 可以合并成 range，减少 signal 次数。

---

## 12. 分页与超大相册

### 12.1 初始加载

打开相册时：

```text
1. 查询前 300~1000 条 metadata
2. 立即显示 skeleton / placeholder
3. 请求当前视口 thumbnail
4. 空闲时预取后续页
```

### 12.2 滚动到底部预加载

当 last visible index 接近 `rowCount - threshold`：

```python
if last_visible > model.rowCount() - 300:
    model.fetch_more()
```

### 12.3 `canFetchMore/fetchMore`

`QAbstractListModel` 原生支持：

```python
def canFetchMore(self, parent):
    return self._has_more

def fetchMore(self, parent):
    items = self._index_store.query_next_page(self._cursor)
    self.beginInsertRows(QModelIndex(), old_count, old_count + len(items) - 1)
    self._items.extend(items)
    self.endInsertRows()
```

---

## 13. 与 resumable scan lifecycle 的关系

当前分支名表明项目正在处理可恢复扫描生命周期。Gallery 重构应与扫描生命周期解耦：

```text
Scanner / ResumableScanLifecycle
    ↓ 写入/更新 SQLite
IndexStore
    ↓ 发送 album/assets changed 事件
GalleryModel
    ↓ 局部更新 rows
GalleryView
```

要求：

1. 扫描过程不能阻塞 Gallery 滚动；
2. 扫描新增 asset 时，model 使用 `beginInsertRows/endInsertRows`；
3. 扫描更新 metadata 时，model 使用 `dataChanged`；
4. 扫描删除 asset 时，model 使用 `beginRemoveRows/endRemoveRows`；
5. 缩略图生成不应由 scanner 直接驱动，而由 Gallery 视口请求驱动；
6. 后台 scanner 可以低优先级预生成缩略图，但不能抢占当前视口任务。

推荐事件：

```python
class LibraryEvents(QObject):
    assetsInserted = Signal(int, list)      # album_id, asset_ids
    assetsUpdated = Signal(int, list)       # album_id, asset_ids
    assetsRemoved = Signal(int, list)       # album_id, asset_ids
    scanProgressChanged = Signal(int, int)  # done, total
```

---

## 14. 线程边界

### 14.1 GUI 线程允许

- 创建/使用 `QPixmap`；
- 更新 model；
- 发送 `dataChanged`；
- 绘制 delegate；
- 响应滚动和选择。

### 14.2 Worker 线程允许

- 读取磁盘缩略图为 `QImage`；
- 读取原图；
- resize；
- 写入磁盘缓存；
- 返回 `QImage`。

### 14.3 Worker 线程禁止

- 创建 `QPixmap`；
- 直接操作 QWidget；
- 直接触发 view repaint；
- 修改 Qt model 内部 list。

---

## 15. 最小实现阶段划分

### Phase 1：View/Model/Delegate 替换

目标：移除海量 QWidget PhotoCard。

任务：

- 新增 `AssetGridModel(QAbstractListModel)`；
- 新增 `AssetGridDelegate(QStyledItemDelegate)`；
- Gallery 改为 `QListView IconMode`；
- 使用 placeholder 绘制；
- 首屏 metadata 从 SQLite 加载。

验收：

- 1 万条 mock asset 滚动不卡；
- 不创建 1 万个 QWidget；
- `paint()` 内无 IO。

### Phase 2：Memory Thumbnail Cache

目标：实现内存缓存和异步返回。

任务：

- 新增 `ThumbnailService`；
- 新增 LRU memory cache；
- worker 返回 `QImage`；
- GUI 线程转换 `QPixmap`；
- ready 后局部 `dataChanged`。

验收：

- 首屏缩略图异步出现；
- 快速滚动不会错图；
- 内存可设上限。

### Phase 3：Disk Thumbnail Cache

目标：避免重复解码原图。

任务：

- 新增 `.iPhoto/thumbs/{size}`；
- 实现 thumb key；
- 实现 disk cache lookup/write；
- 增加 `thumb_cache` 表或轻量文件路径规则。

验收：

- 第二次打开相册明显更快；
- 修改原图或 edit_version 后缩略图自动失效；
- 父/子相册同名图片不串图。

### Phase 4：Viewport Scheduler

目标：当前视口优先，快速滚动不卡。

任务：

- Gallery 计算 visible/hot/warm ranges；
- ThumbnailService 引入 priority queue；
- 支持 generation 软取消；
- 支持滚动方向预取。

验收：

- 快速拖动滚动条后，当前屏幕优先显示；
- 后台不会持续处理远离视口的旧任务；
- CPU/IO 峰值可控。

### Phase 5：Pagination / Seek Query

目标：支持十万级/百万级 asset。

任务：

- `IndexStore` 增加 seek pagination；
- `AssetGridModel` 实现 `canFetchMore/fetchMore`；
- 接近底部自动加载下一页；
- 排序字段支持 `capture_time DESC, id DESC`。

验收：

- 10 万 asset mock 数据首屏秒开；
- 深滚动分页查询稳定；
- 不使用大 OFFSET。

---

## 16. 关键接口草案

### 16.1 AssetRoles

```python
class AssetRoles:
    AssetIdRole = Qt.ItemDataRole.UserRole + 1
    ItemRole = Qt.ItemDataRole.UserRole + 2
    ThumbnailRole = Qt.ItemDataRole.UserRole + 3
    FavoriteRole = Qt.ItemDataRole.UserRole + 4
    LiveRole = Qt.ItemDataRole.UserRole + 5
    AspectRatioRole = Qt.ItemDataRole.UserRole + 6
```

### 16.2 AssetGridModel

```python
class AssetGridModel(QAbstractListModel):
    thumbnailRequested = Signal(list)  # list[ThumbnailRequest]

    def __init__(self, index_store, thumbnail_service, parent=None):
        super().__init__(parent)
        self._items: list[AssetGridItem] = []
        self._pixmaps: dict[int, QPixmap] = {}
        self._index_by_asset_id: dict[int, int] = {}
        self._cursor = None
        self._has_more = True

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        if role == AssetRoles.ItemRole:
            return item
        if role == AssetRoles.AssetIdRole:
            return item.asset_id
        if role == AssetRoles.ThumbnailRole:
            return self._pixmaps.get(item.asset_id)
        if role == AssetRoles.FavoriteRole:
            return item.favorite
        if role == AssetRoles.LiveRole:
            return item.live_role
        return None

    def on_thumbnail_ready(self, result):
        row = self._index_by_asset_id.get(result.asset_id)
        if row is None:
            return
        item = self._items[row]
        if item.thumb_key != result.thumb_key:
            return
        self._pixmaps[item.asset_id] = QPixmap.fromImage(result.image)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [AssetRoles.ThumbnailRole])
```

### 16.3 ThumbnailService

```python
class ThumbnailService(QObject):
    thumbnailReady = Signal(object)  # ThumbnailResult

    def request_many(self, requests: list[ThumbnailRequest]) -> None:
        for request in requests:
            self.request(request)

    def request(self, request: ThumbnailRequest) -> None:
        cached = self._memory_cache.get(request.thumb_key, request.size_class)
        if cached is not None:
            self.thumbnailReady.emit(cached)
            return

        disk_path = self._disk_cache.path_for(request.thumb_key, request.size_class)
        if self._disk_cache.exists(disk_path):
            self._enqueue_disk_load(request, disk_path)
            return

        self._enqueue_generate(request)
```

---

## 17. 测试方案

### 17.1 单元测试

- `test_thumbnail_key_changes_when_mtime_changes`
- `test_thumbnail_key_changes_when_edit_version_changes`
- `test_memory_cache_evicts_by_budget`
- `test_disk_cache_path_is_stable`
- `test_asset_grid_model_updates_single_row`
- `test_seek_pagination_no_duplicates`
- `test_seek_pagination_order_stable`

### 17.2 GUI 测试

使用 `pytest-qt`：

- 创建 10,000 条 mock asset；
- 确认 Gallery 不创建 10,000 个 child QWidget；
- 滚动到底部不会卡死；
- thumbnail ready 后对应 index 更新；
- 快速滚动后旧 thumbnail result 不覆盖新 item。

### 17.3 性能测试

Mock 数据：

| 数据量 | 测试目标 |
|---|---|
| 10,000 | 基础滚动流畅 |
| 100,000 | 首屏加载与分页 |
| 500,000 | 内存是否稳定 |

记录指标：

- 首屏 metadata 查询耗时；
- 首屏 first paint 耗时；
- 缩略图平均加载耗时；
- 滚动时 UI frame drop；
- peak memory；
- worker queue length；
- cache hit rate。

---

## 18. 日志与调试

建议增加 namespace logger：

```text
iphoto.gallery
iphoto.gallery.model
iphoto.gallery.delegate
iphoto.thumbnail
iphoto.thumbnail.cache
iphoto.thumbnail.worker
```

关键日志：

```text
[Gallery] open album album_id=12 initial_limit=500 cost=42ms
[Gallery] visible range changed first=1200 last=1260 direction=down
[Thumb] request visible=60 hot=240 warm=500 generation=8
[Thumb] memory hit key=... size=512
[Thumb] disk hit key=... size=512 cost=3ms
[Thumb] generate key=... size=512 cost=48ms
[Thumb] discard stale result asset_id=123 generation=7 current=8
[Gallery] fetchMore rows=500 cursor=(capture_time,id)
```

---

## 19. 风险与注意事项

### 19.1 QPixmap 线程问题

`QPixmap` 不应在 worker 线程创建。Worker 返回 `QImage`，model 或 service 在 GUI 线程转换。

### 19.2 paint() 内禁止 IO

Delegate 的 `paint()` 被高频调用，任何 IO 都会造成滚动卡顿。

### 19.3 缩略图错图

必须用 `asset_id + thumb_key + generation` 三重检查。

### 19.4 SQLite 连接线程

SQLite connection 不要随意跨线程复用。Gallery model 查询可在主线程轻量执行，重查询/扫描写入应有独立连接。

### 19.5 扫描与 Gallery 写冲突

Scanner 更新 SQLite 时，Gallery 应通过事件局部刷新，而不是全量 reload。

### 19.6 父/子相册缓存冲突

如果 `.iPhoto` 同时存在于父目录和子目录，thumb key 必须包含 library identity 与 normalized rel path，避免同名文件串图。

---

## 20. Definition of Done

本次 Gallery View 渲染结构重构完成的标准：

1. Gallery View 不再为每张照片创建 QWidget；
2. Gallery 显示完全由 `QAbstractListModel + Delegate` 或 QML GridView 虚拟化驱动；
3. UI 线程不执行原图读取、EXIF 读取、缩略图生成；
4. ThumbnailService 支持内存缓存、磁盘缓存、后台生成；
5. 缩略图 key 可根据原图/编辑版本正确失效；
6. 当前视口优先加载，快速滚动时旧任务不会污染 UI；
7. Model 支持分页加载，避免深 OFFSET；
8. Scanner/ResumableScanLifecycle 与 Gallery 解耦；
9. 10 万 mock asset 可以首屏快速显示并稳定滚动；
10. 有单元测试和 GUI smoke test 覆盖关键行为。

---

## 21. 推荐提交拆分

建议拆成以下 PR/commit：

1. `refactor(gallery): introduce asset grid model and delegate`
2. `feat(thumbnails): add memory thumbnail cache and async service`
3. `feat(thumbnails): add disk thumbnail cache and stable thumb keys`
4. `feat(gallery): add viewport-driven thumbnail scheduling`
5. `feat(gallery): add seek pagination for large albums`
6. `test(gallery): add large album model and thumbnail scheduling tests`
7. `perf(gallery): add debug metrics for cache hit and viewport latency`

---

## 22. 最小落地建议

优先做 QWidget 版，不建议一开始重写为 QML。

最小闭环：

```text
QListView IconMode
+ QAbstractListModel
+ QStyledItemDelegate
+ SQLite metadata query
+ ThumbnailService
+ Memory LRU
+ Disk thumbs
+ viewport prefetch
```

等 QWidget 架构稳定后，再评估是否把显示层替换为 QML GridView。因为真正决定超大型相册流畅度的不是 QML 或 QWidget 本身，而是：

```text
索引化数据源
虚拟化 item
异步缩略图
视口优先调度
缓存失效正确性
```

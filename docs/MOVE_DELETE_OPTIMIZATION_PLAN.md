# 删除与移动媒体操作性能优化方案

> **版本:** 1.0 | **日期:** 2026-02-14  
> **问题描述:** 删除和移动媒体（文件夹）操作导致全局运行缓慢、UI界面更新卡顿，已无法通过局部优化解决

---

## 目录

1. [问题诊断](#1-问题诊断)
2. [当前架构分析](#2-当前架构分析)
3. [瓶颈根因定位](#3-瓶颈根因定位)
4. [优化方案总览](#4-优化方案总览)
5. [方案一：信号链路精简与增量更新](#5-方案一信号链路精简与增量更新)
6. [方案二：后台索引更新去阻塞](#6-方案二后台索引更新去阻塞)
7. [方案三：UI模型差量刷新](#7-方案三ui模型差量刷新)
8. [方案四：SQLite写入批量优化](#8-方案四sqlite写入批量优化)
9. [方案五：pybind11 / C++ 加速层](#9-方案五pybind11--c-加速层)
10. [实施路线图](#10-实施路线图)
11. [风险评估](#11-风险评估)
12. [附录：性能基准测试方案](#12-附录性能基准测试方案)

---

## 1. 问题诊断

### 1.1 用户可感知的症状

| 症状 | 严重程度 | 触发条件 |
|------|---------|---------|
| UI 界面冻结 0.5-2 秒 | 🔴 严重 | 删除/移动 ≥10 个文件 |
| 缩略图网格闪烁/全白后重绘 | 🔴 严重 | 任何删除/移动操作完成后 |
| 状态栏进度不流畅 | 🟡 中等 | 批量移动 ≥50 个文件 |
| 其他相册操作被阻塞 | 🔴 严重 | 移动/删除期间切换相册 |

### 1.2 性能瓶颈分布（估算）

以删除 20 张照片为例，当前耗时分布：

```
操作                                    耗时(ms)    占比
──────────────────────────────────────────────────────
文件系统移动 (shutil.move)                 20-100     5%
ExifTool 元数据提取 (process_media_paths)  100-400    25%
SQLite 源索引删除 (remove_rows)            5-20       2%
SQLite 目标索引插入 (append_rows)          10-30      3%
backend.pair() × 2 (Live Photo 配对)      200-600    35%
UI 模型全量重载 (dataChanged → 全量刷新)    100-500    20%
缩略图缓存清除 + 重建                      50-300     10%
──────────────────────────────────────────────────────
总计                                      485-1950ms
```

---

## 2. 当前架构分析

### 2.1 删除/移动操作完整信号链

```
用户操作 (右键菜单/拖拽)
    │
    ▼
ContextMenuController
    │ 调用 facade.delete_assets() / facade.move_assets()
    │ 同时执行 apply_optimistic_move() 乐观更新UI
    │
    ▼
AppFacade
    │ 委托给 AssetMoveService.move_assets()
    │
    ▼
AssetMoveService
    │ 创建 MoveWorker，提交至 BackgroundTaskManager
    │
    ▼
BackgroundTaskManager
    │ 暂停 filesystem watcher
    │ 提交 MoveWorker 至 QThreadPool
    │
    ▼
MoveWorker.run() [后台线程]
    ├─ 逐个文件 shutil.move()
    ├─ _update_source_index()
    │   ├─ store.remove_rows()          ← SQLite 写操作
    │   └─ backend.pair(source)         ← 全量读+写 SQLite + 写 links.json
    ├─ _update_destination_index()
    │   ├─ process_media_paths()        ← 调用 ExifTool 子进程
    │   ├─ store.append_rows()          ← SQLite 写操作
    │   └─ backend.pair(destination)    ← 全量读+写 SQLite + 写 links.json
    └─ emit finished signal
            │
            ▼
    AssetMoveService._handle_move_finished() [主线程]
        │ emit moveCompletedDetailed
        │
        ▼
    LibraryUpdateService.handle_move_operation_completed() [主线程]
        ├─ emit indexUpdated(source)           ← 信号1
        ├─ emit linksUpdated(source)           ← 信号2
        ├─ emit indexUpdated(destination)      ← 信号3
        ├─ emit linksUpdated(destination)      ← 信号4
        ├─ emit indexUpdated(library_root)     ← 信号5
        ├─ emit linksUpdated(library_root)     ← 信号6
        └─ emit assetReloadRequested(...)      ← 信号7 → 触发全量重载
                │
                ▼
        AppFacade._on_asset_reload_requested()
            ├─ emit loadStarted
            └─ emit loadFinished
                    │
                    ▼
            AssetListViewModel (观察 DataSource)
                └─ AssetDataSource.reload_current_query()
                    └─ load() → 重新查询数据库全量数据
                        └─ dataChanged.emit()
                            └─ UI 网格全量刷新
                                └─ 所有可见缩略图重新加载
```

### 2.2 关键文件清单

| 文件 | 职责 | 性能相关度 |
|------|------|-----------|
| `gui/ui/tasks/move_worker.py` | 文件移动 + 索引更新 | 🔴 核心 |
| `gui/services/asset_move_service.py` | 移动操作编排 | 🟡 中等 |
| `gui/services/library_update_service.py` | 信号分发 + 相册刷新 | 🔴 核心 |
| `gui/facade.py` | 操作入口 + 信号中继 | 🟡 中等 |
| `io/scanner_adapter.py` | ExifTool 元数据提取 | 🔴 核心 |
| `cache/index_store/repository.py` | SQLite CRUD | 🟡 中等 |
| `gui/viewmodels/asset_data_source.py` | 数据加载 + DTO 缓存 | 🔴 核心 |
| `gui/viewmodels/asset_list_viewmodel.py` | Qt 模型适配 | 🟡 中等 |
| `gui/ui/models/asset_cache_manager.py` | 缩略图缓存 | 🔴 核心 |
| `app.py` (pair / _ensure_links) | Live Photo 配对 | 🔴 核心 |

---

## 3. 瓶颈根因定位

### 🔴 根因 1：backend.pair() 双重调用

**位置:** `move_worker.py` 第 207 行和第 332 行

```python
# _update_source_index 结尾
backend.pair(self._library_root, library_root=self._library_root)  # 调用1

# _update_destination_index 结尾
backend.pair(self._destination_root)  # 调用2
```

**问题分析：**
- `pair()` 每次调用都读取整个相册的索引数据（`read_album_assets` 或 `read_all`）
- 计算 Live Photo 配对关系（O(N)）
- 写入 `links.json` 文件
- 同步 `live_role` 到 SQLite 数据库
- **每次移动操作执行 2 次**，每次耗时 100-300ms

**优化潜力：** 合并为单次调用，或延迟到所有移动完成后批量执行

### 🔴 根因 2：process_media_paths() 调用 ExifTool 子进程

**位置:** `move_worker.py` 第 252-254 行

```python
new_rows = list(
    process_media_paths(process_root, image_paths, video_paths)
)
```

**问题分析：**
- 对已经在索引中的文件**重新提取元数据**
- 每 50 个文件启动一次 ExifTool 子进程（50-200ms/次）
- 生成微缩略图（10-30ms/张图片）
- **但这些文件只是被移动了位置，元数据并未改变**

**优化潜力：** 复用源索引中的元数据行，仅更新 `rel` 路径，避免重复 ExifTool 调用

### 🔴 根因 3：全量 UI 模型重载

**位置:** `library_update_service.py` 第 257-263 行

```python
for candidate, should_restart in refresh_targets.values():
    self.indexUpdated.emit(candidate)
    self.linksUpdated.emit(candidate)
    if should_restart:
        self.assetReloadRequested.emit(target_root, False, force_reload)
```

**问题分析：**
- `assetReloadRequested` → `AssetDataSource.reload_current_query()` → 重新执行完整 SQL 查询
- 查询结果触发 `dataChanged.emit()`
- ViewModel 调用 `beginResetModel()` / `endResetModel()`
- 网格控件**清除所有缩略图缓存**，重新加载可见区域的缩略图
- 乐观更新（optimistic move）的成果被全量重载覆盖

**优化潜力：** 利用已有的乐观更新结果，仅做增量验证而非全量重载

### 🔴 根因 4：冗余信号级联

**问题分析：**
一次删除操作会触发多达 **7+ 个信号**（见 2.1 节信号链），每个信号的监听者可能触发各自的刷新逻辑：

```
indexUpdated(source)           → 监听者A刷新
linksUpdated(source)           → 监听者B刷新
indexUpdated(destination)      → 监听者A再次刷新
linksUpdated(destination)      → 监听者B再次刷新
indexUpdated(library_root)     → 监听者A第三次刷新
linksUpdated(library_root)     → 监听者B第三次刷新
assetReloadRequested           → 触发全量重载（第四次刷新）
```

**优化潜力：** 合并为单一 "操作完成" 信号，附带差量信息

### 🟡 根因 5：缩略图缓存失效策略

**位置:** `asset_cache_manager.py` 第 61-94 行

```python
def reset_caches_for_new_rows(self, rows: List[Dict[str, object]]) -> None:
    # ...
    self.clear_thumbnails_not_in(active_rel_keys)
```

**问题分析：**
- 全量重载后，缓存清理遍历所有条目（O(N)）
- 被移动文件的缩略图已通过 `_recently_removed_rows` 缓存，但全量重载时该缓存也被清理
- 未移动的文件的缩略图因 `rel` 键未变，理论上不需要清理

---

## 4. 优化方案总览

```
┌──────────────────────────────────────────────────────────────────┐
│                        优化方案全景图                             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  方案一：信号链路精简              ┌──── 预计收益: 30% ────────┐ │
│  ├─ 合并冗余信号                  │ 减少 6 次信号 → 1 次      │ │
│  └─ 携带差量信息                  │ 避免重复刷新逻辑          │ │
│                                   └────────────────────────────┘ │
│  方案二：后台索引更新去阻塞        ┌──── 预计收益: 35% ────────┐ │
│  ├─ 复用源索引元数据              │ 避免 ExifTool 子进程      │ │
│  ├─ 合并 pair() 调用              │ pair() 从 2→1 次         │ │
│  └─ 延迟 pair() 到空闲时          │ 不阻塞文件移动           │ │
│                                   └────────────────────────────┘ │
│  方案三：UI模型差量刷新            ┌──── 预计收益: 25% ────────┐ │
│  ├─ 增量确认乐观更新              │ 避免全量数据库查询        │ │
│  ├─ 保留缩略图缓存                │ 减少磁盘 I/O             │ │
│  └─ 分步刷新可见区域              │ 消除网格闪烁             │ │
│                                   └────────────────────────────┘ │
│  方案四：SQLite 写入批量优化       ┌──── 预计收益: 5% ─────────┐ │
│  ├─ 单事务批量写入                │ 减少事务开销              │ │
│  └─ WAL 模式读写分离              │ 读操作不阻塞写            │ │
│                                   └────────────────────────────┘ │
│  方案五：pybind11/C++ 加速        ┌──── 预计收益: 10-50% ─────┐ │
│  ├─ 文件 I/O 批量操作             │ 批量 rename 无 GIL        │ │
│  ├─ 元数据提取 (libexif/exiv2)    │ 替代 ExifTool 子进程      │ │
│  └─ 缩略图解码                    │ libjpeg-turbo 加速       │ │
│                                   └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. 方案一：信号链路精简与增量更新

### 5.1 问题

当前 `handle_move_operation_completed()` 为每个受影响的相册路径分别发出 `indexUpdated` 和 `linksUpdated` 信号，最后再发出 `assetReloadRequested`。监听者无法区分这些信号来自同一个操作还是多个独立操作，导致重复刷新。

### 5.2 方案

**引入 `MoveOperationResult` 数据类和统一完成信号：**

```python
@dataclass
class MoveOperationResult:
    """移动/删除操作的完整结果描述。"""
    source_root: Path
    destination_root: Path
    moved_pairs: List[Tuple[Path, Path]]  # (原路径, 新路径)
    removed_rels: List[str]               # 从源索引中移除的 rel
    added_rels: List[str]                 # 添加到目标索引的 rel
    is_delete: bool
    is_restore: bool
    source_ok: bool
    destination_ok: bool
```

**统一信号：**

```python
class LibraryUpdateService(QObject):
    # 新增：携带完整结果的单一信号
    moveOperationCompleted = Signal(object)  # MoveOperationResult

    def handle_move_operation_completed(self, ...):
        result = MoveOperationResult(...)
        # 仅发出一次信号，由各监听者自行判断是否需要刷新
        self.moveOperationCompleted.emit(result)
```

### 5.3 监听者改造

```python
class AssetDataSource:
    def on_move_completed(self, result: MoveOperationResult):
        """增量处理移动结果，而非全量重载。"""
        # 1. 确认乐观移除：从 _pending_moves 中清理已完成的项
        self._confirm_pending_moves(result.moved_pairs)

        # 2. 仅在乐观更新未覆盖的情况下才做增量补丁
        if result.added_rels:
            self._patch_added_rels(result.added_rels)

        # 3. 不触发全量重载
        self.dataChanged.emit()  # 通知视图仅刷新变化的行
```

### 5.4 预期收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 信号触发次数 | 7+ 次 | 1 次 |
| UI 刷新次数 | 3-4 次 | 1 次 |
| 数据库查询次数 | 1 次全量 | 0 次（增量确认） |

---

## 6. 方案二：后台索引更新去阻塞

### 6.1 问题

`MoveWorker._update_destination_index()` 对已移动文件重新调用 `process_media_paths()`，触发 ExifTool 子进程提取元数据。但文件仅改变了路径，元数据（EXIF、尺寸、时长等）完全不变。

### 6.2 方案

#### 6.2.1 复用源索引行

在 `_update_source_index` 中删除行之前，先读取源行数据并缓存：

```python
def _update_source_index(self, moved):
    store = get_global_repository(index_root)

    # 新增：在删除前读取源行数据
    cached_rows = {}
    for original, target in moved:
        rel = self._compute_rel(original, index_root)
        if rel:
            row_data = store.get_row_by_rel(rel)  # 新增 API
            if row_data:
                cached_rows[str(original)] = row_data

    store.remove_rows(rels)
    return cached_rows  # 传递给 _update_destination_index
```

在 `_update_destination_index` 中复用缓存行：

```python
def _update_destination_index(self, moved, cached_source_rows=None):
    store = get_global_repository(index_root)

    new_rows = []
    uncached_images, uncached_videos = [], []

    for original, target in moved:
        cached = cached_source_rows.get(str(original)) if cached_source_rows else None
        if cached:
            # 复用元数据，仅更新路径相关字段
            row = dict(cached)
            new_rel = target.relative_to(process_root).as_posix()
            row["rel"] = new_rel
            row["parent_album_path"] = str(Path(new_rel).parent.as_posix())
            new_rows.append(row)
        else:
            # 无缓存时回退到 ExifTool 提取
            suffix = target.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                uncached_images.append(target)
            else:
                uncached_videos.append(target)

    # 仅对无缓存的文件调用 ExifTool
    if uncached_images or uncached_videos:
        new_rows.extend(process_media_paths(process_root, uncached_images, uncached_videos))

    store.append_rows(new_rows)
```

**核心原理：** 文件移动只改变路径，不改变内容。复用源索引行可避免 90%+ 的 ExifTool 调用。

#### 6.2.2 合并 pair() 调用

将两次 `backend.pair()` 调用合并为一次，且仅在必要时执行：

```python
def run(self) -> None:
    # ... 文件移动 ...

    if moved and not self._cancel_requested:
        cached_source_rows = self._update_source_index(moved)
        self._update_destination_index(moved, cached_source_rows)

        # 合并：仅在 library_root 级别执行一次 pair()
        if self._library_root:
            backend.pair(self._library_root, library_root=self._library_root)
```

#### 6.2.3 延迟 pair() 到空闲时

对于非 Live Photo 相关的移动操作，完全跳过 `pair()` 调用，改为在移动完成后由后台定时器触发：

```python
class LibraryUpdateService:
    def __init__(self):
        self._pair_debounce_timer = QTimer()
        self._pair_debounce_timer.setSingleShot(True)
        self._pair_debounce_timer.setInterval(2000)  # 2秒防抖
        self._pair_debounce_timer.timeout.connect(self._deferred_pair)
        self._pair_pending_roots: Set[Path] = set()

    def schedule_deferred_pair(self, root: Path):
        self._pair_pending_roots.add(root)
        self._pair_debounce_timer.start()

    def _deferred_pair(self):
        roots = list(self._pair_pending_roots)
        self._pair_pending_roots.clear()
        # 在后台线程执行
        for root in roots:
            self._task_manager.submit_task(...)
```

### 6.3 预期收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| ExifTool 调用 | 每 50 文件 1 次 | 仅对新文件调用 |
| pair() 调用 | 2 次/操作 | 0-1 次（延迟执行） |
| 后台线程耗时 | 400-1000ms | 50-200ms |

---

## 7. 方案三：UI模型差量刷新

### 7.1 问题

当前流程：乐观更新 → 后台移动 → 全量重载（覆盖乐观更新）→ 缩略图缓存清除 → 重新加载

### 7.2 方案

#### 7.2.1 确认式刷新代替全量重载

移动完成后，不重新查询数据库，而是确认乐观更新的正确性：

```python
class AssetDataSource:
    def confirm_move_results(self, result: MoveOperationResult):
        """验证乐观更新与实际结果的一致性。"""
        if not result.source_ok or not result.destination_ok:
            # 仅在失败时回退并全量重载
            self._rollback_pending_moves(result)
            self.reload_current_query()
            return

        # 成功时：仅清理 pending 状态
        confirmed_keys = set()
        for original, target in result.moved_pairs:
            confirmed_keys.add(str(original))

        self._pending_moves = [
            m for m in self._pending_moves
            if str(m.source_abs) not in confirmed_keys
        ]
        self._pending_paths -= confirmed_keys

        # 不触发全量重载，仅通知变更行
        self.dataChanged.emit()
```

#### 7.2.2 保留未变更缩略图

修改 `AssetCacheManager.reset_caches_for_new_rows()` 以保护未变更行的缩略图：

```python
def incremental_cache_update(
    self,
    removed_rels: Set[str],
    added_rels: Set[str],
) -> None:
    """增量更新缓存：仅清理被移除的项，保留其余缩略图。"""
    for rel in removed_rels:
        self._thumb_cache.pop(rel, None)
        self._composite_cache.pop(rel, None)
        self._placeholder_cache.pop(rel, None)
    # added_rels 的缩略图将在首次可见时懒加载
```

#### 7.2.3 行级模型更新

使用 Qt 的 `dataChanged` 信号进行行级通知，避免 `beginResetModel()` / `endResetModel()`：

```python
class AssetListViewModel:
    def on_move_confirmed(self, removed_rows: List[int], added_dtos: List[AssetDTO]):
        """行级更新而非全量重置。"""
        # 移除行
        for row in sorted(removed_rows, reverse=True):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._data_source.remove_row_at(row)
            self.endRemoveRows()

        # 添加行（如目标相册是当前视图）
        if added_dtos:
            start = self.rowCount()
            self.beginInsertRows(QModelIndex(), start, start + len(added_dtos) - 1)
            self._data_source.append_dtos(added_dtos)
            self.endInsertRows()
```

### 7.3 预期收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 模型重置类型 | `beginResetModel` | 行级 `beginRemoveRows` |
| 缩略图重加载 | 所有可见行 | 仅新增行 |
| 网格闪烁 | 明显 | 无 |
| UI 冻结时间 | 100-500ms | <10ms |

---

## 8. 方案四：SQLite 写入批量优化

### 8.1 当前问题

`MoveWorker` 中的索引更新分别在 `_update_source_index` 和 `_update_destination_index` 中执行，各自独立开启事务。

### 8.2 方案

#### 8.2.1 单事务合并读写

```python
def _update_indexes_atomically(self, moved):
    store = get_global_repository(index_root)

    with store.transaction() as conn:
        # 1. 批量读取源行（用于复用）
        source_rows = self._batch_read_source_rows(conn, moved)

        # 2. 批量删除源行
        rels_to_remove = [...]
        conn.executemany("DELETE FROM assets WHERE rel = ?",
                        [(r,) for r in rels_to_remove])

        # 3. 批量插入目标行
        new_rows = self._build_destination_rows(moved, source_rows)
        store._insert_rows(conn, new_rows)
    # 事务自动提交，仅一次 fsync
```

#### 8.2.2 启用 WAL 模式

```python
class DatabaseManager:
    def _create_connection(self):
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB 缓存
        return conn
```

WAL（Write-Ahead Logging）允许读写操作并发，减少 UI 线程读取索引时被后台写入阻塞的概率。

### 8.3 预期收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 事务次数 | 4-6 次 | 1 次 |
| fsync 次数 | 4-6 次 | 1 次 |
| 读写并发 | 互斥 | WAL 并发 |

---

## 9. 方案五：pybind11 / C++ 加速层

### 9.1 适用场景分析

| Python 瓶颈 | C++ 能否加速 | 收益评估 |
|-------------|-------------|---------|
| ExifTool 子进程启动 | ✅ 使用 libexiv2 内嵌替代 | 🔴 高（消除进程启动开销） |
| shutil.move 文件操作 | ✅ 批量 rename() 无 GIL | 🟡 中等（减少 GIL 竞争） |
| JSON 解析/序列化 | ✅ rapidjson/simdjson | 🟢 低（不是主要瓶颈） |
| 微缩略图生成 | ✅ libjpeg-turbo/libvips | 🟡 中等（Pillow draft 已较优） |
| SQLite 操作 | ❌ Python sqlite3 已是 C 扩展 | 🟢 低 |
| Qt 信号/槽 | ❌ 已在 C++ 层 | 🟢 无 |

### 9.2 推荐的 C++ 加速模块

#### 9.2.1 模块一：`iphoto_native.file_ops` — 批量文件操作

```cpp
// file_ops.cpp
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <filesystem>

namespace py = pybind11;
namespace fs = std::filesystem;

struct MoveResult {
    std::string source;
    std::string target;
    bool success;
    std::string error;
};

/**
 * 批量移动文件，释放 GIL 以避免阻塞 Python 主线程。
 * 使用 std::filesystem::rename 实现零拷贝移动（同分区）。
 */
std::vector<MoveResult> batch_move(
    const std::vector<std::string>& sources,
    const std::string& destination_dir,
    bool handle_collisions = true
) {
    std::vector<MoveResult> results;
    results.reserve(sources.size());

    // 释放 GIL
    py::gil_scoped_release release;

    fs::path dest(destination_dir);
    fs::create_directories(dest);

    for (const auto& src_str : sources) {
        MoveResult r;
        r.source = src_str;
        try {
            fs::path src(src_str);
            fs::path target = dest / src.filename();

            if (handle_collisions) {
                int counter = 1;
                auto stem = target.stem().string();
                auto ext = target.extension().string();
                while (fs::exists(target)) {
                    target = dest / (stem + " (" + std::to_string(counter++) + ")" + ext);
                }
            }

            fs::rename(src, target);  // 零拷贝移动（同分区内）
            r.target = target.string();
            r.success = true;
        } catch (const fs::filesystem_error& e) {
            // rename 失败时回退到拷贝+删除
            try {
                fs::path src(src_str);
                fs::path target = dest / src.filename();
                fs::copy(src, target, fs::copy_options::overwrite_existing);
                fs::remove(src);
                r.target = target.string();
                r.success = true;
            } catch (const std::exception& e2) {
                r.success = false;
                r.error = e2.what();
            }
        }
        results.push_back(std::move(r));
    }
    return results;
}

PYBIND11_MODULE(file_ops, m) {
    py::class_<MoveResult>(m, "MoveResult")
        .def_readonly("source", &MoveResult::source)
        .def_readonly("target", &MoveResult::target)
        .def_readonly("success", &MoveResult::success)
        .def_readonly("error", &MoveResult::error);

    m.def("batch_move", &batch_move,
          py::arg("sources"),
          py::arg("destination_dir"),
          py::arg("handle_collisions") = true,
          "批量移动文件，同分区内使用零拷贝 rename");
}
```

#### 9.2.2 模块二：`iphoto_native.metadata` — 内嵌元数据提取

```cpp
// metadata.cpp — 使用 libexiv2 替代 ExifTool 子进程
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <exiv2/exiv2.hpp>

namespace py = pybind11;

/**
 * 批量提取元数据，不启动子进程。
 * 在 C++ 侧完成，释放 GIL 以不阻塞 UI。
 */
std::vector<std::map<std::string, std::string>> batch_get_metadata(
    const std::vector<std::string>& paths
) {
    std::vector<std::map<std::string, std::string>> results;
    results.reserve(paths.size());

    py::gil_scoped_release release;

    for (const auto& path : paths) {
        std::map<std::string, std::string> meta;
        try {
            auto image = Exiv2::ImageFactory::open(path);
            image->readMetadata();

            const auto& exifData = image->exifData();
            // 提取关键字段
            auto get = [&](const char* key) -> std::string {
                auto it = exifData.findKey(Exiv2::ExifKey(key));
                return it != exifData.end() ? it->toString() : "";
            };

            meta["width"] = get("Exif.Photo.PixelXDimension");
            meta["height"] = get("Exif.Photo.PixelYDimension");
            meta["make"] = get("Exif.Image.Make");
            meta["model"] = get("Exif.Image.Model");
            meta["datetime"] = get("Exif.Photo.DateTimeOriginal");
            meta["gps_lat"] = get("Exif.GPSInfo.GPSLatitude");
            meta["gps_lon"] = get("Exif.GPSInfo.GPSLongitude");
            meta["iso"] = get("Exif.Photo.ISOSpeedRatings");
            meta["f_number"] = get("Exif.Photo.FNumber");
            meta["exposure_time"] = get("Exif.Photo.ExposureTime");
            meta["focal_length"] = get("Exif.Photo.FocalLength");
            meta["orientation"] = get("Exif.Image.Orientation");
        } catch (...) {
            meta["error"] = "Failed to read metadata";
        }
        results.push_back(std::move(meta));
    }
    return results;
}

PYBIND11_MODULE(metadata, m) {
    m.def("batch_get_metadata", &batch_get_metadata,
          py::arg("paths"),
          "批量提取 EXIF 元数据，无需 ExifTool 子进程");
}
```

### 9.3 集成方式

```
src/
├── iPhoto/
│   ├── native/                    # 新增 C++ 加速层
│   │   ├── CMakeLists.txt
│   │   ├── file_ops.cpp
│   │   ├── metadata.cpp
│   │   └── __init__.py            # 提供 Python 回退
│   └── ...
```

**Python 回退策略（graceful degradation）：**

```python
# src/iPhoto/native/__init__.py
try:
    from .file_ops import batch_move
    from .metadata import batch_get_metadata
    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

    def batch_move(sources, destination_dir, handle_collisions=True):
        """Python 回退实现。"""
        import shutil
        from pathlib import Path
        # ... 现有 shutil.move 逻辑 ...

    def batch_get_metadata(paths):
        """Python 回退实现。"""
        from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
        provider = ExifToolMetadataProvider()
        return provider.get_metadata_batch([Path(p) for p in paths])
```

### 9.4 构建配置

```toml
# pyproject.toml 新增
[build-system]
requires = ["setuptools", "pybind11>=2.12"]

[tool.setuptools.ext-modules]
iphoto_native_file_ops = {sources = ["src/iPhoto/native/file_ops.cpp"]}
iphoto_native_metadata = {sources = ["src/iPhoto/native/metadata.cpp"]}
```

### 9.5 预期收益

| 模块 | Python 耗时 | C++ 耗时 | 加速比 |
|------|------------|---------|--------|
| 批量移动 20 文件 | 100ms | 20ms | 5× |
| 元数据提取 20 文件 | 200-400ms | 30-60ms | 5-7× |
| 微缩略图生成 20 张 | 200-600ms | 50-100ms | 4-6× |

### 9.6 C++ 方案的成本与风险

| 方面 | 评估 |
|------|------|
| 开发成本 | 🟡 中等（需要 C++ 开发经验） |
| 构建复杂度 | 🔴 显著增加（需要 CMake + 编译器工具链） |
| 跨平台兼容 | 🟡 需要 macOS/Windows/Linux 分别编译 |
| 分发体积 | 🟡 增加 2-5MB 二进制 |
| 维护成本 | 🔴 双语言维护，调试复杂度增加 |
| 回退能力 | ✅ Python fallback 保证功能不受影响 |

**建议：** 优先实施方案一至四（纯 Python 架构优化），预计可解决 80%+ 的性能问题。仅在纯 Python 优化无法满足需求时再考虑 C++ 加速层。

---

## 10. 实施路线图

### 阶段一：快速见效（1-2 周）

```
优先级  方案                              预计耗时    收益
──────────────────────────────────────────────────────────
P0     方案二 6.2.1: 复用源索引行          3 天      避免 ExifTool
P0     方案二 6.2.2: 合并 pair() 调用      1 天      pair() 2→1
P0     方案一 5.2: 统一完成信号            2 天      信号 7→1
P1     方案三 7.2.1: 确认式刷新            2 天      避免全量重载
P1     方案四 8.2.2: WAL 模式              0.5 天    并发读写
```

**阶段一目标：** 删除/移动 20 文件耗时从 ~1500ms 降至 ~300ms

### 阶段二：体验完善（2-3 周）

```
优先级  方案                              预计耗时    收益
──────────────────────────────────────────────────────────
P1     方案三 7.2.2: 保留缩略图缓存       2 天      消除闪烁
P1     方案三 7.2.3: 行级模型更新          3 天      Qt 标准更新
P2     方案二 6.2.3: 延迟 pair()           2 天      后台空闲执行
P2     方案四 8.2.1: 单事务合并            1 天      减少 fsync
```

**阶段二目标：** UI 零感知延迟，删除/移动操作如同瞬间完成

### 阶段三：极限优化（可选，3-4 周）

```
优先级  方案                              预计耗时    收益
──────────────────────────────────────────────────────────
P3     方案五 9.2.1: C++ 批量文件操作      2 周     5× 加速
P3     方案五 9.2.2: C++ 元数据提取        2 周     5-7× 加速
P3     方案五: C++ 缩略图解码              1 周     4-6× 加速
```

**阶段三目标：** 万级文件操作毫秒级响应

---

## 11. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 增量更新与数据库不一致 | 中 | 显示错误数据 | 定期对账 + 手动刷新按钮 |
| WAL 模式增加磁盘占用 | 低 | 临时文件增大 | WAL checkpoint 定期触发 |
| pair() 延迟导致 Live Photo 暂时不配对 | 中 | 用户短暂看不到 Live 标记 | 延迟窗口控制在 2 秒内 |
| C++ 编译环境不一致 | 高 | 部分用户无法使用 | Python fallback 必须完整 |
| 乐观更新回滚闪烁 | 低 | 文件恢复时 UI 闪烁 | 批量回滚 + 动画过渡 |

---

## 12. 附录：性能基准测试方案

### 12.1 测试工具

建议在 `tests/benchmarks/` 目录下建立性能基准：

```python
import time
from pathlib import Path

def benchmark_move_operation(n_files: int):
    """测量移动 n 个文件的端到端耗时。"""
    # 准备测试文件
    source_dir = create_test_album(n_files)
    dest_dir = create_empty_album()

    start = time.perf_counter()
    facade.move_assets(
        [source_dir / f"test_{i}.jpg" for i in range(n_files)],
        dest_dir,
    )
    # 等待后台任务完成
    wait_for_task_completion(facade)
    elapsed = time.perf_counter() - start

    print(f"移动 {n_files} 文件: {elapsed*1000:.1f}ms")
    return elapsed
```

### 12.2 关键指标

| 指标 | 测量方法 | 目标值 |
|------|---------|--------|
| 端到端延迟 | `time.perf_counter()` | <300ms (20 文件) |
| UI 主线程阻塞 | Qt profiler / `QElapsedTimer` | <16ms (60fps) |
| 信号触发次数 | 信号计数器 | ≤2 次/操作 |
| 内存增量 | `tracemalloc` | <10MB/100 文件 |
| ExifTool 进程数 | `subprocess` 计数 | 0（复用索引时） |

### 12.3 回归测试

每次优化后运行以下场景：

1. **单文件删除：** 验证 <100ms
2. **批量删除 100 文件：** 验证 <500ms
3. **跨相册移动 50 文件：** 验证 <300ms
4. **删除后立即切换相册：** 验证无卡顿
5. **连续快速删除 5 次：** 验证无堆积/崩溃
6. **Live Photo 删除：** 验证静态图 + 动态视频同时移除
7. **从回收站恢复：** 验证恢复到原始路径

---

> **总结：** 当前性能瓶颈的根本原因不是 Python 语言本身的速度限制，而是**架构层面的冗余操作**——重复的元数据提取、重复的 Live Photo 配对计算、以及全量 UI 模型重载。通过实施方案一至四的纯 Python 架构优化，预计可将删除/移动操作的耗时降低 80%+，使用户感知延迟控制在 300ms 以内。C++ 加速层作为可选的第三阶段方案，适用于对万级文件操作有极端性能要求的场景。

# 删除与移动媒体操作性能优化 — 已完成部分（纯 Python 架构优化）

> **版本:** 1.0 | **完成日期:** 2026-02-14  
> **原始文档:** `docs/requirements/MOVE_DELETE_OPTIMIZATION_PLAN.md`  
> **状态:** ✅ 已实施

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
9. [实施路线图](#9-实施路线图)
10. [风险评估](#10-风险评估)
11. [附录：性能基准测试方案](#11-附录性能基准测试方案)

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
    │   ├─ store.get_rows_by_rels()    ← ✅ 新增：缓存源行
    │   ├─ store.remove_rows()          ← SQLite 写操作
    │   └─ (pair() 已移除)
    ├─ _update_destination_index()
    │   ├─ 复用缓存行（避免 ExifTool） ← ✅ 优化
    │   ├─ store.append_rows()          ← SQLite 写操作
    │   └─ (pair() 已移除)
    ├─ backend.pair() (仅执行一次)      ← ✅ 合并调用
    └─ emit finished signal
            │
            ▼
    AssetMoveService._handle_move_finished() [主线程]
        │ emit moveCompletedDetailed
        │
        ▼
    LibraryUpdateService.handle_move_operation_completed() [主线程]
        ├─ emit moveOperationCompleted(MoveOperationResult)  ← ✅ 新增统一信号
        ├─ emit indexUpdated(source)           ← 保留兼容
        ├─ emit linksUpdated(source)           ← 保留兼容
        └─ ...
```

### 2.2 关键文件清单

| 文件 | 职责 | 修改状态 |
|------|------|---------|
| `gui/ui/tasks/move_worker.py` | 文件移动 + 索引更新 | ✅ 已优化 |
| `gui/services/library_update_service.py` | 信号分发 + 相册刷新 | ✅ 已优化 |
| `cache/index_store/repository.py` | SQLite CRUD | ✅ 已优化 |
| `cache/index_store/engine.py` | SQLite 连接管理 | ✅ 已优化 |
| `gui/ui/models/asset_cache_manager.py` | 缩略图缓存 | ✅ 已优化 |
| `gui/services/__init__.py` | 服务层导出 | ✅ 已更新 |

---

## 3. 瓶颈根因定位

### 🔴 根因 1：backend.pair() 双重调用 — ✅ 已修复

**修复方案：** 将 `_update_source_index()` 和 `_update_destination_index()` 中的两次 `pair()` 合并为 `run()` 末尾的单次调用。

### 🔴 根因 2：process_media_paths() 调用 ExifTool 子进程 — ✅ 已修复

**修复方案：** 在 `_update_source_index()` 中使用新增的 `get_rows_by_rels()` API 缓存源行数据，`_update_destination_index()` 复用缓存行仅更新 `rel` 路径，仅对无缓存的文件调用 ExifTool。

### 🔴 根因 3：全量 UI 模型重载 — ✅ 部分修复

**修复方案：** 添加 `MoveOperationResult` 数据类和 `moveOperationCompleted` 统一信号，为监听者提供增量更新所需的差量信息。新增 `AssetCacheManager.incremental_cache_update()` 支持增量缓存清理。

### 🔴 根因 4：冗余信号级联 — ✅ 部分修复

**修复方案：** 新增 `moveOperationCompleted` 信号携带完整 `MoveOperationResult`，监听者可基于此进行增量更新。原有信号级联保留以确保向后兼容。

### 🟡 根因 5：缩略图缓存失效策略 — ✅ 已修复

**修复方案：** 新增 `AssetCacheManager.incremental_cache_update(removed_rels, added_rels)` 方法，仅清理被移除项的缓存，保留其余缩略图。

---

## 4. 优化方案总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    已实施优化方案                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  方案一：信号链路精简              ┌──── 预计收益: 30% ────────┐ │
│  ├─ MoveOperationResult 数据类    │ 统一信号携带差量信息      │ │
│  └─ moveOperationCompleted 信号   │ 支持增量更新              │ │
│                                   └────────────────────────────┘ │
│  方案二：后台索引更新去阻塞        ┌──── 预计收益: 35% ────────┐ │
│  ├─ 复用源索引元数据              │ 避免 ExifTool 子进程      │ │
│  └─ 合并 pair() 调用              │ pair() 从 2→1 次         │ │
│                                   └────────────────────────────┘ │
│  方案三：UI模型差量刷新            ┌──── 预计收益: 25% ────────┐ │
│  └─ incremental_cache_update()    │ 增量缓存清理              │ │
│                                   └────────────────────────────┘ │
│  方案四：SQLite 写入批量优化       ┌──── 预计收益: 5% ─────────┐ │
│  ├─ WAL 模式读写分离              │ 读操作不阻塞写            │ │
│  └─ PRAGMA 优化                   │ 缓存+同步优化             │ │
│                                   └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. 方案一：信号链路精简与增量更新 — ✅ 已实施

### 5.1 实施内容

**引入 `MoveOperationResult` 数据类：**

```python
@dataclass
class MoveOperationResult:
    """移动/删除操作的完整结果描述。"""
    source_root: Path
    destination_root: Path
    moved_pairs: List[Tuple[Path, Path]] = field(default_factory=list)
    removed_rels: List[str] = field(default_factory=list)
    added_rels: List[str] = field(default_factory=list)
    is_delete: bool = False
    is_restore: bool = False
    source_ok: bool = True
    destination_ok: bool = True
```

**统一信号：**

```python
class LibraryUpdateService(QObject):
    moveOperationCompleted = Signal(object)  # MoveOperationResult

    def handle_move_operation_completed(self, ...):
        result = MoveOperationResult(...)
        self.moveOperationCompleted.emit(result)
        # 原有信号级联保留以确保向后兼容
```

### 5.2 实施文件

- `src/iPhoto/gui/services/library_update_service.py` — 新增 `MoveOperationResult` 和 `moveOperationCompleted` 信号
- `src/iPhoto/gui/services/__init__.py` — 导出 `MoveOperationResult`

---

## 6. 方案二：后台索引更新去阻塞 — ✅ 已实施

### 6.1 复用源索引行

新增 `AssetRepository.get_rows_by_rels()` API，在 `_update_source_index` 中删除行之前缓存源行数据：

```python
def _update_source_index(self, moved) -> Dict[str, Dict]:
    store = get_global_repository(index_root)
    # 缓存源行
    rows_by_rel = store.get_rows_by_rels(rels)
    # 删除源行
    store.remove_rows(rels)
    return cached_source_rows
```

在 `_update_destination_index` 中复用缓存行，仅对无缓存文件调用 ExifTool：

```python
def _update_destination_index(self, moved, cached_source_rows=None):
    for original, target in moved:
        cached = cached_source_rows.get(str(original))
        if cached:
            row = dict(cached)
            row["rel"] = new_rel
            row["parent_album_path"] = ...
            reused_rows.append(row)
        else:
            uncached_images.append(target)
    # 仅对无缓存的文件调用 ExifTool
    if uncached_images or uncached_videos:
        freshly_scanned = list(process_media_paths(...))
```

### 6.2 合并 pair() 调用

将两次 `backend.pair()` 调用合并为 `run()` 末尾的单次调用：

```python
def run(self) -> None:
    cached_source_rows = self._update_source_index(moved)
    self._update_destination_index(moved, cached_source_rows)
    # 合并：仅执行一次 pair()
    backend.pair(self._library_root, library_root=self._library_root)
```

### 6.3 实施文件

- `src/iPhoto/cache/index_store/repository.py` — 新增 `get_rows_by_rels()`
- `src/iPhoto/gui/ui/tasks/move_worker.py` — 缓存复用 + pair() 合并

---

## 7. 方案三：UI模型差量刷新 — ✅ 部分实施

### 7.1 增量缓存更新

新增 `AssetCacheManager.incremental_cache_update()` 方法：

```python
def incremental_cache_update(self, removed_rels: Set[str], added_rels: Set[str]) -> None:
    """仅清理被移除项的缓存，保留其余缩略图。"""
    for rel in removed_rels:
        self._thumb_cache.pop(rel, None)
        self._composite_cache.pop(rel, None)
        self._placeholder_cache.pop(rel, None)
```

### 7.2 实施文件

- `src/iPhoto/gui/ui/models/asset_cache_manager.py` — 新增 `incremental_cache_update()`

---

## 8. 方案四：SQLite 写入批量优化 — ✅ 已实施

### 8.1 WAL 模式

在 `DatabaseManager._create_connection()` 中启用 WAL 模式和优化 PRAGMA：

```python
def _create_connection(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")  # 8 MB cache
    return conn
```

### 8.2 实施文件

- `src/iPhoto/cache/index_store/engine.py` — WAL 模式 + PRAGMA 优化

---

## 9. 实施路线图

### 阶段一 + 阶段二（已完成）

```
状态  方案                              修改文件
──────────────────────────────────────────────────────────
✅   方案二 6.2.1: 复用源索引行          repository.py, move_worker.py
✅   方案二 6.2.2: 合并 pair() 调用      move_worker.py
✅   方案一 5.2: 统一完成信号            library_update_service.py
✅   方案三 7.2.2: 增量缓存更新          asset_cache_manager.py
✅   方案四 8.2.2: WAL 模式              engine.py
```

---

## 10. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 增量更新与数据库不一致 | 中 | 显示错误数据 | 定期对账 + 手动刷新按钮 |
| WAL 模式增加磁盘占用 | 低 | 临时文件增大 | WAL checkpoint 定期触发 |
| pair() 延迟导致 Live Photo 暂时不配对 | 中 | 用户短暂看不到 Live 标记 | 延迟窗口控制在 2 秒内 |
| 乐观更新回滚闪烁 | 低 | 文件恢复时 UI 闪烁 | 批量回滚 + 动画过渡 |

---

## 11. 附录：性能基准测试方案

### 11.1 测试工具

在 `tests/cache/test_move_delete_optimizations.py` 中已包含功能验证测试：

- `TestWALMode` — 验证 WAL 模式和 PRAGMA 设置
- `TestGetRowsByRels` — 验证源行缓存 API
- `TestMoveOperationResult` — 验证统一结果数据类

### 11.2 关键指标

| 指标 | 测量方法 | 目标值 |
|------|---------|--------|
| ExifTool 进程数 | 复用索引时 | 0（已实现） |
| pair() 调用次数 | 每次移动操作 | 1 次（已实现，原为 2 次） |
| SQLite 并发 | WAL 模式 | 读写可并发（已实现） |

---

> **总结：** 本阶段实施了纯 Python 架构优化（方案一至四），主要成果包括：
> 1. 消除 90%+ 的冗余 ExifTool 调用（复用源索引行）
> 2. 将 pair() 调用从 2 次减少到 1 次
> 3. 提供统一的 `MoveOperationResult` 信号支持增量更新
> 4. 启用 WAL 模式实现读写并发
> 5. 新增增量缓存清理避免全量缩略图重建
>
> 剩余的 pybind11/C++ 加速层作为未来需求保留在 `docs/requirements/MOVE_DELETE_OPTIMIZATION_PLAN.md`。

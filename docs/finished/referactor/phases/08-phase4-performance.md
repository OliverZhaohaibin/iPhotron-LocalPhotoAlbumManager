# 08 — 阶段四：性能优化

> 目标：并行扫描、三级缩略图缓存、内存治理、GPU 管线优化。  
> 时间：3-4 周  
> 风险：🟠 中（性能变更需充分压测）  
> 前置：阶段三基本完成

---

## 1. 并行扫描优化

### 1.1 当前问题

```
当前扫描流程: 串行处理
- 10,000 文件: ~85秒
- 100,000 文件: ~15分钟
- 瓶颈: ExifTool 子进程调用为串行
- UI 阻塞: 扫描期间 UI 冻结 ~8秒
```

### 1.2 目标架构

```mermaid
graph TB
    subgraph "当前：串行扫描 ⚠️"
        S_Start["开始扫描"]
        S_Walk["遍历文件系统<br/>(串行)"]
        S_Meta["读取元数据<br/>(串行 ExifTool)"]
        S_DB["写入数据库<br/>(串行)"]
        S_End["完成"]

        S_Start --> S_Walk --> S_Meta --> S_DB --> S_End
    end

    subgraph "目标：并行扫描 ✅"
        P_Start["开始扫描"]
        P_Walk["遍历文件系统<br/>(生成器)"]
        P_Queue["文件队列"]

        P_W1["Worker 1<br/>ExifTool"]
        P_W2["Worker 2<br/>ExifTool"]
        P_W3["Worker 3<br/>ExifTool"]
        P_W4["Worker 4<br/>ExifTool"]

        P_Batch["批量写入<br/>100条/批"]
        P_End["完成"]

        P_Start --> P_Walk --> P_Queue
        P_Queue --> P_W1
        P_Queue --> P_W2
        P_Queue --> P_W3
        P_Queue --> P_W4
        P_W1 --> P_Batch
        P_W2 --> P_Batch
        P_W3 --> P_Batch
        P_W4 --> P_Batch
        P_Batch --> P_End
    end

    style S_Walk fill:#ff6b6b,color:#fff
    style S_Meta fill:#ff6b6b,color:#fff
    style P_W1 fill:#51cf66,color:#fff
    style P_W2 fill:#51cf66,color:#fff
    style P_W3 fill:#51cf66,color:#fff
    style P_W4 fill:#51cf66,color:#fff
    style P_Batch fill:#fcc419,color:#333
```

### 1.3 实施方案

```python
# src/iPhoto/application/services/parallel_scanner.py
class ParallelScanner:
    """并行文件扫描器"""

    def __init__(
        self,
        max_workers: int = 4,
        batch_size: int = 100,
        event_bus: EventBus | None = None,
    ):
        self._max_workers = max_workers
        self._batch_size = batch_size
        self._event_bus = event_bus

    def scan(self, album_path: Path) -> ScanResult:
        files = list(self._discover_files(album_path))
        total = len(files)

        results: list[Asset] = []
        errors: list[tuple[Path, str]] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._scan_file, f): f
                for f in files
            }

            for i, future in enumerate(as_completed(futures)):
                path = futures[future]
                try:
                    asset = future.result()
                    results.append(asset)
                except Exception as e:
                    errors.append((path, str(e)))

                # 进度通知
                if self._event_bus and (i + 1) % self._batch_size == 0:
                    self._event_bus.publish(ScanProgressEvent(
                        processed=i + 1,
                        total=total,
                    ))

        return ScanResult(assets=results, errors=errors)

    def _discover_files(self, path: Path) -> Generator[Path, None, None]:
        """使用生成器遍历，减少内存占用"""
        for entry in os.scandir(path):
            if entry.is_file() and self._is_supported(entry.name):
                yield Path(entry.path)
            elif entry.is_dir() and not entry.name.startswith('.'):
                yield from self._discover_files(Path(entry.path))
```

### 1.4 SQLite 批量写入

```python
# src/iPhoto/infrastructure/repositories/sqlite_asset_repository.py
class SQLiteAssetRepository:
    def batch_insert(self, assets: list[Asset]) -> int:
        """批量插入 — WAL 模式 + 事务"""
        with self._pool.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                "INSERT OR REPLACE INTO assets (id, filename, ...) VALUES (?, ?, ...)",
                [(a.id, a.filename, ...) for a in assets]
            )
            return len(assets)
```

### 1.5 性能目标

| 文件数 | 当前 | 目标 | 提升 |
|--------|------|------|------|
| 1,000 | ~8秒 | ≤3秒 | 62% |
| 10,000 | ~85秒 | ≤30秒 | 65% |
| 100,000 | ~15分钟 | ≤5分钟 | 67% |

---

## 2. 三级缩略图缓存

### 2.1 缓存架构

```mermaid
graph TB
    subgraph "三级缓存架构"
        Request2["缩略图请求<br/>asset_id + size"]

        subgraph "L1: 内存 LRU 缓存"
            L1["LRU Cache<br/>≤500 条目<br/>~200MB<br/>命中率: ~70%"]
        end

        subgraph "L2: 磁盘缓存"
            L2["SQLite + 文件<br/>.thumbnails/ 目录<br/>JPEG 质量 85%<br/>命中率: ~25%"]
        end

        subgraph "L3: 实时生成"
            L3["Pillow / FFmpeg<br/>后台线程生成<br/>生成后回填 L2→L1"]
        end

        Request2 --> L1
        L1 -->|"Miss"| L2
        L2 -->|"Miss"| L3
        L3 -->|"回填"| L2
        L2 -->|"回填"| L1
    end

    style L1 fill:#51cf66,color:#fff
    style L2 fill:#fcc419,color:#333
    style L3 fill:#ff922b,color:#fff
```

### 2.2 L1 内存缓存

```python
# src/iPhoto/infrastructure/services/thumbnail_cache.py
from functools import lru_cache
from collections import OrderedDict

class MemoryThumbnailCache:
    """L1: LRU 内存缓存"""

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> bytes | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, data: bytes) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # 淘汰最久未用
        self._cache[key] = data

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def memory_usage_bytes(self) -> int:
        return sum(len(v) for v in self._cache.values())
```

### 2.3 L2 磁盘缓存

```python
# src/iPhoto/infrastructure/services/disk_thumbnail_cache.py
class DiskThumbnailCache:
    """L2: 磁盘缓存"""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> bytes | None:
        path = self._key_to_path(key)
        if path.exists():
            return path.read_bytes()
        return None

    def put(self, key: str, data: bytes) -> None:
        path = self._key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _key_to_path(self, key: str) -> Path:
        # 使用 hash 分桶，避免单目录文件过多
        hash_hex = hashlib.md5(key.encode()).hexdigest()
        return self._cache_dir / hash_hex[:2] / f"{hash_hex}.jpg"
```

### 2.4 统一缩略图服务

```python
# src/iPhoto/infrastructure/services/thumbnail_service.py
class ThumbnailService:
    """三级缓存统一入口"""

    def __init__(
        self,
        memory_cache: MemoryThumbnailCache,
        disk_cache: DiskThumbnailCache,
        generator: ThumbnailGenerator,
        executor: ThreadPoolExecutor,
    ):
        self._l1 = memory_cache
        self._l2 = disk_cache
        self._generator = generator
        self._executor = executor

    def get_thumbnail(self, asset_id: str, size: tuple[int, int] = (256, 256)) -> bytes | None:
        key = f"{asset_id}_{size[0]}x{size[1]}"

        # L1: 内存
        data = self._l1.get(key)
        if data:
            return data

        # L2: 磁盘
        data = self._l2.get(key)
        if data:
            self._l1.put(key, data)  # 回填 L1
            return data

        return None  # L3 需异步生成

    def request_thumbnail(self, asset_id: str, size: tuple[int, int], callback: Callable):
        """异步请求（L3 生成）"""
        self._executor.submit(self._generate_and_cache, asset_id, size, callback)

    def _generate_and_cache(self, asset_id: str, size: tuple[int, int], callback: Callable):
        key = f"{asset_id}_{size[0]}x{size[1]}"
        data = self._generator.generate(asset_id, size)
        if data:
            self._l2.put(key, data)  # 回填 L2
            self._l1.put(key, data)  # 回填 L1
            callback(asset_id, data)
```

---

## 3. 内存治理

### 3.1 内存问题诊断

```
当前内存使用 (100K 文件相册):
- 资产列表加载: ~2GB (全部加载到内存)
- 缩略图缓存: ~3GB (无上限)
- 元数据缓存: ~500MB
- 总计峰值: 5-10GB
```

### 3.2 优化策略

```mermaid
graph TB
    subgraph "内存优化策略"
        V["虚拟化列表<br/>仅加载可见区域"]
        P["分页加载<br/>每页 200 条"]
        LRU["LRU 缓存<br/>上限 500 条目"]
        WR["弱引用<br/>非活跃对象自动释放"]
        LP["惰性属性<br/>按需加载元数据"]
    end

    style V fill:#51cf66,color:#fff
    style P fill:#51cf66,color:#fff
    style LRU fill:#fcc419,color:#333
    style WR fill:#fcc419,color:#333
    style LP fill:#fcc419,color:#333
```

### 3.3 虚拟化列表

```python
# src/iPhoto/gui/ui/widgets/virtual_grid.py
class VirtualAssetGrid(QAbstractScrollArea):
    """虚拟化网格 — 仅渲染可见区域"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total_count = 0
        self._item_size = QSize(200, 200)
        self._visible_range: tuple[int, int] = (0, 0)

    def set_total_count(self, count: int):
        self._total_count = count
        self._update_scrollbar()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        first, last = self._calculate_visible_range()

        for i in range(first, min(last + 1, self._total_count)):
            rect = self._item_rect(i)
            self._paint_item(painter, i, rect)

    def _calculate_visible_range(self) -> tuple[int, int]:
        """计算当前可见的 item 索引范围"""
        scroll_y = self.verticalScrollBar().value()
        viewport_height = self.viewport().height()
        cols = max(1, self.viewport().width() // self._item_size.width())

        first_row = scroll_y // self._item_size.height()
        last_row = (scroll_y + viewport_height) // self._item_size.height() + 1

        return first_row * cols, (last_row + 1) * cols
```

### 3.4 内存目标

| 场景 | 当前内存 | 目标内存 | 减少 |
|------|---------|---------|------|
| 10K 文件相册 | ~1.5GB | ≤500MB | 67% |
| 100K 文件相册 | ~5-10GB | ≤2GB | 60-80% |
| 缩略图缓存 | 无上限 | ≤200MB | 有界 |

---

## 4. GPU 管线优化

### 4.1 当前问题

```
gl_renderer.py (940行):
- 着色器编译在主线程
- 纹理上传未分批
- 无 FBO 缓存
- 视口变化重建整个管线
```

### 4.2 优化方向

```mermaid
graph TB
    subgraph "GPU 优化"
        SO["着色器预编译<br/>启动时编译所有着色器"]
        TU["纹理流式上传<br/>分块传输大图"]
        FBO["FBO 缓存池<br/>复用 FrameBuffer"]
        LOD["LOD 渲染<br/>远距离低分辨率"]
    end

    style SO fill:#51cf66,color:#fff
    style TU fill:#fcc419,color:#333
    style FBO fill:#fcc419,color:#333
    style LOD fill:#74c0fc,color:#333
```

---

## 5. 阶段四检查清单

- [ ] **并行扫描**
  - [ ] 实现 `ParallelScanner` (4 Worker)
  - [ ] 实现 `batch_insert` 批量写入 (100条/批)
  - [ ] SQLite WAL 模式启用
  - [ ] 进度事件发布 (ScanProgressEvent)
  - [ ] 压测: 10K 文件 ≤30秒
- [ ] **三级缩略图缓存**
  - [ ] 实现 `MemoryThumbnailCache` (L1, LRU 500)
  - [ ] 实现 `DiskThumbnailCache` (L2, hash 分桶)
  - [ ] 实现 `ThumbnailService` (统一入口)
  - [ ] 异步 L3 生成 + 回填
  - [ ] 缓存命中率监控
- [ ] **内存治理**
  - [ ] 虚拟化列表 `VirtualAssetGrid`
  - [ ] 分页加载 (200条/页)
  - [ ] 缩略图缓存上限 (200MB)
  - [ ] 弱引用非活跃对象
  - [ ] 内存使用监控 (≤2GB @100K)
- [ ] **GPU 优化**
  - [ ] 着色器预编译
  - [ ] 纹理流式上传
  - [ ] FBO 缓存池

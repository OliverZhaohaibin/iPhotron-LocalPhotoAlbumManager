# 大型相册性能迁移开发实施指南

> **版本:** 1.0 | **日期:** 2026-05-30  
> **状态:** 已归档；核心阶段已完成并进入生产约束
> **关联规格:** `docs/finished/requirements/large_library_performance/REARCHITECTURE.md`
> **目标读者:** 负责扫描、数据库、Gallery UI、缩略图、启动流程和性能测试的开发人员

---

## 1. 开发目标与交付边界

本文档是大型相册性能迁移的执行手册。架构目标、SLO、字段约束和行业参考以 `REARCHITECTURE.md` 为准；本文只回答开发人员如何分阶段下手、每阶段改哪些模块、接口如何落地、测试如何证明完成。

### 1.1 总体交付目标

- All Photos、album、favorites、videos、GPS/date range 等常见 collection 不再依赖全量读取或内存过滤。
- Gallery model 不再因 reset、snapshot hash、row lookup、live partner lookup 遍历全库。
- 扫描过程分阶段 job 化，并能向 UI 实时或接近实时发布已完成缩略图的 ready 媒体。
- 普通媒体 grid 只展示 `thumbnail_state='ready'` 的资产；metadata-only row 只能作为内部 staging 状态。
- 首次启动只完成窗口首帧、library session 绑定和首屏查询；重任务延后到 idle 或用户进入相关页面。
- 大型库性能通过 synthetic 10k/100k/1M rows 和 `tools/testbase` opt-in 测试持续回归。

### 1.2 不在本轮实施指南范围

- 不要求切换到 PostgreSQL。
- 不要求一次性重写所有 GUI。
- 不要求 People/OCR/相似度识别立刻迁移，只需要避免它们抢占首屏和浏览资源。
- 不要求删除现有 `AssetQuery`，可以通过兼容 adapter 逐步迁移到 `CollectionQuery`。

### 1.3 当前代码起点

开发时优先从以下模块落地：

- `src/iPhoto/cache/index_store/`: SQLite schema、query、repository、scan merge。
- `src/iPhoto/bootstrap/library_asset_query_service.py`: session 级 collection 查询入口。
- `src/iPhoto/bootstrap/library_scan_service.py`: session 级扫描入口。
- `src/iPhoto/gui/viewmodels/gallery_collection_store.py`: Gallery 窗口化缓存。
- `src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py`: Qt model adapter、reset、thumbnail role。
- `src/iPhoto/infrastructure/services/thumbnail_cache_service.py`: L1/L2 缩略图与异步生成。
- `src/iPhoto/gui/main.py`、`src/iPhoto/bootstrap/runtime_context.py`: 启动延迟加载。
- `tests/performance/`: 性能基线和大库压力测试。

---

## 2. 推荐开发顺序

严格按以下顺序实施，避免先改扫描导致 UI 和查询层承载不了增量数据。

| Phase | 主题 | 推荐 PR 数 | 必须先完成的原因 |
| --- | --- | ---: | --- |
| Phase 0 | 性能观测与审计保护 | 1-2 | 先知道全量读取、慢查询和 UI stall 在哪里发生 |
| Phase 1 | `CollectionQuery` 与 SQL 下推 | 2-3 | 所有大库性能都依赖可分页、可索引的查询入口 |
| Phase 2 | Gallery model 去全量化 | 2-3 | 防止 UI reset 和滚动路径把分页收益抵消 |
| Phase 3 | 缩略图 ready 入库 | 2-4 | 保证扫描发布到 UI 的 row 已可显示 |
| Phase 4 | 扫描 job 化与实时发布 | 3-5 | 在查询和缩略图基础完成后做实时扫描体验 |
| Phase 5 | 启动延迟加载 | 1-2 | 将重任务从首屏移走 |
| Phase 6 | 大库压测与回归门禁 | 1-3 | 把性能目标固定为可持续验证 |

每个 Phase 都必须保持应用可运行。禁止在没有兼容层的情况下让旧 album、favorite、move/delete、People、Maps 功能整体失效。

---

## 3. 目标接口草案

以下接口是后续开发的目标契约。实现时应优先放入 application/domain 边界，例如 `src/iPhoto/domain/models/query.py`、`src/iPhoto/application/ports/` 或新的 application DTO 模块，不要放进 GUI 或 concrete repository 内部。

### 3.1 CollectionQuery

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CollectionType(str, Enum):
    ALL_PHOTOS = "all_photos"
    ALBUM = "album"
    FAVORITES = "favorites"
    VIDEOS = "videos"
    MAP = "map"
    PEOPLE = "people"
    SEARCH = "search"


class SortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


@dataclass(frozen=True)
class CollectionQuery:
    collection_type: CollectionType = CollectionType.ALL_PHOTOS
    album_path: str | None = None
    include_subalbums: bool = True
    media_types: tuple[int, ...] = ()
    is_favorite: bool | None = None
    has_gps: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    search_text: str | None = None
    sort_key: str = "sort_ts"
    sort_direction: SortDirection = SortDirection.DESC
    min_thumbnail_state: str = "ready"
```

开发约束：

- `CollectionQuery` 是 view intent，不携带 repository 或 GUI 对象。
- 所有常见 collection 必须能被转换成 SQL where/order/limit。
- `min_thumbnail_state` 默认 `ready`，普通媒体 grid 不应请求 pending/failed/stale rows。

### 3.2 PageCursor / PageResult / WindowResult

```python
@dataclass(frozen=True)
class PageCursor:
    sort_ts: int
    asset_id: str


@dataclass(frozen=True)
class PageResult:
    rows: list["AssetSummaryDTO"]
    next_cursor: PageCursor | None
    total_count: int | None
    collection_revision: int


@dataclass(frozen=True)
class WindowResult:
    first: int
    rows: list["AssetSummaryDTO"]
    total_count: int
    collection_revision: int
```

开发约束：

- 深分页必须优先使用 keyset cursor。
- `WindowResult` 是 Gallery model 的窗口数据源，不能要求 materialize 全库。
- `collection_revision` 用于判断局部刷新，不允许通过遍历全量 row 计算 snapshot hash。

### 3.3 ThumbnailState / ThumbnailCacheKey

```python
class ThumbnailState(str, Enum):
    READY = "ready"
    PENDING = "pending"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class ThumbnailCacheKey:
    asset_id: str
    content_signature: str
    size: tuple[int, int]
    edit_revision: str
    renderer_version: str
```

开发约束：

- `READY` row 必须有 `micro_thumbnail` 或 `thumb_cache_key`。
- `FAILED` row 必须有可诊断错误信息和 retry 策略。
- `STALE` row 只用于旧库迁移和后台 backfill，不进入普通媒体 grid。

### 3.4 ScanJob / ScanStage / ScanBatchCommitted

```python
class ScanStage(str, Enum):
    DISCOVER = "discover"
    STAT_CACHE = "stat_cache_validation"
    METADATA = "metadata_extraction"
    THUMBNAIL = "thumbnail_extraction"
    DB_COMMIT = "db_commit"
    VISIBLE_PUBLISH = "visible_publish"
    DERIVED_JOBS = "derived_jobs_enqueue"


@dataclass(frozen=True)
class ScanJob:
    job_id: str
    root: str
    scope: str
    status: str
    stage: ScanStage
    found_count: int = 0
    processed_count: int = 0
    visible_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True)
class AssetSummaryDTO:
    id: str
    rel: str
    parent_album_path: str
    sort_ts: int
    media_type: int
    live_role: int
    is_favorite: bool
    thumbnail_state: ThumbnailState
    micro_thumbnail: bytes | None
    thumb_cache_key: str | None


@dataclass(frozen=True)
class ScanBatchCommitted:
    job_id: str
    root: str
    collection_revision: int
    ready_count: int
    rows: list[AssetSummaryDTO]
    stage_elapsed_ms: dict[str, float]
```

开发约束：

- `ScanBatchCommitted.rows` 只能包含 `thumbnail_state=READY` 的 row。
- UI 接收到 batch 后只做局部插入或局部 dataChanged，不做全量 reset。
- 扫描 job 进度要区分 discover、metadata、thumbnail、db commit、visible publish。

---

## 4. Phase 0: 性能观测与审计保护

### 4.1 目标行为

在改架构前先把慢路径暴露出来。开发人员应能从日志和测试中看到：

- 哪些 GUI collection 路径调用了 `read_all()`。
- 每个 collection count/page/window query 的耗时和 query plan。
- Gallery model reset 是否 materialize 全量 row。
- 缩略图队列深度、命中率、失败率。
- 扫描阶段耗时和 visible publish 延迟。
- UI 线程是否出现 > 50ms stall。

### 4.2 主要涉及模块

- `src/iPhoto/cache/index_store/repository.py`
- `src/iPhoto/cache/index_store/queries.py`
- `src/iPhoto/bootstrap/library_asset_query_service.py`
- `src/iPhoto/gui/viewmodels/gallery_collection_store.py`
- `src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py`
- `src/iPhoto/infrastructure/services/thumbnail_cache_service.py`
- `src/iPhoto/bootstrap/runtime_context.py`
- `tests/performance/`

### 4.3 新增/修改接口

- 新增轻量性能 logger，例如 `iPhoto.infrastructure.services.performance_events`。
- 提供 `emit_perf_event(name: str, **payload)`。
- 提供环境开关：
  - `IPHOTO_PERF_LOG=1`: 输出结构化 JSONL。
  - `IPHOTO_FAIL_ON_FULL_SCAN_QUERY=1`: 测试中发现 GUI collection 调用 `read_all()` 时失败。

### 4.4 开发步骤

1. 新增结构化事件 helper，不改变业务行为。
2. 在 repository 查询入口记录：
   - query type
   - collection scope
   - elapsed ms
   - row count
   - limit/cursor/offset
   - optional `EXPLAIN QUERY PLAN`
3. 在 `read_all()` 加调用来源审计：
   - 普通兼容路径只 warning。
   - GUI collection 路径在测试开关开启时 raise/assert。
4. 在 Gallery model reset 和 window reload 记录：
   - total count
   - requested window
   - materialized row count
   - elapsed ms
5. 在 thumbnail service 记录：
   - L1/L2 hit/miss
   - pending count
   - generate started/finished/failed
6. 在 scan service 记录阶段耗时和 chunk/batch 大小。

### 4.5 禁止事项

- 不要在 Phase 0 改 schema。
- 不要改变扫描和 UI 刷新行为。
- 不要让生产环境默认输出大量 JSONL；必须有开关或采样。

### 4.6 完成标准

- 能通过日志定位一次 All Photos 打开过程中是否调用 `read_all()`。
- 能看见 Gallery reset materialized rows 数量。
- 能看见 thumbnail hit/miss/generate 的统计。
- 性能日志默认不影响普通运行体验。

### 4.7 测试用例

- `test_perf_event_helper_emits_json_payload`
- `test_read_all_audit_warns_for_gui_collection_path`
- `test_collection_page_query_records_elapsed_and_rows`
- `test_gallery_reset_records_materialized_row_count`
- `test_thumbnail_service_records_hit_miss_generate`

### 4.8 回滚/兼容注意事项

- 所有观测点必须可关闭。
- 不改变 public behavior，回滚时只移除日志 helper 和调用点。

---

## 5. Phase 1: `CollectionQuery` 与 SQL 下推

### 5.1 目标行为

将 collection 查询统一到一个 SQL-first 的查询入口。All Photos、album、favorites、videos、GPS、date range 都应通过 count + page/window query 完成，不再读取全库后 Python 过滤。

### 5.2 主要涉及模块

- `src/iPhoto/domain/models/query.py`
- `src/iPhoto/application/ports/repositories.py`
- `src/iPhoto/bootstrap/library_asset_query_service.py`
- `src/iPhoto/cache/index_store/queries.py`
- `src/iPhoto/cache/index_store/repository.py`
- `tests/application/test_library_asset_query_service.py`
- `tests/cache/test_index_store_features.py`
- `tests/performance/test_refactor_performance_baseline.py`

### 5.3 新增/修改接口

- 新增 `CollectionQuery`、`PageCursor`、`PageResult`、`WindowResult`。
- 在 repository port 增加：
  - `count_collection(query: CollectionQuery) -> int`
  - `read_collection_page(query: CollectionQuery, cursor: PageCursor | None, limit: int) -> PageResult`
  - `read_collection_window(query: CollectionQuery, first: int, limit: int) -> WindowResult`
- `AssetQuery` 暂时保留，通过 adapter 转换到 `CollectionQuery`。

### 5.4 开发步骤

1. 新增 query DTO 和 port 方法，不删除旧方法。
2. 在 `QueryBuilder` 增加 collection where/order 构造器。
3. 把以下过滤 SQL 下推：
   - album path + include subalbums
   - live visible only
   - deleted/trash exclusion
   - media type
   - favorite true/false
   - has GPS true/false
   - date range
   - thumbnail ready only
4. 增加 keyset pagination：
   - 默认 order: `sort_ts DESC, id DESC`
   - cursor condition: `(sort_ts, id) < (?, ?)`
5. 只允许小 offset 窗口查询；深 offset 需要 anchor seek 或 cursor。
6. 在 `LibraryAssetQueryService` 中让简单 query 使用新 collection API。
7. 为旧 `read_query_asset_rows()` 保留兼容层，但内部优先走 collection API。

### 5.5 禁止事项

- 禁止为 date range、GPS、favorite false 退回 `_filtered_query_rows()` 全量过滤。
- 禁止 All Photos 首屏调用 `read_all()`。
- 禁止 page query 返回 `thumbnail_state!='ready'` 的普通媒体 row。

### 5.6 完成标准

- All Photos first page 只执行 count + page/window SQL。
- favorites/videos/GPS/date range 都有 SQL where。
- 100k synthetic rows 下 first page 和 next page 达到规格文档 SLO。
- `read_all()` 审计在 GUI collection 测试中不会触发。

### 5.7 测试用例

- `test_all_photos_first_page_uses_collection_page_query`
- `test_album_collection_uses_parent_album_path_index`
- `test_favorites_collection_sql_pushdown`
- `test_videos_collection_sql_pushdown`
- `test_gps_collection_sql_pushdown`
- `test_date_range_collection_sql_pushdown`
- `test_collection_page_filters_thumbnail_ready`
- `test_deep_offset_is_rejected_or_converted`

### 5.8 回滚/兼容注意事项

- 保留旧 `read_album_assets()`、`get_assets_page()`、`read_geometry_only()`，直到 Gallery 全部迁移。
- 新 DTO 可先作为 internal API 使用，再逐步提升到稳定 port。

---

## 6. Phase 2: Gallery model 去全量化

### 6.1 目标行为

Gallery 只加载可见窗口和少量 lookahead/lookbehind，不因 reset、snapshot hash、row lookup、live partner lookup materialize 全库。

### 6.2 主要涉及模块

- `src/iPhoto/gui/viewmodels/gallery_collection_store.py`
- `src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py`
- `src/iPhoto/gui/viewmodels/asset_dto_converter.py`
- `src/iPhoto/gui/coordinators/main_coordinator.py`
- `tests/gui/viewmodels/test_gallery_collection_store.py`
- `tests/gui/viewmodels/test_gallery_list_model_adapter.py`

### 6.3 新增/修改接口

- `GalleryCollectionStore` 增加 request generation id。
- `GalleryCollectionStore` 内部使用 `WindowResult`。
- `GalleryListModelAdapter` 使用 `collection_revision/window_range` 判断刷新。
- `LibraryAssetQueryService` 提供 `find_row_by_path()` 和 `find_live_partner()`。

### 6.4 开发步骤

1. 替换 `_snapshot_hash(count)`：
   - 不再遍历 `range(count)`。
   - 用 `(collection_id, collection_revision, total_count, window_range)`。
2. 在 store 中加入 request id：
   - 每次可见范围变化递增。
   - 异步或延迟查询返回时，如果 request id 过期则丢弃。
3. 给 `prioritize_rows()` 加 16-50ms 去抖或合并策略。
4. `_fetch_rows()` 改为请求 `WindowResult`。
5. `asset_at()` 只返回缓存 row 或稳定占位；不得同步触发深分页查询。
6. `row_for_path()` 改为 query service lookup，不再 batch 扫全库。
7. live partner 查找改为按 `asset_id/live_partner_rel` lookup。
8. UI 接收扫描 batch 后：
   - 如果影响当前窗口，局部插入或局部刷新。
   - 如果不影响当前窗口，只更新 count/revision。

### 6.5 禁止事项

- 禁止 reset 时计算全量 path hash。
- 禁止滚动时每个 row 单独发 DB 查询。
- 禁止 UI 线程做 path exists 全量校验。
- 禁止 live partner 查找循环 `for row in range(count)`。

### 6.6 完成标准

- 打开 100k All Photos 时 DTO cache 不超过窗口上限 + pinned rows。
- reset 不调用每个 row 的 `asset_at()`。
- 快速滚动 5k rows 时 DB 查询被合并为窗口请求。
- `row_for_path()` 对 100k rows 不线性扫描。

### 6.7 测试用例

- `test_model_reset_uses_revision_not_full_snapshot`
- `test_asset_at_does_not_fetch_deep_row_synchronously`
- `test_prioritize_rows_coalesces_window_requests`
- `test_row_for_path_uses_query_lookup`
- `test_live_partner_lookup_does_not_scan_model`
- `test_scan_batch_updates_visible_window_without_reset`

### 6.8 回滚/兼容注意事项

- 如果新 window API 未准备好，保留旧 `_fetch_rows()` fallback，但只在测试外或小库中允许。
- 加 feature flag 便于对比旧 window cache 与新 window cache。

---

## 7. Phase 3: 缩略图 ready 入库

### 7.1 目标行为

普通媒体 grid 只展示有可用缩略图的资产。扫描提交到可见索引的最小单位是 `metadata + thumbnail_state + (micro_thumbnail OR thumb_cache_key)`。

### 7.2 主要涉及模块

- `src/iPhoto/cache/index_store/migrations.py`
- `src/iPhoto/cache/index_store/row_mapper.py`
- `src/iPhoto/cache/index_store/scan_merge.py`
- `src/iPhoto/infrastructure/services/thumbnail_cache_service.py`
- `src/iPhoto/infrastructure/services/thumbnail_generator.py`
- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py`
- `tests/test_thumbnail_cache_service.py`
- `tests/test_scanner_adapter.py`
- `tests/cache/test_index_store_features.py`

### 7.3 新增/修改接口

- `ThumbnailState`
- `ThumbnailCacheKey`
- repository 字段：
  - `thumbnail_state`
  - `thumb_cache_key`
  - `thumb_updated_at`
  - `thumb_error`
- thumbnail service 增加：
  - `ensure_scan_thumbnail(path, asset_id, size) -> ThumbnailReadyResult`
  - `mark_failed(asset_id, error)`
  - `retry_failed(asset_id)`

### 7.4 开发步骤

1. 增加 schema 字段和索引。
2. 更新 row mapper，读写 thumbnail state 字段。
3. 扫描 normalize row 后立即准备 micro thumbnail 或 L2 disk thumbnail。
4. ready row 才进入 visible collection 查询。
5. failed row 写入 `thumbnail_state='failed'` 和 `thumb_error`。
6. stale row 用于旧库迁移，后台 backfill 后变 ready。
7. `GalleryListModelAdapter`：
   - DB 有 `micro_thumbnail` 时立即显示。
   - L1/L2 miss 时不显示普通空白媒体格。
8. thumbnail service 增加 pending 去重、LRU、失败冷却、并发上限。

### 7.5 禁止事项

- 禁止 metadata-only row 出现在普通 grid。
- 禁止 `None` pixmap 作为正常媒体展示结果。
- 禁止缩略图失败无限重试。
- 禁止滚动路径同步解码原图。

### 7.6 完成标准

- 所有 visible rows 均满足 `thumbnail_state='ready'`。
- ready row 均有 `micro_thumbnail` 或 `thumb_cache_key`。
- failed/stale/pending 不出现在普通 collection。
- L1/L2 hit 不触发 generator。
- 缩略图失败可诊断、可重试、有冷却。

### 7.7 测试用例

- `test_ready_row_requires_thumbnail_payload`
- `test_pending_rows_are_hidden_from_gallery_collection`
- `test_failed_rows_are_hidden_from_gallery_collection`
- `test_scan_generates_thumbnail_before_visible_commit`
- `test_l1_l2_hit_does_not_generate`
- `test_thumbnail_failure_sets_failed_state`
- `test_thumbnail_retry_clears_failed_state_on_success`

### 7.8 回滚/兼容注意事项

- 旧库迁移时不要把全部旧 row 立刻标 ready。
- 对旧 row 缺缩略图时标 stale，并优先 backfill 当前可见窗口。
- 如果 L2 cache key 版本变化，旧 cache 可以保留但不得污染新 key。

---

## 8. Phase 4: 扫描 job 化与实时发布

### 8.1 目标行为

扫描成为可观察、可取消、可恢复的 job。UI 在扫描中持续收到 ready 媒体批次，并能在 All Photos、当前相册、筛选视图增量感知更新。

### 8.2 主要涉及模块

- `src/iPhoto/bootstrap/library_scan_service.py`
- `src/iPhoto/application/use_cases/scan_library.py`
- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/cache/index_store/repository.py`
- `src/iPhoto/gui/viewmodels/gallery_collection_store.py`
- `src/iPhoto/gui/coordinators/main_coordinator.py`
- `tests/application/test_library_scan_service.py`
- `tests/library/test_scanner_worker.py`
- `tests/performance/`

### 8.3 新增/修改接口

- `ScanJob`
- `ScanStage`
- `ScanBatchCommitted`
- repository 增加 `create_scan_job()`、`update_scan_job_stage()`、`append_scan_event()`。
- scan service 增加：
  - `start_scan_job(root, scope, priority)`
  - `cancel_scan_job(job_id)`
  - `subscribe_scan_batches(callback)`

### 8.4 开发步骤

1. 增加 `scan_jobs` 和 `scan_events` 表。
2. 将扫描流程拆阶段记录：
   - discover
   - stat cache validation
   - metadata extraction
   - thumbnail extraction
   - db commit
   - visible publish
   - derived jobs enqueue
3. 将 DB commit 与 UI publish 分离：
   - DB commit 批量 500-2000 rows。
   - UI publish 每 100-250ms 或 50-200 ready rows 合并。
4. `ScanBatchCommitted` 只发布 ready rows。
5. Gallery store 判断 batch 是否影响当前 query：
   - 影响首屏或可见窗口：局部刷新。
   - 只影响总数：更新 count/revision。
6. 扫描失败不阻断整个 job：
   - metadata failed
   - thumbnail failed
   - db commit failed
   - visible publish failed
7. Live Photo 配对改为局部 bucket，不做全库 O(n*m)。

### 8.5 禁止事项

- 禁止扫描 finished 后一次性把全量 rows 发给 UI。
- 禁止小 chunk 每 10 行写一次 DB。
- 禁止 UI batch 包含 pending/failed/stale rows。
- 禁止扫描取消后继续发布旧 batch。

### 8.6 完成标准

- 扫描过程中 UI 可见 ready 媒体持续增加。
- 从 discover 到 visible publish P95 <= 500ms，最差 <= 2s。
- DB 写入事务批量默认 >= 500 rows。
- 取消扫描后不再发布旧 job batch。
- All Photos/current album/favorites/videos 都能接收相关 batch。

### 8.7 测试用例

- `test_scan_job_records_stage_transitions`
- `test_scan_batch_committed_contains_only_ready_rows`
- `test_scan_publish_is_incremental_not_finished_only`
- `test_cancelled_scan_does_not_publish_late_batches`
- `test_gallery_applies_scan_batch_without_reset`
- `test_scan_commit_batch_size_is_large_enough`
- `test_live_pairing_uses_bucketed_candidates`

### 8.8 回滚/兼容注意事项

- 初期可保留旧 `scanFinished` 信号，但新 UI 应优先消费 `scan_batch_committed`。
- 对 CLI 可以继续返回完整 `ScanLibraryResult`，但 GUI 不应依赖 finished 全量 rows。

---

## 9. Phase 5: 启动延迟加载

### 9.1 目标行为

启动路径只做首屏必需工作，重任务延后。恢复已有库时，用户应先看到可交互窗口和首屏 collection，而不是等待扫描、People、Maps、缩略图 warmup。

### 9.2 主要涉及模块

- `src/iPhoto/gui/main.py`
- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/bootstrap/library_session.py`
- `src/iPhoto/library/runtime_controller.py`
- `src/iPhoto/bootstrap/library_people_service.py`
- `src/iPhoto/infrastructure/services/map_runtime_service.py`
- `tests/application/test_runtime_context.py`
- `tests/test_app_open_album_lazy.py`

### 9.3 新增/修改接口

- `RuntimeContext.resume_startup_tasks()` 拆分：
  - `bind_startup_library()`
  - `open_startup_collection()`
  - `schedule_idle_jobs()`
- 增加 idle job scheduler 或复用现有 task scheduler。

### 9.4 开发步骤

1. 记录启动 stage 性能。
2. 保证 Qt window show 在 library heavy work 前完成。
3. library bind 只做：
   - session 创建
   - schema quick check
   -首屏 collection query
4. 如果已有 index：
   - 不做 full rescan。
   - schedule low priority incremental scan。
5. 如果没有 index：
   - progressive scan。
   - 不等待扫描完成才显示 UI。
6. People、Maps、Face scan、shader warmup、thumbnail warmup 全部 idle 或页面触发。
7. 自动扫描支持暂停、取消、降速。

### 9.5 禁止事项

- 禁止 startup 同步 full rescan。
- 禁止 startup 同步 People/Face scan。
- 禁止 startup 同步 Maps heavy probe。
- 禁止恢复库时同步 backfill 全部缩略图。

### 9.6 完成标准

- 主窗口首帧 <= 1.5s。
- 100k 已索引库恢复可交互 <= 3s。
- 启动日志能证明 heavy jobs 在 first paint 之后。
- 自动扫描在后台低优先级运行，不阻塞浏览。

### 9.7 测试用例

- `test_resume_startup_tasks_does_not_full_rescan_existing_index`
- `test_startup_schedules_incremental_scan_after_first_collection`
- `test_people_maps_not_initialized_before_first_paint`
- `test_empty_index_starts_progressive_scan_without_blocking_ui`

### 9.8 回滚/兼容注意事项

- 保留手动 rescan 行为，不受自动扫描降级影响。
- 如果 idle scheduler 不稳定，可先用 `QTimer.singleShot` 分阶段延迟。

---

## 10. Phase 6: 大库压测与回归门禁

### 10.1 目标行为

性能目标成为可持续测试，而不是一次性手工确认。

### 10.2 主要涉及模块

- `tests/performance/`
- `tools/benchmarks/`
- `pytest.ini`
- CI 配置
- synthetic data helper

### 10.3 开发步骤

1. 保留当前小数据 baseline。
2. 新增 synthetic 10k/100k/1M row 生成器。
3. 新增 query plan regression：
   - All Photos
   - album with subalbums
   - favorites
   - videos
   - GPS
   - date range
4. 新增 Gallery model-level scroll benchmark。
5. 新增 scan visible publish latency benchmark。
6. 新增 thumbnail ready invariant benchmark。
7. `tools/testbase` 继续 opt-in：
   - `IPHOTO_RUN_STRESS=1`
   - `IPHOTO_STRESS_TESTBASE=...`
8. 输出 JSON/CSV，便于后续比较。

### 10.4 禁止事项

- 禁止 CI 默认依赖真实素材。
- 禁止 benchmark 修改用户真实库。
- 禁止用过宽阈值掩盖全量读取回归。

### 10.5 完成标准

- CI 至少覆盖 10k 小型性能 sanity。
- 本地 opt-in 可跑 100k/1M synthetic。
- 任一 GUI collection 退回 `read_all()` 时测试失败。
- 输出包含耗时、rows、query plan、cache hit/miss、visible publish latency。

### 10.6 测试用例

- `test_100k_all_photos_first_page_baseline`
- `test_1m_keyset_pagination_baseline`
- `test_gallery_scroll_window_materialization_bound`
- `test_scan_visible_publish_latency_baseline`
- `test_thumbnail_ready_invariant_for_visible_rows`
- `test_query_plan_uses_visible_global_index`

### 10.7 回滚/兼容注意事项

- 大型 benchmark 默认 skip，只在环境变量开启。
- 小型 baseline 阈值可以宽松，但必须能捕获全量读取、全量 reset、cache hit 触发 generator 等明显退化。

---

## 11. 数据库迁移执行步骤

### 11.1 Schema version

新增或复用 schema version 表：

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
```

建议版本：

| Version | 内容 |
| ---: | --- |
| 1 | 当前 global index 基础表 |
| 2 | `sort_ts`, `has_gps`, `is_deleted`, `index_revision` |
| 3 | thumbnail state 字段 |
| 4 | scan jobs/events 表 |
| 5 | visible collection indexes |

### 11.2 迁移顺序

1. 停止扫描 job 和 thumbnail worker。
2. 关闭 active write transaction。
3. 备份 `.iPhoto/global_index.db`。
4. 开启 WAL。
5. 新增字段，所有字段必须有兼容默认值。
6. 分批 backfill，每批 1k-10k rows。
7. 创建索引。大库索引创建可能耗时，需记录进度或至少记录阶段日志。
8. 校验用户状态：
   - favorite
   - hidden
   - trash/recently deleted
   - manual metadata
9. 校验 visible invariant：
   - ready row 有 thumbnail。
   - pending/failed/stale 不出现在 ordinary collection query。
10. 写入 schema version。
11. 启动 thumbnail backfill low priority job。

### 11.3 旧资产处理

- 已有 `micro_thumbnail` 的旧资产可标记 `ready`。
- 没有 `micro_thumbnail` 且没有 L2 cache 的旧资产标记 `stale`。
- stale row 默认不进入普通 grid；但为避免旧库首屏空白，启动后应优先对当前窗口 backfill。
- backfill 成功后更新为 `ready` 并发布局部 UI batch。

### 11.4 回滚策略

- 迁移前必须备份旧 DB。
- 如果迁移失败：
  - 关闭新连接。
  - 恢复旧 DB。
  - 清理半成品 WAL/SHM 文件。
  - 启动旧查询路径。
- 如果 thumbnail backfill 失败：
  - 不回滚 DB。
  - 保留 stale/failed 状态。
  - 允许用户重试。

---

## 12. 开发 PR 拆分建议

### PR 1: 性能事件基础设施

- 新增 perf event helper。
- 添加 query/model/thumbnail/scan 关键观测点。
- 不改变业务行为。

### PR 2: `read_all()` 审计与小型回归测试

- 标记 GUI collection 路径中的全量读取。
- 加测试开关和断言。

### PR 3: `CollectionQuery` DTO 与 repository port

- 新增 DTO 和 port。
- 旧 `AssetQuery` adapter 转新 query。

### PR 4: SQL 下推与 keyset pagination

- 实现 collection where/order/page。
- 加 All Photos、album、favorites、videos、GPS、date range 测试。

### PR 5: Gallery revision reset

- 替换全量 snapshot hash。
- reset 基于 revision/window。

### PR 6: Gallery window request coalescing

- 增加 request id、去抖、窗口合并。
- `asset_at()` 不做深同步 fetch。

### PR 7: Path/live partner lookup

- SQL lookup 替代线性扫描。
- 加对应测试。

### PR 8: Thumbnail state schema

- 增加字段、mapper、默认迁移。
- visible query 默认只取 ready。

### PR 9: Thumbnail service 队列升级

- LRU、pending 去重、失败冷却、并发限制、disk key version。

### PR 10: Scanner ready row

- 扫描时先准备缩略图，再 visible commit。
- metadata-only row 进入 staging/pending，不进入普通 grid。

### PR 11: Scan jobs/events

- 增加 job 表、event 表、stage 记录。

### PR 12: Scan batch committed

- UI 小批 publish ready rows。
- Gallery 局部应用 batch。

### PR 13: Startup lazy binding

- 拆 first paint、session bind、first collection、idle tasks。
- 降低 People/Maps/thumbnail warmup 优先级。

### PR 14: Performance gates

- 10k CI sanity。
- 100k/1M opt-in benchmark。
- query plan regression。

---

## 13. 验收清单

### 13.1 查询层

- [ ] All Photos 首屏不调用 `read_all()`。
- [ ] Album include subalbums 使用索引查询。
- [ ] Favorites/videos/GPS/date range 均 SQL 下推。
- [ ] 深分页不使用大 OFFSET。
- [ ] 普通 collection 默认 `thumbnail_state='ready'`。

### 13.2 Gallery/UI

- [ ] Model reset 不遍历全库。
- [ ] 滚动只加载窗口 DTO。
- [ ] `asset_at()` 不触发深同步 DB 查询。
- [ ] `row_for_path()` 不扫描全库。
- [ ] live partner lookup 不扫描 model。
- [ ] 扫描 batch 局部刷新，不全量 reset。

### 13.3 缩略图

- [ ] visible row 必须有 `micro_thumbnail` 或 `thumb_cache_key`。
- [ ] metadata-only row 不出现在普通 grid。
- [ ] failed/stale/pending 不出现在普通 grid。
- [ ] L1/L2 hit 不触发 generator。
- [ ] 缩略图失败有错误信息、冷却和重试。

### 13.4 扫描

- [ ] Scan job 记录 stage。
- [ ] DB commit 默认批量 >= 500 rows。
- [ ] UI publish 100-250ms 合并或 50-200 ready rows 合并。
- [ ] `scan_batch_committed` 只包含 ready rows。
- [ ] 取消扫描后不发布旧 batch。
- [ ] Live Photo 配对不做全库 O(n*m)。

### 13.5 启动

- [ ] 主窗口首帧 <= 1.5s。
- [ ] 100k 已索引库恢复可交互 <= 3s。
- [ ] 启动不 full rescan 已有 index。
- [ ] People/Maps/Face scan/thumbnail warmup 延后。
- [ ] 无 index 库 progressive scan，不阻塞 UI。

### 13.6 性能测试

- [ ] 10k synthetic CI baseline。
- [ ] 100k All Photos first page <= 800ms。
- [ ] 1M All Photos first page <= 1.5s。
- [ ] 100k keyset next page P95 <= 80ms。
- [ ] 5k rows scroll P95 frame <= 24ms。
- [ ] scan visible publish P95 <= 500ms。
- [ ] `tools/testbase` opt-in 压测不修改原素材。

---

## 14. 开发注意事项

- 先加观测，再改行为。没有指标时不要猜测优化是否有效。
- 每个 Phase 都要保持旧路径兼容，直到新路径测试覆盖足够。
- 数据库迁移必须能重复执行、能中断恢复、能保留用户状态。
- 缩略图 ready 是 UI 可见的硬门槛，不是后台 nice-to-have。
- GUI 层不要直接依赖 SQLite repository；通过 `LibrarySession` 的 query/scan/thumbnail surface 调用。
- 性能测试要记录机器差异，但退回全量读取、全量 reset、cache hit 触发 generator 这类行为必须无条件失败。

---

## 15. 关联文档

- `docs/finished/requirements/large_library_performance/REARCHITECTURE.md`
- `docs/finished/refactor/vnext-2026-06/01-target-architecture-vnext.md`
- `docs/finished/refactor/vnext-2026-06/28-performance-baseline.md`
- [扫描阶段 C 语言热点代码优化分析](scan_c_hotspot_optimization.md)

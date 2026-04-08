# Phase 1-4 遗留集成 — 评估报告

> **日期**: 2026-02-16  
> **范围**: `docs/refactor/10-remaining-phase-integration.md` 中列出的所有 Phase 1-4 遗留集成任务  
> **状态**: ✅ 已完成（核心集成）  
> **前置条件**: Phase 1 (基础设施) ✅, Phase 2 (领域与应用) ✅, Phase 3 (GUI MVVM) ✅, Phase 4 (性能) ✅

---

## 执行摘要

本次集成将 Phase 1-4 中已实现但未接入生产代码的模块进行了全面集成。重点包括：

1. **5 个 P2 级 Use Cases** 全部实现并通过测试
2. **ParallelScanner 增强** — CPU 感知资源管理、流式扫描、取消支持
3. **性能模块服务集成** — WeakAssetCache → AssetService、ParallelScanner → LibraryService
4. **DI Bootstrap 扩展** — 注册所有性能基础设施为单例

**关键指标：**
- 35 个新增测试全部通过，0 回归
- 所有新模块均为纯 Python — 无需 QApplication 即可测试
- 完全向后兼容：现有服务签名保持不变（新参数均为可选）

---

## 1. Phase 2 遗留：P2 级 Use Cases ✅

### 1.1 任务清单对照

| Use Case | 文档要求 | 实现状态 | 文件位置 |
|----------|---------|---------|---------|
| `ManageTrashUseCase` | 从 `library/trash_manager.py` 提取 | ✅ 完整实现 | `application/use_cases/manage_trash.py` |
| `AggregateGeoDataUseCase` | 从 `library/geo_aggregator.py` 提取 | ✅ 完整实现 | `application/use_cases/aggregate_geo_data.py` |
| `WatchFilesystemUseCase` | 从 `library/filesystem_watcher.py` 提取 | ✅ 状态管理层 | `application/use_cases/watch_filesystem.py` |
| `ExportAssetsUseCase` | 从 `core/export.py` 提取 | ✅ 完整实现 | `application/use_cases/export_assets.py` |
| `ApplyEditUseCase` | 从 `core/*_resolver.py` 提取 | ✅ 完整实现 | `application/use_cases/apply_edit.py` |

**Use Case 总计**: 14/14 已实现（原有 9 个 + 新增 5 个）

### 1.2 各 Use Case 详细评估

#### ManageTrashUseCase ✅

| 需求 | 状态 | 说明 |
|------|------|------|
| 移入回收站（trash） | ✅ | `shutil.move` + 从仓储删除记录 |
| 从回收站恢复（restore） | ✅ | 文件移回相册目录 |
| 清理回收站（cleanup） | ✅ | 永久删除回收站中所有文件 |
| 碰撞安全命名 | ✅ | `_unique_path()` 添加计数后缀 |
| 可配置回收站目录 | ✅ | `trash_dir` 参数，默认 `.deleted` |
| 测试 | ✅ 7 个 | 移入、恢复、清理、重名碰撞、缺失资源跳过 |

#### AggregateGeoDataUseCase ✅

| 需求 | 状态 | 说明 |
|------|------|------|
| GPS 坐标聚合 | ✅ | 支持 `latitude`/`longitude` 和 `GPSLatitude`/`GPSLongitude` |
| 无效坐标过滤 | ✅ | `ValueError`/`TypeError` 静默跳过 |
| 位置名称提取 | ✅ | 从 `metadata.location_name` 读取 |
| `GeoAssetInfo` 数据类 | ✅ | `asset_id`, `latitude`, `longitude`, `location_name`, `path` |
| 测试 | ✅ 3 个 | GPS 聚合、空相册、无 GPS 排除 |

#### WatchFilesystemUseCase ✅

| 需求 | 状态 | 说明 |
|------|------|------|
| 开始/停止监听 | ✅ | `start`/`stop` action |
| 暂停/恢复 | ✅ | `pause`/`resume` action |
| 监听路径管理 | ✅ | `watched_paths` 集合维护 |
| OS 级监听 | ⚠️ 设计决策 | 状态管理层，实际监听由 `FileSystemWatcherMixin` 处理 |
| 测试 | ✅ 3 个 | 启动、停止、暂停/恢复 |

> **设计说明**: `WatchFilesystemUseCase` 被定位为状态管理用例，负责跟踪监听路径和暂停/恢复状态。
> 实际的 OS 级文件系统监听由 `library/filesystem_watcher.py` 中的 `FileSystemWatcherMixin`（基于 `QFileSystemWatcher`）处理。
> 这符合关注点分离原则 — Use Case 层不直接依赖 Qt。

#### ExportAssetsUseCase ✅

| 需求 | 状态 | 说明 |
|------|------|------|
| 文件复制导出 | ✅ | `shutil.copy2` 保留元数据 |
| 碰撞安全命名 | ✅ | `_unique_dest()` 添加计数后缀 |
| 可选渲染函数 | ✅ | `render_fn` 返回 bytes 时写入 `.jpg`，否则复制原文件 |
| 自动创建导出目录 | ✅ | `mkdir(parents=True, exist_ok=True)` |
| 错误隔离 | ✅ | 单个文件失败不影响其他文件 |
| 测试 | ✅ 5 个 | 复制、缺失资源、目录创建、碰撞处理、渲染函数 |

#### ApplyEditUseCase ✅

| 需求 | 状态 | 说明 |
|------|------|------|
| 调整参数验证 | ✅ | 空调整返回错误 |
| 资源/相册验证 | ✅ | 缺失资源/相册返回错误 |
| Sidecar 持久化 | ✅ | 通过注入的 `save_adjustments_fn` 写入 |
| 应用参数列表返回 | ✅ | `applied_adjustments` 字段 |
| 测试 | ✅ 3 个 | 成功应用、空调整、缺失资源 |

---

## 2. Phase 4 遗留：ParallelScanner 增强与集成 ✅

### 2.1 CPU 感知资源管理

| 需求 | 状态 | 说明 |
|------|------|------|
| Worker 数量 = `cpu_count // 2` | ✅ | `_default_max_workers()` 保留一半核心给 UI 线程 |
| 可配置 Worker 数 | ✅ | `max_workers` 参数，`None` 使用默认值 |
| 批间 GIL 释放 | ✅ | `yield_interval=0.005` 秒，可配置 |
| 文件发现期间可取消 | ✅ | `_discover_files()` 检查 `_cancelled` 标志 |

### 2.2 流式扫描

| 需求 | 状态 | 说明 |
|------|------|------|
| `scan_streaming()` 生成器 | ✅ | 每批次 yield 一个 `ScanResult` |
| 增量 UI 加载 | ✅ | 调用方可逐批更新 UI |
| 批次间进度事件 | ✅ | `ScanProgressEvent` 按 `batch_size` 间隔发布 |
| 取消支持 | ✅ | `cancel()` 设置 `threading.Event`，所有循环检查 |
| 线程安全文档 | ✅ | 类文档明确说明单实例不支持并发扫描 |

### 2.3 ParallelScanner 测试

| 测试 | 状态 | 说明 |
|------|------|------|
| 默认 Worker 数为 CPU 一半 | ✅ | `test_default_max_workers_is_half_cpus` |
| None 使用默认 Worker | ✅ | `test_scanner_uses_default_workers_when_none` |
| 取消中止扫描 | ✅ | `test_cancel_aborts_scan` |
| 流式扫描产出批次 | ✅ | `test_scan_streaming_yields_batches` |
| 进度事件发布 | ✅ | `test_progress_events_published` |

---

## 3. Phase 4 遗留：服务层集成 ✅

### 3.1 ParallelScanner → LibraryService

| 需求 | 状态 | 说明 |
|------|------|------|
| `scan_album_parallel()` | ✅ | 批量返回所有结果 |
| `scan_album_streaming()` | ✅ | 流式 yield + 自动持久化 |
| `cancel_scan()` | ✅ | UI 可中止扫描 |
| 向后兼容 | ✅ | `parallel_scanner=None` 时返回空结果 |
| 测试 | ✅ 4 个 | 并行扫描、流式持久化、无扫描器、取消 |

### 3.2 WeakAssetCache → AssetService

| 需求 | 状态 | 说明 |
|------|------|------|
| 缓存优先查找 | ✅ | `get_asset()` 先查 WeakAssetCache |
| 缓存回填 | ✅ | 仓储查询结果自动放入缓存 |
| 突变时失效 | ✅ | `toggle_favorite()` 前后失效缓存 |
| 路径突变失效 | ✅ | `toggle_favorite_by_path()` 同样失效 |
| 无缓存兼容 | ✅ | `weak_cache=None` 时正常工作 |
| 测试 | ✅ 3 个 | 缓存命中、失效、无缓存 |

### 3.3 DI Bootstrap 扩展

| 注册服务 | 生命周期 | 配置 |
|----------|---------|------|
| `EventBus` | 单例 | 默认配置 |
| `CacheStatsCollector` | 单例 | 默认配置 |
| `MemoryThumbnailCache` | 单例 | `max_size=500` |
| `WeakAssetCache` | 单例 | `max_size=5000` |
| `MemoryMonitor` | 单例 | `warning=1GiB, critical=2GiB` |

测试: ✅ 2 个（服务注册验证、单例一致性验证）

---

## 4. 文档要求对照（10-remaining-phase-integration.md）

### 4.1 已完成任务

| 文档章节 | 任务 | 状态 | 说明 |
|----------|------|------|------|
| §1 P2 Use Cases | 5 个 P2 Use Case 实现 | ✅ | 全部实现并测试 |
| §3 ParallelScanner 集成 | 替换串行扫描逻辑 | ✅ | 集成到 LibraryService |
| §3 WeakAssetCache 集成 | 管理非活跃 Asset 引用 | ✅ | 集成到 AssetService |
| §3 MemoryMonitor 集成 | 启动时注册阈值告警 | ✅ | DI Bootstrap 注册 |
| §3 CacheStatsCollector 集成 | 接入 L1/L2 命中率监控 | ✅ | 已在 ThumbnailService 中接入（Phase 4 已完成） |

### 4.2 待完成任务（非本次范围）

| 文档章节 | 任务 | 状态 | 原因 |
|----------|------|------|------|
| §2.1 Qt ViewModel 迁移 | PureAssetListVM → AssetListVM | ❌ 未开始 | 涉及 Qt 依赖重构，需独立迭代 |
| §2.2 大文件拆分 | `facade.py` 733→200 行 | ❌ 未开始 | 高风险重构，需独立 PR |
| §2.2 大文件拆分 | `app.py` 580→300 行 | ❌ 未开始 | 高风险重构，需独立 PR |
| §2.3 MainCoordinator 精简 | 提取 AppBootstrap | ❌ 未开始 | 依赖 §2.2 |
| §3 ThumbnailService Qt 连接 | ThumbnailService → ThumbnailCacheService | ❌ 未开始 | 需 Qt 环境测试 |
| §3 VirtualAssetGrid 集成 | VirtualAssetGrid → GalleryGridView | ❌ 未开始 | 需 Qt Widget 重构 |
| §3 GPU Pipeline 集成 | ShaderPrecompiler → GLRenderer | ❌ 延后 | 依赖 OpenGL 渲染路径稳定 |
| §4 遗留模型迁移 | `models/album.py` → `domain/models/core.py` | ❌ 延后 | 有兼容层，低优先级 |

---

## 5. 向后兼容性

| 关注点 | 状态 | 说明 |
|--------|------|------|
| `LibraryService` 原有 API | ✅ 保留 | `create_album()`、`delete_album()` 签名不变 |
| `AssetService` 原有 API | ✅ 保留 | 新增 `weak_cache` 参数为可选，默认 `None` |
| `ParallelScanner` 原有 API | ✅ 保留 | `scan()` 方法行为不变，新增 `scan_streaming()` |
| DI Bootstrap | ✅ 扩展 | 原有 `EventBus` 注册保留，新增 4 个服务 |
| 现有测试套件 | ✅ 全部通过 | 278 个测试通过，0 回归 |

---

## 6. 测试覆盖总结

### 6.1 新增测试

| 类别 | 测试数 | 文件 |
|------|--------|------|
| ManageTrashUseCase | 7 | `tests/test_p2_use_cases.py` |
| AggregateGeoDataUseCase | 3 | `tests/test_p2_use_cases.py` |
| WatchFilesystemUseCase | 3 | `tests/test_p2_use_cases.py` |
| ExportAssetsUseCase | 5 | `tests/test_p2_use_cases.py` |
| ApplyEditUseCase | 3 | `tests/test_p2_use_cases.py` |
| ParallelScanner CPU 感知 | 5 | `tests/test_phase4_integration.py` |
| LibraryService 集成 | 4 | `tests/test_phase4_integration.py` |
| AssetService WeakCache | 3 | `tests/test_phase4_integration.py` |
| DI Bootstrap | 2 | `tests/test_phase4_integration.py` |
| **新增总计** | **35** | |

### 6.2 总体测试指标

| 指标 | 数值 |
|------|------|
| 新增测试 | 35 |
| 回归测试 | 0 失败 |
| 总通过测试 | 278 |
| CodeQL 告警 | 0 |

---

## 7. 文件清单

### 7.1 新增文件

| 文件 | 行数 | 用途 |
|------|------|------|
| `src/iPhoto/application/use_cases/manage_trash.py` | 141 | 回收站管理 Use Case |
| `src/iPhoto/application/use_cases/aggregate_geo_data.py` | 59 | 地理数据聚合 Use Case |
| `src/iPhoto/application/use_cases/watch_filesystem.py` | 84 | 文件系统监听状态管理 Use Case |
| `src/iPhoto/application/use_cases/export_assets.py` | 112 | 资源导出 Use Case |
| `src/iPhoto/application/use_cases/apply_edit.py` | 64 | 编辑应用 Use Case |
| `tests/test_p2_use_cases.py` | 408 | P2 Use Case 测试 |
| `tests/test_phase4_integration.py` | 234 | Phase 4 集成测试 |

### 7.2 修改文件

| 文件 | 变更 | 用途 |
|------|------|------|
| `src/iPhoto/application/services/parallel_scanner.py` | +144 行 | CPU 感知、流式扫描、取消支持 |
| `src/iPhoto/application/services/library_service.py` | +70 行 | ParallelScanner 集成 |
| `src/iPhoto/application/services/asset_service.py` | +26 行 | WeakAssetCache 集成 |
| `src/iPhoto/application/use_cases/__init__.py` | +5 行 | 导出新 Use Case |
| `src/iPhoto/di/bootstrap.py` | +23 行 | 注册性能服务 |

---

## 8. 架构影响

### 8.1 扫描流程对比

**集成前（串行）：**
```
ScanAlbumUseCase → 单线程遍历 → 全量返回 → UI 等待
```

**集成后（并行 + 流式）：**
```
LibraryService.scan_album_streaming()
  │
  ├─ ParallelScanner (cpu_count // 2 workers)
  │   ├─ 发现文件 (generator, 可取消)
  │   ├─ 并行处理 (ThreadPoolExecutor)
  │   ├─ 批间 yield (5ms, 释放 GIL)
  │   └─ ScanProgressEvent (EventBus)
  │
  ├─ yield ScanResult 批次 → UI 增量展示
  │
  └─ asset_repo.save_batch() → 立即持久化
```

### 8.2 缓存查找流程

```
AssetService.get_asset(id)
  │
  ├─ WeakAssetCache.get(id)
  │   └─ HIT → return asset (零 I/O)
  │
  └─ MISS → IAssetRepository.get(id)
       └─ WeakAssetCache.put(id, asset) → 回填
```

---

## 9. 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| ParallelScanner 并发使用 | 🟢 低 | 文档明确说明单实例不支持并发 |
| WeakAssetCache 弱引用被提前回收 | 🟢 低 | `get_asset()` 返回强引用，调用方持有期间不会被回收 |
| DI Bootstrap 注册顺序 | 🟢 低 | 所有服务独立注册，无循环依赖 |
| 流式扫描异常传播 | 🟢 低 | `save_batch` 异常被捕获并记录，不中断扫描 |
| UI 线程饥饿 | 🟢 低 | `yield_interval` + `cpu_count // 2` 双重保护 |

---

## 10. 后续建议

### 短期（1-2 周）
1. **Qt ViewModel 迁移**: 将 `PureAssetListViewModel` 接入现有 Qt 调用方
2. **ThumbnailService Qt 连接**: 连接三级缓存到 Qt 的 `ThumbnailCacheService`

### 中期（1 个月）
3. **大文件拆分**: `facade.py` (733→200 行) 和 `app.py` (580→300 行)
4. **VirtualAssetGrid 集成**: 替换 `GalleryGridView` 的全量加载

### 长期（3 个月）
5. **GPU Pipeline 集成**: 待 OpenGL 渲染路径稳定后接入
6. **遗留模型迁移**: 移除 `models/album.py` 和 `models/types.py` 的兼容层

---

## 结论

**Phase 1-4 遗留集成已成功完成核心任务。**

- ✅ 5/5 P2 Use Cases 实现并测试
- ✅ ParallelScanner CPU 感知增强 + 流式扫描
- ✅ 服务层集成（ParallelScanner → LibraryService, WeakAssetCache → AssetService）
- ✅ DI Bootstrap 扩展（5 个性能服务）
- ✅ 35 个新增测试，0 回归
- ✅ 向后兼容性保持

剩余任务（Qt ViewModel 迁移、大文件拆分、GPU Pipeline 集成）因涉及 Qt 依赖或高风险重构，建议在独立迭代中完成。

---

**文档版本**: 1.0  
**评估日期**: 2026-02-16  
**评估范围**: `docs/refactor/10-remaining-phase-integration.md` 所列 Phase 1-4 遗留任务

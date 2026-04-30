# 超大相册初始扫描稳定性改造需求

> **版本:** 1.0 | **日期:** 2026-04-30  
> **状态:** 待排期  
> **触发背景:** 用户反馈在超大相册初始扫描阶段容易闪退  
> **关联模块:** 扫描管线、索引合并、Live Photo 配对、People 人脸扫描、GUI 初始绑定

---

## 1. 背景与结论

当前初始扫描流程已经具备分批发现、分批提取元数据、分批落库等设计，但代码中仍存在多个会在超大相册下稳定放大的资源风险。用户反馈的“初始扫描阶段闪退”不应按偶发处理，优先判断为内存峰值、原生图像解码、People 人脸扫描和扫描结束全量处理叠加导致的稳定性问题。

本需求用于把排查结论纳入后续开发进度，目标是让初次绑定或首次打开超大 Basic Library 时，应用可以降级、限流、可取消，并避免在扫描完成瞬间出现全量内存峰值。

---

## 2. 目标

1. 初始扫描在超大相册下不因全量 rows、Live Photo pairing、People face scan 或 GUI 刷新造成进程退出。
2. 扫描主路径真正支持流式处理：发现、提取、落库、进度、最终清理都不依赖完整相册常驻内存。
3. 初次绑定库时具备大库保护策略，包括预估、用户可见状态、附加任务延后、可取消和可恢复。
4. People 人脸扫描不与初始整库扫描抢占峰值资源，并且不会在每个小批次触发全量聚类。
5. 建立可复现的大库压力测试和内存观测基线，防止回归。

---

## 3. 当前风险点

### 3.1 分块扫描仍保留全量结果

涉及位置：

- `src/iPhoto/application/use_cases/scan_library.py`
- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/bootstrap/library_scan_service.py`

风险说明：

- `ScanLibraryUseCase.execute()` 即使 `persist_chunks=True`，仍会持续 `rows.append(row)`。
- `ScannerWorker.run()` 在扫描完成后把完整 `result.rows` 通过 `finished` 信号传回。
- `LibraryScanService.finalize_scan()` 又将 rows 复制为 `materialized_rows`。

在数万到数十万资产的相册中，这会形成多份 dict 列表常驻内存。微缩略图、GPS、相机参数、Live Photo 字段等都会放大单行体积。

### 3.2 扫描结束阶段存在全量二次处理

涉及位置：

- `src/iPhoto/index_sync_service.py`
- `src/iPhoto/core/pairing.py`
- `src/iPhoto/cache/index_store/repository.py`

风险说明：

- `update_index_snapshot()` 会读取全库验证，并构建全量 fresh row 映射。
- `prune_index_scope()` 会读取 scoped rows 并构建全量 fresh rel 集合。
- `ensure_links()` 会对整批 rows 做 Live Photo pairing，并写 `links.json`。
- repository merge 对传入的大 snapshot 会物化列表并查询已有行。

这些逻辑在扫描完成瞬间叠加，容易产生扫描过程中看似正常、结束时突然闪退的表现。

### 3.3 初始绑定没有大库保护

涉及位置：

- `src/iPhoto/gui/ui/controllers/dialog_controller.py`
- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/library/scan_coordinator.py`

风险说明：

- 首次绑定或启动时只检查 `global_index.db` 是否存在，不存在就直接整库扫描。
- 没有文件数预估、空间/内存预检查、分阶段提示或用户确认。
- 绑定后会继续打开 All Photos，UI 查询和扫描后台任务可能同时竞争数据库和 CPU。

### 3.4 People 人脸扫描与初始扫描并发

涉及位置：

- `src/iPhoto/library/scan_coordinator.py`
- `src/iPhoto/library/workers/face_scan_worker.py`
- `src/iPhoto/people/pipeline.py`
- `src/iPhoto/people/scan_session.py`

风险说明：

- `start_scanning()` 每次扫描都会启动 `FaceScanWorker`。
- 人脸检测会全尺寸加载图片并复制为 numpy BGR 数组。
- 每批提交会读取已有 faces，并重建 runtime snapshot。
- 聚类阶段会构建 `N x N` 距离矩阵，faces 数量大时是平方级内存风险。

这部分是初始扫描闪退的高危叠加项，应从初始扫描主路径中解耦。

### 3.5 取消与资源阈值不足

涉及位置：

- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/application/use_cases/scan_library.py`
- `src/iPhoto/infrastructure/services/memory_monitor.py`

风险说明：

- `is_cancelled` 只在 scanner yield row 后被检查，ExivTool、Pillow、RAW/HEIC 解码、人脸检测执行中无法及时中断。
- 已存在 `MemoryMonitor`，但当前未接入扫描管线做降级或中断。
- 取消扫描时后台 discoverer、native 解码、face scan 可能仍继续占用资源。

---

## 4. 功能需求

### P0: 初始扫描安全模式

1. 当 Basic Library 没有 `global_index.db` 时，进入 Initial Scan Safe Mode。
2. Safe Mode 默认只执行媒体发现、元数据提取和索引落库。
3. Safe Mode 下默认延后 People face scan、全量 Live Photo links 写入和非必要全库 GUI 刷新。
4. Safe Mode 必须允许用户取消；取消后已成功落库的分块保持可恢复，下一次扫描继续补齐。
5. Safe Mode 必须在状态栏或任务 UI 中展示扫描状态，避免用户误认为应用卡死。

### P0: 真正流式化扫描结果

1. GUI 背景扫描不应在 worker 内累计完整 `rows`。
2. `finished` 信号不应携带全量 rows，改为携带成功/失败、统计信息和 scan session id。
3. chunk merge 成功后立即以数据库为事实源，后续 finalize 仅处理必要差异。
4. `finalize_scan()` 需要拆分为可流式或基于数据库游标的步骤，避免一次性复制全部 rows。
5. 对 CLI 或测试仍需原子语义的路径，可以保留显式 atomic mode，但不能作为 GUI 初始扫描默认路径。

### P0: 结束阶段去峰值

1. `update_index_snapshot()` 不应为了验证腐败而无条件 `list(read_all())`。
2. `prune_index_scope()` 应使用数据库临时表、标记表、扫描 session id 或分块游标比较，避免构建全量 fresh rel set。
3. Live Photo pairing 应支持按目录、按 content_id 或按窗口分批处理，避免对整库 rows 一次性运行。
4. `links.json` 对超大库应允许延迟生成或按 album scope 生成，不阻塞初始扫描完成。

### P0: People 人脸扫描解耦

1. 初始整库扫描期间不自动启动 `FaceScanWorker`，或只在用户显式开启 People 后启动。
2. 人脸扫描必须独立排队，低优先级执行，并受内存阈值控制。
3. 人脸聚类不能在每个 4 张图片的小批次后重建全量 snapshot。
4. 聚类算法需要避免无界 `N x N` 距离矩阵，可采用增量聚类、分片聚类或持久化 embedding 索引。
5. 如果 AI 依赖不可用或内存不足，主相册扫描必须继续可用。

### P1: 大库预估与降级策略

1. 初次绑定前快速估算候选媒体数量，可只计数并跳过详细元数据。
2. 超过阈值时提示进入安全扫描，例如 20,000、50,000、100,000 三档。
3. 大库扫描默认降低缩略图和 face scan 优先级。
4. 可以提供“仅建立索引，不生成微缩略图”的极限安全模式。

### P1: 内存监控接入扫描管线

1. 将 `MemoryMonitor` 接入 `ScannerWorker`、thumbnail generation 和 People worker。
2. warning 阈值触发降级：暂停 People、跳过非必要微缩略图、增大 UI 刷新间隔。
3. critical 阈值触发安全中断：保存当前扫描进度，停止新批次，向 UI 报告可恢复状态。
4. 日志记录峰值 RSS、已扫描数量、当前阶段和最后处理文件。

### P1: 数据库和 UI 查询降载

1. 初始扫描期间 All Photos 默认只显示首屏或分页窗口，不触发全量 read。
2. GUI 刷新应基于 `scanChunkReady` 和分页查询，不因 scan finished 触发全库重载。
3. repository 层应提供针对 finalize 的分页游标或 scan session 查询接口。

---

## 5. 非目标

1. 本需求不要求立即引入 C/C++ 加速层。性能热点加速可参考 `scan_c_hotspot_optimization.md`，但稳定性优先级高于纯加速。
2. 本需求不改变现有媒体索引 schema 的最终形态，但允许新增临时扫描 session 表或状态字段。
3. 本需求不要求一次性重构所有 CLI 路径；优先保证 GUI 初始扫描稳定。

---

## 6. 建议实施阶段

### 阶段 1: 快速止血

- 初始扫描期间禁用或延后 `FaceScanWorker`。
- GUI `ScannerWorker.finished` 不再传递全量 rows，先改为统计结果。
- Safe Mode 下跳过全量 `ensure_links()`，改为后台低优先级任务。
- 增加扫描阶段日志：开始、每 N 个文件、内存采样、完成、取消、失败。

### 阶段 2: 扫描主链路流式化

- 引入 scan session id，chunk 落库时记录本次扫描发现状态。
- finalize 使用数据库查询完成 prune 和 merge，不从 Python 层持有全量 rows。
- Live Photo pairing 改为按目录或 content_id 分区运行。
- GUI finished 只触发当前可见窗口刷新。

### 阶段 3: People 管线重构

- People scan 改为显式任务队列，受内存和 CPU 限流。
- 聚类从每批全量重建改为增量或批量 checkpoint。
- 大库下默认在主索引稳定后再启动 People。

### 阶段 4: 压测和性能基线

- 构造 20k、50k、100k 虚拟库压测。
- 记录峰值 RSS、扫描耗时、DB 大小、取消恢复能力。
- 将大库扫描测试纳入 CI 的可选性能套件。

---

## 7. 验收标准

### 稳定性

- 50,000 个媒体文件的空库初始扫描不会闪退。
- 100,000 个媒体文件的空库初始扫描可完成或可安全取消并恢复。
- 扫描完成瞬间 RSS 不应出现超过扫描中位值 2 倍以上的峰值。
- People 依赖缺失、模型初始化失败或内存不足时，主扫描仍成功完成。

### 资源边界

- GUI 初始扫描路径不持有完整 rows 列表。
- finalize 过程不构建全量 Python fresh rel set。
- face clustering 不在初始扫描期间构建无界全量距离矩阵。
- critical memory threshold 触发后能停止新批次并返回可恢复状态。

### 用户体验

- 初始扫描有明确进度和阶段状态。
- 用户取消扫描后应用不退出、不损坏 index，重新打开后可继续扫描。
- 大库首次绑定时能明确提示安全模式或后台处理策略。

### 回归保护

- 新增单元测试覆盖：
  - `ScannerWorker` finished 不携带全量 rows。
  - Safe Mode 不启动 People face scan。
  - finalize 能处理大批量 rels 而不物化全量 rows。
  - cancel 后已落库 chunk 可继续读取。
- 新增集成或性能测试覆盖：
  - 20k fake media rows 的扫描内存上限。
  - Live Photo pairing 分区处理正确性。
  - People 延后任务不影响主扫描。

---

## 8. 风险与注意事项

1. 流式 finalize 可能改变删除/prune 语义，需要确保未完成扫描不会误删已有索引。
2. 延后 Live Photo pairing 会让扫描刚完成时部分 Live Photo 仍显示为独立文件，需要 UI 标记“配对处理中”或后台快速补齐当前可见区域。
3. 延后 People scan 会改变首次打开 People 页面的数据可用性，需要状态提示。
4. 若引入 scan session 表，需要考虑异常退出后的清理和恢复。
5. 微缩略图如果在安全模式跳过，需要后续按需补生成，避免首屏体验明显下降。

---

## 9. 参考排查入口

- 初始绑定触发扫描：`src/iPhoto/gui/ui/controllers/dialog_controller.py`
- 启动恢复触发扫描：`src/iPhoto/bootstrap/runtime_context.py`
- 扫描调度与 People 并发：`src/iPhoto/library/scan_coordinator.py`
- GUI 扫描 worker：`src/iPhoto/library/workers/scanner_worker.py`
- 扫描 use case：`src/iPhoto/application/use_cases/scan_library.py`
- 扫描适配器：`src/iPhoto/io/scanner_adapter.py`
- 索引 finalize：`src/iPhoto/index_sync_service.py`
- Live Photo pairing：`src/iPhoto/core/pairing.py`
- People worker：`src/iPhoto/library/workers/face_scan_worker.py`
- People pipeline：`src/iPhoto/people/pipeline.py`
- 内存监控：`src/iPhoto/infrastructure/services/memory_monitor.py`

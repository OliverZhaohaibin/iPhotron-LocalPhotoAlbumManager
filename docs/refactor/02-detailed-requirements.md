# 02 - Detailed Requirements

> 本文定义下一轮大架构重构的功能需求、非功能需求、边界规则和验收标准。
> 所有需求以保持现有产品行为为前提。

## 1. 产品不变量

重构不得改变以下产品原则：

- Folder-native album：文件夹仍然是相册，用户无需 import 才能浏览。
- Local-first：所有核心能力在本地运行，不依赖云服务。
- Non-destructive editing：编辑写入 `.ipo` sidecar，不覆盖原图。
- Library-scoped state：library 状态位于 `<LibraryRoot>/.iPhoto/`。
- Rebuildable facts vs durable choices：扫描事实可重建，用户选择不可丢。
- Optional People AI：缺少 InsightFace/ONNXRuntime 时，其他功能正常。
- Optional Maps extension：地图 native runtime 不可用时，应 graceful fallback。
- Cross-platform desktop first：macOS、Windows、Linux 都是目标平台。

## 2. 功能需求

### 2.1 Library 与 Album

- 任意文件夹可作为 library root。
- library root 下的子文件夹可作为 album/collection 浏览。
- `.iphoto.album.json` 和 `.iphoto.album` marker 继续兼容。
- 打开 library 时创建或绑定一个 `LibrarySession`。
- 打开 album/collection 时通过 application query 获取分页资产 DTO。
- album manifest 继续保存 folder-local metadata，但不能作为全局用户状态的唯一来源。

### 2.2 全局索引

- 每个 library root 拥有一个 `.iPhoto/global_index.db`。
- 索引用于高性能分页、搜索、过滤、地图聚合、People 候选读取。
- 索引必须支持增量 merge。
- 索引可通过重新扫描恢复。
- scan merge 不能隐式删除 favorites、hidden、trash、pinned 等用户选择。

### 2.3 用户状态

- 目标架构必须区分可重建扫描事实和持久用户状态。
- favorite、hidden、trash、pinned、manual order、cover、featured 等状态必须有独立持久边界。
- 删除、移动、恢复、重新扫描不得丢失用户状态。
- 如果实现期继续存放在 `global_index.db`，也必须在 schema 和 repository API 上区分 scan tables 与 user-state tables。

### 2.4 Live Photo

- 强配对优先使用 `content.identifier`。
- 弱配对可使用文件名、时间接近等规则。
- 配对结果可重建。
- `links.json` 保留兼容 materialization，但目标权威状态应通过 repository port 读取。
- pairing 不应直接依赖 GUI 或 app facade。

### 2.5 People

- People runtime snapshot 可重建。
- People stable state 必须持久：
  - names
  - covers
  - hidden flags
  - person order
  - groups
  - group order
  - pinned state
  - group covers
  - manual faces
- People scan commit 不得清空 stable state。
- 不允许合并 hidden state 不兼容的人物。
- People UI mutation 必须通过 People application service/port。

### 2.6 Maps

- Maps 是 optional bounded context。
- 地图扩展缺失时，album browsing、editing、People、Live Photo 不受影响。
- 地图资产聚合通过 application use case 或 query port 获取。
- 地图 marker 点击与 gallery selection 的联动通过 application event/query 完成，不直接跨写 UI 内部模型。
- OBF/native widget/helper 的选择属于 maps runtime adapter。

### 2.7 非破坏编辑

- 编辑参数全部通过 `.ipo` sidecar 读写。
- 原始媒体不因普通编辑被覆盖。
- Assign Location 是显式例外：本地状态先持久化，ExifTool 写回为 best-effort。
- 编辑预览渲染可使用 GPU/QRhi/OpenGL，但业务状态不依赖具体渲染 backend。
- core adjustment、geometry、filter math 应保持纯逻辑，可独立测试。

### 2.8 Import / Move / Delete / Restore

- Import use case 负责导入文件、扫描导入文件、merge index、返回冲突和错误报告。
- Move use case 负责文件移动、索引路径更新、用户状态迁移。
- Delete use case 负责 trash lifecycle，不直接丢失用户状态。
- Restore use case 负责恢复路径、恢复状态、必要时重新扫描。
- GUI worker 只能执行 task adapter 责任，不能拥有唯一业务规则。

### 2.9 Assign Location

- Assign Location 必须通过 `AssetRepositoryPort` / `LibraryStateRepositoryPort` 保存本地状态。
- ExifTool 写入失败时返回 warning，不回滚本地数据库状态。
- metadata refresh 通过 `MetadataReaderPort` 完成。
- application service 不得调用 `get_global_repository()`。

### 2.10 CLI

- CLI 必须复用 `RuntimeContext` / `LibrarySession` / application use cases。
- CLI 不应维护独立扫描或仓储路径。
- CLI 输出可以不同，但业务行为必须与 GUI 一致。

## 3. 非功能需求

### 3.1 可维护性

- 新业务优先进入 use case、application service 或 infrastructure adapter。
- compatibility surface 只允许桥接和转发，不允许扩张业务。
- 文件大小不是硬性指标，但一个模块不得长期承担多个业务边界。
- 复杂流程必须有明确 owning use case。

### 3.2 可测试性

- Domain 纯逻辑必须无 Qt、无 SQLite、无 filesystem side effect。
- Application use case 可用 fake ports 测试。
- Infrastructure adapter 用真实 SQLite/tmp filesystem 做集成测试。
- GUI viewmodel 尽量不需要 QApplication。
- Qt widget 测试只覆盖 presentation 行为。

### 3.3 性能

- 大 library 扫描必须增量处理，避免全量 metadata 重读。
- 扫描和缩略图生成不得阻塞 UI thread。
- Gallery 分页必须走 repository query/count，不把全量资产加载到 GUI 内存。
- 缩略图必须使用 memory/disk cache。
- People 和 Maps 任务必须可取消或可后台运行。

### 3.4 可靠性

- 写入 manifest、sidecar、state 文件必须使用 atomic write。
- SQLite 写入必须使用 transaction。
- scan merge 必须 idempotent。
- 外部工具缺失或失败不得破坏本地状态。
- crash 后下次启动可以恢复或重建可重建状态。

### 3.5 跨平台

- 路径处理使用 `Path` 和统一 normalizer。
- 外部二进制通过 wrapper/runtime discovery 调用。
- macOS QRhi/Metal、Windows/Linux OpenGL、Maps native runtime 通过 adapter 选择。
- 不允许业务流程依赖单一平台 rendering backend。

### 3.6 安全

- ExifTool/FFmpeg 调用必须通过 wrapper，参数不得字符串拼接 shell。
- 用户路径必须 normalize，避免错误写入 `.iPhoto/` 或 trash 外部路径。
- 删除和覆盖类操作必须可恢复或显式确认。

## 4. 边界需求

### 4.1 Domain

允许：

- dataclass/value object/entity。
- 纯领域服务。
- 不触发 IO 的规则和算法。

禁止：

- PySide6/Qt。
- SQLite/cache/index_store。
- filesystem write。
- ExifTool/FFmpeg。
- thumbnail generation。

### 4.2 Application

允许：

- use case。
- application service。
- DTO/query/event/policy。
- port protocol。

禁止：

- `gui/` imports。
- concrete `cache/` or `infrastructure/` imports。
- `get_global_repository()`。
- Qt signal、QRunnable、widget、thread-pool ownership。

### 4.3 Infrastructure

允许：

- SQLite repository adapter。
- ExifTool/FFmpeg adapter。
- filesystem scanner adapter。
- thumbnail renderer/cache adapter。
- People runtime adapter。
- Maps runtime adapter。

禁止：

- `gui/` imports。
- viewmodel/coordinator/widget 调用。
- product workflow decision。

### 4.4 GUI

允许：

- views/widgets。
- viewmodels。
- coordinators。
- Qt worker/signal/task adapters。
- presentation state。

禁止：

- 直接写 SQLite 或调用 concrete repository singleton。
- 独占扫描、导入、移动、删除、恢复业务规则。
- 绕过 use case 直接改 durable user state。

## 5. 验收标准

本轮重构完成时必须满足：

- GUI、CLI、watcher、workers 都通过 `RuntimeContext` / `LibrarySession` 进入 application use cases。
- asset persistence 只有一个 public repository port 和一个目标 SQLite adapter。
- 扫描只有一个 application use case，Qt 和 CLI 都是 adapter。
- `application/` 无 concrete persistence 和 GUI imports。
- `infrastructure/` 无 GUI imports。
- runtime code 不再依赖 `iPhoto.models.*`，兼容 shim 和兼容测试除外。
- Assign Location 不再直接调用 `get_global_repository()`。
- Thumbnail infrastructure 不再导入 `gui.ui.tasks.geo_utils`。
- 架构检查进入 CI。
- 关键行为不回退：folder browsing、global indexing、Live Photo、People、Maps、editing、location assignment、trash、import/move/delete/restore、export。

## 6. 明确降级的旧指标

以下旧指标不再作为硬性目标：

- 所有文件必须小于 300 行。
- EventBus 使用率必须 100%。
- DI 覆盖率必须达到某个百分比。
- Use case 数量必须达到固定数字。

替代标准：

- 每个复杂流程有唯一 owning use case。
- 每个跨边界依赖通过 port 或 session surface。
- 关键路径有测试和架构 guardrail。
- 性能基准不回退，UI 不被长任务阻塞。


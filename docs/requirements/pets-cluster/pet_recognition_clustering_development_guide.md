# 宠物识别聚类开发指南

> 版本：1.0
> 日期：2026-06-07
> 配套需求：`pet_recognition_clustering_requirements.md`
> 目标读者：后续实现该功能的工程师 / agent

## 1. 结论先行

宠物识别聚类应作为新的 `iPhoto.pets` bounded context 引入。它要复用现有 People 人脸系统的工程架构，但不能复用人脸模型、face/person 数据表或人脸身份语义。

推荐技术路径：

- 人脸继续使用现有 InsightFace `buffalo_s`，不要迁移到 DINOv2。
- 宠物检测使用 YOLOX Nano/Tiny ONNX，运行在 ONNXRuntime 上。
- 宠物 embedding 使用 DINOv2 ViT-S/14，提取宠物裁剪区域的通用视觉特征。
- 宠物聚类默认使用 HDBSCAN，缺少依赖时回退项目内 cosine DBSCAN。
- UI 层在 People & Pets 页面合并展示人物和宠物，但底层数据、服务和状态独立。

实现的核心原则：

- 复用模式，不复用表。
- 复用调度，不复用模型。
- 复用 UI 交互，不复用 Person 语义。
- `pet_index.db` 可重建，`pet_state.db` 保存用户决定。

## 2. 现有人脸系统基线

### 2.1 当前技术栈

现有人脸识别聚类由以下部分组成：

| 层级 | 当前实现 |
|------|----------|
| 人脸检测 | InsightFace `buffalo_s` / `det_500m.onnx` |
| 人脸 embedding | InsightFace `buffalo_s` / `w600k_mbf.onnx` |
| 推理后端 | ONNXRuntime，优先 CUDA，回退 CPU |
| 聚类算法 | 项目内 cosine distance DBSCAN |
| 默认阈值 | `distance_threshold = 0.6`，`min_samples = 2` |
| 后台扫描 | `FaceScanWorker` |
| 写入协调 | `PeopleIndexCoordinator` |
| 可重建索引 | `.iPhoto/faces/face_index.db` |
| 持久用户状态 | `.iPhoto/faces/face_state.db` |

关键代码边界：

- `src/iPhoto/people/pipeline.py`：检测、embedding、聚类、身份 canonicalize。
- `src/iPhoto/people/repository.py`：运行时 face/person snapshot。
- `src/iPhoto/people/state_repository.py`：持久 profile、名称、封面、隐藏、groups、manual faces。
- `src/iPhoto/people/index_coordinator.py`：序列化写入和 snapshot event。
- `src/iPhoto/library/workers/face_scan_worker.py`：后台批处理 worker。
- `src/iPhoto/people/service.py`：session-bound People API。

### 2.2 不应复用的人脸部分

以下内容不适合宠物直接复用：

- `InsightFace` / `buffalo_s`：专门用于人脸检测和人脸身份 embedding。
- `FaceRecord` / `PersonRecord`：字段和语义围绕人脸与人物。
- `face_index.db` / `face_state.db`：People 状态安全边界，不能混入宠物。
- `face_key`：基于人脸 bbox 的身份修复 key，宠物需要独立 `pet_key`。
- 人脸阈值 `0.6`：DINOv2 embedding 的距离分布不同，不能沿用为默认业务阈值。
- manual face / face overlay：可借鉴交互，但不能直接复用数据结构。

## 3. 总体架构

### 3.1 目标架构

```text
iPhoto AI Runtime
├── People bounded context
│   ├── InsightFace detection
│   ├── InsightFace face embedding
│   ├── cosine DBSCAN
│   ├── face_index.db
│   └── face_state.db
└── Pets bounded context
    ├── YOLOX pet detection
    ├── DINOv2 pet embedding
    ├── HDBSCAN / cosine DBSCAN fallback
    ├── pet_index.db
    └── pet_state.db
```

UI 层可以呈现为一个 People & Pets 页面，但服务层应是组合式：

```text
PeopleDashboardWidget
├── PeopleService.load_dashboard()
└── PetService.load_dashboard()
```

不要把 `PetSummary` 包装成 `PersonSummary`。应在 UI card 层支持一个明确的 card kind：

- `kind = "person"`
- `kind = "pet"`
- `kind = "group"`
- 后续可扩展 `kind = "pet_group"`

### 3.2 新增模块建议

新增 package：

```text
src/iPhoto/pets/
├── __init__.py
├── records.py
├── status.py
├── image_utils.py
├── pipeline.py
├── repository_utils.py
├── repository.py
├── state_repository.py
├── scan_session.py
├── index_coordinator.py
└── service.py
```

新增 worker / bootstrap：

```text
src/iPhoto/library/workers/pet_scan_worker.py
src/iPhoto/bootstrap/library_pet_service.py
```

这些模块可以按 People 模块结构实现，但必须使用宠物命名和宠物 record 类型。

## 4. 模块复用策略

### 4.1 可以复用

| 现有能力 | 复用方式 |
|----------|----------|
| ONNXRuntime provider 选择 | 抽出或复制 `_resolve_execution_providers()` 模式 |
| 后台 worker 批处理 | 参考 `FaceScanWorker` 队列、batch、retry、cancel 设计 |
| snapshot coordinator | 参考 `PeopleIndexCoordinator` 的锁、revision、Qt queued signal |
| repository/state split | 复制 `face_index.db` 可重建 + `face_state.db` 持久状态模式 |
| embedding 序列化 | 复用 `_serialize_embedding()` / `_deserialize_embedding()` 逻辑或抽到共享工具 |
| cosine 工具 | 复用 `normalize_vector()`、`cosine_distance()`、`cosine_distance_matrix()` 模式 |
| 状态值 | 复用 `pending/done/retry/failed/skipped` 枚举设计 |
| 缩略图保存 | 参考 `save_face_thumbnail()`，实现 `save_pet_thumbnail()` |
| People dashboard card 交互 | 复用视觉语言和菜单模式，新增 pet card kind |

### 4.2 必须新增

| 新能力 | 原因 |
|--------|------|
| `PetDetectionRecord` | 宠物检测目标不是 face，需记录 species、detector、embedding model |
| `PetRecord` | 宠物身份不是 person，需独立 profile 生命周期 |
| `PetProfile` | 宠物稳定身份复用依赖独立 embedding 空间 |
| `PetRepository` | `pet_index.db` 可重建快照独立存放 |
| `PetStateRepository` | 名称、封面、隐藏、合并、误检拒绝跨重扫保留 |
| `PetClusterPipeline` | YOLOX + DINOv2 与 InsightFace 完全不同 |
| `PetScanWorker` | 独立状态字段和资源限流 |
| `PetService` | UI/API 层查询宠物卡片、图库 query、命名和合并 |
| `pet_status` | 资产宠物扫描状态不能复用 `face_status` |

## 5. 数据模型设计

### 5.1 Runtime records

建议在 `iPhoto.pets.records` 中定义：

```python
@dataclass(frozen=True)
class PetDetectionRecord:
    detection_id: str
    pet_key: str
    asset_id: str
    asset_rel: str
    species_label: str
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    confidence: float
    embedding: np.ndarray
    embedding_dim: int
    embedding_model: str
    detector_model: str
    thumbnail_path: str | None
    pet_id: str | None
    detected_at: str
    image_width: int
    image_height: int
    quality_score: float | None = None
```

```python
@dataclass(frozen=True)
class PetRecord:
    pet_id: str
    name: str | None
    species_label: str
    key_detection_id: str
    detection_count: int
    center_embedding: np.ndarray
    embedding_dim: int
    created_at: str
    updated_at: str
    sample_count: int = 0
    profile_state: str = "unstable"
```

```python
@dataclass(frozen=True)
class PetSummary:
    pet_id: str
    name: str | None
    species_label: str
    key_detection_id: str
    detection_count: int
    thumbnail_path: Path | None
    created_at: str
    is_hidden: bool = False
```

### 5.2 Storage layout

```text
<library_root>/.iPhoto/
└── pets/
    ├── pet_index.db
    ├── pet_state.db
    └── thumbnails/
```

`pet_index.db` 是 rebuildable cache：

- `pet_detections`
- `pets`
- `scan_metadata`

`pet_state.db` 是 durable state：

- `pet_profiles`
- `pet_keys`
- `pet_covers`
- `hidden_pets`
- `rejected_pet_keys`
- `pet_order`
- `manual_pet_assignments`，P1 可实现

### 5.3 Asset status

在未来实现中需要给 `global_index.db.assets` 增加 `pet_status`：

- `pending`
- `done`
- `retry`
- `failed`
- `skipped`

实现位置应参考现有 `face_status`：

- index store migrations
- row mapper / DTO
- scan merge
- metadata provider / scanner adapter
- repository status helpers
- application port

重要规则：

- 同一资产且 `pet_status` 为 `done/skipped` 时保留。
- `retry/failed` 在 rescan 后重置为 `pending`。
- 资产 ID 或内容身份变化时重置。
- 视频和非图片资产默认 `skipped`。

## 6. Pipeline 设计

### 6.1 宠物检测

`PetClusterPipeline.detect_pets_for_rows()` 输入资产 rows，输出 `DetectedAssetPets`：

```python
@dataclass(frozen=True)
class DetectedAssetPets:
    asset_id: str
    asset_rel: str
    detections: list[PetDetectionRecord]
    error: str | None = None
```

检测流程：

1. 从 `library_root / asset_rel` 读取图片。
2. 使用 YOLOX ONNXRuntime session 推理。
3. 后处理 bbox、confidence、class id。
4. 只保留首版支持的 `cat`、`dog`。
5. 过滤最小尺寸低于 48 px 的目标。
6. 对 bbox 加 8% padding 并裁剪。
7. 保存缩略图到 `.iPhoto/pets/thumbnails/`。
8. 用 DINOv2 对裁剪图提取 embedding。
9. 生成 `pet_key` 和 `PetDetectionRecord`。

### 6.2 pet_key

`pet_key` 用于重扫后复用身份，类似 `face_key`，但必须包含物种：

```text
sha1(asset_id | image_width x image_height | species_label | q_center_x | q_center_y | q_w | q_h)
```

建议 bbox 量化 step：

- 默认 `quantization = 12`
- 对宠物检测框可比 face 更宽松，因为目标框通常抖动更大

### 6.3 Embedding

DINOv2 ViT-S/14 输出的 embedding 必须：

- 转为 `float32`
- flatten
- L2 normalize
- 记录 `embedding_dim`
- 记录 `embedding_model = "dinov2_vits14"`

DINOv2 适合宠物，是因为它提供通用视觉语义和外观特征；它不应替代 InsightFace做人脸识别，因为人脸身份识别需要 ArcFace/InsightFace 这类专门训练的判别 embedding。

### 6.4 聚类

聚类前先按 `species_label` 分桶：

```text
cat detections -> cluster independently
dog detections -> cluster independently
```

默认使用 HDBSCAN：

- metric：cosine 或 precomputed cosine distance
- min_cluster_size：默认 2
- min_samples：默认 1 或 2，需通过测试集调参

fallback 使用现有 cosine DBSCAN：

- 不要求 `hdbscan` 必装
- 阈值不得沿用 face 的 `0.6`，建议先以配置常量单独定义
- 初始值建议 `pet_distance_threshold = 0.35` 到 `0.5` 区间内实测调整

### 6.5 身份 canonicalize

重扫后身份复用顺序固定：

1. `pet_key` 投票：当前 cluster 中命中已有 `pet_key -> pet_id` 最多者胜出。
2. 稳定 profile embedding：仅 `profile_state == "stable"` 且同 species 才参与距离匹配。
3. 新建 `pet_id`：无可信匹配时生成新身份。

稳定门槛：

- `sample_count >= 2` 标记为 stable。
- 单样本 profile 为 unstable，不参与自动 embedding 复用。

## 7. Coordinator 与 Worker 设计

### 7.1 PetIndexCoordinator

职责与 `PeopleIndexCoordinator` 对齐：

- 对宠物 snapshot 写入加锁。
- 接收 worker 的检测 batch。
- 读取已有 runtime detections。
- rebuild 当前宠物 snapshot。
- canonicalize 身份。
- commit `pet_index.db`。
- 同步 `pet_state.db` 的 profiles、covers、pet keys。
- 发布 `PetSnapshotEvent`。
- commit 后更新 `pet_status = done`。

事件建议：

```python
@dataclass(frozen=True)
class PetSnapshotEvent:
    library_root: Path
    revision: int
    changed_asset_ids: tuple[str, ...] = ()
    changed_pet_ids: tuple[str, ...] = ()
    pet_redirects: dict[str, str] = field(default_factory=dict)
```

### 7.2 PetScanWorker

行为参考 `FaceScanWorker`：

- `BATCH_SIZE = 2` 起步，比 face 更保守。
- `QUEUE_TARGET_SIZE = 8` 起步。
- 从 `pet_status in (pending, retry)` 读取候选资产。
- 候选资产规则与 face 一致：图片可扫描，视频跳过。
- 首次失败置 `retry`。
- retry 再失败置 `failed`。
- cancel 时未完成资产保持 pending 或置 retry，不能提交半成品。

资源限流：

- CPU-only 模式下，不应让 Face 和 Pet 同时高负载推理。
- 初版可采取简单策略：Pet worker batch 小、sleep/backoff，或者在 scan coordinator 中串行 AI workers。
- 后续可引入统一 `AIRuntimeScheduler`。

## 8. Service API 设计

`PetService` 应是 UI 和应用层唯一入口。

建议 API：

```python
class PetService:
    def list_pets(self, *, include_hidden: bool = False) -> list[PetSummary]: ...
    def load_dashboard(self, *, include_hidden: bool = False) -> tuple[list[PetSummary], int]: ...
    def rename_pet(self, pet_id: str, new_name: str | None) -> None: ...
    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool: ...
    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool: ...
    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool: ...
    def delete_detection(self, detection_id: str) -> bool: ...
    def move_detection_to_pet(self, detection_id: str, target_pet_id: str) -> bool: ...
    def move_detection_to_new_pet(self, detection_id: str, new_name: str) -> bool: ...
    def build_pet_query(self, pet_id: str) -> AssetQuery: ...
    def pet_status_counts(self) -> dict[str, int]: ...
```

不要把宠物 API 塞进 `PeopleService`。People 页面可以组合两个 service，但 bounded context 要清晰。

## 9. UI 集成方案

### 9.1 Dashboard

`PeopleDashboardWidget` 当前已显示 “People & Pets” 标题，但数据仍来自 People。实现宠物后应改为：

- loader 同时请求 `PeopleService.load_dashboard()` 与 `PetService.load_dashboard()`。
- People cards 和 Pet cards 可共用 card 基类或共享 renderer helper。
- Pet card 显示：
  - name 或默认 `Unnamed Cat` / `Unnamed Dog`
  - species badge
  - detection count
  - pet thumbnail

### 9.2 Navigation

点击 pet card：

- 调用 `PetService.build_pet_query(pet_id)`。
- Gallery viewmodel 增加 `people_cluster_kind` 之外的 entity kind，或改为通用 `smart_cluster_kind`。
- P0 可以先支持 `kind in {"person", "group", "pet"}`。

### 9.3 Menus

Pet card menu P0：

- Rename
- Merge
- Hide / Unhide
- Set as Cover，从图库右键或详情中触发
- Delete Detection，详情/overlay P1 可完善

不要让 Pet card 进入现有 People group dialog。Pet groups 后续单独设计。

## 10. 错误处理与降级

### 10.1 缺依赖

缺少 `pets-ai` 依赖时：

- `PetScanWorker` 发出状态消息。
- 相关 pending/retry 资产可标记 failed，或保持 pending 等待依赖安装；建议与 face runtime 保持一致，初始化失败时标记当前 batch 和剩余项 failed。
- UI 显示 “Pet scanning unavailable” 类消息。
- 核心图库、People 人脸、编辑和地图功能正常。

### 10.2 缺模型

缺 YOLOX 或 DINOv2 模型时：

- 报错消息必须包含期望模型目录。
- 不自动联网下载。
- 不在应用启动时阻塞。

### 10.3 推理失败

单张图片失败：

- 记录 asset id、rel、异常信息。
- 首次失败 retry。
- retry 后 failed。
- batch 中其他资产继续处理。

### 10.4 数据损坏

`pet_index.db` 损坏：

- 可以删除重建。
- 不得删除 `pet_state.db`。

`pet_state.db` 损坏：

- 不能静默覆盖。
- 应提示用户或记录错误，避免丢失名称、封面、隐藏和合并决定。

## 11. 依赖、模型与许可

### 11.1 Optional dependencies

建议新增 optional group：

```toml
[project.optional-dependencies]
pets-ai = [
    "onnxruntime>=1.18,<2",
    "torch",
    "torchvision",
    "hdbscan",
]
```

如果后续 DINOv2 转 ONNX，可把 `torch/torchvision` 移出运行时依赖。

### 11.2 Model directory

默认模型目录：

```text
src/iPhoto/extension/models/pets/
├── detector/
│   └── yolox_nano_coco.onnx
└── embedding/
    └── dinov2_vits14/
```

环境变量：

- `IPHOTO_PET_MODEL_DIR`
- `IPHOTO_PET_SCAN_DISABLED=1`

### 11.3 许可

默认只采用 Apache-2.0 / BSD 许可栈：

- YOLOX：Apache-2.0
- DINOv2：Apache-2.0
- HDBSCAN：BSD

MegaDescriptor / WildlifeTools 可作为后续增强，但不要默认打包非商业权重。若用户显式启用，应在设置和文档中提示许可约束。

## 12. 实施阶段

### Phase 1：数据层和状态字段

目标：

- 增加 `pet_status`。
- 新增 `iPhoto.pets.records/status/repository_utils`。
- 新增 `PetRepository` 和 `PetStateRepository`。
- 覆盖 repository 单元测试。

验收：

- `pet_index.db` 可创建、replace all、查询 summaries。
- `pet_state.db` 可保存名称、封面、隐藏、pet keys、rejected keys。
- scan merge 正确处理 `pet_status`。

### Phase 2：Pipeline

目标：

- 实现 YOLOX ONNX detector wrapper。
- 实现 DINOv2 embedding wrapper。
- 实现 `PetClusterPipeline.detect_pets_for_rows()`。
- 实现 species-aware HDBSCAN / DBSCAN 聚类。
- 实现 canonicalize identities。

验收：

- 合成 embedding 测试通过。
- 无模型时错误清晰。
- 单图多宠物可生成多个 detection。

### Phase 3：Coordinator 和 Worker

目标：

- 实现 `PetIndexCoordinator`。
- 实现 `PetScanWorker`。
- 接入 library runtime controller，但要保持资源保守。

验收：

- pending -> done。
- pending -> retry -> failed。
- cancel 不提交半成品。
- snapshot event 能触发 UI reload。

### Phase 4：Service 和 Query

目标：

- 实现 `PetService`。
- 实现 `build_pet_query()`。
- 增加 session/bootstrap 创建入口。

验收：

- list/load dashboard 可返回 summaries。
- rename/merge/hide/cover/delete detection 生效并跨重扫保持。
- pet query 返回正确资产集合。

### Phase 5：UI

目标：

- People & Pets 页面展示 pet cards。
- 支持 pet card 打开图库和基础菜单。
- 状态消息展示宠物扫描不可用或进行中。

验收：

- People 现有行为不回归。
- Pets 与 People 可并列显示。
- Hidden people/pets 筛选行为清晰。

### Phase 6：打包与文档

目标：

- `pets-ai` 可选依赖文档。
- 模型目录和离线部署说明。
- 许可 NOTICE。

验收：

- 无 `pets-ai` 环境可正常运行。
- 有模型环境可完成宠物扫描。

## 13. 测试矩阵

### Unit tests

- `build_pet_key()` bbox 抖动稳定。
- `normalize_pet_status()` 与 initial status。
- `cluster_pet_records()` 同 species 聚类、跨 species 隔离。
- `canonicalize_pet_identities()` key 投票优先。
- `PetStateRepository` durable state。
- `PetRepository` snapshot replace。

### Worker tests

- queue 去重。
- status transition。
- model init failure。
- per-asset failure。
- cancel。
- post-commit bookkeeping failure。

### Service tests

- rename persists after rescan。
- merge persists after rescan。
- rejected detection does not return。
- hidden pets filtered。
- build pet query returns asset ids。

### UI tests

- dashboard loader combines people and pets。
- pet card activation opens gallery。
- pet menu actions call service。
- existing People groups unaffected。

### Regression tests

- Existing People service tests。
- index store migration tests。
- scan merge tests。
- library runtime stop/wait behavior。

## 14. 开发注意事项

- 不要在 GUI 层直接打开 `pet_index.db` 或 `pet_state.db`。
- 不要把宠物命名为 person，也不要让 `pet_id` 进入 People group。
- 不要把 DINOv2 embedding 与 InsightFace embedding 混合比较。
- 不要让模型初始化发生在应用启动主线程。
- 不要默认联网下载模型。
- 不要因为宠物扫描失败阻塞普通图库扫描。
- 不要把 `pet_index.db` 当作持久用户状态。

## 15. 推荐文件落点摘要

最多优先关注这些实现区域：

- `src/iPhoto/pets/`：宠物 bounded context。
- `src/iPhoto/library/workers/pet_scan_worker.py`：后台扫描。
- `src/iPhoto/gui/ui/widgets/people_dashboard_widget.py`：People & Pets 页面合并展示。
- `src/iPhoto/cache/index_store/`：`pet_status` 迁移、映射、scan merge。
- `tests/test_pet_service.py` 与 `tests/cache/`：核心回归覆盖。

这份指南的实现顺序应从数据层开始，再进入 pipeline 和 worker。不要先做 UI；没有稳定 service/query 前，UI 容易绑定到错误的数据模型。


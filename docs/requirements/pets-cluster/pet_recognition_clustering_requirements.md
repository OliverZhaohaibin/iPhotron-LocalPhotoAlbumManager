# 宠物识别聚类开发需求文档

> 版本：1.0
> 日期：2026-06-07
> 状态：待实施需求
> 范围：只定义需求与实施边界，不包含代码改动

## 1. 背景与目标

iPhotron 当前已经具备 People 人脸检测、聚类、命名、合并、隐藏、封面、分组与持久状态能力。现有实现的核心约束如下：

- 人脸运行时索引位于 `.iPhoto/faces/face_index.db`，可重建。
- 人名、封面、隐藏状态、排序、groups、人工人脸等用户决定位于 `.iPhoto/faces/face_state.db`，必须跨重扫保留。
- 人脸扫描依赖 `ai-demo` 可选依赖 `insightface` 与 `onnxruntime`，缺失时核心图库仍需可用。
- `PeopleIndexCoordinator` 负责序列化 People 写入，`FaceScanWorker` 负责小批量后台扫描，避免阻塞 UI。

本需求的目标是在不破坏上述 People 语义和数据安全边界的前提下，引入宠物识别聚类，使用户可以在现有 People 页面中看到并管理猫狗等宠物个体。

首版目标不是“通用动物百科识别”，而是“个人照片库中的宠物个体聚类”：检测照片中的宠物，提取可比较的视觉特征，把同一只宠物聚成一个可命名、可合并、可隐藏、可打开图库的卡片。

## 2. 推荐开源技术栈

### 2.1 默认方案

| 环节 | 推荐技术 | 许可 | 选择理由 |
|------|----------|------|----------|
| 宠物检测 | YOLOX Nano/Tiny ONNX | Apache-2.0 | 轻量、跨平台、官方支持 ONNXRuntime 部署，可复用现有 `onnxruntime` 方向 |
| 特征提取 | DINOv2 ViT-S/14 | Apache-2.0 | 通用视觉 embedding 对动物外观、花纹、姿态较稳健，代码和模型权重许可清晰 |
| 聚类 | HDBSCAN | BSD | 适合未知类别数和噪声样本；比固定 `min_samples` 的 DBSCAN 更适合宠物照片分布 |
| 回退聚类 | 现有 cosine DBSCAN | 项目内实现 | 避免 `hdbscan` 未安装时宠物功能完全不可用 |

### 2.2 非默认增强方案

MegaDescriptor / WildlifeTools 对动物 re-identification 场景更贴近，但其常见发布权重存在非商业或额外许可约束。首版不得默认打包或默认下载该模型；可在后续作为用户显式启用的高级模式，并在 UI 与文档中说明许可限制。

### 2.3 参考资料

- YOLOX ONNXRuntime 文档：https://yolox.readthedocs.io/en/latest/demo/onnx_readme.html
- YOLOX 项目：https://github.com/Megvii-BaseDetection/YOLOX
- DINOv2 项目：https://github.com/facebookresearch/dinov2
- HDBSCAN 项目：https://github.com/scikit-learn-contrib/hdbscan
- HDBSCAN 文档：https://hdbscan.readthedocs.io/
- WildlifeDatasets 论文与项目：https://arxiv.org/abs/2311.09118

## 3. 总体设计原则

### 3.1 独立 Pet bounded context

宠物识别聚类必须新增独立的 `pets` bounded context，不得把宠物作为 `PersonRecord` 或 face row 写入现有 People 表。

原因：

- 人脸 `face_key`、`PersonProfile`、人工人脸、信息面板人脸框等语义都假定目标是人脸。
- 宠物框通常覆盖整只宠物或头部，不具备人脸 5 点对齐和 face embedding 语义。
- 宠物个体聚类需要 `species_label`、检测类别、全身/头部裁剪策略等独立字段。
- People 状态必须保持可重建和可迁移，不能被宠物实验性模型污染。

### 3.2 复用架构模式，不复用数据表

宠物功能应复用 People 的运行模式：

- 可重建运行时快照数据库。
- 单独持久化用户决定。
- 后台 worker 小批量扫描。
- coordinator 序列化写入并发布 snapshot。
- 服务层暴露 session-bound 查询和命令。
- AI 运行时缺失时优雅降级。

但数据库、record 类型、服务接口、状态字段应独立命名。

## 4. 功能需求

### 4.1 宠物检测

| ID | 需求 | 优先级 |
|----|------|--------|
| PET-101 | 对图片资产检测猫、狗宠物目标，输出边界框 `(x, y, w, h)`、置信度、物种标签 | P0 |
| PET-102 | 检测器使用 YOLOX Nano/Tiny ONNX，通过 ONNXRuntime 执行 CPU 推理；如可用 GPU provider 可自动使用 | P0 |
| PET-103 | 默认只展示 `cat`、`dog`；其他 COCO 动物类可记录为 `species_label`，但首版不进入默认宠物卡片 | P0 |
| PET-104 | 过滤过小目标，默认最小边长 48 px，避免远景动物污染聚类 | P0 |
| PET-105 | 每个检测目标生成裁剪缩略图，保存到 `.iPhoto/pets/thumbnails/` | P0 |
| PET-106 | 生成稳定 `pet_key`，基于 `asset_id`、量化 bbox、图片尺寸、`species_label` 计算 | P0 |
| PET-107 | 支持检测失败按资产进入 `retry/failed` 状态，不影响其他资产继续扫描 | P0 |

### 4.2 宠物特征提取

| ID | 需求 | 优先级 |
|----|------|--------|
| PET-201 | 对宠物裁剪区域提取 DINOv2 embedding，并做 L2 normalize | P0 |
| PET-202 | embedding 记录模型标识、维度和归一化版本，便于后续模型升级后重建索引 | P0 |
| PET-203 | 提取前对 bbox 增加适度 padding，默认 8%，并裁剪到图片边界内 | P1 |
| PET-204 | 对极端长宽比、强模糊、过暗裁剪可记录质量分，但首版只作为排序参考，不直接丢弃 | P1 |
| PET-205 | DINOv2 运行时缺失时返回明确错误消息，核心图库和 People 人脸扫描不受影响 | P0 |

### 4.3 宠物聚类

| ID | 需求 | 优先级 |
|----|------|--------|
| PET-301 | 聚类前必须按 `species_label` 分桶，猫和狗不得进入同一聚类计算 | P0 |
| PET-302 | 默认使用 HDBSCAN 基于 cosine distance 聚类，允许未知聚类数和噪声点 | P0 |
| PET-303 | 未安装 HDBSCAN 时回退到现有 cosine DBSCAN，实现基础聚类能力 | P0 |
| PET-304 | 每个聚类生成 `pet_id`、`key_detection_id`、`detection_count`、`center_embedding`、`species_label` | P0 |
| PET-305 | 噪声样本首版可形成单样本临时宠物卡片，但标记为 `unstable`，重扫时不优先复用身份 | P1 |
| PET-306 | 重扫后通过 `pet_key` 投票优先复用已有 `pet_id`；没有 key 命中时再用稳定 profile 的 embedding 距离匹配 | P0 |
| PET-307 | profile 稳定门槛默认 `sample_count >= 2`，单样本 profile 不参与自动身份复用 | P0 |

### 4.4 宠物管理

| ID | 需求 | 优先级 |
|----|------|--------|
| PET-401 | 用户可给宠物聚类命名，名称存入 `pet_state.db` 并跨重扫保留 | P0 |
| PET-402 | 用户可合并两个宠物聚类，保留目标 `pet_id`，迁移 source 的检测记录和 pet keys | P0 |
| PET-403 | 用户可将单个宠物检测移动到其他宠物聚类 | P0 |
| PET-404 | 用户可把单个检测移动为新的宠物聚类 | P0 |
| PET-405 | 用户可删除误检；删除应记录 rejected `pet_key`，重扫后不恢复该误检 | P0 |
| PET-406 | 用户可设置宠物封面，封面存入稳定状态；自动封面可由置信度和 bbox 面积排序选择 | P0 |
| PET-407 | 用户可隐藏宠物聚类；隐藏状态应与 People 隐藏开关保持一致或使用同一页面过滤能力 | P0 |
| PET-408 | 宠物排序、pin 状态为 P1；不得在 P0 中阻塞核心识别聚类上线 | P1 |

### 4.5 UI 集成

| ID | 需求 | 优先级 |
|----|------|--------|
| PET-501 | People 页面标题可继续使用 “People & Pets”，但数据源需同时加载 person summaries 与 pet summaries | P0 |
| PET-502 | 宠物卡片视觉上应与 People card 保持一致，但显示 species badge 或默认宠物名称，避免与人物混淆 | P0 |
| PET-503 | 点击宠物卡片打开匹配资产图库，查询条件为包含该 `pet_id` 的资产 | P0 |
| PET-504 | 宠物卡片菜单提供命名、合并、隐藏、设置封面、删除误检入口 | P0 |
| PET-505 | 信息面板可在 P1 显示宠物检测框；P0 只要求宠物卡片和图库可用 | P1 |
| PET-506 | 首版不实现 People 与 Pets 混合 group；现有 People groups 不改变语义 | P0 |
| PET-507 | 宠物 groups 可作为 P1 独立能力，命名为 Pet Groups，不能复用 `PeopleGroupRecord` | P1 |

## 5. 数据与持久化需求

### 5.1 目录结构

新增库内数据目录：

```text
<library_root>/.iPhoto/
└── pets/
    ├── pet_index.db       # 可重建宠物运行时快照
    ├── pet_state.db       # 持久宠物用户状态
    └── thumbnails/        # 可重建宠物裁剪缩略图
```

### 5.2 `pet_index.db`

`pet_index.db` 是可重建缓存。模型升级、索引损坏、用户主动重扫时允许删除并重建。

核心表建议：

| 表 | 说明 |
|----|------|
| `pet_detections` | 每个宠物检测框、embedding、缩略图、所属 `pet_id` |
| `pets` | 当前运行时聚类结果，包括聚类中心和 key detection |
| `scan_metadata` | 模型版本、embedding 维度、聚类算法、阈值、创建时间 |

`pet_detections` 关键字段：

- `detection_id TEXT PRIMARY KEY`
- `pet_key TEXT NOT NULL`
- `asset_id TEXT NOT NULL`
- `asset_rel TEXT NOT NULL`
- `species_label TEXT NOT NULL`
- `box_x INTEGER NOT NULL`
- `box_y INTEGER NOT NULL`
- `box_w INTEGER NOT NULL`
- `box_h INTEGER NOT NULL`
- `confidence REAL NOT NULL`
- `embedding BLOB NOT NULL`
- `embedding_dim INTEGER NOT NULL`
- `embedding_model TEXT NOT NULL`
- `thumbnail_path TEXT`
- `pet_id TEXT`
- `detected_at TEXT NOT NULL`
- `image_width INTEGER NOT NULL`
- `image_height INTEGER NOT NULL`

### 5.3 `pet_state.db`

`pet_state.db` 是持久用户状态，必须跨重扫、重建索引、应用重启保留。

核心表建议：

| 表 | 说明 |
|----|------|
| `pet_profiles` | 稳定身份 profile、名称、中心向量、样本数、状态 |
| `pet_keys` | `pet_key -> pet_id` 映射，用于重扫身份复用 |
| `pet_covers` | 用户指定或自动同步的封面 |
| `hidden_pets` | 隐藏状态 |
| `rejected_pet_keys` | 用户删除的误检 key，重扫后过滤 |
| `manual_pet_assignments` | 手动移动检测或人工标注的持久补丁，P1 可扩展 |

### 5.4 `global_index.db` 状态字段

新增资产级 `pet_status`：

- `pending`：图片可扫描但尚未完成。
- `done`：宠物扫描已完成，无论是否检测到宠物。
- `retry`：首次处理失败，下次继续尝试。
- `failed`：重试后仍失败，等待后续 rescan 重置。
- `skipped`：视频或不适合宠物扫描的资产。

扫描合并逻辑必须遵守：

- 同一资产且 `pet_status` 为 `done/skipped` 时保留状态。
- 同一资产 `failed/retry` 在 rescan 后重置为 `pending`。
- 资产身份变化时重置为初始状态。
- `pet_status` 不得复用 `face_status` 字段。

## 6. 服务与任务调度需求

### 6.1 新增服务边界

建议新增：

- `iPhoto.pets.records`
- `iPhoto.pets.pipeline`
- `iPhoto.pets.repository`
- `iPhoto.pets.state_repository`
- `iPhoto.pets.index_coordinator`
- `iPhoto.pets.service`
- `iPhoto.library.workers.pet_scan_worker`
- `iPhoto.bootstrap.library_pet_service`

这些模块应参考 People 结构，但命名和类型必须独立。

### 6.2 Worker 行为

`PetScanWorker` 应满足：

- 默认 batch size 为 2 或 4，避免 DINOv2 占用过多内存。
- 与 `FaceScanWorker` 并行时不得使 UI 明显卡顿。
- 支持 `cancel()`，取消时未完成资产回到 `retry` 或保持 `pending`。
- 每个 batch 提交后发布 `petSnapshotCommitted` 或合并到通用 AI snapshot 事件。
- 模型初始化失败时只更新宠物状态消息，不终止普通扫描。

### 6.3 与现有扫描协调

首版允许 Face 和 Pet worker 并行启动，但必须加入资源保护：

- CPU-only 模式下同时最多运行一个重模型推理 worker；另一个 worker 可排队或低频轮询。
- GPU provider 可用时允许并行，但 batch size 仍需限制。
- 停止扫描时必须同时取消 scanner、face worker、pet worker。
- 关闭应用时不得强制 terminate AI worker，避免数据库损坏。

## 7. 配置与依赖需求

### 7.1 可选依赖

新增 optional dependency group 建议命名为 `pets-ai`：

- `onnxruntime`：检测器推理，版本范围与 `ai-demo` 保持兼容。
- `torch`、`torchvision`：DINOv2 首版运行时。
- `hdbscan`：默认聚类器。

如果后续将 DINOv2 导出 ONNX，可把 `torch` 从运行时依赖降为模型转换工具依赖。

### 7.2 模型目录

默认模型目录：

```text
src/iPhoto/extension/models/pets/
├── detector/
│   └── yolox_nano_coco.onnx
└── embedding/
    └── dinov2_vits14/
```

环境变量覆盖：

- `IPHOTO_PET_MODEL_DIR`：宠物模型根目录。
- `IPHOTO_PET_SCAN_DISABLED=1`：禁用宠物扫描，用于调试和低资源设备。

### 7.3 打包要求

- 打包产物可以不内置宠物模型，但 UI 必须清晰提示缺少宠物 AI runtime。
- 若内置模型，必须带上第三方许可文本和 NOTICE。
- 不得在应用启动时自动联网下载模型；下载只能由用户显式触发。

## 8. 非功能需求

| ID | 需求 |
|----|------|
| NFR-PET-01 | 本地优先，宠物图片、embedding、模型推理结果不得上传云端 |
| NFR-PET-02 | 无 `pets-ai` 依赖时，主图库、People、编辑、地图等功能正常可用 |
| NFR-PET-03 | 10,000 张图库规模下，后台扫描不能阻塞 UI 交互 |
| NFR-PET-04 | `pet_index.db` 可删除重建，`pet_state.db` 不得因重扫丢失用户决定 |
| NFR-PET-05 | 宠物缩略图缓存可重建，缺失时 UI 应显示占位图并异步恢复 |
| NFR-PET-06 | 记录每批扫描耗时、失败原因、模型初始化错误，便于诊断 |
| NFR-PET-07 | 所有写入必须通过 session-bound service/coordinator，不允许 GUI 直接写 SQLite |

## 9. 验收标准

### 9.1 P0 验收

- 安装 `pets-ai` 并提供模型后，扫描图片库会生成宠物卡片。
- 猫狗不会被聚到同一个宠物身份中。
- 用户给宠物命名后，删除 `pet_index.db` 并重扫，名称仍保留。
- 用户合并两个宠物聚类后，重扫不会恢复被合并的旧聚类。
- 用户删除误检后，重扫不会再次显示该检测框。
- 点击宠物卡片能打开包含该宠物的资产图库。
- 未安装 `pets-ai` 时，应用启动、图库浏览、People 人脸扫描均正常。

### 9.2 P1 验收

- 宠物检测框可在信息面板或预览 overlay 中查看。
- 支持 Pet Groups 或宠物 pin，但不得影响现有 People groups。
- 支持更细 species 配置，例如只显示猫狗或显示全部动物类。

## 10. 测试计划

### 10.1 单元测试

- `build_pet_key()`：bbox 轻微抖动时 key 稳定，明显不同目标 key 不同。
- `cluster_pet_records()`：同物种相近 embedding 聚类，跨物种不聚类。
- `canonicalize_pet_identities()`：优先使用 pet key 投票，其次使用稳定 profile embedding。
- `PetStateRepository`：命名、封面、隐藏、rejected key、合并映射跨重扫保留。
- `PetRepository.replace_all()`：运行时快照可整体替换，且同步 state 默认封面。

### 10.2 Worker 测试

- pending 资产成功处理后变为 done。
- 首次失败变为 retry，retry 再失败变为 failed。
- 模型初始化失败时剩余 pending/retry 资产按策略更新，不影响主扫描结束。
- cancel 后不提交半成品 snapshot。

### 10.3 UI / Service 测试

- People 页面同时加载 people summaries 与 pet summaries。
- 宠物隐藏开关生效。
- 宠物卡片打开图库 query 正确。
- 宠物合并、重命名、设封面后 dashboard 刷新。
- Pinned People 现有行为不因宠物数据改变而回归。

### 10.4 回归测试

- 现有 `tests/test_people_service.py`、People repository、dashboard、scan merge 相关测试必须继续通过。
- 无宠物依赖环境下测试默认不需要下载模型。
- 数据库迁移测试覆盖新增 `pet_status`，并验证旧库可升级。

## 11. 风险与取舍

| 风险 | 影响 | 缓解 |
|------|------|------|
| DINOv2 通用 embedding 对同品种宠物区分不足 | 聚类可能过合并 | 首版提供合并/移动/删除误检；后续评估 MegaDescriptor |
| PyTorch 增大打包体积 | 打包复杂 | 首版可把宠物 AI 作为可选依赖；后续 ONNX 化 embedding |
| 宠物目标检测框不总是包含可辨识头部 | embedding 噪声 | 使用 bbox padding、质量分、HDBSCAN 噪声处理 |
| 与 Face worker 抢 CPU/GPU | UI 卡顿或扫描变慢 | 限制 batch size，CPU-only 单重模型 worker |
| 许可误用 | 发布风险 | 默认只使用 Apache/BSD 技术栈，非商业权重不得默认打包 |

## 12. 明确不做

首版不做以下内容：

- 不把宠物写入 `face_index.db` 或 `face_state.db`。
- 不修改现有 `PersonRecord`、`FaceRecord` 表示宠物。
- 不实现 People 与 Pets 混合 group。
- 不自动联网下载模型。
- 不引入云端识别服务。
- 不训练自有模型。
- 不要求视频宠物识别。


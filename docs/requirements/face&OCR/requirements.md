# 📋 人脸识别 / OCR 文字识别 — 需求文档

> 版本 1.0 · 2026-02-18
>
> 本文档定义 iPhotron 中 **人脸识别（Face Recognition & Clustering）** 与 **OCR 文字提取** 两大子系统的完整功能需求、数据库设计、任务调度架构，以及 GPU / CPU 并行策略。

---

## 目录

1. [术语与缩写](#1-术语与缩写)
2. [项目背景与目标](#2-项目背景与目标)
3. [功能需求](#3-功能需求)
   - 3.1 [人脸检测与特征提取](#31-人脸检测与特征提取)
   - 3.2 [人脸聚类](#32-人脸聚类)
   - 3.3 [聚类管理](#33-聚类管理)
   - 3.4 [OCR 文字提取](#34-ocr-文字提取)
   - 3.5 [文字搜索](#35-文字搜索)
4. [非功能需求](#4-非功能需求)
5. [数据库设计](#5-数据库设计)
   - 5.1 [数据库拓扑总览](#51-数据库拓扑总览)
   - 5.2 [人脸数据库 `face_index.db`](#52-人脸数据库-face_indexdb)
   - 5.3 [OCR 数据库 `ocr_index.db`](#53-ocr-数据库-ocr_indexdb)
   - 5.4 [与主库 `global_index.db` 的关系](#54-与主库-global_indexdb-的关系)
6. [任务调度与多队列架构](#6-任务调度与多队列架构)
   - 6.1 [队列拓扑](#61-队列拓扑)
   - 6.2 [资源公平调度策略](#62-资源公平调度策略)
   - 6.3 [CUDA 加速与 CPU 回退](#63-cuda-加速与-cpu-回退)
7. [外部依赖](#7-外部依赖)
8. [验收标准](#8-验收标准)
9. [附录 A — 参考项目](#附录-a--参考项目)

---

## 1. 术语与缩写

| 术语 | 说明 |
|------|------|
| **Embedding / 特征向量** | 由深度学习模型从人脸区域提取的 128-D 或 512-D 浮点向量，用于度量人脸相似度 |
| **Cluster / 聚类** | 被算法判定为同一人物的若干人脸分组 |
| **Person / 人物** | 用户确认或命名后的聚类，等同于 macOS Photos 中的 "People" |
| **ROI** | Region of Interest — 人脸在原图中的边界框区域 |
| **OCR** | Optical Character Recognition — 光学字符识别 |
| **Worker** | 独立线程或进程，消费任务队列并执行计算 |
| **主库** | `global_index.db` — iPhotron 现有的全局资产索引数据库 |
| **DNN** | Deep Neural Network — 深度神经网络 |
| **CUDA** | NVIDIA GPU 并行计算平台 |
| **HAL** | Hardware Abstraction Layer — OpenCV 的硬件抽象层 |

---

## 2. 项目背景与目标

iPhotron 当前已具备完整的相册管理、元数据索引、实况照片配对、地图视图等功能，但**缺少基于图像内容的智能分析能力**。

参照 Apple Photos、Google Photos、Synology Photos、Immich、PhotoPrism 等成熟产品，本需求拟为 iPhotron 新增以下能力：

| # | 能力 | 对标产品功能 |
|---|------|-------------|
| F-1 | 人脸检测 + 特征提取 | Apple Photos "People"、Google Photos "People & Pets"、Immich Face Detection |
| F-2 | 无监督聚类 + 用户确认 | Apple Photos 自动聚类 + 手动命名 |
| F-3 | 聚类合并 / 拆分 / 单张移动 | Synology Photos "Merge/Unmerge faces" |
| F-4 | OCR 文字提取 + 全文搜索 | Apple Photos "Visual Lookup"、Google Lens 内置搜索 |

### 核心设计原则

1. **非侵入**：所有 AI 分析数据存入独立数据库，**不修改**现有 `global_index.db` 表结构。
2. **后台处理**：所有耗时任务在后台队列中异步执行，**不阻塞**主界面和主库写入队列。
3. **GPU 优先、CPU 兜底**：优先使用 CUDA/OpenCL 加速，自动回退至 CPU。
4. **渐进可用**：首批结果在秒级可用，完整索引允许后台慢速完成。

---

## 3. 功能需求

### 3.1 人脸检测与特征提取

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-101 | 基于 OpenCV DNN 模块加载预训练 SSD / YuNet 人脸检测模型，支持 ONNX 格式 | P0 |
| FR-102 | 对每张图片检测所有人脸，输出边界框 `(x, y, w, h)` 与置信度 `confidence` | P0 |
| FR-103 | 对检测到的每张人脸裁剪并对齐（5-point landmark），缩放至 112×112 | P0 |
| FR-104 | 使用 OpenCV DNN 加载 ArcFace / SFace 模型，提取 128-D 或 512-D 特征向量（L2-normalized） | P0 |
| FR-105 | 每张人脸生成 160×160 缩略图保存至磁盘缓存目录 | P1 |
| FR-106 | 支持批量处理：一次加载多张图片进行检测 + 提取，减少模型加载开销 | P1 |
| FR-107 | 最小人脸尺寸可配置（默认 40×40 像素），过小人脸跳过 | P1 |
| FR-108 | 可选模糊人脸过滤（Laplacian 方差阈值），减少低质量人脸入库 | P2 |

### 3.2 人脸聚类

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-201 | 初始聚类使用 **Chinese Whispers** 或 **DBSCAN** 算法，基于余弦距离阈值（默认 0.6）将人脸分组 | P0 |
| FR-202 | 聚类结果生成 `Person` 记录，初始 `name` 为 `NULL`（未命名） | P0 |
| FR-203 | 支持增量聚类：新照片入库后仅需对新人脸与现有聚类中心对比，无需全量重算 | P0 |
| FR-204 | 每个聚类自动选取置信度最高的人脸作为"代表脸" `key_face_id` | P1 |
| FR-205 | 用户可手动设置任一人脸为聚类代表脸 | P2 |
| FR-206 | 聚类完成后发布 `FaceClusteringCompletedEvent` 事件通知 GUI 层 | P0 |

### 3.3 聚类管理

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-301 | **合并聚类**：用户选择两个或多个 Person 进行合并，保留其中一个 `person_id`，其余人脸 `person_id` 更新，旧 Person 逻辑删除 | P0 |
| FR-302 | **拆分聚类**：用户从一个 Person 中选取若干人脸移出，自动创建新 Person | P0 |
| FR-303 | **移动单张人脸**：用户将某张人脸从当前 Person 移动到指定目标 Person | P0 |
| FR-304 | **命名人物**：用户为 Person 设置显示名称 `name` | P0 |
| FR-305 | **设置/取消隐藏**：用户可隐藏某 Person 使其不在"人物"视图中显示 | P1 |
| FR-306 | **删除人物**：逻辑删除 Person，其关联人脸标记为 `unassigned`，后续重新聚类时可再次分组 | P1 |
| FR-307 | 合并/拆分/移动操作后自动重算受影响聚类的中心向量与代表脸 | P1 |
| FR-308 | 所有聚类管理操作支持撤销（记录操作日志至 `face_operations_log` 表） | P2 |

### 3.4 OCR 文字提取

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-401 | 基于 OpenCV DNN + 预训练 EAST / DB (Differentiable Binarization) 文字检测模型，检测图片中的文字区域 | P0 |
| FR-402 | 使用 OpenCV DNN + CRNN 识别模型或 Tesseract OCR 引擎对文字区域进行识别 | P0 |
| FR-403 | 支持中文、英文、日文、韩文等多语言识别（可配置语言包） | P0 |
| FR-404 | 识别结果按区域存储：每条记录包含 `(x, y, w, h)`（文字区域坐标）、`text`（识别文本）、`confidence`（置信度）、`language`（语言） | P0 |
| FR-405 | 支持旋转文字检测（角度 ±45°） | P2 |
| FR-406 | 提供 OCR 完成事件 `OCRCompletedEvent`，通知 GUI 更新搜索索引 | P0 |

### 3.5 文字搜索

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-501 | 在搜索栏输入文字时，同时查询主库的元数据字段和 OCR 数据库的 `text` 字段 | P0 |
| FR-502 | 搜索结果按相关性排序：精确匹配 > 前缀匹配 > 模糊匹配 | P1 |
| FR-503 | 搜索结果中高亮显示匹配的文字区域（在图片预览上叠加半透明标注框） | P2 |
| FR-504 | 支持 FTS5 全文搜索索引以加速文字查询 | P0 |
| FR-505 | 搜索响应时间 ≤ 200ms（10 万张图片库规模） | P1 |

---

## 4. 非功能需求

| ID | 需求 | 说明 |
|----|------|------|
| NFR-01 | **性能** | 人脸检测+提取：≥ 5 张/秒 (GPU) 或 ≥ 1 张/秒 (CPU)；OCR：≥ 3 张/秒 (GPU) |
| NFR-02 | **内存** | 单个 Worker 峰值内存 ≤ 1 GiB；总 AI 子系统内存 ≤ 3 GiB |
| NFR-03 | **存储** | 人脸特征向量：约 2 KB/人脸（512-D float32）；OCR 文本：约 0.5 KB/区域 |
| NFR-04 | **兼容性** | 支持 Windows 10+ / macOS 12+ / Linux (Ubuntu 22.04+)，Python ≥ 3.12 |
| NFR-05 | **CUDA 兼容性** | 支持 CUDA 11.8+，cuDNN 8.6+；无 NVIDIA GPU 时自动回退 CPU |
| NFR-06 | **可观测性** | 所有 Worker 暴露进度（已处理/总数）、速率（张/秒）、错误计数指标 |
| NFR-07 | **幂等性** | 对同一张图片重复入队不产生重复记录（基于 `asset_rel` 去重） |
| NFR-08 | **优雅退出** | 应用关闭时所有 Worker 在当前任务完成后退出，未完成任务保留在队列中下次启动继续 |
| NFR-09 | **可配置** | 模型路径、聚类阈值、最小人脸尺寸、Worker 数量等均通过配置文件管理 |

---

## 5. 数据库设计

### 5.1 数据库拓扑总览

iPhotron 采用 **三库分离** 架构：

```
<library_root>/.iPhoto/
├── global_index.db      ← 现有主库（资产元数据、相册结构）
├── face_index.db        ← 新增：人脸检测、特征向量、聚类
└── ocr_index.db         ← 新增：OCR 文字提取结果、全文索引
```

**分库原因：**

| 理由 | 说明 |
|------|------|
| **隔离故障** | AI 数据库损坏不影响主库的正常浏览和管理功能 |
| **独立升级** | 模型更新后可独立重建 AI 数据库，主库无需变动 |
| **减少锁竞争** | 后台 Worker 高频写入 AI 数据库时不会与主库读写产生 WAL 锁争用 |
| **按需加载** | 用户不使用 AI 功能时，无需打开 AI 数据库 |
| **体积控制** | 特征向量占用较大存储空间（512-D × 4 bytes × N），分离后主库保持轻量 |

---

### 5.2 人脸数据库 `face_index.db`

#### 5.2.1 表 `faces` — 人脸检测记录

存储每张照片中检测到的每张人脸。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `face_id` | TEXT | PRIMARY KEY | UUID v4，人脸唯一标识 |
| `asset_rel` | TEXT | NOT NULL | 关联资产的相对路径（对应主库 `assets.rel`） |
| `box_x` | INTEGER | NOT NULL | 人脸边界框左上角 X（像素坐标） |
| `box_y` | INTEGER | NOT NULL | 人脸边界框左上角 Y（像素坐标） |
| `box_w` | INTEGER | NOT NULL | 人脸边界框宽度（像素） |
| `box_h` | INTEGER | NOT NULL | 人脸边界框高度（像素） |
| `confidence` | REAL | NOT NULL | 检测置信度 [0.0, 1.0] |
| `embedding` | BLOB | NOT NULL | 特征向量（float32 数组序列化，128-D 或 512-D） |
| `embedding_dim` | INTEGER | NOT NULL DEFAULT 512 | 特征向量维度 |
| `embedding_model` | TEXT | NOT NULL DEFAULT 'SFace_v2' | 使用的特征提取模型标识 |
| `thumbnail_path` | TEXT | | 人脸缩略图相对路径 |
| `quality_score` | REAL | | 人脸质量评分（清晰度、角度等综合评估） |
| `person_id` | TEXT | REFERENCES persons(person_id) | 所属 Person（聚类后赋值，NULL 表示未分配） |
| `is_key_face` | INTEGER | NOT NULL DEFAULT 0 | 是否为该 Person 的代表脸 |
| `detected_at` | TEXT | NOT NULL | 检测时间 ISO 8601 |
| `detector_model` | TEXT | NOT NULL DEFAULT 'YuNet_v3' | 使用的检测模型标识 |
| `image_width` | INTEGER | | 原图宽度（用于坐标归一化计算） |
| `image_height` | INTEGER | | 原图高度（用于坐标归一化计算） |

**索引：**

| 索引名 | 列 | 用途 |
|--------|-----|------|
| `idx_faces_asset_rel` | `asset_rel` | 按资产查找所有人脸 |
| `idx_faces_person_id` | `person_id` | 按 Person 查找所有人脸 |
| `idx_faces_confidence` | `confidence DESC` | 选取高置信度人脸 |
| `idx_faces_key_face` | `person_id, is_key_face` | 快速获取代表脸 |
| `idx_faces_detected_at` | `detected_at` | 按时间排序 |

---

#### 5.2.2 表 `persons` — 人物（聚类）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `person_id` | TEXT | PRIMARY KEY | UUID v4，聚类唯一标识 |
| `name` | TEXT | | 用户命名的人物名称（NULL 表示未命名） |
| `key_face_id` | TEXT | REFERENCES faces(face_id) | 代表脸 ID |
| `face_count` | INTEGER | NOT NULL DEFAULT 0 | 该 Person 下的人脸总数（冗余计数，触发器维护） |
| `center_embedding` | BLOB | | 聚类中心向量（所有人脸 embedding 的均值） |
| `is_hidden` | INTEGER | NOT NULL DEFAULT 0 | 是否在"人物"视图中隐藏 |
| `is_deleted` | INTEGER | NOT NULL DEFAULT 0 | 逻辑删除标记 |
| `created_at` | TEXT | NOT NULL | 创建时间 ISO 8601 |
| `updated_at` | TEXT | NOT NULL | 最后更新时间 ISO 8601 |
| `merge_source_ids` | TEXT | | 被合并的原 person_id 列表（JSON 数组） |

**索引：**

| 索引名 | 列 | 用途 |
|--------|-----|------|
| `idx_persons_name` | `name` | 按名称搜索人物 |
| `idx_persons_face_count` | `face_count DESC` | 按人脸数排序（首页展示最常出现的人物） |
| `idx_persons_active` | `is_deleted, is_hidden` | 筛选活跃人物 |

---

#### 5.2.3 表 `face_operations_log` — 操作审计日志

用于支持撤销操作和操作追溯。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `log_id` | TEXT | PRIMARY KEY | UUID v4 |
| `operation` | TEXT | NOT NULL | 操作类型：`merge`, `split`, `move`, `rename`, `hide`, `delete`, `set_key_face` |
| `payload` | TEXT | NOT NULL | JSON 格式的操作详情（包含操作前后的完整状态快照） |
| `created_at` | TEXT | NOT NULL | 操作时间 ISO 8601 |
| `is_undone` | INTEGER | NOT NULL DEFAULT 0 | 是否已撤销 |

---

#### 5.2.4 表 `face_queue` — 人脸处理队列（持久化）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `queue_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 队列序号 |
| `asset_rel` | TEXT | NOT NULL UNIQUE | 待处理资产相对路径 |
| `status` | TEXT | NOT NULL DEFAULT 'pending' | `pending` / `processing` / `completed` / `failed` |
| `priority` | INTEGER | NOT NULL DEFAULT 0 | 优先级（0 = 普通，正数更高优先） |
| `retry_count` | INTEGER | NOT NULL DEFAULT 0 | 重试次数 |
| `error_message` | TEXT | | 最后一次错误信息 |
| `enqueued_at` | TEXT | NOT NULL | 入队时间 |
| `started_at` | TEXT | | 开始处理时间 |
| `completed_at` | TEXT | | 完成时间 |

**索引：**

| 索引名 | 列 | 用途 |
|--------|-----|------|
| `idx_face_queue_status` | `status, priority DESC, queue_id ASC` | Worker 取任务（优先级+先进先出） |

---

### 5.3 OCR 数据库 `ocr_index.db`

#### 5.3.1 表 `ocr_regions` — OCR 检测区域

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `region_id` | TEXT | PRIMARY KEY | UUID v4，区域唯一标识 |
| `asset_rel` | TEXT | NOT NULL | 关联资产相对路径（对应主库 `assets.rel`） |
| `box_x` | INTEGER | NOT NULL | 文字区域左上角 X |
| `box_y` | INTEGER | NOT NULL | 文字区域左上角 Y |
| `box_w` | INTEGER | NOT NULL | 文字区域宽度 |
| `box_h` | INTEGER | NOT NULL | 文字区域高度 |
| `text` | TEXT | NOT NULL | 识别出的文字内容 |
| `confidence` | REAL | NOT NULL | 识别置信度 [0.0, 1.0] |
| `language` | TEXT | NOT NULL DEFAULT 'eng' | 检测到的语言代码 (ISO 639-3) |
| `rotation_angle` | REAL | DEFAULT 0.0 | 文字区域旋转角度（度） |
| `ocr_model` | TEXT | NOT NULL DEFAULT 'CRNN_v1' | 使用的 OCR 模型标识 |
| `detected_at` | TEXT | NOT NULL | 检测时间 ISO 8601 |
| `image_width` | INTEGER | | 原图宽度 |
| `image_height` | INTEGER | | 原图高度 |

**索引：**

| 索引名 | 列 | 用途 |
|--------|-----|------|
| `idx_ocr_regions_asset` | `asset_rel` | 按资产查找所有文字区域 |
| `idx_ocr_regions_lang` | `language` | 按语言过滤 |

---

#### 5.3.2 虚拟表 `ocr_fts` — 全文搜索索引 (FTS5)

```sql
CREATE VIRTUAL TABLE ocr_fts USING fts5(
    text,
    content='ocr_regions',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
```

| 列名 | 说明 |
|------|------|
| `text` | 与 `ocr_regions.text` 同步的全文搜索字段 |

**触发器：** 使用 `INSERT`、`UPDATE`、`DELETE` 触发器保持 `ocr_fts` 与 `ocr_regions` 同步。

---

#### 5.3.3 表 `ocr_queue` — OCR 处理队列（持久化）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `queue_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 队列序号 |
| `asset_rel` | TEXT | NOT NULL UNIQUE | 待处理资产相对路径 |
| `status` | TEXT | NOT NULL DEFAULT 'pending' | `pending` / `processing` / `completed` / `failed` |
| `priority` | INTEGER | NOT NULL DEFAULT 0 | 优先级 |
| `retry_count` | INTEGER | NOT NULL DEFAULT 0 | 重试次数 |
| `error_message` | TEXT | | 最后一次错误信息 |
| `enqueued_at` | TEXT | NOT NULL | 入队时间 |
| `started_at` | TEXT | | 开始处理时间 |
| `completed_at` | TEXT | | 完成时间 |

**索引：**

| 索引名 | 列 | 用途 |
|--------|-----|------|
| `idx_ocr_queue_status` | `status, priority DESC, queue_id ASC` | Worker 取任务 |

---

### 5.4 与主库 `global_index.db` 的关系

三个数据库通过 `asset_rel`（资产相对路径）字段关联。**不使用 SQLite `ATTACH DATABASE`**，而是在应用层通过代码连接查询结果。

```
global_index.db                face_index.db              ocr_index.db
┌──────────────┐              ┌──────────────┐           ┌──────────────┐
│ assets       │              │ faces        │           │ ocr_regions  │
│ ─────────    │   asset_rel  │ ─────────    │           │ ─────────    │
│ rel (PK)  ◄──┼──────────────┤ asset_rel    │           │ asset_rel ───┼──► rel
│ dt           │              │ face_id (PK) │           │ region_id(PK)│
│ w, h         │              │ embedding    │           │ text         │
│ ...          │              │ person_id    │           │ confidence   │
└──────────────┘              │ ...          │           │ ...          │
                              ├──────────────┤           ├──────────────┤
                              │ persons      │           │ ocr_fts (FTS)│
                              │ ─────────    │           └──────────────┘
                              │ person_id(PK)│
                              │ name         │
                              │ ...          │
                              ├──────────────┤
                              │ face_queue   │
                              └──────────────┘
```

**跨库查询示例：**

```python
# 查找某人物的所有照片（应用层 JOIN）
face_repo = FaceRepository(face_db_path)
asset_repo = AssetRepository(global_db_path)

face_records = face_repo.get_faces_by_person(person_id)
asset_rels = [f.asset_rel for f in face_records]
assets = asset_repo.get_by_rels(asset_rels)
```

---

## 6. 任务调度与多队列架构

### 6.1 队列拓扑

```
                    ┌───────────────────────────────────────┐
                    │         ScanCompletedEvent            │
                    │    (新资产入主库后触发)                  │
                    └────────────┬──────────────────────────┘
                                 │
                    ┌────────────▼──────────────────────────┐
                    │        AI Task Dispatcher              │
                    │  (读取新增 asset_rel，分发至子队列)       │
                    └────┬──────────────┬───────────────────┘
                         │              │
              ┌──────────▼───┐   ┌──────▼────────┐
              │ face_queue   │   │ ocr_queue     │
              │ (face_index  │   │ (ocr_index    │
              │  .db 内)     │   │  .db 内)      │
              └──────┬───────┘   └──────┬────────┘
                     │                  │
           ┌─────────┼────────┐    ┌────┼─────────┐
           │         │        │    │    │          │
        ┌──▼──┐  ┌──▼──┐  ┌──▼──┐ ┌▼──┐  ┌──▼──┐
        │ FW1 │  │ FW2 │  │ FWn │ │OW1│  │ OWn │
        └─────┘  └─────┘  └─────┘ └───┘  └─────┘
        Face Workers (线程)       OCR Workers (线程)
```

**关键要求：**

- **主队列独立性**：`ScannerWorker` → `global_index.db` 写入路径不受 AI 子系统影响。AI Task Dispatcher 通过监听事件被动触发，不参与主库写入。
- **持久化队列**：任务状态持久化在各自数据库内的 `*_queue` 表中，应用崩溃后重启自动恢复未完成任务。
- **可配置 Worker 数**：人脸 Worker 和 OCR Worker 数量独立配置。

### 6.2 资源公平调度策略

采用 **加权公平队列（Weighted Fair Queuing）** 策略确保各 Worker 公平使用系统资源。

```
┌──────────────────────────────────────────────────┐
│              ResourceGovernor                      │
│  ┌──────────────────────────────────────────────┐ │
│  │  GPU Scheduler (CUDA / OpenCL)               │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐   │ │
│  │  │ Slot 1   │  │ Slot 2   │  │ Slot N   │   │ │
│  │  │ (Face)   │  │ (OCR)    │  │ (...)    │   │ │
│  │  └──────────┘  └──────────┘  └──────────┘   │ │
│  └──────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │  CPU Thread Pool                              │ │
│  │  max_threads = os.cpu_count() - 2             │ │
│  │  face_weight = 0.6                            │ │
│  │  ocr_weight  = 0.4                            │ │
│  └──────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │  Memory Budget                                │ │
│  │  max_ai_memory = 3 GiB                        │ │
│  │  per_worker_limit = 1 GiB                     │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**调度规则：**

| 规则 | 说明 |
|------|------|
| **CPU 线程分配** | 预留 2 个核心给 GUI 和主库操作。剩余核心按 `face_weight : ocr_weight`（默认 60:40）比例分配给 Face Workers 和 OCR Workers |
| **GPU 时间片** | 如果只有一块 GPU，Face 和 OCR Worker 通过信号量交替使用 GPU（Round-Robin），每次获取 GPU 锁后处理一个批次（batch） |
| **内存上限** | `ResourceGovernor` 通过 `MemoryMonitor`（现有组件）监控内存占用，超过 `per_worker_limit` 时暂停新任务分发 |
| **优先级动态调整** | 用户正在浏览某张照片时，该照片的 AI 任务优先级临时提升至最高（交互优先） |
| **背压控制** | 队列中待处理任务超过 10000 条时，降低入队速率（Dispatcher 每批次间增加 sleep） |

### 6.3 CUDA 加速与 CPU 回退

```python
# 伪代码：Backend 选择策略
class ComputeBackend:
    """在应用启动时检测一次，整个生命周期内复用同一后端。"""

    @staticmethod
    def select() -> str:
        # 1. 尝试 CUDA
        if cv2.cuda.getCudaEnabledDeviceCount() > 0:
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
            return "cuda"

        # 2. 尝试 OpenCL (Intel/AMD GPU)
        if cv2.ocl.haveOpenCL():
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_DEFAULT)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_OPENCL)
            return "opencl"

        # 3. CPU 回退
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        return "cpu"
```

**回退行为矩阵：**

| 硬件环境 | DNN Backend | DNN Target | 预期性能 |
|----------|-------------|------------|---------|
| NVIDIA GPU + CUDA + cuDNN | `DNN_BACKEND_CUDA` | `DNN_TARGET_CUDA` | 最优 |
| Intel/AMD GPU + OpenCL | `DNN_BACKEND_DEFAULT` | `DNN_TARGET_OPENCL` | 中等 |
| 仅 CPU | `DNN_BACKEND_OPENCV` | `DNN_TARGET_CPU` | 基础 |

**运行时监控：**
- 启动时将选择的后端写入日志：`INFO  ComputeBackend: using cuda (NVIDIA GeForce RTX 4090)`
- 如果 CUDA 初始化失败（驱动不兼容等），捕获异常并自动降级，记录 `WARNING`

---

## 7. 外部依赖

| 包名 | 用途 | 版本要求 | 是否已有 |
|------|------|---------|---------|
| `opencv-python-headless` | DNN 推理、图像预处理 | ≥ 4.10 | ✅ 已有 |
| `opencv-contrib-python-headless` | 额外 DNN 模型支持（SFace 等） | ≥ 4.10 | 需新增 |
| `numpy` | 特征向量操作 | ≥ 2.3.4 | ✅ 已有 |
| `scikit-learn` | DBSCAN 聚类算法 | ≥ 1.4 | 需新增 |

> **模型文件**（非 pip 依赖，首次启动时自动下载至 `<library>/.iPhoto/models/`）：
>
> | 模型 | 文件 | 大小 | 用途 |
> |------|------|------|------|
> | YuNet | `face_detection_yunet_2023mar.onnx` | ~220 KB | 人脸检测 |
> | SFace | `face_recognition_sface_2021dec.onnx` | ~37 MB | 人脸特征提取 |
> | EAST | `frozen_east_text_detection.pb` | ~95 MB | 文字区域检测 |
> | CRNN | `crnn_cs_CN.onnx` / `crnn_en.onnx` | ~30 MB | 文字识别 |

---

## 8. 验收标准

| # | 验收项 | 通过标准 |
|---|--------|---------|
| AC-1 | 人脸检测准确率 | 在 LFW 数据集上 Precision ≥ 95%, Recall ≥ 90% |
| AC-2 | 人脸聚类 | 100 张不同人脸照片自动分组后，调整兰德指数 (ARI) ≥ 0.85 |
| AC-3 | 增量聚类 | 新增 10 张照片后，增量聚类耗时 ≤ 全量聚类的 10% |
| AC-4 | 合并聚类 | 合并两个 Person 后，所有人脸归属正确更新，代表脸和中心向量重新计算 |
| AC-5 | 移动单张人脸 | 移动后原 Person 和目标 Person 的 `face_count`、`center_embedding` 正确更新 |
| AC-6 | OCR 中文识别率 | 清晰印刷体中文字符准确率 ≥ 90% |
| AC-7 | 全文搜索延迟 | 10 万条 OCR 记录下搜索响应时间 ≤ 200ms |
| AC-8 | GPU 加速生效 | 有 CUDA 时检测速度 ≥ CPU 回退的 3 倍 |
| AC-9 | 主队列无影响 | AI Worker 满载运行时，主库 `ScannerWorker` 的扫描速率下降 ≤ 10% |
| AC-10 | 优雅退出 | 关闭应用后 5 秒内所有 Worker 退出，无数据损坏 |

---

## 附录 A — 参考项目

| 项目 | 参考要点 |
|------|---------|
| [Apple Photos](https://support.apple.com/guide/photos/) | People & Pets 功能的 UX 流程、自动聚类 + 手动确认模式 |
| [Google Photos](https://photos.google.com/) | 文字搜索图片、人脸识别精度标杆 |
| [Immich](https://github.com/immich-app/immich) | 开源自托管方案，人脸识别 pipeline 设计、机器学习微服务架构 |
| [PhotoPrism](https://github.com/photoprism/photoprism) | TensorFlow-based 人脸检测与聚类、文件级缓存策略 |
| [Synology Photos](https://www.synology.com/en-us/dsm/feature/photos) | 聚类合并/拆分 UI 交互设计、NAS 场景下的后台任务管理 |
| [DigiKam](https://www.digikam.org/) | 开源桌面相册，DNN 人脸识别、深度学习模型管理 |
| [dlib](http://dlib.net/) | 人脸对齐算法、Chinese Whispers 聚类算法参考 |

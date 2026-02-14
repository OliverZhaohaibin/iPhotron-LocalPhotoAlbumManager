# 📋 iPhotron — 人脸识别 & OCR 文字识别 需求文档

> **版本**: 1.0.0  
> **日期**: 2026-02-13  
> **状态**: Draft  
> **模块**: Face Recognition & OCR Indexing  

---

## 目录 / Table of Contents

1. [概述 / Overview](#1-概述--overview)
2. [术语定义 / Glossary](#2-术语定义--glossary)
3. [功能需求 / Functional Requirements](#3-功能需求--functional-requirements)
   - 3.1 [人脸检测与识别 / Face Detection & Recognition](#31-人脸检测与识别--face-detection--recognition)
   - 3.2 [人脸聚类 / Face Clustering](#32-人脸聚类--face-clustering)
   - 3.3 [OCR 文字识别 / OCR Text Recognition](#33-ocr-文字识别--ocr-text-recognition)
4. [数据库设计 / Database Design](#4-数据库设计--database-design)
   - 4.1 [数据库分离策略 / Database Isolation Strategy](#41-数据库分离策略--database-isolation-strategy)
   - 4.2 [人脸数据库 face_index.db](#42-人脸数据库-face_indexdb)
   - 4.3 [OCR 数据库 ocr_index.db](#43-ocr-数据库-ocr_indexdb)
   - 4.4 [主库关联 / Main DB Relation](#44-主库关联--main-db-relation)
5. [CUDA 加速 / CUDA Acceleration](#5-cuda-加速--cuda-acceleration)
6. [多队列架构 / Multi-Queue Architecture](#6-多队列架构--multi-queue-architecture)
   - 6.1 [队列隔离 / Queue Isolation](#61-队列隔离--queue-isolation)
   - 6.2 [Worker 池与公平调度 / Worker Pool & Fair Scheduling](#62-worker-池与公平调度--worker-pool--fair-scheduling)
   - 6.3 [资源限制与背压 / Resource Limits & Back-Pressure](#63-资源限制与背压--resource-limits--back-pressure)
7. [非功能需求 / Non-Functional Requirements](#7-非功能需求--non-functional-requirements)
8. [参考 / References](#8-参考--references)

---

## 1. 概述 / Overview

本文档定义 iPhotron 项目中**人脸识别**与 **OCR 文字识别**两大子系统的完整需求。两个子系统各自拥有独立的 SQLite 数据库（`face_index.db` 和 `ocr_index.db`），与主库 `global_index.db` 物理隔离，通过 `rel`（资产相对路径）字段进行逻辑关联。

核心目标：

| 目标 | 说明 |
|------|------|
| **人脸检测** | 基于 OpenCV DNN 模块检测照片中的人脸区域 |
| **人脸嵌入** | 提取 128-D 人脸特征向量用于比对 |
| **人脸聚类** | 无监督聚类将同一人的人脸自动归组 |
| **聚类管理** | 支持合并聚类、拆分、移动单张到其他聚类等交互操作 |
| **OCR 文字提取** | 识别照片中的文字内容并入库 |
| **文字搜图** | 根据 OCR 提取的文字内容反向搜索图片 |
| **CUDA 加速** | 可选使用 CUDA 后端加速 DNN 推理 |
| **队列隔离** | 人脸/OCR 入库不阻塞主扫描队列 |
| **公平调度** | 多 Worker 间公平分配 CPU/GPU 资源 |

---

## 2. 术语定义 / Glossary

| 术语 | 定义 |
|------|------|
| **rel** | 资产相对于图库根目录的路径，主库 `assets` 表主键 |
| **Face ROI** | 人脸感兴趣区域 (Region of Interest)，以 `(x, y, w, h)` 归一化坐标表示 |
| **Embedding** | 人脸特征向量，128 维浮点数组，用于计算余弦相似度 |
| **Cluster** | 一组被判定为同一人的人脸集合 |
| **Person** | 用户确认命名后的聚类，具有唯一 person_id |
| **Worker** | 运行在 QThreadPool 中的 QRunnable 后台任务 |
| **Primary Queue** | 主扫描入库队列（ScannerWorker → global_index.db） |
| **Secondary Queue** | 人脸/OCR 处理队列，与 Primary Queue 隔离运行 |

---

## 3. 功能需求 / Functional Requirements

### 3.1 人脸检测与识别 / Face Detection & Recognition

#### 3.1.1 检测引擎

- **FR-FACE-010**: 使用 OpenCV DNN 模块加载预训练人脸检测模型（推荐 YuNet / `face_detection_yunet`）。
- **FR-FACE-011**: 检测模型须支持多种输入尺寸，默认 `320×320`，当图像分辨率高于 2000px 时自动缩放。
- **FR-FACE-012**: 检测置信度阈值默认 `0.7`，用户可在设置中调整范围 `[0.5, 0.95]`。
- **FR-FACE-013**: 对每张图片输出零到多个人脸区域，每个区域包含：
  - 归一化边界框 `(x, y, w, h)` — 值域 `[0.0, 1.0]`
  - 置信度 `confidence` — `float`
  - 五个关键点 `landmarks` — 左眼、右眼、鼻尖、左嘴角、右嘴角（归一化坐标）

#### 3.1.2 嵌入提取

- **FR-FACE-020**: 使用 OpenCV `FaceRecognizerSF`（SFace 模型）提取 128 维嵌入向量。
- **FR-FACE-021**: 嵌入向量须 L2 归一化后存储，便于后续余弦相似度计算。
- **FR-FACE-022**: 嵌入提取前须对人脸区域进行对齐（基于五点关键点做仿射变换，目标尺寸 `112×112`）。

#### 3.1.3 质量过滤

- **FR-FACE-030**: 检测到的人脸 ROI 面积小于 `48×48` 像素的，标记为 `low_quality`，仍入库但不参与聚类。
- **FR-FACE-031**: 人脸模糊度（拉普拉斯方差）低于阈值 `100` 的，标记为 `blurry`。
- **FR-FACE-032**: 人脸偏转角（基于关键点估算 yaw）超过 ±45° 的，标记为 `side_face`。

---

### 3.2 人脸聚类 / Face Clustering

#### 3.2.1 自动聚类

- **FR-CLUS-010**: 使用基于余弦距离的层次聚类（Agglomerative Clustering），默认距离阈值 `0.40`。
- **FR-CLUS-011**: 当人脸数 < 10,000 时使用全量聚类；超过时采用增量聚类（Mini-Batch 策略）：
  - 新入库的人脸与现有聚类中心比较
  - 距离小于阈值则合入最近聚类
  - 否则创建新聚类
- **FR-CLUS-012**: 聚类运行后须计算并持久化每个聚类的中心向量（centroid）。
- **FR-CLUS-013**: 支持用户手动设置距离阈值，范围 `[0.20, 0.60]`，默认 `0.40`。

#### 3.2.2 聚类管理操作

- **FR-CLUS-020**: **合并聚类** — 用户选择两个或多个聚类合并为一个，保留其中一个的 `person_id`，更新所有关联人脸记录。
- **FR-CLUS-021**: **拆分聚类** — 对选中聚类重新运行聚类算法（使用更严格的阈值），生成子聚类。
- **FR-CLUS-022**: **移动单张** — 将一张人脸从当前聚类移到目标聚类，更新 `cluster_id` 和目标聚类中心向量。
- **FR-CLUS-023**: **命名/重命名** — 为聚类指定人名（`person_name`），创建或更新 `persons` 表记录。
- **FR-CLUS-024**: **隐藏聚类** — 标记聚类为 `hidden`，不在 UI 聚类列表中显示，但保留数据。
- **FR-CLUS-025**: **删除聚类** — 软删除，将聚类中所有人脸的 `cluster_id` 设为 `NULL`，聚类标记为 `deleted`。

#### 3.2.3 增量更新

- **FR-CLUS-030**: 新照片入库后，自动对新检测到的人脸运行增量聚类。
- **FR-CLUS-031**: 增量聚类仅处理 `cluster_id IS NULL` 的人脸记录。
- **FR-CLUS-032**: 用户手动操作（合并/移动）后触发受影响聚类的中心向量重算。

---

### 3.3 OCR 文字识别 / OCR Text Recognition

#### 3.3.1 文字检测

- **FR-OCR-010**: 使用 OpenCV DNN 加载文字检测模型（推荐 DB / EAST 文字检测器）。
- **FR-OCR-011**: 检测输出文字区域的旋转矩形边界框。
- **FR-OCR-012**: 过滤置信度低于 `0.5` 的检测结果。

#### 3.3.2 文字识别

- **FR-OCR-020**: 使用 OpenCV DNN 加载文字识别模型（推荐 CRNN），支持中英文混合识别。
- **FR-OCR-021**: 识别结果包含：
  - 文字内容 `text` — `TEXT`
  - 识别置信度 `confidence` — `REAL`
  - 语言标签 `lang` — `TEXT`（`zh` / `en` / `mixed`）
- **FR-OCR-022**: 对整张图片的所有文字区域进行合并，生成 `full_text`（按阅读顺序拼接）。

#### 3.3.3 文字搜图

- **FR-OCR-030**: 支持全文搜索——用户输入关键词，返回包含该文字的所有图片。
- **FR-OCR-031**: 搜索须支持模糊匹配（SQLite FTS5 或 LIKE 查询）。
- **FR-OCR-032**: 搜索结果按匹配度降序排列，匹配度由匹配的文字区域数量和置信度加权计算。

---

## 4. 数据库设计 / Database Design

### 4.1 数据库分离策略 / Database Isolation Strategy

```
<LibraryRoot>/
└── .iPhoto/
    ├── global_index.db          ← 主库（资产元数据）
    ├── global_index.db-wal
    ├── face_index.db            ← 人脸独立库（新增）
    ├── face_index.db-wal
    ├── ocr_index.db             ← OCR 独立库（新增）
    ├── ocr_index.db-wal
    ├── models/                  ← DNN 模型文件目录（新增）
    │   ├── face_detection_yunet_2023mar.onnx
    │   ├── face_recognition_sface_2021dec.onnx
    │   ├── text_detection_db.onnx
    │   └── text_recognition_crnn.onnx
    └── ...
```

**设计原则：**

| 原则 | 说明 |
|------|------|
| **物理隔离** | 三个 `.db` 文件独立存在，互不锁定 |
| **逻辑关联** | 通过 `rel` 字段与主库 `assets.rel` 建立外键语义关联（非物理外键） |
| **独立 WAL** | 每个库各自使用 WAL 模式，写入互不阻塞 |
| **可丢弃** | `face_index.db` 和 `ocr_index.db` 均可安全删除后从原图重建 |
| **增量构建** | 仅处理尚未在对应库中存在记录的新资产 |

---

### 4.2 人脸数据库 face_index.db

#### 表 `faces` — 人脸记录

存储每张检测到的人脸及其嵌入向量。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `face_id` | TEXT | PRIMARY KEY | UUID v4，人脸唯一标识 |
| `rel` | TEXT | NOT NULL, INDEX | 关联主库 `assets.rel`，资产相对路径 |
| `bbox_x` | REAL | NOT NULL | 归一化边界框 x（左上角），值域 [0,1] |
| `bbox_y` | REAL | NOT NULL | 归一化边界框 y（左上角），值域 [0,1] |
| `bbox_w` | REAL | NOT NULL | 归一化边界框宽度，值域 [0,1] |
| `bbox_h` | REAL | NOT NULL | 归一化边界框高度，值域 [0,1] |
| `confidence` | REAL | NOT NULL | 检测置信度 |
| `landmarks` | TEXT | | 5 个关键点的 JSON 数组，如 `[[0.3,0.4],[0.6,0.4],...]` |
| `embedding` | BLOB | | 128-D 浮点向量，以 `numpy.float32` 序列化存储 |
| `quality_flags` | TEXT | DEFAULT '' | 质量标记，逗号分隔，如 `low_quality,blurry` |
| `cluster_id` | TEXT | INDEX | 所属聚类 ID，未聚类时为 NULL |
| `is_representative` | INTEGER | DEFAULT 0 | 是否为聚类代表面孔（封面） |
| `created_at` | TEXT | NOT NULL | ISO 8601 创建时间 |
| `updated_at` | TEXT | NOT NULL | ISO 8601 最后更新时间 |

```sql
CREATE TABLE IF NOT EXISTS faces (
    face_id            TEXT    PRIMARY KEY,
    rel                TEXT    NOT NULL,
    bbox_x             REAL    NOT NULL,
    bbox_y             REAL    NOT NULL,
    bbox_w             REAL    NOT NULL,
    bbox_h             REAL    NOT NULL,
    confidence         REAL    NOT NULL,
    landmarks          TEXT,
    embedding          BLOB,
    quality_flags      TEXT    DEFAULT '',
    cluster_id         TEXT,
    is_representative  INTEGER DEFAULT 0,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_faces_rel ON faces (rel);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces (cluster_id);
CREATE INDEX IF NOT EXISTS idx_faces_unassigned ON faces (cluster_id) WHERE cluster_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_faces_representative ON faces (cluster_id, is_representative)
    WHERE is_representative = 1;
```

#### 表 `clusters` — 聚类记录

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `cluster_id` | TEXT | PRIMARY KEY | UUID v4，聚类唯一标识 |
| `person_id` | TEXT | INDEX | 关联 `persons.person_id`，命名后填充 |
| `centroid` | BLOB | | 聚类中心向量，128-D float32 |
| `face_count` | INTEGER | DEFAULT 0 | 聚类中人脸数量（冗余计数，触发器维护） |
| `status` | TEXT | DEFAULT 'active' | `active` / `hidden` / `deleted` |
| `created_at` | TEXT | NOT NULL | ISO 8601 |
| `updated_at` | TEXT | NOT NULL | ISO 8601 |

```sql
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id   TEXT    PRIMARY KEY,
    person_id    TEXT,
    centroid     BLOB,
    face_count   INTEGER DEFAULT 0,
    status       TEXT    DEFAULT 'active',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clusters_person ON clusters (person_id);
CREATE INDEX IF NOT EXISTS idx_clusters_status ON clusters (status);
```

#### 表 `persons` — 人物信息

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `person_id` | TEXT | PRIMARY KEY | UUID v4，人物唯一标识 |
| `name` | TEXT | NOT NULL | 人物名称 |
| `avatar_face_id` | TEXT | | 头像所用的 face_id |
| `is_favorite` | INTEGER | DEFAULT 0 | 是否收藏 |
| `created_at` | TEXT | NOT NULL | ISO 8601 |
| `updated_at` | TEXT | NOT NULL | ISO 8601 |

```sql
CREATE TABLE IF NOT EXISTS persons (
    person_id      TEXT    PRIMARY KEY,
    name           TEXT    NOT NULL,
    avatar_face_id TEXT,
    is_favorite    INTEGER DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
```

#### 表 `face_process_log` — 处理进度跟踪

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `rel` | TEXT | PRIMARY KEY | 已处理的资产路径 |
| `status` | TEXT | NOT NULL | `done` / `error` / `skipped` |
| `face_count` | INTEGER | DEFAULT 0 | 检测到的人脸数 |
| `error_msg` | TEXT | | 错误信息 |
| `processed_at` | TEXT | NOT NULL | ISO 8601 处理时间 |

```sql
CREATE TABLE IF NOT EXISTS face_process_log (
    rel           TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    face_count    INTEGER DEFAULT 0,
    error_msg     TEXT,
    processed_at  TEXT NOT NULL
);
```

---

### 4.3 OCR 数据库 ocr_index.db

#### 表 `ocr_regions` — 文字区域

存储每个检测到的文字区域及其识别结果。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `region_id` | TEXT | PRIMARY KEY | UUID v4，区域唯一标识 |
| `rel` | TEXT | NOT NULL, INDEX | 关联主库 `assets.rel` |
| `bbox_x` | REAL | NOT NULL | 归一化边界框 x |
| `bbox_y` | REAL | NOT NULL | 归一化边界框 y |
| `bbox_w` | REAL | NOT NULL | 归一化边界框宽度 |
| `bbox_h` | REAL | NOT NULL | 归一化边界框高度 |
| `rotation` | REAL | DEFAULT 0 | 文字区域旋转角度（度） |
| `text` | TEXT | NOT NULL | 识别出的文字内容 |
| `confidence` | REAL | NOT NULL | 识别置信度 |
| `lang` | TEXT | DEFAULT 'unknown' | 语言标签：`zh` / `en` / `mixed` / `unknown` |
| `created_at` | TEXT | NOT NULL | ISO 8601 |

```sql
CREATE TABLE IF NOT EXISTS ocr_regions (
    region_id   TEXT PRIMARY KEY,
    rel         TEXT NOT NULL,
    bbox_x      REAL NOT NULL,
    bbox_y      REAL NOT NULL,
    bbox_w      REAL NOT NULL,
    bbox_h      REAL NOT NULL,
    rotation    REAL DEFAULT 0,
    text        TEXT NOT NULL,
    confidence  REAL NOT NULL,
    lang        TEXT DEFAULT 'unknown',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ocr_regions_rel ON ocr_regions (rel);
```

#### 表 `ocr_documents` — 整图文字聚合

将同一张图片所有文字区域合并为完整文档，便于全文搜索。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `rel` | TEXT | PRIMARY KEY | 关联主库 `assets.rel` |
| `full_text` | TEXT | NOT NULL | 所有文字区域按阅读顺序合并的完整文本 |
| `region_count` | INTEGER | DEFAULT 0 | 文字区域数量 |
| `avg_confidence` | REAL | | 平均识别置信度 |
| `primary_lang` | TEXT | DEFAULT 'unknown' | 主要语言 |
| `updated_at` | TEXT | NOT NULL | ISO 8601 |

```sql
CREATE TABLE IF NOT EXISTS ocr_documents (
    rel             TEXT PRIMARY KEY,
    full_text       TEXT NOT NULL,
    region_count    INTEGER DEFAULT 0,
    avg_confidence  REAL,
    primary_lang    TEXT DEFAULT 'unknown',
    updated_at      TEXT NOT NULL
);
```

#### FTS5 全文搜索虚拟表

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
    full_text,
    content='ocr_documents',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

-- 触发器：ocr_documents 插入/更新/删除时同步 FTS 索引
CREATE TRIGGER IF NOT EXISTS ocr_fts_insert AFTER INSERT ON ocr_documents BEGIN
    INSERT INTO ocr_fts (rowid, full_text)
    VALUES (new.rowid, new.full_text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_fts_delete AFTER DELETE ON ocr_documents BEGIN
    INSERT INTO ocr_fts (ocr_fts, rowid, full_text)
    VALUES ('delete', old.rowid, old.full_text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_fts_update AFTER UPDATE ON ocr_documents BEGIN
    INSERT INTO ocr_fts (ocr_fts, rowid, full_text)
    VALUES ('delete', old.rowid, old.full_text);
    INSERT INTO ocr_fts (rowid, full_text)
    VALUES (new.rowid, new.full_text);
END;
```

#### 表 `ocr_process_log` — 处理进度跟踪

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `rel` | TEXT | PRIMARY KEY | 已处理的资产路径 |
| `status` | TEXT | NOT NULL | `done` / `error` / `skipped` |
| `region_count` | INTEGER | DEFAULT 0 | 检测到的文字区域数 |
| `error_msg` | TEXT | | 错误信息 |
| `processed_at` | TEXT | NOT NULL | ISO 8601 处理时间 |

```sql
CREATE TABLE IF NOT EXISTS ocr_process_log (
    rel            TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    region_count   INTEGER DEFAULT 0,
    error_msg      TEXT,
    processed_at   TEXT NOT NULL
);
```

---

### 4.4 主库关联 / Main DB Relation

三个数据库之间的关联通过 `rel` 字段在应用层实现逻辑关联，**不使用跨库外键**：

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  global_index.db    │     │   face_index.db      │     │   ocr_index.db      │
│                     │     │                      │     │                     │
│  assets             │     │  faces               │     │  ocr_regions        │
│  ┌───────────────┐  │     │  ┌────────────────┐  │     │  ┌───────────────┐  │
│  │ rel (PK) ─────┼──┼─────┼──│ rel (INDEX) ───┼──┼─────┼──│ rel (INDEX)   │  │
│  │ dt            │  │     │  │ face_id (PK)   │  │     │  │ region_id(PK) │  │
│  │ parent_album  │  │     │  │ bbox_*         │  │     │  │ bbox_*        │  │
│  │ media_type    │  │     │  │ embedding      │  │     │  │ text          │  │
│  │ ...           │  │     │  │ cluster_id ────┼──┤     │  │ confidence    │  │
│  └───────────────┘  │     │  └────────────────┘  │     │  └───────────────┘  │
│                     │     │                      │     │                     │
│                     │     │  clusters             │     │  ocr_documents      │
│                     │     │  ┌────────────────┐  │     │  ┌───────────────┐  │
│                     │     │  │ cluster_id(PK) │  │     │  │ rel (PK)      │  │
│                     │     │  │ person_id ─────┼──┤     │  │ full_text     │  │
│                     │     │  │ centroid       │  │     │  └───────────────┘  │
│                     │     │  └────────────────┘  │     │                     │
│                     │     │                      │     │  ocr_fts (FTS5)     │
│                     │     │  persons             │     │  ┌───────────────┐  │
│                     │     │  ┌────────────────┐  │     │  │ full_text     │  │
│                     │     │  │ person_id (PK) │  │     │  └───────────────┘  │
│                     │     │  │ name           │  │     │                     │
│                     │     │  └────────────────┘  │     │                     │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

**应用层查询示例（跨库 JOIN 等效）：**

```python
# 1. 查找某人的所有照片
person_face_rels = face_db.execute(
    """SELECT DISTINCT f.rel FROM faces f
       JOIN clusters c ON f.cluster_id = c.cluster_id
       JOIN persons p ON c.person_id = p.person_id
       WHERE p.name = ?""",
    (person_name,)
).fetchall()

assets = main_db.execute(
    "SELECT * FROM assets WHERE rel IN ({})".format(
        ",".join("?" * len(person_face_rels))
    ),
    [r["rel"] for r in person_face_rels]
).fetchall()

# 2. 根据文字搜索图片
ocr_matches = ocr_db.execute(
    """SELECT d.rel, snippet(ocr_fts, 0, '<b>', '</b>', '...', 32) AS snippet
       FROM ocr_fts fts
       JOIN ocr_documents d ON fts.rowid = d.rowid
       WHERE ocr_fts MATCH ?
       ORDER BY rank""",
    (search_query,)
).fetchall()
```

---

## 5. CUDA 加速 / CUDA Acceleration

### 5.1 支持策略

- **FR-CUDA-010**: 应用启动时探测 CUDA 可用性（`cv2.cuda.getCudaEnabledDeviceCount()`）。
- **FR-CUDA-011**: 当 CUDA 可用时，DNN 推理后端自动切换为 `cv2.dnn.DNN_BACKEND_CUDA` + `cv2.dnn.DNN_TARGET_CUDA`。
- **FR-CUDA-012**: 当 CUDA 不可用时，回退至 CPU 后端 `cv2.dnn.DNN_BACKEND_OPENCV` + `cv2.dnn.DNN_TARGET_CPU`。
- **FR-CUDA-013**: 后端选择结果缓存为单例，避免重复探测。

### 5.2 GPU 资源管理

- **FR-CUDA-020**: 同一时刻最多一个 Worker 占用 GPU 进行 DNN 推理（GPU 互斥锁）。
- **FR-CUDA-021**: GPU 内存使用上限可配置（默认不限制，由 CUDA 运行时管理）。
- **FR-CUDA-022**: 当 GPU 推理出错（OOM 等）时自动回退至 CPU，并记录警告日志。

### 5.3 模型加载

- **FR-CUDA-030**: DNN 模型以 ONNX 格式存储在 `<library_root>/.iPhoto/models/` 目录。
- **FR-CUDA-031**: 模型文件首次使用时从应用内置资源复制到工作目录。
- **FR-CUDA-032**: 模型实例在进程内共享（单例），避免重复加载。

---

## 6. 多队列架构 / Multi-Queue Architecture

### 6.1 队列隔离 / Queue Isolation

```
                    ┌────────────────────────────────────────┐
                    │          BackgroundTaskManager          │
                    │                                        │
                    │  ┌──────────────────┐                  │
  新文件入库 ──────►│  │  Primary Queue   │──► global_index.db│
                    │  │  (ScannerWorker) │                  │
                    │  └──────────────────┘                  │
                    │                                        │
                    │  ┌──────────────────┐                  │
  Primary完成 ─────►│  │  Face Queue      │──► face_index.db │
                    │  │  (FaceWorker)    │                  │
                    │  └──────────────────┘                  │
                    │                                        │
                    │  ┌──────────────────┐                  │
  Primary完成 ─────►│  │  OCR Queue       │──► ocr_index.db  │
                    │  │  (OcrWorker)     │                  │
                    │  └──────────────────┘                  │
                    └────────────────────────────────────────┘
```

- **FR-QUEUE-010**: 主扫描队列（Primary Queue）完成一批资产入库后，将新增资产的 `rel` 列表发布到 Face Queue 和 OCR Queue。
- **FR-QUEUE-011**: Face Queue 和 OCR Queue **独立运行**，不阻塞主队列的后续扫描。
- **FR-QUEUE-012**: 各队列使用独立的数据库连接，写入互不阻塞（WAL 模式保证）。
- **FR-QUEUE-013**: 队列消费采用 FIFO 顺序，支持优先级覆盖（用户手动触发的处理优先）。

### 6.2 Worker 池与公平调度 / Worker Pool & Fair Scheduling

```
             QThreadPool (global)
        ┌────────────────────────────┐
        │  maxThreadCount = N        │
        │                            │
        │  ┌────────┐ ┌────────┐    │
        │  │Thread 1│ │Thread 2│    │    Reserved for Primary Queue
        │  └────────┘ └────────┘    │    (scan / import / move)
        │                            │
        │  ┌────────┐ ┌────────┐    │
        │  │Thread 3│ │Thread 4│    │    Shared: Face + OCR Workers
        │  └────────┘ └────────┘    │    (round-robin fair share)
        │                            │
        │  ┌────────┐               │
        │  │Thread 5│               │    Reserved for UI tasks
        │  └────────┘               │    (thumbnail / preview)
        └────────────────────────────┘
```

- **FR-SCHED-010**: `QThreadPool` 总线程数 `N = max(4, cpu_count)`，确保至少 4 个工作线程。
- **FR-SCHED-011**: 线程分配策略：
  - **主队列保留**: `ceil(N × 0.3)` 个线程专用于主扫描/导入/移动任务
  - **UI 保留**: 至少 1 个线程专用于缩略图/预览等 UI 任务
  - **次队列共享**: 剩余线程由 Face Worker 和 OCR Worker 公平分享
- **FR-SCHED-012**: Face 和 OCR Worker 通过**加权轮询 (Weighted Round-Robin)** 公平调度：
  - 默认权重 Face:OCR = 1:1
  - 当积压量差异超过 2 倍时，自动调整为 2:1 或 1:2 倾斜
- **FR-SCHED-013**: 次队列 Worker 的优先级 (`QRunnable.setAutoDelete`, priority) 低于主队列和 UI 任务。

### 6.3 资源限制与背压 / Resource Limits & Back-Pressure

- **FR-SCHED-020**: 每个次队列维护待处理队列长度上限（默认 `1000`），达到上限后暂停生产端。
- **FR-SCHED-021**: 当系统内存使用超过 80% 时，次队列 Worker 主动暂停，等待内存回落到 70% 以下后恢复。
- **FR-SCHED-022**: GPU 推理互斥锁等待超时 `30s` 后，Worker 回退至 CPU 执行当前任务。
- **FR-SCHED-023**: Worker 处理单张图片的超时时间为 `60s`，超时后跳过并记录错误到 `process_log`。

---

## 7. 非功能需求 / Non-Functional Requirements

| 编号 | 类别 | 需求 |
|------|------|------|
| NFR-001 | **性能** | 人脸检测单张 ≤ 200ms (GPU) / ≤ 500ms (CPU)，目标分辨率 2000px |
| NFR-002 | **性能** | OCR 单张 ≤ 300ms (GPU) / ≤ 800ms (CPU) |
| NFR-003 | **性能** | 10,000 张人脸全量聚类 ≤ 30s |
| NFR-004 | **吞吐** | 稳态吞吐 ≥ 5 张/秒 (GPU) / ≥ 2 张/秒 (CPU) |
| NFR-005 | **可靠性** | Worker 崩溃不影响主应用进程，错误记录到 process_log |
| NFR-006 | **可恢复** | face_index.db / ocr_index.db 删除后可从原图完全重建 |
| NFR-007 | **存储** | 128-D float32 嵌入 = 512 bytes/人脸，10 万人脸 ≈ 50 MB |
| NFR-008 | **兼容性** | 支持 opencv-python-headless ≥ 4.10，opencv-contrib-python 可选 |
| NFR-009 | **跨平台** | Windows / macOS / Linux 均可运行，CUDA 仅限 NVIDIA GPU |
| NFR-010 | **可测试** | 所有核心逻辑（检测/嵌入/聚类/OCR）须有单元测试覆盖 |

---

## 8. 参考 / References

| 资源 | 链接 |
|------|------|
| OpenCV DNN Face Detection (YuNet) | https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html |
| OpenCV FaceRecognizerSF (SFace) | https://docs.opencv.org/4.x/da/d60/tutorial_face_main.html |
| OpenCV DNN Text Detection (EAST/DB) | https://docs.opencv.org/4.x/d4/d43/tutorial_dnn_text_spotting.html |
| SQLite FTS5 Full-Text Search | https://www.sqlite.org/fts5.html |
| SQLite WAL Mode | https://www.sqlite.org/wal.html |
| scikit-learn AgglomerativeClustering | https://scikit-learn.org/stable/modules/generated/sklearn.cluster.AgglomerativeClustering.html |
| Apple Photos (参考实现) | macOS Photos.app — People & Text 功能 |
| Google Photos (参考实现) | Google Photos — Face Grouping & Lens OCR |
| DigiKam Face Recognition | https://www.digikam.org/documentation/ |
| Immich (开源相册) | https://github.com/immich-app/immich |
| PhotoPrism (开源相册) | https://github.com/photoprism/photoprism |

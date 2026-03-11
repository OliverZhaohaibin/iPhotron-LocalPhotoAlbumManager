# 🔍 Immich 人脸识别 / OCR 集成可行性评估

> 版本 1.0 · 2026-03-08
>
> 本文档评估将 [Immich](https://github.com/immich-app/immich) 项目中 **人脸识别** 与 **OCR 文字识别** 模块集成到 iPhotron 的可行性与工作量，并与基于 OpenCV DNN 从零开发的方案进行对比分析。

---

## 目录

1. [评估背景](#1-评估背景)
2. [Immich ML 架构分析](#2-immich-ml-架构分析)
   - 2.1 [总体架构](#21-总体架构)
   - 2.2 [人脸识别实现](#22-人脸识别实现)
   - 2.3 [OCR 实现](#23-ocr-实现)
   - 2.4 [硬件加速支持](#24-硬件加速支持)
3. [iPhotron 现有方案回顾](#3-iphotron-现有方案回顾)
4. [集成方案分析](#4-集成方案分析)
   - 4.1 [方案 A — 直接集成 Immich ML 核心代码](#41-方案-a--直接集成-immich-ml-核心代码)
   - 4.2 [方案 B — 提取 Immich 核心依赖库独立集成](#42-方案-b--提取-immich-核心依赖库独立集成)
   - 4.3 [方案 C — 基于 OpenCV DNN 从零开发（现有方案）](#43-方案-c--基于-opencv-dnn-从零开发现有方案)
5. [三方案对比](#5-三方案对比)
   - 5.1 [功能对比](#51-功能对比)
   - 5.2 [技术指标对比](#52-技术指标对比)
   - 5.3 [工作量对比](#53-工作量对比)
   - 5.4 [依赖与包体积对比](#54-依赖与包体积对比)
   - 5.5 [维护性对比](#55-维护性对比)
6. [风险评估](#6-风险评估)
7. [推荐方案](#7-推荐方案)
8. [参考资料](#8-参考资料)

---

## 1. 评估背景

iPhotron 当前已规划人脸识别与 OCR 子系统（见 `requirements.md` 与 `development.md`），方案基于 **OpenCV DNN 模块**（YuNet 检测 + SFace 识别）和 **EAST + CRNN** 文字识别管线。

Immich 是一款成熟的开源自托管照片管理平台，其机器学习子系统（`machine-learning/`）已在生产环境中验证，支持人脸识别和 OCR。本文评估将 Immich 的 ML 能力引入 iPhotron 的可行性。

### 评估目标

| # | 问题 | 
|---|------|
| Q1 | Immich 的人脸识别 / OCR 模块能否直接集成到 iPhotron？ |
| Q2 | 集成 vs 从零开发，工作量差异多大？ |
| Q3 | 两种方案在精度、性能、维护性上的优劣如何？ |
| Q4 | 推荐 iPhotron 采用哪种路线？ |

---

## 2. Immich ML 架构分析

### 2.1 总体架构

Immich 的 ML 系统是一个 **独立的 FastAPI 微服务**，通过 HTTP API 向主服务提供推理能力。

```
┌────────────────────────────────────┐
│         Immich Server (Node.js)    │
│  ┌──────────┐   ┌──────────────┐  │
│  │ 照片上传  │──▶│ ML API 调用   │  │
│  └──────────┘   └──────┬───────┘  │
└─────────────────────────┼──────────┘
                          │ HTTP POST /predict
                          ▼
┌─────────────────────────────────────┐
│     Machine Learning Service        │
│           (FastAPI + Python)        │
│  ┌──────────┐  ┌──────┐  ┌─────┐  │
│  │FaceDetect│  │FaceRec│  │ OCR │  │
│  │(InsightF)│  │(ArcFac│  │(Rapid│  │
│  │          │  │  e)   │  │ OCR)│  │
│  └────┬─────┘  └──┬───┘  └──┬──┘  │
│       └────────────┴─────────┘     │
│          ONNX Runtime              │
│    (CUDA / OpenVINO / CPU)         │
└─────────────────────────────────────┘
```

**关键特征：**

| 特征 | 说明 |
|------|------|
| **部署方式** | 独立微服务，通过 Docker 部署 |
| **框架** | FastAPI + uvicorn |
| **推理引擎** | ONNX Runtime（支持多种硬件后端） |
| **模型来源** | HuggingFace Hub 自动下载 |
| **模型缓存** | LRU + TTL 自动卸载机制 |
| **许可证** | AGPL-3.0 ⚠️ |

### 2.2 人脸识别实现

Immich 采用 **InsightFace** 库的两阶段管线：

#### 检测阶段 — `FaceDetector`

| 项目 | 详情 |
|------|------|
| **库** | `insightface` (RetinaFace) |
| **模型** | `antelopev2` / `buffalo_l` / `buffalo_m` / `buffalo_s` |
| **输入尺寸** | 640 × 640 |
| **输出** | 边界框 (x1, y1, x2, y2) + 置信分数 + 5点关键点 |
| **默认阈值** | `minScore = 0.7` |

```python
# Immich FaceDetector 核心逻辑（简化）
class FaceDetector(InferenceModel):
    def _load(self):
        session = self._make_session(self.model_path)
        self.model = RetinaFace(session=session)
        self.model.prepare(ctx_id=0, det_thresh=self.min_score,
                          input_size=(640, 640))

    def _predict(self, inputs):
        bboxes, landmarks = self._detect(inputs)
        return {"boxes": bboxes[:, :4], "scores": bboxes[:, 4],
                "landmarks": landmarks}
```

#### 识别阶段 — `FaceRecognizer`

| 项目 | 详情 |
|------|------|
| **库** | `insightface` (ArcFaceONNX) |
| **特征维度** | 512-D L2 归一化向量 |
| **对齐方式** | `norm_crop` — 基于5点关键点的仿射变换 |
| **批处理** | 支持可配置批大小 |

```python
# Immich FaceRecognizer 核心逻辑（简化）
class FaceRecognizer(InferenceModel):
    def _predict(self, inputs, faces):
        cropped = [norm_crop(inputs, lm) for lm in faces["landmarks"]]
        embeddings = self._predict_batch(cropped)
        return self.postprocess(faces, embeddings)
```

### 2.3 OCR 实现

Immich 采用 **RapidOCR**（基于百度 PaddleOCR）的两阶段管线：

#### 文字检测 — `TextDetector`

| 项目 | 详情 |
|------|------|
| **库** | `rapidocr` |
| **模型** | PP-OCRv5 (server / mobile 变体) |
| **检测阈值** | `threshold = 0.3`，`box_threshold = 0.5` |
| **最大分辨率** | 736px |
| **输出** | 文字区域多边形坐标 + 区域置信分数 |

#### 文字识别 — `TextRecognizer`

| 项目 | 详情 |
|------|------|
| **库** | `rapidocr` |
| **模型** | PP-OCRv5 多语言变体 (中/英/日/韩等) |
| **批大小** | 默认 6 |
| **识别输入** | (3, 48, 320) — 彩色图，48px 高 |
| **输出** | 识别文本字符串 + 字符级置信分数 |

### 2.4 硬件加速支持

Immich 通过 ONNX Runtime 的 Execution Provider 机制支持多种硬件：

| 后端 | 库 | 适用平台 |
|------|-----|---------|
| CUDA | `onnxruntime-gpu` | NVIDIA GPU |
| MIGraphX | `onnxruntime-migraphx` | AMD ROCm GPU |
| OpenVINO | `onnxruntime-openvino` | Intel GPU / CPU |
| CoreML | 内置 | Apple Silicon |
| ARM NN | `onnxruntime` + ARM backend | ARM64 设备 |
| RKNN | `rknn-toolkit-lite2` | Rockchip SoC |
| CPU | `onnxruntime` | 通用回退 |

---

## 3. iPhotron 现有方案回顾

根据 `requirements.md` 和 `development.md`，iPhotron 当前规划方案如下：

### 人脸识别

| 环节 | 技术选型 |
|------|---------|
| 检测 | OpenCV DNN + YuNet ONNX 模型 |
| 特征提取 | OpenCV DNN + SFace ONNX 模型 |
| 对齐 | 5点关键点仿射变换 |
| 聚类 | DBSCAN（scikit-learn） |
| 特征维度 | 128-D 或 512-D |

### OCR

| 环节 | 技术选型 |
|------|---------|
| 文字检测 | EAST / DB 模型 (OpenCV DNN) |
| 文字识别 | CRNN 模型 (OpenCV DNN) |
| 全文搜索 | SQLite FTS5 |

### 核心设计

- **嵌入式部署**：无需独立服务，作为 iPhotron 进程内模块运行
- **异步工作队列**：多 Worker 线程消费持久化任务队列
- **独立数据库**：`face_index.db` + `ocr_index.db`，不修改主库
- **GPU 优先**：CUDA → OpenCL → CPU 自动回退
- **预估开发周期**：~11.5 周

---

## 4. 集成方案分析

### 4.1 方案 A — 直接集成 Immich ML 核心代码

**思路**：将 Immich `machine-learning/immich_ml/` 中的模型推理代码抽取并嵌入 iPhotron。

#### 需要做的工作

| 步骤 | 工作内容 | 工作量 |
|------|---------|--------|
| A1 | 剥离 FastAPI 服务层，仅保留模型推理类 | 1 周 |
| A2 | 将 `InferenceModel` 基类适配为 iPhotron 的嵌入式调用模式 | 1 周 |
| A3 | 重写模型下载逻辑（Immich 使用 HuggingFace Hub，需适配离线/代理场景） | 0.5 周 |
| A4 | 适配 iPhotron 的 MVVM + DDD 架构（Repository、UseCase、ViewModel 层） | 2 周 |
| A5 | 实现 Worker 队列 + 资源调度（Immich 无此部分，使用 FastAPI 线程池） | 1.5 周 |
| A6 | 实现聚类管理（合并/拆分/移动/撤销）（Immich 无此功能） | 1 周 |
| A7 | 实现数据库层（Immich 使用 PostgreSQL + pgvector，需重写为 SQLite） | 1.5 周 |
| A8 | 实现 GUI 组件（人物视图、搜索面板等）| 2 周 |
| A9 | 测试 + 调优 + 文档 | 1.5 周 |
| **总计** | | **~12 周** |

#### 关键障碍

1. **许可证冲突 ⚠️ 严重**
   - Immich 采用 **AGPL-3.0** 许可证
   - iPhotron 采用 **MIT** 许可证
   - AGPL-3.0 要求所有衍生作品也必须以 AGPL-3.0 发布，**直接复制代码将迫使 iPhotron 整体转为 AGPL-3.0**
   - 即使仅"参考"实现，若代码实质性相似，也可能构成侵权风险

2. **架构不匹配**
   - Immich ML 是**无状态微服务**（FastAPI HTTP API），每次请求独立推理
   - iPhotron 是**有状态桌面应用**，需要持久化队列、增量处理、GUI 联动
   - Immich 的模型缓存 / TTL 卸载机制为服务端设计，桌面端需重新设计生命周期

3. **数据库不兼容**
   - Immich 使用 PostgreSQL + TypeORM + pgvector 存储人脸向量
   - iPhotron 使用 SQLite（无 pgvector 扩展），需完全重写数据持久层

4. **缺少关键功能**
   - Immich **不包含**聚类管理功能（合并/拆分/移动/撤销等），这些是 iPhotron 需求的核心交互
   - Immich **不包含**嵌入式任务调度器，其调度逻辑在 Node.js 主服务中
   - Immich **不包含** GUI 组件

5. **依赖膨胀**
   - `insightface` 自带完整的人脸分析管线（2D/3D/表情/年龄/性别等），iPhotron 只需检测+识别
   - `rapidocr` 引入百度 PaddleOCR 的 ONNX 模型生态

#### 结论

> **不推荐直接集成。** 许可证冲突是硬性障碍，架构差异导致适配工作量不亚于重写，且无法复用 Immich 的服务端调度和数据存储逻辑。

---

### 4.2 方案 B — 提取 Immich 核心依赖库独立集成

**思路**：不直接使用 Immich 代码，而是采用 Immich 所依赖的**上游开源库**（InsightFace + RapidOCR），在 iPhotron 架构中独立集成。

#### 依赖库分析

| 库 | 许可证 | 用途 | 与 iPhotron 兼容性 |
|---|--------|------|-------------------|
| `insightface` | MIT ✅ | 人脸检测 + 识别 | 完全兼容 |
| `onnxruntime` | MIT ✅ | 模型推理引擎 | 完全兼容 |
| `rapidocr` | Apache-2.0 ✅ | OCR 检测 + 识别 | 完全兼容 |
| `huggingface-hub` | Apache-2.0 ✅ | 模型下载 | 完全兼容 |
| `opencv-python-headless` | Apache-2.0 ✅ | 图像处理 | 已有依赖 |
| `scikit-learn` | BSD-3-Clause ✅ | DBSCAN 聚类 | 完全兼容 |

> **所有上游库的许可证均与 MIT 兼容**，无许可证风险。

#### 需要做的工作

| 步骤 | 工作内容 | 工作量 |
|------|---------|--------|
| B1 | 基于 InsightFace 实现 FaceDetector + FaceRecognizer | 1 周 |
| B2 | 基于 RapidOCR 实现 TextDetector + TextRecognizer | 0.5 周 |
| B3 | 实现 ComputeBackend（ONNX Runtime EP 检测） | 0.5 周 |
| B4 | 实现模型下载 + 缓存管理 | 0.5 周 |
| B5 | 适配 iPhotron 架构（Repository、UseCase、ViewModel） | 1.5 周 |
| B6 | 实现 Worker 队列 + 资源调度 | 1.5 周 |
| B7 | 实现聚类引擎 + 聚类管理 | 1.5 周 |
| B8 | 实现数据库层（face_index.db + ocr_index.db） | 1 周 |
| B9 | 实现 GUI 组件 | 2 周 |
| B10 | 测试 + 调优 + 文档 | 1.5 周 |
| **总计** | | **~11 周** |

#### 优势

1. **许可证安全**：所有依赖库均为宽松许可证，与 MIT 兼容
2. **更高精度**：InsightFace (RetinaFace + ArcFace) 是当前学术界和工业界公认的最优开源人脸识别方案
3. **更广泛的硬件支持**：ONNX Runtime 原生支持 CUDA / OpenVINO / CoreML / ARM NN 等
4. **成熟的 OCR 方案**：RapidOCR 封装了 PP-OCRv5，多语言支持完善，API 简洁
5. **可参考 Immich 的设计思路**（架构参考不涉及版权问题）

#### 劣势

1. **InsightFace 包体积较大**（~50 MB 安装），含冗余功能（年龄/性别/表情等）
2. **ONNX Runtime 包体积显著**（CPU 版 ~50 MB，GPU 版 ~300 MB）
3. **需要额外依赖管理**（InsightFace 通过 Cython 编译，部分平台可能有兼容性问题）
4. **模型文件需要网络下载**（首次运行需从 HuggingFace / InsightFace 官方下载模型）

---

### 4.3 方案 C — 基于 OpenCV DNN 从零开发（现有方案）

即 `requirements.md` 和 `development.md` 中已规划的方案。

#### 需要做的工作

| 步骤 | 工作内容 | 工作量 |
|------|---------|--------|
| C1 | 实现 ComputeBackend（OpenCV DNN 后端检测） | 1 周 |
| C2 | 实现 FaceDetector（OpenCV DNN + YuNet） | 1.5 周 |
| C3 | 实现 FaceRecognizer（OpenCV DNN + SFace） | 1 周 |
| C4 | 实现人脸对齐 + 质量评估 | 0.5 周 |
| C5 | 实现聚类引擎 + 聚类管理 | 1.5 周 |
| C6 | 实现 OCR 引擎（EAST + CRNN） | 1.5 周 |
| C7 | 实现 Worker 队列 + 资源调度 | 1 周 |
| C8 | 实现数据库层 | 1 周 |
| C9 | 实现 GUI 组件 | 2 周 |
| C10 | 测试 + 调优 + 文档 | 1.5 周 |
| **总计** | | **~12.5 周** |

#### 优势

1. **极简依赖**：仅需 `opencv-python-headless`（已有）+ `scikit-learn`，无额外大型依赖
2. **最小包体积**：YuNet (~220 KB) + SFace (~37 MB) + EAST (~95 MB) + CRNN (~30 MB) ≈ 160 MB 模型总量
3. **完全可控**：所有推理代码自行实现，无外部库 API 变更风险
4. **ONNX 模型可内嵌**：模型文件较小，可直接打包进发布包
5. **与现有架构无缝衔接**：iPhotron 已使用 `opencv-python-headless`

#### 劣势

1. **人脸识别精度较低**
   - YuNet 检测精度低于 RetinaFace（尤其在小脸、遮挡、大角度场景）
   - SFace 识别精度低于 ArcFace（学术基准 LFW 上差距约 0.5-1%）
   - OpenCV DNN 的推理优化程度低于 ONNX Runtime
2. **OCR 精度较低**
   - EAST + CRNN 是较早一代的 OCR 方案
   - PP-OCRv5 在中文/多语言场景下精度显著优于 CRNN
   - EAST 对弯曲文本、旋转文本支持较弱
3. **GPU 加速有限**
   - OpenCV DNN 仅支持 CUDA 后端（不支持 OpenVINO / CoreML / ARM NN）
   - OpenCV DNN 的 CUDA 后端性能低于 ONNX Runtime CUDA EP
4. **维护成本较高**
   - 需自行实现人脸对齐、预处理、后处理等通用管线
   - 模型升级需要手动适配输入输出格式
5. **社区支持较弱**
   - YuNet / SFace / EAST / CRNN 的社区活跃度和迭代速度低于 InsightFace / PaddleOCR

---

## 5. 三方案对比

### 5.1 功能对比

| 功能 | 方案 A (集成 Immich) | 方案 B (上游库独立集成) | 方案 C (OpenCV DNN) |
|------|---------------------|----------------------|-------------------|
| 人脸检测 | ✅ RetinaFace (高精度) | ✅ RetinaFace (高精度) | ⚠️ YuNet (中等精度) |
| 人脸识别 | ✅ ArcFace 512-D | ✅ ArcFace 512-D | ⚠️ SFace 128/512-D |
| 人脸对齐 | ✅ norm_crop | ✅ norm_crop | 🔧 需自行实现 |
| 人脸聚类 | 🔧 需自行实现 | 🔧 需自行实现 | 🔧 需自行实现 |
| 聚类管理 | 🔧 需自行实现 | 🔧 需自行实现 | 🔧 需自行实现 |
| 文字检测 | ✅ PP-OCRv5 (高精度) | ✅ PP-OCRv5 (高精度) | ⚠️ EAST (中等精度) |
| 文字识别 | ✅ PP-OCRv5 多语言 | ✅ PP-OCRv5 多语言 | ⚠️ CRNN (基础精度) |
| 全文搜索 (FTS5) | 🔧 需自行实现 | 🔧 需自行实现 | 🔧 需自行实现 |
| GUI 组件 | 🔧 需自行实现 | 🔧 需自行实现 | 🔧 需自行实现 |

### 5.2 技术指标对比

| 指标 | 方案 A | 方案 B | 方案 C |
|------|--------|--------|--------|
| **人脸检测精度 (WIDER FACE)** | ~94% mAP (hard) | ~94% mAP (hard) | ~86% mAP (hard) |
| **人脸识别精度 (LFW)** | ~99.8% | ~99.8% | ~99.0-99.3% |
| **GPU 推理速度 (人脸)** | ~15 ms/张 (CUDA) | ~15 ms/张 (CUDA) | ~25 ms/张 (CUDA) |
| **CPU 推理速度 (人脸)** | ~100 ms/张 | ~100 ms/张 | ~150 ms/张 |
| **OCR 中文精度** | ~95%+ (PP-OCRv5) | ~95%+ (PP-OCRv5) | ~80-85% (CRNN) |
| **OCR 多语言支持** | 中/英/日/韩/法/德 等 | 中/英/日/韩/法/德 等 | 主要中/英 |
| **GPU 后端支持数** | 6+ (CUDA/OpenVINO/CoreML/ARM NN/MIGraphX/RKNN) | 6+ (同左) | 2 (CUDA/OpenCL) |

> 注：以上数据基于公开学术基准和社区测试报告，实际精度受数据集和硬件差异影响。

### 5.3 工作量对比

| 工作模块 | 方案 A (周) | 方案 B (周) | 方案 C (周) |
|---------|------------|------------|------------|
| ML 推理引擎 | 2.5 | 2 | 4 |
| 架构适配 | 2 | 1.5 | 0 (原生设计) |
| 队列 + 调度 | 1.5 | 1.5 | 1 |
| 聚类管理 | 1 | 1.5 | 1.5 |
| 数据库层 | 1.5 | 1 | 1 |
| GUI 组件 | 2 | 2 | 2 |
| 测试 + 文档 | 1.5 | 1.5 | 1.5 |
| 许可证合规处理 | ∞ (不可行) | 0 | 0 |
| **合计** | **~12 周** ⚠️ | **~11 周** | **~12.5 周** |

### 5.4 依赖与包体积对比

| 指标 | 方案 A | 方案 B | 方案 C |
|------|--------|--------|--------|
| **新增 Python 包** | insightface, onnxruntime, rapidocr, huggingface-hub | insightface, onnxruntime, rapidocr, huggingface-hub | scikit-learn |
| **包安装体积 (CPU)** | ~150 MB | ~150 MB | ~15 MB |
| **包安装体积 (GPU)** | ~400 MB | ~400 MB | 0 (使用 OpenCV 已有) |
| **模型文件总体积** | ~200 MB (buffalo_l + PP-OCRv5) | ~200 MB | ~160 MB (YuNet+SFace+EAST+CRNN) |
| **发布包体积增量** | +350-600 MB | +350-600 MB | +175 MB |

### 5.5 维护性对比

| 维护维度 | 方案 A | 方案 B | 方案 C |
|---------|--------|--------|--------|
| **模型升级** | 受 Immich 版本约束 | 独立更新上游库 | 需手动适配新模型 |
| **Bug 修复** | 依赖 Immich 发布周期 | 直接使用上游修复 | 完全自行维护 |
| **API 稳定性** | 中（Immich 内部 API 无稳定性承诺）| 高（InsightFace / RapidOCR API 稳定）| 高（OpenCV DNN API 极稳定）|
| **社区活跃度** | 高（Immich 社区活跃） | 高（InsightFace 6.3K⭐，PaddleOCR 48K⭐）| 中（OpenCV DNN 功能稳定但迭代慢）|
| **硬件适配** | 由 ONNX Runtime 维护 | 由 ONNX Runtime 维护 | 需自行适配 OpenCV CUDA/OpenCL |

---

## 6. 风险评估

### 方案 A 风险

| 风险 | 严重性 | 说明 |
|------|--------|------|
| 🔴 许可证污染 | **致命** | AGPL-3.0 的传染性将迫使 iPhotron 放弃 MIT 许可证 |
| 🟠 架构侵入 | 高 | 微服务→嵌入式的适配需要重写大量胶水代码 |
| 🟡 版本锁定 | 中 | 与 Immich 内部 API 耦合，上游重构时被动跟进 |

### 方案 B 风险

| 风险 | 严重性 | 说明 |
|------|--------|------|
| 🟡 包体积膨胀 | 中 | ONNX Runtime GPU 版约 300 MB，影响下载/安装体验 |
| 🟡 编译兼容性 | 中 | InsightFace 的 Cython 扩展在部分平台（Windows ARM/macOS）可能需要预编译轮子 |
| 🟢 模型下载 | 低 | 首次运行需网络，可通过模型内嵌或离线包缓解 |

### 方案 C 风险

| 风险 | 严重性 | 说明 |
|------|--------|------|
| 🟠 精度瓶颈 | 高 | YuNet/SFace 在困难场景（小脸、遮挡、大角度）的精度落后于 RetinaFace/ArcFace |
| 🟠 OCR 弱点 | 高 | EAST+CRNN 在中文密集文本、弯曲文本场景远不如 PP-OCRv5 |
| 🟡 技术债 | 中 | 自行维护预/后处理管线，模型升级时适配成本高 |
| 🟢 GPU 覆盖 | 低 | 仅 CUDA/OpenCL，无法利用 Intel/Apple/ARM 硬件加速 |

---

## 7. 推荐方案

### 🏆 推荐：方案 B — 提取 Immich 核心依赖库独立集成

**理由：**

1. **许可证安全**：所有上游库（InsightFace MIT、RapidOCR Apache-2.0、ONNX Runtime MIT）均与 iPhotron 的 MIT 许可证兼容，零法律风险。

2. **精度最优**：InsightFace (RetinaFace + ArcFace) 和 PP-OCRv5 是当前开源界公认的顶级方案，在所有关键指标上显著优于 OpenCV DNN 方案：
   - 人脸检测 mAP：94% vs 86%（+8%）
   - 人脸识别 LFW：99.8% vs 99.0%（+0.8%）
   - 中文 OCR 精度：95%+ vs 80-85%（+10-15%）

3. **工作量最优**：~11 周，低于方案 A（~12 周，且有许可证障碍）和方案 C（~12.5 周），因为：
   - InsightFace 封装了完整的检测→对齐→识别管线，省去 2+ 周的管线开发
   - RapidOCR 提供开箱即用的 OCR 管线，比 EAST+CRNN 集成更简单
   - ONNX Runtime EP 机制统一了多硬件后端，省去 OpenCV DNN 的手动后端适配

4. **可参考 Immich 的架构设计**：无需复制代码，仅参考其模型选型、参数配置、推理管线设计等思路，不存在版权问题。

5. **硬件覆盖广**：通过 ONNX Runtime 一次集成即可支持 NVIDIA / AMD / Intel / Apple / ARM 硬件加速，远优于 OpenCV DNN 仅 CUDA/OpenCL 的覆盖范围。

### 对现有 `development.md` 的影响

方案 B 的采纳需要对现有开发文档进行以下调整：

| 模块 | 原方案 | 调整为 |
|------|--------|--------|
| 人脸检测器 | OpenCV DNN + YuNet | InsightFace RetinaFace (ONNX) |
| 人脸识别器 | OpenCV DNN + SFace | InsightFace ArcFace (ONNX) |
| 人脸对齐 | 自行实现 5点仿射变换 | InsightFace `norm_crop` |
| 文字检测 | EAST (OpenCV DNN) | RapidOCR PP-OCRv5 TextDetector |
| 文字识别 | CRNN (OpenCV DNN) | RapidOCR PP-OCRv5 TextRecognizer |
| 推理引擎 | OpenCV DNN (CUDA/OpenCL/CPU) | ONNX Runtime (CUDA/OpenVINO/CoreML/CPU) |
| 依赖变更 | `opencv-contrib-python-headless` + `scikit-learn` | `insightface` + `onnxruntime` + `rapidocr` + `scikit-learn` |

> **不受影响的部分**：数据库设计、任务队列架构、聚类管理逻辑、资源调度策略、GUI 组件设计、测试策略、开发里程碑结构等均可沿用。

### 实施建议

1. **依赖可选化**：将 `insightface` + `onnxruntime` + `rapidocr` 加入 `pyproject.toml` 的 `[project.optional-dependencies]` 下：
   ```toml
   [project.optional-dependencies]
   ai = [
       "insightface>=0.7.3,<1.0",
       "onnxruntime>=1.23.2,<2",
       "rapidocr>=3.1.0",
       "scikit-learn>=1.4",
       "huggingface-hub>=0.20.1,<1.0",
   ]
   ai-gpu = [
       "insightface>=0.7.3,<1.0",
       "onnxruntime-gpu>=1.23.2,<2",
       "rapidocr>=3.1.0",
       "scikit-learn>=1.4",
       "huggingface-hub>=0.20.1,<1.0",
   ]
   ```

2. **模型管理**：参考 Immich 的 HuggingFace Hub 集成方式，实现首次运行自动下载 + 本地缓存 + 离线回退。

3. **渐进集成**：优先实现人脸检测 → 人脸识别 → 聚类，再实现 OCR，与现有里程碑节奏一致。

---

## 8. 参考资料

| 资源 | 链接 |
|------|------|
| Immich 项目 | https://github.com/immich-app/immich |
| Immich ML 子模块 | https://github.com/immich-app/immich/tree/main/machine-learning |
| InsightFace | https://github.com/deepinsight/insightface |
| ONNX Runtime | https://github.com/microsoft/onnxruntime |
| RapidOCR | https://github.com/RapidAI/RapidOCR |
| PaddleOCR | https://github.com/PaddlePaddle/PaddleOCR |
| OpenCV DNN | https://docs.opencv.org/4.x/d2/d58/tutorial_table_of_content_dnn.html |
| YuNet 人脸检测 | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet |
| SFace 人脸识别 | https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface |
| RetinaFace 论文 | https://arxiv.org/abs/1905.00641 |
| ArcFace 论文 | https://arxiv.org/abs/1801.07698 |
| PP-OCRv5 | https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm_overview.md |

---

*文档结束*

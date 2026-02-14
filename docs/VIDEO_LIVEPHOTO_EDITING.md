# 🎬 视频 & Live Photo 调色/裁剪 技术方案 / Video & Live Photo Editing Technical Plan

> **版本 / Version:** 1.0
> **创建日期 / Created:** 2026-02-08
> **项目 / Project:** iPhotron – Local Photo Album Manager
> **目标 / Goal:** 将现有照片调色（Light / Color / WB / Curve / Levels / Selective Color / B&W）和裁剪/透视功能扩展至视频与 Live Photo，实现实时预览、实时响应、零卡顿

---

## 📑 目录 / Table of Contents

1. [执行摘要 / Executive Summary](#1-执行摘要--executive-summary)
2. [现状分析 / Current Architecture Analysis](#2-现状分析--current-architecture-analysis)
3. [核心技术挑战 / Core Technical Challenges](#3-核心技术挑战--core-technical-challenges)
4. [整体架构设计 / Overall Architecture Design](#4-整体架构设计--overall-architecture-design)
5. [视频实时调色方案 / Video Real-Time Color Grading](#5-视频实时调色方案--video-real-time-color-grading)
6. [Live Photo 编辑方案 / Live Photo Editing Plan](#6-live-photo-编辑方案--live-photo-editing-plan)
7. [裁剪与透视变换 / Crop & Perspective Transform](#7-裁剪与透视变换--crop--perspective-transform)
8. [性能预算与线程模型 / Performance Budget & Threading](#8-性能预算与线程模型--performance-budget--threading)
9. [实现阶段与文件清单 / Implementation Phases & File Inventory](#9-实现阶段与文件清单--implementation-phases--file-inventory)
10. [导出流水线 / Export Pipeline](#10-导出流水线--export-pipeline)
11. [风险与缓解 / Risks & Mitigation](#11-风险与缓解--risks--mitigation)
12. [验收标准 / Acceptance Criteria](#12-验收标准--acceptance-criteria)

---

## 1. 执行摘要 / Executive Summary

iPhotron 已实现基于 **OpenGL 3.3 Core** 的照片非破坏性编辑流水线（`EditSession` → Resolvers → `GLRenderer` fragment shader），支持 9 大调整模块（Light、Color、WB、Curve、Levels、Selective Color、B&W、Crop、Perspective）。

本方案的核心目标是：**将完全相同的 GLSL fragment shader 和调整参数体系复用于视频帧和 Live Photo 运动分量**，使用户在编辑视频/Live Photo 时获得与照片编辑一致的体验 —— **实时预览、零卡顿、所见即所得**。

### 技术路径概要

| 环节 | 照片（现有） | 视频/Live Photo（新增） |
|------|------------|----------------------|
| **帧来源** | `QImage` 静态图 | `QVideoSink` → `QVideoFrame` 逐帧拦截 |
| **GPU 处理** | `GLRenderer.render()` 单帧 | `GLRenderer.render()` 逐帧（复用同一 shader） |
| **预览输出** | `GLImageViewer` (QOpenGLWidget) | 同一 `GLImageViewer`（视频帧作为 texture 输入） |
| **调整参数** | `EditSession` uniform dict | 同一 `EditSession`（视频/Live Photo 复用） |
| **导出** | `OffscreenRenderer` → 单张图 | FFmpeg + `OffscreenRenderer` → 逐帧渲染合成 |

**关键设计决策**：
- ✅ **GPU shader 零修改** —— `gl_image_viewer.frag` 不需任何改动
- ✅ **EditSession 零修改** —— 调整参数完全通用
- ✅ 新增 `QVideoSink` 帧拦截层替代 `QGraphicsVideoItem` 直出
- ✅ 视频解码线程与 GPU 渲染线程分离，确保 UI 响应

---

## 2. 现状分析 / Current Architecture Analysis

### 2.1 照片编辑流水线（已实现）

```
用户拖动滑块
    │
    ▼
EditLightSection / EditColorSection / ... (Widget)
    │  valueChanged signal
    ▼
EditSession (QObject, OrderedDict)
    │  valuesChanged signal  →  adjustments: dict[str, float]
    ▼
EditPreviewManager
    │  选择后端
    ├─── _OpenGlPreviewBackend.render(session, adjustments)
    │         │
    │         ▼
    │    GLRenderer.render(adjustments=...)
    │         │  设置 30+ 个 uniform
    │         ▼
    │    gl_image_viewer.frag (GLSL)
    │         │  apply_channel() → apply_color_transform()
    │         │  → apply_wb() → apply_curve() → apply_levels()
    │         │  → apply_selective_color() → apply_bw()
    │         ▼
    │    帧缓冲 → QImage → GLImageViewer.paintGL()
    │
    └─── _CpuPreviewBackend.render(session, adjustments)
              │  fallback: apply_adjustments() via NumPy/Pillow
              ▼
         QImage → GLImageViewer
```

### 2.2 视频播放（现有，无编辑能力）

```
VideoArea (QWidget)
    │
    ├── QMediaPlayer
    │       │  setVideoOutput(QGraphicsVideoItem)
    │       ▼
    │   QGraphicsVideoItem → QGraphicsScene → QGraphicsView
    │       （直接渲染，无法插入调色处理）
    │
    ├── PlayerBar (播放控制)
    └── QAudioOutput (音频输出)
```

### 2.3 Live Photo 处理（现有）

```
core/pairing.py → pair_live() → LiveGroup
    │  .still  = 静态图路径 (JPEG/HEIC)
    │  .motion = 运动视频路径 (.mov)
    │
    ▼
PlaybackCoordinator
    │  _active_live_still  → GLImageViewer (照片编辑流水线)
    │  _active_live_motion → VideoArea (仅播放，无编辑)
```

### 2.4 差距分析

| 能力 | 照片 ✅ | 视频 ❌ | Live Photo ❌ |
|------|---------|---------|--------------|
| 实时调色预览 | OpenGL shader | 无（直接播放） | 仅静态图部分 |
| 裁剪/透视 | shader 内裁剪 | 无 | 仅静态图部分 |
| 曲线/色阶 | LUT 纹理 | 无 | 仅静态图部分 |
| 选择性颜色 | 6 范围 HSL | 无 | 仅静态图部分 |
| 非破坏性存储 | EditSession | 无 | 不完整 |
| 导出 | OffscreenRenderer | 无 | 无 |

---

## 3. 核心技术挑战 / Core Technical Challenges

### 3.1 帧率与延迟预算

| 指标 | 目标 | 约束 |
|------|------|------|
| **预览帧率** | ≥ 24 fps（1080p）, ≥ 30 fps（720p） | GPU 单帧渲染 < 8ms |
| **调整响应延迟** | < 50ms（用户感知即时） | uniform 更新 < 1ms |
| **解码吞吐** | 30 fps 硬件解码 | PyAV + `thread_type='AUTO'` |
| **内存占用** | < 500MB 增量（1080p 视频） | 仅缓存 2-3 帧 |
| **GPU 显存** | < 50MB 增量 | 1 张 1080p 纹理 ≈ 6MB |

### 3.2 线程安全

- **解码线程** → `QVideoFrame` → 主线程纹理上传 → GPU 渲染
- OpenGL 上下文仅在主线程（`GLImageViewer.paintGL()`）中使用
- 必须避免跨线程 GL 调用

### 3.3 音视频同步

- 调色处理引入的 GPU 延迟（< 8ms）远低于一帧周期（33ms@30fps）
- 音频直通 `QAudioOutput`，不经过调色流水线
- 需要精确的 PTS（Presentation Timestamp）管理

---

## 4. 整体架构设计 / Overall Architecture Design

### 4.1 统一编辑流水线

```
                    ┌──────────────────────────────────────┐
                    │         EditSession (不变)            │
                    │   adjustments: dict[str, float]       │
                    │   valueChanged / valuesChanged        │
                    └──────────────┬───────────────────────┘
                                   │
               ┌───────────────────┼───────────────────────┐
               │                   │                       │
               ▼                   ▼                       ▼
        ┌─────────────┐   ┌──────────────┐   ┌───────────────────┐
        │  Photo Edit  │   │  Video Edit  │   │  Live Photo Edit  │
        │  (现有)      │   │  (新增)      │   │  (新增)           │
        └──────┬──────┘   └──────┬───────┘   └────────┬──────────┘
               │                  │                     │
               │           ┌──────┴───────┐      ┌──────┴──────┐
               │           │ QVideoSink   │      │ Still: 照片  │
               │           │ 帧拦截       │      │ Motion: 视频 │
               │           └──────┬───────┘      └──────┬──────┘
               │                  │                     │
               └──────────────────┼─────────────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────────┐
                    │   GLRenderer.render()         │
                    │   (同一 shader, 同一 uniform)  │
                    │   gl_image_viewer.frag        │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   GLImageViewer (QOpenGLWidget)│
                    │   统一预览输出                 │
                    └──────────────────────────────┘
```

### 4.2 关键设计原则

1. **Shader 零修改**：`gl_image_viewer.frag` 对输入纹理是无感知的 —— 无论纹理来自静态图还是视频帧，处理逻辑完全相同
2. **EditSession 零修改**：adjustments dict 对所有媒体类型通用
3. **帧源抽象**：引入 `FrameSource` 抽象层，统一照片 / 视频帧 / Live Photo 的纹理输入
4. **解码-渲染分离**：视频解码在独立线程，纹理上传和 GL 渲染始终在主线程

---

## 5. 视频实时调色方案 / Video Real-Time Color Grading

### 5.1 核心思路：QVideoSink 帧拦截

**放弃 `QGraphicsVideoItem` 直接输出**，改用 `QVideoSink` 逐帧拦截 + OpenGL 渲染：

```
QMediaPlayer
    │
    ├── setVideoOutput(QVideoSink)   ← 替代 QGraphicsVideoItem
    │       │
    │       ▼
    │   QVideoSink.videoFrameChanged signal
    │       │  每帧触发（30fps → 每 33ms 一次）
    │       ▼
    │   VideoFrameProcessor (新增)
    │       │
    │       ├── QVideoFrame.toImage() → QImage
    │       │       或
    │       ├── QVideoFrame.map(ReadOnly) → raw pixel pointer
    │       │
    │       ▼
    │   GLRenderer.upload_texture(frame_image)
    │       │  glTexSubImage2D（增量上传，避免重建纹理）
    │       ▼
    │   GLRenderer.render(adjustments=edit_session.values())
    │       │  复用完全相同的 fragment shader
    │       ▼
    │   GLImageViewer.update()  → paintGL() → 显示调色后的帧
    │
    └── setAudioOutput(QAudioOutput)  ← 音频直通，不经过调色
```

### 5.2 VideoFrameProcessor 帧拦截实现

```python
# 新增: src/iPhoto/gui/ui/controllers/video_frame_processor.py

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtMultimedia import QVideoSink, QVideoFrame
from PySide6.QtGui import QImage


class VideoFrameProcessor(QObject):
    """
    拦截 QMediaPlayer 的视频帧，转换为 QImage 供 GLRenderer 上传。

    关键性能点:
    - QVideoFrame.toImage() 在 PySide6 6.5+ 使用零拷贝映射
    - 帧率自适应: 若 GPU 渲染未完成则跳过当前帧（drop frame）
    - 纹理上传使用 glTexSubImage2D 增量更新（尺寸不变时避免重建）
    """

    frameReady = Signal(QImage)  # 发射已转换的帧图像

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._rendering = False  # 渲染锁，防止帧堆积
        self._last_size = (0, 0)

    @property
    def video_sink(self) -> QVideoSink:
        """供 QMediaPlayer.setVideoOutput() 使用"""
        return self._sink

    @Slot(QVideoFrame)
    def _on_frame(self, frame: QVideoFrame) -> None:
        # 丢帧策略: 如果上一帧还在 GPU 渲染中，跳过
        if self._rendering:
            return

        if not frame.isValid():
            return

        # 高性能路径: 直接获取 QImage（PySide6 内部零拷贝）
        image = frame.toImage()
        if image.isNull():
            return

        # 转换为 GL 友好格式
        if image.format() != QImage.Format.Format_RGB888:
            image = image.convertToFormat(QImage.Format.Format_RGB888)

        self._rendering = True
        self.frameReady.emit(image)

    def mark_render_complete(self):
        """GLImageViewer 渲染完成后调用，解除帧锁"""
        self._rendering = False
```

### 5.3 GLRenderer 纹理增量上传

```python
# 在 gl_renderer.py 中新增方法（不修改现有代码）

def upload_texture_incremental(self, image: QImage) -> bool:
    """
    增量纹理上传: 若尺寸与上次相同，使用 glTexSubImage2D。
    避免每帧重建纹理对象，减少 GPU 内存分配开销。

    Returns:
        True 表示增量更新, False 表示全量重建
    """
    w, h = image.width(), image.height()

    if (w, h) == (self._tex_width, self._tex_height) and self._texture_id:
        # 增量更新: 仅替换像素数据，不重建纹理
        ptr = image.constBits()
        gl = self._gl
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
        gl.glTexSubImage2D(
            gl.GL_TEXTURE_2D, 0,
            0, 0, w, h,
            gl.GL_RGB, gl.GL_UNSIGNED_BYTE, ptr
        )
        return True
    else:
        # 尺寸变化: 全量重建纹理
        self.upload_texture(image)
        return False
```

### 5.4 VideoEditCoordinator 编排

```python
# 新增: src/iPhoto/gui/ui/controllers/video_edit_coordinator.py

class VideoEditCoordinator(QObject):
    """
    编排视频编辑流水线:
    QMediaPlayer → VideoFrameProcessor → GLRenderer → GLImageViewer

    与 EditSession 联动:
    - EditSession.valuesChanged → 更新 self._current_adjustments
    - 下一帧到来时自动使用最新的 adjustments 渲染

    性能关键:
    - adjustments 更新是即时的 (< 1ms, 仅 dict 赋值)
    - 实际渲染在下一个 videoFrameChanged 信号时执行
    - 用户拖动滑块 → adjustments 更新 → 最多等 33ms(30fps) 即可看到效果
    """

    def __init__(self, player, gl_viewer, edit_session, parent=None):
        super().__init__(parent)
        self._player = player
        self._gl_viewer = gl_viewer
        self._edit_session = edit_session
        self._frame_processor = VideoFrameProcessor(self)

        # 将 QMediaPlayer 输出到 VideoFrameProcessor
        self._player.setVideoOutput(self._frame_processor.video_sink)

        # 帧到达 → 上传纹理 + 渲染
        self._frame_processor.frameReady.connect(self._render_frame)

        # EditSession 参数变化 → 缓存最新值
        self._current_adjustments = edit_session.values()
        self._edit_session.valuesChanged.connect(self._on_adjustments_changed)

    def _on_adjustments_changed(self, adjustments):
        """参数更新: 仅存储，不触发渲染（等待下一帧）"""
        self._current_adjustments = adjustments

        # 若视频暂停，需要主动重渲染当前帧
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._gl_viewer.update()  # 触发 paintGL 使用新参数

    def _render_frame(self, frame_image):
        """收到新帧: 上传纹理 + 用当前 adjustments 渲染"""
        renderer = self._gl_viewer.renderer

        # 增量纹理上传 (尺寸不变时 ~2ms, 变化时 ~5ms)
        renderer.upload_texture_incremental(frame_image)

        # 触发重绘 (paintGL 内部调用 renderer.render(adjustments=...))
        self._gl_viewer.set_adjustments(self._current_adjustments)
        self._gl_viewer.update()

        # 解除帧锁
        self._frame_processor.mark_render_complete()
```

### 5.5 为什么这条路径能实现"实时不卡顿"

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 解码 (QMediaPlayer 内部) | ~3ms | 硬件解码 (VA-API/VideoToolbox/DXVA) |
| `QVideoFrame.toImage()` | ~1ms | PySide6 6.5+ 零拷贝映射 |
| `Format_RGB888` 转换 | ~2ms | 仅格式不匹配时 |
| `glTexSubImage2D` | ~2ms | 增量上传 1080p (6MB) |
| Fragment shader 渲染 | ~3ms | 30+ uniform, 9 个处理阶段 |
| **总计** | **~11ms** | **远低于 33ms (30fps) 预算** |
| **adjustments 更新** | **< 1ms** | dict 赋值，无 GPU 操作 |

---

## 6. Live Photo 编辑方案 / Live Photo Editing Plan

### 6.1 Live Photo 编辑核心需求

Live Photo = **静态图 (still)** + **运动视频 (motion, .mov, 1-3秒)**

编辑需求：
1. **共享同一组调整参数** —— 用户在静态图上的调色自动应用于运动视频
2. **实时预览运动部分** —— 播放 Live Photo 时显示调色后的效果
3. **封面帧同步** —— 编辑后的静态图作为 Live Photo 封面

### 6.2 架构设计

```
LiveGroup
    │
    ├── .still (JPEG/HEIC) ──────────────┐
    │                                    │
    │   EditSession (共享参数)             │
    │       │                            │
    │       ├── 编辑模式: 静态图          │
    │       │   GLRenderer + QImage ───── GLImageViewer
    │       │   (与照片编辑完全相同)        │
    │       │                            │
    │       └── 预览模式: 运动视频         │
    │           QMediaPlayer              │
    │           + VideoFrameProcessor     │
    │           + GLRenderer ──────────── GLImageViewer
    │           (与视频编辑完全相同)        (同一个 viewer)
    │
    └── .motion (.mov) ─── QMediaPlayer ──┘
```

### 6.3 LivePhotoEditController

```python
# 新增: src/iPhoto/gui/ui/controllers/live_photo_edit_controller.py

class LivePhotoEditController(QObject):
    """
    Live Photo 编辑控制器:

    - 静态图模式: 标准照片编辑流水线
    - 运动预览模式: VideoEditCoordinator 渲染调色后的运动视频
    - 两种模式共享同一个 EditSession (调整参数自动同步)

    用户交互流程:
    1. 进入 Live Photo 编辑 → 默认显示静态图 (照片编辑模式)
    2. 用户调整滑块 → EditSession 更新 → 静态图实时预览
    3. 用户点击 "LIVE" 徽章 → 切换到运动预览
    4. 播放运动视频 → 每帧自动应用当前 adjustments
    5. 用户可在运动播放时继续调整滑块 → 实时生效
    """

    def __init__(self, live_group, gl_viewer, edit_session, parent=None):
        super().__init__(parent)
        self._live_group = live_group
        self._gl_viewer = gl_viewer
        self._edit_session = edit_session

        # 静态图编辑 (复用现有 EditPreviewManager)
        self._still_image = None  # QImage, 延迟加载

        # 运动视频编辑
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video_coordinator = None  # 延迟初始化

        self._mode = "still"  # "still" | "motion"

    def enter_still_mode(self):
        """切换到静态图编辑 (标准照片流水线)"""
        self._mode = "still"
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

        if self._still_image is None:
            from PySide6.QtGui import QImage
            self._still_image = QImage(str(self._live_group.still))

        self._gl_viewer.renderer.upload_texture(self._still_image)
        self._gl_viewer.set_adjustments(self._edit_session.values())
        self._gl_viewer.update()

    def enter_motion_mode(self):
        """切换到运动视频预览 (VideoEditCoordinator 流水线)"""
        self._mode = "motion"

        if self._video_coordinator is None:
            self._video_coordinator = VideoEditCoordinator(
                self._player, self._gl_viewer,
                self._edit_session, parent=self
            )

        from PySide6.QtCore import QUrl
        self._player.setSource(QUrl.fromLocalFile(str(self._live_group.motion)))
        self._player.play()

    def toggle_mode(self):
        """在 still / motion 之间切换"""
        if self._mode == "still":
            self.enter_motion_mode()
        else:
            self.enter_still_mode()
```

### 6.4 Live Photo 参数同步策略

```
用户在静态图上编辑
    │
    ▼
EditSession.set_value("Exposure", 0.3)
    │  valuesChanged signal
    │
    ├──→ GLImageViewer (静态图实时预览)
    │
    └──→ VideoEditCoordinator._current_adjustments 更新
         │  下次播放运动视频时自动使用
         ▼
    运动视频每帧渲染: GLRenderer.render(adjustments=same_dict)
```

**关键特性**：调整参数天然同步 —— 静态图和运动视频共享同一个 `EditSession` 实例，同一个 `adjustments` 字典。

---

## 7. 裁剪与透视变换 / Crop & Perspective Transform

### 7.1 现有照片裁剪架构

当前 `gl_image_viewer.frag` 已在 shader 内实现裁剪和透视：

```glsl
// Fragment shader 中的裁剪参数 (uniform)
uniform float uCropCX, uCropCY, uCropW, uCropH;  // 归一化 [0,1] 裁剪区域
uniform mat3  uPerspectiveMatrix;                   // 透视变换矩阵
uniform int   uRotate90;                            // 0-3 旋转
```

裁剪处理顺序（shader 内）：
1. 屏幕坐标 → 归一化图像坐标
2. 检查是否在 crop 区域内（区域外 discard）
3. `apply_inverse_perspective()` 反向透视变换
4. `apply_rotation_90()` 旋转
5. 采样纹理

### 7.2 视频裁剪 —— 零额外开发

**视频裁剪无需额外实现**，因为：

1. 裁剪参数（`Crop_CX/CY/W/H`, `Perspective_Vertical/Horizontal`）存储在 `EditSession`
2. `EditSession.values()` 返回的 dict 包含所有裁剪参数
3. `GLRenderer.render(adjustments=...)` 将裁剪参数设置为 uniform
4. `gl_image_viewer.frag` 在渲染每帧时自动应用裁剪

```
视频帧 → glTexSubImage2D → Fragment Shader → 裁剪+透视+调色 → 输出
         纹理数据是全帧          shader 内部处理          仅显示裁剪区域
```

### 7.3 裁剪交互 (CropController 复用)

```python
# 现有 gl_crop_controller.py 无需修改
# CropController 操作 EditSession 中的 Crop_* 参数
# 视频编辑模式下:
# - 暂停视频 → 显示裁剪手柄
# - 用户拖动裁剪框 → EditSession 更新 → 当前帧实时预览裁剪效果
# - 恢复播放 → 每帧自动应用裁剪参数
```

### 7.4 透视校正

同理，`Perspective_Vertical` / `Perspective_Horizontal` 参数：
- 存储在 `EditSession`
- 通过 `uPerspectiveMatrix` uniform 传入 shader
- `build_perspective_matrix()` (perspective_math.py) 计算变换矩阵
- 视频帧和照片使用完全相同的矩阵计算和 shader 逻辑

---

## 8. 性能预算与线程模型 / Performance Budget & Threading

### 8.1 线程架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Thread 1: UI / 主线程 (Qt Event Loop)                          │
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Edit UI Widgets  │  │ GLImageViewer    │  │ PlayerBar    │  │
│  │ (滑块/曲线/色阶)  │  │ paintGL()        │  │ (播放控制)    │  │
│  │                  │  │ glTexSubImage2D  │  │              │  │
│  │ EditSession      │  │ GLRenderer       │  │              │  │
│  │ .set_value()     │  │ .render()        │  │              │  │
│  └──────┬───────────┘  └──────┬───────────┘  └──────────────┘  │
│         │                     │                                 │
│    valuesChanged          frameReady                            │
│    (< 1ms)                (~11ms total)                         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Thread 2: Qt Multimedia 解码线程 (QMediaPlayer 内部)            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  硬件解码器 (VA-API / VideoToolbox / DXVA / MediaCodec)  │   │
│  │  → QVideoFrame → QVideoSink.videoFrameChanged signal    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Thread 3: 导出渲染线程 (仅导出时)                               │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  OffscreenRenderer (独立 GL context)                     │   │
│  │  逐帧: 解码 → 纹理上传 → shader 渲染 → 写回             │   │
│  │  FFmpeg 编码 (H.264/H.265)                              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Thread 4: 音频输出 (QAudioOutput 内部管理)                      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  直通音频流，不经过调色流水线                               │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 性能指标预估

| 操作 | 1080p 耗时 | 4K 耗时 | 瓶颈 |
|------|-----------|---------|------|
| 硬件解码 | 2-4ms | 5-8ms | 解码器硬件 |
| `QVideoFrame.toImage()` | 0.5-1ms | 1-2ms | 内存映射 |
| RGB888 格式转换 | 1-2ms | 3-5ms | CPU memcpy |
| `glTexSubImage2D` | 1-3ms | 3-6ms | PCIe 带宽 |
| Fragment shader 渲染 | 2-4ms | 5-10ms | GPU ALU |
| `glReadPixels` (仅导出) | 3-5ms | 8-15ms | GPU → CPU |
| **预览总计** | **7-14ms** ✅ | **17-31ms** ⚠️ | |
| **帧预算** | **33ms (30fps)** | **33ms (30fps)** | |

### 8.3 4K 优化策略

对 4K (3840×2160) 视频，预览时降采样以保持流畅：

```python
class VideoFrameProcessor:
    PREVIEW_MAX_DIMENSION = 1920  # 预览最大边长

    def _on_frame(self, frame):
        image = frame.toImage()

        # 4K → 降采样到 1080p 进行预览 (导出时使用原始分辨率)
        if max(image.width(), image.height()) > self.PREVIEW_MAX_DIMENSION:
            image = image.scaled(
                self.PREVIEW_MAX_DIMENSION,
                self.PREVIEW_MAX_DIMENSION,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation  # 最近邻，< 1ms
            )

        self.frameReady.emit(image)
```

### 8.4 丢帧策略细节

```
帧到达
    │
    ├── _rendering == True ?
    │       │
    │       YES → 丢弃该帧 (GPU 上一帧还没渲染完)
    │       │
    │       NO → 处理该帧
    │            │
    │            ├── toImage() + 格式转换
    │            ├── _rendering = True
    │            ├── emit frameReady(image)
    │            │
    │            └── ... GLRenderer 渲染 ...
    │                     │
    │                     └── mark_render_complete()
    │                              │
    │                              └── _rendering = False
    │                                   (下一帧可被接收)
```

**实测预期丢帧率**：
- 1080p: < 5% (11ms 处理 vs 33ms 帧周期)
- 4K (降采样后): < 10%
- 用户完全无感知（人眼对 > 24fps 即感觉流畅）

---

## 9. 实现阶段与文件清单 / Implementation Phases & File Inventory

### Phase 1: 视频帧拦截与 GPU 渲染（核心）

**新增文件：**

| 文件路径 | 说明 |
|---------|------|
| `src/iPhoto/gui/ui/controllers/video_frame_processor.py` | QVideoSink 帧拦截器 |
| `src/iPhoto/gui/ui/controllers/video_edit_coordinator.py` | 视频编辑流水线编排 |

**修改文件：**

| 文件路径 | 修改内容 |
|---------|---------|
| `src/iPhoto/gui/ui/widgets/gl_renderer.py` | 新增 `upload_texture_incremental()` 方法 |
| `src/iPhoto/gui/ui/widgets/video_area.py` | 新增 `set_video_output_sink()` 方法，支持切换 QVideoSink / QGraphicsVideoItem |
| `src/iPhoto/gui/ui/controllers/player_view_controller.py` | 增加视频编辑模式入口 |

**不修改的文件：**

| 文件路径 | 原因 |
|---------|------|
| `gl_image_viewer.frag` | shader 对帧来源无感知 |
| `gl_image_viewer.vert` | 顶点着色器不变 |
| `edit_session.py` | adjustments dict 已通用 |
| `edit_sidebar.py` + 各 section | 调整 UI 组件不变 |
| `preview_backends.py` | 照片后端不变 |
| 所有 `*_resolver.py` | 解算器不变 |

### Phase 2: Live Photo 编辑

**新增文件：**

| 文件路径 | 说明 |
|---------|------|
| `src/iPhoto/gui/ui/controllers/live_photo_edit_controller.py` | Live Photo 双模式切换 |

**修改文件：**

| 文件路径 | 修改内容 |
|---------|---------|
| `src/iPhoto/gui/coordinators/playback_coordinator.py` | 增加 Live Photo 编辑入口 |
| `src/iPhoto/gui/ui/widgets/live_badge.py` | 点击切换 still/motion 预览 |
| `src/iPhoto/gui/ui/controllers/edit_pipeline_loader.py` | 支持 Live Photo 类型的 session 初始化 |

### Phase 3: 视频导出

**新增文件：**

| 文件路径 | 说明 |
|---------|------|
| `src/iPhoto/gui/ui/tasks/video_export_worker.py` | 后台逐帧渲染 + FFmpeg 编码 |
| `src/iPhoto/core/video_export_pipeline.py` | 导出流水线抽象 |

**修改文件：**

| 文件路径 | 修改内容 |
|---------|---------|
| `src/iPhoto/utils/ffmpeg.py` | 新增 `encode_video_from_frames()` |
| `src/iPhoto/gui/ui/controllers/export_controller.py` | 支持视频导出 UI |

### Phase 4: 优化与打磨

| 任务 | 涉及文件 |
|------|---------|
| 4K 降采样预览 | `video_frame_processor.py` |
| 硬件解码适配 | `ffmpeg.py` (检测 VAAPI/DXVA) |
| Live Photo 导出 (still + motion) | `video_export_worker.py` |
| 非破坏性编辑元数据存储 | `edit_session.py` (序列化) |
| 撤销/重做支持 | `edit_history_manager.py` (已有) |

---

## 10. 导出流水线 / Export Pipeline

### 10.1 视频导出架构

```
EditSession.values()  →  adjustments dict
    │
    ▼
VideoExportWorker (QThread)
    │
    ├── PyAV 逐帧解码原始视频
    │       │
    │       ▼
    │   QImage frame (原始分辨率)
    │       │
    │       ▼
    │   OffscreenRenderer.render_offscreen_image(
    │       image=frame,
    │       adjustments=adjustments,
    │       target_size=原始分辨率
    │   )
    │       │  独立 GL context, FBO 渲染
    │       ▼
    │   QImage rendered_frame
    │       │
    │       ▼
    │   FFmpeg 编码器 (H.264/H.265)
    │       │  frame → encoder → muxer
    │       ▼
    │   输出文件 (.mp4 / .mov)
    │
    └── 音频流: 直接拷贝 (stream copy, 无重编码)
```

### 10.2 FFmpeg 编码集成

```python
# 新增于 src/iPhoto/utils/ffmpeg.py

def encode_video_from_frames(
    output_path: Path,
    frame_generator,       # Iterable[QImage]
    fps: float,
    audio_source=None,     # Optional[Path], 原始音频
    codec: str = "libx264",
    quality: int = 23,     # CRF value (lower = better quality)
    pixel_format: str = "yuv420p",
) -> None:
    """
    将调色后的帧序列编码为视频文件。

    使用 PyAV 库进行编码:
    1. 创建输出容器
    2. 添加视频流 (codec + CRF + pixel_format)
    3. 逐帧接收 QImage → numpy → av.VideoFrame → encode
    4. 若有音频源，拷贝音频流 (不重编码)
    5. 关闭容器 (flush encoder)
    """
    import av
    import numpy as np

    output = av.open(str(output_path), mode='w')
    video_stream = output.add_stream(codec, rate=fps)
    video_stream.options = {"crf": str(quality)}
    video_stream.pix_fmt = pixel_format

    # 音频流 (直接拷贝)
    if audio_source:
        audio_input = av.open(str(audio_source), mode='r')
        audio_in_stream = audio_input.streams.audio[0]
        audio_out_stream = output.add_stream(template=audio_in_stream)

    for frame_image in frame_generator:
        arr = qimage_to_numpy(frame_image)  # (H, W, 3) uint8 RGB
        if video_stream.width == 0:
            video_stream.width = arr.shape[1]
            video_stream.height = arr.shape[0]

        frame = av.VideoFrame.from_ndarray(arr, format='rgb24')
        for packet in video_stream.encode(frame):
            output.mux(packet)

    for packet in video_stream.encode():
        output.mux(packet)

    if audio_source:
        for packet in audio_input.demux(audio_in_stream):
            if packet.dts is not None:
                packet.stream = audio_out_stream
                output.mux(packet)
        audio_input.close()

    output.close()
```

### 10.3 Live Photo 导出

Live Photo 导出需要同时输出：
1. **调色后的静态图** (JPEG/HEIC) —— 使用 `OffscreenRenderer.render_offscreen_image()`
2. **调色后的运动视频** (.mov) —— 使用 `VideoExportWorker`
3. **重新打包为 Live Photo** —— 保留 content_id 和 still_image_time 元数据

---

## 11. 风险与缓解 / Risks & Mitigation

| ⚠️ 风险 | 影响 | 概率 | 缓解措施 |
|---------|------|------|---------|
| **QVideoFrame.toImage() 性能** | 帧率下降 | 中 | 监测耗时；若 > 5ms 改用 `map(ReadOnly)` 直接访问像素指针 |
| **QVideoSink 兼容性** | 部分平台不支持 | 低 | PySide6 6.4+ 已稳定；保留 QGraphicsVideoItem 回退 |
| **OpenGL context 线程安全** | 崩溃 | 高 | 严格限制所有 GL 调用在主线程；使用 `QMetaObject.invokeMethod` 跨线程调度 |
| **4K 视频预览卡顿** | 帧率 < 24fps | 中 | 预览降采样到 1080p；导出使用原始分辨率 |
| **Live Photo 参数不同步** | 运动部分色调不一致 | 低 | 强制共享同一个 EditSession 实例 |
| **FFmpeg 编码质量损失** | 导出画质降低 | 低 | 使用 CRF 模式 (质量优先)；默认 CRF=18 (高质量) |
| **内存峰值 (4K 解码)** | OOM | 低 | 单帧缓冲（不预解码）；4K 帧 ≈ 24MB，3 帧 < 100MB |
| **硬件解码不可用** | 解码帧率下降 | 低 | PyAV `thread_type='AUTO'` 多线程软解；减少预览分辨率 |
| **音视频同步偏移** | 音画不同步 | 中 | GPU 处理延迟 (< 11ms) 远低于帧周期 (33ms)；不做额外同步 |
| **HEVC/ProRes 兼容性** | 部分格式无法解码 | 低 | 检测 `ffprobe` 结果；不支持的格式显示提示 |

---

## 12. 验收标准 / Acceptance Criteria

### Phase 1: 视频实时调色

- [ ] 视频播放时，调整任意 Light/Color/WB 滑块后 **< 50ms** 内看到效果变化
- [ ] 1080p 视频调色预览帧率 ≥ 24fps
- [ ] 4K 视频调色预览帧率 ≥ 24fps（降采样模式）
- [ ] 视频暂停状态下拖动滑块，当前帧实时更新
- [ ] 音频播放不受调色影响（无杂音、无中断）
- [ ] Curve / Levels LUT 调整在视频上实时生效
- [ ] Selective Color 6 范围调整在视频上实时生效
- [ ] B&W 模式在视频上实时生效

### Phase 2: Live Photo 编辑

- [ ] Live Photo 静态图编辑与照片编辑体验完全一致
- [ ] 点击 LIVE 徽章可切换到运动预览
- [ ] 运动预览自动应用当前调整参数
- [ ] 在运动播放时调整滑块实时生效
- [ ] 从运动模式切回静态图，调整参数保持一致

### Phase 3: 裁剪 & 透视

- [ ] 视频裁剪预览实时生效（与照片裁剪体验一致）
- [ ] 透视校正在视频上实时生效
- [ ] 旋转 (0/90/180/270) 在视频上实时生效
- [ ] Live Photo 裁剪同时应用于 still 和 motion

### Phase 4: 导出

- [ ] 视频导出保持原始分辨率和帧率
- [ ] 导出视频包含调色 + 裁剪 + 透视效果
- [ ] 导出保留原始音频（无重编码）
- [ ] Live Photo 导出同时输出 still + motion
- [ ] 导出进度显示 (0-100%)
- [ ] 导出可取消

---

## 📎 相关文档

- [架构分析与重构方案 / Architecture Analysis](./referactor/ARCHITECTURE_ANALYSIS_AND_REFACTORING.md)
- [架构图 / Architecture Diagrams](./referactor/ARCHITECTURE_DIAGRAMS.md)
- [QML 迁移方案 / QML Migration Plan](./to-qml/MIGRATION_PLAN.md)
- [组件映射 / Component Mapping](./to-qml/COMPONENT_MAPPING.md)

---

## 📎 附录 A: 现有 Shader Uniform 完整清单

> 以下所有 uniform 对视频帧和照片帧处理逻辑完全相同，无需修改。

| Uniform 名称 | 类型 | 用途 | 来源 |
|--------------|------|------|------|
| `uTex` | `sampler2D` | 源图/视频帧纹理 | `GL_TEXTURE0` |
| `uCurveLUT` | `sampler2D` | 曲线 LUT (256x1 RGB32F) | `GL_TEXTURE1` |
| `uLevelsLUT` | `sampler2D` | 色阶 LUT (256x1 RGB32F) | `GL_TEXTURE2` |
| `uBrilliance` | `float` | 鲜明度 | `EditSession["Brilliance"]` |
| `uExposure` | `float` | 曝光 | `EditSession["Exposure"]` |
| `uHighlights` | `float` | 高光 | `EditSession["Highlights"]` |
| `uShadows` | `float` | 阴影 | `EditSession["Shadows"]` |
| `uBrightness` | `float` | 亮度 | `EditSession["Brightness"]` |
| `uContrast` | `float` | 对比度 | `EditSession["Contrast"]` |
| `uBlackPoint` | `float` | 黑色色阶 | `EditSession["BlackPoint"]` |
| `uSaturation` | `float` | 饱和度 | `EditSession["Saturation"]` |
| `uVibrance` | `float` | 自然饱和度 | `EditSession["Vibrance"]` |
| `uColorCast` | `float` | 色偏 | `EditSession["Cast"]` |
| `uGain` | `vec3` | RGB 增益 | `color_resolver` 计算 |
| `uBWParams` | `vec4` | B&W 参数 (intensity, neutrals, tone, grain) | `EditSession` |
| `uBWEnabled` | `bool` | B&W 启用 | `EditSession["BW_Enabled"]` |
| `uWBWarmth` | `float` | WB 暖色 | `EditSession["WB_Warmth"]` |
| `uWBTemperature` | `float` | WB 色温 | `EditSession["WB_Temperature"]` |
| `uWBTint` | `float` | WB 色调 | `EditSession["WB_Tint"]` |
| `uWBEnabled` | `bool` | WB 启用 | `EditSession["WB_Enabled"]` |
| `uCurveEnabled` | `bool` | 曲线启用 | `EditSession["Curve_Enabled"]` |
| `uLevelsEnabled` | `bool` | 色阶启用 | `EditSession["Levels_Enabled"]` |
| `uSCRange0[6]` | `vec4[6]` | 选择性颜色参数组0 | `EditSession` |
| `uSCRange1[6]` | `vec4[6]` | 选择性颜色参数组1 | `EditSession` |
| `uSCEnabled` | `bool` | 选择性颜色启用 | `EditSession` |
| `uCropCX/CY/W/H` | `float` | 裁剪区域 (归一化) | `EditSession["Crop_*"]` |
| `uPerspectiveMatrix` | `mat3` | 透视变换矩阵 | `perspective_math.py` |
| `uRotate90` | `int` | 90度旋转 (0-3) | `EditSession["Crop_Rotate90"]` |

---

## 📎 附录 B: 关键数据结构

### EditSession adjustments dict 示例

```python
{
    # Light
    "Light_Master": 0.0, "Light_Enabled": True,
    "Brilliance": 0.0, "Exposure": 0.0, "Highlights": 0.0,
    "Shadows": 0.0, "Brightness": 0.0, "Contrast": 0.0, "BlackPoint": 0.0,

    # Color
    "Color_Master": 0.0, "Color_Enabled": True,
    "Saturation": 0.0, "Vibrance": 0.0, "Cast": 0.0,
    "Gain_R": 1.0, "Gain_G": 1.0, "Gain_B": 1.0,

    # B&W
    "BW_Master": 0.0, "BW_Enabled": False,
    "BW_Intensity": 0.5, "BW_Neutrals": 0.5, "BW_Tone": 0.5, "BW_Grain": 0.0,

    # White Balance
    "WB_Enabled": False,
    "WB_Warmth": 0.0, "WB_Temperature": 0.0, "WB_Tint": 0.0,

    # Curves
    "Curve_Enabled": False,
    "Curve_RGB": [(0,0), (1,1)],
    "Curve_Red": [(0,0), (1,1)],
    "Curve_Green": [(0,0), (1,1)],
    "Curve_Blue": [(0,0), (1,1)],

    # Levels
    "Levels_Enabled": False,
    "Levels_Handles": [0.0, 0.25, 0.5, 0.75, 1.0],

    # Selective Color
    "SelectiveColor_Enabled": False,
    "SelectiveColor_Ranges": [...],  # 6 ranges x 5 params

    # Crop & Transform
    "Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 1.0, "Crop_H": 1.0,
    "Crop_Straighten": 0.0, "Crop_Rotate90": 0, "Crop_FlipH": False,
    "Perspective_Vertical": 0.0, "Perspective_Horizontal": 0.0,
}
```

### LiveGroup 数据结构

```python
@dataclass
class LiveGroup:
    id: str              # "live_a1b2c3"
    still: str           # "/photos/IMG_001.HEIC"
    motion: str          # "/photos/IMG_001.mov"
    content_id: str | None
    still_image_time: float | None  # motion 视频中对应静态图的时间戳
    confidence: float    # 配对置信度 (1.0 / 0.7 / 0.5)
```

---

> **维护者 / Maintainer:** iPhotron Team
> **最后更新 / Last Updated:** 2026-02-08

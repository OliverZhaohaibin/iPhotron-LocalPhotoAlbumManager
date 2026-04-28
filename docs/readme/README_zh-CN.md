# 📸 iPhotron
> 受 macOS *照片* 启发的文件夹原生照片管理器，支持 Windows、macOS 与 Linux，提供实况照片、地图和智能相册。

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Python%203.12%2B-blue)
![Framework](https://img.shields.io/badge/framework-PySide6%20(Qt6)-orange)
![License](https://img.shields.io/badge/license-MIT-green)
[![GitHub Repo](https://img.shields.io/badge/github-iPhotron-181717?logo=github)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager)

**语言 / Languages:**  
[![English](https://img.shields.io/badge/English-Click-blue?style=flat)](../../README.md) | [![中文简体](https://img.shields.io/badge/中文简体-点击-red?style=flat)](README_zh-CN.md) | [![Deutsch](https://img.shields.io/badge/Deutsch-Klick-yellow?style=flat)](README_de.md)

---

## ☕ 支持

[![请我喝杯咖啡](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-支持开发-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/oliverzhao)
[![PayPal](https://img.shields.io/badge/PayPal-支持开发-blue?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=AJKMJMQA8YHPN)

## 📥 下载与安装

[![下载 Windows 版本](https://img.shields.io/badge/⬇️%20下载-Windows%20(.exe)-blue?style=for-the-badge&logo=windows)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v5.0.0/v5.00-x86-setup.exe)
[![下载 Linux 版本](https://img.shields.io/badge/⬇️%20下载-Linux%20(.deb)-orange?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v5.0.0/iPhotron_5.00_amd64.deb)
[![Download for Linux (.AppImage)](https://img.shields.io/badge/⬇️%20Download-Linux%20(.AppImage)-brightgreen?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v5.0.0/iPhotron-5.00-x86_64.AppImage)

**💡 快速安装：** 点击上方按钮直接下载最新安装程序。

- **Windows：** 直接运行 `.exe` 安装程序。
- **Linux：** 安装命令为：

```bash
sudo apt install ./iPhotron_5.00_amd64.deb
```

- **Linux（AppImage）：** 赋予执行权限后直接运行：

```bash
chmod +x iPhotron-5.00-x86_64.AppImage
./iPhotron-5.00-x86_64.AppImage
```

**开发者安装：**

```bash
pip install -e .
```

---

## 🚀 快速开始

```bash
iphoto-gui
```

或直接打开特定相册：

```bash
iphoto-gui /photos/LondonTrip
```

---

## 🌟 Star 历史

<p align="center">
  <a href="https://www.star-history.com/#OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right">
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right" />
  </a>
</p>

## 🚀 Product Hunt
<p align="center">
  <a href="https://www.producthunt.com/products/iphotron/launches/iphotron?embed=true&amp;utm_source=badge-featured&amp;utm_medium=badge&amp;utm_campaign=badge-iphotron" target="_blank" rel="noopener noreferrer">
    <img alt="iPhotron - A macOS Photos–style photo manager for Windows | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1067965&amp;theme=light&amp;t=1772225909629">
  </a>
</p>

<p align="center">
  <span style="color:#FF6154;"><strong>请为我们点赞支持</strong></span> •
  <span style="color:#FF6154;"><strong>关注我们</strong></span> •
  <span style="color:#FF6154;"><strong>在论坛参与讨论</strong></span>
</p>

---

## 🌟 概述

**iPhotron** 是一款受 macOS *照片* 启发的**文件夹原生照片管理器**。  
它使用轻量级 JSON 清单和缓存文件来组织您的媒体文件 ——  
提供丰富的相册功能，同时让编辑操作保持非破坏性，不覆盖原始媒体内容。

核心亮点：
- 🗂 文件夹原生设计 —— 每个文件夹*就是*一个相册，无需导入。
- ⚙️ 基于 JSON 的清单记录"人工决策"（封面、精选、排序）。
- ⚡ **SQLite 驱动的全局数据库**，为海量图库提供闪电般快速的查询。
- 🧠 智能增量扫描，使用持久化 SQLite 索引。
- 🎥 完整的**实况照片**配对和播放支持。
- 🗺 地图视图，可视化所有照片和视频的 GPS 元数据。
- 👥 可选的 People 人脸扫描，支持 face cluster、人物命名、封面、隐藏人物与多人 group。
![Main interface](../picture/mainview.png)
![Preview interface](../picture/preview.png)
---

## 🗺 Maps Extension

iPhotron 的离线 OBF 地图运行时以自包含的 **maps extension** 形式提供，
根目录位于 `src/maps/tiles/extension/`。本项目源码运行、Nuitka 打包产物、
以及各平台安装产物都以这套目录结构作为运行时约定。

当前 extension 主要包含：
- `World_basemap_2.obf` 离线地图数据
- `misc/`、`poi/`、`rendering_styles/`、`routing/` 以及相关运行时资源目录
- `search/geonames.sqlite3` 离线地点搜索数据
- `bin/` 下的平台原生二进制
  - Windows：`osmand_render_helper.exe`、`osmand_native_widget.dll`、
    `OsmAndCore_shared.dll`、`OsmAndCoreTools_shared.dll` 以及所需 Qt DLL
  - Linux：`osmand_render_helper`、`osmand_native_widget.so`、
    `libOsmAndCore_shared.so`、`libOsmAndCoreTools_shared.so`
  - macOS：`osmand_render_helper`、`osmand_native_widget.dylib` 以及复制到
    `bin/` 下的非系统 Mach-O 依赖

平台地图运行时说明：
- 当平台运行时可用时，iPhotron 既可使用 helper-backed OBF renderer，也可使用原生 OsmAnd widget。
- 如果仓库旁边存在 `PySide6-OsmAnd-SDK/` 工作区，Linux 和 macOS 开发环境可优先使用其中 `tools/osmand_render_helper_native/dist-*` 的 widget 构建产物。
- 原生 Linux widget 目前依赖 Qt 的 XCB + desktop OpenGL 路径；选择该后端时，iPhotron 会自动设置 `QT_QPA_PLATFORM=xcb`、`QT_OPENGL=desktop` 与 `QT_XCB_GL_INTEGRATION=xcb_glx`。
- macOS 上 legacy OpenGL 地图使用 `QOpenGLWindow + createWindowContainer()`，以避开透明主窗口下 `QOpenGLWidget` 的合成问题；媒体预览默认走支持 Metal 的 QRhi 路径，可通过 `IPHOTO_RHI_BACKEND=opengl` 强制 OpenGL。

| 未启用 maps extension | 启用 maps extension |
| --- | --- |
| ![未启用 maps extension](../picture/without_extension.png) | ![启用 maps extension](../picture/maps_extension.png) |

这套 extension 的上游构建工作区是独立子项目
[PySide6-OsmAnd-SDK](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)。
该仓库维护 vendored OsmAnd 源码、Windows、Linux 与 macOS 构建脚本、原生 Qt Widget bridge
以及预览程序，本仓库消费的运行时产物正是由它生成。

完整的“如何基于 side project 构建本仓库 maps extension”流程请参阅
[Development](../development.md)；
Nuitka 打包、runtime 同步与安装器说明请参阅
[Executable Build](../misc/BUILD_EXE.md)。

## ✨ 功能特性

### 🗺 位置视图
在交互式地图上显示您的照片足迹，根据 GPS 元数据聚类附近的照片。
![Location interface](../picture/map1.png)
![Location interface](../picture/map2.png)

### 🎞 实况照片支持
使用 Apple 的 `ContentIdentifier` 无缝配对 HEIC/JPG 和 MOV 文件。  
静态照片上会显示"实况"徽章 —— 点击即可内联播放动态视频。
![Live interface](../picture/live.png)

### 🧩 智能相册
侧边栏提供自动生成的**基础图库**，将照片分组为：
`所有照片`、`视频`、`实况照片`、`收藏`和`最近删除`。

### 👥 People、Face Cluster 与 Group
可选的 People 管线会检测照片中的人脸，生成 face cluster，并在 People 页面以人物卡片展示。
您可以为人物命名、合并重复 cluster、隐藏或重新显示隐藏人物，并让选定封面在重新扫描后继续保留。

将多个人物组成 group 后，可以查看这些人物共同出现的照片。Group 卡片支持设置封面、拖拽排序，
未置顶的 group 可以解散。人脸扫描依赖可选的 `ai-demo` 依赖；即使不安装 AI 运行时，核心照片管理功能仍可使用。

### 🖼 沉浸式详细视图
优雅的照片/视频查看器，带有胶片条导航器和浮动播放栏；GPU 渲染路径会按平台选择：
macOS 默认 QRhi/Metal，Windows 与 Linux 使用 OpenGL-backed QRhi。

### 🎨 非破坏性照片编辑
全面的编辑套件，包含**调整**和**裁剪**模式：

#### 调整模式
- **光线调整：** 亮度、曝光、高光、阴影、明度、对比度、黑场
- **颜色调整：** 饱和度、自然饱和度、色偏（白平衡校正）
- **黑白：** 强度、中性、色调、颗粒，带有艺术胶片预设
- **色彩曲线：** RGB 和单通道（R/G/B）曲线编辑器，可拖动控制点进行精确色调调整
- **可选颜色：** 针对六个色相范围（红/黄/绿/青/蓝/品红）进行独立的色相/饱和度/亮度控制
- **色阶：** 5 点输入-输出色调映射，带有直方图背景和单通道控制
- **主滑块：** 每个部分都有一个智能主滑块，可在多个微调控件之间分配值
- **实时缩略图：** 实时预览条显示每个调整的效果范围
![edit interface](../picture/editview.png)
![edit interface](../picture/professionaltools.png)
#### 裁剪模式
- **透视校正：** 垂直和水平梯形失真调整
- **拉直工具：** ±45° 旋转，亚度精度
- **翻转（水平）：** 水平翻转支持
- **交互式裁剪框：** 拖动手柄、边缘吸附和宽高比约束
- **黑边防止：** 自动验证确保透视变换后不出现黑边
  
![crop interface](../picture/cropview.png)
所有编辑都存储在 `.ipo` 附属文件中，保持原始照片不被触动。

### ℹ️ 浮动信息面板
切换浮动元数据面板，查看 EXIF、相机/镜头信息、曝光、光圈、焦距、尺寸、文件大小和拍摄时间等。
如果当前资源已有 People 数据，面板会显示检测到的人脸头像，并支持删除该人脸、移动到其他人物，
或创建新的人物标注。

位置工具也集成在面板中：带 GPS 的资源可以显示内嵌地图；没有位置的资源可以通过
“Assign a Location” 搜索、选择并确认地点。地点会始终保存到本机图库数据库；如果
ExifTool 可用，iPhotron 还会尽力把 GPS 写回原始媒体文件，写回失败时会给出提示。
如果 maps extension 缺失，面板会提供下载入口，而不是静默失效。
![Info interface](../picture/info1.png)

### 💬 丰富的交互
- 从资源管理器/访达直接拖放文件到相册。
- 多选和上下文菜单，用于复制、在文件夹中显示、移动、删除、恢复。
- 流畅的缩略图过渡和 macOS 风格的相册导航。

---

## 📚 文档

详细技术文档请参阅（英文版）：

[![Architecture](https://img.shields.io/badge/📐_Architecture-blue?style=for-the-badge)](../architecture.md)
[![Development](https://img.shields.io/badge/🧰_Development-green?style=for-the-badge)](../development.md)
[![Executable Build](https://img.shields.io/badge/🧱_Executable_Build-purple?style=for-the-badge)](../misc/BUILD_EXE.md)
[![Security](https://img.shields.io/badge/🔒_Security-red?style=for-the-badge)](../security.md)
[![Changelog](https://img.shields.io/badge/📋_Changelog-orange?style=for-the-badge)](../CHANGELOG.md)

| 文档 | 说明 |
|------|------|
| [Architecture](../architecture.md) | 整体架构、模块边界、数据流、关键设计决策 |
| [Development](../development.md) | 开发环境、依赖、调试，以及面向 Windows / Linux / macOS 的 maps extension 构建流程 |
| [Executable Build](../misc/BUILD_EXE.md) | Nuitka 打包、AOT、QRhi shader 资源、maps extension 同步与平台运行时说明 |
| [Security](../security.md) | 权限、加密、数据存储位置、威胁模型 |
| [Changelog](../CHANGELOG.md) | 所有版本更新记录 |

---

## 🧩 外部工具

| 工具 | 用途 |
|------|------|
| **ExifTool** | 读取 EXIF、GPS、QuickTime 和实况照片元数据；在用户执行 Assign Location 时写入 GPS 元数据。 |
| **FFmpeg / FFprobe** | 生成视频缩略图并解析视频信息。 |
| **InsightFace / ONNXRuntime + `buffalo_s` 模型** | 可选的 People 人脸扫描：使用 `src/extension/models/buffalo_s/` 中的 `det_500m.onnx` 进行人脸检测，使用 `w600k_mbf.onnx` 生成人脸 embedding。 |

> 请确保 FFmpeg/FFprobe 已加入系统 `PATH`；如果需要把指定地点的 GPS 写回原始媒体文件，
> 也请安装 ExifTool。
> AI 人脸运行时是可选功能；源码安装可使用 `pip install -e ".[ai-demo]"`，
> 离线打包版本需要保留 `extension/models`。

Python 依赖（例如 `Pillow`、`reverse-geocoder`）会通过 `pyproject.toml` 自动安装。

---

## 📄 许可证

**MIT 许可证 © 2025**  
由 **Haibin Zhao (OliverZhaohaibin)** 创建  

> *iPhotron —— 一个文件夹原生、人类可读且完全可重建的照片系统。*  
> *无需强制导入。没有专有锁定。只有您的照片，优雅地组织。*

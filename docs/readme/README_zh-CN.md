# 📸 iPhotron
> 将 macOS *照片* 体验带到 Windows —— 文件夹原生、非破坏性的照片管理，支持实况照片、地图和智能相册。

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Python%203.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-PySide6%20(Qt6)-orange)
![License](https://img.shields.io/badge/license-MIT-green)
[![GitHub Repo](https://img.shields.io/badge/github-iPhotos-181717?logo=github)](https://github.com/OliverZhaohaibin/iPhotos-LocalPhotoAlbumManager)

**语言 / Languages:**  
[![English](https://img.shields.io/badge/English-Click-blue?style=flat)](../../README.md) | [![中文简体](https://img.shields.io/badge/中文简体-点击-red?style=flat)](README_zh-CN.md) | [![Deutsch](https://img.shields.io/badge/Deutsch-Klick-yellow?style=flat)](README_de.md)

---

## ☕ 支持

[![请我喝杯咖啡](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-支持开发-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/oliverzhao)
[![PayPal](https://img.shields.io/badge/PayPal-支持开发-blue?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=AJKMJMQA8YHPN)

## 📥 下载与安装

[![下载 Windows 版本](https://img.shields.io/badge/⬇️%20下载-Windows%20(.exe)-blue?style=for-the-badge&logo=windows)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.3.0/v4.30.exe)
[![下载 Linux 版本](https://img.shields.io/badge/⬇️%20下载-Linux%20(.deb)-orange?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.3.1/iPhotron_4.31_amd64.deb)
[![Download for Linux (.AppImage)](https://img.shields.io/badge/⬇️%20Download-Linux%20(.AppImage)-brightgreen?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.3.1/iPhotron_4.31_amd64.deb)

**💡 快速安装：** 点击上方按钮直接下载最新安装程序。

- **Windows：** 直接运行 `.exe` 安装程序。
- **Linux：** 安装命令为：

```bash
sudo apt install ./iPhotron_4.30_amd64.deb
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
提供丰富的相册功能，同时**保持所有原始文件完整无损**。

核心亮点：
- 🗂 文件夹原生设计 —— 每个文件夹*就是*一个相册，无需导入。
- ⚙️ 基于 JSON 的清单记录"人工决策"（封面、精选、排序）。
- ⚡ **SQLite 驱动的全局数据库**，为海量图库提供闪电般快速的查询。
- 🧠 智能增量扫描，使用持久化 SQLite 索引。
- 🎥 完整的**实况照片**配对和播放支持。
- 🗺 地图视图，可视化所有照片和视频的 GPS 元数据。
![Main interface](../picture/mainview.png)
![Preview interface](../picture/preview.png)
---

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

### 🖼 沉浸式详细视图
优雅的照片/视频查看器，带有胶片条导航器和浮动播放栏。

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
切换浮动元数据面板，显示 EXIF、相机/镜头信息、曝光、光圈、焦距、文件大小等。
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
[![Security](https://img.shields.io/badge/🔒_Security-red?style=for-the-badge)](../security.md)
[![Changelog](https://img.shields.io/badge/📋_Changelog-orange?style=for-the-badge)](../CHANGELOG.md)

| 文档 | 说明 |
|------|------|
| [Architecture](../architecture.md) | 整体架构、模块边界、数据流、关键设计决策 |
| [Development](../development.md) | 开发环境、依赖、构建/打包、调试、代码风格、提交规范 |
| [Security](../security.md) | 权限、加密、数据存储位置、威胁模型 |
| [Changelog](../CHANGELOG.md) | 所有版本更新记录 |

---

## 📄 许可证

**MIT 许可证 © 2025**  
由 **Haibin Zhao (OliverZhaohaibin)** 创建  

> *iPhotron —— 一个文件夹原生、人类可读且完全可重建的照片系统。*  
> *无需导入。无需数据库。只有您的照片，优雅地组织。*

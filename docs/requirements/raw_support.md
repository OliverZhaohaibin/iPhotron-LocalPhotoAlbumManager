# iPhoto RAW 图片支持需求文档

## 1. 背景
随着数字摄影的普及，越来越多的用户使用相机拍摄 RAW 格式照片以保留最丰富的图像细节和宽广的动态范围。iPhoto 作为一个本地照片管理工具，目前对于常规图片（JPEG, PNG, HEIC 等）支持良好，但在处理单反/微单相机生成的 RAW 格式（如 CR2, NEF, ARW, DNG 等）时存在局限性。为了满足专业摄影师和高级爱好者的需求，本项目计划引入 `rawpy` 库，为 iPhoto 提供全面的 RAW 格式图片支持，包括浏览、编辑、非破坏性保存和多格式导出。

## 2. 目标
* **广泛的格式支持**：基于 `rawpy` 库，支持绝大多数主流相机的 RAW 格式（如 Canon CR2/CR3, Nikon NEF, Sony ARW, Adobe DNG 等）。
* **流畅的浏览体验**：为 RAW 图片建立高效的缩略图和预览图缓存机制，保证在网格视图和详情视图中切换流畅、打开迅速。
* **完整的编辑能力**：RAW 图片能够使用 iPhoto 现有的所有编辑功能（如曝光、对比度、白平衡、色调曲线等），并遵循非破坏性编辑原则，将调整参数保存在 `.ipo` XML sidecar 文件中。
* **灵活的导出选项**：在导出功能中增加格式选择，允许用户将编辑后的 RAW 照片导出为 JPEG, HEIC, TIFF 等常见格式。

## 3. 范围与功能需求

### 3.1 核心解析支持 (RAW Decoding)
* **库依赖**：在 `pyproject.toml` 中增加 `rawpy` 依赖。
* **图像加载**：在 `src/iPhoto/utils/image_loader.py` 中集成 `rawpy`，当 Qt 和 Pillow 无法原生解析文件时，尝试使用 `rawpy` 读取并转换为 `QImage` 或 `numpy` 数组。
* **缩略图提取**：优先尝试提取 RAW 文件中内嵌的 JPEG 预览图以生成缩略图（极大提升浏览速度）；若无内嵌预览，则使用 `rawpy` 的半尺寸或四分之一尺寸快速解码模式生成缩略图。

### 3.2 浏览与性能
* **缓存策略**：对于 RAW 文件，其解码成本远高于普通图片。需要确保生成的全分辨率预览图和微缩略图能够被有效地存入现有的内存/磁盘缓存中。
* **后台加载**：在详情页切换到 RAW 图片时，UI 不应阻塞，解码过程应在后台线程中完成。

### 3.3 非破坏性编辑 (Non-destructive Editing)
* **Sidecar 存储**：对 RAW 图片进行的所有编辑参数（包括但不限于：白平衡、色阶、曲线、色彩调整、裁剪等），必须无缝记录在与原文件同目录的 `.ipo` 格式 XML sidecar 文件中。
* **原片保护**：严格禁止修改原始 RAW 文件的任何数据（包括 EXIF 信息和图像数据）。
* **GPU 渲染兼容**：RAW 解码出的 RGB 数据必须能够顺利传递给现有的 OpenGL 预览后端（`GLImageViewer`）进行实时渲染。

### 3.4 导出功能增强 (Export Enhancements)
* **设置项扩展**：在设置菜单（和 `schema.py`）中增加“导出格式”（Export Format）选项，支持的值应包括：`jpg` (默认), `heic`, `tiff`。
* **UI 调整**：将现有的“导出目的地 (Export Destination)”和新增的“导出格式 (Export Format)”统一放置在主界面的“Export”菜单栏下。
* **导出渲染**：
  * 在执行导出时，系统应先使用 `rawpy` 读取 RAW 原始数据（若有 `.ipo` 则应用对应的滤镜和裁剪参数）。
  * 将最终渲染结果使用 Pillow 编码为用户选择的目标格式（JPG/HEIC/TIFF）并保存到目标路径。
  * 导出的图像应尽可能保留原图的分辨率。

## 4. 技术方案概述

1. **依赖更新**：修改 `pyproject.toml`，在 `dependencies` 中添加 `"rawpy>=0.19.0"`。
2. **Settings Schema 更新**：
   * 修改 `src/iPhoto/settings/schema.py`，在 `ui` 属性下增加 `export_format` 字段，枚举值为 `["jpg", "heic", "tiff", "png"]`。
   * 修改 `DEFAULT_SETTINGS` 加入 `"export_format": "jpg"`。
3. **图像加载层 (image_loader.py)**：
   * 编写一个新的助手函数 `_load_with_rawpy(source)`。
   * 在 `load_qimage` 的失败回退链路中，若 Pillow 抛出 `UnidentifiedImageError` 或返回 None，则判断扩展名是否为已知 RAW 格式，进而调用 `rawpy` 处理。
4. **UI 层修改**：
   * 在 `src/iPhoto/gui/ui/widgets/main_header.py` 中添加一个 `QActionGroup` 用于选择导出格式，并将其添加到 Export 菜单下。
   * 同步更新 `ui_main_window.py` 绑定对应的 action。
5. **导出逻辑修改**：
   * 在 `src/iPhoto/application/use_cases/export_assets.py` 和 `src/iPhoto/core/export.py` 中，读取 `export_format` 设置项。
   * 替换原有的硬编码 `.jpg` 后缀为用户选择的格式后缀，并指示底层的图像保存函数使用对应的编码器（如 Pillow 的 `save(..., format="TIFF")`）。

## 5. 验收标准
1. **文档产出**：本文档需保存在 `docs/requirements/raw_support.md`。
2. **依赖安装**：项目能够通过 `pip install .` 成功安装 `rawpy` 及其依赖。
3. **功能验证**：
   * 能够顺利导入并显示至少一种 RAW 格式（如 `.CR2` 或 `.DNG`）的缩略图和大图。
   * 对 RAW 图片进行裁剪、调色后，关闭软件并重新打开，调整依然有效（`.ipo` 读写正常）。
   * 能够将调整后的 RAW 图片成功导出为 TIFF 或 HEIC 格式。
   * 在大量包含 RAW 照片的相册中滚动时，程序不应崩溃，界面响应及时。

# Issue: Crop Display Coordinate Transformation Bug

## 问题描述 (Problem Description)

### 现象 (Symptoms)
当前裁剪（Crop）功能存在坐标系转换问题：

1. **Crop界面保存数据正确**：在裁剪界面中调整裁剪框后保存，数据正确写入 `.ipo` 旁车文件
2. **Detail（Playback）界面显示错误**：返回到详情/播放界面时，显示的裁剪结果与预期不符
3. **Adjust界面显示错误**：在调整（Adjust）界面中，裁剪框的可视化显示位置不正确
4. **重新打开Crop界面显示正确**：再次进入裁剪界面时，能够正确恢复到保存前最后一刻的状态

### 结论 (Conclusion)
- **数据持久化正常**：`.ipo` 文件中的裁剪参数保存和读取都没有问题
- **显示逻辑错误**：在非裁剪模式下（Detail/Adjust界面），裁剪参数的显示/渲染逻辑存在坐标系转换错误

---

## 技术分析 (Technical Analysis)

### 坐标系架构 (Coordinate System Architecture)

根据 `AGENT.md` 文档第11节"OpenGL开发规范"第5小节，系统使用以下坐标系：

#### A. 纹理坐标系 (Texture Space) - **持久化存储空间**
- **定义**：图片文件的原始像素空间，是不变的坐标系统
- **范围**：归一化坐标 `[0, 1]`，覆盖整个源图像
- **用途**：
  - 数据持久化：所有裁剪参数（`Crop_CX`, `Crop_CY`, `Crop_W`, `Crop_H`）存储在 `.ipo` 文件中都使用纹理坐标
  - GPU纹理采样：Shader 最终从纹理坐标采样像素
  - **不受旋转影响**：即使用户旋转图片，存储的纹理坐标保持不变
- **示例**：一张原始图片的中心裁剪框在纹理空间始终为 `(0.5, 0.5, 0.8, 0.8)`，无论视觉上如何旋转

#### B. 逻辑坐标系 (Logical Space) - **用户交互空间**
- **定义**：应用了旋转后用户在屏幕上看到的坐标系统
- **形态**：Python层所有裁剪交互都在此空间进行
- **用途**：
  - UI交互：用户拖拽、调整裁剪框的所有操作都在逻辑空间
  - 透视变换：透视扭曲在逻辑空间应用
- **坐标范围**：归一化为 `[0, 1]` 区间
- **与纹理空间的关系**：通过 `texture_crop_to_logical()` 和 `logical_crop_to_texture()` 转换

### 坐标转换函数 (Coordinate Transformation Functions)

位置：`src/iPhoto/gui/ui/widgets/gl_image_viewer/geometry.py`

```python
def texture_crop_to_logical(
    crop: tuple[float, float, float, float], rotate_steps: int
) -> tuple[float, float, float, float]:
    """将纹理空间裁剪值映射到逻辑空间以供UI渲染"""
    
def logical_crop_to_texture(
    crop: tuple[float, float, float, float], rotate_steps: int
) -> tuple[float, float, float, float]:
    """将逻辑空间裁剪值转换回不变的纹理空间"""
```

---

## 问题根源 (Root Cause)

### 1. Crop界面（正确实现）

**文件**：`src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py`

**方法**：`setCropMode()` (行 523-532)

```python
def setCropMode(self, enabled: bool, values: Mapping[str, float] | None = None) -> None:
    was_active = self._crop_controller.is_active()
    source_values = values if values is not None else self._adjustments
    # ✅ 正确：从纹理空间转换到逻辑空间
    logical_values = geometry.logical_crop_mapping_from_texture(source_values)
    self._crop_controller.set_active(enabled, logical_values)
    # ...
```

**分析**：
- 进入裁剪模式时，**正确地**调用 `logical_crop_mapping_from_texture()` 将存储的纹理坐标转换为逻辑坐标
- 这确保了裁剪框在UI上的显示位置与用户看到的旋转后图像一致

### 2. Detail（Playback）界面（疑似问题所在）

**文件**：`src/iPhoto/gui/ui/controllers/player_view_controller.py`

**方法**：`_on_adjusted_image_ready()` (行 276-296)

```python
def _on_adjusted_image_ready(self, source: Path, image: QImage, adjustments: dict) -> None:
    # ...
    self._image_viewer.set_image(
        image,
        adjustments,  # ❌ 疑似问题：直接传递adjustments，未转换坐标空间
        image_source=source,
        reset_view=True,
    )
```

**文件**：`src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py`

**方法**：`set_image()` (行 153-208)

```python
def set_image(
    self,
    image: QImage | None,
    adjustments: Mapping[str, float] | None = None,
    # ...
) -> None:
    # ...
    self._adjustments = dict(adjustments or {})
    self._update_crop_perspective_state()  # ❌ 直接使用adjustments，未转换
```

**分析**：
- Detail界面加载调整参数时，直接使用从 `.ipo` 文件读取的纹理空间坐标
- **未进行纹理空间→逻辑空间的转换**
- Shader接收的是纹理空间的裁剪参数，但如果有旋转，显示会错误

### 3. Adjust界面（疑似问题所在）

**文件**：`src/iPhoto/gui/ui/controllers/edit_controller.py`

**方法**：`begin_edit()` (行 212-277)

```python
def begin_edit(self) -> None:
    # ...
    adjustments = sidecar.load_adjustments(source)  # 从文件加载（纹理空间）
    
    session = EditSession(self)
    session.set_values(adjustments, emit_individual=False)  # ❌ 直接设置
    # ...
    viewer.setCropMode(False, session.values())  # 传递给viewer
```

**分析**：
- 加载调整参数后，未进行坐标空间转换就传递给UI
- 当 `setCropMode(False, ...)` 时（非裁剪模式），参数仍应该被正确转换和显示

---

## Shader层的裁剪测试 (Shader-side Crop Testing)

**文件**：`src/iPhoto/gui/ui/widgets/gl_image_viewer.frag`

根据 `AGENT.md` 第11节第5小节的说明，Shader 中的裁剪测试：

```glsl
// 4. 裁剪测试 **关键: 在旋转之前进行**
// Crop parameters are defined in texture space (original unrotated texture).
float crop_min_x = uCropCX - uCropW * 0.5;
float crop_max_x = uCropCX + uCropW * 0.5;
float crop_min_y = uCropCY - uCropH * 0.5;
float crop_max_y = uCropCY + uCropH * 0.5;

if (uv_perspective.x < crop_min_x || uv_perspective.x > crop_max_x ||
    uv_perspective.y < crop_min_y || uv_perspective.y > crop_max_y) {
    discard;  // 裁剪框外
}

// 5. 应用旋转
vec2 uv_tex = apply_rotation_90(uv_perspective, uRotate90);
```

**关键设计**：
- Shader 期望接收**纹理空间**的裁剪参数（`uCropCX`, `uCropCY`, `uCropW`, `uCropH`）
- 裁剪测试在旋转变换**之前**进行
- 这是正确的设计，因为存储的参数就是纹理空间

---

## 问题汇总 (Problem Summary)

### 症状对比表 (Symptom Comparison)

| 界面 | 坐标转换 | 显示结果 | 原因 |
|------|---------|---------|------|
| **Crop界面** | ✅ 纹理空间 → 逻辑空间 | ✅ 正确 | `logical_crop_mapping_from_texture()` 正确转换 |
| **Detail界面** | ❌ 未转换（直接使用纹理空间） | ❌ 错误 | 缺少坐标空间转换逻辑 |
| **Adjust界面** | ❌ 未转换（直接使用纹理空间） | ❌ 错误 | 缺少坐标空间转换逻辑 |

### 核心问题 (Core Issue)

**当图像存在旋转（`Crop_Rotate90` ≠ 0）时：**

1. **存储的裁剪坐标**：纹理空间（不随旋转变化）
2. **Shader期望接收**：纹理空间坐标（正确）
3. **Python UI层期望**：
   - **Crop模式**：逻辑空间坐标（用于绘制裁剪框UI）
   - **非Crop模式**：纹理空间坐标（传递给Shader）

**但实际情况**：
- Detail/Adjust界面的非Crop模式下，如果有UI层的裁剪可视化（例如绘制裁剪框边界），需要使用逻辑空间坐标
- 如果只是通过Shader渲染裁剪结果，应该使用纹理空间坐标（当前实现）

**可能的矛盾**：
- 如果Detail/Adjust界面在Python层有裁剪框可视化代码，但使用纹理空间坐标而非逻辑空间坐标，就会导致显示错误
- 需要检查这些界面是否有Python层的裁剪框绘制逻辑

---

## 需要验证的代码路径 (Code Paths to Verify)

### 1. GLRenderer 的 uniform 设置

**文件**：`src/iPhoto/gui/ui/widgets/gl_renderer.py`

搜索关键字：`uCropCX`, `uCropCY`, `uCropW`, `uCropH`

```python
# 需要确认这些uniform是从哪个坐标空间获取的
self._set_uniform1f("uCropCX", adjustment_value("Crop_CX", 0.5))
self._set_uniform1f("uCropCY", adjustment_value("Crop_CY", 0.5))
```

**问题**：
- 如果 `adjustment_value()` 直接从 `self._adjustments` 获取，那就是纹理空间（正确）
- 需要确认Detail/Adjust界面是否有额外的Python层裁剪框绘制

### 2. Edit Controller 的调整参数流

**文件**：`src/iPhoto/gui/ui/controllers/edit_controller.py`

```python
def begin_edit(self) -> None:
    adjustments = sidecar.load_adjustments(source)  # 纹理空间
    session.set_values(adjustments, emit_individual=False)
    # ...
    self._apply_session_adjustments_to_viewer()
```

**方法**：`_apply_session_adjustments_to_viewer()` (需要查看实现)

### 3. Crop Controller 的坐标空间处理

**文件**：`src/iPhoto/gui/ui/widgets/gl_crop/controller.py`

需要验证：
- Crop controller 如何处理坐标空间
- `get_crop_values()` 返回的是哪个空间的坐标

---

## 建议的修复方案 (Proposed Fix)

### 方案A：统一Shader输入为纹理空间（推荐）

**原则**：
- 所有传递给Shader的裁剪参数保持纹理空间
- Python层UI需要可视化裁剪框时，临时转换为逻辑空间

**实施**：
1. 检查Detail/Adjust界面是否有Python层的裁剪框绘制
2. 如果有，添加坐标转换：
   ```python
   # 从adjustments获取纹理空间坐标
   texture_crop = (adjustments["Crop_CX"], adjustments["Crop_CY"], 
                   adjustments["Crop_W"], adjustments["Crop_H"])
   rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
   
   # 转换为逻辑空间用于UI绘制
   logical_crop = geometry.texture_crop_to_logical(texture_crop, rotate_steps)
   ```

### 方案B：检查Crop退出时的坐标保存

**可能性**：
- Crop界面退出时，保存的坐标可能没有正确转换回纹理空间
- 需要检查 `logical_crop_to_texture()` 是否被正确调用

**验证**：
1. 检查Crop controller的保存逻辑
2. 确认 `get_crop_values()` 返回的坐标空间
3. 确认保存到 `.ipo` 文件时的坐标转换

---

## 复现步骤 (Reproduction Steps)

1. 打开一张图片
2. 进入Edit界面，点击Crop工具
3. 旋转图片（Rotate 90°）
4. 调整裁剪框位置和大小
5. 保存并退出Crop模式
6. **观察Detail界面**：裁剪显示与预期不符
7. **再次进入Crop界面**：裁剪框位置正确恢复

**预期结果**：Detail界面显示的裁剪结果应与Crop界面中看到的一致

**实际结果**：Detail界面显示的裁剪位置错误

---

## 影响范围 (Impact Scope)

- **功能影响**：高 - 裁剪功能是核心编辑功能
- **用户体验**：高 - 用户无法准确预览裁剪结果
- **数据完整性**：低 - 数据保存正确，只是显示问题

---

## 相关文件清单 (Related Files)

### 核心文件
- `src/iPhoto/io/sidecar.py` - `.ipo` 文件读写
- `src/iPhoto/gui/ui/widgets/gl_image_viewer/geometry.py` - 坐标转换函数
- `src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py` - 图像查看器主类
- `src/iPhoto/gui/ui/widgets/gl_renderer.py` - OpenGL渲染器
- `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag` - Fragment Shader

### 控制器
- `src/iPhoto/gui/ui/controllers/player_view_controller.py` - Detail界面控制器
- `src/iPhoto/gui/ui/controllers/edit_controller.py` - Adjust界面控制器
- `src/iPhoto/gui/ui/widgets/gl_crop/controller.py` - Crop控制器

### 数据模型
- `src/iPhoto/gui/ui/models/edit_session.py` - 编辑会话数据

---

## 参考文档 (References)

- `AGENT.md` 第11节："OpenGL开发规范"
- `AGENT.md` 第11节第5小节："裁剪与透视变换：坐标系定义"
- `src/iPhoto/gui/ui/widgets/gl_image_viewer/geometry.py` 顶部文档注释

---

## 优先级 (Priority)

**高优先级** - 影响核心编辑功能的用户体验

## 状态 (Status)

**待修复 (To Be Fixed)**

---

## 附录：坐标转换示例 (Appendix: Coordinate Transformation Example)

### 场景：旋转90°后裁剪

**原始图片**：1920x1080 (横向)

**操作**：
1. 旋转90° (rotate_steps = 1)
2. 裁剪中心：视觉上的中心

**纹理空间（存储）**：
```python
Crop_CX = 0.5
Crop_CY = 0.5
Crop_W = 0.8
Crop_H = 0.6
Crop_Rotate90 = 1
```

**逻辑空间（UI显示）**：
```python
# 应用 texture_crop_to_logical() 转换
# rotate_steps = 1: (x', y') = (1-y, x)
# 变量映射关系 (Variable mapping):
#   logical_cx = 1.0 - Crop_CY = 1.0 - 0.5 = 0.5
#   logical_cy = Crop_CX = 0.5
#   logical_w  = Crop_H = 0.6   # 宽高互换 (width/height swapped)
#   logical_h  = Crop_W = 0.8
logical_cx = 1.0 - Crop_CY  # 0.5
logical_cy = Crop_CX        # 0.5
logical_w  = Crop_H         # 0.6
logical_h  = Crop_W         # 0.8
```

**如果不转换**：
- UI会显示 (0.5, 0.5, 0.8, 0.6) 的裁剪框
- 但用户看到的是旋转后的图像 (1080x1920，竖向)
- 裁剪框的宽高比和位置都不正确

---

**创建日期**：2025-11-23  
**文档版本**：1.0

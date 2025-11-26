# Issue: 统一 Sidecar 文件裁剪坐标处理与旋转步数问题

## 1. 问题描述

### 1.1 当前症状

在对图像编辑参数进行保存和重新加载后，存在以下问题：

1. **Detail 界面和 Adjust 界面加载 ipo 文件失效**
   - 从已保存的 `.ipo` 文件读取调整参数后，无法正确显示框内裁剪图像
   - 图像显示与存储的裁剪参数不匹配

2. **进入 Crop 界面时裁剪框位置变化**
   - 当 `step != 0`（即存在 90° 旋转步数）时，进入 crop 界面会导致裁剪框发生偏移
   - 现场编辑时旋转没有位移，但保存后重新进入则出现位移

3. **保存-重载循环导致裁剪框累积偏移**
   - 在 crop 编辑时当场旋转是正确的
   - 点击 Done 保存后，再次进入 crop 进行 step rotate 就会出现位移

### 1.2 已确认正常的场景

- `step = 0` 时的所有裁剪和透视操作
- 在 Crop 编辑模式下的即时旋转操作（未保存前）
- 黑边检测功能（通过 shader 统一处理后已修复）

---

## 2. Sidecar 文件读写链条完整分析

### 2.1 文件格式概述

`.ipo` 文件采用 XML 格式存储编辑参数：

```xml
<?xml version="1.0" encoding="utf-8"?>
<iPhotoAdjustments version="1.0">
    <Light>
        <!-- Light/Color/BW adjustments -->
    </Light>
    <crop>
        <x>0.100000</x>     <!-- 左边界（归一化坐标） -->
        <y>0.150000</y>     <!-- 上边界（归一化坐标） -->
        <w>0.800000</w>     <!-- 宽度（归一化） -->
        <h>0.700000</h>     <!-- 高度（归一化） -->
        <straighten>5.000000</straighten>
        <rotate90>1.000000</rotate90>
        <vertical>0.300000</vertical>
        <horizontal>-0.200000</horizontal>
        <flipHorizontal>false</flipHorizontal>
    </crop>
</iPhotoAdjustments>
```

### 2.2 写入链条

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SIDECAR 写入链条                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  1. 用户操作阶段 (edit_controller.py)                                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _handle_crop_changed(cx, cy, width, height)                        │ │
│  │   └─ 接收来自 GLImageViewer 的裁剪框变化信号                         │ │
│  │      └─ 坐标已通过 geometry.logical_crop_to_texture() 转换          │ │
│  │         到纹理空间                                                   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  2. 会话存储阶段 (edit_session.py)                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ session.set_values({                                                │ │
│  │     "Crop_CX": texture_cx,    # 纹理空间中心 X                       │ │
│  │     "Crop_CY": texture_cy,    # 纹理空间中心 Y                       │ │
│  │     "Crop_W": texture_w,      # 纹理空间宽度                         │ │
│  │     "Crop_H": texture_h,      # 纹理空间高度                         │ │
│  │ })                                                                  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  3. 点击 Done 保存 (edit_controller.py → sidecar.py)                    │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _handle_done_clicked():                                             │ │
│  │   crop_values = self._ui.edit_image_viewer.crop_values()           │ │
│  │   self._session.set_values(crop_values, emit_individual=False)     │ │
│  │   adjustments = self._session.values()                             │ │
│  │   sidecar.save_adjustments(source, adjustments)                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  4. XML 序列化 (sidecar.py)                                              │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _write_crop_node(root, values):                                     │ │
│  │   left, top, width, height = _normalised_crop_components(values)   │ │
│  │   # 从中心坐标 (Crop_CX, Crop_CY) 转换为左上角坐标 (x, y)            │ │
│  │   # 写入 <crop><x/><y/><w/><h/><straighten/>...</crop>              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ⚠️ 关键问题：写入时裁剪坐标处于纹理空间，但 rotate90 参数表示的旋转      │
│     已经影响了逻辑空间的显示，存储格式未考虑这种关系                        │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 读取链条

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SIDECAR 读取链条                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  A. Detail 界面加载 (player_view_controller.py)                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _AdjustedImageWorker.run():                                         │ │
│  │   raw_adjustments = sidecar.load_adjustments(self._source)         │ │
│  │   # raw_adjustments 包含:                                           │ │
│  │   #   Crop_CX, Crop_CY, Crop_W, Crop_H (纹理空间)                   │ │
│  │   #   Crop_Rotate90, Crop_Straighten, Crop_FlipH                    │ │
│  │   #   Perspective_Vertical, Perspective_Horizontal                  │ │
│  │   adjustments = sidecar.resolve_render_adjustments(raw_adjustments) │ │
│  │   # 发送到 GL viewer 进行渲染                                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  B. Edit 界面加载 (edit_controller.py)                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ begin_edit():                                                       │ │
│  │   adjustments = sidecar.load_adjustments(source)                   │ │
│  │   session = EditSession(self)                                       │ │
│  │   session.set_values(adjustments, emit_individual=False)           │ │
│  │   viewer.setCropMode(False, session.values())                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  C. 进入 Crop 模式 (edit_controller.py → widget.py)                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _set_mode("crop"):                                                  │ │
│  │   crop_values = {                                                   │ │
│  │       "Crop_CX": session.value("Crop_CX"),  # 纹理空间值            │ │
│  │       "Crop_CY": session.value("Crop_CY"),                          │ │
│  │       "Crop_W": session.value("Crop_W"),                            │ │
│  │       "Crop_H": session.value("Crop_H"),                            │ │
│  │   }                                                                 │ │
│  │   viewer.setCropMode(True, crop_values)                             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  D. Viewer 设置 Crop 模式 (widget.py)                                    │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ setCropMode(enabled, values):                                       │ │
│  │   # ⚠️ 关键转换点                                                   │ │
│  │   logical_values = geometry.logical_crop_mapping_from_texture(      │ │
│  │       source_values                                                 │ │
│  │   )                                                                 │ │
│  │   # 根据 rotate_steps 将纹理空间坐标转换为逻辑空间                   │ │
│  │   self._crop_controller.set_active(enabled, logical_values)        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│  E. Crop Controller 应用值 (controller.py)                               │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ _apply_crop_values(values):                                         │ │
│  │   crop_state.set_from_mapping(values)  # 设置逻辑空间坐标           │ │
│  │   self._model.ensure_crop_center_inside_quad()  # 约束到透视四边形  │ │
│  │   self._model.auto_scale_crop_to_quad()  # 自动缩放适应             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ⚠️ 问题链条：                                                            │
│  1. sidecar 存储的是纹理空间坐标                                          │
│  2. setCropMode 调用 logical_crop_mapping_from_texture 进行转换           │
│  3. 但 crop_model 的 perspective_quad 计算没有考虑 rotate_steps           │
│  4. 导致 ensure_crop_center_inside_quad 和 auto_scale_crop_to_quad       │
│     使用错误的参考四边形，引起裁剪框位移                                    │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 根本原因分析

### 3.1 坐标空间不一致

系统中存在三种坐标空间：

| 坐标空间 | 描述 | 使用位置 |
|---------|------|----------|
| **纹理空间** | 原始图像像素坐标，不受旋转影响 | Sidecar 文件存储、Shader 纹理采样 |
| **逻辑空间** | 考虑旋转后的显示坐标 | Crop 交互、UI 显示 |
| **透视空间** | 透视变换后的坐标 | Perspective quad 计算 |

### 3.2 问题根源

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          坐标空间不匹配问题                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  CropSessionModel.update_perspective() 中：                               │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ matrix = build_perspective_matrix(                                  │ │
│  │     vertical,                                                       │ │
│  │     horizontal,                                                     │ │
│  │     image_aspect_ratio=aspect_ratio,  # ← 使用逻辑空间宽高比        │ │
│  │     straighten_degrees=straighten,                                  │ │
│  │     rotate_steps=0,  # ← 始终为 0，不考虑旋转！                      │ │
│  │     flip_horizontal=flip,                                           │ │
│  │ )                                                                   │ │
│  │ self._perspective_quad = compute_projected_quad(matrix)            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  结果：perspective_quad 在逻辑空间中计算，                                 │
│        但裁剪框验证 (rect_inside_quad) 时：                               │
│        - 裁剪框坐标在纹理空间                                              │
│        - 透视四边形在逻辑空间                                              │
│        - 当 rotate_steps != 0 时，两者不匹配                              │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 保存-重载问题时序

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     保存-重载问题时序图                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  时刻 T1: 首次进入 Crop 编辑                                              │
│  ├─ rotate_steps = 0                                                     │
│  ├─ 裁剪框坐标: (cx=0.5, cy=0.5, w=1.0, h=1.0) [纹理空间]               │
│  ├─ 逻辑坐标转换: 无需转换                                                │
│  └─ ✓ 显示正确                                                           │
│                                                                           │
│  时刻 T2: 用户旋转 90° (step = 1)                                        │
│  ├─ rotate_steps = 1                                                     │
│  ├─ 裁剪框坐标保持不变 [纹理空间]                                         │
│  ├─ Shader 通过 uRotate90 正确旋转显示                                   │
│  └─ ✓ 显示正确 (当场旋转)                                                │
│                                                                           │
│  时刻 T3: 用户点击 Done 保存                                              │
│  ├─ crop_values() 返回逻辑空间坐标                                        │
│  ├─ _handle_crop_changed 通过 logical_crop_to_texture 转回纹理空间       │
│  ├─ sidecar.save_adjustments() 存储纹理空间坐标                          │
│  └─ ✓ 存储正确                                                           │
│                                                                           │
│  时刻 T4: 重新进入 Crop 编辑 (问题发生！)                                 │
│  ├─ load_adjustments() 读取纹理空间坐标                                   │
│  ├─ setCropMode() 调用 logical_crop_mapping_from_texture()               │
│  │   └─ 将纹理坐标转换为逻辑坐标                                          │
│  ├─ _apply_crop_values() 设置裁剪框                                       │
│  │   └─ ensure_crop_center_inside_quad() 使用 perspective_quad 验证      │
│  │       └─ perspective_quad 在逻辑空间但未正确考虑 rotate_steps         │
│  │           └─ ⚠️ 验证失败，裁剪框被错误调整！                           │
│  └─ ✗ 裁剪框发生位移                                                     │
│                                                                           │
│  时刻 T5: 再次旋转                                                        │
│  ├─ 位移累积叠加                                                          │
│  └─ ✗ 问题加剧                                                           │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 详细代码流程分析

### 4.1 Sidecar 读取流程

**文件：`src/iPhoto/io/sidecar.py`**

```python
def _read_crop_from_node(node: ET.Element) -> dict[str, float]:
    """读取 <crop> 节点中的裁剪参数"""
    
    # 读取左上角坐标格式
    left = _child_value(_CROP_CHILD_X, 0.0)     # <x>
    top = _child_value(_CROP_CHILD_Y, 0.0)      # <y>
    width = _child_value(_CROP_CHILD_W, 1.0)    # <w>
    height = _child_value(_CROP_CHILD_H, 1.0)   # <h>
    
    # 读取变换参数
    straighten = ...  # <straighten>
    rotate_steps = ...  # <rotate90>
    flip_enabled = ...  # <flipHorizontal>
    vertical = ...  # <vertical>
    horizontal = ...  # <horizontal>
    
    # ⚠️ 关键：转换为中心坐标格式
    values = _centre_crop_from_top_left(left, top, width, height)
    # 返回: {"Crop_CX": cx, "Crop_CY": cy, "Crop_W": w, "Crop_H": h}
    
    values.update({
        "Crop_Straighten": straighten,
        "Crop_Rotate90": rotate_steps,
        "Crop_FlipH": flip_enabled,
        "Perspective_Vertical": vertical,
        "Perspective_Horizontal": horizontal,
    })
    return values
```

### 4.2 坐标转换流程

**文件：`src/iPhoto/gui/ui/widgets/gl_image_viewer/geometry.py`**

```python
def texture_crop_to_logical(
    crop: tuple[float, float, float, float], 
    rotate_steps: int
) -> tuple[float, float, float, float]:
    """纹理空间 → 逻辑空间"""
    
    tcx, tcy, tw, th = crop
    
    if rotate_steps == 0:
        return (tcx, tcy, tw, th)  # 无转换
    
    if rotate_steps == 1:
        # 90° CW: (x', y') = (1-y, x)
        return (
            clamp_unit(1.0 - tcy),  # 新 CX = 1 - 原 CY
            clamp_unit(tcx),         # 新 CY = 原 CX
            clamp_unit(th),          # 新 W = 原 H (宽高交换)
            clamp_unit(tw),          # 新 H = 原 W
        )
    
    # ... rotate_steps == 2, 3 类似


def logical_crop_to_texture(
    crop: tuple[float, float, float, float], 
    rotate_steps: int
) -> tuple[float, float, float, float]:
    """逻辑空间 → 纹理空间 (逆变换)"""
    
    lcx, lcy, lw, lh = crop
    
    if rotate_steps == 0:
        return (lcx, lcy, lw, lh)
    
    if rotate_steps == 1:
        # Step 1 逆变换: (x, y) = (y', 1-x')
        return (
            clamp_unit(lcy),          # 原 CX = 新 CY
            clamp_unit(1.0 - lcx),    # 原 CY = 1 - 新 CX
            clamp_unit(lh),           # 原 W = 新 H
            clamp_unit(lw),           # 原 H = 新 W
        )
    
    # ...
```

### 4.3 透视四边形计算

**文件：`src/iPhoto/gui/ui/widgets/gl_crop/model.py`**

```python
def update_perspective(
    self,
    vertical: float,
    horizontal: float,
    straighten: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
    aspect_ratio: float = 1.0,
) -> bool:
    """更新透视四边形"""
    
    # ... 参数更新 ...
    
    # ⚠️ 问题：始终传入 rotate_steps=0
    matrix = build_perspective_matrix(
        new_vertical,
        new_horizontal,
        image_aspect_ratio=aspect_ratio,  # 逻辑空间宽高比
        straighten_degrees=new_straighten,
        rotate_steps=0,  # 旋转由 shader 处理
        flip_horizontal=new_flip,
    )
    
    # perspective_quad 在逻辑空间中计算
    self._perspective_quad = compute_projected_quad(matrix)
    return True
```

### 4.4 Shader 中的坐标处理

**文件：`src/iPhoto/gui/ui/widgets/gl_image_viewer.frag`**

```glsl
void main() {
    // ... 视口坐标转换 ...
    
    vec2 uv = texPx / uTexSize;  // 逻辑空间 UV
    
    // 1. 黑边检测（逻辑空间）
    if (!is_within_valid_bounds(uv_corrected)) {
        discard;
    }
    
    // 2. 透视逆变换（逻辑空间 → 透视前空间）
    vec2 uv_original = apply_inverse_perspective(uv_corrected);
    
    // 3. 旋转变换（透视前空间 → 纹理空间）
    uv_original = apply_rotation_90(uv_original, uRotate90);
    
    // 4. 裁剪检测（⚠️ uv_original 在纹理空间，但裁剪参数来自？）
    float crop_min_x = uCropCX - uCropW * 0.5;
    // ...
    if (uv_original.x < crop_min_x || ...) {
        discard;
    }
    
    // 5. 纹理采样（纹理空间）
    vec4 texel = texture(uTex, uv_original);
}
```

---

## 5. 修复方案

### 5.1 方案概述

**核心思路：确保 perspective_quad 和裁剪框验证在同一坐标空间中进行**

有两种可行方案：

| 方案 | 描述 | 复杂度 | 风险 |
|------|------|--------|------|
| 方案 A | 将 perspective_quad 转换到纹理空间 | 中 | 低 |
| 方案 B | 将裁剪框验证统一到逻辑空间 | 高 | 中 |

**推荐方案 A**：在 `CropSessionModel.update_perspective()` 中添加坐标转换。

### 5.2 方案 A 详细设计

#### 5.2.1 修改 CropSessionModel

```python
# 文件: src/iPhoto/gui/ui/widgets/gl_crop/model.py

def update_perspective(
    self,
    vertical: float,
    horizontal: float,
    straighten: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
    aspect_ratio: float = 1.0,
) -> bool:
    """Update the perspective quad based on parameters."""
    
    # ... 现有参数更新代码 ...
    
    # 在逻辑空间中计算透视四边形
    matrix = build_perspective_matrix(
        new_vertical,
        new_horizontal,
        image_aspect_ratio=aspect_ratio,
        straighten_degrees=new_straighten,
        rotate_steps=0,
        flip_horizontal=new_flip,
    )
    logical_quad = compute_projected_quad(matrix)
    
    # ✅ 新增：将透视四边形从逻辑空间转换到纹理空间
    self._perspective_quad = self._transform_quad_to_texture_space(
        logical_quad, 
        new_rotate
    )
    return True

def _transform_quad_to_texture_space(
    self, 
    quad: list[tuple[float, float]], 
    rotate_steps: int
) -> list[tuple[float, float]]:
    """将四边形从逻辑空间转换到纹理空间。
    
    这是 shader 中 apply_rotation_90() 的逆操作。
    当 shader 应用旋转将逻辑坐标映射到物理坐标时，
    我们需要逆变换将 perspective_quad 映射回纹理空间，
    以便与存储在纹理空间中的裁剪坐标进行比较。
    """
    if rotate_steps % 4 == 0:
        return quad
    
    def inverse_rotate_point(x: float, y: float) -> tuple[float, float]:
        steps = rotate_steps % 4
        if steps == 1:
            # 90° CW 的逆变换: (x, y) = (y', 1-x')
            return (y, 1.0 - x)
        elif steps == 2:
            # 180° 的逆变换: (x, y) = (1-x', 1-y')
            return (1.0 - x, 1.0 - y)
        else:  # steps == 3
            # 270° CW 的逆变换: (x, y) = (1-y', x')
            return (1.0 - y, x)
    
    return [inverse_rotate_point(pt[0], pt[1]) for pt in quad]
```

#### 5.2.2 修改 CropInteractionController

```python
# 文件: src/iPhoto/gui/ui/widgets/gl_crop/controller.py

def _apply_crop_values(self, values: Mapping[str, float] | None) -> None:
    """Apply crop values to the crop state."""
    
    crop_state = self._model.get_crop_state()
    if values:
        # ⚠️ 确保传入的是纹理空间坐标
        # values 应该已经通过 logical_crop_to_texture 转换
        crop_state.set_from_mapping(values)
    else:
        crop_state.set_full()
    
    # 现在 perspective_quad 也在纹理空间，验证正确
    changed = self._model.ensure_crop_center_inside_quad()
    if not self._model.is_crop_inside_quad():
        changed = self._model.auto_scale_crop_to_quad() or changed
    
    # ...
```

#### 5.2.3 修改 GLImageViewer.setCropMode

```python
# 文件: src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py

def setCropMode(self, enabled: bool, values: Mapping[str, float] | None = None) -> None:
    """Enable or disable crop mode."""
    
    was_active = self._crop_controller.is_active()
    source_values = values if values is not None else self._adjustments
    
    # ✅ 修改：保持纹理空间坐标，不进行转换
    # 因为 perspective_quad 现在也在纹理空间中
    texture_values = {
        "Crop_CX": float(source_values.get("Crop_CX", 0.5)),
        "Crop_CY": float(source_values.get("Crop_CY", 0.5)),
        "Crop_W": float(source_values.get("Crop_W", 1.0)),
        "Crop_H": float(source_values.get("Crop_H", 1.0)),
    }
    
    self._crop_controller.set_active(enabled, texture_values)
    # ...
```

### 5.3 需要同步修改的地方

| 文件 | 修改点 | 说明 |
|------|--------|------|
| `gl_crop/model.py` | `update_perspective()` | 添加 quad 到纹理空间的转换 |
| `gl_crop/model.py` | 新增 `_transform_quad_to_texture_space()` | 实现坐标转换 |
| `gl_crop/controller.py` | `_apply_crop_values()` | 确保使用纹理空间坐标 |
| `gl_image_viewer/widget.py` | `setCropMode()` | 移除 logical_crop_mapping_from_texture 调用 |
| `gl_image_viewer/widget.py` | `_handle_crop_interaction_changed()` | 确保输出纹理空间坐标 |

---

## 6. 验证测试矩阵

### 6.1 功能测试

| 测试场景 | 操作步骤 | 预期结果 |
|---------|---------|---------|
| 基础保存-加载 | 1. 打开图片 2. 编辑裁剪 3. 保存 4. 重新打开 | 裁剪框位置与保存时一致 |
| 旋转后保存-加载 | 1. 旋转 90° 2. 编辑裁剪 3. 保存 4. 重新打开 | 裁剪框位置与保存时一致 |
| 多次旋转保存-加载 | 1. 旋转多次 2. 每次保存重新打开 | 无累积偏移 |
| 透视+旋转 | 1. 设置透视参数 2. 旋转 3. 保存 4. 重新打开 | 裁剪框位置与保存时一致 |

### 6.2 边界条件测试

| 测试场景 | 条件 | 预期结果 |
|---------|------|---------|
| 极限旋转 | step = 3 + straighten = ±45° | 无裁剪框位移 |
| 极限透视 | vertical = ±1.0, horizontal = ±1.0 | 无裁剪框位移 |
| 组合变换 | step=2 + straighten=15° + vertical=0.5 | 无裁剪框位移 |

---

## 7. 实施顺序

### Phase 1: 核心修复（优先级：高）

1. 在 `CropSessionModel` 中实现 `_transform_quad_to_texture_space()`
2. 修改 `update_perspective()` 调用新方法
3. 更新 `setCropMode()` 移除多余的坐标转换

### Phase 2: 验证和回归（优先级：高）

1. 编写单元测试覆盖坐标转换
2. 执行功能测试矩阵
3. 验证现有功能无回归

### Phase 3: 文档更新（优先级：中）

1. 更新 `black_border_detection_parameters.md`
2. 更新 `requirements_unified_black_border_detection.md`
3. 添加架构说明文档

---

## 8. 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 现有裁剪功能回归 | 中 | 高 | 完整的回归测试 |
| 坐标转换精度问题 | 低 | 中 | 使用 float64 精度 |
| 边界条件处理不当 | 中 | 中 | 增加边界测试用例 |
| 其他模块依赖影响 | 低 | 中 | 代码审查 |

---

## 9. 附录：坐标变换公式

### 9.1 逻辑空间 → 纹理空间（Shader 中的 apply_rotation_90 逆变换）

```
Step 0 (0°):    (x, y) = (x', y')
Step 1 (90° CW):  (x, y) = (y', 1-x')
Step 2 (180°):   (x, y) = (1-x', 1-y')
Step 3 (270° CW): (x, y) = (1-y', x')
```

### 9.2 纹理空间 → 逻辑空间（Shader 中的 apply_rotation_90）

```
Step 0 (0°):    (x', y') = (x, y)
Step 1 (90° CW):  (x', y') = (1-y, x)
Step 2 (180°):   (x', y') = (1-x, 1-y)
Step 3 (270° CW): (x', y') = (y, 1-x)
```

---

## 10. 参考文档

- [黑边检测参数差异分析](./black_border_detection_parameters.md)
- [统一黑边检测逻辑需求](./requirements_unified_black_border_detection.md)
- [裁剪框旋转位移问题](./issue_crop_box_displacement_with_rotation.md)

---

**文档版本**: 1.0
**最后更新**: 2024
**作者**: Development Team

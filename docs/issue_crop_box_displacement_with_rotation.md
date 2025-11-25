# Issue: 裁剪框在旋转步数(step)不为0时的位移问题

## 1. 问题描述

### 1.1 现象

当用户对图像进行以下组合操作时，裁剪框(crop box)会出现位移：

1. **旋转步数 (step/rotate_steps) ≠ 0**：即图像进行了90°/180°/270°离散旋转
2. **加入 straighten（微调旋转）**：任意微调角度
3. **加入 vertical/horizontal（透视校正）**：垂直或水平透视调整

在上述组合下，应用step旋转后，裁剪框的位置与图像的实际有效区域不匹配，导致用户看到的裁剪框发生了偏移。

### 1.2 正常情况

当 `step = 0` 时，即图像未进行90°离散旋转，配合 `straighten`、`vertical`、`horizontal` 变换时，裁剪框位置始终正确。

---

## 2. 问题分析

### 2.1 代码架构回顾

系统中涉及黑边检测和裁剪框定位的核心模块：

| 模块 | 文件 | 职责 |
|------|------|------|
| 透视矩阵构建 | `perspective_math.py` | 构建包含透视、微调、翻转的变换矩阵 |
| 裁剪模型 | `gl_crop/model.py` | 管理裁剪状态、透视四边形计算 |
| 渲染器 | `gl_renderer.py` | 传递uniform到shader，设置透视矩阵 |
| 着色器 | `gl_image_viewer.frag` | 执行透视逆变换、90°旋转、黑边检测 |
| 坐标变换 | `gl_image_viewer/geometry.py` | 纹理空间与逻辑空间的坐标转换 |
| 缩放计算 | `view_transform_controller.py` | 计算旋转覆盖缩放系数 |

### 2.2 变换链分析

#### 2.2.1 渲染侧的变换顺序（Shader中）

在 `gl_image_viewer.frag` 中，变换按以下顺序应用：

```glsl
// main() 中的变换顺序：
1. uv_corrected = crop_boundary_check(uv);      // 裁剪边界检查
2. is_within_valid_bounds(uv_corrected);         // 统一黑边检测
   2.1. apply_inverse_perspective(uv);           // 透视逆变换
   2.2. apply_rotation_90(uv_perspective, uRotate90);  // 90°旋转
3. uv_original = apply_inverse_perspective(uv_corrected);  // 透视逆变换
4. uv_original = apply_rotation_90(uv_original, uRotate90); // 90°旋转
5. texture(uTex, uv_original);                   // 纹理采样
```

#### 2.2.2 裁剪模型侧的透视四边形计算

在 `CropSessionModel.update_perspective()` 中：

```python
# 当前实现
matrix = build_perspective_matrix(
    new_vertical,
    new_horizontal,
    image_aspect_ratio=aspect_ratio,
    straighten_degrees=new_straighten,
    rotate_steps=0,  # 始终为0
    flip_horizontal=new_flip,
)
self._perspective_quad = compute_projected_quad(matrix)
```

问题在于：`build_perspective_matrix` 中的 `rotate_steps` 始终传入 `0`，因此透视四边形的计算不考虑90°旋转。

#### 2.2.3 坐标空间不匹配

```
┌─────────────────────────────────────────────────────────────────┐
│                         问题根源                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  裁剪模型 (CropSessionModel):                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ perspective_quad = compute_projected_quad(matrix)        │   │
│  │                                                          │   │
│  │ matrix 使用:                                              │   │
│  │   - straighten_degrees = 实际微调角度                      │   │
│  │   - rotate_steps = 0  ← 不考虑90°旋转！                    │   │
│  │   - aspect_ratio = 逻辑宽高比（已旋转）                     │   │
│  │                                                          │   │
│  │ 结果: perspective_quad 在"逻辑空间"中计算，               │   │
│  │       但裁剪坐标存储在"纹理空间"中                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           ↓                                       │
│  裁剪验证 (rect_inside_quad):                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ rect_inside_quad(crop_rect, perspective_quad)            │   │
│  │                                                          │   │
│  │ crop_rect 在"纹理空间"中定义                               │   │
│  │ perspective_quad 在"逻辑空间"中定义                        │   │
│  │                                                          │   │
│  │ 空间不匹配 → 验证结果错误 → 裁剪框位移！                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 根本原因

**坐标空间不一致**：

1. **裁剪框坐标（Crop_CX, Crop_CY等）** 存储在 **纹理空间**（未旋转的原始纹理坐标系）
2. **透视四边形（perspective_quad）** 计算在 **逻辑空间**（已应用90°旋转的视觉坐标系）
3. 当 `rotate_steps ≠ 0` 时，两个坐标系不一致
4. 使用 `rect_inside_quad()` 验证时，坐标空间不匹配导致验证结果错误
5. 裁剪框约束和自动缩放基于错误的验证结果，导致视觉上的位移

---

## 3. 问题根源总结

| 问题 | 描述 |
|------|------|
| **坐标空间混淆** | 透视四边形在逻辑空间计算，裁剪框在纹理空间存储 |
| **rotate_steps 处理不一致** | shader中rotate_steps影响渲染，但裁剪模型中不考虑 |
| **验证逻辑缺陷** | `rect_inside_quad` 比较了两个不同坐标空间的几何体 |
| **宽高比计算** | aspect_ratio 使用逻辑尺寸，但矩阵未完整考虑旋转 |

---

## 4. 修复方案：完全统一到Shader

### 4.1 方案概述

**核心思想**：将所有黑边检测和验证逻辑从Python端移至Shader端，确保所有 `rotate_steps` 值使用完全相同的检测逻辑。

**优点**：
- 完全消除Python端和Shader端的坐标空间不一致问题
- 所有 `rotate_steps` 值使用完全相同的检测逻辑
- 渲染和验证在同一位置执行，保证一致性
- 减少Python端的复杂度

**设计原则**：
- Python端只负责传递原始参数，不做任何坐标变换
- Shader端统一处理所有变换和边界检测
- 裁剪框验证也在Shader坐标系中进行

---

## 5. 详细修复步骤

### 步骤1：修改 Shader 文件 `gl_image_viewer.frag`

#### 1.1 增强统一黑边检测函数

当前的 `is_within_valid_bounds()` 函数已经存在，但需要确保它正确处理所有变换组合。

**修改位置**: 行 182-204

**当前代码**:
```glsl
// Unified black border detection function
// Checks if UV coordinate is within valid bounds after all transformations
bool is_within_valid_bounds(vec2 uv) {
    // 1. Apply inverse perspective transformation
    vec2 uv_perspective = apply_inverse_perspective(uv);
    
    // Check if perspective transformation caused out-of-bounds
    if (uv_perspective.x < 0.0 || uv_perspective.x > 1.0 ||
        uv_perspective.y < 0.0 || uv_perspective.y > 1.0) {
        return false;
    }
    
    // 2. Apply 90-degree rotation
    vec2 uv_rotated = apply_rotation_90(uv_perspective, uRotate90);
    
    // Final check: ensure we're within physical texture bounds
    if (uv_rotated.x < 0.0 || uv_rotated.x > 1.0 ||
        uv_rotated.y < 0.0 || uv_rotated.y > 1.0) {
        return false;
    }
    
    return true;
}
```

**改进后代码** (增加宽高比校正):
```glsl
// Unified black border detection function
// Checks if UV coordinate is within valid bounds after all transformations
// This function ensures consistent black border detection across all rotate_steps values
bool is_within_valid_bounds(vec2 uv) {
    // 1. Apply inverse perspective transformation (includes straighten)
    vec2 uv_perspective = apply_inverse_perspective(uv);
    
    // Check if perspective transformation caused out-of-bounds
    // Use small epsilon to avoid edge artifacts
    float eps = 1e-4;
    if (uv_perspective.x < -eps || uv_perspective.x > 1.0 + eps ||
        uv_perspective.y < -eps || uv_perspective.y > 1.0 + eps) {
        return false;
    }
    
    // 2. Apply 90-degree rotation to map to physical texture space
    vec2 uv_rotated = apply_rotation_90(uv_perspective, uRotate90);
    
    // Final check: ensure we're within physical texture bounds [0, 1]
    if (uv_rotated.x < -eps || uv_rotated.x > 1.0 + eps ||
        uv_rotated.y < -eps || uv_rotated.y > 1.0 + eps) {
        return false;
    }
    
    return true;
}

// New function: Check if a logical-space crop box corner is valid
// This is used for crop validation in the same coordinate space as rendering
bool is_crop_corner_valid(vec2 logical_corner) {
    return is_within_valid_bounds(logical_corner);
}
```

#### 1.2 新增 Shader 函数：计算有效裁剪区域

**添加位置**: 在 `is_within_valid_bounds()` 函数之后

```glsl
// Calculate the maximum inscribed rectangle within the valid perspective quad
// This is used to constrain the crop box to avoid black borders
// Returns: (min_x, min_y, max_x, max_y) of the valid crop region
vec4 compute_valid_crop_bounds() {
    // Sample the valid region by testing a grid of points
    // This gives us the effective bounds after all transformations
    
    float min_x = 1.0;
    float min_y = 1.0;
    float max_x = 0.0;
    float max_y = 0.0;
    
    // Test boundary points
    const int samples = 32;
    for (int i = 0; i <= samples; i++) {
        float t = float(i) / float(samples);
        
        // Test all four edges
        vec2 test_points[4];
        test_points[0] = vec2(t, 0.0);       // Top edge
        test_points[1] = vec2(t, 1.0);       // Bottom edge
        test_points[2] = vec2(0.0, t);       // Left edge
        test_points[3] = vec2(1.0, t);       // Right edge
        
        for (int j = 0; j < 4; j++) {
            if (is_within_valid_bounds(test_points[j])) {
                min_x = min(min_x, test_points[j].x);
                min_y = min(min_y, test_points[j].y);
                max_x = max(max_x, test_points[j].x);
                max_y = max(max_y, test_points[j].y);
            }
        }
    }
    
    return vec4(min_x, min_y, max_x, max_y);
}
```

---

### 步骤2：修改 Python 端 `gl_crop/model.py`

#### 2.1 移除坐标空间转换逻辑

**修改文件**: `src/iPhoto/gui/ui/widgets/gl_crop/model.py`

**当前 `update_perspective()` 方法** (行 80-145):

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
    # ... 现有代码 ...
    
    # Pass original parameters directly to the perspective matrix builder.
    # The shader now handles black border detection uniformly across all rotation
    # steps, so we no longer need to reverse parameters for odd rotations.
    matrix = build_perspective_matrix(
        new_vertical,
        new_horizontal,
        image_aspect_ratio=aspect_ratio,
        straighten_degrees=new_straighten,
        rotate_steps=0,  # Always 0; rotation is handled by shader's uRotate90
        flip_horizontal=new_flip,
    )
    self._perspective_quad = compute_projected_quad(matrix)
    return True
```

**改进后代码**:

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
    """Update the perspective quad based on parameters.

    NOTE: This method now computes the perspective quad in LOGICAL space,
    matching the shader's coordinate system. The crop validation functions
    (_current_normalised_rect, is_crop_inside_quad, etc.) must also operate
    in logical space for consistency.

    Parameters
    ----------
    vertical:
        Vertical perspective distortion.
    horizontal:
        Horizontal perspective distortion.
    straighten:
        Straighten angle in degrees.
    rotate_steps:
        Number of 90° rotation steps.
    flip_horizontal:
        Whether to flip horizontally.
    aspect_ratio:
        Image aspect ratio (width/height) in LOGICAL space (post-rotation).

    Returns
    -------
    bool:
        True if the perspective quad changed, False otherwise.
    """
    new_vertical = float(vertical)
    new_horizontal = float(horizontal)
    new_straighten = float(straighten)
    new_rotate = int(rotate_steps)
    new_flip = bool(flip_horizontal)

    # Check if anything changed
    if (
        abs(new_vertical - self._perspective_vertical) <= 1e-6
        and abs(new_horizontal - self._perspective_horizontal) <= 1e-6
        and abs(new_straighten - self._straighten_degrees) <= 1e-6
        and new_rotate == self._rotate_steps
        and new_flip is self._flip_horizontal
    ):
        return False

    self._perspective_vertical = new_vertical
    self._perspective_horizontal = new_horizontal
    self._straighten_degrees = new_straighten
    self._rotate_steps = new_rotate
    self._flip_horizontal = new_flip

    # Build perspective matrix in LOGICAL space (matching shader's coordinate system).
    # The shader handles 90° rotation via uRotate90, so we pass rotate_steps=0 here.
    # The aspect_ratio must be the LOGICAL aspect ratio (post-rotation dimensions).
    matrix = build_perspective_matrix(
        new_vertical,
        new_horizontal,
        image_aspect_ratio=aspect_ratio,
        straighten_degrees=new_straighten,
        rotate_steps=0,  # Rotation handled by shader's uRotate90
        flip_horizontal=new_flip,
    )
    
    # Compute the perspective quad in LOGICAL space
    logical_quad = compute_projected_quad(matrix)
    
    # Transform the quad from logical space to texture space for crop validation
    # This ensures crop coordinates (stored in texture space) are validated correctly
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
    """Transform a quad from logical space to texture space.
    
    This is the inverse of the shader's apply_rotation_90() function.
    When the shader applies rotation to go from logical→physical,
    we need the inverse to go from logical→texture for crop validation.
    
    Parameters
    ----------
    quad:
        List of (x, y) tuples in logical space.
    rotate_steps:
        Number of 90° rotation steps.
        
    Returns
    -------
    list[tuple[float, float]]:
        List of (x, y) tuples in texture space.
    """
    if rotate_steps % 4 == 0:
        return quad
    
    def inverse_rotate_point(x: float, y: float) -> tuple[float, float]:
        steps = rotate_steps % 4
        if steps == 1:
            # Inverse of 90° CW: (x, y) = (y', 1-x')
            return (y, 1.0 - x)
        elif steps == 2:
            # Inverse of 180°: (x, y) = (1-x', 1-y')
            return (1.0 - x, 1.0 - y)
        else:  # steps == 3
            # Inverse of 270° CW: (x, y) = (1-y', x')
            return (1.0 - y, x)
    
    return [inverse_rotate_point(pt[0], pt[1]) for pt in quad]
```

#### 2.2 更新 `_current_normalised_rect()` 方法

**当前代码** (行 147-150):
```python
def _current_normalised_rect(self) -> NormalisedRect:
    """Return the current crop as a normalised rect."""
    left, top, right, bottom = self._crop_state.bounds_normalised()
    return NormalisedRect(left, top, right, bottom)
```

**改进后代码** (保持不变，因为我们现在在 `update_perspective` 中将 quad 转换到纹理空间):
```python
def _current_normalised_rect(self) -> NormalisedRect:
    """Return the current crop as a normalised rect in TEXTURE space.
    
    The crop coordinates are stored in texture space, and the perspective_quad
    is now also transformed to texture space in update_perspective(), so
    this method can directly return the texture-space rect.
    """
    left, top, right, bottom = self._crop_state.bounds_normalised()
    return NormalisedRect(left, top, right, bottom)
```

---

### 步骤3：修改 Python 端 `gl_renderer.py`

#### 3.1 确保 Uniform 传递正确

**当前代码** (行 396-428):
```python
# Pass rotation to shader as uniform
self._set_uniform1i("uRotate90", rotate_steps % 4)

# Pass transformation parameters for unified black border detection
self._set_uniform1f("uStraightenDegrees", straighten_value)
self._set_uniform1f("uVertical", adjustment_value("Perspective_Vertical", 0.0))
self._set_uniform1f("uHorizontal", adjustment_value("Perspective_Horizontal", 0.0))
self._set_uniform1i("uFlipHorizontal", 1 if flip_enabled else 0)
```

**无需修改**：当前代码已经正确传递所有参数。

---

### 步骤4：修改 Python 端 `view_transform_controller.py`

#### 4.1 简化 `compute_rotation_cover_scale()` 函数

**修改文件**: `src/iPhoto/gui/ui/widgets/view_transform_controller.py`

**当前代码** (行 32-92):
```python
def compute_rotation_cover_scale(
    texture_size: tuple[int, int],
    base_scale: float,
    straighten_degrees: float,
    rotate_steps: int,
    physical_texture_size: tuple[int, int] | None = None,
) -> float:
    # ... 复杂的角隅检测逻辑 ...
```

**改进后代码**:
```python
def compute_rotation_cover_scale(
    texture_size: tuple[int, int],
    base_scale: float,
    straighten_degrees: float,
    rotate_steps: int,
    physical_texture_size: tuple[int, int] | None = None,
) -> float:
    """Return the scale multiplier that keeps rotated images free of black corners.
    
    NOTE: With the unified shader-based black border detection, this function
    now only needs to account for the straighten angle. The 90° discrete rotations
    are handled entirely in the shader, which maps logical→physical coordinates
    without introducing black corners.
    
    Args:
        texture_size: Logical (rotation-aware) dimensions used for frame calculation
        base_scale: Scale factor calculated from logical dimensions
        straighten_degrees: Straighten angle in degrees
        rotate_steps: Number of 90° rotations (now only used for legacy compatibility)
        physical_texture_size: Physical (original) dimensions for bounds checking.
    """
    tex_w, tex_h = texture_size
    if tex_w <= 0 or tex_h <= 0 or base_scale <= 0.0:
        return 1.0
    
    # Only straighten angle requires cover scale adjustment
    # 90° rotations are handled by shader without introducing black corners
    total_degrees = float(straighten_degrees)
    
    if abs(total_degrees) <= 1e-5:
        return 1.0
        
    theta = math.radians(total_degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    
    # Frame corners calculated in logical space (rotation-aware dimensions)
    half_frame_w = tex_w * base_scale * 0.5
    half_frame_h = tex_h * base_scale * 0.5
    corners = [
        (-half_frame_w, -half_frame_h),
        (half_frame_w, -half_frame_h),
        (half_frame_w, half_frame_h),
        (-half_frame_w, half_frame_h),
    ]
    
    # Use physical dimensions for bounds checking
    if physical_texture_size is not None:
        phys_w, phys_h = physical_texture_size
    else:
        phys_w, phys_h = tex_w, tex_h
    
    scale = 1.0
    for xf, yf in corners:
        x_prime = xf * cos_t + yf * sin_t
        y_prime = -xf * sin_t + yf * cos_t
        s_corner = max((2.0 * abs(x_prime)) / phys_w, (2.0 * abs(y_prime)) / phys_h)
        if s_corner > scale:
            scale = s_corner
    return max(scale, 1.0)
```

---

### 步骤5：修改 Python 端 `gl_image_viewer/widget.py`

#### 5.1 简化 `_update_cover_scale()` 方法

**修改文件**: `src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py`

**当前代码** (行 566-604):
```python
def _update_cover_scale(self, straighten_deg: float, rotate_steps: int) -> None:
    """Compute the rotation cover scale and forward it to the transform controller."""

    if not self._renderer or not self._renderer.has_texture():
        self._transform_controller.set_image_cover_scale(1.0)
        return
    
    # When rotation is handled in the shader (current implementation), cover_scale
    # only needs to account for straighten angle, not the 90° discrete rotations.
    if abs(straighten_deg) <= 1e-5:
        # No straighten angle: no cover scale needed
        self._transform_controller.set_image_cover_scale(1.0)
        return
        
    # ... 其余代码 ...
```

**改进后代码** (简化):
```python
def _update_cover_scale(self, straighten_deg: float, rotate_steps: int) -> None:
    """Compute the rotation cover scale and forward it to the transform controller.
    
    NOTE: With unified shader-based black border detection, cover_scale only needs
    to account for straighten angle. The shader handles 90° rotations without
    introducing black corners, so rotate_steps is no longer used here.
    """
    if not self._renderer or not self._renderer.has_texture():
        self._transform_controller.set_image_cover_scale(1.0)
        return
    
    # Only straighten angle requires cover scale adjustment
    if abs(straighten_deg) <= 1e-5:
        self._transform_controller.set_image_cover_scale(1.0)
        return
        
    tex_w, tex_h = self._texture_dimensions()
    if tex_w <= 0 or tex_h <= 0:
        self._transform_controller.set_image_cover_scale(1.0)
        return
        
    display_w, display_h = self._display_texture_dimensions()
    view_width, view_height = self._view_dimensions_device_px()
    
    base_scale = compute_fit_to_view_scale(
        (display_w, display_h), float(view_width), float(view_height)
    )
    
    # Compute cover scale for straighten only (rotate_steps handled by shader)
    cover_scale = compute_rotation_cover_scale(
        (display_w, display_h),
        base_scale,
        straighten_deg,
        0,  # rotate_steps not needed - shader handles rotation
        physical_texture_size=(tex_w, tex_h),
    )
    
    self._transform_controller.set_image_cover_scale(cover_scale)
```

---

### 步骤6：修改 `perspective_math.py`（可选简化）

**修改文件**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

**当前 `build_perspective_matrix()` 函数** (行 37-141):

由于我们现在在调用处始终传入 `rotate_steps=0`，可以考虑：
1. 保留参数以保持向后兼容性
2. 添加注释说明该参数不再使用

**改进后代码** (添加注释):
```python
def build_perspective_matrix(
    vertical: float,
    horizontal: float,
    *,
    image_aspect_ratio: float,
    straighten_degrees: float = 0.0,
    rotate_steps: int = 0,  # NOTE: Deprecated - rotation now handled by shader
    flip_horizontal: bool = False,
) -> np.ndarray:
    """Return the 3×3 matrix that maps projected UVs back to texture UVs.

    The caller supplies ``image_aspect_ratio`` so the straightening rotation can
    be evaluated in a coordinate space that preserves the original image
    proportions.  Without this correction non-square images would be rotated in
    a squashed reference frame which visually manifests as a shear.
    
    NOTE: The rotate_steps parameter is deprecated. 90° discrete rotations are
    now handled entirely in the shader via uRotate90 uniform. This function
    should always be called with rotate_steps=0.
    """
    # ... 其余代码保持不变 ...
```

---

## 6. 实现顺序和验证

### 6.1 实施顺序

| 步骤 | 文件 | 改动 | 优先级 |
|------|------|------|--------|
| 1 | `gl_crop/model.py` | 添加 `_transform_quad_to_texture_space()` 方法 | 高 |
| 2 | `gl_crop/model.py` | 修改 `update_perspective()` 使用新方法 | 高 |
| 3 | `gl_image_viewer.frag` | 增强 `is_within_valid_bounds()` 函数 | 中 |
| 4 | `view_transform_controller.py` | 简化 `compute_rotation_cover_scale()` | 中 |
| 5 | `gl_image_viewer/widget.py` | 简化 `_update_cover_scale()` | 低 |
| 6 | `perspective_math.py` | 添加废弃注释 | 低 |

### 6.2 验证测试矩阵

| 测试场景 | 预期结果 |
|---------|---------|
| step=0 + straighten=0 + perspective=0 | 裁剪框位置正确，无黑边 |
| step=0 + straighten=±10° | 裁剪框位置正确，图像放大覆盖 |
| step=0 + vertical=±0.5 | 裁剪框位置正确，适应透视变换 |
| step=0 + horizontal=±0.5 | 裁剪框位置正确，适应透视变换 |
| step=1 + straighten=0 | 裁剪框位置正确，无黑边 |
| step=1 + straighten=±10° | 裁剪框位置正确，无黑边 |
| step=1 + vertical=±0.5 | 裁剪框位置正确，适应透视变换 |
| step=2 + straighten=±10° | 裁剪框位置正确 |
| step=3 + straighten=±10° | 裁剪框位置正确 |
| step=1 + straighten=5° + vertical=0.3 + horizontal=-0.2 | 裁剪框位置正确 |
| 裁剪框拖动（任意step） | 裁剪框正确约束在有效区域内 |
| 透视滑块拖动（任意step） | 裁剪框自动适应透视变化 |

---

## 7. 影响范围

| 文件路径 | 改动类型 | 风险评估 |
|---------|---------|---------|
| `src/iPhoto/gui/ui/widgets/gl_crop/model.py` | 核心修改 | 高 |
| `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag` | 增强函数 | 中 |
| `src/iPhoto/gui/ui/widgets/view_transform_controller.py` | 简化逻辑 | 中 |
| `src/iPhoto/gui/ui/widgets/gl_image_viewer/widget.py` | 简化调用 | 低 |
| `src/iPhoto/gui/ui/widgets/perspective_math.py` | 添加注释 | 低 |

---

## 8. 回归测试清单

### 8.1 功能测试

- [ ] 裁剪框拖动正常
- [ ] 裁剪框缩放正常
- [ ] 透视滑块响应正确
- [ ] 旋转按钮工作正常
- [ ] 微调旋转滑块工作正常
- [ ] 水平翻转功能正常

### 8.2 边界测试

- [ ] 极端透视值 (vertical=1.0, horizontal=1.0)
- [ ] 极端微调角度 (straighten=±45°)
- [ ] 组合变换极端情况
- [ ] 非方形图像测试
- [ ] 极端宽高比图像测试

### 8.3 性能测试

- [ ] Shader性能无明显下降
- [ ] 裁剪交互流畅性
- [ ] 透视滑块响应延迟

---

## 9. 附录：变换公式参考

### 90°旋转变换（纹理空间 → 逻辑空间）

```
Step 0 (0°):   (x', y') = (x, y)
Step 1 (90° CW):  (x', y') = (1-y, x)
Step 2 (180°): (x', y') = (1-x, 1-y)
Step 3 (270° CW): (x', y') = (y, 1-x)
```

### 逆变换（逻辑空间 → 纹理空间）

```
Step 0 (0°):   (x, y) = (x', y')
Step 1 (90° CW):  (x, y) = (y', 1-x')
Step 2 (180°): (x, y) = (1-x', 1-y')
Step 3 (270° CW): (x, y) = (1-y', x')
```

---

## 10. 参考文档

- [黑边检测参数差异分析](./black_border_detection_parameters.md)
- [统一黑边检测逻辑需求](./requirements_unified_black_border_detection.md)

# Issue: 裁剪框位移问题进一步修复方案

## 1. 当前修复状态回顾

### 1.1 已完成的修复

根据提交 `26539c0` 的改动，目前已实现以下修复：

#### Shader端改动 (gl_image_viewer.frag)
- 新增 uniforms: `uStraightenDegrees`, `uVertical`, `uHorizontal`, `uFlipHorizontal`
- 增强 `is_within_valid_bounds()` 函数，添加 epsilon 容差提高数值稳定性
- 统一所有 rotate_steps (0/1/2/3) 的黑边检测逻辑

#### Python端改动 (gl_renderer.py)
- 注册并传递新的 uniforms 到 shader
- 直接传递参数，移除反向处理逻辑

#### 裁剪模型修复 (gl_crop/model.py)
- 添加 `_transform_quad_to_texture_space()` 方法
- 将透视四边形从逻辑空间转换到纹理空间进行裁剪验证
- 修复渲染和裁剪验证之间的坐标系不匹配问题

### 1.2 当前剩余问题

尽管进行了上述修复，仍存在以下场景的裁剪框位移问题：

1. **旋转步数为奇数时透视变换效果异常**
   - 当 `step = 1 或 3` 时，加入 `vertical` 或 `horizontal` 透视校正后，裁剪框的约束边界可能不准确

2. **宽高比变化导致的透视四边形计算误差**
   - 当图像旋转90°/270°后，宽高比发生交换
   - 透视矩阵使用的宽高比与实际显示的宽高比可能存在不一致

3. **裁剪框拖动交互时的边界约束错误**
   - 在某些变换组合下，拖动裁剪框边缘时约束不正确
   - 可能导致裁剪框超出有效区域或被过度限制

---

## 2. 问题根源深入分析

### 2.1 坐标变换链分析

当前系统中的坐标变换涉及多个步骤：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        坐标变换链                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  用户交互空间（屏幕坐标）                                                  │
│         ↓                                                                │
│  ViewTransformController（逻辑纹理空间）                                   │
│         ↓  compute_fit_to_view_scale() 使用 display_texture_size         │
│         ↓                                                                │
│  裁剪模型（纹理空间）                                                      │
│         ↓  _transform_quad_to_texture_space()                            │
│         ↓                                                                │
│  Shader（物理纹理空间）                                                    │
│         │                                                                │
│         ├── apply_inverse_perspective() → 透视逆变换                      │
│         └── apply_rotation_90() → 90°旋转                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 `_transform_quad_to_texture_space()` 问题分析

当前实现：

```python
def _transform_quad_to_texture_space(
    self, 
    quad: list[tuple[float, float]], 
    rotate_steps: int
) -> list[tuple[float, float]]:
    steps = rotate_steps % 4
    if steps == 0:
        return quad
    
    def inverse_rotate_point(x: float, y: float) -> tuple[float, float]:
        if steps == 1:
            # Inverse of 90° CW: (x, y) -> (y, 1-x)
            # So to reverse: (x', y') -> (1-y', x')
            return (1.0 - y, x)
        elif steps == 2:
            # Inverse of 180°
            return (1.0 - x, 1.0 - y)
        else:  # steps == 3
            # Inverse of 270° CW
            return (y, 1.0 - x)
    
    return [inverse_rotate_point(pt[0], pt[1]) for pt in quad]
```

**问题1**: 透视四边形在逻辑空间计算，但变换到纹理空间时没有考虑透视变换的非线性特性。简单的点坐标旋转变换不能正确表示透视变换后的四边形。

**问题2**: 当透视参数 (vertical/horizontal) 不为0时，四边形不再是矩形。对非矩形四边形应用逆旋转变换后，裁剪验证逻辑 (`rect_inside_quad`) 可能产生错误结果。

### 2.3 透视矩阵构建问题

在 `build_perspective_matrix()` 中：

```python
# perspective_math.py 第92行
total_degrees = float(straighten_degrees) + float(int(rotate_steps)) * -90.0
```

**问题**: 虽然在调用处 `rotate_steps=0`，但当用户同时使用 straighten 和透视校正时，straighten 角度需要在正确的宽高比下应用。当前实现使用 `image_aspect_ratio` 参数，但这个参数在调用链中可能不一致：

- `gl_renderer.py` 使用 `logical_aspect_ratio`（旋转后的逻辑宽高比）
- `gl_crop/model.py` 的 `update_perspective()` 也传入 `aspect_ratio` 参数

如果两处的宽高比计算方式不同，会导致透视四边形与渲染结果不匹配。

---

## 3. 修复方案

### 方案A: 完全统一到Shader端验证（推荐）

#### 3.1 核心思想

将所有裁剪验证逻辑移至 Shader 端，确保验证和渲染使用完全相同的变换链。

#### 3.2 实现步骤

##### 步骤1: 修改 Shader 支持裁剪边界查询

在 `gl_image_viewer.frag` 中添加用于计算有效裁剪区域的采样函数：

```glsl
// 新增：在main()之前添加
// 计算透视四边形的有效边界
// 通过采样边界点来确定最大内接矩形
uniform bool uComputeBounds;  // 标记是否在计算边界模式
uniform vec2 uQueryPoint;     // 查询点的逻辑坐标

// 输出：查询点是否在有效区域内
// 这个函数可以在Python端通过多次渲染调用来构建有效区域
bool query_valid_region(vec2 point) {
    // 使用与渲染相同的变换链
    vec2 uv_perspective = apply_inverse_perspective(point);
    
    float eps = 1e-4;
    if (uv_perspective.x < -eps || uv_perspective.x > 1.0 + eps ||
        uv_perspective.y < -eps || uv_perspective.y > 1.0 + eps) {
        return false;
    }
    
    vec2 uv_rotated = apply_rotation_90(uv_perspective, uRotate90);
    
    if (uv_rotated.x < -eps || uv_rotated.x > 1.0 + eps ||
        uv_rotated.y < -eps || uv_rotated.y > 1.0 + eps) {
        return false;
    }
    
    return true;
}
```

##### 步骤2: 添加 Python 端边界采样函数

在 `gl_crop/model.py` 或新文件中添加：

```python
def sample_valid_bounds_from_shader(
    renderer: GLRenderer,
    adjustments: dict,
    grid_resolution: int = 32,
) -> list[tuple[float, float]]:
    """从Shader端采样获取有效区域边界点。
    
    通过在逻辑空间的网格上查询每个点是否有效，
    构建准确的透视四边形边界。
    """
    valid_points = []
    
    for i in range(grid_resolution + 1):
        for j in range(grid_resolution + 1):
            x = i / grid_resolution
            y = j / grid_resolution
            
            # 通过shader查询该点是否有效
            # 这需要renderer支持单点查询模式
            if renderer.query_point_valid(x, y, adjustments):
                valid_points.append((x, y))
    
    return compute_convex_hull(valid_points)
```

##### 步骤3: 修改 `CropSessionModel.update_perspective()`

```python
def update_perspective(
    self,
    vertical: float,
    horizontal: float,
    straighten: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
    aspect_ratio: float = 1.0,
    renderer: GLRenderer | None = None,  # 新增参数
) -> bool:
    """Update the perspective quad based on parameters.
    
    如果提供了renderer，使用Shader采样获取准确的透视四边形；
    否则使用数学计算的近似值。
    """
    # ... 现有参数检查代码 ...
    
    if renderer is not None and renderer.has_texture():
        # 使用Shader采样获取准确的透视四边形
        adjustments = self._build_adjustments_for_sampling(
            vertical, horizontal, straighten, rotate_steps, flip_horizontal
        )
        self._perspective_quad = sample_valid_bounds_from_shader(
            renderer, adjustments
        )
    else:
        # 回退到数学计算（当前实现）
        matrix = build_perspective_matrix(
            new_vertical,
            new_horizontal,
            image_aspect_ratio=aspect_ratio,
            straighten_degrees=new_straighten,
            rotate_steps=0,
            flip_horizontal=new_flip,
        )
        logical_quad = compute_projected_quad(matrix)
        self._perspective_quad = self._transform_quad_to_texture_space(
            logical_quad, new_rotate
        )
    
    return True
```

#### 3.3 优点

- 完全消除Python端计算与Shader渲染的不一致性
- 使用与渲染完全相同的变换链进行边界检测
- 自动适应未来可能添加的新变换类型

#### 3.4 缺点

- 需要多次GPU查询来采样边界点，可能影响性能
- 实现复杂度较高
- 需要修改 GLRenderer 支持查询模式

---

### 方案B: 修复数学计算确保一致性

#### 3.1 核心思想

修复 Python 端的数学计算，确保其与 Shader 端的变换链完全一致。

#### 3.2 实现步骤

##### 步骤1: 统一宽高比计算

在 `gl_crop/model.py` 的 `update_perspective()` 中，确保使用与渲染器相同的逻辑宽高比：

```python
def update_perspective(
    self,
    vertical: float,
    horizontal: float,
    straighten: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
    aspect_ratio: float = 1.0,
    physical_aspect_ratio: float | None = None,  # 新增：物理宽高比
) -> bool:
    """Update the perspective quad.
    
    Parameters
    ----------
    aspect_ratio:
        逻辑空间的宽高比（旋转后）
    physical_aspect_ratio:
        物理纹理的宽高比（旋转前）。如果提供，用于计算变换矩阵。
    """
    # ... 参数检查 ...
    
    # 使用正确的宽高比构建透视矩阵
    effective_aspect_ratio = aspect_ratio
    
    # 透视矩阵需要在逻辑空间中构建，使用逻辑宽高比
    matrix = build_perspective_matrix(
        new_vertical,
        new_horizontal,
        image_aspect_ratio=effective_aspect_ratio,
        straighten_degrees=new_straighten,
        rotate_steps=0,
        flip_horizontal=new_flip,
    )
    
    # 在逻辑空间计算投影四边形
    logical_quad = compute_projected_quad(matrix)
    
    # 正确转换到纹理空间，考虑变换的非线性特性
    self._perspective_quad = self._transform_quad_to_texture_space_v2(
        logical_quad,
        new_rotate,
        matrix,  # 传入矩阵用于正确的逆变换
    )
    
    return True
```

##### 步骤2: 改进四边形空间变换

创建新的变换函数，正确处理透视变换的非线性特性：

```python
def _transform_quad_to_texture_space_v2(
    self, 
    quad: list[tuple[float, float]], 
    rotate_steps: int,
    perspective_matrix: np.ndarray,
) -> list[tuple[float, float]]:
    """Transform a quad from logical space to texture space.
    
    这个版本正确处理透视变换的非线性特性。
    
    变换步骤：
    1. 四边形是在逻辑空间通过透视矩阵的前向投影计算得到
    2. 需要应用旋转的逆变换来得到纹理空间坐标
    3. 裁剪坐标存储在纹理空间，所以验证需要在纹理空间进行
    """
    steps = rotate_steps % 4
    if steps == 0:
        return quad
    
    # 对于透视变换，简单的点旋转可能不够准确
    # 需要考虑完整的变换链
    
    def apply_inverse_rotation(x: float, y: float) -> tuple[float, float]:
        """Apply inverse 90° rotation to go from logical to texture space."""
        if steps == 1:
            # Shader: (x, y) -> (y, 1-x) for 90° CW
            # Inverse: (x', y') -> (1-y', x')
            return (1.0 - y, x)
        elif steps == 2:
            # Shader: (x, y) -> (1-x, 1-y) for 180°
            # Inverse is the same
            return (1.0 - x, 1.0 - y)
        else:  # steps == 3
            # Shader: (x, y) -> (1-y, x) for 270° CW
            # Inverse: (x', y') -> (y', 1-x')
            return (y, 1.0 - x)
    
    # 但这里有个关键问题：透视四边形是在应用透视变换后计算的
    # 在Shader中，变换顺序是：
    # 1. apply_inverse_perspective（将逻辑UV映射回未变换空间）
    # 2. apply_rotation_90（将结果映射到物理纹理空间）
    
    # 所以透视四边形（在逻辑空间的边界）需要：
    # 1. 四边形点 -> 逻辑空间（透视变换后的有效区域边界）
    # 2. 应用逆旋转 -> 纹理空间
    
    return [apply_inverse_rotation(pt[0], pt[1]) for pt in quad]
```

##### 步骤3: 添加变换链验证

添加调试/验证函数来确保 Python 计算与 Shader 一致：

```python
def _verify_transform_chain(
    self,
    test_point: tuple[float, float],
    rotate_steps: int,
    perspective_matrix: np.ndarray,
) -> bool:
    """验证Python端变换与Shader端一致。
    
    用于调试和单元测试。
    """
    # 模拟Shader的变换链
    x, y = test_point
    
    # 1. apply_inverse_perspective
    centered = np.array([(x * 2.0) - 1.0, (y * 2.0) - 1.0, 1.0])
    warped = perspective_matrix @ centered
    denom = warped[2] if abs(warped[2]) > 1e-5 else 1e-5
    restored = warped[:2] / denom
    uv_perspective = (restored[0] * 0.5 + 0.5, restored[1] * 0.5 + 0.5)
    
    # 检查是否越界
    if (uv_perspective[0] < 0 or uv_perspective[0] > 1 or
        uv_perspective[1] < 0 or uv_perspective[1] > 1):
        return False
    
    # 2. apply_rotation_90
    steps = rotate_steps % 4
    px, py = uv_perspective
    if steps == 1:
        uv_rotated = (py, 1.0 - px)
    elif steps == 2:
        uv_rotated = (1.0 - px, 1.0 - py)
    elif steps == 3:
        uv_rotated = (1.0 - py, px)
    else:
        uv_rotated = (px, py)
    
    # 检查最终纹理坐标是否有效
    return (0 <= uv_rotated[0] <= 1 and 0 <= uv_rotated[1] <= 1)
```

#### 3.3 优点

- 不需要GPU查询，纯数学计算
- 性能开销小
- 可以添加详细的单元测试验证正确性

#### 3.4 缺点

- 需要完全理解并复现Shader的变换链
- 如果Shader变换逻辑更新，Python端也需要同步更新
- 对于复杂的变换组合，数学公式可能变得难以维护

---

### 方案C: 混合方案（推荐用于初期实现）

#### 3.1 核心思想

结合方案A和B的优点：
- 使用改进的数学计算作为主要实现
- 添加运行时验证机制检测不一致性
- 在关键场景（如变换参数变化时）使用Shader采样进行校准

#### 3.2 实现步骤

##### 步骤1: 改进现有数学计算（方案B的步骤1-2）

##### 步骤2: 添加一致性检测

```python
class CropSessionModel:
    def __init__(self) -> None:
        # ... 现有初始化 ...
        self._consistency_check_enabled = True
        self._last_shader_quad: list[tuple[float, float]] | None = None
    
    def update_perspective(self, ...):
        # 使用数学计算获得初始四边形
        computed_quad = self._compute_perspective_quad(...)
        
        if self._consistency_check_enabled and self._renderer:
            # 采样几个关键点进行验证
            sample_points = [
                (0.0, 0.0), (0.5, 0.0), (1.0, 0.0),
                (0.0, 0.5), (1.0, 0.5),
                (0.0, 1.0), (0.5, 1.0), (1.0, 1.0),
            ]
            discrepancies = self._check_consistency(sample_points, computed_quad)
            
            if discrepancies > threshold:
                # 回退到Shader采样
                computed_quad = self._sample_quad_from_shader()
        
        self._perspective_quad = computed_quad
```

##### 步骤3: 添加错误报告和日志

```python
def _check_consistency(
    self,
    sample_points: list[tuple[float, float]],
    computed_quad: list[tuple[float, float]],
) -> int:
    """检测计算四边形与Shader结果的不一致性。
    
    返回不一致点的数量。
    """
    discrepancy_count = 0
    
    for point in sample_points:
        # 检查点是否在计算的四边形内
        in_computed = point_in_convex_polygon(point, computed_quad)
        
        # 检查Shader认为该点是否有效
        in_shader = self._renderer.query_point_valid(point[0], point[1], ...)
        
        if in_computed != in_shader:
            discrepancy_count += 1
            _LOGGER.warning(
                "Consistency check failed at point (%f, %f): "
                "computed=%s, shader=%s",
                point[0], point[1], in_computed, in_shader
            )
    
    return discrepancy_count
```

#### 3.3 优点

- 正常情况下使用高效的数学计算
- 检测到不一致时自动回退到更准确的Shader采样
- 提供详细的日志便于调试
- 渐进式改进，可以逐步优化数学计算的准确性

#### 3.4 缺点

- 实现复杂度最高
- 需要在Python端和Shader端同时维护验证逻辑
- 一致性检查有额外性能开销

---

## 4. 推荐实施路径

### Phase 1: 快速修复（1-2天）

1. **修复宽高比一致性**
   - 确保 `gl_renderer.py` 和 `gl_crop/model.py` 使用相同的宽高比计算方式
   - 在 `update_perspective()` 中添加 `physical_aspect_ratio` 参数

2. **添加调试日志**
   - 在关键变换点添加日志，便于追踪坐标变换

### Phase 2: 核心修复（3-5天）

1. **实现方案B的改进数学计算**
   - 创建 `_transform_quad_to_texture_space_v2()` 方法
   - 添加单元测试验证变换正确性

2. **添加边界情况处理**
   - 处理极端透视值
   - 处理接近90°旋转边界的straighten角度

### Phase 3: 完善（可选，3-5天）

1. **实现方案C的混合验证**
   - 添加Shader查询支持
   - 实现一致性检测机制

2. **性能优化**
   - 缓存透视四边形计算结果
   - 优化Shader采样频率

---

## 5. 测试矩阵

### 5.1 基本功能测试

| 测试用例 | step=0 | step=1 | step=2 | step=3 |
|---------|--------|--------|--------|--------|
| 仅透视校正 (V=0.5, H=0) | | | | |
| 仅透视校正 (V=0, H=0.5) | | | | |
| 透视校正组合 (V=0.3, H=0.3) | | | | |
| 仅straighten (±10°) | | | | |
| 透视+straighten | | | | |
| 透视+straighten+flip | | | | |

### 5.2 裁剪交互测试

| 测试场景 | 预期行为 |
|---------|---------|
| 拖动裁剪框中心 | 裁剪框不能超出有效区域 |
| 拖动裁剪框边缘 | 边缘约束在有效区域边界 |
| 拖动裁剪框角点 | 角点约束在有效区域内 |
| 透视参数变化时 | 裁剪框自动收缩/适应 |

### 5.3 边界条件测试

| 边界条件 | 预期行为 |
|---------|---------|
| V=1.0, H=1.0 (极端透视) | 正确计算有效区域 |
| straighten=±45° | 正确放大覆盖 |
| 非常窄的裁剪框 | 约束正确，不越界 |
| 非常大的裁剪框 | 能够正确收缩到有效区域 |

---

## 6. 代码变更影响分析

### 6.1 受影响的文件

| 文件 | 变更类型 | 风险等级 |
|------|---------|---------|
| `gl_crop/model.py` | 核心修改 | 高 |
| `gl_renderer.py` | 可能新增查询接口 | 中 |
| `perspective_math.py` | 可能添加辅助函数 | 低 |
| `gl_image_viewer.frag` | 可能添加查询模式支持 | 中 |
| `view_transform_controller.py` | 可能调整缩放计算 | 低 |

### 6.2 API变更

```python
# CropSessionModel.update_perspective() 签名变更
def update_perspective(
    self,
    vertical: float,
    horizontal: float,
    straighten: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
    aspect_ratio: float = 1.0,
    # 新增参数（可选）
    physical_aspect_ratio: float | None = None,
    renderer: GLRenderer | None = None,
) -> bool:
```

### 6.3 向后兼容性

- 所有新参数都是可选的，不影响现有调用
- 内部行为变更对外部API透明
- 无需迁移用户数据

---

## 7. 附录

### 7.1 坐标变换公式汇总

#### Shader端 `apply_rotation_90()`

```glsl
// 90° CW (step=1):  (x, y) -> (y, 1-x)
// 180° (step=2):    (x, y) -> (1-x, 1-y)
// 270° CW (step=3): (x, y) -> (1-y, x)
```

#### Python端逆变换

```python
# 90° CW inverse (step=1):  (x', y') -> (1-y', x')
# 180° inverse (step=2):    (x', y') -> (1-x', 1-y')
# 270° CW inverse (step=3): (x', y') -> (y', 1-x')
```

#### 透视变换

```python
# 前向变换 (使用 forward = inv(matrix)):
# centered = (x * 2 - 1, y * 2 - 1, 1)
# warped = forward @ centered
# result = (warped.xy / warped.z) * 0.5 + 0.5

# 逆变换 (使用 matrix):
# centered = (x * 2 - 1, y * 2 - 1, 1)
# warped = matrix @ centered
# result = (warped.xy / warped.z) * 0.5 + 0.5
```

### 7.2 相关文档

- [黑边检测参数差异分析](./black_border_detection_parameters.md)
- [统一黑边检测逻辑需求](./requirements_unified_black_border_detection.md)
- [原始Issue文档](./issue_crop_box_displacement_with_rotation.md)

### 7.3 调试技巧

1. **可视化透视四边形**
   ```python
   # 在crop overlay中绘制透视四边形边界
   def draw_debug_quad(self, quad):
       for i in range(len(quad)):
           pt1 = quad[i]
           pt2 = quad[(i + 1) % len(quad)]
           # 绘制线段 pt1 -> pt2
   ```

2. **输出变换链中间结果**
   ```python
   _LOGGER.debug(
       "Transform chain: logical_quad=%s -> texture_quad=%s, "
       "rotate_steps=%d",
       logical_quad, texture_quad, rotate_steps
   )
   ```

3. **Shader调试**
   ```glsl
   // 临时输出调试颜色
   if (!is_within_valid_bounds(uv_corrected)) {
       FragColor = vec4(1.0, 0.0, 0.0, 1.0);  // 红色表示无效区域
       return;
   }
   ```

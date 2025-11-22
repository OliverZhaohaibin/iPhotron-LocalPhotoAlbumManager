# iPhoto 裁剪框免黑边算法分析

## 背景

在 iPhoto 项目的图片编辑功能中，当裁剪步骤(crop step)不为0时，存在两种不同的免黑边算法实现：

1. **透视校正自动缩放算法** - 用于 vertical/horizontal perspective 调整时
2. **拖动边缘压力感应算法** - 用于拖动裁剪框边缘接近屏幕边界时

本文档基于项目源码（`src/iPhoto/gui/ui/widgets/`）详细分析这两种算法的实现机制和核心差异。

---

## 一、透视校正自动缩放算法

### 适用场景
- Vertical Perspective 调整（垂直透视校正，模拟绕X轴旋转）
- Horizontal Perspective 调整（水平透视校正，模拟绕Y轴旋转）
- 透视滑块交互时的实时裁剪框适配

### 核心实现位置

**主要文件：**
- `src/iPhoto/gui/ui/widgets/gl_crop_controller.py` - 裁剪控制器
- `src/iPhoto/gui/ui/widgets/perspective_math.py` - 透视数学计算
- `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag` - GPU着色器

### 算法原理

当用户调整透视参数时，系统采用以下步骤确保裁剪框始终包含在有效的投影四边形内：

#### 1. 透视投影矩阵构建

```python
# 来自 perspective_math.py
def build_perspective_matrix(vertical: float, horizontal: float) -> np.ndarray:
    """构建3×3透视投影矩阵，将投影UV映射回纹理UV"""
    
    # vertical/horizontal 范围 [-1, 1]，对应 ±20° 旋转
    angle_scale = math.radians(20.0)
    angle_x = clamped_v * angle_scale  # 绕X轴
    angle_y = clamped_h * angle_scale  # 绕Y轴
    
    # 构建旋转矩阵
    rx = np.array([
        [1.0, 0.0, 0.0],
        [0.0, cos(angle_x), -sin(angle_x)],
        [0.0, sin(angle_x), cos(angle_x)]
    ])
    
    ry = np.array([
        [cos(angle_y), 0.0, sin(angle_y)],
        [0.0, 1.0, 0.0],
        [-sin(angle_y), 0.0, cos(angle_y)]
    ])
    
    return np.matmul(ry, rx)  # Y轴旋转 × X轴旋转
```

**设计要点：**
- 透视角度范围：±20°（非常保守，避免极端畸变）
- 矩阵顺序：先X轴后Y轴（符合常见的俯仰+偏航组合）

#### 2. 投影四边形计算

```python
# 来自 perspective_math.py
def compute_projected_quad(matrix: np.ndarray) -> list[tuple[float, float]]:
    """计算单位纹理经过透视投影后的四边形"""
    
    # 求逆矩阵（从纹理空间到投影空间）
    forward = np.linalg.inv(matrix)
    
    # 对纹理四个角点进行投影
    corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    projected = []
    
    for (x, y) in corners:
        centered = np.array([(x * 2.0) - 1.0, (y * 2.0) - 1.0, 1.0])
        warped = forward @ centered
        denom = warped[2]  # 透视除法的分母
        
        # 避免除零
        if abs(denom) < 1e-6:
            denom = 1e-6 if denom >= 0.0 else -1e-6
        
        # 归一化到 [0, 1]
        nx = warped[0] / denom
        ny = warped[1] / denom
        projected.append(((nx + 1.0) * 0.5, (ny + 1.0) * 0.5))
    
    return projected
```

**关键概念：**
- `forward` 矩阵：从归一化纹理坐标到投影坐标的变换
- 透视除法：`w` 分量不为1时需要除以 `w` 才能得到真实坐标
- 投影四边形：透视变换后，原本的矩形变成梯形或不规则四边形

#### 3. 最小缩放因子计算（射线投射法）

```python
# 来自 perspective_math.py
def calculate_min_zoom_to_fit(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> float:
    """计算使矩形完全适配四边形所需的最小统一缩放"""
    
    cx, cy = rect.center
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    
    max_scale = 1.0
    for corner in corners:
        # 从矩形中心向角点发射射线
        direction = (corner[0] - cx, corner[1] - cy)
        if abs(direction[0]) <= 1e-9 and abs(direction[1]) <= 1e-9:
            continue
        
        # 射线与四边形边界的交点
        hit = _ray_polygon_hit((cx, cy), direction, quad)
        if hit is None or hit <= 1e-6:
            continue
        if hit >= 1.0:
            continue  # 角点已经在内部
        
        # 缩放因子 = 1 / 交点参数
        scale = 1.0 / max(hit, 1e-6)
        if scale > max_scale:
            max_scale = scale
    
    return max_scale
```

**算法解释：**
1. 以裁剪框中心为原点
2. 向每个角点发射射线
3. 计算射线与四边形边界的交点
4. 交点参数 `t < 1` 意味着角点超出边界
5. 缩放因子 `S = 1/t` 确保角点刚好落在边界上
6. 取所有角点的最大缩放因子

#### 4. 裁剪框更新逻辑

```python
# 来自 gl_crop_controller.py
def update_perspective(self, vertical: float, horizontal: float) -> None:
    """更新透视参数并强制裁剪约束"""
    
    # 1. 构建透视矩阵和投影四边形
    matrix = build_perspective_matrix(vertical, horizontal)
    self._perspective_quad = compute_projected_quad(matrix)
    
    # 2. 应用约束
    if self._baseline_crop_state is not None:
        # 交互模式：基于原始裁剪框进行适配
        changed = self._apply_baseline_perspective_fit()
    else:
        # 非交互模式：确保当前裁剪框有效
        changed = self._ensure_crop_center_inside_quad()
        if not self._is_crop_inside_perspective_quad():
            changed = self._auto_scale_crop_to_quad() or changed
    
    # 3. 通知变更
    if changed:
        self._crop_state.clamp()
        self._emit_crop_changed()
```

**基准裁剪框机制：**
- 交互开始时快照当前裁剪框状态
- 交互期间始终参考原始尺寸计算缩放
- 确保透视减小时裁剪框可以恢复原始大小

### GPU实现（着色器层面）

```glsl
// 来自 gl_image_viewer.frag
vec2 apply_inverse_perspective(vec2 uv) {
    // 将 [0,1] UV 映射到 [-1,1] NDC 空间
    vec2 centered = uv * 2.0 - 1.0;
    
    // 应用透视矩阵
    vec3 warped = uPerspectiveMatrix * vec3(centered, 1.0);
    float denom = warped.z;
    if (abs(denom) < 1e-5) {
        denom = (denom >= 0.0) ? 1e-5 : -1e-5;
    }
    
    // 透视除法 + 映射回 [0,1]
    vec2 restored = warped.xy / denom;
    return restored * 0.5 + 0.5;
}

void main() {
    // 先应用裁剪
    if (uv_corrected.x < crop_min_x || uv_corrected.x > crop_max_x ||
        uv_corrected.y < crop_min_y || uv_corrected.y > crop_max_y) {
        discard;
    }
    
    // 再应用透视逆变换
    vec2 uv_original = apply_inverse_perspective(uv_corrected);
    if (uv_original.x < 0.0 || uv_original.x > 1.0 ||
        uv_original.y < 0.0 || uv_original.y > 1.0) {
        discard;
    }
    
    // 采样纹理
    vec4 texel = texture(uTex, uv_original);
}
```

---

## 二、拖动边缘压力感应算法

### 适用场景
- 用户拖动裁剪框的边缘或角点
- 裁剪框边缘接近屏幕边界（< 48px）时自动触发

### 核心实现位置

**主要文件：**
- `src/iPhoto/gui/ui/widgets/gl_crop_controller.py` - `_apply_edge_push_auto_zoom()`
- `src/iPhoto/gui/ui/widgets/gl_crop_utils.py` - 缓动函数

### 算法原理

当裁剪框边缘被拖向屏幕边界时，系统会逐渐缩小视图并向相反方向平移。

#### 1. 压力检测

```python
def _apply_edge_push_auto_zoom(self, delta_view: QPointF) -> None:
    """当手柄推向视口边界时自动缩小和平移"""
    
    # 1. 获取裁剪框在设备像素中的位置
    crop_rect = self.current_crop_rect_pixels()
    vw, vh = self._transform_controller._get_view_dimensions_device_px()
    
    dpr = self._transform_controller._get_dpr()
    threshold = max(1.0, self._crop_edge_threshold * dpr)  # 48px × DPR
    
    # 2. 计算各边到屏幕边界的距离
    left_margin = float(crop_rect["left"])
    right_margin = max(0.0, vw - float(crop_rect["right"]))
    top_margin = float(crop_rect["top"])
    bottom_margin = max(0.0, vh - float(crop_rect["bottom"]))
    
    pressure = 0.0
    offset_x = 0.0
    offset_y = 0.0
    
    # 3. 根据拖动方向和距离计算压力
    # 左边向外推（delta < 0）且距离边界 < threshold
    if handle in (CropHandle.LEFT, CropHandle.TOP_LEFT, CropHandle.BOTTOM_LEFT):
        if delta_device.x() < 0.0 and left_margin < threshold:
            p = (threshold - left_margin) / threshold
            pressure = max(pressure, p)
            offset_x = max(offset_x, -float(delta_image.x()) * p)
    
    # 右边、上边、下边使用相同的逻辑...
```

**压力计算公式：**
```
pressure = (threshold - margin) / threshold, if margin < threshold

距离越近，压力越大：
- margin = 0px  → pressure = 1.0（最大压力）
- margin = 24px → pressure = 0.5
- margin = 48px → pressure = 0.0（无压力）
```

#### 2. 缩放和平移

```python
# 应用缓动
eased_pressure = ease_in_quad(min(1.0, pressure))  # t²

# 计算新缩放
shrink_strength = 0.05  # 单次最大缩小 5%
new_scale_raw = view_scale * (1.0 - shrink_strength * eased_pressure)
new_scale = max(min_scale, min(max_scale, new_scale_raw))

# 围绕裁剪框中心缩放
crop_center_view = self._transform_controller.convert_image_to_viewport(...)
target_zoom = new_scale / base_scale_safe
self._transform_controller.set_zoom(target_zoom, anchor=crop_center_view)

# 计算反向平移
pan_gain = 0.75 + 0.25 * eased_pressure  # [0.75, 1.0]
offset_delta = QPointF(offset_x * pan_gain, offset_y * pan_gain)

# 应用平移
current_center = self._transform_controller.get_image_center_pixels()
target_center = current_center + offset_delta
clamped_center = self._clamp_image_center_to_crop(target_center, effective_scale)
self._transform_controller.apply_image_center_pixels(clamped_center, effective_scale)
```

**关键特点：**
- 增量调整：每次最多缩小 5%
- 缓动函数：ease_in_quad 使初期响应柔和，后期响应强烈
- 平移增益：压力越大，平移幅度越大（0.75 ~ 1.0）
- 反向平移：与拖动方向相反，模拟"推"的反作用力

#### 3. 完整流程示例

**场景：用户向左拖动左边缘，距离屏幕左边界 20px**

```
1. 压力计算：
   pressure = (48 - 20) / 48 ≈ 0.583
   
2. 缓动：
   eased_pressure = 0.583² ≈ 0.340
   
3. 缩放：
   new_scale = current_scale × (1 - 0.05 × 0.340)
             = current_scale × 0.983  # 缩小约 1.7%
   
4. 平移增益：
   pan_gain = 0.75 + 0.25 × 0.340 = 0.835
   
5. 平移偏移：
   delta_x = -10px（向左）
   offset_x = -(-10) × 0.583 = +5.83px
   final_offset = 5.83 × 0.835 ≈ 4.87px（向右）
   
6. 结果：
   - 视图缩小 1.7%
   - 图像向右平移 4.87px
   - 用户感觉"推着图像走"
```

---

## 三、核心差异对比

### 1. 触发机制

| 算法类型 | 触发条件 | 触发频率 |
|---------|---------|---------|
| **透视校正** | 透视滑块值变化 | 实时（每次参数更新） |
| **边缘压力** | 拖动边缘 + 距离边界 < 48px | 条件触发 |

### 2. 调整对象

| 算法类型 | 裁剪框 | 视图变换 |
|---------|-------|---------|
| **透视校正** | 动态调整尺寸和位置 | 不变（仅自动居中） |
| **边缘压力** | 不变 | 动态调整缩放和平移 |

**关键区别：**
- 透视算法**主动调整裁剪框**以适应几何约束
- 边缘算法**保持裁剪框不变**，只调整视图

### 3. 计算方式

| 算法类型 | 计算方法 | 复杂度 |
|---------|---------|-------|
| **透视校正** | 射线投射 + 几何求解 | O(n)，n=角点数量 |
| **边缘压力** | 距离检测 + 增量调整 | O(1) |

**透视算法：**
```python
scale = calculate_min_zoom_to_fit(rect, quad)
# 精确计算，一次到位
```

**边缘算法：**
```python
pressure = (threshold - margin) / threshold
new_scale = old_scale * (1.0 - 0.05 * ease_in_quad(pressure))
# 增量调整，多次累积
```

### 4. 用户体验

| 算法类型 | 响应特性 | 用户感受 |
|---------|---------|---------|
| **透视校正** | 瞬时响应，精确计算 | "裁剪框自动适配" |
| **边缘压力** | 渐进响应，累积效果 | "推着图像走" |

### 5. GPU参与

| 算法类型 | GPU角色 |
|---------|--------|
| **透视校正** | 着色器中应用透视逆变换 |
| **边缘压力** | 不参与（仅CPU视图变换） |

### 6. 可恢复性

| 算法类型 | 是否可恢复 |
|---------|-----------|
| **透视校正** | 是（基准裁剪框机制） |
| **边缘压力** | 否（单向累积） |

---

## 四、设计哲学

### 透视校正算法

**目标：几何正确性**
- 确保透视变换后裁剪框始终有效
- 自动计算最优裁剪尺寸
- 支持可恢复编辑

**设计原则：**
- 数学精确：射线投射计算严格的最小缩放
- 可预测：给定透视参数，裁剪框尺寸是确定的
- 可恢复：透视减小时，裁剪框恢复原始尺寸

### 边缘压力算法

**目标：交互流畅性**
- 让用户感受到"推"的物理反馈
- 避免裁剪框卡在边界
- 提供渐进式的视图调整

**设计原则：**
- 渐进响应：增量调整，避免跳变
- 物理隐喻：模拟"推开门"的感觉
- 阻力反馈：压力越大，阻力越大

---

## 五、总结

### 核心差异表

| 维度 | 透视校正算法 | 边缘压力算法 |
|-----|------------|------------|
| **触发条件** | 透视滑块变化 | 拖动边缘 + 接近边界 |
| **调整对象** | 裁剪框尺寸和位置 | 视图变换（缩放+平移） |
| **计算方式** | 射线投射 + 几何求解 | 距离检测 + 增量调整 |
| **响应特性** | 瞬时、精确 | 渐进、累积 |
| **用户感受** | 自动智能适配 | 物理推动反馈 |
| **GPU参与** | 是（着色器透视逆变换） | 否（仅CPU视图变换） |
| **数学约束** | 裁剪框 ⊆ 投影四边形 | 图像 ⊇ 裁剪框 |
| **可恢复性** | 是（基准裁剪框机制） | 否（单向累积） |

### 算法协同

两种算法在实际使用中是**独立但互补**的：

1. **透视调整时：**仅透视算法生效，裁剪框自动缩小
2. **拖动裁剪框时：**直接调整边界，接近边界时边缘算法触发
3. **组合使用：**透视校正 → 拖动扩大 → 边缘压力 → 透视归零恢复

### 设计亮点

**透视算法：**
- ✅ 数学严谨，无黑边保证
- ✅ 基准裁剪框机制，支持可恢复编辑
- ✅ GPU加速的透视逆变换

**边缘算法：**
- ✅ 物理感强，交互自然
- ✅ 渐进式响应，避免跳变
- ✅ 平移增益设计，压力感知精准

---

**文档版本**: 2.0  
**最后更新**: 2025-11-22  
**基于源码**: `src/iPhoto/gui/ui/widgets/` (gl_crop_controller.py, perspective_math.py, gl_renderer.py, gl_image_viewer.frag)

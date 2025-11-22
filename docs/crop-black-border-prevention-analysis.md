# iPhotos 裁剪框黑边防止机制详解

## 概述 (Overview)

本文档详细分析 iPhotos 项目中**如何保证裁剪框内没有黑边**的完整机制。该机制涉及三个坐标系统、几何验证算法和交互策略的协同工作。

## 核心问题 (Core Problem)

当用户对图片应用**透视变换**（perspective transformation）或**旋转**（rotation）时，原始矩形图像在投影空间中会变成一个**凸四边形**。如果裁剪框超出这个有效区域，最终渲染的图像会包含黑色边缘（黑边），这是不可接受的。

## 三坐标系架构 (Three-Coordinate-System Architecture)

防黑边的核心在于理解和正确使用三个坐标系统：

### A. 原始纹理空间 (Texture Space)
- **定义**: 图片文件的原始像素空间
- **范围**: `[0, 0]` 到 `[W_src, H_src]`（像素单位）
- **作用**: 透视变换的**输入源**
- **示例**: 1920×1080 图片的纹理坐标从 `(0, 0)` 到 `(1920, 1080)`

### B. 投影空间 (Projected/Distorted Space) — **核心计算空间**
- **定义**: 应用透视变换矩阵后的二维空间
- **形态**: 原始矩形边界变为**凸四边形** `Q_valid`
- **裁剪框**: 始终保持为**轴对齐矩形** (AABB) `R_crop`
- **坐标范围**: 归一化为 `[0, 1]` 区间
- **关键特性**: **所有防黑边验证必须在此空间进行**

### C. 视口/屏幕空间 (Viewport Space)
- **定义**: 最终渲染在屏幕上的像素坐标
- **作用**: **仅用于**处理用户交互（鼠标点击、拖拽）
- **变换要求**: 必须**逆变换**回投影空间才能进行逻辑计算

## 核心验证算法 (Core Validation Algorithms)

### 1. 点在凸多边形内判定 (`point_in_convex_polygon`)

**文件位置**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

**算法原理**: 使用叉积判断点相对于多边形所有边的方向一致性

```python
def point_in_convex_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """判断点是否在凸多边形内部"""
    if len(polygon) < 3:
        return False
    last_sign = 0.0
    for i in range(len(polygon)):
        a = polygon[i]
        b = polygon[(i + 1) % len(polygon)]
        orient = _point_orientation(a, b, point)
        if abs(orient) <= 1e-6:
            continue
        sign = 1.0 if orient > 0.0 else -1.0
        if last_sign == 0.0:
            last_sign = sign
        elif sign != last_sign:
            return False  # 方向不一致，点在外部
    return True
```

**工作原理**:
1. 遍历多边形的每条边
2. 计算点相对于边的方向（通过叉积）
3. 如果所有方向一致，则点在内部；否则在外部

### 2. 矩形完全包含于四边形内判定 (`rect_inside_quad`)

**文件位置**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

```python
def rect_inside_quad(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> bool:
    """判断矩形是否完全在四边形内部"""
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    return all(point_in_convex_polygon(corner, quad) for corner in corners)
```

**工作原理**:
- 检查矩形的**所有四个角点**是否都在四边形内
- **只有当四个角点全部满足条件**，才认为矩形完全包含
- 这是防黑边的**核心判定函数**

### 3. 最小缩放计算 (`calculate_min_zoom_to_fit`)

**文件位置**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

```python
def calculate_min_zoom_to_fit(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> float:
    """计算使矩形完全适配四边形所需的最小缩放系数"""
    cx, cy = rect.center
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    max_scale = 1.0
    for corner in corners:
        direction = (corner[0] - cx, corner[1] - cy)
        if abs(direction[0]) <= 1e-9 and abs(direction[1]) <= 1e-9:
            continue
        hit = _ray_polygon_hit((cx, cy), direction, quad)
        if hit is None or hit <= 1e-6:
            continue
        if hit >= 1.0:
            continue
        scale = 1.0 / max(hit, 1e-6)
        if scale > max_scale:
            max_scale = scale
    return max_scale
```

**工作原理**:
1. 从矩形中心向每个角点发射射线
2. 计算射线与四边形边界的交点
3. 如果交点在角点之前，说明需要缩小
4. 返回所需的最大缩放系数

## 防黑边策略实现 (Black Border Prevention Strategies)

### 策略 1: 实时验证与回退 (`ensure_valid_or_revert`)

**文件位置**: `src/iPhoto/gui/ui/widgets/gl_crop/model.py`

```python
def ensure_valid_or_revert(
    self,
    snapshot: tuple[float, float, float, float],
    *,
    allow_shrink: bool,
) -> bool:
    """保持裁剪框在有效区域内，否则恢复快照"""
    if self.is_crop_inside_quad():
        return True  # 已经在内部，无需操作
    if allow_shrink and self.auto_scale_crop_to_quad():
        return True  # 自动缩小以适配
    self.restore_snapshot(snapshot)
    return False  # 恢复到安全状态
```

**应用场景**:
- 用户拖拽裁剪框边缘时
- 用户调整透视参数时
- 用户旋转或翻转图像时

**工作流程**:
1. **验证**: 检查当前裁剪框是否完全在有效四边形内
2. **自动修正**: 如果允许，自动缩小裁剪框以适配
3. **回退**: 如果无法修正，恢复到操作前的安全状态

### 策略 2: 边缘拖拽约束 (`ResizeStrategy`)

**文件位置**: `src/iPhoto/gui/ui/widgets/gl_crop/strategies/resize_strategy.py`

```python
def on_drag(self, delta_view: QPointF) -> None:
    """处理边缘拖拽，确保不超出边界"""
    # 1. 创建操作前快照
    snapshot = self._model.create_snapshot()
    
    # 2. 计算新的裁剪框位置
    # （应用拖拽增量，约束在原始图像边界内）
    
    # 3. 验证新状态，失败则回退
    if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
        return  # 操作被拒绝，保持原状态
    
    # 4. 通知UI更新
    self._on_crop_changed()
```

**关键约束**:
```python
# 确保不超出原始图像边界
new_left = max(new_left, img_bounds_world["left"])
new_right = min(new_right, img_bounds_world["right"])
new_top = min(new_top, img_bounds_world["top"])
new_bottom = max(new_bottom, img_bounds_world["bottom"])
```

### 策略 3: 透视变换时的基线适配 (`apply_baseline_perspective_fit`)

**文件位置**: `src/iPhoto/gui/ui/widgets/gl_crop/model.py`

```python
def apply_baseline_perspective_fit(self) -> bool:
    """将基线裁剪框适配到当前透视四边形"""
    if self._baseline_crop_state is None:
        return False
    
    # 1. 获取基线状态
    base_cx, base_cy, base_width, base_height = self._baseline_crop_state
    
    # 2. 检查中心点是否在新四边形内
    center = (float(base_cx), float(base_cy))
    if not point_in_convex_polygon(center, quad):
        # 中心点在外部，使用四边形质心作为新中心
        centroid = quad_centroid(quad)
        center = (max(0.0, min(1.0, float(centroid[0]))),
                  max(0.0, min(1.0, float(centroid[1]))))
    
    # 3. 计算适配缩放
    rect = NormalisedRect(
        center[0] - half_w,
        center[1] - half_h,
        center[0] + half_w,
        center[1] + half_h,
    )
    scale = calculate_min_zoom_to_fit(rect, quad)
    
    # 4. 应用缩放
    new_width = max(self._crop_state.min_width, float(base_width) / scale)
    new_height = max(self._crop_state.min_height, float(base_height) / scale)
    
    # 5. 更新状态
    self._crop_state.width = min(1.0, new_width)
    self._crop_state.height = min(1.0, new_height)
    self._crop_state.cx = center[0]
    self._crop_state.cy = center[1]
    self._crop_state.clamp()
    
    return True
```

**工作原理**:
1. 保存用户调整透视前的裁剪框状态（基线）
2. 当透视参数变化时，计算新的有效四边形
3. 如果基线裁剪框超出新四边形，自动缩小并重新定位
4. 保证透视调整过程中始终无黑边

## 交互流程示例 (Interaction Flow Examples)

### 场景 1: 用户拖拽裁剪框右边缘

```
1. 用户按下鼠标 → 创建快照 snapshot = (cx, cy, w, h)
2. 用户拖动 → 计算新的右边界位置 new_right
3. 约束检查:
   - new_right <= img_bounds["right"] ✓
   - rect_inside_quad(new_rect, perspective_quad) ?
     - ✓ 接受新状态，更新UI
     - ✗ restore_snapshot(snapshot)，拒绝操作
4. 用户释放鼠标 → 完成操作
```

### 场景 2: 用户调整透视垂直参数

```
1. 用户移动滑块 → vertical = 0.5
2. 重新计算投影四边形:
   - matrix = build_perspective_matrix(vertical, horizontal, ...)
   - quad = compute_projected_quad(matrix)
3. 应用基线适配:
   - 检查当前裁剪框是否在新四边形内
   - 如果不在，计算 scale = calculate_min_zoom_to_fit(...)
   - 缩小裁剪框: width /= scale, height /= scale
4. 验证并更新UI
```

### 场景 3: 用户旋转图像 90 度

```
1. 用户点击旋转按钮 → rotate_steps = 1
2. 更新透视矩阵（包含旋转）
3. 计算新的投影四边形
4. 由于旋转会改变有效区域，自动触发基线适配
5. 裁剪框被自动调整以避免黑边
6. UI 平滑过渡到新状态
```

## 关键代码文件索引 (Key Files Index)

| 文件路径 | 职责 | 关键函数 |
|---------|------|---------|
| `src/iPhoto/gui/ui/widgets/perspective_math.py` | 核心几何算法 | `point_in_convex_polygon`<br>`rect_inside_quad`<br>`calculate_min_zoom_to_fit`<br>`compute_projected_quad` |
| `src/iPhoto/gui/ui/widgets/gl_crop/model.py` | 裁剪会话模型 | `is_crop_inside_quad`<br>`ensure_valid_or_revert`<br>`apply_baseline_perspective_fit`<br>`auto_scale_crop_to_quad` |
| `src/iPhoto/gui/ui/widgets/gl_crop/utils.py` | 裁剪框状态 | `CropBoxState.clamp`<br>`CropBoxState.drag_edge_pixels` |
| `src/iPhoto/gui/ui/widgets/gl_crop/strategies/resize_strategy.py` | 边缘拖拽策略 | `ResizeStrategy.on_drag` |
| `src/iPhoto/gui/ui/widgets/gl_crop/strategies/pan_strategy.py` | 平移策略 | `PanStrategy.on_drag` |

## 测试覆盖 (Test Coverage)

项目包含完整的单元测试以确保防黑边机制的正确性：

| 测试文件 | 测试内容 |
|---------|---------|
| `tests/test_gl_crop_model.py` | 裁剪模型核心功能测试 |
| `tests/test_crop_box_state.py` | 裁剪框状态管理测试 |
| `tests/test_gl_image_viewer_crop_logic.py` | 裁剪逻辑集成测试 |
| `tests/test_gl_crop_hit_tester.py` | 点击检测测试 |

关键测试用例：
```python
def test_is_crop_inside_quad_initially(model):
    """测试裁剪框初始状态在单位四边形内"""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    assert model.is_crop_inside_quad()

def test_ensure_valid_or_revert_reverts_invalid_crop(model):
    """测试无效裁剪框会被自动回退"""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    snapshot = model.create_snapshot()
    
    # 制造无效状态
    crop_state = model.get_crop_state()
    crop_state.cx = -0.5  # 移到边界外
    crop_state.cy = -0.5
    
    # 应该回退到快照
    result = model.ensure_valid_or_revert(snapshot, allow_shrink=False)
    assert not result  # 操作失败
    # 状态已恢复到安全值
```

## 设计优势 (Design Advantages)

### 1. 坐标系分离
- 清晰的职责划分：屏幕交互 vs 逻辑计算 vs GPU渲染
- 避免坐标混用导致的bug
- 易于维护和扩展

### 2. 快照与回退机制
- 所有可能失败的操作都先创建快照
- 验证失败时立即回退到安全状态
- 用户体验流畅，无意外行为

### 3. 分层验证
- 边界约束（原始图像范围）
- 几何验证（投影四边形包含性）
- 自动修正（缩放适配）
- 回退保护（恢复快照）

### 4. 性能优化
- 几何计算使用归一化坐标（避免浮点精度问题）
- 快速叉积算法（O(n) 复杂度）
- 避免不必要的矩阵运算

## 总结 (Summary)

iPhotos 的防黑边机制通过以下核心要素保证裁剪质量：

1. **三坐标系架构**: 明确区分纹理空间、投影空间和视口空间
2. **几何验证算法**: `rect_inside_quad` 确保裁剪框完全在有效区域内
3. **交互策略**: 拖拽和透视调整时的实时验证与回退
4. **自动适配**: 透视变换时自动缩放裁剪框以避免黑边
5. **分层防护**: 多层验证机制保证任何情况下都不会出现黑边

这套机制在 `perspective_math.py` 提供的核心算法和 `gl_crop/model.py` 的状态管理基础上，通过策略模式实现了健壮、流畅的用户交互体验。

## 参考文档 (References)

- `README.md` - 项目总览和坐标系定义
- `AGENT.md` - OpenGL开发规范和坐标系详细说明
- `demo/crop_final.py` - 裁剪功能演示代码
- `demo/perspective.py` - 透视变换演示代码

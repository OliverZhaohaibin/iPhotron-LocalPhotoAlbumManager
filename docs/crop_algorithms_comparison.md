# 裁剪框免黑边算法对比分析

## 背景

在图片编辑中，当裁剪步骤(crop step)不为0时，存在两种不同的免黑边算法：

1. **旋转自动适应裁剪框算法** - 用于 straighten、vertical 和 horizontal 旋转时
2. **拖动裁剪框算法** - 用于直接拖动裁剪框边缘时

本文档详细分析这两种算法的实现机制和关键差异。

---

## 一、旋转自动适应裁剪框算法

### 适用场景
- Straighten（微调旋转，±45°）
- Vertical Perspective（垂直透视校正）
- Horizontal Perspective（水平透视校正）
- 90° 快速旋转

### 核心实现（来自 `demo/rotate.py`）

#### 1. 算法原理

当用户调整旋转角度时，裁剪框在屏幕上保持固定，但图像需要旋转。为了保证旋转后的图像完全覆盖裁剪框（无黑边），系统会自动计算所需的最小缩放系数。

```python
# 关键代码：_update_mvp_matrix() 方法
def _update_mvp_matrix(self) -> None:
    # 计算总旋转角度 = 基准旋转(90°步进) + 滑块微调(-45° ~ +45°)
    total_deg = (self.base_rotation_idx * -90.0) + self.angle_deg
    theta = math.radians(total_deg)
    c = math.cos(theta)
    s = math.sin(theta)
    
    # 获取裁剪框的四个角点（屏幕坐标系）
    corners = [
        (-half_Wf, -half_Hf),  # 左下
        ( half_Wf, -half_Hf),  # 右下
        ( half_Wf,  half_Hf),  # 右上
        (-half_Wf,  half_Hf),  # 左上
    ]
    
    # 核心：逆旋转框角到图像坐标系，计算所需的最小缩放
    S_min = 0.0
    for xf, yf in corners:
        # 逆旋转矩阵 R(-θ) = [[cos, sin], [-sin, cos]]
        x_prime =  xf * c + yf * s
        y_prime = -xf * s + yf * c
        
        # 确保所有角点都在图像范围内 [-w_img/2, w_img/2] × [-h_img/2, h_img/2]
        S_corner = max(
            2.0 * abs(x_prime) / w_img,
            2.0 * abs(y_prime) / h_img,
        )
        if S_corner > S_min:
            S_min = S_corner
    
    # S_min 就是保证无黑边的最小缩放系数
    S = S_min
```

#### 2. 算法特点

**固定裁剪框，动态缩放图像**
- 裁剪框尺寸和位置始终固定
- 通过逆旋转计算图像需要的缩放量
- 缩放是全局统一的（uniform scaling）

**精确的数学计算**
- 将裁剪框的四个角点逆向旋转到图像坐标系
- 计算使所有角点落入图像矩形所需的最小缩放比
- 公式：`S = max(2·|x'|/w_img, 2·|y'|/h_img)` for all corners

**适用于透视变换**（来自 `demo/perspective.py`）

对于透视校正（垂直/水平），使用类似的自动缩放补偿：

```python
def _update_scale(self):
    # 透视变换的缩放补偿
    # zx = 1 + uHorz * y, zy = 1 + uVert * x
    max_shear = max(abs(self.uHorz), abs(self.uVert))
    if max_shear < 1e-4:
        self.uScale = 1.0
    else:
        max_shear = min(max_shear, 0.95)
        # 关键公式：uScale = 1 / (1 - max_shear)
        self.uScale = 1.0 / (1.0 - max_shear)
```

**透视校正的特点：**
- 使用 keystone 变形（梯形校正）
- 根据透视系数自动计算放大倍数
- 确保变形后的图像仍然完全覆盖裁剪框

---

## 二、拖动裁剪框算法

### 适用场景
- 用户拖动裁剪框的边缘或角点
- 裁剪框接近屏幕边界时

### 核心实现（来自 `demo/crop_final.py`）

#### 1. 算法原理

当用户拖动裁剪框边缘时，如果框接近屏幕边界，系统会：
1. 检测"压力"（框边缘到屏幕边缘的距离）
2. 按压力比例缩小图像
3. 同时向压力相反方向平移图像和裁剪框
4. 保持图像始终覆盖裁剪框

```python
def _auto_shrink_on_drag(self, dpx: Tuple[float, float]):
    """拖边贴窗时：按压力缩小，并沿压力相反方向叠加平移；保持无黑边。"""
    
    # 1. 获取裁剪框四边在屏幕上的位置
    L_px, R_px, T_px, B_px = self._crop_edges_screen()
    vw, vh = self.cam.vw, self.cam.vh
    thr = float(self._edge_threshold_px)  # 阈值，默认 48px
    
    pressure = 0.0
    d_offset = Vec2(0, 0)  # 额外的世界坐标平移
    d_world = self.cam.screen_vec_to_world_vec(dpx)
    
    # 2. 计算各边的压力
    # 左边向外推（dx<0）：向右平移（+x），带入左侧内容
    if self._drag_handle in (Handle.L, Handle.LT, Handle.LB) and dpx[0] < 0:
        margin = L_px
        if margin < thr:
            p = (thr - margin) / thr  # 压力系数 [0, 1]
            pressure = max(pressure, p)
            d_offset.x = max(d_offset.x, d_world.x * -p)
    
    # 右边向外推（dx>0）：向左平移（-x）
    if self._drag_handle in (Handle.R, Handle.RT, Handle.RB) and dpx[0] > 0:
        margin = vw - R_px
        if margin < thr:
            p = (thr - margin) / thr
            pressure = max(pressure, p)
            d_offset.x = min(d_offset.x, d_world.x * -p)
    
    # 上边和下边同理...
    
    if pressure <= 0.0:
        return
    
    # 3. 应用缓动函数，使压力效果更平滑
    eased_pressure = ease_in_quad(min(1.0, pressure))
    
    # 4. 计算新的缩放比例
    k_max = 0.05  # 单次事件最大缩小比例（5%）
    factor = 1.0 - k_max * eased_pressure
    new_scale_raw = self.img_scale * factor
    
    # 5. 应用动态最小缩放限制（确保图像始终覆盖裁剪框）
    dyn_min = self._dynamic_min_scale_to_cover_crop()
    new_scale = max(dyn_min, min(max_allowed, new_scale_raw))
    
    # 6. 缩放围绕裁剪框中心进行
    anchor = Vec2(self.crop.rect.cx, self.crop.rect.cy)
    s = new_scale / max(1e-12, self.img_scale)
    new_offset = Vec2(
        anchor.x + (self.img_offset.x - anchor.x) * s,
        anchor.y + (self.img_offset.y - anchor.y) * s
    )
    
    # 7. 叠加"反压力方向"的平移
    pan_gain = 0.75 + 0.25 * eased_pressure
    final_d_offset = d_offset * pan_gain
    new_offset = new_offset + final_d_offset
    
    # 8. 关键：裁剪框同步平移，保持相对位置不变
    self.crop.rect.cx += final_d_offset.x
    self.crop.rect.cy += final_d_offset.y
    
    # 9. 夹紧，确保无黑边
    new_offset = self._clamp_offset_to_cover_crop(new_offset, new_scale)
    
    self.img_scale = new_scale
    self.img_offset = new_offset
```

#### 2. 算法特点

**动态裁剪框，渐进式缩放**
- 裁剪框位置可以移动
- 图像逐步缩小（非一次性计算）
- 缩放速率基于用户拖动速度

**压力感应系统**
- 检测裁剪框到屏幕边界的距离（margin）
- 距离越小，"压力"越大
- 压力转换公式：`p = (threshold - margin) / threshold`

**缓动和增益**
- 使用 `ease_in_quad(t) = t²` 缓动函数
- 单次最大缩小比例限制（5%）
- 平移增益：`pan_gain = 0.75 + 0.25 * eased_pressure`

**裁剪框同步移动**
```python
# 关键点：裁剪框跟随平移
self.crop.rect.cx += final_d_offset.x
self.crop.rect.cy += final_d_offset.y
```

**动态最小缩放计算**
```python
def _dynamic_min_scale_to_cover_crop(self) -> float:
    # 确保图像始终能覆盖裁剪框
    c = self.crop.rect
    return max(c.w / self.r.img_w, c.h / self.r.img_h)
```

---

## 三、核心差异对比

### 1. 触发时机

| 算法类型 | 触发条件 |
|---------|---------|
| **旋转自动适应** | 用户调整旋转/透视滑块 |
| **拖动裁剪框** | 用户拖动裁剪框边缘且接近屏幕边界 |

### 2. 裁剪框行为

| 算法类型 | 裁剪框状态 |
|---------|-----------|
| **旋转自动适应** | 完全固定，尺寸和位置都不变 |
| **拖动裁剪框** | 可移动，随着压力反方向平移 |

### 3. 缩放计算方式

| 算法类型 | 计算方法 | 特点 |
|---------|---------|------|
| **旋转自动适应** | 几何精确计算：逆旋转四个角点，找最大值 | 一次性计算，精确到位 |
| **拖动裁剪框** | 压力感应 + 增量调整 | 渐进式缩放，多次小步调整 |

**旋转算法：**
```python
S = max(2·|x'|/w, 2·|y'|/h) for all corners
```

**拖动算法：**
```python
pressure = (threshold - margin) / threshold
eased_pressure = pressure²
new_scale = old_scale × (1 - 0.05 × eased_pressure)
```

### 4. 平移策略

| 算法类型 | 平移方式 |
|---------|---------|
| **旋转自动适应** | 图像围绕裁剪框中心缩放，不需要额外平移 |
| **拖动裁剪框** | 图像和裁剪框同时向压力相反方向平移 |

**拖动算法的平移逻辑：**
- 图像缩放围绕裁剪框中心
- 叠加反压力方向的平移偏移
- 裁剪框同步移动相同的偏移量
- 保持图像和裁剪框的相对关系

### 5. 用户体验

| 算法类型 | 用户感受 |
|---------|---------|
| **旋转自动适应** | 瞬时响应，图像立即放大到合适大小 |
| **拖动裁剪框** | 平滑过渡，边推边缩，有"弹性"的感觉 |

### 6. 缓动函数

| 算法类型 | 缓动应用 |
|---------|---------|
| **旋转自动适应** | 无缓动（直接计算结果） |
| **拖动裁剪框** | `ease_in_quad(t) = t²` 用于压力响应 |

### 7. 边界处理

| 算法类型 | 边界策略 |
|---------|---------|
| **旋转自动适应** | 确保角点在图像矩形内 |
| **拖动裁剪框** | 检测屏幕边界距离（阈值 48px） |

---

## 四、实现细节对比

### 旋转算法关键代码结构

```python
class GLImageWidget:
    def set_angle(self, deg: float):
        self.angle_deg = deg
        self.update()  # 触发 paintGL
    
    def paintGL(self):
        self._update_mvp_matrix()  # 重新计算缩放和变换矩阵
        # 绘制图像...
    
    def _update_mvp_matrix(self):
        # 1. 计算旋转矩阵参数
        # 2. 逆旋转裁剪框四角
        # 3. 找最小覆盖缩放
        # 4. 生成 MVP 矩阵
```

### 拖动算法关键代码结构

```python
class GLViewport:
    def mouseMoveEvent(self, ev):
        if self._drag_state == 1:  # 拖动裁剪框边缘
            dpx = (new_pos - old_pos)
            self.crop.drag_edge(handle, world_delta, bounds)
            self._auto_shrink_on_drag(dpx)  # 应用压力响应
    
    def _auto_shrink_on_drag(self, dpx):
        # 1. 检测屏幕边界压力
        # 2. 计算压力系数和方向
        # 3. 应用缓动函数
        # 4. 增量缩小图像
        # 5. 计算反向平移偏移
        # 6. 同步移动裁剪框
        # 7. 应用覆盖约束
```

---

## 五、算法选择的设计考量

### 为什么旋转使用固定裁剪框？

1. **用户意图明确**：旋转时，用户关注的是"在当前取景范围内，图像如何最优显示"
2. **数学可控**：旋转变换是线性的，可以精确计算所需缩放
3. **视觉稳定**：裁剪框保持不动，用户清楚地看到旋转效果

### 为什么拖动使用动态裁剪框？

1. **用户意图是移动框**：拖动操作本身就是在改变裁剪范围
2. **避免边界卡顿**：当框接近边界时，如果完全禁止移动会很别扭
3. **提供缓冲机制**：压力响应让用户可以"推着框走"，但有阻力
4. **内容不丢失**：通过缩小和平移，确保框内内容持续可见

### 设计哲学差异

| 方面 | 旋转算法 | 拖动算法 |
|-----|---------|---------|
| **设计目标** | 精确性 | 流畅性 |
| **用户控制** | 参数化（角度滑块） | 直接操作（拖动） |
| **反馈方式** | 即时计算 | 渐进响应 |
| **适用范围** | 全局变换 | 局部交互 |

---

## 六、数学推导

### 旋转算法推导

给定：
- 图像尺寸：`w_img × h_img`
- 裁剪框尺寸：`W_frame × H_frame`
- 旋转角度：`θ`

目标：找到最小缩放系数 `S`，使得旋转后的图像完全覆盖裁剪框。

**步骤：**

1. 裁剪框四个角点（屏幕坐标）：
   ```
   corners = [(-W/2, -H/2), (W/2, -H/2), (W/2, H/2), (-W/2, H/2)]
   ```

2. 逆旋转变换（将框角点转到图像坐标系）：
   ```
   R⁻¹(θ) = [[ cos(θ),  sin(θ)]
             [-sin(θ),  cos(θ)]]
   
   [x'] = R⁻¹(θ) · [xf]
   [y']            [yf]
   ```

3. 图像坐标系的边界：
   ```
   |x'| ≤ (w_img · S) / 2
   |y'| ≤ (h_img · S) / 2
   ```

4. 求解 S：
   ```
   S ≥ 2·|x'| / w_img  for all corners
   S ≥ 2·|y'| / h_img  for all corners
   
   S = max{2·|x'ᵢ|/w_img, 2·|y'ᵢ|/h_img | i = 1,2,3,4}
   ```

### 拖动算法推导

给定：
- 裁剪框边缘到屏幕边界的距离：`margin`
- 压力阈值：`threshold`（如 48px）
- 鼠标移动向量：`dpx`

目标：计算缩放因子和平移偏移。

**步骤：**

1. 压力计算：
   ```
   p = (threshold - margin) / threshold,  if margin < threshold
   p = 0,                                  otherwise
   ```

2. 缓动：
   ```
   p_eased = p²
   ```

3. 缩放因子：
   ```
   k_max = 0.05
   factor = 1 - k_max · p_eased
   S_new = S_old · factor
   ```

4. 平移增益：
   ```
   gain = 0.75 + 0.25 · p_eased
   ```

5. 平移偏移（世界坐标）：
   ```
   d_offset = -p · d_world  (反向)
   final_offset = gain · d_offset
   ```

6. 同步更新：
   ```
   image_offset += final_offset
   crop_center += final_offset
   ```

---

## 七、实际应用场景

### 场景 1：旋转校正歪斜照片

**用户操作：**
- 调整 straighten 滑块，图片从 -5° 旋转到 0°

**算法响应（旋转自动适应）：**
- 裁剪框保持固定
- 图像瞬时放大约 1.08 倍（`S = 1/cos(5°) ≈ 1.0038`）
- 用户看到图像"填满"裁剪框，无黑边

### 场景 2：透视校正建筑照片

**用户操作：**
- 调整 vertical perspective 滑块，修正垂直方向的梯形畸变

**算法响应（旋转自动适应）：**
- 裁剪框固定
- 根据透视系数自动放大：`S = 1/(1 - |perspective|)`
- 例如 perspective = 0.3 时，S = 1.43 倍

### 场景 3：拖动裁剪框到屏幕边缘

**用户操作：**
- 拖动裁剪框右边缘向右，直到接近屏幕右边界

**算法响应（拖动裁剪框）：**
- 当右边缘距屏幕 < 48px 时触发压力
- 图像逐步缩小（每次最多 5%）
- 图像和裁剪框同时向左平移
- 用户感觉像"推着有阻力的弹簧"

---

## 八、性能考量

### 旋转算法

**优点：**
- 计算量小：只需计算 4 个角点
- 一次性完成：不需要迭代
- 精确：数学上严格保证无黑边

**性能：**
- O(1) 复杂度
- 适合实时滑块调整（60fps）

### 拖动算法

**优点：**
- 平滑：渐进式调整避免突变
- 灵活：可以处理任意方向的拖动

**性能：**
- O(1) 复杂度（每次鼠标事件）
- 但需要多次调用才能达到目标状态
- 适合交互式操作（30-60fps）

**优化点：**
- 压力检测只在接近边界时触发
- 缩放限制在 5% 以内，避免剧烈变化

---

## 九、边界情况处理

### 旋转算法的边界情况

1. **极端旋转角度（接近 45°）**
   ```python
   S = max(2·|x'|/w, 2·|y'|/h) 
   # 当 θ = 45° 时，S ≈ √2 ≈ 1.414
   ```

2. **细长图片 + 大角度旋转**
   - S 可能很大（例如 2.0+）
   - 但算法保证数学正确性

3. **透视极限**
   ```python
   max_shear = min(max_shear, 0.95)  # 防止爆炸
   ```

### 拖动算法的边界情况

1. **图像太小无法覆盖裁剪框**
   ```python
   dyn_min = self._dynamic_min_scale_to_cover_crop()
   new_scale = max(dyn_min, new_scale_raw)
   ```

2. **多方向同时施压**
   ```python
   pressure = max(pressure_x, pressure_y)  # 取最大压力
   ```

3. **快速拖动**
   - 缓动函数 `p²` 防止过度反应
   - 平移增益限制在 `[0.75, 1.0]` 范围

---

## 十、总结

### 核心差异表

| 维度 | 旋转自动适应算法 | 拖动裁剪框算法 |
|-----|----------------|--------------|
| **触发条件** | 旋转/透视滑块变化 | 拖动边缘 + 接近屏幕边界 |
| **裁剪框行为** | 完全固定 | 可移动 |
| **缩放计算** | 几何精确，一次到位 | 压力感应，渐进式 |
| **平移策略** | 无（围绕中心缩放） | 反压力方向平移 |
| **用户体验** | 瞬时响应 | 平滑过渡 |
| **数学基础** | 逆变换 + 边界约束 | 压力函数 + 增量更新 |
| **缓动** | 无 | ease_in_quad |
| **适用场景** | 全局变换（旋转、透视） | 局部交互（框选） |

### 设计哲学

**旋转算法：精确性优先**
- 用户调整参数，系统计算最优解
- 数学保证，无黑边
- 适合"专业编辑模式"

**拖动算法：流畅性优先**
- 用户直接操作，系统智能辅助
- 压力感应，自然反馈
- 适合"直观交互模式"

### 实现建议

1. **保持算法独立**：两种算法服务不同的交互场景，不应混用
2. **清晰的状态管理**：明确当前是否处于"拖动模式"或"旋转模式"
3. **参数可调**：压力阈值、缩放速率等应该可配置
4. **性能监控**：确保两种算法都能保持 60fps

### 未来改进方向

1. **旋转算法**：支持任意形状的裁剪框（圆形、多边形）
2. **拖动算法**：自适应压力阈值（根据屏幕尺寸）
3. **统一框架**：将两种算法抽象为"覆盖约束求解器"的不同策略

---

## 附录：关键公式速查

### 旋转算法

```
S = max{2·|xᵢ'|/w, 2·|yᵢ'|/h | i ∈ corners}

其中：
[xᵢ'] = [[ cos(θ),  sin(θ)] · [xᵢ]
[yᵢ']    [-sin(θ),  cos(θ)]]   [yᵢ]
```

### 透视算法

```
S = 1 / (1 - max(|uHorz|, |uVert|))
```

### 拖动算法

```
p = (threshold - margin) / threshold
p_eased = p²
S_new = S_old × (1 - 0.05 × p_eased)
gain = 0.75 + 0.25 × p_eased
offset_final = gain × (-p × d_world)
```

### 动态最小缩放

```
S_min = max(W_crop / W_img, H_crop / H_img)
```

---

**文档版本**: 1.0  
**最后更新**: 2025-11-22  
**作者**: 基于 `demo/rotate.py`、`demo/crop_final.py` 和 `demo/perspective.py` 的代码分析

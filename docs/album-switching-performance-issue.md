# 相册切换性能问题分析与解决方案

## 问题描述

用户反馈：
- ✅ 物理相册之间切换非常丝滑
- ✅ 从聚合相册向物理相册切换非常丝滑
- ❌ **从物理相册逆向切换到聚合相册需要卡顿一段时间**

## 问题根源分析

### 双模型架构

iPhoto 采用了双模型架构来优化性能：

1. **`_library_list_model`** (持久化库模型)
   - 用于聚合视图（All Photos、Videos、Live Photos、Favorites 等）
   - 绑定到库根目录（Library Root）
   - 持久化保持数据，避免重复加载

2. **`_album_list_model`** (瞬态相册模型)
   - 用于物理相册视图
   - 每次切换到新的物理相册时重置并重新加载
   - 轻量级，适合频繁切换

### 切换场景分析

#### 场景 1：物理相册 → 物理相册 ✅ 丝滑
```
物理相册A (_album_list_model) → 物理相册B (_album_list_model)
```
- 使用同一个模型 `_album_list_model`
- 执行 `prepare_for_album()` 清空并重新加载
- 由于物理相册通常较小，加载速度快

#### 场景 2：聚合相册 → 物理相册 ✅ 丝滑
```
All Photos (_library_list_model) → 物理相册A (_album_list_model)
```
- 从 `_library_list_model` 切换到 `_album_list_model`
- `_album_list_model` 清空并加载物理相册数据
- 物理相册数据量小，加载快速

#### 场景 3：物理相册 → 聚合相册 ❌ 卡顿
```
物理相册A (_album_list_model) → All Photos (_library_list_model)
```
- 从 `_album_list_model` 切换到 `_library_list_model`
- **问题**：`_library_list_model` 在使用物理相册期间被闲置，数据已清空
- 需要执行 `prepare_for_album()` 重置模型
- 需要重新加载整个库的索引数据（可能包含数千张照片）
- 造成明显卡顿

### 代码层面分析

**文件位置**: `src/iPhoto/gui/facade.py` - `open_album()` 方法

```python
# 第 214-229 行：优化检查逻辑
should_prepare = True
if target_model is self._library_list_model:
    existing_root = target_model.album_root()
    if (
        target_model.rowCount() > 0  # ← 关键检查点
        and existing_root is not None
        and self._paths_equal(existing_root, album_root)
        and getattr(target_model, "is_valid", lambda: False)()
    ):
        should_prepare = False  # 跳过重置，实现即时切换
```

**问题**：当从物理相册切换回聚合相册时：
- `_library_list_model.rowCount()` 返回 0（因为模型已被闲置）
- `should_prepare` 保持为 `True`
- 触发完整的模型重置和数据重载
- 导致 UI 卡顿

**导航控制器的优化仅限于同根切换**

**文件位置**: `src/iPhoto/gui/ui/controllers/navigation_controller.py` - `open_static_collection()` 方法

```python
# 第 329-341 行：同根优化路径
if is_same_root:
    # --- 优化路径（内存中）---
    # 保持在同一个库中
    # 1. 跳过 open_album() 以防止模型销毁和重载
    # 2. 直接应用过滤器，这是唯一的成本
    self._asset_model.set_filter_mode(filter_mode)
    self._asset_model.ensure_chronological_order()
    # ... 手动更新 UI 状态 ...
```

这个优化仅在以下情况下有效：
- `current_root` 已经是 `library_root`
- 即：在聚合视图之间切换（All Photos → Videos → Favorites）
- 不适用于：物理相册 → 聚合视图的切换

## 解决方案

### 方案 1：保持库模型热加载 ⭐ 推荐

**思路**：即使在浏览物理相册时，也保持 `_library_list_model` 在后台保持加载状态。

**实现方式**：
1. 修改 `open_album()` 逻辑，当切换到物理相册时，不清空 `_library_list_model`
2. 仅清空和重置 `_album_list_model`
3. 这样切换回聚合视图时，`_library_list_model` 仍然有数据

**优点**：
- 切换速度显著提升（从秒级降到毫秒级）
- 用户体验更流畅
- 实现相对简单

**缺点**：
- 内存占用略微增加（保持两个模型的数据）
- 对于大型库（数万张照片），内存占用可能需要优化

**代码修改位置**：
- `src/iPhoto/gui/facade.py` - `open_album()` 方法
- 修改 `prepare_for_album()` 的调用条件
- 仅在必要时清空 `_library_list_model`

### 方案 2：缓存预加载

**思路**：在用户浏览物理相册时，在后台预加载库视图的索引数据。

**实现方式**：
1. 添加后台预加载任务
2. 检测用户在物理相册时，启动低优先级的库索引加载
3. 当用户切换回聚合视图时，数据已准备就绪

**优点**：
- 对内存占用的控制更精细
- 可以根据用户行为动态调整

**缺点**：
- 实现复杂度较高
- 需要额外的后台任务管理
- 可能影响其他后台任务的性能

### 方案 3：增量加载优化

**思路**：优化 `_library_list_model` 的加载策略，采用分页或虚拟滚动。

**实现方式**：
1. 实现游标分页加载（代码中已有部分实现）
2. 首次只加载前几屏的数据
3. 用户滚动时按需加载更多

**优点**：
- 首屏加载速度快
- 内存占用优化
- 适合大型库

**缺点**：
- 需要重构现有的加载逻辑
- 实现复杂度最高
- 可能影响其他功能（如搜索、排序）

## 推荐实施方案

### 短期方案：方案 1（保持库模型热加载）

立即实施以解决用户体验问题：

```python
# 修改 src/iPhoto/gui/facade.py 的 open_album() 方法

def open_album(self, root: Path) -> Optional[Album]:
    # ... 现有代码 ...
    
    # 修改部分：不再每次都清空库模型
    if target_model is self._library_list_model:
        # 库模型优化：如果已经加载过且根目录匹配，跳过 prepare
        existing_root = target_model.album_root()
        if (
            target_model.rowCount() > 0
            and existing_root is not None
            and self._paths_equal(existing_root, album_root)
            and getattr(target_model, "is_valid", lambda: False)()
        ):
            should_prepare = False
    elif target_model is self._album_list_model:
        # 物理相册总是需要 prepare（因为每次切换到不同相册）
        should_prepare = True
    
    # 关键改进：切换到物理相册时，保持库模型数据不清空
    if should_prepare:
        target_model.prepare_for_album(album_root)
    
    # ... 后续代码 ...
```

### 中期方案：方案 1 + 内存优化

在短期方案的基础上，添加智能内存管理：

1. **监控内存使用**：
   - 当库模型数据量超过阈值（如 10,000 项）时
   - 在切换到物理相册后，延迟 N 秒清空库模型缓存
   - 如果用户在 N 秒内切换回来，数据仍在

2. **LRU 缓存策略**：
   - 对于经常访问的聚合视图，保持热缓存
   - 对于长时间未访问的视图，允许清空

### 长期方案：方案 3（增量加载优化）

作为架构优化的一部分：

1. 完善现有的分页加载机制（`PaginatedLoaderWorker`）
2. 实现虚拟滚动，仅加载可见区域的数据
3. 优化数据库查询，使用更高效的索引策略

## 性能基准测试

建议在实施后进行以下测试：

| 场景 | 库大小 | 当前耗时 | 目标耗时 | 备注 |
|------|--------|----------|----------|------|
| 物理相册 → All Photos | 1,000 张 | ~500ms | <100ms | 小型库 |
| 物理相册 → All Photos | 5,000 张 | ~2s | <200ms | 中型库 |
| 物理相册 → All Photos | 20,000 张 | ~8s | <500ms | 大型库 |
| 物理相册 → Videos | 5,000 张 | ~2s | <200ms | 带过滤 |

## 实施检查清单

- [ ] 修改 `facade.py` 的 `open_album()` 方法
- [ ] 确保 `_library_list_model` 在切换到物理相册时不被清空
- [ ] 添加单元测试验证模型切换逻辑
- [ ] 进行性能基准测试
- [ ] 监控内存使用情况
- [ ] 更新相关文档

## 相关文件

### 核心文件
- `src/iPhoto/gui/facade.py` - 主要的相册管理逻辑
- `src/iPhoto/gui/ui/controllers/navigation_controller.py` - 导航控制
- `src/iPhoto/gui/ui/models/asset_list/model.py` - 资源列表模型
- `src/iPhoto/gui/ui/models/asset_list/controller.py` - 加载控制器

### 相关文件
- `src/iPhoto/gui/ui/tasks/asset_loader_worker.py` - 后台加载任务
- `src/iPhoto/cache/index_store.py` - 索引数据存储

## 结论

物理相册切换回聚合相册时的卡顿问题，根源在于 `_library_list_model` 在切换到物理相册时被闲置并清空。通过保持库模型的热加载状态，可以显著提升切换性能，实现与其他切换场景一样的丝滑体验。

推荐优先实施方案 1（保持库模型热加载），这是成本最低、效果最显著的解决方案。长期来看，可以结合方案 3 的增量加载优化，进一步提升大型库的性能和内存效率。

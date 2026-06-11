# Gallery 滚动性能 Phase 2 轻量 Tile 交接文档

> 状态：开发完成，待 Windows GPU、Linux Wayland 与真实大型图库验收  
> 日期：2026-06-11  
> 上游交接：`GALLERY_SCROLL_PERFORMANCE_PHASE2_HANDOFF.md`

## 1. 本阶段结论

本阶段在保留 visible-first、warm-second 与 micro-first 行为的前提下，将 SQL 查询
窗口中的宽 `AssetDTO` 替换为轻量 `GalleryTileDTO`：

- 查询窗口不再为每个 tile 常驻复制完整 row 到 `metadata` 字典。
- Gallery 使用独立 row-to-tile mapper，只 materialize typed 字段。
- `GalleryCollectionStore` 查询模式缓存 `GalleryTileDTO`；direct mode、乐观移动和
  其他兼容入口继续使用 `AssetDTO`。
- metadata 消费者通过 store 按需生成兼容视图；运行期间的 metadata 更新保存在
  稀疏 overlay 中，并同步更新 tile 的 typed 字段。
- micro thumbnail 暂时仍由 window worker 解码并随 tile 发布，避免重新出现高速
  滚动 placeholder。

公开 model roles、详情、播放、选择、上下文菜单和文件操作行为保持不变。

## 2. API 与投影变化

### `GalleryTileDTO`

新增可变、slots 化的轻量 DTO，并新增联合类型：

```python
GalleryAssetDTO = AssetDTO | GalleryTileDTO
```

`GalleryTileDTO` 不提供 `metadata` 属性。它保存 Gallery 绘制、详情入口、Live Photo、
位置和缩略图调度实际使用的 typed 字段，以及过渡期 `micro_thumbnail`。

### Gallery mapper 与 SQL 投影

- `gallery_row_to_tile()` 不保留输入 row，也不调用通用 `scan_row_to_dto()`。
- `GALLERY_WINDOW_COLUMNS` 删除：
  - `parent_album_path`
  - `original_rel_path`
  - `original_album_id`
  - `original_album_subpath`
- `mime` 因详情信息面板仍会读取而保留为 typed 字段；删除它会造成公开 UI 回退。
- 完整 metadata 与相机曝光字段仍不进入 Gallery window。

### Metadata 兼容视图

`GalleryCollectionStore.metadata_for_asset()` 为少数兼容消费者按需组合 typed 字段和
稀疏 overlay。`update_asset_metadata()` 对 query tile 不再创建常驻宽 DTO，但会更新
位置、GPS、尺寸、时长、codec 等可复用 typed 字段。

## 3. 本机性能采集

执行环境：

- macOS 26.5.1 arm64
- Qt backend：`offscreen`
- runtime label：`lightweight-tile-offscreen`
- 每场景 120 个 wheel event，batch size 8

| Rows | scroll P95 | paint P95 | frame interval P95 | 输入追平 | 最终 micro 发布 | micro 覆盖 | placeholder |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10k | 0.002ms | 1.505ms | 26.587ms | 0.081ms | 25.273ms | 100% | 0% |
| 100k | 0.002ms | 1.914ms | 27.839ms | 0.065ms | 26.387ms | 100% | 0% |
| 1M | 0.002ms | 1.774ms | 28.563ms | 0.073ms | 27.009ms | 100% | 0% |

所有场景均满足：

- visible micro 在 warm 阶段前发布；
- micro/full 覆盖率 `100%`；
- placeholder 覆盖率 `0%`；
- `ensure_row_loaded`、同步 thumbnail、同步 layout 与 `repaint` 违规均为 `0`。

offscreen frame interval 不能替代真实 GPU 平台验收。报告位于：

```text
/tmp/iphoto-gallery-scroll-performance
```

## 4. 验证记录

已通过：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/cache/test_index_store_features.py \
  tests/gui/viewmodels/test_gallery_window_loader.py \
  tests/gui/viewmodels/test_gallery_collection_store.py \
  tests/gui/viewmodels/test_gallery_list_model_adapter.py \
  tests/gui/viewmodels/test_detail_viewmodel.py -q
# 123 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/gui/viewmodels/test_gallery_viewmodel.py \
  tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py \
  tests/gui/coordinators/test_playback_coordinator.py \
  tests/ui/controllers/test_selection_controller.py \
  tests/ui/controllers/test_context_menu_cover.py \
  tests/ui/controllers/test_context_menu_operations.py \
  tests/ui/controllers/test_context_menu_export.py \
  tests/ui/controllers/test_preview_controller.py \
  tests/ui/widgets/test_filmstrip_view.py \
  tests/ui/widgets/test_filmstrip_performance.py \
  tests/performance/test_refactor_performance_baseline.py -q
# 153 passed, 2 skipped

QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/test_asset_grid_scroll.py \
  tests/ui/test_gallery_grid_view.py \
  tests/ui/test_media_selection_session.py -q
# 16 passed

QT_QPA_PLATFORM=offscreen IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
  IPHOTO_RUNTIME_LABEL=lightweight-tile-offscreen \
  .venv/bin/pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
# 3 passed

QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_aspect_ratio_constraint.py -q
# 27 passed

python3 -m compileall -q src/iPhoto tests/performance/test_gallery_scroll_qt_benchmark.py
git diff --check
# passed
```

默认 `pytest -q` 在约 36% 处再次于既有 `icons.py::load_icon` Qt 原生调用中发生
segmentation fault。隔离重跑触发文件 `tests/test_aspect_ratio_constraint.py` 为
`27 passed`。该问题与上游 Phase 2 交接记录一致。

已知 pytest 警告仍为：

```text
PytestConfigWarning: Unknown config option: env
```

## 5. 下一步交接

### 下一开发项

1. 将 window-ready 与 thumbnail-ready 更新按 `16ms` 帧合并。
2. 合并连续 row，并为 `dataChanged` 指定准确 roles。
3. 只刷新实际变化的 viewport 区域，避免 warm window 的宽范围重复绘制。
4. 为该 coordinator 增加每帧最多一次 update 的测试与性能事件。

### 后续 Phase 3

1. 为完整缩略图请求增加共享 viewport generation 与 visible/hot/warm 优先级。
2. stale 完整缩略图结果不再发布。
3. 完成 generation-aware 缩略图管线后，移除 `GalleryTileDTO.micro_thumbnail` 过渡
   字段和 window worker 中的 micro 解码。
4. 将缩略图内存缓存迁移到真实字节预算 LRU，并短期 pin visible pixmap。

### 待平台验收

1. Windows packaged runtime / GPU。
2. Linux Wayland 真实会话。
3. 100K+ 真实混合格式图库与并发扫描场景。

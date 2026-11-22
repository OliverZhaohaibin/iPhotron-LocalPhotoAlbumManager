# 平行项目 Gallery 性能审计与本地整合计划

- **审计日期** 2026-11-17，Europe/Berlin
- **上游基线** `zhangzhanglaila/local-photo-manager` 的 `main`，`deddabf2f660f26efcb7fc202f6c1fdba7275d66`
- **本地基线** `1d3455b5cd5c236cd765f5caf6e398b1a71759fe`
- **本地复核** 2026-11-22，`c623135b`

本地 PR #946 和 #947 已合入同一 SQLite 快照的窗口查询、扫描增量发布、缩略图替换保护和按资产身份更新视图。当前工作集中在重叠视口的重复通知，以及原生平台上扫描与拖动同时发生时的呈现验证。第 4 节保留 11 月 17 日的基线探针及后续复测方法。[当前扫描发布约束](../misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md)

## 1. 审计范围和提交记录

主窗口按柏林时间取 11 月 11 日至 17 日截至审计时，共七个自然日。通过 GitHub 的默认分支头和最近提交列表核对边界，逐一读取窗口内提交的补丁。该窗口共 8 笔，均在 11 月 14 日。其前一笔为 11 月 9 日的 `00da7b4a`。文中把 11 月 7–9 日的关联改动作为历史背景。 [上游 main 提交历史](https://github.com/zhangzhanglaila/local-photo-manager/commits/main/) · [上游审计基线](https://github.com/zhangzhanglaila/local-photo-manager/commit/deddabf2f660f26efcb7fc202f6c1fdba7275d66)

### 直接涉及性能的 5 笔提交

| 柏林时间 | 补丁核对结果 | 本地决策 | 来源 |
| --- | --- | --- | --- |
| 11-14 10:19 | `b5ce3b5c` 在 `GalleryGridView._do_update_empty_state()` 缓存视口矩形，避免对三个全视口标签重复调用 `setGeometry()`。 | 本地只保留一个空状态标签，仍有重复调用。可以做局部幂等整理，收益单独量化。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/b5ce3b5c94f80ab5bd4988c55f498cc5128eb94b) |
| 11-14 10:25 | `fe15b771` 拖动时间轴时保留扫描刷新 pending，暂停扫描驱动的模型刷新。最初在松手时强制补一次。 | 借鉴输入与呈现的协调。优先诊断本地重置的触发源，再判断是否需要暂缓结构刷新。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/fe15b771c086355e695ef7b10d764bf1bd99cd28) |
| 11-14 10:30 | `2305980b` 修正上一笔的立即补刷。若有 pending，松手后启动 300 ms 单次定时器，回调再次检查拖动抑制。 | 借鉴先让目标视口出图、再补刷的顺序。等待时间以本地数据确定，优先复用现有滚动状态与批次合并。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/2305980b75d36822aa8a9fc42fbe4f28a7c8def6) |
| 11-14 10:33 | `71a18ce7` 在拖动且已有缓存、窗口非空、总数相同的情况下，按绝对行号合并窗口，走 `rows_loaded` 通知。提交只改了视图与 Store，Adapter 走基线中已有的槽。该槽发 Display、Decoration、ToolTip 三个角色。 | 常规窗口合并在本地已生效。可验证并改进稀疏通知；结构变化继续检查资产身份、计数和顺序。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/71a18ce7989f6aff237256a2be1f1c9dcf507b6f) · [上游 Adapter 基线](https://github.com/zhangzhanglaila/local-photo-manager/blob/deddabf2f660f26efcb7fc202f6c1fdba7275d66/src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py) |
| 11-14 11:07 | `deddabf2` 将合并通知裁剪为加载区间和当前可见区间的交集。无交集时不发送通知，窗口数据仍已缓存。若无可见区间，上游保留原窗口范围。 | 本地已有多 surface 可见区间裁剪。候选改进是按实际变动行生成连续段、合并重叠段和收窄角色。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/deddabf2f660f26efcb7fc202f6c1fdba7275d66) |

上游最后一笔记录了扫描中一次拖动的诊断样本。worker 平均 106 ms、最大 650 ms，端到端平均 186 ms、最大 654 ms。提交描述的旧通知窗口约 200–1000 行，实际可见约 20–30 格。该记录缺少同场景改动前后成对数据、样本数与分位数，适合形成排查假设。worker 最大值本身也值得单独归因。五笔提交报告的筛选测试依次为 105 passed / 2 skipped、197 / 1、232 / 1、126 / 1、119 / 1，作用是说明上游回归检查范围。这些记录不能用来计算本地收益。 [上游诊断与验证记录](https://github.com/zhangzhanglaila/local-photo-manager/commit/deddabf2f660f26efcb7fc202f6c1fdba7275d66)

### 同期排除项

| 提交 | 分类和用途 | 来源 |
| --- | --- | --- |
| `119a5fb0` | 集合成员关系修复。收藏页取消收藏后从集合移除该行，普通视图只改收藏状态。相同的黑块症状可作为鉴别用例。 | [提交与用例](https://github.com/zhangzhanglaila/local-photo-manager/commit/119a5fb0cbff661be85c0452906ada0ebe742eed) |
| `0ef973e5` | 单个 HEIC 样本的解码兼容修复，涉及全局解码限制。解码失败单独记录，保持现有平台解码和输入限制策略。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/0ef973e540eb50c2069e5aa8a78982d0f3dc8f25) |
| `b3a057fc` | 单击选择、双击查看的交互改动。回归用例应按本地产品行为驱动，维持当前打开方式。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/b3a057fcedc207a173f2adca77ef2b0045a3b45c) |

### 窗口外的关联提交

| 提交 | 可复用的经验 | 本地对应 | 来源 |
| --- | --- | --- | --- |
| 11-07 `ce7ba180` | 分开磁盘命中和原图生成，命中时保留文件字节与 mtime。损坏缓存允许修复，任务必须有终态。 | 本地可见 worker 在 L2 命中后直接返回；miss 或解码错误才进入带 revision 检查的原子发布。guard/far 的 L2-only 路径不会解码原图。可以补一个对文件字节、mtime 和发布调用的端到端断言。 | [补丁与三个测试](https://github.com/zhangzhanglaila/local-photo-manager/commit/ce7ba180b4b1326430bf585598e216c00a9c7db6) |
| 11-09 `36a9bed3` | 同查询参数下扫描也会改变计数，失效需要随数据写入发生。 | 本地 `IndexStore` 已按 `CollectionQuery` 缓存 count/revision，scan merge 和收藏写入会一起清理集合锚点、计数缓存。保持现有仓储边界。 | [补丁与测试](https://github.com/zhangzhanglaila/local-photo-manager/commit/36a9bed3717724f74b0ad17dab45c1cae4197ada) |
| 11-09 `8ce52d1f` | 删除两个默认关闭且未证明收益的图库实验路径。后来在明确的拖动回归中重新使用区间更新。 | 本地 loader 已合并重复请求，并按 surface 和代次淘汰旧的排队请求。对 A/B 实验使用同一复现脚本和独立提交。 | [实验清理记录](https://github.com/zhangzhanglaila/local-photo-manager/commit/8ce52d1fb5d1132fc180d24d14ba1058c30439e5) |
| 11-08 `914eac76` | 库绑定顺序可决定是否进入固定的 5 秒启动等待。同时修复搜索线程和模型初始化竞态。 | 若实际启动回归出现固定等待，沿本地启动里程碑定位。此次开发以图库输入、窗口和缩略图为范围。 | [提交与补丁](https://github.com/zhangzhanglaila/local-photo-manager/commit/914eac76bc6a0734aecff102a8f06fb37e245803) |

## 2. 当前调用链

```text
滚轮 / 滚动条 -> GalleryScrollController -> AssetViewportDemand
    -> GalleryListModelAdapter + GalleryDemandCoordinator
    -> GalleryCollectionStore -> GalleryWindowLoader
    -> LibrarySession.asset_queries -> SQL 窄窗口 / 缩略图提示
    -> Store 稀疏合并 -> 局部 Qt 通知

扫描 / 文件监听 -> LibrarySession.scans -> LibraryScanService -> ScanLibraryUseCase
    -> 事务合并 -> ScanBatchCommitted -> Adapter 150 ms 合并
    -> 窗口 worker 从同一 SQLite 快照读取活跃视口
    -> Store 应用数据或新顺序 -> Adapter 发送局部数据或结构通知

各 surface 的 AssetViewportDemand -> 缩略图可见 / guard / far 队列
    -> worker 读 L2 或生成 QImage -> GUI 在预算内发布 QPixmap
    -> 精确缩略图角色通知 -> delegate 读取内存中的 TILE_SNAPSHOT
```

本地 `fc6da6bf`（5 月 31 日）加入扫描批次合并，`ae0c2e55`（6 月 14 日）加入异步稀疏窗口合并，`138905a5`（8 月 20 日）加入多视图裁剪，同日 `1b8dbc7d` 统一 512px 制品。8 月的 `718fa903` 还为 Filmstrip 实现了扫描中按资产身份恢复可见位置。对方 11 月的修复是在自己的图库时间轴上实现类似的交互结果。本地已有的视口需求、稀疏窗口和缩略图发布是收敛后的共同落点。

| 层 | 本地入口 | 当前行为 |
| --- | --- | --- |
| 输入 | [`GalleryScrollController`](../../src/iPhoto/gui/ui/widgets/gallery_scroll_controller.py) | 滚动条、滚轮和触控板发布滚动意图与各自的可见范围。 |
| 稀疏窗口 | [`GalleryCollectionStore`](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py)、[`GalleryWindowLoader`](../../src/iPhoto/gui/viewmodels/gallery_window_loader.py) | 带版本的窗口按实际变动行发出连续区间。新顺序需要先准备活动 Gallery 和 Filmstrip 的可见范围，离屏身份与待移动偏移由后台按同一顺序版本解析。 |
| 呈现 | [`GalleryListModelAdapter`](../../src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py) | 带版本的扫描更新用行插入、移除与 layout 信号。局部窗口信号分别与活动视口求交，目前按完全相同的区间去重。 |
| 扫描 | [`LibraryScanService`](../../src/iPhoto/bootstrap/library_scan_service.py)、[扫描发布约束](../misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md) | 普通批次按 150 ms 合并进入窗口读取。成功完成再请求快照，覆盖删除和配对等终态变化。 |
| 缩略图 | [`ThumbnailCacheService`](../../src/iPhoto/infrastructure/services/thumbnail_cache_service.py)、[`publish_thumbnail_artifact()`](../../src/iPhoto/infrastructure/services/thumbnail_artifact.py) | delegate 从内存读取 tile snapshot。纯 L2 命中直接返回，替换已验证的制品时保留原预览并校验目标 revision。 |
| 仓储 | [`LibraryAssetQueryService`](../../src/iPhoto/bootstrap/library_asset_query_service.py)、[`AssetRepository`](../../src/iPhoto/cache/index_store/repository.py) | 同一快照提供计数、窄窗口及版本。`collection_revision` 区分数据快照，`order_revision` 管理计数、排序锚点与行号。 |

## 3. 本地整合状态与后续工作

### A · 连续行段已落地，视口重叠去重待做

`GalleryCollectionStore.apply_window_result()` 为带版本的窗口计算变动 DTO 的连续行段。`GalleryListModelAdapter._on_window_changed()` 单独与各活动视口求交，去除相同区间。两个交集部分重叠时，源模型仍可能重复通知同一行。

后续在源模型坐标下合并活动视口区间，再与加载行段和有效行号求交。用实际写入 `[10, 11, 14, 15, 20]`、Gallery 可见 `[9, 14]`、Filmstrip 可见 `[11, 17]` 的用例，断言最终只发出 `[10, 11]` 和 `[14, 15]`。覆盖越界、只释放一个 surface、无视口启动及滚入已缓存行。初次载入的 DTO 保留现有角色语义，已知只变更缩略图时继续用缩略图角色。

### B · 批次合并与窗口收敛已落地

Store 将相关 ready 行登记为待刷新，由 Adapter 合并后提交窗口请求。旧窗口返回时，待刷批次或队列中的提交后刷新继续交付。Loader 合并同一选择的排队刷新，Store 拒绝已过期的快照。空库首批使用初始窗口，成功完成再读快照以覆盖删除和 Live Photo 配对。

现有回归分别让旧结果在合并定时器前后返回，检查后续 ready 行仍出现、扫描和 backfill 突发只留一个排队刷新，并用真实 JPEG 检查扫描完成前已经出图。若后续测得批次与终态产生重复读取，按触发源和窗口范围记录，再评估能否复用查询。[扫描进度回归](../../tests/gui/viewmodels/test_gallery_scan_progress.py)

### C · 扫描完成和拖动重叠时，选能解决实测问题的最小改变

带版本的扫描刷新现在通过增量结构通知、稳定资产身份和像素锚点应用新顺序。旧基线曾在按住滑块时重置模型。用原生滚动条和真实 L2 复测当前实现，观察资产跳位、输入长尾与已绘制图块回退。

| 对照结果 | 本地结合方式 | 实施边界 |
| --- | --- | --- |
| 同一批次和终态重复读取同一范围 | 记录 150 ms 合并、入队和应用代次，检查能否复用窗口 | 保留终态对计数、删除和配对的收敛。 |
| 必要的结构刷新造成位置或选择漂移 | 追踪现有资产身份与像素锚点，比较变更前后的目标和偏移 | 行号从同一顺序版本的后台快照解析。 |
| 锚点正确，拖动目标仍连续掉图 | 对照松手后先处理目标视口、再应用额外扫描刷新的实验 | 用 `sliderPressed`、`sliderReleased` 或 `isSliderDown()` 记录交互，验证长拖、取消和切库时也能收敛。 |
| 窗口、输入、当前资产与出图稳定 | 保留当前调度和并发回放 | 用逐帧观测和延迟分布记录结果。 |

Gallery 的滚轮和触控板继续走已有滚动意图。拖动中的普通窗口查询也能读到新顺序。比较松手时的目标资产和绘制内容，并在滑块最大值变化时同时记录像素偏移和资产身份。 [本地 Filmstrip 锚点](../../src/iPhoto/gui/ui/widgets/filmstrip_view.py) · [本地滚动意图](../../src/iPhoto/gui/ui/widgets/gallery_scroll_controller.py) · [Qt 滑块信号](https://doc.qt.io/qt-6/qabstractslider.html#sliderPressed)

行数变化现在由增量插入或移除通知包围，重排时通过 layout 信号映射持久索引。即使行数相同，也可能发生重排或原位置的资产被替换。 [Qt 模型结构变更说明](https://doc.qt.io/qt-6/qabstractitemmodel.html#beginRemoveRows)

| 交错场景 | 应有结果 |
| --- | --- |
| A 发起、B 提交、旧的 A 后返回 | A 的返回不会取消 B 已登记或已排队的刷新，后续窗口显示 B 的提交。 |
| 完成先于松手、与松手同一事件轮、晚于松手 | 最终收敛到已提交的状态和计数，测量各时机的窗口请求、结构通知与出图回退。 |
| 回调等待中再次拖动、隐藏 surface 或重绑库 | 旧回调取消或按 scope 校验退出。当前集合的有效 pending 仍能交付。 |
| 空库或窗口尚未可用，首批 ready 到达 | 初始窗口及时加载，首批不等待拖动结束。 |
| 同计数重排、当前项被删、乐观移动待完成 | 选中身份和滚动锚点按事实解析，结构信号保持合法；失效身份不落到占用旧行号的其他资产。 |
| Gallery 隐藏，Filmstrip 继续，另有显式详情行请求 | 活跃 surface 正常发布。隐藏 lease 不被晚到结果复活，显式行加载仍能完成。 |
| 场景中有历史 no-key 行、失败或过时缩略图 | 正常查询继续只展示 ready 且有完整 key 的行，特定窗口按现有 backfill 补齐。 |

### D · 独立验证小改动和产品行为

**空状态几何。** `GalleryGridView.resizeEvent()` 和 `_update_empty_state()` 都会重设同一个 `QLabel` 的 geometry。先改一个局部入口即可，初始化仍经过现有空状态更新。可以直接读取 label 当前几何，无需维护第二份 `QRect`。如果原平台在同一矩形下已经避免 resize/paint，按低风险维护项评估。

```python
# 在现有 label 存在性检查之后同步几何。
viewport_rect = self.viewport().rect()
if self._empty_label.geometry() != viewport_rect:
    self._empty_label.setGeometry(viewport_rect)
```

验证空库首次显示、首次 resize、空到非空、非空到空、隐藏后恢复，以及滚动条显示状态引起的 viewport 尺寸变化。若把逻辑集中到 viewport resize，覆盖影响实际 viewport 的事件。测量 geometry 写入次数和全视口 paint 次数，避免把重复 Python 调用的减少当作重绘收益。

**收藏成员关系。** 本地 `GalleryViewModel.toggle_favorite_row()` 与 `DetailViewModel.toggle_favorite()` 先通过 `LibrarySession` 绑定的状态服务写入，再调用 `GalleryCollectionStore.update_favorite_status()`。该方法当前只改 DTO、发 `row_changed`。先复现收藏查询取消后立即显示、切走再返回、深窗口和 Detail 中取消当前项。若出现行数或窗内身份滞后，补独立修复，按持久化后路径与当前查询决定是否改变成员关系。可评估现有 `remove_rows()`，同时校正缓存键、在飞窗口、选择锚点和已加载区间。完整查询语义包含 `is_favorite=False` 的反向过滤，复杂筛选用查询层重新确认。普通未筛选查询保持局部状态通知。 [状态服务](../../src/iPhoto/bootstrap/library_asset_state_service.py) · [本地 Store](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py)

**L2 命中与损坏。** 只补实证发现的测试缺口。优先复用 `test_worker_loads_l2_hit_without_rendering_source`、`test_ready_l2_hit_skips_revision_micro_and_repository_work` 和 `test_l2_reader_distinguishes_miss_read_and_decode_errors`。L2 命中验收可同时断言 `read_bytes()`、`st_mtime_ns`、原子发布未调用、请求有终态。损坏文件测试使用临时缓存路径，验证可见请求能在 worker 从原图重建，失败时 pending 退出，guard/far 仍只探测 L2，过期 source/sidecar revision 无法覆盖新制品。

## 4. 性能验证范本

### 两层回放

**确定性测试。** 复用现有扫描进度用例，控制旧窗口读完后的返回时点，在此期间登记 ready 批次，再检查后续窗口显示已提交行。另测扫描与 backfill 合并、取消，以及切换集合后拒绝旧结果。对于待做的区间去重，Adapter 测试直接订阅源模型 `dataChanged`，断言实际变动行、可见区间并集和越界。

**Qt 回放。** 扩展现有真实事件循环、临时 SQLite 和 512px L2 fixture。预先等待初始需求稳定，使用固定的拖动步长和数据种子。

1. 设置 `sliderDown` 后按 `20%、40%、70%、90%、65%、30%` 移动，每步约 210 ms。用相同数据比较无扫描、变动落在可见窗口内、变动在窗口前后，以及同计数更新。
2. 单独控制完成信号落在第三步按住时、松手所在事件轮、松手后。先用小数据校对状态机，再使用实际发布范围内的 50–200 条 ready 行重跑。加入同拍摄时间的记录以及另行持久化的非 ready 行，确认选择、排序稳定与 ready-only。
3. 记录提交事件、请求/应用代次、视口中的资产身份、Qt reset、滑块目标与实际值，以及 tile 的 placeholder、micro、full 转换。事件计数用于定位，资产像素偏移和可见帧用于判断用户能否感知跳动或掉图。
4. 用当前实现重跑，作为后续候选的基线。完成信号仍引发可见回归时，再比较 C 的方案，使用同一轨迹、同一 L2 条件和可绘制资产集。 [现有 Qt 性能测试](../../tests/performance/test_gallery_scroll_qt_benchmark.py)

### 场景、指标和通过条件

| 场景 | 主要指标 | 通过条件 |
| --- | --- | --- |
| 无扫描，稳定同一集合，快拖往返 | 输入处理 p95、事件循环 gap p95、模型 reset、重复通知行数 | 普通窗口加载 0 次全模型重置；现有快速滚动用例的调度 p95 ≤ 2 ms、单次追帧最大值 ≤ 100 ms 继续通过。 |
| 扫描中快拖，期间有提交和完成信号 | 按住期间的插入、移除、重排与 reset，目标命中后又成 placeholder 的帧占比，松手到 90% 可见图块 full 的时间 | 选中身份与目标位置正确。调度候选要改善已复现的可见回退或延迟分布，同时保持扫描结果及时收敛。 |
| 在飞旧窗、批次、完成交错 | 请求代次、当前 scope、待刷触发、页面最终计数和资产 | 后续窗口覆盖新的已提交批次。窗口错误或取消后，后续有效触发仍能刷新。 |
| 空库首批以及松手后的最终收敛 | 已提交首批到首个可用缩略图，最新已提交批次到最终一致 | 无首批饥饿、无漏刷。以基线和测得的补刷上界作为评审依据。 |
| Gallery 与 Filmstrip 同时消费，窗口有空洞或乱序返回 | 发出的连续区间数、同一源行重复通知、队列旧代次拒绝、两面 L1/L2 物理 key | 纯加载通知不穿空洞；重叠区间一次通知；隐藏 surface 不复活；两面共享现有 512px 缓存制品。 |
| 慢滚和高 DPI | 下一屏进入前的 full 就绪率、GUI 发布耗时与 backlog、L1 bytes | 现有候选场景的 99% 预热基线与已有高 DPI 用例的事件循环 p95 ≤ 24 ms 继续通过；其他用例按各自当前断言。 |
| 新增与旧库无 key、损坏 L2、外部媒体慢盘 | SQL 拉取行数、L2 outcome、L3 次数、cache ready 终态 | 无全库同步查询；无主线程读盘；ready-only；命中不写盘；可见缺失可收敛；guard/far 未占用可见通道。 |

建议每组在同一个机器和 Qt 构建下至少取 30 次独立拖动。冷 L1/热 L2、热 L1 和受控慢 L2 分开记录。库规模采用 1 万和 5 万行，至少拿一组接近实际使用的图库，带较大偏移、不同日期和相同时间戳。记录平台、媒体介质、DPR、视口、Qt 版本、库规模、批次数和投入行数。报告 median、p95、max、失败率和样本数。松手到 full 就绪使用松手时的目标资产集合做分母，区分已画过 micro、最终 full 成功与失败/未达标。固定采样超时，仍缺图的样本单独报超时率。关闭高频诊断再复跑计时，验证日志扰动。

### 诊断采集

可从已验证的测试入口采样。用于复测的库由测试生成。

```bash
IPHOTO_PERF_LOG=1 IPHOTO_PERF_PRIVACY_SAFE=1 \
  IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
  .venv/bin/python -m pytest -s -q \
  tests/performance/test_gallery_scroll_qt_benchmark.py \
  -k 'fast_round_trip' 2> /tmp/iphoto-gallery-perf.log
```

| 阶段 | 已有事件或可观测点 | 建议字段 |
| --- | --- | --- |
| 输入与需求 | `gallery_scroll_intent`、`gallery_viewport_demand` | surface、generation、intent、visible count、queued chunks；手动拖动用 `sliderPressed`、`sliderMoved`、`sliderReleased` 采集起止。 |
| 窗口 | `gallery_window_worker_finished`、`gallery_window_result_received`、`gallery_window_result_applied` | 通过请求 generation 对齐 worker 耗时和 GUI 接收、应用。若要测队列等待，在入队/worker 起点加同一个单调时钟采样。 |
| 数据提交 | `scan_events` 的 `batch_committed`、`gallery_scan_batch_received`、`gallery_scan_batches_flushed` | 批次数、ready 行数、revision、当前会话，区分普通批次和终态信号。 |
| 模型 | `gallery_window_model_update`、`gallery_model_reset` 与测试订阅的 `dataChanged` | 变更前后计数、是否重排、资产锚点、连续段和角色。区间计数以 Qt spy 为准。 |
| 缩略图 | `thumbnail_demand_reconciled`、`thumbnail_l2_finished`、`thumbnail_prefetch_l2_finished`、`thumbnail_publish_batch` | visible/guard/far、队列等待、读盘 outcome、decode、publish backlog。用 delegate 测试探针记录第一次画出 micro/full 和回退帧。 |
| SQL | `collection_count_query` 与窗口查询事件 | 查询类型、深度、耗时、结果行数。慢 SQL 可开启 `IPHOTO_PERF_EXPLAIN=1`；计时测量时独立关闭。 |

诊断日志中 `time_ms` 为墙钟。精确持续时间使用 `monotonic_ms()` 或 `time.perf_counter_ns()`。采集测试用合成文件名；现有隐私开关处理性能 JSON 字段，普通应用日志可能包含路径，分享前检查。 [性能日志实现](../../src/iPhoto/infrastructure/services/performance_events.py)

### 回归命令

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest -q tests/architecture

.venv/bin/python -m pytest -q \
  tests/gui/viewmodels/test_gallery_collection_store.py \
  tests/gui/viewmodels/test_gallery_list_model_adapter.py \
  tests/gui/viewmodels/test_gallery_demand_coordinator.py \
  tests/gui/viewmodels/test_gallery_thumbnail_hint_loader.py \
  tests/gui/viewmodels/test_gallery_backfill_completion_order.py \
  tests/gui/viewmodels/test_gallery_scan_progress.py \
  tests/test_gallery_demand.py tests/test_asset_grid_scroll.py \
  tests/ui/test_gallery_grid_view.py tests/ui/widgets/test_filmstrip_view.py \
  tests/test_thumbnail_cache_service.py \
  tests/test_thumbnail_runtime_policy.py

.venv/bin/python -m pytest -q \
  tests/application/test_scan_library_use_case.py \
  tests/application/test_library_scan_service.py \
  tests/application/test_library_asset_query_service.py \
  tests/cache/test_index_store_features.py \
  tests/infrastructure/test_thumbnail_artifact.py \
  tests/performance/test_refactor_performance_baseline.py

IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
  .venv/bin/python -m pytest -q tests/performance/test_gallery_scroll_qt_benchmark.py

IPHOTO_RUN_STRESS=1 .venv/bin/python -m pytest -q \
  tests/performance/test_refactor_performance_baseline.py
```

Windows/Linux 对调度改动使用本机 Qt、真实 L2 及窗口输入重复跑，打包产物用实际鼠标拖动滚动条。macOS 单独验证触控板、滚动条显示策略以及 Retina 下的覆盖区间和发布成本。面向打包的图库滚动验证要观察重置时当前资产、滚动条滑块和全图缩略图。使用 GPU-first Detail 现有跑法做打开、返回、Filmstrip、编辑后回图库的回归，涉及渲染路径本身时按 Detail runbook 跑。 [图库性能约束](../misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md) · [Detail 基准](DETAIL_OPEN_BENCHMARK_RUNBOOK.md)

### 11 月 17 日历史基线与探针

环境为 macOS arm64、Python 3.12.10、PySide6 6.11.1，Qt 测试使用 offscreen。测量点为本地基线提交。

| 验证 | 结果 |
| --- | --- |
| 图库 Store、Adapter、demand、hint loader、滚动控件与缩略图策略相关测试 | 261 passed，6.00 s。 |
| 架构检查及架构、查询、扫描、索引、backfill 完成顺序的测试 | 架构检查通过。104 passed，10.45 s。 |
| 完整 opt-in `test_gallery_scroll_qt_benchmark.py` | 13 passed，103.93 s。部分用例注入 Windows/Linux 策略与延迟，本次执行平台是 macOS。 |
| `test_refactor_performance_baseline.py`，启用 `IPHOTO_RUN_STRESS` | 8 passed，10.83 s。包含 10 万和 100 万行模拟查询服务的窗口上界检查。 |
| 本次复核 · 定向回归 | Store、Adapter、backfill 完成顺序、Filmstrip 与 AssetGrid 滚动共 158 passed，2.49 s。与原始基线测试有重叠。 |
| 空状态几何探针 | 在可见的空图库上同矩形调用 100 次，标签收到 0 个 Move、0 个 Resize。实际调整窗口尺寸后收到各 1 个事件，空与非空的显示切换通过。 |
| 原始滚动条与合成批次探针 | 240 条现有记录及热 L2，按住滑块后每隔约 210 ms 移动到 20%、40%、70%、90%、65%、30%。无批次的一轮在按住和松手后均为 0 次 reset。有批次的一轮在每次移动后合并 4 条 ready 记录，松手后发完成通知。按住期间 0 次、松手后 1 次 reset，最终行数 264。 |
| 本次复核 · 活动视口重叠 | 向真实 Adapter 交付窗口包络 `[10, 20]`，Gallery 活动区间 `[9, 14]`、Filmstrip `[11, 17]`。实际发出 `[10, 14]` 与 `[11, 17]`，源行 11–14 重复覆盖，两个信号都携带空角色列表。 |
| 本次复核 · 同 revision 旧窗口 | 用真实 Store、受控查询服务和窗口回包。窗口 A 发出后登记一批相关 ready 行 B，返回同 revision 的 A 后 `apply_window_result()` 接受了 A，pending 被清空，随后 flush 发出的请求数为 0。 |
| 本次复核 · 完成时序对照 | 两轮各有 240 条初始记录、热 L2、同一六步滑块轨迹，在 70% 处提交 12 条 ready 行。按住时发完成则按住期间 reset 1 次；松手后发完成则按住期间 0 次、松手后 1 次。两轮最终行数均为 252，六个采样点的滑块 `value` 都与各自目标一致。 |

滑块探针复用现有 benchmark 的 Qt、SQLite 与 L2 fixture，在临时图库提交合成记录，分别打到当时的 Adapter 和 Store 批次、完成入口。每个条件各跑一轮，批次小于真实发布规模，采样间隔无法捕捉瞬时回跳或画面变空。原生时序结论仍需用当前实现和实际批量规模复测。

### 实施记录模板

| 记录项 | 填写内容 |
| --- | --- |
| 基线与候选 | 两个 commit SHA，平台、Qt、DPR、视口、介质、数据种子及缓存条件 |
| 重现 | 场景、滚动目标序列、批次大小和时点、迭代数、采样超时 |
| 策略变化 | 被改动的请求、区间、角色或 timer，绑定的 session/collection/generation |
| 性能结果 | input、worker、GUI apply、首个可用 tile、90% full 的 median/p95/max，重置与重复通知计数，超时率 |
| 数据与行为 | 最终总数、目标及选中资产身份、ready-only、两面缓存共享、旧回调丢弃、完成补刷 |
| 复测 | 原有用例、定向回归、各原生目标平台与未覆盖场景 |

## 5. 后续交接

1. 补重叠可见区间的定向回归，再验证源模型只通知一次。Gallery 和 Filmstrip 均应绘制已缓存的可见行。
2. 在原生平台用当前批次大小重复拖动回放，记录窗口请求、资产像素偏移、选中身份和缩略图回退。复现可见问题时按第 3 节选择改动。
3. 几何、收藏成员关系和 L2 用例单独验证。每项候选记录上游依据、复现、当前基线和未覆盖平台。

实施时遵循 [当前架构](../architecture.md)、[图库滚动](../misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md)、[大图库查询](../misc/LARGE_LIBRARY_QUERY_GUARDRAILS.md)、[扫描可见发布](../misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md) 和 [移动/恢复覆盖层](../misc/MOVE_RESTORE_OPTIMISTIC_UI_GUARDRAILS.md) 的约束。调用库行为沿 `RuntimeContext -> LibrarySession -> application ports/services`，Gallery 的 Qt 调度和缓存结果应用保留在 GUI 边界。扫描持久化、用户状态和 Detail/Edit 的 GPU-first 渲染合同由现有流程继续负责。

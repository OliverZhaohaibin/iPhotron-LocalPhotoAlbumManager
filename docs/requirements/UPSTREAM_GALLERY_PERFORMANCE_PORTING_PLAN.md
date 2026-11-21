# 平行项目 Gallery 性能审计与本地整合计划

- **审计日期** 2026-11-17，Europe/Berlin
- **上游基线** `zhangzhanglaila/local-photo-manager` 的 `main`，`deddabf2f660f26efcb7fc202f6c1fdba7275d66`
- **本地基线** `1d3455b5cd5c236cd765f5caf6e398b1a71759fe`
- **交付范围** 平行项目提交审计、本地整合设计和验证范本

两边已经在稀疏窗口合并、可见区间裁剪和 L2 命中免写盘上趋同。整合点是把对方的两条经验接到本地已有流程。**窗口结果只通知真正写入且可见的行，扫描变动由覆盖到该批次的窗口刷新确认。** Gallery 和 Filmstrip 继续用同一 Store 和缩略图调度。

复核时观察到两个可以直接写回归测试的本地缺口。一是两个视图的可见区间重叠时，同一组源模型行会收到两次通知。二是早于扫描批次发起的窗口请求，即使返回的 revision 与当前相同，应用结果后也能提前清掉待刷新标记。另一次离屏 Qt 对照把扫描完成信号提前到了按住滑块期间，触发 1 次重置，但采样点的滑块值仍跟上了目标。**先修复通知和批次确认，再确定结构刷新是否需要调整时机。** 实验条件和结果见第 4 节。

## 1. 审计范围和提交记录

主窗口按柏林时间取 11 月 11 日至 17 日截至审计时，包含今天在内共七个自然日。通过 GitHub 的默认分支头和最近提交列表核对边界，逐一读取窗口内提交的补丁。该窗口共 8 笔，均在 11 月 14 日。其前一笔为 11 月 9 日的 `00da7b4a`。文中把 11 月 7–9 日的关联改动作为历史背景。 [上游 main 提交历史](https://github.com/zhangzhanglaila/local-photo-manager/commits/main/) · [上游审计基线](https://github.com/zhangzhanglaila/local-photo-manager/commit/deddabf2f660f26efcb7fc202f6c1fdba7275d66)

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

## 2. 本地调用链和候选问题

```text
滚轮 / 滚动条 -> GalleryScrollController -> AssetViewportDemand
    -> GalleryListModelAdapter + GalleryDemandCoordinator
    -> GalleryCollectionStore -> GalleryWindowLoader
    -> LibrarySession.asset_queries -> SQL 窄窗口 / 缩略图提示
    -> Store 稀疏合并 -> 局部 Qt 通知

扫描 / 文件监听 -> LibrarySession -> ScanLibraryUseCase -> 已提交的 ScanBatchCommitted
    -> Adapter 150 ms 合并 -> Store 决定是否刷新窗口 -> 同一 loader
    -> Store 更新计数或 revision -> Adapter 选择区间通知或重置

各 surface 的 AssetViewportDemand -> 缩略图可见 / guard / far 队列
    -> worker 读 L2 或生成 QImage -> GUI 在预算内发布 QPixmap
    -> 精确缩略图角色通知 -> delegate 读取内存中的 TILE_SNAPSHOT
```

本地 `fc6da6bf`（5 月 31 日）加入扫描批次合并，`ae0c2e55`（6 月 14 日）加入异步稀疏窗口合并，`138905a5`（8 月 20 日）加入多视图裁剪，同日 `1b8dbc7d` 统一 512px 制品。8 月的 `718fa903` 还为 Filmstrip 实现了扫描中按资产身份恢复可见位置。对方 11 月的修复是在自己的图库时间轴上实现类似的交互结果。本地已有的视口需求、稀疏窗口和缩略图发布是收敛后的共同落点。

| 层 | 本地入口 | 审计结论 |
| --- | --- | --- |
| 输入 | [`GalleryGridView`](../../src/iPhoto/gui/ui/widgets/gallery_grid_view.py)、[`AssetGrid`](../../src/iPhoto/gui/ui/widgets/asset_grid.py)、[`GalleryScrollController`](../../src/iPhoto/gui/ui/widgets/gallery_scroll_controller.py) | 本地对应操作是图库滚动条、滚轮与触控板。这里没有上游的图库时间轴 scrubber，`AssetScrollController` 已从 `valueChanged` 识别 scrollbar 输入与速率。 |
| 稀疏窗口 | [`GalleryCollectionStore.apply_window_result()`](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py#L801)、[`GalleryWindowLoader`](../../src/iPhoto/gui/viewmodels/gallery_window_loader.py#L323) | 已区分 surface、请求代次和数据 revision，旧 viewport 结果只能按现行规则复用相关行。缓存有裁剪预算。普通窗口就绪会按实际缓存行的最小与最大行号发送一个 `window_changed`，这两个端点之间可能有空洞。 |
| 呈现 | [`GalleryListModelAdapter._on_source_changed()`](../../src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py#L930)、[`_on_window_changed()`](../../src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py#L1110) | 后者与各活动 surface 的可见范围求交，只去除完全相同的区间。离屏 Qt 探针观察到两个部分重叠的 `dataChanged`。空角色列表表示所有角色可以变动。前者能在选择、行数和窗口资产身份均稳定时走数据刷新，行数或顺序变化可触发重置。 [Qt 数据变更语义](https://doc.qt.io/qt-6/qabstractitemmodel.html#dataChanged) |
| 扫描 | [`_enqueue_scan_batch_on_ui_thread()`](../../src/iPhoto/gui/viewmodels/gallery_list_model_adapter.py#L635)、[`record_scan_batch()`](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py#L1456)、[`handle_scan_finished()`](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py#L1481)、[`desktop_coordinator_runtime._connect_signals()`](../../src/iPhoto/gui/coordinators/desktop_coordinator_runtime.py#L580) | 普通批次已合并，Store 用路径、排序键判断是否影响当前窗口。成功完成信号直接请求窗口。通用窗口应用还会清掉扫描 pending，探针证实同 revision 的旧窗口也能消费较新标记。 |
| 缩略图 | [`_load_or_render_thumbnail()`](../../src/iPhoto/infrastructure/services/thumbnail_cache_service.py#L3053)、[`publish_thumbnail_artifact()`](../../src/iPhoto/infrastructure/services/thumbnail_artifact.py#L148)、[`AssetGridDelegate.paint()`](../../src/iPhoto/gui/ui/widgets/asset_delegate.py#L61) | 热路径从内存消费 tile snapshot，`DecorationRole` miss 已有测试保证不触发 full 请求。上游“大范围通知直接排满解码”的链条在本地已拆开。剩余成本应通过 Qt 通知、paint 和 demand 重算分别测量。 |
| 仓储 | [`LibraryAssetQueryService`](../../src/iPhoto/bootstrap/library_asset_query_service.py)、[`IndexStore._collection_count_and_revision()`](../../src/iPhoto/cache/index_store/repository.py#L1985) | SQL 窄投影、ready-only 和 count/revision 缓存都已存在。若日志指向慢查询，先按 collection、偏移及 query plan 查证。 |

| 平行项目的经验 | 本地落点 | 整合决定 |
| --- | --- | --- |
| 缓存可以预热大范围，通知按实际可见内容缩小 | Store 已应用行的连续段，Adapter 的多视图可见区间并集 | 直接吸收。修正已复现的源模型通知重叠，同时覆盖稀疏空洞和越界。 |
| 拖动时保留扫描 pending，松手能补刷 | Store 登记扫描变动、现有窗口 loader、Adapter 合并扫描信号 | 先吸收 pending 的可靠确认与单次补刷。保留当前 150 ms 合并作为基线。 |
| 松手先恢复目标视口，再呈现扫描变动 | 本地滚动控制器、结构通知、Filmstrip 的身份和像素锚点 | 用实际回跳或出图回退决定实施方式。优先区分多余请求与必须呈现的结构变化。 |
| 只在几何改变时调整占位标签，L2 命中免写盘 | 本地一个空状态标签、已有原子缩略图制品发布 | 视证据补轻量清理与行为测试。同步紧邻的收藏成员关系、HEIC 和双击改动需要独立产品范围。 |

## 3. 本地整合设计与提交顺序

### A · 普通窗口用实际行段通知 Gallery 和 Filmstrip

**修改入口** `GalleryCollectionStore.apply_window_result()` 的普通窗口分支及 Adapter 的 `_on_window_changed()`。这一步在现有稀疏合并上做减法，窗口查询、缓存填充、micro warm 和 full demand 照现有规则继续。

1. Store 在代次、revision、相关性过滤和缓存裁剪之后，从本次实际写入并仍可读取的行号生成连续段。先处理区间精度，行内容去重只在证实重复写入构成成本时再做。结构通知、显式 `row_loaded`、当前项锚和乐观移动覆盖层沿现有语义处理。
2. Adapter 在源模型坐标下合并活动 Gallery 和 Filmstrip 的可见区间，再与各连续加载段及有效行号求交。分别用 `max(start, 0)` 与 `min(end, count - 1)` 限定边界，交集为空就跳过。若选择逐段复用现有 `window_changed(first, last)`，应先合并可见区间，确保同一源行只覆盖一次。
3. 首次载入的 DTO 可能同时新增名称、类型、日期、收藏状态和 tile snapshot，保留该路径的角色语义。已知只有缩略图变化时继续使用现有 `DecorationRole` 和 `Roles.TILE_SNAPSHOT`。上游三角色列表适用于它自己的 Adapter。若样本显示角色传播仍有成本，逐个核对本地代理、过滤和尺寸消费者，再独立优化角色。
4. 初次显示还没有视口需求时，保留初始窗口通知和显式详情行的 `row_loaded`。已有另一活动 surface 时，释放 Gallery 只减少对应可见区间。随后滚到此前已缓存的屏外行，依靠 viewport demand 和内存 model data 完成出图。

**确定性用例。** Store 实际写入行 `[10, 11, 14, 15, 20]`，Gallery 可见 `[9, 14]`，Filmstrip 可见 `[11, 17]`。当前组合会从包络 `[10, 20]` 产生 `[10, 14]` 与 `[11, 17]` 两条源模型通知。改后应精确产生 `[10, 11]`、`[14, 15]`，不含空洞、屏外行和重复行。加用例覆盖部分越界、完全越界、释放一个 surface、无视口启动和结构刷新。用现有 Store 与 Qt Adapter 测试分别断言行段和信号，再用真实视图检查滚到 20 时出图。

```text
Store: 可应用的结果 → 写入并裁剪缓存 → 实际写入且保留的连续行段
Adapter: 活动视口的可见行并集 ∩ 连续行段 ∩ 有效行号 → 局部 dataChanged
缩略图调度: viewport demand → 可见 / guard / far → 原有 L1/L2 发布
```

`_on_window_changed()` 服务的调用方还有移除行、显式行加载；`_emit_data_refresh()` 则服务来源刷新，并有无窗口时的宽通知。首个提交限定在普通窗口写入的通知与 Adapter 对可见区间的求交，分别统计这两类信号。Qt 的源模型信号一次仍可被两个视图各自消费，去除的是同一源行被两个重叠区间重复覆盖。

### B · 让已提交扫描批次有对应的窗口确认

**修改入口** `GalleryCollectionStore.record_scan_batch()`、`flush_pending_scan_refresh()`、`apply_window_result()`。Adapter 继续合并普通批次，并转发 watcher 和 facade 的完成事件。Store 复用现有请求代次识别哪个结果能消费对应 pending，loader 继续负责排队、去重和实际读取。

当前 `apply_window_result()` 只要接受任意窗口，就会清空 pending 标志、路径和排序键。旧窗口可用于补齐自己的行，但无法证明已读到后来的扫描提交。相等的 collection revision 也无法提供这个证明。下列设计优先用现有递增的请求代次，必要时才记独立的扫描序号。

1. Store 接受与活动集合相关的 ready 批次后，记录该集合下一次可覆盖该批次的最早请求代次，保留既有路径与排序键。批次 B 到来前已发出的 A，即使成功且 revision 相同，也不能确认 B。集合切换、切库或 rebind 重新建立状态，旧请求继续由现有生命周期淘汰。
2. 只有当前集合的有效结果覆盖对应变动所需的查询范围时，才消费待刷新标记。已有窗口请求能覆盖时可以复用。显式详情单行、只为其他 surface 加载的无关窗、取消和错误结果不代表当前待刷范围已经处理。若覆盖范围缺少可验证的标识，可以先让只有 Store 发起的该次 scan refresh 有权确认 pending，并将其请求代次记在 Store。请求去重必须能区分可复用的在飞请求与早于提交的请求。
3. B 之后 C 又到了时，用保守的合并语义。B 的结果若无法覆盖 C，就保留聚合待刷集合，在既有合并调度中补一次。避免按每个批次立刻读库，也避免因一个失败结果立即循环重试。空库的首个 ready 批次直接走初始窗口。合法的删除、成功完成或其他使可见集合失效的事件仍须收敛到最新查询。
4. 观测普通批次和成功完成分别发起了几次同范围请求。若确认为重复，在 Adapter 的既有合并入口协调两者，区分“依据排序范围决定要不要刷”和“终态需要收敛计数、删除和排序”的语义。正常窗中批次触发的查询若已覆盖最终状态，终态可复用。完成事件的库/范围过滤须在这一入口生效，Map 和状态栏照常独立消费。backfill 的现有契约是先进入 Gallery 刷新调度再发完成反馈，保留同根库重绑定时的终态交付。
5. 若改变加载器的签名或请求用途，验证排队合并、丢弃和失败都会释放关联的请求状态，pending 仍有后续触发。首版可用 Store 的现有请求代次记录确认资格，按测试需要决定是否扩展 `GalleryWindowRequest`。不需要修改 SQLite schema、扫描批大小或缩略图制品。

```text
窗口 A 已发出 ─────── B 提交并登记 ─────── A 返回 ─────── 当前窗口 C 读取并应用
                                             │                         │
                                        可补 A 的数据               覆盖 B 后确认
                                        保留 B 的 pending          如有更新批次则继续合并
```

**提交拆分。** 先加 A、B 同 revision 且顺序固定的回归用例，修 pending 的确认。再用 batch 与 finish 交错的测试决定是否合并请求触发。两者按独立的失败断言评审。

### C · 扫描完成和拖动重叠时，选能解决实测问题的最小改变

**决策入口** `GalleryScrollController` 的真实滚动条交互、Adapter 的结构刷新及现有 Store 的稳定资产锚点。对方的 300 ms 先作为实验条件。离屏现已复现“按住时模型重置”，还要验证其是否造成资产跳位、输入长尾或已绘制图块回退。

| 对照结果 | 本地结合方式 | 实施边界 |
| --- | --- | --- |
| 同一批次/终态重复查询或重复重置 | 完成 B 的合并和确认后，用相同回放验证 | 合并触发与结果确认，优先用现有 150 ms 调度和有效查询范围解释差异。 |
| 确有一次必要结构刷新，伴随位置或选择漂移 | 借用本地 Filmstrip 的稳定身份与像素锚定思路。以拖动目标附近已加载资产的 id 或 path 记录纵向位置，刷新后按相同资产恢复。 | 仅在合理的请求、集合与视口代次内恢复。按需由已有异步查找解析，避免在重置、paint 或鼠标回调中同步查询。锚点缺失时遵循选择恢复和范围限制规则。 |
| 结构刷新本身使正在拖动的目标连续掉图，且 B 与锚点处理仍不足 | 已有可用窗口且直接拖动滑块时，把扫描驱动的 Gallery 额外呈现刷新和松手后的最终 viewport demand 协调到现有调度。 | `sliderPressed` / `sliderReleased` 或 `isSliderDown()` 识别明确交互。松手先处理最终可见 demand，再尝试一次待刷新请求；设上界供长拖、未返回窗口或无 settled 事件时收敛。 |
| 有 reset，但窗口、输入、当前资产与出图稳定 | 保持结构刷新策略，保留并发回放用于回归 | 重置次数用于归因，用户实际可见的结果决定调度变更。 |

Gallery 的滚轮和触控板继续走已有的 velocity/intent。试验只延后导致回退的扫描呈现工作，仍允许可见窗口与缩略图读取。活跃拖动中的普通查询也可能读到新的计数并触发 reset，所以仅拦截 `scanFinished` 无法给出“拖动期间零重置”的保证。若探针指向普通窗口，优先修正确实发生的锚点漂移或重复发布。此时应比较松手时的目标资产和绘制内容，滚动条最大值变化时同步记录像素与资产锚点。 [本地 Filmstrip 锚点](../../src/iPhoto/gui/ui/widgets/filmstrip_view.py#L185) · [本地滚动意图](../../src/iPhoto/gui/ui/widgets/gallery_scroll_controller.py#L261) · [Qt 滑块信号](https://doc.qt.io/qt-6/qabstractslider.html#sliderPressed)

必要的新增、删除、重排和切换集合继续依靠合法的 Qt 结构通知。即使行数相同，排序也可能改变。只有能证明精确插入或删除区间时，才评估 `beginInsertRows` / `beginRemoveRows`。对应的 begin/end 必须包围实际的行数据和计数变化。 [Qt 模型结构变更说明](https://doc.qt.io/qt-6/qabstractitemmodel.html#beginRemoveRows)

| 交错场景 | 应有结果 |
| --- | --- |
| A 发起、B 提交、同 revision 的 A 后返回 | A 可覆盖自己的行，B 的 pending 仍在，相应窗口随后读取并确认。 |
| 完成先于松手、与松手同一事件轮、晚于松手 | 正确的状态和计数最多需要一个合并收敛流程。测量各时机的 reset 与出图回退。 |
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

**收藏成员关系。** 本地 `GalleryViewModel.toggle_favorite_row()` 与 `DetailViewModel.toggle_favorite()` 先通过 `LibrarySession` 绑定的状态服务写入，再调用 `GalleryCollectionStore.update_favorite_status()`。该方法当前只改 DTO、发 `row_changed`。先复现收藏查询取消后立即显示、切走再返回、深窗口和 Detail 中取消当前项。若出现行数或窗内身份滞后，补独立修复，按持久化后路径与当前查询决定是否改变成员关系。可评估现有 `remove_rows()`，同时校正缓存键、在飞窗口、选择锚点和已加载区间。完整查询语义包含 `is_favorite=False` 的反向过滤，复杂筛选用查询层重新确认。普通未筛选查询保持局部状态通知。 [状态服务](../../src/iPhoto/bootstrap/library_asset_state_service.py) · [本地 Store](../../src/iPhoto/gui/viewmodels/gallery_collection_store.py#L486)

**L2 命中与损坏。** 只补实证发现的测试缺口。优先复用 `test_worker_loads_l2_hit_without_rendering_source`、`test_ready_l2_hit_skips_revision_micro_and_repository_work` 和 `test_l2_reader_distinguishes_miss_read_and_decode_errors`。L2 命中验收可同时断言 `read_bytes()`、`st_mtime_ns`、原子发布未调用、请求有终态。损坏文件测试使用临时缓存路径，验证可见请求能在 worker 从原图重建，失败时 pending 退出，guard/far 仍只探测 L2，过期 source/sidecar revision 无法覆盖新制品。

## 4. 性能验证范本

### 两层回放

**确定性测试。** 在 Store 测试用受控 handler 保存实际 `GalleryWindowRequest`，先应用初始窗口，发出显式行请求 A，登记 ready 批次 B，再回送 A 的旧快照。让 A 和 B 使用相同 revision，分别断言 A 仍可补行、B 仍 pending、当前可见窗完成后 B 才被确认。另测在飞请求去重、失败以及收到更多批次。Adapter 测试直接订阅源模型 `dataChanged`，分别校对实际写入段、可见区间并集和越界。

**Qt 回放。** 扩展现有真实事件循环、临时 SQLite 和 512px L2 fixture。预先等待初始需求稳定，使用固定的拖动步长和数据种子。

1. 设置 `sliderDown` 后按 `20%、40%、70%、90%、65%、30%` 移动，每步约 210 ms。用相同数据比较无扫描、变动落在可见窗口内、变动在窗口前后，以及同计数更新。
2. 单独控制完成信号落在第三步按住时、松手所在事件轮、松手后。先用小数据校对状态机，再使用实际发布范围内的 50–200 条 ready 行重跑。加入同拍摄时间的记录以及另行持久化的非 ready 行，确认选择、排序稳定与 ready-only。
3. 记录提交事件、请求/应用代次、视口中的资产身份、Qt reset、滑块目标与实际值，以及 tile 的 placeholder、micro、full 转换。事件计数用于定位，资产像素偏移和可见帧用于判断用户能否感知跳动或掉图。
4. 候选实施逐个打开。先分别比较 A 和 B，保留可解释的通知和批次行为。完成信号仍引发可见回归时才比较 C 的锚定或延后方案，使用同一轨迹、同一 L2 条件和可绘制资产集。 [现有 Qt 性能测试](../../tests/performance/test_gallery_scroll_qt_benchmark.py)

### 场景、指标和通过条件

| 场景 | 主要指标 | 通过条件 |
| --- | --- | --- |
| 无扫描，稳定同一集合，快拖往返 | 输入处理 p95、事件循环 gap p95、模型 reset、重复通知行数 | 普通窗口加载 0 次全模型重置；现有快速滚动用例的调度 p95 ≤ 2 ms、单次追帧最大值 ≤ 100 ms 继续通过。 |
| 扫描中快拖，期间有提交和完成信号 | 按住期间 reset，目标命中后又成 placeholder 的帧占比，松手到 90% 可见图块 full 的时间 | 选中身份与目标位置正确。调度候选要改善已复现的可见回退或延迟分布，同时保持扫描结果及时收敛。 |
| 同 revision 的在飞旧窗、批次、完成交错 | 请求代次、当前 scope、确认的批次、页面最终计数和资产 | 旧窗不能清掉新批次；确认来自覆盖相应变动的有效结果。失败、取消和再拖动后仍能收敛。 |
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
| 数据提交 | `gallery_scan_batch_received`、`gallery_scan_batches_flushed` | 批次数、ready 行数、revision、当前会话；在测试中区分普通批次和终态信号。 |
| 模型 | `gallery_model_reset`、`gallery_model_data_refresh` 与测试订阅的 `dataChanged` | reset 时间、count、revision、资产锚点、连续段和角色。现有 data refresh 日志不覆盖所有局部信号，区间计数以 Qt spy 为准。 |
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
  tests/test_gallery_demand.py tests/test_asset_grid_scroll.py \
  tests/ui/test_gallery_grid_view.py tests/ui/widgets/test_filmstrip_view.py \
  tests/test_thumbnail_cache_service.py \
  tests/test_thumbnail_runtime_policy.py

.venv/bin/python -m pytest -q \
  tests/application/test_scan_library_use_case.py \
  tests/application/test_library_scan_service.py \
  tests/application/test_library_asset_query_service.py \
  tests/cache/test_index_store_features.py \
  tests/performance/test_refactor_performance_baseline.py

IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 \
  .venv/bin/python -m pytest -q tests/performance/test_gallery_scroll_qt_benchmark.py

IPHOTO_RUN_STRESS=1 .venv/bin/python -m pytest -q \
  tests/performance/test_refactor_performance_baseline.py
```

Windows/Linux 对调度改动使用本机 Qt、真实 L2 及窗口输入重复跑，打包产物用实际鼠标拖动滚动条。macOS 单独验证触控板、滚动条显示策略以及 Retina 下的覆盖区间和发布成本。面向打包的图库滚动验证要观察重置时当前资产、滚动条滑块和全图缩略图。使用 GPU-first Detail 现有跑法做打开、返回、Filmstrip、编辑后回图库的回归，涉及渲染路径本身时按 Detail runbook 跑。 [图库性能约束](../misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md) · [Detail 基准](DETAIL_OPEN_BENCHMARK_RUNBOOK.md)

### 原始基线与本次整合复核

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

滑块探针复用现有 benchmark 的 Qt、SQLite 与 L2 fixture，在临时图库提交合成记录，分别打到生产 Adapter 和 Store 的批次、完成入口。每个条件各跑一轮，批次小于真实发布规模，采样间隔无法捕捉瞬时回跳或画面变空。复核确认了完成信号可以在拖动中触发重置，延迟代价与可见影响待产品规模回放和原生平台验证。重叠与同 revision 探针是可独立迁入定向测试的反例，性能结果留待实施。

### 实施记录模板

| 记录项 | 填写内容 |
| --- | --- |
| 基线与候选 | 两个 commit SHA，平台、Qt、DPR、视口、介质、数据种子及缓存条件 |
| 重现 | 场景、滚动目标序列、批次大小和时点、迭代数、采样超时 |
| 策略变化 | 被改动的请求、区间、角色或 timer，绑定的 session/collection/generation |
| 性能结果 | input、worker、GUI apply、首个可用 tile、90% full 的 median/p95/max，重置与重复通知计数，超时率 |
| 数据与行为 | 最终总数、目标及选中资产身份、ready-only、两面缓存共享、旧回调丢弃、完成补刷 |
| 复测 | 原有用例、定向回归、各原生目标平台与未覆盖场景 |

## 5. 提交安排和交接记录

1. **合入 A 时附局部通知回归。** Store 验证实际写入段，Adapter 验证源模型区间并集，Qt 用例覆盖 Gallery 和 Filmstrip 的出图。关联平行项目的窗口合并和区间补丁。
2. **合入 B 时附批次确认回归。** 用同 revision 的 A/B 复现证明修正，按需用第二提交合并终态与普通批次的重复请求。保留失败、再拖动、切库和同根 rebind 的执行记录。
3. **用同一回放决定 C。** 提交基准、稳定身份和逐帧观测。证实可见回归后，按第 3 节决策表实施。记录选择策略的测量结果和触发条件。小范围的几何、收藏成员关系与 L2 验证使用独立改动。
4. **跨项目复用用例。** 每笔参考改动在 PR 写明平行项目 SHA、复现场景、本地入口和验收断言。双方可使用同一拖动轨迹、批次时点和结果表验证 Gallery，Qt 与后台线程接线沿各自现有架构实现。
5. **复测与回退。** 各提交单独对比本地基线。撤销产生回归的调度或通知改动后，复跑同一脚本，确认选择、计数、可见缩略图和 pending 恢复。记录工具链、命令、冷热缓存、统计、通过的用例及未覆盖的原生平台。

实施时遵循 [当前架构](../architecture.md)、[图库滚动](../misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md)、[大图库查询](../misc/LARGE_LIBRARY_QUERY_GUARDRAILS.md)、[扫描可见发布](../misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md) 和 [移动/恢复覆盖层](../misc/MOVE_RESTORE_OPTIMISTIC_UI_GUARDRAILS.md) 的约束。调用库行为沿 `RuntimeContext -> LibrarySession -> application ports/services`，Gallery 的 Qt 调度和缓存结果应用保留在 GUI 边界。扫描持久化、用户状态和 Detail/Edit 的 GPU-first 渲染合同由现有流程继续负责。

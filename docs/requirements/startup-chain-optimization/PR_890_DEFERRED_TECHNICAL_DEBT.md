# PR #890 延期技术债务台账

更新日期：2026-08-10

## 状态与范围

- 原审查基线：`59c961875a5e337299cf7f2fb1bff59c63b74f93`
- Bug-only 修复提交：`588682c29f526e9903dd90c32796bb7e3e744fe5`
- 修复 PR：[#902](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/pull/902)
- 当前状态：`deferred / not_started`

PR #902 只修复 PR #890 审查项 1–6、9，并补充第 10 项的正常解释器退出
smoke test。磁盘缓存性能、统一 surface/RAW 内存预算、历史 PR 的组织方式，以及
后续分支 promotion 策略均未在该 bugfix PR 中改动。

本台账用于后续独立实施，不得把下列事项误报为 PR #902 已完成。实现时应先取得
可重复基线，再进行所有权或存储结构调整；不要在没有 profiler、RSS 和 I/O 证据的
情况下同时重写缓存、RenderSession 和 RAW 解码链路。

## 技术债务总览

| ID | 优先级 | 债务 | 当前风险 | 状态 |
| --- | --- | --- | --- | --- |
| TD-890-01 | P1 | 磁盘 neutral-surface cache 的命中、写入和 prune 为高线性成本 | PR #903 recovery/lifecycle 收口已完成本地验证，最终 head 三平台证据待补 | `in_progress` |
| TD-890-02 | P1 | CPU/mmap/RenderSession/GPU/RAW 缺少统一字节所有权 | 只观测 tracker 已接入；预算执行与 lease 迁移仍未开始 | `in_progress` |
| TD-890-03 | P2 | Windows Pets production-shape 合同超过 30 分钟 job 上限 | PR #902 CI 中唯一非成功项，无法提供稳定的三平台 50k×384 证据 | `not_started` |
| TD-890-04 | P2 | stacked branch 到 `edit-base`/`main` 的 CI promotion 策略未闭环 | 当前只保证本阶段 base/head 触发，向前合并后的同 SHA 证据仍需人工组织 | `not_started` |
| TD-890-05 | P3 | 大型跨子系统 PR 的回滚和归因边界不足 | 历史 PR 无法安全追溯拆分；未来同类变更仍可能形成不可独立回滚的组合 | `process_debt` |

## TD-890-01：磁盘 surface cache 的线性成本

### 当前证据

主要代码位于 `src/iPhoto/gui/detail_surface_cache.py`：

- `NeutralSurfaceStore.load()` 在返回 mmap-backed `QImage` 前对整个 RGBA payload
  计算 checksum。该校验会读取并预 fault 所有映射页，因此 cache hit 仍为
  `O(surface_bytes)`。
- `NeutralSurfaceStore.write()` 使用 `bytes(image.constBits()[...])` 创建完整 payload
  副本，再写临时文件并原子 replace。写入期间至少多持有一份 surface 大小的
  Python bytes。
- 每次成功写入都会调用 `prune()`；`prune()` 遍历、stat 并排序全部
  `*.ipsurface`，单次写入成本包含 `O(N log N)` 的目录维护。
- 当前磁盘预算按可用空间的 2% 计算并上限为 2 GiB，但预算只限制最终占用，
  不限制单次命中、写入和 prune 的延迟或瞬时内存。

### 目标设计

1. 建立持久 metadata index。可使用 SQLite 或具备事务/崩溃恢复语义的 manifest，
   至少记录 digest、size、last-access、decoder contract 和校验状态。
2. prune 改为按累计写入字节、时间窗口或超预算水位批量触发；普通单次写入不得
   全目录扫描和排序。
3. 保持临时文件 + 原子 replace，改用 buffer protocol、分块写入或其他无完整
   Python bytes 副本的路径。
4. 完整 checksum 保留在首次写入、异常恢复、抽样审计或不可信 entry 路径；
   正常命中策略必须有明确的损坏检测和 decoder-contract 失效协议，不能简单删除
   完整性校验。
5. last-access 更新采用批量刷新，避免每次命中的 metadata/fsync 放大。

### 验收门禁

- 新增 cold hit、warm hit、write、prune 的基准，至少覆盖 16 MiB、64 MiB 和接近
  当前最大 surface 的样例。
- 普通 cache hit 不再无条件完整扫描 payload；若仍扫描，必须用目标平台数据证明
  不影响纹理上传关键路径。
- 单次写入的额外 heap 峰值不随 surface 大小增加一份完整 payload；测试应记录
  RSS/分配峰值，而不只检查返回值。
- 未到 prune 水位时，连续写入的目录遍历次数为常数；超预算回收仍满足 LRU 或
  明确定义的近似 LRU 语义。
- 覆盖 crash 后临时文件、metadata 与 payload 不一致、checksum 错误、decoder
  contract 提升和同路径 source revision 更新。
- Detail cold/warm benchmark 不退化，跨平台 shutdown 不遗留 cache executor 或
  打开的 mmap/file owner。

### 当前实施证据

- `codex/td-890-surface-cache-index` 将 namespace 提升到 v3，并以 SQLite
  `index.sqlite3` 持久化 entry、LRU、checksum trust 和维护水位；可信命中不再调用
  payload checksum。
- 写入改为 4 MiB buffer 分块、增量 xxhash、fsync 和原子 replace，不再创建完整
  Python payload 副本；last-access、异常恢复、抽样审计和低水位 prune 均有独立合同。
- `detail-surface-cache-contract` 在同一 runner checkout 固定 `fe623e68` 基线与候选
  head，覆盖 16/64/180 MiB 并上传原始 JSON。此前实现 SHA `034df643` 的
  [Actions run 31339926157](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/actions/runs/31339926157)
  已在 Ubuntu、macOS 和 Windows 通过；PR #903 当前 head 新增了 generation close
  barrier、失败恢复合同，以及 fresh-process/warm 的完整 mmap payload CPU 消费门禁。
- 本地同 runner 的 `fe623e68`/candidate 16/64/180 MiB comparison 已通过；该指标只
  证明完整 CPU payload 消费，不声明等价于真实 GPU upload。最终 head 三平台 artifact
  与 GPU-first 合同完成前，本项暂为 `in_progress`。
- 最后用户版本 v6.6.8 不包含 neutral-surface 持久化格式；兼容路径为“无旧 cache →
  初始化 v3”，不读取或迁移开发阶段 v2。

## TD-890-02：统一 surface/RAW 内存所有权

### 当前证据

当前至少存在四套相互独立的保留策略：

1. `MappedSurfaceCache` 有 128–512 MiB 的 CPU/mmap LRU 预算。
2. `PhotoRenderSessionHandle._surfaces_by_key` 强引用该 session 获得过的所有 LOD，
   没有字节预算；`PlayerViewController` 最多保留三个 session，但只按数量淘汰。
3. `TextureManager` 和 `RhiImageRenderer` 各自使用固定 192 MiB GPU still residency
   预算，与 CPU/session 预算没有统一计费或 backpressure。
4. RAW full decode 会先创建完整 demosaic/RGB ndarray，再转换为 `QImage`，之后才
   缩放到 target surface；最终 192 MiB 限制不覆盖这些中间数组。

因此 surface 从 `MappedSurfaceCache` 淘汰后，仍可能被 RenderSession、当前
presentation、pending upload 或 GPU staging 间接保留。当前 `budget_bytes` 不能代表
进程真实 RSS 上限。

### 目标设计

1. 定义单一的 `SurfaceOwner`/`SurfaceLease` 协议，统一记录 source key、LOD、CPU
   heap bytes、mmap bytes、upload staging bytes、GPU estimated bytes 和引用 owner。
2. RenderSession 优先保存 key/lease，不无限强引用所有 `DecodedSurface`。高 LOD
   成功后，根据当前 viewport、Edit baseline 和回退需求主动释放不再需要的低 LOD。
3. 将 current、Edit、prefetch、memory cache 和 upload queue 的优先级写入统一
   eviction policy；正在编辑的 baseline 可以 pin，但 pin 必须计费且有上限。
4. Library rebind、session eviction、Edit Done/Cancel 和 renderer teardown 必须显式
   release owner，并可通过诊断快照证明没有旧 library lease。
5. RAW decoder 在选择 embedded/half/full candidate 前申请中间内存额度。无法满足时
   应降级到 preview/half-size、tile 或明确失败，不能先分配后再检查最终尺寸。
6. 物理内存比例、最低/最高值和 GPU 预算应由一个 runtime policy 生成；平台后端
   可以有不同上限，但不能继续使用互不知情的固定常量。

### 建议的分阶段实现

1. **观测阶段**：只增加 owner/bytes 诊断事件和 benchmark，不改变淘汰行为。
2. **CPU/session 阶段**：统一 memory cache 与 RenderSession LOD 计费，验证 Edit
   baseline、Live Photo still restore 和相邻预取行为。
3. **RAW 阶段**：加入 decode reservation、降级策略和超大 RAW 峰值测试。
4. **GPU 阶段**：把 GL/QRhi residency 与统一 policy 对接，保留后端自己的资源销毁
   线程约束。

不要把四个阶段压入一个不可回滚提交。每个阶段都必须保持现有 generation、
library epoch、source identity 和 Edit handle 生命周期合同。

当前首批只完成观测阶段：`SurfaceResidencyTracker` 区分唯一资源和 owner 引用，记录
CPU heap、mmap、upload staging、GPU estimated 与 RAW intermediate 字节；它不拥有资源、
不拒绝分配，也不改变现有 LRU/session/GPU 行为。后续 CPU/session PR 才引入
`SurfaceOwner`/`SurfaceLease` 执行合同。

### 验收门禁

- 构造三个 session、每个包含 preview/2K/4K/full LOD 的压力场景，断言总计费、
  淘汰顺序和实际 RSS 都在“配置预算 + 明确的 bounded overhead”内。
- 大 RAW 测试同时记录 demosaic ndarray、QImage、缩放结果和上传 staging 的峰值；
  不能只断言最终 surface 尺寸。
- Edit active session 在预算压力下保持 baseline 可恢复；非 active LOD 可回收。
- Live Motion → still restore、相邻预取、快速切图、切库和关闭应用后，旧 lease 数量
  归零且不会使用已释放 mmap/GPU resource。
- GL 和 QRhi 后端执行同一 ownership 合同测试；平台特定 GPU 销毁仍在合法线程。
- 性能基准证明统一计费没有把 warm Detail 打开或 Edit slider 延迟推离现有门禁。

## TD-890-03：Windows Pets production-shape 合同超时

### 证据

PR #902 的 [GitHub Actions run 31333850127](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/actions/runs/31333850127)
共有 12 个检查：11 个成功，唯一非成功项是
[`pets-production-shape-contract (windows-latest)`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/actions/runs/31333850127/job/93296293595)。

该 job 在 `tests/contracts/test_pets_scale_contract.py` 的 50k×384 production-shape
路径运行约 27 分 36 秒后，被 30 分钟 job timeout 注入 `KeyboardInterrupt`；日志为
`1 skipped in 1656.66s` 和 `The operation was canceled`，没有测试断言失败。相同 run
中的 Ubuntu/macOS production-shape、Windows startup、Windows Detail 和常规测试均
成功。PR #902 没有修改 Pets repository 或该合同实现，因此未在 bug-only PR 中通过
放宽 timeout、缩小数据规模或跳过 Windows 来制造绿灯。

### 后续计划与验收

1. 在 `_benchmark_growth()` 增加分阶段耗时和进度采样，区分 SQLite mutation、
   USearch add/rebuild、restart、merge/delete 和 RSS probe。
2. 使用 Windows profiler/ETW 或等价工具确认 50k batch-16 增长的主热点，并与同一
   runner image 上的 Ubuntu/macOS 结构对比。
3. 优先修复算法或 I/O 放大；只有在有稳定 P95 数据证明工作量合理且不可再优化时，
   才单独评估 job timeout。
4. 不得通过减少 50k、降低 384 维、禁用 USearch/fallback 或把取消标为成功来关闭
   本项。
5. 完成标准为三平台 production-shape 在同一 commit SHA 上成功，Windows 至少连续
   两次低于 job 上限并输出完整 metrics；独立 4 维快速合同继续保留。

## TD-890-04：CI promotion 与分支触发策略

PR #902 当前只保证 `codex/startup-chain-optimization` 阶段的 pull request 和 push
触发。`edit-base`/`main` 的向前合并、同一或组合 SHA 的重新验证，以及 stacked PR
解除后的最终 required-check 策略未在本轮修改。

后续应为每个实际 promotion base 明确：

- `pull_request.branches` 匹配 base branch，不把 head branch 写成 base filter；
- push 后哪些平台合同必须重新运行；
- stacked PR 合并后以 merge SHA、head SHA 还是发布 artifact SHA 作为证据；
- required checks 的稳定名称，以及取消/超时是否阻止 promotion；
- `workflow_dispatch` 只作为补充证据，不能替代正常 PR/push 触发。

完成标准是在 `edit-base` 和最终 `main` promotion 上各取得一次由正常事件触发的完整
成功 run，并把 run URL、commit SHA 和 artifact/build manifest 对应关系回填到现有
工程关闭文档。

## TD-890-05：未来大型 PR 的可回滚性

PR #890 历史上同时覆盖 startup、Detail、Edit、Live Photo、Pets、cache、CI 和 legacy
清理。该历史提交图不在本轮追溯拆分：强制拆分会重新组合已经联调的生命周期合同，
风险高于当前收益。

未来变更采用前向规则：

- 一个 PR 只拥有一个主要 runtime 生命周期或存储迁移边界；
- 跨 PR 依赖通过明确 base、handoff 文档和可单独执行的合同测试表达；
- schema/数据迁移与行为切换提供独立回滚或向前恢复策略；
- cache、renderer、Edit session 和 startup 不在同一提交中同时更换 owner；
- PR 正文记录可独立回滚的 commit、风险开关和验证 SHA；
- stacked PR 的每一层都必须实际触发 CI，不以更高层组合 run 代替底层证据。

此项是工程流程债，不以重写 PR #890 历史为完成条件。后续连续的大型改造按上述规则
执行并形成可独立回滚记录后，可将状态改为 `adopted`。

## 推荐实施顺序

1. TD-890-01/02 的观测与基线：建立 I/O、延迟、RSS、owner/lease 数据。
2. TD-890-03：定位 Windows 50k×384 热点，使当前 CI 证据稳定。
3. TD-890-01：先处理 prune 与写入复制，再评估命中校验策略。
4. TD-890-02：按 CPU/session → RAW → GPU 顺序迁移统一所有权。
5. TD-890-04：在向 `edit-base`/`main` promotion 前闭环触发和 required checks。
6. TD-890-05：作为后续所有大型改造的持续交付规则执行。

## 关闭规则

- 每项只能更新为 `in_progress`、`automated_pass`、`manual_pending` 或附证据的
  `not_applicable`，不得仅凭“代码已提交”标记完成。
- benchmark 必须保存平台、物理内存、Python/PySide/Qt、runner image、commit SHA、
  fixture 规模和原始 metrics。
- 技术债修复不得削弱 PR #902 已建立的 Live Photo surface、library epoch/token、
  Edit rebind、startup owner-thread、bounded shutdown 和 stable source identity 回归。
- 全部自动化和真实平台证据闭环后，再把本文移动到
  `docs/finished/requirements/`；未完成前保留在当前目录。

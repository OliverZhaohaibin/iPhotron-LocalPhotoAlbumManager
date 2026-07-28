# Pets 合并前审查修复台账

更新日期：2026-07-28

## 状态与证据边界

- 合并基线：`edit-base` / `6ff592f72a6a4fd8575d5bd392e035dd2a95a12a`
- 修复分支：`codex/pets-review-remediation`（目标分支：`codex/startup-chain-optimization`）
- 审查结论：`REQUEST_CHANGES`，保持 Draft
- 当前工程状态：`automated_core_pass / release_artifact_and_manual_validation_pending`
- 修复前定向基线：Pets、People repository、Recognition merge/query 共
  `85 passed, 1 warning`
- 完成规则：每一项只能进入 `automated_pass`、`manual_pending`，或附有可复核
  证据的 `not_applicable`；不得用“代码已写”替代验收证据。

本台账是本轮修复的第一检查点。它完成前不得修改生产代码；之后的实现、测试
和人工验收都必须回填到这里。本目录在全部人工证据闭环前不得移入
`docs/finished/requirements`。

## 2026-07-27 二次审查检查点

审查附件：`e65678cb-e242-462b-9573-badc6f933d3d/pasted-text.txt`。审查已确认
`#884` 从未上线，因此不为未发布的 v4 Pets 数据提升 detector pipeline version，
但重新打开下列首发版本可达的一致性、身份和验证问题。下表完成并进入
`automated_pass` 前，之前对应条目的自动化结论不再作为合并证据。

| ID | 当前复现证据与代码位置 | 确定性处置 | 验收要求 | 状态 |
| --- | --- | --- | --- | --- |
| R2-01 | `pets/repository.py::replace_assets_incrementally` 先提交 runtime，之后才调用 state sync；`pets/index_coordinator.py` 会在 state 失败时删除已经发布的缩略图并 finalize error。 | runtime 事务内写 operation commit marker；marker 存在时只允许幂等向前恢复 state/outbox/event，禁止回滚已提交 runtime 和文件。 | `test_runtime_commit_marker_recovers_state_without_deleting_published_thumbnail`、原有 asset-status/outbox 故障回归及全量测试通过。 | `automated_pass` |
| R2-02 | `_assign_incremental_pet_ids` 在 exact key 命中后仍要求 ID 存在于 runtime centers，否则生成新 UUID。 | exact v2 key 优先恢复 durable `pet_id`；新 generation 只重建 embedding profile，保留 name/hidden/cover。 | `test_exact_key_resurrects_durable_identity_and_user_state` 通过。 | `automated_pass` |
| R2-03 | `RecognitionAnnotation.kind` 同时承担 source detection 和 canonical identity；overlay 编辑使用 source 名称/ID，删除和移动按 canonical kind 路由。 | DTO 分离 source/canonical；identity mutation 使用 canonical，detection mutation 使用 source；跨类型单框归属保存 detection-level assignment。 | 双向跨类型 assignment、overlay 初值、canonical rename、source delete 和 playback 路由回归通过。 | `automated_pass` |
| R2-04 | `activate_embedding_generation` 通过三次独立 metadata commit 激活 generation/version/dimension。 | generation contract 和 detector/clustering metadata 单事务激活并可幂等恢复。 | `test_generation_contract_is_reused_and_activated_in_one_transaction` 通过。 | `automated_pass` |
| R2-05 | merge/move/recluster 未校验 generation、version、dimension，可能异常或静默混合 feature space。 | 所有人工和自动聚类操作验证完整 embedding contract；不兼容时明确 rejected。 | `test_generation_contract_rejects_cross_space_merge_and_move` 及 active-generation recluster 回归通过。 | `automated_pass` |
| R2-06 | persisted boundary embeddings 未进入匹配上下文；回退查询取最低质量样本而非离中心最远样本。 | 直接使用 profile boundary；legacy 数据一次性批量补算最远 8 个样本。 | `test_persisted_boundary_samples_are_used_without_candidate_sql` 及 legacy farthest-8 回归通过。 | `automated_pass` |
| R2-07 | model resolver 按目录/文件存在选 root，空 cache、损坏 cache 和不完整 bundled artifact 会遮挡有效 fallback 或触发只读写入。 | 按完整且 hash/size/shape 有效的 artifact 选择；只下载到用户 cache；损坏 cache 自愈。 | 空 cache、损坏 cache、完整 bundled 和 hash 校验回归通过；真实只读安装仍在人工矩阵。 | `automated_pass` |
| R2-08 | state repository 多个 caller-sized `IN` 未分块，50k 测试未传 `pet_state.db`。 | 所有可变 `IN` 统一按 500 分块并增加定向 profile 查询。 | `test_state_repository_chunks_all_large_identity_reads` 覆盖 1,205 项；规模门禁覆盖真实 `pet_state.db` 的 50k。 | `automated_pass` |
| R2-09 | 每批加载全部 profiles 并重建 ANN；现有规模测试只预装 50k 后测 +2。 | session 级、按 contract/species 分区的增量 USearch index；只读取和更新受影响候选；初始、重建和 batch 16 更新均使用批量 `index.add`。 | `pets-scale-contract` 覆盖空库 batch 16 增长到 1k/10k/50k 及 50k+2，`1 passed in 65.08s`。 | `automated_pass` |
| R2-10 | journal recovery 仅在新扫描批次前触发，其他 mutation 可越过旧 applying 操作。 | 初始化和每个 public mutation 前按创建顺序恢复；失败时拒绝新 mutation。 | `test_public_mutation_cannot_overtake_unrecovered_journal_owner` 通过。 | `automated_pass` |
| R2-11 | `_publish_staged_thumbnails` 的逐文件 replace 没有内部补偿或预登记正式目标。 | publish 前 journal 记录清单；部分失败反向清理，进程恢复也能识别 orphan。 | `test_thumbnail_publish_compensates_when_later_replace_fails` 及 scan recovery 回归通过。 | `automated_pass` |
| R2-12 | DINO 仅固定 Torch Hub source revision，首次权重内容没有发布前 SHA。 | 生产运行时改用项目固定 Release TorchScript，manifest 固定 URL/SHA/size/shape；Torch Hub 仅保留开发转换工具。 | 已固定并在首次加载前验证官方 checkpoint SHA/size，生成 cache 复核 metadata/hash；仓库 `pet-models-v1` 不可变 TorchScript Release 尚未发布，生产 Torch Hub 路径尚不能删除。 | `manual_pending` |
| R2-13 | `.github/workflows/test.yml` 的 PR base 仅允许 `main`，当前 stacked PR 没有 head status。 | 同时允许 `main` 与 `codex/startup-chain-optimization`，增加手工触发，并为独立 Pets job 配置 Linux Qt headless runtime。 | `e0001ee646e95fadc33659ebe277eb067e79a084` 的 9 个 GitHub Actions job 全部成功：[run 30263442145](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/actions/runs/30263442145)。 | `automated_pass` |

二次审查的工程关闭上限为
`automated_remediation_complete / manual_validation_pending`。远端 CI、真实安装权限、
真实图库升级和跨平台 50k 证据未完成时继续保持 Draft / `REQUEST_CHANGES`。

## 2026-07-28 Recognition 并发与恢复审查检查点

审查附件：`04480d6f-5dfb-45c5-90d7-94504b86cdda/pasted-text.txt`。本轮六项
问题均复现并完成代码处置；其中 production-shape 远端三平台 CI 尚未取得成功
run，因此 PR 继续保持 `REQUEST_CHANGES`。

| ID | 复查结论与代码范围 | 已实施处置 | 自动化证据 | 状态 |
| --- | --- | --- | --- | --- |
| R4-01 | Pet backfill 已占用 Pet worker 时，原 `_start_ai_scan_workers` 同时跳过 Face/Pet，且 root 在启动成功前写入。 | Face/Pet 构造与启动改为独立幂等；部分失败清除对应 worker 并仅重试失败项；两个 worker 均满足后才激活 root。 | Qt 真事件循环 backfill→activate 与 Pet 首次启动失败重试测试通过。 | `automated_pass` |
| R4-02 | Pet retry stale/status、正式缩略图和 People done status 可在取得全局 journal lease 前或 outbox 前对外可见。 | Pet done/retry 同一 runtime transaction 写 detection/profile/commit marker；资产 status 在 runtime/state 后幂等推进；People 对称调整为 bookkeeping→outbox→dispatch。 | retry-only、done+retry 无 lease 零变更；asset status/state/outbox 故障向前恢复测试通过。 | `automated_pass` |
| R4-03 | stale 展示后缀被写入 `canonical_display_name`，Enter 可污染持久名称。 | adapter 永远保留纯 canonical name；overlay 只在绘制阶段由 `is_stale/stale_reason` 拼接后缀。 | 有名/无名 stale editor 初值、Enter 提交和 adapter 契约通过。 | `automated_pass` |
| R4-04 | People/Pets/merge/assignment 分散打开 journal、按 kind 自行恢复，状态更新无 CAS。 | 新增 session-owned `RecognitionMutationCoordinator`；生产源码仅该 owner 打开 journal；active/legacy typed handler registry 严格 FIFO；未知 kind 阻塞；状态 CAS、原位 outbox migration、稳定 event id、统一 commit-and-dispatch；People rename 纳入 durable operation。 | FIFO、未知 kind、两实例 admission/CAS、legacy schema、dispatch crash 重放、LibrarySession 单 owner/全 kind registry 和架构门禁通过。 | `automated_pass` |
| R4-05 | Pet merge bool 丢失临时失败语义，UI 全部显示业务拒绝。 | `PetMergeOutcome/PetMutationFailure` 区分 rejected、recovery pending、shutdown；跨 identity 映射为 `IdentityMergeFailure.RECOVERY_PENDING`；UI 使用独立 busy 文案。 | 业务拒绝、busy journal、recovery pending、shutdown 和 UI 信息分支测试通过。 | `automated_pass` |
| R4-06 | 原 50k 契约仅 4 维，未覆盖生产 384 维冷启动与 mutation。 | 保留 4 维 PR job；新增 `50k × 384` USearch 与 `1k × 384` fallback，覆盖 batch 16、冷重启精确匹配、增量、merge/delete、时间/RSS/WAL；手工 CI 扩展 Linux/macOS/Windows matrix。 | 本机 USearch：build `94.00s`、cold restart `7.04s`、peak RSS `1,153,368,064 B`、incremental WAL `0 B`、mutation `12.05s`；fallback `1k × 384` 通过；远端三平台 run 待补。 | `manual_pending` |

本轮本地证据：Recognition/Pets/People/UI 定向 `354 passed, 1 warning`；架构
`23 passed`；原 4 维 50k PR 契约 `1 passed in 71.89s`；384 维 USearch
`1 passed in 114.97s`；fallback `1 passed in 3.89s`。全量、compileall、Ruff 与
diff 检查通过，全量为 `2806 passed, 16 skipped, 10 warnings`。只有远端
`pets-production-shape-contract` 三平台
成功并回填 run URL 后，R4-06 才能改为 `automated_pass` 并重新评估合并状态。

## 2026-07-28 Recognition 二次并发审查检查点

PR #888 二次审查确认了两个遗漏的合并阻断项，并指出 stacked PR base 未被 CI
监听。本轮已在本地完成处置；远端 workflow run 仍须以推送后的 GitHub 结果为准。

| ID | 复查结论与代码范围 | 已实施处置 | 自动化证据 | 状态 |
| --- | --- | --- | --- | --- |
| R5-01 | operation 进入 `applying` 后原 coordinator 锁即释放，另一个线程可把仍在正常 apply 的 head 当成崩溃残留恢复。 | 每个 resolved library root 增加进程级可重入 lifecycle lease；People、Pets、merge 与 assignment 从 recovery/admission 一直持有到 commit/finalize；所有 coordinator 实例共享同一 lease，崩溃后由新进程自然取得并恢复。 | 两个独立 owner 并发测试证明 active owner 持有 lease 时 recovery 线程阻塞、handler 未执行；active owner finalize 后 recovery 仅观察到空队列。 | `automated_pass` |
| R5-02 | `PeopleService.set_cluster_hidden` 直写 repository，可在 merge hidden 检查与 operation admission 之间插入。 | People hidden 改由 `PeopleIndexCoordinator` 执行，先通过统一 recovery gate，再在共享 lifecycle lease 内完成单 DB mutation；merge 从 hidden read 到最终 mutation 全程持有同一 lease。 | hidden 路由契约与真实线程交错测试通过；hidden writer 在 merge 完成前不能进入临界区。 | `automated_pass` |
| R5-03 | PR #888 base `codex/pets-review-remediation` 不在 workflow 的 `pull_request.branches`，且 384 维 job 仅允许手工触发。 | workflow 增加 stacked base；production-shape job 在 pull request 与手工触发时均运行 Linux/macOS/Windows matrix。 | workflow 静态配置已更新；远端 run 待推送后核验。 | `remote_pending` |

首次自动 run `30365797816` 已证明 workflow 触发修复生效：常规 test、50k 快速
契约、模型契约、startup 与 GPU-first jobs 全部通过，Linux/macOS 384 维通过。
Windows 在完成约 16 分钟 benchmark 后仅因 ctypes 未声明 64 位 Win32 API 签名，
`GetProcessMemoryInfo` 收到截断 handle 而失败；已显式声明 `HANDLE`/pointer/DWORD
参数与 BOOL 返回值，并将无跨平台时间 SLA 的 production matrix job 余量调为
30 分钟。修复后的 Windows run 仍待成功证据，R5-03 状态不提前提升。

第二次自动 run `30367483000` 已取得 Linux/macOS/Windows 三平台 384 维成功
证据；Windows job `90302358960` 完整通过，证明 Win32 RSS 读取修复有效。该 run
的独立 4 维快速契约暴露了既有 fixture 的 ANN 非确定性：两个相邻超密向量可合法
命中同一 nearby profile，却硬编码要求两个 distinct updated IDs。fixture 已改为
图库首尾跨度内的两个远距离 probe，保持“两次增量、无新增 identity”的结构门禁，
同时不把近邻近似召回误判为写入失败。完整 run 仍须在该修复推送后再次全绿。

本轮本地证据：新增/关联定向 `161 passed, 1 warning`；全量
`2809 passed, 16 skipped, 10 warnings`；架构门禁、`compileall`、变更范围 Ruff
`F/E9/I` 与 `git diff --check` 全部通过。PR 在 R5-03 远端常规 CI 与三平台
production-shape job 全绿前继续保持 No-Go，并按 stacked 顺序先合并 #888、再在
#887 组合 head 上重跑后合并 #887。

## 2026-07-27 Windows Live Photo 静态图方向回归检查点

Windows 实机样例 `IMG_3684.HEIC` 已确认动态视频方向正确，错误仅位于
静态 HEIC。前一提交 `4e522e60` 对 Live Motion 视频投影的修改与实机证据
不符，本轮必须完整反向恢复，不作为最终修复的一部分。

| ID | 当前复现证据与代码位置 | 确定性处置 | 验收要求 | 状态 |
| --- | --- | --- | --- | --- |
| R3-01 | `detail_decode_windows.py::WindowsWicStillDecodeBackend.decode` 无条件按索引 `image_orientation` 调用 `_apply_orientation`。样例 EXIF Orientation=6，索引展示尺寸为 3024×4032，WIC frame 也已是 3024×4032；再旋转会变成错误的 4032×3024。 | 只对会交换宽高的 EXIF 5/6/7/8 做严格尺寸判定：WIC frame 已精确匹配索引展示尺寸时认为 WIC 已转正，跳过二次旋转；frame 仍是原始横向尺寸时继续应用 EXIF。Orientation 2/3/4、方形图和无效尺寸不作猜测。 | `test_windows_wic_preoriented_frame_detection_is_strict` 5 组边界及 `test_windows_wic_decode_avoids_only_proven_double_orientation` 2 组 decode 分支通过；Detail/Live Photo/metadata 回归 `330 passed, 1 skipped`，全量 `2763 passed, 14 skipped`。Windows packaged 真实 HEIC+MOV 保留人工验证。 | `automated_pass` |
| R3-02 | `detail_surface_cache.py::_canonical_key` 的 neutral-surface 解码契约仍为 `1`。当前链路在 WIC 前先读取 `.iPhoto/cache/detail-surfaces/v2`；修复前已由 WIC 二次旋转写入的错误横图仍使用相同 key 命中，因此 R3-01 的新鲜解码修复会被完全绕过。`v6.6.8` 没有这层持久化 Detail surface，始终经 Qt `QImageReader.setAutoTransform(True)` 重新解码。 | 将“磁盘文件格式 schema”和“像素解码语义 contract”分离；只提升解码 contract，使旧错误 surface 逻辑失效并触发一次重新解码，不删除图库文件，也不扩大 Live Motion 视频投影或其他 EXIF 规则。旧不可达缓存由既有容量 prune 回收。 | Windows 用户对原问题样例复测确认已解决。不可弱化回归 `test_live_photo_decoder_contract_rejects_legacy_wrong_orientation_surface` 同时锁定生产 contract `>=2`、旧 key miss、delegate 重解码和新竖图磁盘回读；WIC fresh-decode 测试旁已添加禁止删除说明。定向链路 `233 passed, 1 skipped`，完整 Detail 门禁 `87 passed, 1 skipped`，全量 `2764 passed, 14 skipped`。正式 packaged artifact 证据仍按人工矩阵补齐。 | `automated_pass` |

## 处置原则

1. 事实与审查建议分开裁决。确认的问题必须修复；建议与上游模型契约冲突时，
   以固定版本上游证据和真实模型测试为准。
2. 扫描事实属于可重建状态，名字、封面、隐藏、拒绝、合并与 group 属于 durable
   state，任何 generation 切换不得静默丢失后者。
3. 跨数据库写入使用可恢复的 command journal 和幂等向前恢复；事件只能在
   bookkeeping 完整后发布。
4. 大图库门禁同时检查算法结构、写入放大、WAL、内存、取消延迟和增量成本。

## 自动化证据（2026-07-27）

- 全量：`2756 passed, 14 skipped`。跳过项包含由独立 PR job 强制执行的真实模型与
  规模契约，以及当前主机不适用的 Windows-only 测试。
- `pets-model-contract`：`1 passed`。固定模型/图片 SHA，CPU provider，raw-BGR
  输入、dog 类别、bbox、置信度与最终去重全部通过。
- `pets-scale-contract`：`1 passed in 65.08s`。覆盖空库按 batch 16 增长到
  1k/10k/50k 与 50k+2，满足
  结构、时间、RSS、WAL 和增量阈值。
- 架构门禁脚本三项检查通过；Pets/AppImage 静态打包 `2 passed`；`compileall`、
  变更范围 Ruff `F/E9/I`、`git diff --check` 均通过。
- 故障恢复证据覆盖 scan asset-status commit 失败后的重开向前恢复、operation
  finalized/outbox 时序、缩略图第二框失败清理；跨平台真实安装与全提交点进程级
  kill/restart 仍按人工矩阵保留，不据此宣称全平台完成。

## P0 阻断项

| ID | 当前证据与裁决 | 确定性处置 | 验收证据 | 状态 |
| --- | --- | --- | --- | --- |
| P0-1 | **部分成立**。当前只有 mock 输出测试，真实模型契约未锁定；但 YOLOX `0.1.1rc0` 发布说明明确移除了 mean/std normalization，因此不能照审查建议加入归一化。当前还需锁定官方部署示例使用的 raw BGR、`float32`、`0-255`、CHW 契约。 | 引入模型 manifest；默认预处理固定为 raw BGR；新增固定 SHA 的真实模型/图片 PR 门禁。 | 官方 `yolox_nano.onnx` SHA-256 `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d`；官方 dog 图片 SHA-256 `5a9522051c3cec2bbd2f6323fccba32e8fbf3ddcc2b3e2fd46b04c720bc6f866`；`test_official_yolox_raw_bgr_model_contract` 通过。 | `automated_pass` |
| P0-2 | **确认**。`BATCH_SIZE=2`，每批读取全部 detection、创建完整距离矩阵、完整聚类并 `replace_all`。 | 改为有界批次、按物种 ANN profile 候选、有限边界样本复核和受影响行增量更新；删除扫描路径全表重写。 | `test_incremental_commit_scales_to_50k_without_full_rewrite` 通过；50k+2 满足时间/RSS/WAL 门禁。 | `automated_pass` |

上游依据：[YOLOX 0.1.1rc0 发布说明](https://github.com/Megvii-BaseDetection/YOLOX/releases/tag/0.1.1rc0)。

## P1 合并前修复项

| ID | 当前证据与裁决 | 确定性处置 | 验收证据 | 状态 |
| --- | --- | --- | --- | --- |
| P1-1 | **部分实现**。打开 People 且首 viewport ready 后可 drain pending，但升级用户不打开 People 时不会自动运行。 | migration/backfill marker；interactive 后启动低优先级 closed-input Pet worker，普通新库仍首用启动。 | 升级库自动调度与新库不提前启动回归测试通过。 | `automated_pass` |
| P1-2 | **确认**。封面只验证 detection 存在，不验证 pet ownership。 | 请求 pet 和 detection pet 都解析 canonical redirect，只有二者相同才允许。 | `test_unknown_mutations_and_cross_pet_cover_are_rejected` 及 redirect/merge 回归通过。 | `automated_pass` |
| P1-3 | **确认**。当前 key 不含 species，也没有 key version。 | 使用 `v2:<sha256>`，输入包含 asset、尺寸、量化 bbox、species、detector key version；迁移 identity 映射但不复制 v1 rejection 到新物种。 | species key 与 v1 rejection migration 测试通过。 | `automated_pass` |
| P1-4 | **确认**。默认下载目标位于代码/安装包内部。 | 分离 override、用户 cache、bundled root；只下载到用户 cache。 | cache/只读逻辑与静态打包测试通过；三平台真实只读安装目录仍待实机。 | `manual_pending` |
| P1-5 | **确认**。detector upgrade 把全量 ID 放入一个 `IN`。 | repository 条件 UPDATE；通用批量 ID 每 500 个分块；Face 同类接口一并加固。 | 超 SQLite bind limit 的 conditional reset/bulk update 测试通过。 | `automated_pass` |
| P1-6 | **确认**。缩略图在资产成功前写入正式目录，失败/取消会泄漏。 | `.staging/<operation_id>`、manifest、finalize/cleanup/recovery。 | 第二 bbox 失败回滚、取消与 stale staging 清理测试通过。 | `automated_pass` |
| P1-7 | **确认**。merge/delete/cross-kind mutation 横跨 pet/face runtime/state DB，无统一恢复协议。 | `.iPhoto/recognition/operations.db`，`prepared→applying→committed→finalized`，所有步骤幂等向前恢复。 | journal 状态机、scan commit 恢复、Pet mutation 与跨类型 merge 回归通过；真实进程 kill/restart 全提交点矩阵待人工采集。 | `manual_pending` |
| P1-8 | **确认**。snapshot event 先于 asset done bookkeeping。 | journal/outbox；runtime/state、文件和 asset status 完成后才提交并发布事件。 | asset-status 故障时不发布、恢复后 finalized 的测试通过。 | `automated_pass` |

## P2 重要风险

| ID | 当前证据与裁决 | 确定性处置 | 验收证据 | 状态 |
| --- | --- | --- | --- | --- |
| P2-1 | **确认**。记录了 embedding model 名称，但没有 pipeline generation 隔离。 | detection/profile 增加 embedding version 与 generation；不同版本禁止混合，active generation 原子切换。 | embedding dimension change generation 测试通过。 | `automated_pass` |
| P2-2 | **确认**。People-priority geometry 读取过滤 redirected person。 | detector 使用未经过 UI identity 过滤的原始 face geometry。 | raw auto/manual face geometry 与跨类型 redirect annotation 回归通过。 | `automated_pass` |
| P2-3 | **部分实现**。pet→pet annotation 已解析同类 redirect，但跨类型仍不对称。 | annotation 保留 source detection kind/ID，并增加 canonical identity kind/ID/name。 | person→pet、pet→person 均断言 source 与 canonical kind/id/name。 | `automated_pass` |
| P2-4 | **确认**。Pets `min_samples`、`prefer_hdbscan` 被直接丢弃，旧 HDBSCAN 代码不可达。 | 删除 Pets 无效参数、实现和 HDBSCAN 依赖；People 参数不变。 | Pets API、依赖和打包文档静态检查及全量回归通过。 | `automated_pass` |
| P2-5 | **确认**。事件只从新 snapshot 的本批 detection 计算 changed pet。 | 计算 old/new diff，事件包含 added/updated/removed 及兼容并集。 | `test_incremental_event_reports_removed_pet_and_journal_finalizes` 通过。 | `automated_pass` |
| P2-6 | **确认**。任意全图 supported box 会完全跳过 tile。 | 大图选择最多 4 个未覆盖面积最大的 tile，再统一 NMS。 | uncovered-tile 大狗场景和最多四 tile 断言通过。 | `automated_pass` |
| P2-7 | **确认**。第二层 dedupe 在 IoU 0.65 跨 species suppression。 | 0.65 仅同类；跨类只在 IoU≥0.90 且分差≥0.25 时压制低分项。 | 同类/跨类重叠策略测试及真实模型最终去重通过。 | `automated_pass` |
| P2-8 | **审查后已部分修复**。当前已有 `get_asset_ids_by_pets`、批量 global asset 验证和 pet summary 复用。 | 增加 request-scoped redirect/read context 和 SQL query-budget 回归，不恢复逐 pet 查询。 | request context 单次读取测试及 10→1000 constant query-budget 测试通过。 | `automated_pass` |
| P2-9 | **确认**。Pet pipeline 对 resolved path 没有 root containment 检查。 | People/Pets 共用 `resolve_library_asset_path`，拒绝绝对路径、`..` 和 symlink escape。 | traversal、绝对路径、symlink escape 测试通过。 | `automated_pass` |
| P2-10 | **确认**。下载只检查非空，不固定 hash/大小/协议。 | manifest 固定 URL/hash/大小/shape；仅 HTTPS；custom URL 必须带 SHA；缓存载入复核。 | manifest、SHA/size/HTTPS/shape 与真实模型契约通过。 | `automated_pass` |
| P2-11 | **确认**。普通 rebind 取消 worker 但不等待，也没有 recognition generation token。 | generation/root token；retiring worker 保留引用直至退出，迟到回调丢弃；仅 shutdown bounded wait。 | worker retiring、旧 generation status 丢弃、非阻塞切库测试通过。 | `automated_pass` |

## P3 收口项

| ID | 当前证据与裁决 | 确定性处置 | 验收证据 | 状态 |
| --- | --- | --- | --- | --- |
| P3-1 | **确认**。字符串 `live_role="1"` 不会跳过。 | Face/Pet 共用 tolerant integer parser。 | shared tolerant integer parser 测试通过。 | `automated_pass` |
| P3-2 | **确认**。unknown rename 返回成功，hide 可创建 ghost state。 | 解析 canonical ID 并验证 runtime/durable identity 存在；unknown 返回失败。 | unknown mutation 与跨 pet cover 测试通过。 | `automated_pass` |
| P3-3 | **确认**。升级失败时旧 detection 静默继续显示。 | generation carry-forward 必须记录 `is_stale`、reason 和 source generation，UI 明示。 | stale detection、source generation 与 UI adapter 回归通过。 | `automated_pass` |
| P3-4 | **确认**。accepted metric 在资产完成前累计且不回滚。 | 只在完整资产进入待提交结果后累计。 | `test_second_bbox_failure_rolls_back_thumbnails_and_metric` 通过。 | `automated_pass` |

## 实施阶段

1. 模型契约、模型存储/下载安全和图库路径边界。
2. schema/generation、增量索引、command journal、缩略图 staging 和 outbox。
3. 升级 backfill、identity/annotation 语义、worker generation 和 query context。
4. tile/dedupe/status/metrics 收口。
5. 真实模型、规模、迁移、故障注入、全量测试和人工矩阵回填。

## 人工验证保留规则

无法由当前主机自动证明的 packaged 权限、真实 GPU/provider、真实大图库、跨平台
安装位置和快速关窗/切库体验，最终写入 `MANUAL_VALIDATION_MATRIX.md`，并保留
artifact SHA、build manifest、模型 hash、脱敏 fixture 与日志位置。未执行即为
`pending_manual_validation`，不得写成 PASS。

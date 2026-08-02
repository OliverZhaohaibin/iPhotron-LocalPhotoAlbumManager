# PR #890 parity ledger

基线：`edit-base@deb14c05`（含 #899）。审计范围：共同祖先 `6ff592f7` 到 #890 head `59c96187` 的 105 个提交。

本表逐提交记录迁移归属；一个提交涉及多个行为域时会列出多个目标。当前 PR 1 只把 Detail 相关项标为“已迁移”；Recognition 与 coordinator 项保持“核对中”，直到对应纵向 PR 完成。最终关闭 #890 前，所有行只能收敛为“已迁移”或“被更新实现等价替代”。

统计：PR 1 触达 35 项；PR 2 待核对 32 项；PR 3 待核对 12 项。计数可重叠。

| # | #890 commit | 行为/变更 | 迁移归属 | 当前状态 |
|---:|---|---|---|---|
| 1 | `095ce6b6` | feat: harden and accelerate startup pipeline | PR1 Detail | 已迁移 |
| 2 | `865ddda9` | fix startup library filesystem watches | 现有 #892/#899 | 被更新实现等价替代 |
| 3 | `500d0872` | Harden startup library probing and migration recovery | 现有 #892/#899 | 被更新实现等价替代 |
| 4 | `143a07ba` | feat: schedule GUI startup jobs cooperatively | 现有 #892/#899 | 被更新实现等价替代 |
| 5 | `32b1ceb2` | feat: add startup benchmarking and AppImage packaging | 现有 #892/#899 | 被更新实现等价替代 |
| 6 | `277f91b4` | feat: add macOS startup detection report | 现有 #892/#899 | 被更新实现等价替代 |
| 7 | `306326ab` | refactor(gui): split desktop coordinator domains | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 8 | `7f430827` | Optimize startup chain and restore map clusters | PR3 Domains | 核对中（后续域待迁移） |
| 9 | `0b9b4f25` | fix(gui): harden media switching and navigation | PR1 Detail | 已迁移 |
| 10 | `5be2d91a` | Refactor local photo album management workflow | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 11 | `f6f56f2a` | Defer People scans and add staged still-image playback | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 12 | `b26f2eff` | Optimize recognition reads and detail playback | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 13 | `4d2844cc` | Harden startup migration recovery diagnostics | 现有 #892/#899 | 被更新实现等价替代 |
| 14 | `2fce80bf` | Improve local photo album management and sync flows | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 15 | `6c14584d` | Add v2 migration for cached video projection columns | 现有 #891–#899 | 被更新实现等价替代 |
| 16 | `5a389f2e` | Support entrypoint-based AppImage bundles and tighten library probe re | 现有 #892/#899 | 被更新实现等价替代 |
| 17 | `98005e0d` | update icon file | 现有 #891–#899 | 被更新实现等价替代 |
| 18 | `ba758ed7` | Improve Nuitka build Python and icon handling | 现有 #892/#899 | 被更新实现等价替代 |
| 19 | `fbbc7f9f` | Fix Windows PowerShell build path and Python probing | 现有 #892/#899 | 被更新实现等价替代 |
| 20 | `bc39d229` | Prevent Nuitka from discovering duplicate InsightFace Face3D sources | PR2 Recognition | 核对中（后续域待迁移） |
| 21 | `d9700e3b` | Include PyExifTool and Pillow-HEIF in Nuitka builds | 现有 #892/#899 | 被更新实现等价替代 |
| 22 | `3d3bbb1a` | Suppress known DINOv2 warnings and use target-local temp files | PR2 Recognition | 核对中（后续域待迁移） |
| 23 | `f2ac4007` | feat: deduplicate gallery detail image requests | PR1 Detail；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 24 | `3f7e5cbf` | fix: preserve detail scheduler request ownership | PR1 Detail | 已迁移 |
| 25 | `5d5a8d49` | Improve photo album management workflows | PR1 Detail | 已迁移 |
| 26 | `0849d624` | Implement local photo album management improvements | PR1 Detail；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 27 | `67b36902` | Refactor photo album management workflow | PR1 Detail；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 28 | `2b11351d` | Fix image orientation defaults and texture mipmap handling | PR1 Detail | 已迁移 |
| 29 | `5ba0f6d4` | Preserve crop interaction state and defer LOD updates | PR1 Detail | 已迁移 |
| 30 | `fd92d848` | Preserve transformed source pixels during crop preview | PR1 Detail | 已迁移 |
| 31 | `16ba3661` | Improve photo album management workflow | PR1 Detail | 已迁移 |
| 32 | `da427c6b` | Prevent stale thumbnails from returning after invalidation | 现有 #891–#899 | 被更新实现等价替代 |
| 33 | `86aa5091` | Refactor photo album management workflows | PR1 Detail | 已迁移 |
| 34 | `abac7792` | Retry scan thumbnail cache replacement on Windows file locks | 现有 #891–#899 | 被更新实现等价替代 |
| 35 | `d1e84be1` | Preserve live motion controls when presenting video | PR1 Detail | 已迁移 |
| 36 | `f2b4eb84` | Include source metadata in gallery projections | 现有 #891–#899 | 被更新实现等价替代 |
| 37 | `6140b39d` | Match benchmark completion signals to final detail transactions | PR1 Detail | 已迁移 |
| 38 | `71b64a03` | Use fixed-width signed HRESULT handling for Windows WIC | PR1 Detail | 已迁移 |
| 39 | `35357db5` | fix: cancel stale detail requests on library rebind | PR1 Detail | 已迁移 |
| 40 | `a226129e` | refactor: remove legacy application tree | 现有 #898 | 被更新实现等价替代 |
| 41 | `edf1e4d2` | Document GPU-first Detail rendering architecture | PR1 Detail；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 42 | `d898ce2d` | Refactor photo album manager | 现有 #898 | 被更新实现等价替代 |
| 43 | `9c9012a6` | Update GUI startup test for feature plan initialization | 现有 #892/#899 | 被更新实现等价替代 |
| 44 | `52cbef9e` | Stabilize aspect ratio orientation UI tests | 现有 #891–#899 | 被更新实现等价替代 |
| 45 | `2fc74194` | Simplify long-press preview tests with direct event simulation | 现有 #891–#899 | 被更新实现等价替代 |
| 46 | `954f34fc` | Stabilize Qt translation UI tests | 现有 #891–#899 | 被更新实现等价替代 |
| 47 | `b05602b7` | Keep QApplication alive for the full pytest session | 现有 #891–#899 | 被更新实现等价替代 |
| 48 | `c677ad99` | Cancel pending startup work when the event loop exits | 现有 #892/#899 | 被更新实现等价替代 |
| 49 | `a88edaa7` | Ensure SQLite connections close and commit transactions | 现有 #891–#899 | 被更新实现等价替代 |
| 50 | `2e209e0c` | fix: version thumbnail artifacts across edits | 现有 #891–#899 | 被更新实现等价替代 |
| 51 | `42e4171e` | Refactor photo album management workflow | 现有 #891–#899 | 被更新实现等价替代 |
| 52 | `e821f380` | Preserve color profiles and manage still texture residency | PR1 Detail | 已迁移 |
| 53 | `31d7ecb4` | Optimize RAW detail decoding and add benchmark telemetry | PR1 Detail | 已迁移 |
| 54 | `b5676e88` | Merge pull request #886 from OliverZhaohaibin/codex/gallery-detail-gpu-first-phase1 | PR1 Detail | 已迁移 |
| 55 | `effcf058` | Improve photo album management workflows | PR1 Detail | 已迁移 |
| 56 | `c5ef0251` | Handle initial still-texture upload failures and add diagnostics | PR1 Detail | 已迁移 |
| 57 | `06a39362` | Remove verbose detail diagnostics and fix failed texture reuse | PR1 Detail | 已迁移 |
| 58 | `10e6df0c` | Harden startup lifecycle and packaged benchmark evidence | PR3 Domains | 核对中（后续域待迁移） |
| 59 | `448c3172` | Unify identity merging across people and pets | PR1 Detail；PR2 Recognition | 核对中（PR1 已迁移；后续域待迁移） |
| 60 | `3793513c` | Coordinate identity merge refresh policies and pet redirect handling | PR2 Recognition | 核对中（后续域待迁移） |
| 61 | `1bca7749` | fix pets review remediation | PR2 Recognition | 核对中（后续域待迁移） |
| 62 | `e2627e9c` | fix: make pets identity commits recoverable | PR1 Detail；PR2 Recognition | 核对中（PR1 已迁移；后续域待迁移） |
| 63 | `34098693` | fix: harden pets model and stacked CI gates | PR2 Recognition | 核对中（后续域待迁移） |
| 64 | `edf9fcb3` | docs: record second pets review evidence | PR2 Recognition | 核对中（后续域待迁移） |
| 65 | `f3e74d3f` | ci: provision pets contract runtime | PR2 Recognition | 核对中（后续域待迁移） |
| 66 | `adff2c1f` | perf: batch pets ANN index updates | PR2 Recognition | 核对中（后续域待迁移） |
| 67 | `e0001ee6` | ci: rerun transient macOS startup contract | 现有 #891/#897 | 被更新实现等价替代 |
| 68 | `f817bdf2` | docs: close pets automated CI evidence | PR2 Recognition | 核对中（后续域待迁移） |
| 69 | `4e522e60` | Fix Live Photo video projection source identity | PR1 Detail | 已迁移 |
| 70 | `e212bd01` | fix: avoid duplicate WIC Live Photo rotation | PR1 Detail | 已迁移 |
| 71 | `b84b8466` | fix: invalidate stale Live Photo still surfaces | PR1 Detail | 已迁移 |
| 72 | `7a07d09b` | test: clamp Live Photo orientation regression | PR1 Detail | 已迁移 |
| 73 | `da6e2f1b` | Improve local photo album management | PR2 Recognition | 核对中（后续域待迁移） |
| 74 | `d037aa79` | Recover legacy pet merge journal operations | PR2 Recognition | 核对中（后续域待迁移） |
| 75 | `85d895ec` | Add local photo album management features | PR2 Recognition | 核对中（后续域待迁移） |
| 76 | `679dfb02` | Fix recognition recovery cache refresh | PR2 Recognition | 核对中（后续域待迁移） |
| 77 | `0d7492a5` | fix: centralize recognition mutation recovery | PR1 Detail；PR2 Recognition；PR3 Domains | 核对中（PR1 已迁移；后续域待迁移） |
| 78 | `26d5e1fb` | fix: hold recognition mutation lifecycle lease | PR2 Recognition | 核对中（后续域待迁移） |
| 79 | `a29f98ae` | fix: read Windows scale-contract RSS safely | 现有 #891/#897 | 被更新实现等价替代 |
| 80 | `e882cf5b` | test: stabilize pets scale probes | PR2 Recognition | 核对中（后续域待迁移） |
| 81 | `adaa91b0` | Merge pull request #888 from OliverZhaohaibin/codex/recognition-review-remediation | PR2 Recognition | 核对中（后续域待迁移） |
| 82 | `cac8dd24` | Merge pull request #887 from OliverZhaohaibin/codex/pets-review-remediation | PR2 Recognition | 核对中（后续域待迁移） |
| 83 | `e0909f1a` | fix(pets): harden scan recovery and overlap semantics | PR2 Recognition | 核对中（后续域待迁移） |
| 84 | `90492219` | fix(pets): enforce stable complete-link matching | PR2 Recognition | 核对中（后续域待迁移） |
| 85 | `f3c4372a` | fix(images): avoid Qt raster decoder crashes | 现有 #891–#899 | 被更新实现等价替代 |
| 86 | `d927151a` | fix(thumbnails): encode artifacts with Pillow | 现有 #891–#899 | 被更新实现等价替代 |
| 87 | `190f6187` | fix(images): detach Pillow-backed QImages | 现有 #891–#899 | 被更新实现等价替代 |
| 88 | `a8352f3b` | fix(ui): render SVG icons explicitly | 现有 #891–#899 | 被更新实现等价替代 |
| 89 | `305b8f81` | fix(ui): stabilize offscreen popup lifecycle | 现有 #891–#899 | 被更新实现等价替代 |
| 90 | `357d3e8c` | fix(ui): avoid nested offscreen event loop | 现有 #891–#899 | 被更新实现等价替代 |
| 91 | `5665542c` | fix(thumbnails): decode L2 cache safely | 现有 #891–#899 | 被更新实现等价替代 |
| 92 | `c54aa608` | fix(images): route cached assets through safe decoder | 现有 #891–#899 | 被更新实现等价替代 |
| 93 | `48965433` | Handle incremental pet identity matching across embedding generations | PR2 Recognition | 核对中（后续域待迁移） |
| 94 | `71a25038` | Handle pet contract retirement and safe image decoding | PR2 Recognition | 核对中（后续域待迁移） |
| 95 | `705c4ccb` | Stabilize player view initialization tests without multimedia IO | 现有 #891–#899 | 被更新实现等价替代 |
| 96 | `2aefe9eb` | Isolate popup resize test from QtMultimedia initialization | 现有 #891–#899 | 被更新实现等价替代 |
| 97 | `f5d8e4f1` | Isolate VideoArea tests from native multimedia backends | 现有 #891–#899 | 被更新实现等价替代 |
| 98 | `b36084cf` | Stabilize Qt resource cleanup in UI tests | 现有 #891–#899 | 被更新实现等价替代 |
| 99 | `ab5e476a` | Fix seek activity handling and clean up Qt video tests | 现有 #891–#899 | 被更新实现等价替代 |
| 100 | `fb5e7cf6` | Shut down QApplication during pytest fixture teardown | 现有 #891–#899 | 被更新实现等价替代 |
| 101 | `7ee8a27e` | Stabilize saved hover synchronization and Qt widget cleanup | 现有 #891–#899 | 被更新实现等价替代 |
| 102 | `831ca9b6` | Run pytest through CI wrapper | 现有 #891/#897 | 被更新实现等价替代 |
| 103 | `5c1d2334` | Enforce species compatibility and persist contract migrations | PR2 Recognition | 核对中（后续域待迁移） |
| 104 | `0e9d88b9` | Preserve pet migration state across merges and contract upgrades | PR2 Recognition | 核对中（后续域待迁移） |
| 105 | `59c96187` | Merge pull request #889 from OliverZhaohaibin/codex/pr-884-pets-remediation | PR2 Recognition | 核对中（后续域待迁移） |

## 验证规则

- 不以文件同名或 core 单测存在作为等价证据；必须验证生产调用链。
- 后续安全修复优先：async token、Library epoch、Live Photo transaction、Edit invalidation、Recognition journal/lease/recovery/outbox 均不得回退。
- legacy tree 删除由 #898 更新实现等价替代，不恢复 compatibility factories。
- 每个纵向 PR 在三平台 required jobs 通过并以 merge commit 合入后，才更新本表状态。

# 27 - Durable User State Residual Boundary

> **版本:** 1.0 | **日期:** 2026-05-03
> **状态:** 已完成
> **范围:** Phase 2 durable user state residual：hidden / pinned / order

---

## 1. 背景与目标

承接 `26-temp-library-end-to-end-regression.md` 的下一步交接。本轮不扩 Maps，
不改扫描主线，而是收口 Phase 2 剩余的 durable user state：当前已存在的
People hidden、People person/group order，以及 sidebar pinned state。

迁移前的问题是：

- People hidden / order 已由 `FaceStateRepository` 持久化，但缺少通过
  `LibrarySession.people` 入口的 session-level 回归。
- pinned album/person/group 的 pin、unpin、rename、remap、redirect/prune
  规则仍集中在 Qt `PinnedItemsService` 中，业务规则和 `QObject` signal 混在一起。
- temp-library E2E 已覆盖 `favorite + trash`，但 `hidden / pinned / order` 仍是
  文档上的 Phase 2 residual。

目标是：

- 保持 People hidden / order 的事实边界为 `FaceStateRepository` / `PeopleService`。
- 将 pinned sidebar 状态规则迁入 application service，GUI service 只保留 Qt
  transport 与兼容 API。
- 不把 pinned 写入 `global_index.db`；物理存储继续复用 settings payload。
- 用 focused tests 锁定当前行为。

## 2. 变更摘要

### 2.1 Pinned state application boundary

- 新增 `PinnedStateRepositoryPort`
  - 定义 pinned payload 的 load/save 边界。
  - 目前用于 settings-backed adapter，不代表新增独立数据库。
- 新增 `application/services/pinned_state_service.py`
  - `PinnedSidebarStateService` 承接 library scoping、item normalization、
    dedupe、pin/unpin、rename、album path remap、People redirect/prune 与
    `next_group_label()` 规则。
  - `PinnedSidebarItem` 移到 application 层，GUI wrapper 继续 re-export 同一类型。

### 2.2 GUI transport wrapper

- `gui/services/pinned_items_service.py`
  - 保留既有 public API 与 `changed` signal。
  - 内部新增 settings-backed repository adapter，调用
    `PinnedSidebarStateService` 完成实际规则。
  - People stale pin 清理继续优先使用注入的 active People service；
    没有 active service 时仍保留 standalone `PeopleService(library_root)` fallback。

### 2.3 People hidden/order session regression

- `tests/application/test_library_people_service.py`
  - 新增通过 `LibrarySession.people` 设置 hidden + person order 的回归。
  - 新增通过 `LibrarySession.people` 设置 group order 的回归。
  - 两组测试都模拟 People repository reload/rescan，确认 `FaceStateRepository`
    中的用户状态不被 scan replacement 覆盖。

### 2.4 Pinned state focused regression

- 新增 `tests/application/test_pinned_state_service.py`
  - 覆盖 library-scoped pin 顺序。
  - 覆盖 child album 在 parent rename 后 remap，且 custom label 保留。
  - 覆盖 People redirect/prune：person redirect、group delete、stale entity 清理。

## 3. 行为说明

- 本轮的 `hidden` 指 People hidden state；没有新增 asset-level hidden schema。
- 本轮的 `order` 指 People person/group card order；没有新增 album/gallery manual
  order schema。
- pinned sidebar state 仍按 library root 分库持久化，兼容既有 settings JSON
  payload。
- GUI 侧 pinned 行为不变：widgets/coordinators 仍使用 `PinnedItemsService`，
  信号语义保持为状态实际变化后发出一次 `changed`。
- 本轮没有改变 scan/import/move/delete/restore 主线，也没有改变
  `global_index.db` 的 asset source-of-truth 定位。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_pinned_state_service.py tests/application/test_library_people_service.py tests/test_settings_manager.py -q`
- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_people_service.py tests/test_people_repository.py tests/test_settings_manager.py tests/gui/widgets/test_people_dashboard_widget.py tests/test_album_sidebar.py tests/test_album_tree_model.py tests/ui/test_albums_dashboard.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused durable user state regressions 通过。
- 完整计划内 focused regressions 通过。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 补 Phase 6 性能 baseline：scan、gallery pagination、thumbnail cache。
2. 若后续再回到 Maps，只处理新暴露问题；当前不再主动扩新的
   session/runtime boundary。

# iPhotron 重构实施文档（第二阶段）

版本：v1.0  
适用分支：`copilot/refactor-iphotron-first-phase` 之后的第二阶段  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 1. 第一阶段实现评估结论

### 1.1 总体结论
第一阶段实现**基本达标，可以进入第二阶段**。

已完成的关键目标包括：

- `app.py` 已显式降级为 compatibility backend facade
- `bootstrap/container.py` 已建立，DI 装配从 `appctx.py` 中剥离
- `presentation/qt/session/app_session.py` 已建立，session 概念已经落地
- `gui/facade.py` 已转为组合器，并拆出：
  - `album_facade.py`
  - `asset_facade.py`
  - `library_facade.py`
- `library_update_service.py` 已开始通过 scan use case 承接业务调用
- 第一阶段新增 façade 的单元测试已经补上

### 1.2 评估结果
这意味着第一阶段的“架构切口”已经建立，项目现在具备继续推进第二阶段的条件。

---

## 2. 第一阶段残留问题与进入第二阶段前的前置要求

### 2.1 必须先做的事：同步 main
当前分支相对 `main`：
- ahead: 4 commits
- behind: 15 commits

第二阶段开始前，必须先执行：

1. `rebase main` 或 `merge main`
2. 修复冲突
3. 跑一轮第一阶段回归测试
4. 再开始第二阶段开发

### 2.2 第一阶段残留但不构成阻塞的问题

#### 残留问题 A：`AppContext` 仍然保留了一部分旧属性
虽然 `container` 和 `session` 已拆出，但 `AppContext` 仍保留：
- `settings`
- `library`
- `facade`
- `recent_albums`
- `theme`

这说明它还是兼容壳，不是纯薄壳。

#### 残留问题 B：`library_update_service.py` 仍然偏重
虽然已经引入 use case，但它仍然持有较多业务规则：
- trash preserved metadata merge
- move aftermath bookkeeping
- restored album refresh
- stale album cache / forced reload 管理

这正是第二阶段要重点处理的对象。

#### 残留问题 C：`LibraryManager` 仍然是 legacy coordination object
第一阶段只是“止血”，并没有真正拆掉：
- tree coordination
- scan coordination
- watch coordination
仍然汇聚在一个对象里。

#### 残留问题 D：兼容桥仍然较多
`app.py`、`AppContext`、`AppFacade` 目前都还处于“兼容层保留”状态。  
这没有问题，但第二阶段必须开始进一步收缩它们的职责。

---

## 3. 第二阶段目标

第二阶段不是继续横向建新目录，而是要做真正的“核心复杂度下沉”。

### 3.1 第二阶段总目标
1. 将扫描链路独立为完整子域
2. 统一路径解析与路径策略
3. 继续拆解 `LibraryManager`
4. 将 UI 侧服务进一步降权
5. 让 compatibility bridge 更薄
6. 建立更明确的 Result / Error / Event 边界
7. 为第三阶段删除 legacy bridge 做准备

---

## 4. 第二阶段范围

第二阶段只聚焦以下四条主线：

1. **扫描子域独立**
2. **路径策略统一**
3. **LibraryManager 解体**
4. **兼容层进一步收缩**

不在第二阶段内做的事：
- 不重写 UI
- 不重写数据库层
- 不大规模改 domain model
- 不做全仓库命名迁移

---

## 5. 第二阶段目标结构

```text
src/iPhoto/
├── application/
│   ├── use_cases/
│   │   ├── album/
│   │   ├── asset/
│   │   ├── scan/
│   │   └── library/
│   ├── services/
│   │   ├── library_tree_service.py
│   │   ├── library_scan_service.py
│   │   ├── library_watch_service.py
│   │   └── trash_service.py
│   └── policies/
│       ├── album_path_policy.py
│       ├── trash_restore_policy.py
│       └── library_scope_policy.py
│
├── infrastructure/
│   ├── scan/
│   │   ├── fs_scanner.py
│   │   ├── metadata_pipeline.py
│   │   ├── scan_result_persister.py
│   │   └── live_pairing_reader.py
│   ├── watcher/
│   │   └── qt_library_watcher.py
│   └── persistence/sqlite/
│       ├── asset_index_gateway.py
│       └── album_index_gateway.py
│
├── presentation/qt/
│   ├── facade/
│   ├── services/
│   └── session/
│
└── bootstrap/
    ├── container.py
    └── startup.py
```

---

## 6. 第二阶段核心设计

### 6.1 扫描子域独立

#### 当前问题
扫描逻辑现在仍散落在：
- `application/use_cases/scan/rescan_album_use_case.py`
- `gui/services/library_update_service.py`
- `library/workers/*`
- `index_sync_service.py`
- `path_normalizer.py`
- `cache/index_store.py`

#### 第二阶段目标
形成“扫描子域闭包”，让扫描相关复杂度集中在一个边界内。

#### 第二阶段拆分结果
新增：

```text
src/iPhoto/application/use_cases/scan/scan_album_use_case_v2.py
src/iPhoto/application/use_cases/scan/persist_scan_result_use_case.py
src/iPhoto/application/use_cases/scan/merge_trash_restore_metadata_use_case.py
src/iPhoto/application/use_cases/scan/load_incremental_index_use_case.py

src/iPhoto/application/policies/album_path_policy.py
src/iPhoto/application/policies/trash_restore_policy.py

src/iPhoto/infrastructure/scan/fs_scanner.py
src/iPhoto/infrastructure/scan/scan_result_persister.py
src/iPhoto/infrastructure/scan/live_pairing_reader.py
```

#### 要求
- `library_update_service.py` 不再持有 trash metadata merge 规则
- `library_update_service.py` 不再直接持有 index persist 规则
- scanning worker 只负责执行和传递结果
- 真正扫描语义由 use case 和 policy 负责

---

### 6.2 路径策略统一

#### 当前问题
`album root / library root / rel / original_rel_path / recently deleted` 的路径规则仍然分散。

#### 第二阶段目标
路径规则不再散落在：
- `app.py`
- `rescan_album_use_case.py`
- `pair_live_photos_use_case_v2.py`
- `library_update_service.py`
- `path_normalizer.py`

#### 第二阶段新增文件
```text
src/iPhoto/application/policies/album_path_policy.py
src/iPhoto/application/policies/library_scope_policy.py
src/iPhoto/application/policies/trash_restore_policy.py
```

#### 三个策略职责

##### `album_path_policy.py`
负责：
- library_root 下 album_path 计算
- library-relative rel 与 album-relative rel 相互转换
- include_subalbums 范围判断

##### `library_scope_policy.py`
负责：
- 某个 path 是否属于当前 library
- 某个 move / restore 是否跨 library
- library root 与 album root 的作用域关系

##### `trash_restore_policy.py`
负责：
- recently deleted 中 restore metadata 保留规则
- `original_rel_path`
- `original_album_id`
- `original_album_subpath`
- restore fallback 规则

#### 第二阶段验收要求
第二阶段结束后，任何路径转换规则不得再散落在 façade 和 Qt service 中。

---

### 6.3 `LibraryManager` 解体

#### 当前问题
`LibraryManager` 仍然承担太多协调职责。

#### 第二阶段目标
将 `LibraryManager` 从“多职责对象”拆成“薄组合器”。

#### 第二阶段拆分目标文件

```text
src/iPhoto/application/services/library_tree_service.py
src/iPhoto/application/services/library_scan_service.py
src/iPhoto/application/services/library_watch_service.py
src/iPhoto/application/services/trash_service.py
src/iPhoto/infrastructure/watcher/qt_library_watcher.py
```

#### 拆分职责

##### `library_tree_service.py`
负责：
- bind_path 之后的 tree 构建
- album node 构建
- children 关系
- refresh tree
- list_albums / list_children

##### `library_scan_service.py`
负责：
- start_scanning
- stop_scanning
- scan state
- scan buffer
- scan worker 协调
- geotagged cache invalidation

##### `library_watch_service.py`
负责：
- watcher pause / resume
- rebuild watches
- directoryChanged / debounce
- watch suspend depth

##### `trash_service.py`
负责：
- deleted directory 初始化
- restore 相关 album/root 定位
- deleted items 约束规则

##### `qt_library_watcher.py`
负责：
- 将 Qt watcher 具体实现封装到 infrastructure

#### 第一阶段已有 mixin，不必推倒
第二阶段不要求直接删除 mixin。  
推荐方式是：

1. 先提服务
2. 再让 `LibraryManager` 调服务
3. 最后缩掉 mixin 中的具体实现

---

### 6.4 `library_update_service.py` 降权

#### 当前问题
它现在还是 UI service 中最重的一个。

#### 第二阶段目标
让它退化为：

- worker / task_manager 协调器
- signal relay
- UI reload 决策点

而不是业务规则宿主。

#### 第二阶段要移走的逻辑
从 `library_update_service.py` 中移出：

1. trash restore metadata merge
2. persist scan result 细节
3. pair live rows transform
4. stale album bookkeeping 中与领域相关的部分
5. restored album rescan 规则中的业务判断

#### 第二阶段后的目标状态
`library_update_service.py` 保留：
- Qt signal
- BackgroundTaskManager 协调
- worker finished / error 对接
- UI 级 reload 触发

业务计算全部由 application 层完成。

---

### 6.5 compatibility bridge 进一步收缩

#### 第二阶段目标
让以下文件进一步“瘦”：

- `src/iPhoto/app.py`
- `src/iPhoto/appctx.py`
- `src/iPhoto/gui/facade.py`

#### 具体要求

##### `app.py`
只允许：
- 参数兼容
- 调 application use case
- 返回旧结果

不得存在：
- 路径规则
- index sync 规则
- trash 保留规则
- pair 逻辑细节

##### `appctx.py`
只允许：
- 组合 `container`
- 组合 `session`

不得继续保留新的 session 逻辑。

##### `gui/facade.py`
只允许：
- signal 定义
- sub-facade 组合
- public method forwarding

不得新增业务逻辑。

---

## 7. 第二阶段文件级实施方案

### 7.1 需要改造的现有文件

```text
src/iPhoto/app.py
src/iPhoto/appctx.py
src/iPhoto/gui/facade.py
src/iPhoto/gui/services/library_update_service.py
src/iPhoto/library/manager.py
src/iPhoto/application/use_cases/scan/rescan_album_use_case.py
src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py
src/iPhoto/path_normalizer.py
src/iPhoto/index_sync_service.py
```

### 7.2 第二阶段建议新增文件

```text
src/iPhoto/application/use_cases/scan/scan_album_use_case_v2.py
src/iPhoto/application/use_cases/scan/merge_trash_restore_metadata_use_case.py
src/iPhoto/application/use_cases/scan/load_incremental_index_use_case.py

src/iPhoto/application/services/library_tree_service.py
src/iPhoto/application/services/library_scan_service.py
src/iPhoto/application/services/library_watch_service.py
src/iPhoto/application/services/trash_service.py

src/iPhoto/application/policies/album_path_policy.py
src/iPhoto/application/policies/library_scope_policy.py
src/iPhoto/application/policies/trash_restore_policy.py

src/iPhoto/infrastructure/scan/fs_scanner.py
src/iPhoto/infrastructure/scan/scan_result_persister.py
src/iPhoto/infrastructure/scan/live_pairing_reader.py
src/iPhoto/infrastructure/watcher/qt_library_watcher.py
```

---

## 8. 第二阶段逐文件改造说明

### 8.1 `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`

#### 当前问题
该用例内部仍然直接包含：
- incremental index load
- scanner 调用
- trash restore merge
- update snapshot
- ensure links
- favorites sync

#### 第二阶段目标
将它拆成 orchestration use case，只负责编排。

#### 修改方式
把内部逻辑拆给：
- `load_incremental_index_use_case.py`
- `merge_trash_restore_metadata_use_case.py`
- `scan_result_persister.py`
- `album_path_policy.py`

#### 第二阶段后形态
`RescanAlbumUseCase` 只做：
1. 读取 album manifest filters
2. 调 scanner
3. 调 merge policy / use case
4. 调 persister
5. 返回 rows

---

### 8.2 `src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py`

#### 当前问题
仍有 rows transform 与 rel path 转换细节。

#### 第二阶段目标
将 rel path 规则下沉到 `album_path_policy.py`，将 DB rows 读取封装到 `live_pairing_reader.py`

#### 修改后
该用例应只做：
1. 读取 scoped rows
2. 计算 groups / payload
3. 写 links
4. sync live roles

---

### 8.3 `src/iPhoto/gui/services/library_update_service.py`

#### 第二阶段改造重点
把以下方法逻辑减重：
- `rescan_album`
- `pair_live`
- `_on_scan_finished`
- `_refresh_restored_album`

#### 具体动作
1. 注入新的 use case / policy / services
2. 删除内部 trash merge 规则
3. 删除内部 persist 细节
4. 将 restore rescan 规则交给 `trash_service.py` + `library_scan_service.py`

---

### 8.4 `src/iPhoto/library/manager.py`

#### 第二阶段目标
从具体实现宿主变成组合器。

#### 改造步骤
1. 提取 service
2. `LibraryManager` 注入这些 service
3. 旧方法仅委托
4. 逐步清理 mixin 内容

#### 第二阶段后允许的职责
- 作为 QObject signal 容器
- 组合 tree/scan/watch/trash service
- 对外保留兼容 API

---

### 8.5 `src/iPhoto/path_normalizer.py`
#### 第二阶段目标
只保留纯底层 path helper，不再承担业务策略。

业务层路径语义迁入：
- `album_path_policy.py`
- `library_scope_policy.py`
- `trash_restore_policy.py`

---

### 8.6 `src/iPhoto/index_sync_service.py`
#### 第二阶段目标
它继续作为基础能力提供者存在，但不再成为业务规则聚合点。

需要区分：
- 纯技术 helper：可留
- 业务策略：必须挪走

---

## 9. 第二阶段开发顺序

### Step 1：先同步 main
- rebase / merge main
- 解决冲突
- 跑第一阶段测试

### Step 2：建立 policy 层
新建：
- `album_path_policy.py`
- `library_scope_policy.py`
- `trash_restore_policy.py`

### Step 3：重构 scan use case
- 拆 `rescan_album_use_case.py`
- 拆 `pair_live_photos_use_case_v2.py`
- 新建 scan 相关辅助用例

### Step 4：提取 infrastructure/scan
- `fs_scanner.py`
- `scan_result_persister.py`
- `live_pairing_reader.py`

### Step 5：给 `LibraryManager` 提 service
- `library_tree_service.py`
- `library_scan_service.py`
- `library_watch_service.py`
- `trash_service.py`

### Step 6：瘦身 `library_update_service.py`
- 只保留 Qt / worker / reload 协调

### Step 7：补第二阶段测试
- policy 测试
- scan use case 测试
- LibraryManager delegation 测试
- restore / rescan 回归测试

---

## 10. 第二阶段任务清单

- [ ] 同步 `main`
- [ ] 新建 `application/policies/album_path_policy.py`
- [ ] 新建 `application/policies/library_scope_policy.py`
- [ ] 新建 `application/policies/trash_restore_policy.py`
- [ ] 新建 `application/use_cases/scan/merge_trash_restore_metadata_use_case.py`
- [ ] 新建 `application/use_cases/scan/load_incremental_index_use_case.py`
- [ ] 改造 `rescan_album_use_case.py`
- [ ] 改造 `pair_live_photos_use_case_v2.py`
- [ ] 新建 `infrastructure/scan/fs_scanner.py`
- [ ] 新建 `infrastructure/scan/scan_result_persister.py`
- [ ] 新建 `infrastructure/scan/live_pairing_reader.py`
- [ ] 新建 `application/services/library_tree_service.py`
- [ ] 新建 `application/services/library_scan_service.py`
- [ ] 新建 `application/services/library_watch_service.py`
- [ ] 新建 `application/services/trash_service.py`
- [ ] 新建 `infrastructure/watcher/qt_library_watcher.py`
- [ ] 改造 `library/manager.py`
- [ ] 改造 `gui/services/library_update_service.py`
- [ ] 补第二阶段测试

---

## 11. 第二阶段验收标准

### 11.1 结构验收
必须满足：

1. `library_update_service.py` 不再直接持有 trash metadata merge 规则
2. 路径业务规则已统一进入 policy 层
3. `LibraryManager` 已能通过 service delegation 工作
4. scan 相关读取/写入能力已有 infrastructure 封装
5. `app.py` / `appctx.py` / `gui/facade.py` 没有重新膨胀

### 11.2 行为验收
以下行为必须不回归：
- library bind
- tree refresh
- scan
- restore
- delete
- import
- pair live
- recently deleted 恢复链路
- 初始扫描
- global db + nested album 情况

### 11.3 测试验收
至少补齐：

#### 单元测试
- policy 测试
- rescan use case 测试
- pair live use case 测试
- LibraryManager delegation 测试

#### 集成测试
- trash restore metadata 保留
- scan result persist
- restored album refresh
- library watcher / scan interaction
- nested album with global db

---

## 12. 第二阶段完成后的预期结果

### 应完成
- 扫描子域已形成明确边界
- 路径规则已统一
- `LibraryManager` 已明显瘦身
- `library_update_service.py` 已退化为 UI 协调器
- compatibility bridge 更薄
- 第三阶段可以开始清理 legacy bridge

### 可接受的残留
- `app.py` 仍保留兼容函数签名
- `AppContext` 仍保留兼容外观
- `AppFacade` 仍保留原 public API
- 少量 legacy helper 仍保留，但不得继续增长

---

## 13. 风险与回滚

### 风险
1. 路径策略统一后可能引入 rel/path 行为变化
2. restore 元数据迁移可能影响 recently deleted
3. `LibraryManager` service delegation 可能影响 watcher 行为
4. rebase main 后已有阶段一代码可能需要适配新提交

### 回滚策略
- 第二阶段所有改造优先做“提取 + 委托”，不是立刻删除旧实现
- 所有 public API 保持不变
- 路径策略替换必须配套回归测试
- restore / scan / pair live 先通过测试再删旧代码

---

## 14. Definition of Done

- [ ] 分支已同步 `main`
- [ ] 路径规则已进入 policy 层
- [ ] `RescanAlbumUseCase` 已变成编排型用例
- [ ] `PairLivePhotosUseCaseV2` 已去除路径转换细节
- [ ] `LibraryManager` 已通过服务进行 delegation
- [ ] `library_update_service.py` 已退化为 UI 协调器
- [ ] scan / restore / pair live / nested album / global db 回归通过
- [ ] 第二阶段新增测试通过

---

## 15. 推荐提交顺序

### Commit 1
- 同步 main
- 修复冲突
- 跑第一阶段测试

### Commit 2
- 新建 policy 层
- 改造路径相关 use case

### Commit 3
- 新建 infrastructure/scan
- 改造 scan use case

### Commit 4
- 新建 library_*_service
- 改造 `LibraryManager`

### Commit 5
- 瘦身 `library_update_service.py`
- 补回归测试

### Commit 6
- 清理兼容代码中的重复逻辑
- 修文档

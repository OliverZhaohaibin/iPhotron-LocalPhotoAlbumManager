# 1. 完成路径策略收口

这是第二步里最先要收尾的动作，因为后面 scan 和 restore 都依赖它。

## 1.1 统一 `album/library/rel` 转换逻辑

### 目标

把所有以下逻辑都收口到 `AlbumPathPolicy`：

* `compute_album_path`
* library-relative rel
* album-relative rel
* strip/prefix 规则
* subalbum 范围判断

### 具体动作

检查并替换这些文件中零散路径转换逻辑：

#### 要改的文件

* `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`
* `src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py`
* `src/iPhoto/gui/services/library_update_service.py`
* `src/iPhoto/app.py`
* `src/iPhoto/path_normalizer.py`

#### 要做的事

* 把直接字符串拼接 `f"{album_path}/{rel}"` 的地方改成 policy 调用
* 把 prefix strip 的地方统一改成 `AlbumPathPolicy.strip_album_prefix(...)`
* 把 scope 判断改成 `AlbumPathPolicy.is_within_scope(...)`
* `path_normalizer.py` 只保留底层 path helper，不再保留业务语义函数

### 完成标志

全仓库不再出现散落的 album/library rel 转换逻辑，路径语义都从 policy 走。

---

## 1.2 统一 trash restore 路径/元数据规则

### 目标

把 “Recently Deleted” 的 restore 元数据保留逻辑彻底统一到 `TrashRestorePolicy` + `MergeTrashRestoreMetadataUseCase`

### 具体动作

#### 要改的文件

* `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`
* `src/iPhoto/gui/services/library_update_service.py`
* `src/iPhoto/library/trash_manager.py`

#### 要做的事

* 删除 `library_update_service.py` 中任何残余的 trash metadata merge 细节
* 让同步 rescan 和异步 scan finished 都只调用：

  * `MergeTrashRestoreMetadataUseCase`
* `trash_manager.py` 中如果有 restore 元数据规则判断，也下沉到 `TrashRestorePolicy`

### 完成标志

恢复元数据规则只有一套来源，不再出现“同步一套、异步一套”。

---

## 1.3 完成 library scope 规则集中

### 目标

将“某个 path 是否属于当前 library”统一走 `LibraryScopePolicy`

### 具体动作

#### 要改的文件

* `src/iPhoto/gui/services/library_update_service.py`
* `src/iPhoto/application/services/move_bookkeeping_service.py`
* `src/iPhoto/library/manager.py`

#### 要做的事

* 所有 `relative_to(library_root)` / descendant 判断收口到 `LibraryScopePolicy`
* restore target 是否落在 library 内，不再在多个服务里重复判断

### 完成标志

关于 library 归属的判断不再手写 scattered `relative_to` / `startswith` 风格代码。

---

# 2. 完成 scan 子域收口

这是第二步最核心的部分。

## 2.1 把 `RescanAlbumUseCase` 收尾成“纯编排用例”

### 当前状态

已经部分拆分，但还保留：

* scanner 调用
* index persist
* links 更新
* favorites sync

### 具体动作

#### 要改的文件

* `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`

#### 要做的事

把它改成只做 5 件事：

1. 读取 album manifest filters
2. 调 incremental index loader
3. 调 scanner
4. 调 trash merge use case
5. 调 persist use case

### 需要补的协作者

* `LoadIncrementalIndexUseCase`
* `MergeTrashRestoreMetadataUseCase`
* `PersistScanResultUseCase`
* `AlbumPathPolicy`

### 完成标志

`rescan_album_use_case.py` 中不再直接出现：

* prefix/strip path 逻辑
* restore metadata 逻辑
* links 细节逻辑
* 复杂 DB 读写细节

---

## 2.2 把 `PersistScanResultUseCase` 用满

### 目标

让 scan 结果落库和 links 更新全部从这个入口走。

### 具体动作

#### 要改的文件

* `src/iPhoto/gui/services/library_update_service.py`
* `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`
* `src/iPhoto/app.py`

#### 要做的事

确保以下两种路径都统一使用：

* 同步 rescan
* 异步 scan finished

都走：

* `PersistScanResultUseCase.execute(...)`

### 完成标志

全仓库不存在第二种 scan persist 路径。

---

## 2.3 把 live pair 读取逻辑从 use case 中剥出来

### 目标

让 `PairLivePhotosUseCaseV2` 不再直接关心 DB rows 读取与 rel 转换。

### 具体动作

#### 要改的文件

* `src/iPhoto/application/use_cases/scan/pair_live_photos_use_case_v2.py`
* `src/iPhoto/infrastructure/scan/live_pairing_reader.py`

#### 要做的事

* `live_pairing_reader.py` 负责按 album/library scope 读取 rows
* `PairLivePhotosUseCaseV2` 只做：

  1. 取 rows
  2. 计算 groups/payload
  3. 写 links
  4. sync live roles

### 完成标志

`pair_live_photos_use_case_v2.py` 中不再有 rel prefix/strip 的细节代码。

---

## 2.4 把 scanner 调用收口到 infrastructure

### 目标

应用层不再直接依赖 scanner_adapter 的细碎实现。

### 具体动作

#### 要改的文件

* `src/iPhoto/application/use_cases/scan/rescan_album_use_case.py`
* `src/iPhoto/infrastructure/scan/fs_scanner.py`

#### 要做的事

* `fs_scanner.py` 封装对 `scan_album(...)` 的调用
* 应用层只依赖 `FsScanner` 或类似适配器
* scanner worker 仍可保留，但不要让应用层直接依赖 scanner_adapter 的实现细节

### 完成标志

scanner 调用点集中，不再在多个 use case 里直接 import scanner_adapter。

---

# 3. 完成 `library_update_service.py` 降权

这是第二步必须真正落地的地方。

## 3.1 移走 move aftermath 业务规则

### 当前状态

已经抽出 `MoveBookkeepingService`，但 `library_update_service.py` 仍在主导很多后置逻辑。

### 具体动作

#### 要改的文件

* `src/iPhoto/gui/services/library_update_service.py`
* `src/iPhoto/application/services/move_bookkeeping_service.py`

#### 要做的事

把以下内容继续从 UI service 挪走：

* stale 目标选择逻辑
* refresh target 归并逻辑
* restore rescan target 计算逻辑
* forced reload 标记语义

让 `library_update_service.py` 只保留：

* 发 signal
* 调 service
* 调 task manager

### 完成标志

`handle_move_operation_completed()` 中只剩协调代码，不剩大段业务判断。

---

## 3.2 移走 `_on_scan_finished()` 中的业务语义

### 目标

让 `_on_scan_finished()` 只处理成功/失败和 UI signal。

### 具体动作

#### 要改的文件

* `src/iPhoto/gui/services/library_update_service.py`

#### 要做的事

将以下步骤外移：

* trash merge
* persist scan result
* 是否需要 reload 的业务判断

形成类似：

* `finalize_scan_result(...)`
* `compute_post_scan_ui_effect(...)`

这些逻辑应在 application service / use case 里完成。

### 完成标志

`_on_scan_finished()` 只像一个回调，不像业务处理器。

---

# 4. 完成 `LibraryManager` 第二阶段目标

## 4.1 把 tree 逻辑继续交给 `LibraryTreeService`

### 具体动作

#### 要改的文件

* `src/iPhoto/library/manager.py`
* `src/iPhoto/application/services/library_tree_service.py`

#### 要做的事

把这类逻辑尽量转为 service：

* tree build
* album/child sorting
* list_albums
* list_children
* refresh tree diff 比较

### 完成标志

`LibraryManager._refresh_tree()` 中剩下的是调用，不是实现。

---

## 4.2 把 watcher 语义继续交给 `LibraryWatchService`

### 具体动作

#### 要改的文件

* `src/iPhoto/library/filesystem_watcher.py`
* `src/iPhoto/library/manager.py`
* `src/iPhoto/application/services/library_watch_service.py`
* `src/iPhoto/infrastructure/watcher/qt_library_watcher.py`

#### 要做的事

统一处理：

* pause/resume
* watch suspend depth
* desired watch path computation
* rebuild_watches

### 完成标志

watcher 相关规则不再留在 manager/mixin 里重复存在。

---

## 4.3 把 scan state 语义继续交给 `LibraryScanService`

### 具体动作

#### 要改的文件

* `src/iPhoto/library/manager.py`
* `src/iPhoto/application/services/library_scan_service.py`

#### 要做的事

将以下状态尽量迁走：

* `_current_scanner_worker`
* `_live_scan_buffer`
* `_live_scan_root`
* `_scan_buffer_lock`
* geotagged cache invalidation 规则

### 完成标志

`LibraryManager` 中 scan 状态字段明显减少。

---

## 4.4 把 trash 目录规则继续交给 `TrashService`

### 具体动作

#### 要改的文件

* `src/iPhoto/library/trash_manager.py`
* `src/iPhoto/application/services/trash_service.py`
* `src/iPhoto/library/manager.py`

#### 要做的事

统一：

* deleted dir 初始化规则
* restore 目标 album 规则
* trash 目录保护规则

### 完成标志

trash 语义集中，不再横跨 manager/mixin/service 多处。

---

# 5. 完成 compatibility bridge 收缩

## 5.1 `app.py`

### 具体动作

#### 要改的文件

* `src/iPhoto/app.py`

#### 要做的事

确认它只剩：

* `open_album() -> OpenAlbumLegacyBridge`
* `rescan() -> RescanAlbumUseCase`
* `pair() -> PairLivePhotosUseCaseV2`
* `scan_specific_files()` 如果还存在复杂逻辑，也抽到 asset use case

### 完成标志

`app.py` 中没有真正的业务实现块。

---

## 5.2 `appctx.py`

### 具体动作

#### 要改的文件

* `src/iPhoto/appctx.py`

#### 要做的事

进一步减少兼容属性上的“真实逻辑”：

* `resume_startup_tasks()` 只转发给 session
* `remember_album()` 只转发给 session
* 不再新增 settings/library/theme 的真实处理逻辑

### 完成标志

`AppContext` 变成真正薄壳。

---

## 5.3 `gui/facade.py`

### 具体动作

#### 要改的文件

* `src/iPhoto/gui/facade.py`

#### 要做的事

确认它只保留：

* signal
* sub-facade 实例化
* forwarding

### 完成标志

`gui/facade.py` 中不再新增任何业务决策语义。

---

# 6. 完成第二步的测试动作

第二步如果不补测试，后面第三步会很危险。

## 6.1 必补单元测试

新增或完善：

* `AlbumPathPolicy` 测试
* `LibraryScopePolicy` 测试
* `TrashRestorePolicy` 测试
* `RescanAlbumUseCase` 编排测试
* `PairLivePhotosUseCaseV2` 测试
* `MoveBookkeepingService` 测试
* `LibraryTreeService` 测试
* `LibraryWatchService` 测试
* `LibraryScanService` 测试

---

## 6.2 必补集成测试

重点补以下链路：

* global db + nested album 扫描
* recently deleted restore metadata 保留
* restore 后 rescan 目标正确
* pair live 在 library_root / album_root 两种模式都不回归
* library watcher 与 scan 并发时不回归
* compatibility bridge 入口行为不变

---

# 7. 第二步完成判定标准

满足下面这些，才算第二步真正完成：

### 结构上

* 路径规则全部收口到 policy
* scan 子域形成稳定边界
* `library_update_service.py` 只剩 UI 协调职责
* `LibraryManager` 明显瘦身
* compatibility bridge 没有新增业务

### 行为上

* scan / restore / pair live / move / delete / import 不回归
* global db / nested album 不回归
* recently deleted 工作流不回归

### 工程上

* 第二步新增测试通过
* 新旧入口行为一致
* 后续第三步可以开始删 legacy 重复逻辑

---

# 8. 推荐提交顺序

## Commit 1

* 路径策略收尾
* 清理 scattered path logic

## Commit 2

* scan use case 收尾
* persist/live pair 统一入口

## Commit 3

* `library_update_service.py` 继续降权
* `MoveBookkeepingService` 完成接管

## Commit 4

* `LibraryManager` 继续 service delegation
* watcher/scan/trash 进一步抽离

## Commit 5

* compatibility bridge 收缩
* 清理重复逻辑

## Commit 6

* 补齐单元测试与集成测试
* 验收第二步完成状态

如果你愿意，我下一条可以直接把这份内容整理成 **“第二步完成清单.md” 下载文件**。

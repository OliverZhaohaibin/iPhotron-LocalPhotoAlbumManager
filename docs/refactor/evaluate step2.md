结论：**这个分支第二阶段有明显进展，但还不能算完全达标**。

好的部分：

* 分支已经基于 `main` 前进，没有再落后主线，这一点比上一个分支稳。
* 第二阶段要求的核心目录基本都建了：`application/policies/*`、`application/services/*`、`infrastructure/scan/*`、`infrastructure/watcher/*` 都已落地。
* 路径策略确实开始集中到 policy 层，`AlbumPathPolicy` 已经承接了 album path 计算、prefix/strip 和 scope 判断这类规则。
* `RescanAlbumUseCase` 已经从“大而全实现”转成“编排型用例”，把 incremental index、trash merge、scanner、persist 分给了专门组件。
* `library_update_service.py` 比第二阶段前明显减重：trash metadata merge 已下沉到 `MergeTrashRestoreMetadataUseCase`，move aftermath 也下沉到了 `MoveBookkeepingService`。
* watcher 方向是实质性下沉，`FileSystemWatcherMixin` 已经主要只剩 Qt 接线，暂停深度和 watch path 计算交给了 `LibraryWatchService`。

但它**还不满足“第二阶段完成”**，关键原因有三个：

### 1. `LibraryManager` 只完成了“部分瘦身”，还没有完成“服务化 delegation”

虽然 `LibraryManager` 已经实例化了 `LibraryTreeService`、`LibraryScanService`、`LibraryWatchService`、`TrashService` 和 `QtLibraryWatcher`，而且 `_refresh_tree()` 已经委托给 `LibraryTreeService`。

但 scan 协调主逻辑仍大量留在 `ScanCoordinatorMixin` 里，包括：

* `start_scanning`
* `stop_scanning`
* `is_scanning_path`
* `get_live_scan_results`
* `_on_scan_finished`
* `_on_scan_error`

这些方法仍然直接操作 worker、mutex、buffer、live rows、以及扫描完成后的 `backend.pair(...)`。

这说明：

* `LibraryScanService` 已经建了，但**还没有成为 scan 的真正 owner**
* `LibraryManager` 目前更像是“挂了几个 service 字段”，不是“主要通过 service delegation 工作”

这和第二阶段目标还有差距。

---

### 2. `library_update_service.py` 仍然偏重，还是没有完全退化成 UI 协调器

它确实比之前轻了，但还保留了不少非 UI 级决策，例如：

* restore 后哪些 album 需要 rescan
* restore 完成后当前 view 如何 reload
* restore rescan task 的创建与结果分支处理
* 部分 library scope 判断仍在这里发生

虽然 move bookkeeping 已提取，但 `_refresh_restored_album()` 及相关 reload 决策还在这个 Qt service 中。

所以它现在是：

* **比以前好很多**
* 但还**没有完全达到“只负责 worker / task_manager / signal relay / UI reload 接口”的目标**

---

### 3. 第二阶段新增 service 中，有些还是“能力声明”，不是“主路径接管”

`LibraryScanService` 目前只负责 scan state、current root、geo cache invalidation 这类轻状态能力。

但真正复杂的扫描协调没有迁过去。
换句话说，当前状态更像：

* Tree：基本接管了一部分
* Watch：基本接管得比较像样
* Scan：**只接了一小块状态，不是完整接管**
* Trash：还没有成为 restore/rescan 规则的唯一 owner

---

## 评价

我给这个分支的第二阶段结果评价是：

**7/10，方向正确，主要结构已搭起来，但 scan / restore 两条最复杂的主链路还没有完全从 legacy manager 与 Qt service 中剥离，所以不建议直接宣布第二阶段完成。**

更准确地说：

* **第二阶段 70%~80% 已完成**
* **最难的 20% 还没收尾**
* 当前状态适合继续做“补完第二阶段”，不适合马上进入第三阶段

---

# 完成第二阶段的具体动作

你要求的是：如果不满足要求，就给出完成第二阶段的具体动作，不给第三步方案。下面就是可直接执行的补完清单。

## A. 让 `LibraryScanService` 真正接管 scan 协调

### 必做动作 A1

把 `src/iPhoto/library/scan_coordinator.py` 中以下方法的核心逻辑迁入 `src/iPhoto/application/services/library_scan_service.py`：

* `start_scanning`
* `stop_scanning`
* `is_scanning_path`
* `_on_scan_finished`
* `_on_scan_error`

当前这些逻辑仍在 mixin 中。

### 必做动作 A2

`LibraryScanService` 不仅保存 state，还要成为以下职责的 owner：

* active scan root
* scan in progress
* live scan buffer lifecycle
* scan worker start/stop decision
* scan finished bookkeeping
* geo cache invalidation decision

### 必做动作 A3

`ScanCoordinatorMixin` 最终只保留：

* Qt signal 接线
* 对 `LibraryScanService` 的委托
* 与 `QThreadPool` / `ScannerSignals` 的必要适配

也就是把它降成跟 `FileSystemWatcherMixin` 类似的“薄 Qt mixin”。

---

## B. 从 `library_update_service.py` 中继续移出 restore 规则

### 必做动作 B1

把 `_refresh_restored_album()` 的业务判断拆出去。当前它仍负责：

* restore 后是否需要 rescan
* 当前 view 是否需要 force reload
* 当前 library root 下是否触发 assetReloadRequested

这些应迁到：

* `TrashService`
* 或新建 `RestoreRefreshService`

当前文件仍承担这些判断。

### 必做动作 B2

把以下职责从 `library_update_service.py` 中抽成纯 Python 服务：

* restore 完成后的 target album 集合计算
* restore 后 rescan 的判定
* restore 后 UI reload 策略输入值计算

`library_update_service.py` 最终只做：

* 提交 task
* 接 finished/error signal
* 根据服务返回结果发 Qt signal

---

## C. 让 `TrashService` 成为 trash / restore 的唯一 owner

### 必做动作 C1

检查 `src/iPhoto/application/services/trash_service.py`，把以下规则统一收进来：

* deleted directory 初始化规则
* restore metadata 使用规则
* restore fallback 规则
* restore 后目标 album/root 判定
* restore 后是否需要 rescan / reload

### 必做动作 C2

`library_update_service.py` 和 `library/trash_manager.py` 不再各自保留 restore 业务规则，只允许调用 `TrashService`。

---

## D. 收紧 `LibraryManager` 的职责边界

### 必做动作 D1

`LibraryManager` 中已经开始用 `LibraryTreeService`，下一步要做到：

* tree 走 `LibraryTreeService`
* scan 走 `LibraryScanService`
* watch 走 `LibraryWatchService`
* trash 走 `TrashService`

### 必做动作 D2

检查 `LibraryManager` 与 mixin 的方法实现，凡是以下类型都改为委托：

* tree build / iter
* scan state / worker coordination
* watcher suspend depth / desired paths
* restore/trash 判定

### 必做动作 D3

第二阶段完成的判断标准之一应是：

> `LibraryManager` 中不再新增任何业务判断，只允许：
>
> * QObject signal 容器
> * 服务组合
> * 兼容 API 委托

目前还没达到这一点。

---

## E. 继续清理 scan 完成后的 legacy 调用

### 必做动作 E1

`scan_coordinator.py` 里的 `_on_scan_finished()` 目前仍然直接：

* 在 scan 完成后调用 `backend.pair(root, library_root=self._root)`

这说明扫描完成后的 live pairing 持久化仍走 legacy backend。

这一步应改成：

* 调新的 application use case
* 不再从 mixin 里直连 `backend.pair(...)`

### 必做动作 E2

所有 scan 完成后的后处理都应进入 application 层统一编排，不应再留在 legacy mixin 中。

---

## F. 用测试把“第二阶段完成”钉死

虽然这个分支已经加了大量 phase2 测试文件，但建议明确补齐这 4 组断言：

### 必做动作 F1

新增或强化结构测试，断言：

* `LibraryManager` 的 scan 主路径通过 `LibraryScanService`
* `FileSystemWatcherMixin` 不再自己维护 suspend depth
* `library_update_service.py` 不再包含 restore metadata merge 规则
* `scan_coordinator.py` 不再直接调用 `backend.pair(...)`

### 必做动作 F2

新增回归测试覆盖：

* restore 后 album refresh
* nested album + global db
* scan finish 后 pair live 持久化
* library bind / rebind 后 watcher rebuild

---

## 我建议的补完顺序

按这个顺序做最稳：

1. 先把 `scan_coordinator.py` 的 scan 主逻辑迁到 `LibraryScanService`
2. 再把 restore 后 rescan / reload 规则迁到 `TrashService` 或专门服务
3. 删除 `library_update_service.py` 中剩余的 restore 业务判断
4. 干掉 `scan_coordinator.py` 中对 `backend.pair(...)` 的直接依赖
5. 补结构测试和回归测试
6. 最后再宣布第二阶段完成

---

## 最终判断

**这个 branch 不需要回退，也不需要重做第二阶段。**
但它**还差最后一轮“主路径接管”收尾**，尤其是：

* `LibraryScanService` 真接管 scan
* `TrashService` 真接管 restore 规则
* `library_update_service.py` 彻底退化为 UI 协调器
* `scan_coordinator.py` 退出业务实现层

把这几项补齐后，第二阶段才算真正完成。

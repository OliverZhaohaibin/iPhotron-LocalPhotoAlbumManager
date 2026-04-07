下面这份可以直接发给开发人员。

---

# 第二阶段未完成内容总结

## 总体判断

第二阶段**方向正确，已完成主要骨架搭建**，但还没有闭环。
当前状态更接近：

> **“第二阶段完成了 60%～70%，可以继续推进，但不能在这里停。”**

已完成的内容主要是：

* policy 层已建立
* scan 子域已开始拆分
* `LibraryManager` 已开始接入 service
* `library_update_service.py` 已开始降权
* 测试覆盖比第一阶段明显更完整

但以下内容**还没有真正完成**。

---

## 一、`library_update_service.py` 仍然过重

### 当前问题

虽然第二阶段已经把一部分扫描逻辑下沉到了 use case，但 `library_update_service.py` 里仍保留了大量**应用级决策逻辑**，它还不是纯 UI 协调器。

### 还未完成的点

需要继续从 `src/iPhoto/gui/services/library_update_service.py` 移走：

1. **move aftermath bookkeeping**

   * move 完成后的受影响 album 计算
   * refresh target 选择
   * restart / reload 判断

2. **stale album / forced reload 状态管理**

   * `_mark_album_stale`
   * `_consume_forced_reload`

3. **album root 定位逻辑**

   * `_collect_album_roots_from_pairs`
   * `_locate_album_root`

4. **restore 后续刷新规则**

   * `_refresh_restored_album`
   * restore 后对当前 album / library root 的 reload 触发逻辑

### 目标状态

`library_update_service.py` 最终应只保留：

* Qt signal relay
* worker finished/error 对接
* BackgroundTaskManager 协调
* 很薄的 UI reload 触发

---

## 二、`LibraryManager` 还没有真正瘦下来

### 当前问题

虽然第二阶段引入了：

* `LibraryTreeService`
* `LibraryScanService`
* `LibraryWatchService`
* `TrashService`
* `QtLibraryWatcher`

但 `LibraryManager` 目前还只是“挂上 service”，还没有真正从大对象变成薄组合器。

### 还未完成的点

需要继续从 `src/iPhoto/library/manager.py` 中剥离：

1. **watcher 相关具体逻辑**

   * watcher 暂停/恢复
   * 监听路径重建
   * debounce 触发行为

2. **scan 状态具体逻辑**

   * 当前扫描 worker
   * scan root / scan buffer
   * geotagged cache invalidation
   * scanning path 判断

3. **trash 相关具体逻辑**

   * deleted dir 初始化
   * restore 相关目录规则
   * recently deleted 相关宿主逻辑

4. **tree 刷新相关具体行为**

   * build tree 之外的刷新协调仍有残留

### 目标状态

`LibraryManager` 最终应变成：

> **QObject signal 容器 + service composition + backward-compatible API**

不再承担实际业务逻辑。

---

## 三、扫描子域还没有完全闭包

### 当前问题

扫描相关内容虽然已经拆到了：

* `RescanAlbumUseCase`
* `MergeTrashRestoreMetadataUseCase`
* `LoadIncrementalIndexUseCase`
* `PersistScanResultUseCase`
* infrastructure/scan

但还没有形成完全独立、边界清晰的扫描子域。

### 还未完成的点

1. **`RescanAlbumUseCase` 仍然依赖 legacy helper**
   还直接依赖：

   * `scan_album`
   * `update_index_snapshot`
   * `ensure_links`
   * `Album.open`

2. **扫描流程还没有完全 infrastructure 化**
   应进一步收口到：

   * scanner adapter
   * row persistence
   * pairing reader
   * incremental index loader

3. **扫描的同步/异步两条链路还没有统一抽象**
   当前 synchronous rescan 与 async worker flow 仍然存在重复编排倾向。

### 目标状态

扫描子域最终应形成：

* application/use_cases/scan 负责编排
* application/policies 负责规则
* infrastructure/scan 负责扫描、读取、持久化
* presentation 层只管进度与刷新

---

## 四、路径规则虽然已集中，但还没有彻底收口

### 当前问题

第二阶段已经有：

* `AlbumPathPolicy`
* `LibraryScopePolicy`
* `TrashRestorePolicy`

这是正确方向，但路径相关规则还没有完全从其他层清空。

### 还未完成的点

需要继续检查并清掉以下层中的路径业务逻辑残留：

1. `library_update_service.py`
2. `LibraryManager`
3. `app.py`
4. 可能仍残留在 `index_sync_service.py`
5. 可能仍残留在其他 legacy helper 中

### 目标状态

所有路径业务规则统一只存在于：

* `album_path_policy.py`
* `library_scope_policy.py`
* `trash_restore_policy.py`

其它层只调用 policy，不自己写路径语义。

---

## 五、compatibility layer 还没有进入“极薄”状态

### 当前问题

现在以下文件仍然是兼容层，但还不够薄：

* `src/iPhoto/app.py`
* `src/iPhoto/appctx.py`
* `src/iPhoto/gui/facade.py`

### 还未完成的点

#### `app.py`

应继续收缩到只做：

* 参数兼容
* 调用 application use case
* 返回旧接口格式

不得继续残留：

* 路径规则
* 业务决策
* index / links 语义

#### `appctx.py`

应继续收缩到只做：

* `container`
* `session`

不要继续保留更多运行时状态逻辑。

#### `gui/facade.py`

应继续收缩到只做：

* signal 定义
* sub-facade 组合
* public method forwarding

不能再继续承载新的业务逻辑。

---

## 六、第二阶段测试虽然有了，但还不够证明“重构闭环”

### 当前情况

第二阶段测试已经比第一阶段好很多，但还不够覆盖真正高风险区域。

### 还未完成的测试重点

1. **restore 链路集成测试**

   * recently deleted 恢复后 metadata 是否完整保留
   * restore 到 nested album / renamed album 是否正确

2. **async scan flow 测试**

   * worker 完成后 merge/persist/reload 是否一致
   * cancel / retry 是否没有状态污染

3. **LibraryManager delegation 行为测试**

   * 不只是“有 service 实例”
   * 还要验证 method 是否真正委托到 service

4. **global db + nested album 场景测试**

   * prefix / strip / scope 判断必须完整覆盖

### 目标状态

测试不只是验证“新文件存在”，而是验证：

* 行为没变
* 边界更清晰
* 重复逻辑已消失

---

# 建议开发人员接下来的优先级

## 第一优先级

继续瘦身：

```text
src/iPhoto/gui/services/library_update_service.py
```

## 第二优先级

继续解体：

```text
src/iPhoto/library/manager.py
src/iPhoto/application/services/library_tree_service.py
src/iPhoto/application/services/library_scan_service.py
src/iPhoto/application/services/library_watch_service.py
src/iPhoto/application/services/trash_service.py
```

## 第三优先级

继续压缩 compatibility layer：

```text
src/iPhoto/app.py
src/iPhoto/appctx.py
src/iPhoto/gui/facade.py
```




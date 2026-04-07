# iPhotron 第三阶段分支审查报告与补完方案

版本：v1.0  
审查对象分支：`copilot/refactor-third-phase-development`  
基线分支：`main`  
适用仓库：`OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager`

---

## 1. 审查结论

### 1.1 总体判断
`copilot/refactor-third-phase-development` **不建议判定为第三阶段完全达标**。  
结论是：

> **第三阶段方向正确，新增结构合理，但尚未完成“主路径接入”和“兼容层收尾”，因此应判定为：第三阶段未闭环，需要继续完成第三阶段动作，而不进入第四步。**

---

## 2. 为什么不判定通过第三阶段

第三阶段目标不是继续建文件，而是完成从“重构过渡态”到“稳定正式入口”的收尾。  
这条分支已经新增了很多正确的对象：

- `bootstrap/runtime_context.py`
- `presentation/qt/adapters/library_update_adapter.py`
- `presentation/qt/adapters/scan_progress_adapter.py`
- `application/services/library_reload_service.py`
- `application/services/move_aftercare_service.py`
- `application/services/restore_aftercare_service.py`
- `application/use_cases/scan/open_album_workflow_use_case.py`

这些新增内容说明开发方向是对的。

但第三阶段是否通过，关键不在于“是否新增了对象”，而在于：

1. **这些对象是否接入主路径**
2. **兼容层是否进一步收缩**
3. **老的协调层是否真的变薄**
4. **旧入口是否被正式入口取代**

在这几点上，这条分支还没有完成。

---

## 3. 审查评价

### 3.1 评价等级
**评级：B**

这是一个“可以继续开发，但不应该结项”的评级。

### 3.2 正面评价
这条分支有明显进展：

- `app.py` 继续变薄，`open_album()` 已转调 `OpenAlbumWorkflowUseCase`
- `library_update_service.py` 再次下沉业务判断到：
  - `MoveAftercareService`
  - `RestoreAftercareService`
  - `LibraryReloadService`
- 新建了 `RuntimeContext`
- 新建了 presentation adapters
- 第三阶段测试也补上了基础覆盖

这些都说明开发是在认真推进第三阶段，而不是停留在第二阶段。

### 3.3 负面评价
但第三阶段最关键的“闭环”没有完成：

- `RuntimeContext` 没有成为真正的新正式入口
- `AppContext` 没有改为组合 `RuntimeContext`
- `AppFacade` 仍然直接绑定 `LibraryUpdateService`，而没有切到 adapter
- `LibraryManager` 相比第二阶段几乎没有进一步“实质变薄”
- 新增 adapter 更像“存在”，而不是“主路径在用”

因此，不建议判定为“第三阶段完成”。

---

## 4. 具体问题清单

## 4.1 `RuntimeContext` 已创建，但没有真正接管正式入口
### 现状
分支里新增了：

```text
src/iPhoto/bootstrap/runtime_context.py
```

这是正确动作。

### 问题
但当前 `src/iPhoto/appctx.py` 仍然没有真正组合 `RuntimeContext`，而是继续自己：

- 创建 settings
- 创建 library
- 创建 facade
- 创建 container
- 创建 session

也就是说：

> `RuntimeContext` 存在，但 **没有接入主入口**。

### 影响
这意味着第三阶段最重要的目标之一——“正式入口统一”——还没有完成。

---

## 4.2 `AppContext` 仍然不是纯 compatibility shell
### 第三阶段目标
第三阶段里，`AppContext` 应该退化为：

- 一个兼容壳
- 组合 `RuntimeContext`
- 对旧字段做转发访问

### 现状
它现在仍然自己构建：
- settings
- library
- facade
- container
- session

### 判断
这说明 `AppContext` 仍然是“旧入口 + 新入口并存”的状态，而不是“旧入口只是兼容外观”。

---

## 4.3 `AppFacade` 仍未切到 presentation adapter 主路径
### 现状
分支里确实新增了：

```text
src/iPhoto/presentation/qt/adapters/library_update_adapter.py
src/iPhoto/presentation/qt/adapters/scan_progress_adapter.py
```

这是第三阶段预期动作。

### 问题
但 `src/iPhoto/gui/facade.py` 里主路径依然是：

- 直接持有 `LibraryUpdateService`
- 直接连接 `scanProgress / indexUpdated / linksUpdated / assetReloadRequested`
- 直接 relay signal

### 判断
也就是说：

> adapter 已经存在，但 **没有成为主路径的一部分**。

这会让第三阶段目标中的“presentation adapter 化”停留在半完成状态。

---

## 4.4 `LibraryManager` 没有相对第二阶段出现足够明显的进一步收缩
### 现状
`LibraryManager` 仍然：

- 继承多个 mixin
- 保留大量 state
- 保留 tree / scan / watcher / geo / trash 聚合结构
- 继续是 coordination hub

### 问题
与第二阶段相比，它没有完成第三阶段期待的“进一步实质削薄”。

### 判断
它仍处于：

> “第二阶段已改善，但第三阶段未最终收口”的状态

而不是：

> “第三阶段完成，成为 compatibility composition shell”

---

## 4.5 `library_update_service.py` 虽然更轻了，但还没彻底完成 adapter 化边界
### 正面
它已经明显下降了业务浓度，新增：
- `MoveAftercareService`
- `RestoreAftercareService`
- `LibraryReloadService`

这是好事。

### 问题
但它仍然直接承担：
- worker coordination
- reload emit
- move/restore aftermath 协调
- 具体 signal relay
- restore rescan 调度

如果 adapter 已存在，那么第三阶段应进一步把 presentation relay 与 service orchestration 分开。

### 判断
它已经“更轻”，但还没有形成明确的：
- service
- adapter
- facade

三段分离。

---

## 4.6 `scan_progress_adapter.py` / `library_update_adapter.py` 缺少主路径接入证据
### 现状
有文件。
### 问题
从当前主入口代码看，没有看到它们被 `AppFacade` 或主 UI 组合链真正接入。
### 判断
这属于“已建未用”或者“弱接入”状态。

---

## 5. 最终结论

### 是否满足第三阶段要求
**不满足完整第三阶段结项要求。**

### 是否值得继续在这条分支上推进
**值得。**  
因为方向对，代码质量也不差，缺的是“最后接入与收尾”。

### 当前最适合的判断
> 继续完成第三阶段，不进入第四步。

---

## 6. 完成第三阶段的具体动作

下面这些是必须完成的动作。  
在这些动作完成之前，不建议给第四步方案。

---

## 6.1 把 `RuntimeContext` 接入主入口

### 必做动作
修改：

```text
src/iPhoto/appctx.py
```

### 改法
当前 `AppContext` 不要再自己分别构建：
- settings
- library
- facade
- container
- session

改为：

1. 内部持有一个 `RuntimeContext`
2. 旧字段全部代理到 `RuntimeContext`
3. `AppContext` 只保留兼容壳职责

### 目标结构
例如：

```python
class AppContext:
    def __init__(self, defer_startup_tasks: bool = False):
        self._runtime = RuntimeContext.create(defer_startup=defer_startup_tasks)

    @property
    def settings(self):
        return self._runtime.settings

    @property
    def library(self):
        return self._runtime.library

    @property
    def facade(self):
        return self._runtime.facade

    @property
    def container(self):
        return self._runtime.container
```

### 验收标准
- `AppContext` 不再自己装配正式对象
- `RuntimeContext` 成为唯一正式入口
- `AppContext` 成为 compatibility shell

---

## 6.2 把 adapter 接入 `AppFacade` 主路径

### 必做动作
修改：

```text
src/iPhoto/gui/facade.py
```

### 改法
当前 `AppFacade` 仍直接连 `LibraryUpdateService` 的 signals。  
应改为：

1. 创建 `LibraryUpdateAdapter`
2. 创建 `ScanProgressAdapter`
3. 由 adapter 负责 signal relay
4. `AppFacade` 连 adapter，而不是直接连底层 service

### 目标
把 signal relay 层从 `AppFacade` 和 service 中间抽出来，形成稳定 presentation adapter 边界。

### 验收标准
- `AppFacade` 不再直接绑定 `LibraryUpdateService` 的大部分 signal
- adapter 在主链路中被实际使用
- UI 新代码面向 adapter，而不是面向 service

---

## 6.3 继续削薄 `LibraryManager`

### 必做动作
继续修改：

```text
src/iPhoto/library/manager.py
src/iPhoto/library/scan_coordinator.py
src/iPhoto/library/filesystem_watcher.py
src/iPhoto/library/trash_manager.py
```

### 改法
目标不是重写，而是进一步消除“manager 自己知道太多”。

#### 需要做的事
1. 把能迁出的 state 尽量交给 service 管理
2. 让 manager 方法继续退化为 delegation
3. 对 mixin 做减重，保留必须依附 QObject 的部分
4. manager 里避免继续出现新业务判断

### 验收标准
- `LibraryManager` 中的方法大多数是委托
- 旧 mixin 中复杂逻辑继续减少
- state 字段数量显著下降或职责更清晰

---

## 6.4 让 `library_update_service.py` 真正完成 adapter 前的 service 收敛

### 必做动作
修改：

```text
src/iPhoto/gui/services/library_update_service.py
```

### 改法
已经迁出一部分 aftercare，但还需进一步明确：

- 什么属于 application service
- 什么属于 presentation adapter
- 什么属于 Qt task coordination

### 继续迁出的内容
优先继续下沉：
1. restore rescan 触发条件的剩余判断
2. move aftermath 中仍然存在的非 Qt 逻辑
3. reload 决策中可纯 application 化的部分

### service 最终目标
只保留：
- worker 管理
- task_manager 对接
- 触发 application use case
- 发出结果给 adapter / facade

---

## 6.5 让 `scan_progress_adapter.py` 真正承担职责

### 必做动作
检查并接入：

```text
src/iPhoto/presentation/qt/adapters/scan_progress_adapter.py
```

### 改法
把扫描进度相关 signal relay 统一由它承接，而不是继续散落在：
- `AppFacade`
- `LibraryUpdateService`
- 可能的 UI controller / viewmodel

### 验收标准
- scan progress relay 有明确 adapter 层
- `AppFacade` 不直接做大段 progress relay

---

## 6.6 收缩 `app.py` 里残留的扫描细节

### 现状
`app.py` 的 `open_album()` 已明显变薄，这是优点。  
但 `scan_specific_files()` 仍然保留：
- 文件扩展名分类
- scanner adapter 直接调用
- store append_rows

### 必做动作
把 `scan_specific_files()` 也下沉到正式 use case / service。

### 建议新增
```text
src/iPhoto/application/use_cases/asset/scan_specific_files_use_case.py
```

### 验收标准
- `app.py` 不再持有局部扫描业务细节
- `app.py` 只剩兼容函数签名 + forwarding

---

## 6.7 补第三阶段“接入型测试”，不是只测对象存在

### 当前不足
第三阶段已有测试，但还需要补“主路径接入”类测试。

### 必补测试
#### 1）`AppContext` 是否通过 `RuntimeContext` 组合
- 验证 `AppContext` 不再独立构建正式对象

#### 2）`AppFacade` 是否通过 adapter 接 signal
- 验证 adapter 被真实使用

#### 3）adapter 是否成为 UI 对外稳定接口
- 验证 UI 侧连接 adapter 仍能收到所有关键 signal

#### 4）`app.py` 是否全部变成 shim
- 尤其覆盖 `scan_specific_files()`

#### 5）`LibraryManager` delegation 是否比第二阶段更进一步
- 不只是“有 service 字段”
- 而是方法体真的主要在委托

---

## 7. 推荐修改顺序

### Commit 1
- 修改 `appctx.py`
- 把 `RuntimeContext` 接入主入口
- 补 `AppContext` compatibility tests

### Commit 2
- 修改 `gui/facade.py`
- 接入 `LibraryUpdateAdapter` / `ScanProgressAdapter`
- 补 facade-adapter integration tests

### Commit 3
- 继续瘦身 `library_update_service.py`
- 明确 service / adapter 边界
- 补对应 tests

### Commit 4
- 继续削薄 `LibraryManager` 与相关 mixin
- 补 delegation tests

### Commit 5
- 新建 `scan_specific_files_use_case.py`
- 收缩 `app.py`
- 补 shim tests

---

## 8. 第三阶段补完验收标准

只有当以下条件全部满足时，才建议判定第三阶段完成：

- [ ] `RuntimeContext` 成为唯一正式入口
- [ ] `AppContext` 成为真正 compatibility shell
- [ ] `AppFacade` 主路径通过 adapter 接入 signal
- [ ] `LibraryUpdateAdapter` / `ScanProgressAdapter` 被主路径真实使用
- [ ] `LibraryManager` 相比第二阶段继续明显变薄
- [ ] `library_update_service.py` 进一步完成 service 化
- [ ] `app.py` 只剩 shim，不再含局部扫描业务细节
- [ ] 补齐接入型测试与兼容型测试

---

## 9. 给团队的建议话术

可以直接这样给团队反馈：

> 第三阶段分支方向正确，新增了 RuntimeContext、presentation adapters 和 aftercare services，说明架构收口在继续推进。  
> 但当前仍未完成第三阶段的关键闭环：正式入口尚未真正切换到 RuntimeContext，adapter 尚未进入主路径，AppContext / AppFacade / LibraryManager 仍处于“过渡态而非最终态”。  
> 因此建议继续完成第三阶段，不进入第四步。后续应以“主路径接入”和“兼容层彻底退化”为核心目标，而不是继续横向新增结构。

---
